"""
The daily job.

    python -m pricetrail.run              # full run
    python -m pricetrail.run --dry-run    # fetch and hash only, no API spend
    python -m pricetrail.run --only stripe --only intercom
    python -m pricetrail.run --budget 0.50

The whole business is this loop:

    fetch -> clean -> hash -> [unchanged? stop] -> extract -> diff -> store

The hash gate is the entire cost model. Roughly 95% of checks stop there and
cost nothing. Remove it and this goes from about $2/month to about $60/month
for the same output.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import yaml
from urllib.parse import urlparse

from . import storage
from .clean import clean_html, content_hash, looks_like_pricing_page
from .diff import diff_pricing, fingerprint
from .extract import ExtractionError, estimate_cost_usd, extract_pricing
from .fetch import Fetcher

VENDORS_FILE = storage.ROOT / "vendors.yaml"

# Hard ceiling per run. A bug that loops over 200 vendors 50 times should cost
# you pennies and an error message, not your entire budget.
DEFAULT_BUDGET_USD = 0.50


def due_today(vendor: dict, force: bool = False) -> bool:
    """Should this vendor be checked on this run?

    vendors.yaml marks each vendor daily or weekly. Weekly ones are checked on
    Mondays only. Roughly halves the API bill for almost no loss: a long-tail
    vendor that reprices on a Wednesday is recorded the following Monday, and
    the archive is about what changed, not the hour it happened.
    """
    if force:
        return True
    if vendor.get("crawl_tier", "daily") != "weekly":
        return True
    return datetime.now(timezone.utc).weekday() == 0  # Monday


def load_vendors() -> list[dict]:
    data = yaml.safe_load(VENDORS_FILE.read_text(encoding="utf-8"))
    vendors = []
    for v in data["vendors"]:
        v = dict(v)
        v.setdefault("slug", storage.slugify(v["name"]))
        v.setdefault("category", "uncategorised")
        vendors.append(v)
    return vendors


def _rewrite_vendor_urls(repaired: list[tuple[str, str, str]]) -> None:
    """Patch vendors.yaml in place, preserving comments and layout."""
    text = VENDORS_FILE.read_text(encoding="utf-8")
    for _slug, old, new in repaired:
        text = text.replace(f"pricing_url: {old}", f"pricing_url: {new}")
    VENDORS_FILE.write_text(text, encoding="utf-8")


def run(dry_run: bool = False, only: list[str] | None = None,
        budget_usd: float = DEFAULT_BUDGET_USD,
        fix_urls: bool = False, all_vendors: bool = False,
        force: bool = False) -> int:
    """Crawl the vendor list.

    force=True ignores the content hash and re-extracts every page even if it
    has not changed. You need this whenever the extraction prompt changes:
    without it, improved extraction only reaches a vendor the next time that
    vendor happens to edit their page, which could be months. Costs a full
    extraction run, so it is opt-in.
    """
    vendors = load_vendors()
    if only:
        wanted = {s.lower() for s in only}
        vendors = [v for v in vendors
                   if v["slug"] in wanted or v["name"].lower() in wanted]
        if not vendors:
            print(f"No vendors matched {sorted(wanted)}", file=sys.stderr)
            return 1

    # Long-tail vendors are only due on Mondays.
    if not only:
        due = [v for v in vendors if due_today(v, force=all_vendors)]
        skipped = len(vendors) - len(due)
        vendors = due
        if skipped:
            print(f"  (skipping {skipped} weekly vendors -- not Monday. "
                  f"Use --all to override.)\n")

    fetcher = Fetcher()
    state = storage.load_state()
    spent = 0.0

    stats = dict(checked=0, unchanged=0, extracted=0, changes=0,
                 queued=0, failed=0, skipped_robots=0, noisy=0, awaiting=0)
    repaired: list[tuple[str, str, str]] = []

    print(f"Run started {datetime.now(timezone.utc).isoformat()} "
          f"({len(vendors)} vendors, dry_run={dry_run}"
          f"{', FORCED re-extraction' if force else ''})\n")

    for vendor in vendors:
        slug, name, url = vendor["slug"], vendor["name"], vendor["pricing_url"]
        entry = state.setdefault(slug, {})
        stats["checked"] += 1

        result = fetcher.get(url)

        # Dead link? Go and find the new one instead of just reporting a
        # failure and leaving it for you to fix by hand.
        if not result.ok and fetcher.worth_recovering(result):
            found = fetcher.find_pricing_url(url)
            if found:
                print(f"  MOVED {name}: {url}\n         -> {found}")
                entry["suggested_url"] = found
                repaired.append((slug, url, found))
                result = fetcher.get(found)
                url = found

        if result.blocked_by_robots:
            stats["skipped_robots"] += 1
            entry["status"] = "robots_disallowed"
            print(f"  SKIP  {name}: robots.txt disallows this path")
            continue

        if not result.ok:
            stats["failed"] += 1
            entry["status"] = "error"
            entry["last_error"] = result.error
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
            if result.status in (403, 429):
                hint = "  (site is blocking the crawler, not a bad URL)"
            elif entry["consecutive_failures"] >= 3:
                hint = "  <-- FIX THIS URL"
            else:
                hint = ""
            print(f"  FAIL  {name}: {result.error}{hint}")
            continue

        entry["consecutive_failures"] = 0
        cleaned = clean_html(result.html, urlparse(url).netloc)

        if not looks_like_pricing_page(cleaned):
            stats["failed"] += 1
            entry["status"] = "not_a_pricing_page"
            print(f"  FAIL  {name}: fetched OK but does not look like pricing "
                  f"({len(cleaned)} chars) <-- FIX THIS URL")
            continue

        new_hash = content_hash(cleaned)
        entry["last_checked"] = storage.today()
        entry["status"] = "ok"

        if entry.get("hash") == new_hash and not force:
            # An identical page IS the second reading.
            #
            # This was a deadlock, and it stopped the whole product working.
            # The hash is saved when a change is first seen, so the next day
            # the page looks unchanged and gets skipped -- and the reading
            # held for confirmation waits forever. 15 of 23 vendors were sat
            # in that queue, which is why nothing had ever reached the change
            # log. The crawler was finding price changes and silently burying
            # every one.
            #
            # A page whose bytes are identical to yesterday's would extract
            # identically, so re-reading it would cost money to learn nothing.
            # The held reading is confirmed here instead: same page, second
            # day, so it stands.
            pending = storage.load_pending(slug)
            baseline = storage.load_plans(slug)
            if pending is not None and baseline is not None:
                if not pending.get("plans") and baseline.get("plans"):
                    entry["status"] = "extraction_lost_all_plans"
                    storage.clear_pending(slug)
                    stats["kept_old"] = stats.get("kept_old", 0) + 1
                    print(f"  KEEP  {name}: held reading had no plans at all "
                          f"-- keeping the old figures, flagged for review")
                    continue
                confirmed = diff_pricing(name, baseline, pending)
                storage.save_plans(slug, pending)
                storage.clear_pending(slug)
                publishable = [c for c in confirmed if c.publishable]
                if publishable:
                    storage.append_changes(publishable)
                    stats["changes"] += len(publishable)
                    for c in publishable:
                        print(f"  PRICE {c.headline()}")
                else:
                    stats["noisy"] += 1
                    print(f"  noise {name}: held reading confirmed, "
                          f"nothing worth publishing")
                continue
            stats["unchanged"] += 1
            print(f"  same  {name}")
            continue

        # Hash moved. Track how often, so we can spot pages that churn their
        # markup daily without ever changing a price.
        #
        # The fingerprint is NOT saved yet. It gets committed only once we
        # have successfully extracted and stored the pricing. Saving it here
        # would mean a dry run -- or a failed extraction -- convinced the
        # crawler it had already read a page it never read, and it would skip
        # that page until the site next changed.
        entry["hash_changes"] = entry.get("hash_changes", 0) + 1
        storage.save_snapshot(slug, cleaned)

        if dry_run:
            print(f"  DIFF  {name}: content changed (dry run, no extraction)")
            continue

        cost = estimate_cost_usd(cleaned)
        if spent + cost > budget_usd:
            print(f"\nBudget ceiling ${budget_usd:.2f} reached. Stopping.")
            break

        try:
            extracted = extract_pricing(cleaned, name)
        except ExtractionError as exc:
            stats["failed"] += 1
            entry["status"] = "extraction_error"
            entry["last_error"] = str(exc)
            print(f"  FAIL  {name}: extraction failed: {exc}")
            continue

        spent += cost
        storage.record_spend(cost)
        stats["extracted"] += 1

        plan_count = len(extracted["plans"])

        # A pricing page with fewer than two plans is almost always a failed
        # read, not a company with one plan. Usually an interactive pricing
        # slider, where the price is calculated in the browser and never
        # appears in the HTML.
        if plan_count < 2:
            entry["status"] = "suspicious_extraction"
            print(f"  CHECK {name}: only {plan_count} plan(s) found "
                  f"-- verify against the live page")
            print(f"        If the page uses a pricing slider, remove this "
                  f"vendor from vendors.yaml.")

        baseline = storage.load_plans(slug)
        if baseline and baseline.get("demo"):
            # Sample data is invented. Comparing a real price against a made-up
            # one would generate a fake "price change".
            print(f"  NOTE  {name}: discarding sample data")
            baseline = None

        entry["hash"] = new_hash

        if baseline is None:
            storage.save_plans(slug, extracted)
            storage.clear_pending(slug)
            print(f"  NEW   {name}: baseline captured ({plan_count} plans)")
            continue

        # ---- confirmation ----
        #
        # A reading is never published on the strength of one look. It has to
        # appear twice in a row saying the same thing.
        #
        # This is what would have killed every false alarm on the first live
        # run: Intercom read $29, then $19, then $29 again; Mailchimp appeared
        # to pull its pricing when really the page had half-loaded. None of
        # those agreed with themselves twice, so none would have been published.
        #
        # The cost is that a genuine change takes two runs to show up instead
        # of one. Worth it -- a wrong price emailed to a customer loses them
        # for good, while a change arriving a day later loses nothing.

        now_print = fingerprint(extracted)

        if now_print == fingerprint(baseline):
            storage.clear_pending(slug)
            stats["noisy"] += 1
            print(f"  noise {name}: page changed, pricing did not")
            continue

        # A forced run means the way we READ pages changed, not the prices.
        # Every difference it turns up is us correcting ourselves, so logging
        # them as price changes would be a lie: fixing Intercom's figures
        # produced "price_increase 29 -> 39, +34.5%" when Intercom had not
        # touched a thing. Across 23 vendors that is dozens of invented rises
        # on the change log, in the hero panel and in the newsletter.
        #
        # This sits ABOVE the confirmation hold on purpose. Waiting for a
        # second reading exists to catch a vendor's page wobbling; it has
        # nothing to say about us correcting our own extraction, and making
        # someone run a forced crawl twice to get their data fixed is just a
        # trap.
        if force:
            # The same protection the normal path has: a forced re-read that
            # comes back with no plans is an unreadable page, not a company
            # that deleted its pricing. Without this, one bad forced run wipes
            # a vendor off the site and 404s a URL Google had indexed.
            if not extracted.get("plans") and baseline.get("plans"):
                entry["status"] = "extraction_lost_all_plans"
                storage.clear_pending(slug)
                stats["kept_old"] = stats.get("kept_old", 0) + 1
                print(f"  KEEP  {name}: forced re-read found no plans, but "
                      f"{len(baseline['plans'])} were on record -- keeping the "
                      f"old figures and flagging for review")
                continue
            storage.save_plans(slug, extracted)
            storage.clear_pending(slug)
            entry["status"] = "ok"
            stats["rebaselined"] = stats.get("rebaselined", 0) + 1
            print(f"  RESET {name}: re-read and re-baselined "
                  f"({len(extracted['plans'])} plans, no change logged)")
            continue

        pending = storage.load_pending(slug)
        if pending is None or fingerprint(pending) != now_print:
            storage.save_pending(slug, extracted)
            stats["awaiting"] += 1
            flip = " (disagrees with the last one)" if pending else ""
            print(f"  HOLD  {name}: change seen, waiting for a second "
                  f"reading{flip}")
            continue

        # Two runs in a row said the same thing. Believe it -- unless what
        # they agree on is that the page has no prices at all.
        #
        # A page that cannot be read is not a company that deleted its
        # pricing. Layout changes, pricing sliders that compute in the browser,
        # and half-loaded pages all produce zero plans, and they can easily do
        # it twice running. Accepting that would overwrite a good record with
        # an empty one, and an empty record means no vendor page is built at
        # all: the URL starts returning 404 to a search engine that had
        # already indexed it, and the vendor vanishes from the site with
        # nothing reported anywhere.
        #
        # So a reading that removes every plan is never published. The old
        # figures stay up, marked stale rather than deleted, and the vendor is
        # queued for a human to look at.
        if not extracted.get("plans") and baseline.get("plans"):
            entry["status"] = "extraction_lost_all_plans"
            storage.clear_pending(slug)
            # Its own counter. Filing this under "awaiting confirmation" made
            # the run summary claim a change was pending when in fact a write
            # had been refused -- two opposite things reported as one number.
            stats["kept_old"] = stats.get("kept_old", 0) + 1
            print(f"  KEEP  {name}: read no plans twice, but "
                  f"{len(baseline['plans'])} were on record -- keeping the "
                  f"old figures and flagging for review")
            print(f"        Check the live page: a pricing slider or a "
                  f"layout change usually causes this.")
            continue

        changes = diff_pricing(name, baseline, extracted)
        storage.save_plans(slug, extracted)
        storage.clear_pending(slug)

        if not changes:
            stats["noisy"] += 1
            print(f"  noise {name}: confirmed, but nothing worth reporting")
            continue

        published, queued = storage.append_changes(changes)
        stats["changes"] += published
        stats["queued"] += queued
        print(f"  CHANGE {name}: {published} published, {queued} to review "
              f"(confirmed on two runs)")
        for c in changes:
            mark = "*" if c.publishable else "?"
            print(f"         {mark} {c.headline()}  (conf {c.confidence:.2f})")

    pruned = sum(storage.prune_snapshots(v["slug"]) for v in vendors)
    if pruned:
        print(f"\n  Pruned {pruned} old snapshots (kept everything from the "
              f"last 2 months, then one per month).")

    storage.save_state(state)

    if repaired:
        if fix_urls:
            _rewrite_vendor_urls(repaired)
            print(f"\n  Updated {len(repaired)} URL(s) in vendors.yaml.")
        else:
            print(f"\n  {len(repaired)} URL(s) have moved. Re-run with "
                  f"--fix-urls to write the new ones into vendors.yaml.")

    print("\n" + "-" * 60)
    print(f"checked {stats['checked']} | unchanged {stats['unchanged']} | "
          f"extracted {stats['extracted']} | noisy {stats['noisy']}")
    print(f"changes published {stats['changes']} | "
          f"awaiting confirmation {stats['awaiting']} | "
          f"queued for review {stats['queued']} | failed {stats['failed']}")
    if stats.get("rebaselined"):
        print(f"re-baselined {stats['rebaselined']} vendor(s) from a forced "
              f"re-read. No changes were logged: a forced run corrects how we "
              f"read pages, it does not mean prices moved.")
    if stats.get("kept_old"):
        print(f"kept old figures for {stats['kept_old']} vendor(s) whose page "
              f"could not be read -- see the status page")
    print(f"this run ${spent:.4f} | month to date "
          f"${storage.month_to_date_spend():.4f}")

    # A crawler that dies quietly is worse than one that dies loudly: every
    # silent day is an archive day you can never get back. Exiting non-zero
    # turns the GitHub run red, which emails you.
    checked = stats["checked"]
    broken = stats["failed"] + stats["skipped_robots"]
    if checked and broken / checked > 0.5:
        print(f"\n!! {broken} of {checked} vendors failed. Something is "
              f"wrong -- check the output above.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the pricing crawler.")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and hash only; never call the paid API")
    ap.add_argument("--only", action="append",
                    help="limit to these vendor slugs (repeatable)")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD,
                    help="max USD to spend on this run")
    ap.add_argument("--fix-urls", action="store_true",
                    help="write recovered URLs back into vendors.yaml")
    ap.add_argument("--all", action="store_true", dest="all_vendors",
                    help="check every vendor, ignoring weekly scheduling")
    ap.add_argument("--force", action="store_true",
                    help="re-extract even if the page has not changed. Use "
                         "after changing the extraction prompt.")
    args = ap.parse_args()
    return run(dry_run=args.dry_run, only=args.only, budget_usd=args.budget,
               fix_urls=args.fix_urls, all_vendors=args.all_vendors,
               force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
