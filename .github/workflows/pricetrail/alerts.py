"""
Price alerts: email paying customers when a competitor's pricing changes.

How it fits together, with no database and nothing personal in this public
repository:

  * Customers buy through a Stripe payment link that asks one extra question,
    "Which competitors should we watch?". Stripe keeps their email address and
    their answer. This module reads both from Stripe on every run.
  * The crawler has already written today's confirmed changes to
    data/changes.jsonl. Each run looks only at changes logged since the end of
    the previous run, so nothing is sent twice.
  * Emails go out through Resend. A customer gets:
      - a welcome email, the first run after they pay,
      - an alert whenever one of their companies has a confirmed change,
      - a short summary every Monday.
  * The owner gets an email about every new customer, listing any companies
    they asked for that are not tracked yet, so they can be added.

Secrets come from the environment (GitHub Actions secrets):
    STRIPE_KEY       a restricted, read-only Stripe key
    RESEND_API_KEY   the Resend key used to send email
    OWNER_EMAIL      where new-customer notices go (and replies to customers)
    ALERTS_FROM      optional, e.g. "PriceTrail <alerts@getpricetrail.com>"

With any of the first three missing, the module does nothing and says so.
It never prints an email address or a company list, because GitHub Actions
logs on a public repository are public.

    python -m pricetrail.alerts            # normal run
    python -m pricetrail.alerts --preview  # write emails to ./alert-preview
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import storage

STRIPE_API = "https://api.stripe.com/v1"
RESEND_API = "https://api.resend.com/emails"
BASE_URL = "https://getpricetrail.com"
DEFAULT_FROM = "PriceTrail <alerts@getpricetrail.com>"
MAX_COMPANIES = 10
OVERRIDES_FILE = storage.ROOT / "alerts.yaml"


# ---------------------------------------------------------------- http

def _http(method: str, url: str, headers: dict, body: bytes | None = None,
          timeout: int = 30) -> dict:
    """One HTTPS call returning parsed JSON. Raises RuntimeError on failure,
    with a message that never contains the request body."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {urllib.parse.urlsplit(url).netloc} "
                           f"failed with HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise RuntimeError(f"{method} {urllib.parse.urlsplit(url).netloc} "
                           f"failed: {type(e).__name__}") from None


def _stripe_list(path: str, key: str, params: dict) -> list[dict]:
    """Every object from a Stripe list endpoint, following pagination."""
    out, after = [], None
    for _ in range(50):  # hard stop: 5,000 objects
        q = dict(params, limit=100)
        if after:
            q["starting_after"] = after
        data = _http("GET", f"{STRIPE_API}/{path}?{urllib.parse.urlencode(q)}",
                     {"Authorization": f"Bearer {key}"})
        items = data.get("data") or []
        out.extend(items)
        if not data.get("has_more") or not items:
            break
        after = items[-1].get("id")
    return out


# ---------------------------------------------------------------- customers

def _competitor_answer(session: dict) -> str | None:
    """The answer to the "which competitors" question, or None if this
    checkout did not ask it (for example, a data licence purchase)."""
    for f in session.get("custom_fields") or []:
        label = str((f.get("label") or {}).get("custom") or "")
        if "competitor" in (str(f.get("key") or "") + " " + label).lower():
            return str((f.get("text") or {}).get("value") or "")
    return None


def subscribers(stripe_key: str) -> list[dict]:
    """Active price-alert customers: email, raw answer, when they joined."""
    sessions = _stripe_list("checkout/sessions", stripe_key, {"status": "complete"})
    active = {s.get("id") for s in
              _stripe_list("subscriptions", stripe_key, {"status": "active"})}
    active |= {s.get("id") for s in
               _stripe_list("subscriptions", stripe_key, {"status": "trialing"})}
    out = []
    for s in sessions:
        answer = _competitor_answer(s)
        sub = s.get("subscription")
        if isinstance(sub, dict):
            sub = sub.get("id")
        email = ((s.get("customer_details") or {}).get("email")
                 or s.get("customer_email") or "")
        if answer is None or not sub or sub not in active or not email:
            continue
        out.append({"email": email, "answer": answer, "subscription": sub,
                    "created": int(s.get("created") or 0)})
    return out


# ---------------------------------------------------------------- matching

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _core_domain(url: str) -> str:
    host = urllib.parse.urlsplit(url if "//" in url else "//" + url).hostname or ""
    parts = [p for p in host.split(".") if p not in ("www", "app")]
    return parts[0] if parts else ""


def load_vendors() -> list[dict]:
    cfg = yaml.safe_load((storage.ROOT / "vendors.yaml").read_text("utf-8"))
    return [v for v in cfg.get("vendors", []) if v.get("name")]


def match_companies(answer: str, vendors: list[dict]) -> tuple[list[str], list[str]]:
    """Split a free-text answer into tracked company names and the rest.

    Names are matched exactly (ignoring case, spaces and punctuation), by the
    vendor's website ("zendesk.com"), or when the answer starts with the
    name as whole words ("Pipedrive CRM" -> Pipedrive). Part of a word never
    matches, so "closet" is not Close and "kitchen" is not Kit.
    """
    pieces = [p.strip() for p in re.split(r"[,;\n]|\band\b|&|/", answer or "")]
    pieces = [p for p in pieces if p][:MAX_COMPANIES * 2]
    keys = []
    for v in vendors:
        url = v.get("pricing_url") or v.get("url") or ""
        keys.append((v["name"], _norm(v["name"]), _norm(_core_domain(url))))
    found, unknown = [], []
    for piece in pieces:
        n = _norm(piece)
        if not n:
            continue
        dom = _norm(_core_domain(piece)) if "." in piece else ""
        words = re.findall(r"[a-z0-9]+", piece.lower())
        starts = {"".join(words[:k]) for k in range(1, len(words) + 1)}
        hit = None
        for name, key, domain_key in keys:
            if n == key or (dom and dom in (key, domain_key)) or \
                    (len(n) >= 4 and n == domain_key) or \
                    (len(key) >= 4 and key in starts):
                hit = name
                break
        if hit:
            if hit not in found:
                found.append(hit)
        elif piece not in unknown:
            unknown.append(piece[:60])
    return found[:MAX_COMPANIES], unknown[:MAX_COMPANIES]


def overrides() -> dict[str, str]:
    """alerts.yaml: replacement answers keyed by Stripe subscription id, for
    customers who asked to change their list. Holds no emails or names."""
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        data = yaml.safe_load(OVERRIDES_FILE.read_text("utf-8")) or {}
    except yaml.YAMLError:
        return {}
    lists = data.get("lists") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in (lists or {}).items()}


# ---------------------------------------------------------------- the window

def run_window(runs_file: Path | None = None) -> tuple[datetime, datetime]:
    """(start, end) of the period this run is responsible for.

    end is when this run's crawl finished; start is when the previous run's
    crawl finished. Consecutive runs therefore cover time with no gap and no
    overlap, so a change or a new customer is handled exactly once.
    """
    runs_file = runs_file or storage.runs_file()
    rows = []
    if runs_file.exists():
        for line in runs_file.read_text("utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def finished(r: dict) -> datetime | None:
        try:
            at = datetime.fromisoformat(str(r["at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at + timedelta(seconds=float(r.get("seconds") or 0))

    ends = [e for e in (finished(r) for r in rows) if e]
    now = datetime.now(timezone.utc)
    end = ends[-1] if ends else now
    start = ends[-2] if len(ends) > 1 else end - timedelta(days=1)
    return start, end


# ---------------------------------------------------------------- changes

def _events():
    """Confirmed change events, labelled exactly as the website shows them."""
    from . import insights
    from . import history as hist
    from .site import event_sentence
    vendors = {v["name"]: dict(v) for v in load_vendors()}
    records = {}
    for path in sorted(storage.PLANS.glob("*.json")):
        try:
            rec = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(rec, dict) and rec.get("plans"):
            records[path.stem] = rec
    for name in vendors:
        rec = records.get(storage.slugify(name))
        if rec:
            vendors[name]["currency"] = rec.get("currency", "USD")
    history = {s: h for s, h in hist.load_all().items() if s in records}
    events = insights.build_events(storage.read_changes(), vendors,
                                   addon_lookup=insights.addon_lookup_from(history, records))
    out = []
    for e in events:
        if e.get("status") != "confirmed":
            continue
        out.append({"vendor": e["vendor"], "date": e["date"],
                    "detected_at": e.get("detected_at") or e["date"],
                    "text": event_sentence(e),
                    "url": f"{BASE_URL}/v/{storage.slugify(e['vendor'])}.html"})
    return out, records


def _when(e: dict) -> datetime:
    raw = str(e.get("detected_at") or e.get("date") or "")
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        d = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- email text

def _wrap(title: str, inner: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#F6F7F9;
font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0B1324">
<div style="max-width:560px;margin:0 auto;padding:24px 20px">
<p style="font-weight:700;font-size:16px;margin:0 0 18px">PriceTrail</p>
<div style="background:#fff;border:1px solid #E3E7ED;border-radius:10px;padding:22px">
<h1 style="font-size:20px;margin:0 0 14px">{html.escape(title)}</h1>
{inner}
</div>
<p style="font-size:12px;color:#556072;margin:16px 4px 0">You get this because
you subscribe to PriceTrail price alerts. To change which companies are watched,
reply to this email. To cancel, use the link in your Stripe receipt.</p>
</div></body></html>"""


def _li(items: list[str]) -> str:
    return ("<ul style=\"padding-left:18px;margin:8px 0 14px\">" +
            "".join(f"<li style=\"margin:6px 0\">{i}</li>" for i in items) + "</ul>")


def _company_link(name: str) -> str:
    return (f'<a href="{BASE_URL}/v/{storage.slugify(name)}.html" '
            f'style="color:#1D3FCF">{html.escape(name)}</a>')


def welcome_email(found: list[str], unknown: list[str]) -> tuple[str, str]:
    n = len(found) + len(unknown)
    inner = (f"<p>Thanks for subscribing. From today we check these pricing "
             f"pages every day and email you when anything changes:</p>"
             + (_li([_company_link(c) for c in found]) if found else ""))
    if unknown:
        inner += ("<p>We don't track these yet. They are usually added within "
                  "2 working days, and your Monday summary will show them once "
                  "they are live:</p>" + _li([html.escape(u) for u in unknown]))
    if not found and not unknown:
        inner += ("<p>We couldn't read a list of companies from your order. "
                  "Reply to this email with up to 10 names and we'll set it up.</p>")
    inner += ("<p>Every alert shows the old price, the new price and the date, "
              "with a link to the full history.</p>")
    return (f"You're set up: we're watching {n} compan{'y' if n == 1 else 'ies'}",
            _wrap("Your price alerts are on", inner))


def change_email(changes: list[dict]) -> tuple[str, str]:
    names = sorted({c["vendor"] for c in changes})
    subject = (f"Price change: {names[0]}" if len(names) == 1
               else f"Pricing changes at {len(names)} of your competitors")
    items = [f"<strong>{html.escape(c['text'])}</strong><br>"
             f"<span style=\"color:#556072;font-size:13px\">Confirmed "
             f"{html.escape(c['date'])} &middot; <a href=\"{c['url']}\" "
             f"style=\"color:#1D3FCF\">see the history</a></span>"
             for c in changes]
    inner = ("<p>We confirmed these changes on their pricing pages:</p>"
             + _li(items))
    return subject, _wrap("A competitor changed its pricing", inner)


def weekly_email(found: list[str], unknown: list[str], week: list[dict],
                 records: dict) -> tuple[str, str]:
    rows = []
    for name in found:
        mine = [c for c in week if c["vendor"] == name]
        rec = records.get(storage.slugify(name)) or {}
        checked = str(rec.get("captured_at") or "")[:10]
        status = (f"{len(mine)} change{'s' if len(mine) != 1 else ''} this week"
                  if mine else "no change this week")
        rows.append(f"{_company_link(name)} &mdash; {status}"
                    + (f" <span style=\"color:#556072\">(read {html.escape(checked)})</span>"
                       if checked else ""))
    for u in unknown:
        rows.append(f"{html.escape(u)} &mdash; not tracked yet, being added")
    total = len(week)
    subject = ("Your weekly price check: no changes this week" if not total
               else f"Your weekly price check: {total} change{'s' if total != 1 else ''}")
    inner = _li(rows) if rows else "<p>No companies on your list yet. Reply to add some.</p>"
    if week:
        inner += "<p>This week's changes:</p>" + _li(
            [html.escape(c["text"]) for c in week])
    return subject, _wrap("Your competitors this week", inner)


def owner_email(customer: str, found: list[str], unknown: list[str]) -> tuple[str, str]:
    inner = (f"<p>New price alerts customer: <strong>{html.escape(customer)}"
             f"</strong></p><p>Already tracked:</p>"
             + (_li([html.escape(f) for f in found]) if found else "<p>None</p>"))
    if unknown:
        inner += ("<p><strong>Not tracked yet &mdash; paste these to Claude "
                  "to add them:</strong></p>" + _li([html.escape(u) for u in unknown]))
    else:
        inner += "<p>Nothing to do: everything they asked for is tracked.</p>"
    return "New PriceTrail alerts customer", _wrap("New customer", inner)


# ---------------------------------------------------------------- sending

class Mailer:
    def __init__(self, api_key: str, sender: str, reply_to: str,
                 preview_dir: Path | None = None):
        self.api_key, self.sender, self.reply_to = api_key, sender, reply_to
        self.preview_dir = preview_dir
        self.sent = 0
        self.failed = 0

    def send(self, to: str, subject: str, body: str) -> None:
        if self.preview_dir is not None:
            self.preview_dir.mkdir(parents=True, exist_ok=True)
            name = f"{self.sent + 1:03d}-{_norm(subject)[:40]}.html"
            (self.preview_dir / name).write_text(
                f"<!-- to: {html.escape(to)} | {html.escape(subject)} -->\n{body}",
                encoding="utf-8")
            self.sent += 1
            return
        payload = {"from": self.sender, "to": [to], "subject": subject,
                   "html": body}
        if self.reply_to:
            payload["reply_to"] = self.reply_to
        try:
            _http("POST", RESEND_API,
                  {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"},
                  json.dumps(payload).encode("utf-8"))
            self.sent += 1
        except RuntimeError as e:
            self.failed += 1
            print(f"  email failed: {e}")


# ---------------------------------------------------------------- run

def run(preview: bool = False, weekly: bool | None = None,
        now: datetime | None = None) -> dict:
    stripe_key = os.environ.get("STRIPE_KEY", "").strip()
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    owner = os.environ.get("OWNER_EMAIL", "").strip()
    sender = os.environ.get("ALERTS_FROM", "").strip() or DEFAULT_FROM
    if not stripe_key or not (resend_key or preview) or not owner:
        print("Price alerts: not set up yet (STRIPE_KEY, RESEND_API_KEY and "
              "OWNER_EMAIL are needed). Nothing sent.")
        return {"skipped": True}

    now = now or datetime.now(timezone.utc)
    if weekly is None:
        weekly = (os.environ.get("ALERTS_WEEKLY", "").lower() == "true"
                  and now.weekday() == 0)

    start, end = run_window()
    customers = subscribers(stripe_key)
    swap = overrides()
    vendors = load_vendors()
    events, records = _events()
    new_changes = [e for e in events if start < _when(e) <= end]
    week_changes = [e for e in events if _when(e) > now - timedelta(days=7)]

    mail = Mailer(resend_key, sender, owner,
                  Path("alert-preview") if preview else None)
    welcomed = alerted = summaries = 0
    for c in customers:
        found, unknown = match_companies(swap.get(c["subscription"], c["answer"]),
                                         vendors)
        joined = datetime.fromtimestamp(c["created"], timezone.utc)
        if start < joined <= end:
            mail.send(c["email"], *welcome_email(found, unknown))
            mail.send(owner, *owner_email(c["email"], found, unknown))
            welcomed += 1
        mine = [e for e in new_changes if e["vendor"] in found]
        if mine:
            mail.send(c["email"], *change_email(mine))
            alerted += 1
        if weekly and joined <= end:
            mail.send(c["email"], *weekly_email(
                found, unknown, [e for e in week_changes if e["vendor"] in found],
                records))
            summaries += 1

    summary = {"customers": len(customers), "welcomed": welcomed,
               "alerted": alerted, "weekly": summaries,
               "changes_in_window": len(new_changes),
               "sent": mail.sent, "failed": mail.failed}
    # Counts only: this log is public.
    print("Price alerts: " + ", ".join(f"{k} {v}" for k, v in summary.items()))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--preview", action="store_true",
                    help="write the emails to ./alert-preview instead of sending")
    args = ap.parse_args()
    try:
        result = run(preview=args.preview)
    except RuntimeError as e:
        print(f"Price alerts: stopped -- {e}")
        return 1
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
