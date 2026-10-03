"""
The change log, read the way a person would read it.

data/changes.jsonl is the raw record: every entry the crawler confirmed, in
the order it was confirmed. It is never edited. But read raw it overstates
what happened. Over the first two months it held 71 entries, and most were
not price changes at all:

  * Brevo "took pricing off its public page" three times and "put it back"
    twice. Its page fills its prices in with JavaScript and the crawler got
    the numbers on some days and not others. Nothing about Brevo's pricing
    moved.
  * Nutshell "removed" six plans and "added" six plans on the same morning --
    the same six, renamed from "Engagement" to "Engagement Add-on". A month
    later it renamed them back, and that was twelve more entries.
  * Attio's credit packs disappeared on 31 August and reappeared, identical,
    on 14 September.

This module turns raw entries into events with a kind, a group and a status,
so the site can lead with the price changes that really happened and still
show everything else, labelled for what it is. Rules, in order:

  1. Same-day renames. A plan removed and a plan added on the same day for
     the same vendor, either with the same identity once "Add-on" and similar
     words are ignored, or at the same unique price, is one renamed plan.
  2. Reversals. An entry undone within REVERSAL_DAYS -- hidden then shown,
     removed then re-added, $19 -> $29 then $29 -> $19 -- is marked
     "reversed" on both sides. It stays on the record, out of the headlines.
  3. Unstable pages. If a vendor has two or more reversals, any remaining
     entry about pricing appearing or disappearing is marked "unconfirmed":
     that page has shown it loads inconsistently.
  4. Manual corrections from corrections.yaml, if any, are applied last and
     always win. That is the explicit correction process: a person writes
     down what was wrong and why, and the raw log stays untouched.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import yaml

from . import storage
from .diff import plan_key

REVERSAL_DAYS = 21
CORRECTIONS_FILE = storage.ROOT / "corrections.yaml"

PRICE_TYPES = {"price_increase", "price_decrease"}
PLAN_TYPES = {"plan_added", "plan_removed", "plan_renamed"}
PAGE_TYPES = {"pricing_hidden", "pricing_published", "custom_pricing_changed",
              "price_availability_changed"}

ADDON_WORDS = ("add-on", "addon", "add on", "credits", "credit pack",
               "additional", "extra ", "day pass", "pay as you go",
               "sessions", "tasks", "actions")

STATUS_LABEL = {
    "confirmed": "Confirmed",
    "reversed": "Reversed",
    "unconfirmed": "Unconfirmed",
    "corrected": "Corrected",
    "hidden": "Withdrawn",
}


def _date(c: dict) -> str:
    return str(c.get("detected_at") or "")[:10]


def _days(a: str, b: str) -> int:
    try:
        return abs((datetime.strptime(b, "%Y-%m-%d")
                    - datetime.strptime(a, "%Y-%m-%d")).days)
    except ValueError:
        return 10_000


def _is_addon(c: dict) -> bool:
    if c.get("is_addon"):
        return True
    name = str(c.get("plan") or "").lower()
    return any(w in name for w in ADDON_WORDS)


def _num(v):
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(old, new) -> float | None:
    o, n = _num(old), _num(new)
    if o is None or n is None or o == 0:
        return None
    return round((n - o) / o * 100, 1)


def _event_id(c: dict) -> str:
    raw = "|".join(str(c.get(k) or "") for k in
                   ("detected_at", "vendor", "change_type", "plan", "field"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def _base_event(c: dict, vendors: dict, addon_lookup: dict | None = None) -> dict:
    t = str(c.get("change_type") or "")
    vendor = str(c.get("vendor") or "")
    meta = vendors.get(vendor, {}) or {}
    known = (addon_lookup or {}).get(
        (storage.slugify(vendor), plan_key(str(c.get("plan") or ""))))
    addon = bool(known) if known is not None else _is_addon(c)
    if t in PRICE_TYPES:
        group = "price"
        kind = "rise" if t == "price_increase" else "cut"
    elif t in PLAN_TYPES:
        group = "addon" if addon else "plan"
        kind = {"plan_added": "added", "plan_removed": "removed",
                "plan_renamed": "renamed"}[t]
    elif t in PAGE_TYPES:
        group, kind = "page", t
    else:
        group, kind = "detail", t
    return {
        "id": _event_id(c),
        "vendor": vendor,
        "slug": storage.slugify(vendor),
        "category": meta.get("category", ""),
        "currency": (meta.get("currency") or "USD"),
        "plan": c.get("plan"),
        "plan_key": plan_key(str(c.get("plan") or "")),
        "change_type": t,
        "field": c.get("field"),
        "old": c.get("old_value"),
        "new": c.get("new_value"),
        "pct": _pct(c.get("old_value"), c.get("new_value"))
        if t in PRICE_TYPES else None,
        "date": _date(c),
        "detected_at": str(c.get("detected_at") or ""),
        "group": group,
        "kind": kind,
        "addon": addon,
        "billing": ("annual" if "annual" in str(c.get("field") or "")
                    else "monthly"),
        "status": "confirmed",
        "status_note": "",
        "raw": c,
    }


# ---------------------------------------------------------------- rules

def _pair_renames(events: list[dict]) -> list[dict]:
    """Rule 1: fold same-day remove+add pairs into one rename."""
    out, used = [], set()
    by_day: dict[tuple, list[int]] = {}
    for i, e in enumerate(events):
        if e["change_type"] in ("plan_added", "plan_removed"):
            by_day.setdefault((e["vendor"], e["date"]), []).append(i)

    merged: dict[int, dict] = {}
    for idxs in by_day.values():
        removed = [i for i in idxs if events[i]["change_type"] == "plan_removed"]
        added = [i for i in idxs if events[i]["change_type"] == "plan_added"]
        # Same identity first ("SMS" vs "SMS Add-on").
        for r in list(removed):
            for a in list(added):
                if events[r]["plan_key"] and events[r]["plan_key"] == events[a]["plan_key"]:
                    merged[a] = _rename(events[r], events[a])
                    used.update((r, a))
                    removed.remove(r)
                    added.remove(a)
                    break
        # Then a unique shared price.
        def price_map(ids, field):
            m: dict[float, list[int]] = {}
            for i in ids:
                v = _num(events[i][field])
                if v is not None and v > 0:
                    m.setdefault(v, []).append(i)
            return m
        gone, came = price_map(removed, "old"), price_map(added, "new")
        for price, rs in gone.items():
            as_ = came.get(price, [])
            if len(rs) == 1 and len(as_) == 1:
                merged[as_[0]] = _rename(events[rs[0]], events[as_[0]])
                used.update((rs[0], as_[0]))

    for i, e in enumerate(events):
        if i in merged:
            out.append(merged[i])
        elif i not in used:
            out.append(e)
    return out


def _rename(removed: dict, added: dict) -> dict:
    e = dict(added)
    addon = bool(removed.get("addon") or added.get("addon"))
    e.update({
        "addon": addon,
        "group": "addon" if addon else "plan",
        "change_type": "plan_renamed",
        "kind": "renamed",
        "old": removed.get("plan"),
        "new": added.get("plan"),
        "price": added.get("new"),
        "id": added["id"],
        "status_note": "Logged as one plan removed and another added on the "
                       "same day at the same price; shown here as a rename.",
        "folded": [removed["id"], added["id"]],
    })
    return e


def _subject(e: dict) -> tuple | None:
    """What an event is about, for matching it with its reversal."""
    t = e["change_type"]
    if t in ("pricing_hidden", "pricing_published"):
        return (e["vendor"], "public")
    if t in ("plan_added", "plan_removed"):
        return (e["vendor"], "plan", e["plan_key"])
    if t == "custom_pricing_changed":
        return (e["vendor"], "custom", e["plan_key"])
    if t in PRICE_TYPES or t == "price_availability_changed":
        return (e["vendor"], "price", e["plan_key"], e["field"])
    return None


def _undoes(a: dict, b: dict) -> bool:
    ta, tb = a["change_type"], b["change_type"]
    if {ta, tb} == {"pricing_hidden", "pricing_published"}:
        return True
    if {ta, tb} == {"plan_added", "plan_removed"}:
        return True
    if ta == tb == "custom_pricing_changed":
        return str(a["new"]).lower() != str(b["new"]).lower()
    if (ta in PRICE_TYPES or ta == "price_availability_changed") and \
            (tb in PRICE_TYPES or tb == "price_availability_changed"):
        return _num(a["old"]) == _num(b["new"]) and _num(a["new"]) == _num(b["old"])
    return False


def _mark_reversals(events: list[dict]) -> int:
    """Rule 2. Returns how many pairs were found."""
    ordered = sorted(range(len(events)), key=lambda i: events[i]["detected_at"])
    open_by_subject: dict[tuple, int] = {}
    pairs = 0
    for i in ordered:
        e = events[i]
        subj = _subject(e)
        if subj is None:
            continue
        j = open_by_subject.get(subj)
        if j is not None and _undoes(events[j], e) and \
                _days(events[j]["date"], e["date"]) <= REVERSAL_DAYS:
            days = _days(events[j]["date"], e["date"])
            span = "the same day" if days == 0 else \
                f"{days} day{'s' if days != 1 else ''} later"
            for k, other in ((j, e), (i, events[j])):
                events[k]["status"] = "reversed"
                events[k]["status_note"] = (
                    f"Undone {span}" if k == j else f"Undid an entry from {span.replace(' later', ' earlier')}")
                events[k]["status_note"] += (
                    " — most likely the page loading differently on "
                    "different days, not a real change.")
                events[k]["pair"] = other["id"]
            del open_by_subject[subj]
            pairs += 1
        else:
            open_by_subject[subj] = i
    return pairs


def _mark_unstable(events: list[dict]) -> None:
    """Rule 3."""
    reversals: dict[str, int] = {}
    for e in events:
        if e["status"] == "reversed":
            reversals[e["vendor"]] = reversals.get(e["vendor"], 0) + 1
    for e in events:
        if e["status"] == "confirmed" and e["group"] == "page" \
                and reversals.get(e["vendor"], 0) >= 4:  # two pairs
            e["status"] = "unconfirmed"
            e["status_note"] = (
                "This vendor's page has loaded inconsistently before, so this "
                "entry is not treated as a confirmed change.")


def load_corrections() -> list[dict]:
    if not CORRECTIONS_FILE.exists():
        return []
    try:
        data = yaml.safe_load(CORRECTIONS_FILE.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return []
    rows = data.get("corrections") if isinstance(data, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("vendor")]


def _apply_corrections(events: list[dict], corrections: list[dict]) -> int:
    """Rule 4. Returns how many events a correction touched."""
    touched = 0
    for fix in corrections:
        vendor = str(fix.get("vendor", "")).lower()
        day = str(fix.get("date", ""))[:10]
        ctype = fix.get("change_type")
        plan = fix.get("plan")
        status = str(fix.get("status", "corrected")).lower()
        if status not in STATUS_LABEL:
            status = "corrected"
        for e in events:
            if e["vendor"].lower() != vendor:
                continue
            if day and e["date"] != day:
                continue
            if ctype and e["change_type"] != ctype and e["kind"] != ctype:
                continue
            if plan and str(e.get("plan") or "").lower() != str(plan).lower():
                continue
            e["status"] = status
            e["status_note"] = str(fix.get("note") or "Corrected by hand.")
            e["corrected"] = True
            touched += 1
    return touched


# ---------------------------------------------------------------- public

def addon_lookup_from(history: dict[str, list[dict]],
                      records: dict[str, dict] | None = None) -> dict:
    """(vendor slug, plan key) -> was that plan an add-on, from the data.

    Older log entries do not say whether the plan was an add-on. The pricing
    records do, so the answer is looked up there -- most recent reading wins
    -- and the name-based guess is only a fallback.
    """
    out: dict = {}
    for slug, points in (history or {}).items():
        for point in points:
            for plan in point.get("plans", []):
                out[(slug, plan.get("key"))] = bool(plan.get("addon"))
    for slug, rec in (records or {}).items():
        for plan in rec.get("plans", []) or []:
            out[(slug, plan_key(str(plan.get("name") or "")))] = bool(
                plan.get("is_addon"))
    return out


def build_events(changes: list[dict], vendors: dict,
                 corrections: list[dict] | None = None,
                 addon_lookup: dict | None = None) -> list[dict]:
    """Every raw change as an interpreted event, newest first."""
    events = [_base_event(c, vendors, addon_lookup) for c in changes
              if isinstance(c, dict) and c.get("vendor") and c.get("change_type")]
    events = _pair_renames(events)
    _mark_reversals(events)
    _mark_unstable(events)
    _apply_corrections(events, corrections if corrections is not None
                       else load_corrections())
    events.sort(key=lambda e: (e["detected_at"], e["id"]), reverse=True)
    return events


def headline(events: list[dict]) -> list[dict]:
    """What leads the site: confirmed price changes and confirmed plan
    launches or withdrawals for real subscription tiers."""
    return [e for e in events if e["status"] == "confirmed"
            and (e["group"] == "price"
                 or (e["group"] == "plan" and e["kind"] in ("added", "removed")))]


def price_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e["group"] == "price"
            and e["status"] == "confirmed"]


def visible(events: list[dict]) -> list[dict]:
    """Everything except entries withdrawn by a manual correction."""
    return [e for e in events if e["status"] != "hidden"]


def summary(events: list[dict]) -> dict:
    conf = [e for e in events if e["status"] == "confirmed"]
    return {
        "raw": len(events),
        "confirmed": len(conf),
        "price": sum(1 for e in conf if e["group"] == "price"),
        "rises": sum(1 for e in conf if e["kind"] == "rise"),
        "cuts": sum(1 for e in conf if e["kind"] == "cut"),
        "plan": sum(1 for e in conf if e["group"] == "plan"),
        "reversed": sum(1 for e in events if e["status"] == "reversed"),
        "unconfirmed": sum(1 for e in events if e["status"] == "unconfirmed"),
    }
