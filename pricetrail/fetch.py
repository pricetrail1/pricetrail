"""
Fetching pages, politely.

Crawling other companies' sites is fine when you do it the way search engines
do: obey robots.txt, identify yourself honestly, go slowly, and back off when
told to. Doing this properly is not optional politeness -- it is what keeps you
out of trouble and keeps your crawler unblocked.
"""

from __future__ import annotations

import os
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

# Identify yourself. Put a real page at this URL explaining what the bot does
# and how to ask you to stop. Site owners who can reach you rarely block you.
def _default_user_agent() -> str:
    """Identify the bot honestly, using the real site address when we know it.

    Site owners who can find out who is crawling them and how to complain
    almost never block you. Ones who can't, sometimes do.
    """
    site = os.environ.get("SITE_BASE_URL")
    if not site:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if "/" in repo:
            owner, name = repo.split("/", 1)
            site = f"https://{owner}.github.io/{name}"
    contact = f"+{site.rstrip('/')}/bot.html; " if site else ""
    return f"PriceTrailBot/0.1 ({contact}pricing change tracker)"


USER_AGENT = (
    "PriceTrailBot/0.1 (+https://getpricetrail.com/bot.html; "
    "pricing change tracker)"
)

DEFAULT_TIMEOUT = 20
DEFAULT_DELAY = 3.0  # seconds between requests to the same host

# Which "visitor" every page is read as.
#
# This matters more than it looks. Plenty of pricing pages pick their currency
# from the visitor's language header and IP, so the SAME page can say $49 to
# one reader and GBP 39 to another. The daily crawl runs on GitHub's servers in
# the United States, but this crawler was previously announcing itself as
# en-GB, which meant some vendors were read in one currency from GitHub and a
# different one from a laptop in Britain -- and an archive that flips currency
# depending on where it ran is worth nothing.
#
# Pinning it to one locale is what makes yesterday's figure and today's figure
# the same measurement. en-US is chosen to match where the crawl actually
# runs. Override with CRAWL_LOCALE only if you intend to re-baseline the whole
# archive, because changing it changes what the pages say.
CRAWL_LOCALE = os.environ.get("CRAWL_LOCALE", "en-US,en;q=0.9")


@dataclass
class FetchResult:
    url: str
    status: int
    html: str | None
    error: str | None = None
    blocked_by_robots: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.html)


class Fetcher:
    def __init__(self, delay: float = DEFAULT_DELAY,
                 user_agent: str = USER_AGENT):
        self.delay = delay
        self.user_agent = user_agent
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": CRAWL_LOCALE,
        })
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    # ---------- robots.txt ----------

    def _robots_for(self, url: str):
        host = urlparse(url).netloc
        if host in self._robots:
            return self._robots[host]

        scheme = urlparse(url).scheme or "https"
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = self._session.get(
                f"{scheme}://{host}/robots.txt", timeout=DEFAULT_TIMEOUT
            )
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp = None  # no robots.txt means no restrictions
        except requests.RequestException:
            rp = None
        self._robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        if rp is None:
            return True
        return rp.can_fetch(self.user_agent, url)

    # ---------- throttling ----------

    def _wait_turn(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_hit.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self._last_hit[host] = time.monotonic()

    # ---------- self-healing URLs ----------

    # Companies rebrand and reorganise constantly, so pricing URLs rot. Rather
    # than making you hand-check a list every few months, try the obvious
    # variants and then just read the homepage and follow its pricing link --
    # which is what a person would do.
    PATH_GUESSES = ("/pricing", "/pricing/", "/plans", "/plans/",
                    "/en/pricing", "/pricing/plans")

    # Only hunt when the page looks MOVED. A 403 or 429 means the site is
    # actively refusing us, so trying eight more URLs on the same host just
    # earns eight more refusals and makes us look like an attacker.
    RECOVERABLE = (404, 410, 0)

    def worth_recovering(self, result: "FetchResult") -> bool:
        return (not result.blocked_by_robots
                and result.status in self.RECOVERABLE)

    def find_pricing_url(self, url: str) -> str | None:
        """Given a dead pricing URL, hunt for the live one. None if hopeless."""
        parsed = urlparse(url)
        root = f"{parsed.scheme or 'https'}://{parsed.netloc}"

        # 1. Flip the trailing slash -- fixes a surprising number of them.
        flipped = url.rstrip("/") if url.endswith("/") else url + "/"
        if self._reachable(flipped):
            return flipped

        # 2. Try the usual paths.
        for guess in self.PATH_GUESSES:
            candidate = root + guess
            if candidate.rstrip("/") == url.rstrip("/"):
                continue
            if self._reachable(candidate):
                return candidate

        # 3. Give up guessing and read the homepage like a person would.
        home = self.get(root, retries=0)
        if not home.ok:
            return None
        for href in re.findall(r'href=["\']([^"\']+)["\']', home.html or ""):
            if not re.search(r"(pricing|/plans)", href, re.I):
                continue
            if href.startswith("//"):
                candidate = f"{parsed.scheme or 'https'}:{href}"
            elif href.startswith("/"):
                candidate = root + href
            elif href.startswith("http"):
                candidate = href
            else:
                continue
            if urlparse(candidate).netloc != parsed.netloc:
                continue  # don't wander onto a different site
            if self._reachable(candidate):
                return candidate
        return None

    def _reachable(self, url: str) -> bool:
        if not self.allowed(url):
            return False
        result = self.get(url, retries=0)
        return result.ok and len(result.html or "") > 500

    # ---------- main entry point ----------

    def get(self, url: str, retries: int = 2) -> FetchResult:
        if not self.allowed(url):
            return FetchResult(url, 0, None,
                               error="disallowed by robots.txt",
                               blocked_by_robots=True)

        for attempt in range(retries + 1):
            self._wait_turn(url)
            try:
                resp = self._session.get(url, timeout=DEFAULT_TIMEOUT)
            except requests.RequestException as exc:
                if attempt == retries:
                    return FetchResult(url, 0, None, error=str(exc))
                time.sleep(2 ** attempt)
                continue

            # Told to slow down or come back later: obey, don't hammer.
            if resp.status_code in (429, 503):
                wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 3)))
                if attempt == retries:
                    return FetchResult(url, resp.status_code, None,
                                       error=f"rate limited ({resp.status_code})")
                time.sleep(min(wait, 120))
                continue

            if resp.status_code != 200:
                return FetchResult(url, resp.status_code, None,
                                   error=f"HTTP {resp.status_code}")

            return FetchResult(url, 200, resp.text)

        return FetchResult(url, 0, None, error="exhausted retries")
