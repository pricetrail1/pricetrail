"""
Two structured records in, typed change events out.

Important design decision: a change is defined at the STRUCTURED DATA level,
never at the hash level. The hash is only a cheap filter for "is it worth
paying to look at this?". Plenty of pages change their HTML daily while their
prices sit still for years. If you alert on hash changes you will spam your
customers into cancelling in week one.

Every event carries a confidence score. Low-confidence events go to a human
review queue instead of to customers. One wrong price in an email to someone's
CEO costs you that customer permanently, so the asymmetry is worth the delay.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

# A price moving by more than this multiple is almost always an extraction
# error (annual total read as monthly, or a currency switch), not a real
# repricing. Real SaaS price changes cluster between -30% and +60%.
IMPLAUSIBLE_MULTIPLE = 4.0

CONFIDENCE_PUBLISH = 0.80  # at or above this, publish automatically

# Real SaaS repricing lands between about 5% and 30%. A move smaller than this
# is a rounding wobble in how the page was read, not a company changing its
# mind. Seen live: $96.00 -> $95.92, and $43.00 -> $43.08. Nobody reprices by
# eight cents.
MIN_PCT_CHANGE = 1.0


@dataclass
class Change:
    vendor: str
    change_type: str
    plan: str | None
    field: str | None
    old_value: object
    new_value: object
    confidence: float
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def publishable(self) -> bool:
        return self.confidence >= CONFIDENCE_PUBLISH

    def headline(self) -> str:
        p = f" \u2014 {self.plan}" if self.plan else ""
        if self.change_type in ("price_increase", "price_decrease"):
            arrow = "up" if self.change_type == "price_increase" else "down"
            return (f"{self.vendor}{p}: {self.field} {arrow} "
                    f"{self.old_value} \u2192 {self.new_value}")
        if self.change_type == "plan_added":
            return f"{self.vendor}: new plan '{self.plan}'"
        if self.change_type == "plan_removed":
            return f"{self.vendor}: removed plan '{self.plan}'"
        if self.change_type == "feature_moved_out":
            return f"{self.vendor}{p}: dropped feature '{self.old_value}'"
        if self.change_type == "feature_added":
            return f"{self.vendor}{p}: added feature '{self.new_value}'"
        if self.change_type == "pricing_hidden":
            return f"{self.vendor}: removed public pricing"
        if self.change_type == "limit_changed":
            return (f"{self.vendor}{p}: {self.field} limit "
                    f"{self.old_value} \u2192 {self.new_value}")
        return f"{self.vendor}{p}: {self.change_type} {self.field}"


def fingerprint(record: dict) -> str:
    """A hash of everything that matters about a pricing record.

    Ignores when it was captured. Two readings with the same fingerprint mean
    the page said exactly the same thing twice, which is what lets us tell a
    real change from a one-off misreading.
    """
    if not record:
        return ""
    parts = [str(record.get("currency", "")),
             str(bool(record.get("pricing_is_public", True)))]
    for plan in sorted(record.get("plans", []), key=lambda p: _key_of(p)):
        parts.append("|".join([
            _key_of(plan),
            str(plan.get("monthly_price")),
            str(plan.get("annual_price_per_month")),
            str(bool(plan.get("is_free"))),
            str(bool(plan.get("is_custom_pricing"))),
            str(bool(plan.get("is_addon"))),
            ",".join(f'{l.get("metric")}={l.get("value")}'
                     for l in sorted(plan.get("limits", []),
                                     key=lambda l: str(l.get("metric")))),
            ",".join(sorted(plan.get("features", []))),
        ]))
    return hashlib.sha256("||".join(parts).encode()).hexdigest()


def _key_of(plan: dict) -> str:
    """Plan identity, derived if the stored record predates the key field."""
    if plan.get("key"):
        return plan["key"]
    name = (plan.get("name") or "").lower().strip()
    for suffix in (" plan", " tier", " package", " edition"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return "".join(ch for ch in name if ch.isalnum())


def diff_pricing(vendor: str, old: dict | None, new: dict) -> list[Change]:
    """Compare two extractions. Returns [] when nothing meaningful moved."""
    if old is None:
        return []  # first ever capture: baseline, not news

    changes: list[Change] = []
    base = _base_confidence(new)

    # --- whole-page level -------------------------------------------------

    if old.get("pricing_is_public") and not new.get("pricing_is_public"):
        changes.append(Change(vendor, "pricing_hidden", None, None,
                              True, False, base,
                              note="page no longer shows any public prices"))
    elif not old.get("pricing_is_public") and new.get("pricing_is_public"):
        changes.append(Change(vendor, "pricing_published", None, None,
                              False, True, base))

    if old.get("currency") != new.get("currency") and old.get("currency"):
        # Currency flips are usually geo-detection, not a real change.
        changes.append(Change(vendor, "currency_changed", None, "currency",
                              old.get("currency"), new.get("currency"),
                              base * 0.4,
                              note="often geo-IP variation, verify manually"))

    # Be forgiving about records written by older versions or by hand: derive
    # a missing key rather than crashing on someone's archive.
    old_plans = {_key_of(p): p for p in old.get("plans", [])}
    new_plans = {_key_of(p): p for p in new.get("plans", [])}

    # --- plans appearing and disappearing ---------------------------------

    for key in new_plans.keys() - old_plans.keys():
        changes.append(Change(vendor, "plan_added", new_plans[key]["name"],
                              None, None, new_plans[key]["monthly_price"],
                              base))

    for key in old_plans.keys() - new_plans.keys():
        changes.append(Change(vendor, "plan_removed", old_plans[key]["name"],
                              None, old_plans[key]["monthly_price"], None,
                              base))

    # --- plans that persisted ---------------------------------------------

    for key in old_plans.keys() & new_plans.keys():
        changes.extend(_diff_plan(vendor, old_plans[key], new_plans[key], base))

    return changes


def _diff_plan(vendor: str, old: dict, new: dict, base: float) -> list[Change]:
    out: list[Change] = []
    name = new["name"]

    if old["name"] != new["name"]:
        out.append(Change(vendor, "plan_renamed", name, "name",
                          old["name"], new["name"], base))

    for pfield in ("monthly_price", "annual_price_per_month"):
        o, n = old.get(pfield), new.get(pfield)
        if o == n:
            continue
        if o is None or n is None:
            out.append(Change(vendor, "price_availability_changed", name,
                              pfield, o, n, base * 0.7))
            continue
        if o and abs((n - o) / o) * 100 < MIN_PCT_CHANGE:
            continue  # rounding noise, not a repricing

        kind = "price_increase" if n > o else "price_decrease"
        conf = base
        if o > 0 and (max(o, n) / min(o, n)) > IMPLAUSIBLE_MULTIPLE:
            conf *= 0.3  # almost certainly a misread, not a repricing
        out.append(Change(vendor, kind, name, pfield, o, n, conf,
                          note=_pct(o, n)))

    if old.get("is_custom_pricing") != new.get("is_custom_pricing"):
        out.append(Change(vendor, "custom_pricing_changed", name,
                          "is_custom_pricing", old.get("is_custom_pricing"),
                          new.get("is_custom_pricing"), base))

    if old.get("is_per_seat") != new.get("is_per_seat"):
        out.append(Change(vendor, "billing_model_changed", name, "is_per_seat",
                          old.get("is_per_seat"), new.get("is_per_seat"),
                          base * 0.8))

    # --- usage limits ------------------------------------------------------

    old_limits = {l["metric"]: l for l in old.get("limits", [])}
    new_limits = {l["metric"]: l for l in new.get("limits", [])}
    for metric in old_limits.keys() & new_limits.keys():
        o, n = old_limits[metric]["value"], new_limits[metric]["value"]
        if o != n:
            out.append(Change(vendor, "limit_changed", name, metric, o, n,
                              base * 0.9))

    # --- features ----------------------------------------------------------
    # Feature lists are the noisiest field on any pricing page, so they get a
    # confidence haircut and mostly land in the review queue by design.

    old_f, new_f = set(old.get("features", [])), set(new.get("features", []))
    for feature in sorted(new_f - old_f):
        out.append(Change(vendor, "feature_added", name, "features",
                          None, feature, base * 0.6))
    for feature in sorted(old_f - new_f):
        out.append(Change(vendor, "feature_moved_out", name, "features",
                          feature, None, base * 0.6))

    return out


def _base_confidence(new: dict) -> float:
    """Start from the quality of the extraction itself."""
    conf = 0.95
    if new.get("extraction_notes"):
        conf -= 0.15
    if not new.get("plans"):
        conf -= 0.4
    if len(new.get("plans", [])) == 1:
        conf -= 0.1  # single-plan reads are often truncated pages
    if not new.get("currency"):
        conf -= 0.1
    return max(0.0, round(conf, 2))


def _pct(old: float, new: float) -> str:
    if not old:
        return ""
    return f"{((new - old) / old) * 100:+.1f}%"
