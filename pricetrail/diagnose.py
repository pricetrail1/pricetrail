"""
Work out why one vendor is failing.

    py -m pricetrail.diagnose gorgias

Fetches that vendor's page once and shows you every stage: what came back,
what survived cleaning, and which check rejected it. Saves both the raw HTML
and the cleaned text so you can open them and look.

No API key, no cost.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import storage
from .clean import clean_html, content_hash, looks_like_pricing_page
from .fetch import Fetcher
from .run import load_vendors

OUT = storage.ROOT / "diagnose"

VERDICTS = {
    "js_json": ("Prices ARE in the page, but inside a <script> tag as data,\n"
                "  not as visible HTML. The site builds the page in your "
                "browser.\n  This is fixable -- see the samples above for what "
                "the data looks\n  like. Worth doing only if several vendors "
                "hit the same problem."),
    "js_empty": ("No prices anywhere in the HTML. The page fetches them "
                 "separately\n  after loading, so there is nothing to read.\n"
                 "  Fixes, easiest first:\n"
                 "    1. Try a different URL. Some sites have both /pricing "
                 "and\n       /pricing/plans, and only one is server-"
                 "rendered.\n"
                 "    2. Drop this vendor. One awkward site is not worth "
                 "blocking\n       the other twenty-one."),
    "over_cleaned": ("Prices are in plain HTML, but the cleaner threw them "
                     "away.\n  That is a bug in clean.py, not a problem with "
                     "the site.\n  Open the raw file, find the price, and see "
                     "what element it sits\n  in -- the class name will match "
                     "one of the NOISE_PATTERNS."),
    "wrong_page": ("Fetched something that is not a pricing page. Usually a "
                   "redirect\n  to a homepage, a login wall, or a region "
                   "picker.\n  Open the URL in your browser and see where you "
                   "land."),
    "blocked": ("The site is refusing the crawler. Nothing to do with the "
                "URL.\n  Leave it. Some sites block all bots, and that is "
                "their right."),
}

# Money as it appears in HTML: $49, £29.99, "price": 49, amount: 4900
PRICE_IN_HTML = re.compile(
    r'[$\u00a3\u20ac]\s?\d{1,4}(?:[.,]\d{2})?'
    r'|"(?:price|amount|cost|monthly)\w*"\s*:\s*"?\d+',
    re.I)


def _hunt_prices(raw: str, cleaned: str) -> tuple[list[str], list[str]]:
    """Find price-looking strings in the raw HTML and in the cleaned text.

    The gap between the two is the whole diagnosis: prices in raw but not in
    cleaned means they are recoverable.
    """
    in_raw, seen = [], set()
    for m in PRICE_IN_HTML.finditer(raw):
        snippet = raw[max(0, m.start() - 45):m.end() + 45]
        snippet = " ".join(snippet.split())
        key = m.group()[:12]
        if key in seen:
            continue
        seen.add(key)
        in_raw.append(snippet)
        if len(in_raw) >= 6:
            break
    in_clean = [m.group() for m in PRICE_IN_HTML.finditer(cleaned)]
    return in_raw, in_clean


def diagnose(slug: str) -> int:
    vendors = {v["slug"]: v for v in load_vendors()}
    vendor = vendors.get(slug)
    if not vendor:
        print(f"No vendor called '{slug}'. Options:\n  " +
              "\n  ".join(sorted(vendors)), file=sys.stderr)
        return 1

    url = vendor["pricing_url"]
    domain = urlparse(url).netloc
    print(f"\n  {vendor['name']}\n  {url}\n" + "-" * 58)

    fetcher = Fetcher()

    if not fetcher.allowed(url):
        print("  robots.txt says do not crawl this path.")
        print("  Respect it. Remove this vendor from vendors.yaml.")
        return 0
    print("  robots.txt        allowed")

    result = fetcher.get(url)
    if not result.ok:
        print(f"  fetch             FAILED ({result.error})")
        if result.status in (403, 429):
            print(f"\n  {VERDICTS['blocked']}")
        else:
            found = fetcher.find_pricing_url(url)
            print(f"\n  Found a working URL instead: {found}" if found
                  else "\n  Could not find a working URL on that domain.")
        return 0

    raw = result.html or ""
    print(f"  fetch             OK ({len(raw):,} chars of HTML)")

    cleaned = clean_html(raw, domain)
    print(f"  after cleaning    {len(cleaned):,} chars")

    OUT.mkdir(exist_ok=True)
    (OUT / f"{slug}-raw.html").write_text(raw, encoding="utf-8")
    (OUT / f"{slug}-cleaned.txt").write_text(cleaned, encoding="utf-8")

    ok = looks_like_pricing_page(cleaned)
    print(f"  looks like pricing  {'yes' if ok else 'NO'}")
    if ok:
        print(f"  content hash      {content_hash(cleaned)[:16]}")

    # Don't guess from lengths -- go and look for actual prices.
    in_raw, in_clean = _hunt_prices(raw, cleaned)
    print(f"  prices in HTML    {len(in_raw)}{'+' if len(in_raw) >= 6 else ''} found")
    print(f"  prices surviving  {len(in_clean)} found")

    print("\n  First 400 characters of what the AI would read:")
    print("  " + "-" * 56)
    for line in (cleaned[:400] or "(nothing)").split("\n")[:14]:
        print(f"  | {line[:70]}")
    print("  " + "-" * 56)

    if in_raw and not in_clean:
        print("\n  Prices found in the raw HTML but not in the cleaned text:")
        for snippet in in_raw[:4]:
            print(f"  | ...{snippet[:76]}...")

    if not ok:
        if in_raw and not in_clean:
            looks_like_json = any(
                c in s for s in in_raw[:4] for c in ('":', "':", "{", "}")
            )
            verdict = "js_json" if looks_like_json else "over_cleaned"
        elif not in_raw:
            verdict = "js_empty" if len(raw) > 20_000 else "wrong_page"
        else:
            verdict = "wrong_page"
        print(f"\n  Diagnosis: {VERDICTS[verdict]}")

    print(f"\n  Saved for you to open:\n    {OUT / f'{slug}-raw.html'}"
          f"\n    {OUT / f'{slug}-cleaned.txt'}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose one vendor's page.")
    ap.add_argument("slug", help="vendor slug, e.g. gorgias")
    return diagnose(ap.parse_args().slug)


if __name__ == "__main__":
    raise SystemExit(main())
