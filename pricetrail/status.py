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

import yaml

from . import storage

VENDORS = storage.ROOT / "vendors.yaml"


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
        "prices_not_in_page":
            "page loaded without its prices -- old figures kept, retrying daily",
        "holding_for_stability":
            "a large change is being held for a few days before publishing",
        "internal_error": None,
    }
    # state.json is appended to and never pruned, so a vendor removed from
    # vendors.yaml leaves its entry behind forever. zoho-desk was sitting in
    # there weeks after it stopped being tracked, showing on the status page
    # as a live vendor. Only report on things actually being crawled.
    try:
        tracked = {storage.slugify(v["name"]) for v in
                   (yaml.safe_load(VENDORS.read_text(encoding="utf-8")) or {})
                   .get("vendors", [])} if VENDORS.exists() else set()
    except Exception:
        tracked = set()
    # Only hide orphans when the vendor list plainly belongs to this archive.
    # If nothing in state matches it -- an unreadable file, a different
    # project, a test fixture -- filtering would blank the whole status page
    # and hide real failures. Showing one stale row is a far smaller problem
    # than showing none of the broken ones.
    if not tracked & set(state):
        tracked = set()
    for slug, entry in state.items():
        if tracked and slug not in tracked:
            continue
        status = entry.get("status", "")
        rec = records.get(slug)
        if (not status or status == "ok") and rec is not None \
                and not rec.get("plans"):
            # Read fine, but nothing usable came out of it, so the company
            # has no page on the site. Healthy-looking but invisible.
            failing.append((slug, "page reads, but no plans could be "
                                  "extracted -- not shown on the site"))
            entry["_display"] = "no_plans_on_record"
            continue
        if not status or status == "ok":
            continue
        why = WHY.get(status, status.replace("_", " "))
        failing.append((slug, why or entry.get("last_error", status)))

    # A vendor nobody has read in a fortnight is quietly broken.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    failing_slugs = {s for s, _ in failing}
    for slug, entry in state.items():
        if tracked and slug not in tracked:
            continue
        if slug in failing_slugs:
            continue  # already listed with its specific reason
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
        "tracked": tracked,
        "changes": len(changes),
        "pending": pending,
        "review": review,
        "snapshots": snapshots,
        "spend_mtd": storage.month_to_date_spend(),
        "since": storage.recording_since(),
        "last_checked": last_checked,
        "state": state,
    }


LABELS = {
    "ok": "Healthy",
    "error": "Fetch failed",
    "extraction_error": "Reading failed",
    "not_a_pricing_page": "Not a pricing page",
    "robots_disallowed": "Blocked by robots.txt",
    "suspicious_extraction": "Too few plans read",
    "extraction_lost_all_plans": "No plans read — old figures kept",
    "prices_not_in_page": "Prices missing from page — old figures kept",
    "holding_for_stability": "Large change on hold",
    "internal_error": "Unexpected error — skipped",
    "no_plans_on_record": "No plans extracted — not on site",
}

# Statuses where the site is still showing correct, confirmed figures and the
# crawler is simply being careful. Amber, not red.
CAUTION = {"prices_not_in_page", "holding_for_stability",
           "extraction_lost_all_plans", "suspicious_extraction",
           "no_plans_on_record"}


def render(esc, page, back_link, pretty_date, SITE_NAME) -> str:
    """Build the page. Helpers are passed in to avoid a circular import."""
    d = gather()
    runs = storage.read_runs(30)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    def _st(slug):
        e = d["state"].get(slug, {})
        return e.get("_display") or e.get("status")
    failing_hard = [(s, w) for s, w in d["failing"] if _st(s) not in CAUTION]
    caution = [(s, w) for s, w in d["failing"] if _st(s) in CAUTION]

    if not d["last_checked"]:
        health, tone, note = "No data yet", "warn", "No crawl has run yet."
    elif d["last_checked"] < (datetime.now(timezone.utc)
                              - timedelta(hours=36)).strftime("%Y-%m-%d"):
        health, tone = "Crawler may have stopped", "bad"
        note = (f"Nothing has been checked since {pretty_date(d['last_checked'])}. "
                f"GitHub pauses scheduled jobs in quiet repositories; any commit "
                f"wakes it.")
    elif failing_hard:
        health, tone = "Running, with problems", "warn"
        note = (f"{len(failing_hard)} compan{'ies' if len(failing_hard) != 1 else 'y'} "
                f"could not be read on the last attempt. Everything else is "
                f"normal, and the site keeps showing their last confirmed figures.")
    else:
        health, tone = "All systems normal", "ok"
        note = (f"Last checked {pretty_date(d['last_checked'])}. Runs every day "
                f"at about 06:00 UTC.")
    dot = {"ok": "", "warn": " warn", "bad": " bad"}[tone]

    rows = []
    for slug, entry in sorted(d["state"].items()):
        if d.get("tracked") and slug not in d["tracked"]:
            continue
        st = entry.get("_display") or entry.get("status", "unknown")
        label = LABELS.get(st, st.replace("_", " ").capitalize())
        cls = "ok" if st == "ok" else ("warn" if st in CAUTION else "up")
        if st in CAUTION:
            err = ""
        err = entry.get("last_error", "") if st not in ("ok",) else ""
        rows.append(f"""
      <tr>
        <td class="name">{esc(slug)}</td>
        <td data-l="Status"><span class="badge {cls}">{esc(label)}</span></td>
        <td data-l="Last good check" data-v="{esc(entry.get('last_checked', ''))}"><time>{esc(pretty_date(entry.get('last_checked')))}</time></td>
        <td class="num" data-l="Failures in a row">{entry.get('consecutive_failures', 0)}</td>
        <td class="num" data-l="Page edits seen">{entry.get('hash_changes', 0)}</td>
        <td data-l="Detail"><span class="small muted">{esc(err[:140])}</span></td>
      </tr>""")

    run_rows, bars = [], []
    for r in runs[:14]:
        bad = r.get("failed", 0)
        cls = "bad" if r.get("checked") and bad / max(r["checked"], 1) > 0.5 \
            else ("warn" if bad else "")
        run_rows.append(f"""
      <tr>
        <td class="mono">{esc(str(r.get('at', ''))[:16].replace('T', ' '))}</td>
        <td class="num" data-l="Checked">{r.get('checked', 0)}</td>
        <td class="num" data-l="Unchanged">{r.get('unchanged', 0)}</td>
        <td class="num" data-l="Re-read">{r.get('extracted', 0)}</td>
        <td class="num" data-l="Published">{r.get('changes', 0)}</td>
        <td class="num" data-l="Held">{r.get('awaiting', 0) + r.get('kept_old', 0)}</td>
        <td class="num" data-l="Failed">{bad}</td>
        <td class="num" data-l="Cost">${r.get('spent_usd', 0):.2f}</td>
      </tr>""")
    for r in reversed(runs[:30]):
        bad = r.get("failed", 0)
        cls = "bad" if r.get("checked") and bad / max(r["checked"], 1) > 0.5 \
            else ("warn" if bad else "")
        h = 100 if not r.get("checked") else max(25, 100 - bad * 8)
        bars.append(f'<i class="{cls}" style="height:{h}%" '
                    f'title="{esc(str(r.get("at", ""))[:10])}: {bad} failed"></i>')

    problems = ""
    if failing_hard or caution or d["stale"]:
        items = "".join(f"<li><strong>{esc(s)}</strong> — {esc(w)}</li>"
                        for s, w in failing_hard + caution)
        items += "".join(f"<li><strong>{esc(s)}</strong> — not read since "
                         f"{esc(pretty_date(w))}</li>" for s, w in d["stale"])
        problems = f"""
  <section class="card card-pad" style="margin-top:1.25rem">
    <h2 style="font-size:1.05rem;margin-bottom:.6rem">Needs a look</h2>
    <ul class="provenance">{items}</ul>
    <p class="provenance" style="margin-top:.75rem">Amber entries need no
      action: the site keeps showing confirmed figures and the crawler retries
      every day. A company that stays red for a week usually means its pricing
      page moved or now calculates prices in the browser — diagnose with
      <code>py -m pricetrail.diagnose &lt;name&gt;</code>.</p>
  </section>"""

    runs_block = ""
    if run_rows:
        runs_block = f"""
  <section class="card" style="margin-top:1.25rem">
    <div class="card-pad" style="padding-bottom:.5rem"><h2 style="font-size:1.05rem">Recent runs</h2>
      <div class="bars" style="margin-top:.75rem" aria-hidden="true">{''.join(bars)}</div></div>
    <div class="tbl-scroll"><table class="stack">
      <caption class="vh">Recent crawler runs</caption>
      <thead><tr><th scope="col">Started (UTC)</th><th class="num" scope="col">Checked</th>
        <th class="num" scope="col">Unchanged</th><th class="num" scope="col">Re-read</th>
        <th class="num" scope="col">Published</th><th class="num" scope="col">Held</th>
        <th class="num" scope="col">Failed</th><th class="num" scope="col">Cost</th></tr></thead>
      <tbody>{''.join(run_rows)}</tbody></table></div>
  </section>"""

    body = f"""
<div class="wrap">
  <header class="page-head">
    {back_link()}
    <h1>System status</h1>
    <p class="lede">Is the crawler running, what did the last runs do, and
      which companies need attention. Rebuilt after every daily check.</p>
  </header>
  <div class="card health"><i class="dot{dot}" aria-hidden="true"></i>
    <div><h2>{esc(health)}</h2><p>{esc(note)}</p></div></div>
  <div style="margin-top:1.25rem">
  <div class="stats" style="--n:4">
    <div class="stat"><span class="v">{d['vendors_ok']}</span><span class="l">Companies with current pricing</span></div>
    <div class="stat"><span class="v">{len(failing_hard)}</span><span class="l">Failing on last attempt</span></div>
    <div class="stat"><span class="v">{d['pending']}</span><span class="l">Awaiting a second reading</span></div>
    <div class="stat"><span class="v">{d['review']}</span><span class="l">Held for review</span></div>
    <div class="stat"><span class="v">{d['changes']}</span><span class="l">Changes published</span></div>
    <div class="stat"><span class="v">{d['snapshots']}</span><span class="l">Page versions archived</span></div>
    <div class="stat"><span class="v">${d['spend_mtd']:.2f}</span><span class="l">Reading cost this month</span></div>
    <div class="stat"><span class="v">{esc(pretty_date(d['since']))}</span><span class="l">Recording since</span></div>
  </div></div>
  {problems}
  {runs_block}
  <section class="card" style="margin-top:1.25rem">
    <div class="card-pad" style="padding-bottom:.5rem"><h2 style="font-size:1.05rem">Every company</h2>
      <p class="provenance">{d['vendors_known']} tracked. “Page edits seen” counts how often a
        page's text changed — almost always more often than its prices did.</p></div>
    <div class="tbl-scroll"><table class="stack" data-sortable>
      <caption class="vh">Crawler status for every company</caption>
      <thead><tr><th data-sort="text" scope="col">Company</th><th data-sort="text" scope="col">Status</th>
        <th data-sort="text" scope="col">Last good check</th><th class="num" data-sort="num" scope="col">Failures in a row</th>
        <th class="num" data-sort="num" scope="col">Page edits seen</th><th data-sort="off" scope="col">Detail</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>
  </section>
</div>"""
    return page(f"System status — {SITE_NAME}",
                "Crawler health, recent runs and per-company status for the "
                "PriceTrail pricing archive.",
                body, "status.html")
