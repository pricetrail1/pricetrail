"""
Turn the change log into things people read.

    python -m pricetrail.report digest --days 7
    python -m pricetrail.report review

The site itself is built by pricetrail.publish, not here -- this module only
produces text you read or send.

Note that the digest is assembled from your own structured data, not from
scraped page text. You are publishing facts you recorded (a price was X, now
it is Y), which is very different from republishing someone's page. Keep it
that way.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import storage


def _vendor_categories() -> dict[str, str]:
    data = yaml.safe_load((storage.ROOT / "vendors.yaml").read_text("utf-8"))
    return {v["name"]: v.get("category", "uncategorised")
            for v in data["vendors"]}


def digest(days: int = 7) -> str:
    """Weekly summary, grouped by category. This is your newsletter."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cats = _vendor_categories()

    recent = [
        c for c in storage.read_changes()
        if _parse(c.get("detected_at")) and _parse(c["detected_at"]) >= cutoff
    ]

    if not recent:
        return (f"# Pricing changes, last {days} days\n\n"
                "Nothing moved. That is a real finding, not a failure -- most "
                "weeks are quiet, and knowing a category is stable is worth "
                "something to a buyer.\n")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for c in recent:
        grouped[cats.get(c["vendor"], "uncategorised")].append(c)

    lines = [f"# Pricing changes, last {days} days", ""]
    lines.append(f"{len(recent)} verified changes across "
                 f"{len({c['vendor'] for c in recent})} vendors.")
    lines.append("")

    for category in sorted(grouped):
        lines.append(f"## {category.replace('-', ' ').title()}")
        lines.append("")
        for c in sorted(grouped[category], key=lambda r: r["vendor"]):
            lines.append(f"- {_line(c)}")
        lines.append("")

    return "\n".join(lines)


def _line(c: dict) -> str:
    vendor, plan = c["vendor"], c.get("plan")
    where = f" ({plan})" if plan else ""
    t, old, new = c["change_type"], c.get("old_value"), c.get("new_value")

    if t == "price_increase":
        return f"**{vendor}**{where} raised {c['field']} {old} to {new} {c.get('note','')}".strip()
    if t == "price_decrease":
        return f"**{vendor}**{where} cut {c['field']} {old} to {new} {c.get('note','')}".strip()
    if t == "plan_added":
        return f"**{vendor}** added a new plan: {plan}"
    if t == "plan_removed":
        return f"**{vendor}** removed the {plan} plan"
    if t == "pricing_hidden":
        return f"**{vendor}** took pricing off its public page"
    if t == "feature_moved_out":
        return f"**{vendor}**{where} no longer lists '{old}'"
    if t == "feature_added":
        return f"**{vendor}**{where} now lists '{new}'"
    if t == "limit_changed":
        return f"**{vendor}**{where} changed its {c['field']} limit: {old} to {new}"
    return f"**{vendor}**{where} {t}: {old} to {new}"


def review_queue() -> str:
    """Everything the pipeline was not confident enough to publish.

    Working this queue every morning is the only real manual job in the whole
    business. Budget about ten minutes. It shrinks as your prompts improve.
    """
    if not storage.REVIEW.exists():
        return "Review queue is empty.\n"

    rows = [json.loads(ln) for ln in
            storage.REVIEW.read_text("utf-8").splitlines() if ln.strip()]
    if not rows:
        return "Review queue is empty.\n"

    rows.sort(key=lambda r: r.get("confidence", 0))
    out = [f"{len(rows)} items need a human decision", ""]
    for i, c in enumerate(rows, 1):
        out.append(f"{i}. [{c.get('confidence', 0):.2f}] {_line(c)}")
        if c.get("note"):
            out.append(f"   note: {c['note']}")
    out.append("")
    out.append("Verify against the live page. Delete the line from "
               "data/review_queue.jsonl once handled, or move it into "
               "data/changes.jsonl if it was real.")
    return "\n".join(out)


def _money(currency: str, value) -> str:
    if value is None:
        return "-"
    symbol = {"USD": "$", "GBP": "\u00a3", "EUR": "\u20ac"}.get(currency, "")
    return f"{symbol}{value:g}" if symbol else f"{value:g} {currency}"


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Reports from the change log.")
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("digest", help="weekly summary / newsletter")
    d.add_argument("--days", type=int, default=7)
    sub.add_parser("review", help="items needing a human decision")
    args = ap.parse_args()

    if args.command == "digest":
        print(digest(args.days))
    else:
        print(review_queue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
