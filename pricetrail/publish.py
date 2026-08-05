"""
The tool. One command, crawl to website.

    python -m pricetrail.publish --demo --serve   # see it working right now
    python -m pricetrail.publish --serve          # rebuild from real data, preview
    python -m pricetrail.publish                  # crawl, then build
    python -m pricetrail.publish --no-crawl       # rebuild site only, no API spend

--demo fills the archive with invented data so you can see the finished site
before you have six months of real history. It refuses to run if real data
already exists, so it cannot overwrite your archive.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
import webbrowser
from functools import partial

from . import demo, site, storage
from .run import run as run_crawler


def has_real_data() -> bool:
    for path in storage.PLANS.glob("*.json"):
        try:
            if not json.loads(path.read_text("utf-8")).get("demo"):
                return True
        except json.JSONDecodeError:
            continue
    return False


def serve(directory, port: int = 8000, open_browser: bool = True) -> None:
    handler = partial(http.server.SimpleHTTPRequestHandler,
                      directory=str(directory))
    socketserver.TCPServer.allow_reuse_address = True
    for attempt in range(10):
        try:
            with socketserver.TCPServer(("", port + attempt), handler) as srv:
                url = f"http://localhost:{port + attempt}"
                print(f"\n  Preview at {url}")
                print("  Press Ctrl+C to stop.\n")
                if open_browser:
                    try:
                        webbrowser.open(url)
                    except Exception:
                        pass
                srv.serve_forever()
        except OSError:
            continue
        except KeyboardInterrupt:
            print("\n  Stopped.")
            return
    print(f"Could not find a free port between {port} and {port + 9}.",
          file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Crawl pricing pages and build the website.")
    ap.add_argument("--demo", action="store_true",
                    help="fill with sample data so the site has content "
                         "(refuses to overwrite real data)")
    ap.add_argument("--no-crawl", action="store_true",
                    help="skip crawling; rebuild the site from existing data")
    ap.add_argument("--serve", action="store_true",
                    help="preview the site locally when finished")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--budget", type=float, default=0.50,
                    help="max USD to spend on this run's extractions")
    args = ap.parse_args()

    if args.demo:
        if has_real_data():
            print("Real crawl data already exists. Refusing to overwrite it "
                  "with demo data.\nDelete data/plans/ first if you really "
                  "want a fresh demo.", file=sys.stderr)
            return 1
        stats = demo.generate()
        print(f"  Demo data: {stats['vendors']} vendors, "
              f"{stats['changes']} changes")
    elif not args.no_crawl:
        code = run_crawler(budget_usd=args.budget)
        if code != 0:
            return code
        print()

    result = site.build()

    print("-" * 58)
    print(f"  Built {result['pages']} pages")
    print(f"    {result['vendors']} vendor pages")
    print(f"    {result['comparisons']} comparison pages")
    print(f"    {result['changes']} changes in the feed")
    print(f"  Output: {result['out']}")
    print("-" * 58)

    if result.get("demo"):
        print("\n  !! This site was built from SAMPLE DATA. Every price is")
        print("     invented. Every page is marked and set to noindex.")
        print("     Delete data/ and run a real crawl before publishing.")

    if site.BASE_URL == "https://example.com":
        print("\n  Before publishing: set BASE_URL in pricetrail/site.py to "
              "your real domain,\n  or search engines will index the wrong "
              "links.")

    if args.serve:
        serve(result["out"], args.port)
    else:
        print(f"\n  Open: {result['out']}/index.html")
        print("  Or run again with --serve for a local preview.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
