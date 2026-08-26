"""
A health page for the crawler, published with the site.

Deliberately not a desktop app. An app would need your computer on and the
program running; this is a page that rebuilds itself every morning whether or
not you are anywhere near a keyboard. Bookmark it and you can check the whole
system from a phone.

It answers the questions you would otherwise dig through GitHub Actions logs
for: is it still running, what is it costing, and which vendors are broken.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import storage


def gather() -> dict:
    """Everything worth knowing about the state of the archive."""
    state = storage.load_state()
    records, failing, stale = {}, [], []

    for path in storage.PLANS.glob("*.json"):
        try:
            records[path.stem] = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            failing.append((path.stem, "stored record is unreadable"))

    # Anything that is not "ok" needs a human. This used to be a whitelist of
    # known bad statuses, which meant every new status added to run.py was
    # silently dropped from this page -- the flag got set and nobody was ever
    # told. Inverting it means a status invented tomorrow still surfaces, with
    # its raw name if nobody has written a friendly label yet. An ugly label
    # is a far smaller problem than a vendor quietly failing for weeks.
    WHY = {
        "error": None,                      # use last_error, it is specific
        "extraction_error": None,
        "not_a_pricing_page": None,
        "robots_disallowed": "blocked by robots.txt",
        "suspicious_extraction": "fewer than 2 plans found",
        "extraction_lost_all_plans":
            "read no plans at all -- old figures kept, check the live page",
    }
    for slug, entry in state.items():
        status = entry.get("status", "")
        if not status or status == "ok":
            continue
        why = WHY.get(status, status.replace("_", " "))
        failing.append((slug, why or entry.get("last_error", status)))

    # A vendor nobody has read in a fortnight is quietly broken.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    for slug, entry in state.items():
        last = entry.get("last_checked", "")
        if last and last < cutoff:
            stale.append((slug, last))

    changes = storage.read_changes()
    pending = len(list(storage.PENDING.glob("*.json"))) \
        if storage.PENDING.exists() else 0
    review = 0
    if storage.REVIEW.exists():
        review = sum(1 for ln in storage.REVIEW.read_text("utf-8").splitlines()
                     if ln.strip())

    snapshots = sum(len(list(d.glob("*.txt")))
                    for d in storage.SNAPSHOTS.glob("*") if d.is_dir())
    last_checked = max((e.get("last_checked", "") for e in state.values()),
                       default="")

    return {
        "vendors_ok": len(records),
        "vendors_known": len(state),
        "failing": sorted(failing),
        "stale": sorted(stale),
        "changes": len(changes),
        "pending": pending,
        "review": review,
        "snapshots": snapshots,
        "spend_mtd": storage.month_to_date_spend(),
        "since": storage.recording_since(),
        "last_checked": last_checked,
        "state": state,
    }


def render(esc, page, back_link, pretty_date, SITE_NAME) -> str:
    """Build the page. Helpers are passed in to avoid a circular import."""
    d = gather()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not d["last_checked"]:
        health, note = "unknown", "No crawl has run yet."
    elif d["failing"]:
        health = "problems"
        note = (f"{len(d['failing'])} vendor(s) are failing. Everything else "
                f"is running normally.")
    elif d["last_checked"] < today:
        health = "ok"
        note = (f"Last crawled {pretty_date(d['last_checked'])}. Runs daily at "
                f"06:00 UTC.")
    else:
        health, note = "ok", "Crawled today. Everything is running normally."

    rows = []
    for slug, entry in sorted(d["state"].items()):
        status = entry.get("status", "unknown")
        # Unknown statuses fall back to their own name with underscores
        # removed, so a new one reads as English rather than as code.
        label = {"ok": "OK", "error": "Fetch failed",
                 "extraction_error": "Extraction failed",
                 "not_a_pricing_page": "Not a pricing page",
                 "robots_disallowed": "Blocked by robots.txt",
                 "suspicious_extraction": "Too few plans",
                 "extraction_lost_all_plans": "No plans read \u2014 old figures kept",
                 }.get(status, status.replace("_", " ").capitalize())
        good = status == "ok"
        rows.append(f"""
      <tr>
        <td data-l="Vendor">{esc(slug)}</td>
        <td data-l="Status">{'' if good else '<strong>'}{esc(label)}{'' if good else '</strong>'}</td>
        <td data-l="Last read">{esc(pretty_date(entry.get('last_checked')))}</td>
        <td class="num" data-l="Page edits">{entry.get('hash_changes', 0)}</td>
      </tr>""")

    problems = ""
    if d["failing"] or d["stale"]:
        items = "".join(
            f"<li>{esc(slug)} \u2014 {esc(why)}</li>"
            for slug, why in d["failing"]
        ) + "".join(
            f"<li>{esc(slug)} \u2014 not read since {esc(pretty_date(when))}</li>"
            for slug, when in d["stale"]
        )
        problems = f"""
  <section class="section">
    <div class="section-head"><h2>Needs a look</h2></div>
    <ul class="provenance">{items}</ul>
    <p class="note" style="margin-top:1rem">Diagnose any of these with
      <code>py -m pricetrail.diagnose &lt;name&gt;</code>. If a vendor prices
      with a slider there is nothing in the HTML to read, and the right move is
      to remove it rather than leave a broken row on the site.</p>
  </section>"""

    body = f"""
<div class="wrap">
  <section class="section">
    {back_link()}
    <div class="section-head"><h1>System status</h1>
      <span class="aside">{esc(health)}</span></div>
    <p class="note" style="margin-bottom:1.5rem">{esc(note)}</p>

    <div class="grid grid-3">
      <div class="cell"><span class="stat">{d['vendors_ok']}</span>
        <p>Vendors with current pricing</p></div>
      <div class="cell"><span class="stat">{len(d['failing'])}</span>
        <p>Vendors failing</p></div>
      <div class="cell"><span class="stat">{d['snapshots']}</span>
        <p>Page versions archived</p></div>
      <div class="cell"><span class="stat">{d['changes']}</span>
        <p>Changes published</p></div>
      <div class="cell"><span class="stat">{d['pending']}</span>
        <p>Awaiting a second reading</p></div>
      <div class="cell"><span class="stat">{d['review']}</span>
        <p>In the review queue</p></div>
      <div class="cell"><span class="stat">${d['spend_mtd']:.2f}</span>
        <p>API spend this month</p></div>
      <div class="cell"><span class="stat">{esc(pretty_date(d['since']))}</span>
        <p>Recording since</p></div>
    </div>
  </section>
  {problems}
  <section class="section">
    <div class="section-head"><h2>Every vendor</h2>
      <span class="aside">{d['vendors_known']} tracked</span></div>
    <div class="tbl-scroll"><table class="stack">
      <thead><tr><th>Vendor</th><th>Status</th><th>Last read</th>
        <th class="num">Page edits</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table></div>
    <p class="provenance" style="margin-top:1.5rem">
      "Page edits" counts how often the page markup changed, which is almost
      always more often than the pricing did. A high number with no recorded
      changes means the page churns its HTML, not its prices \u2014 that is
      the hash gate earning its keep.
    </p>
  </section>
</div>"""

    return page(f"System status \u2014 {SITE_NAME}",
                "Crawler health, archive size and API spend.",
                body, "status.html")
