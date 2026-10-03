"""
Data on disk -> a website.  (V2)

Every page is static HTML, generated once a day from the archive. That is
deliberate: search engines index it instantly, it loads on a bad phone
connection, it costs nothing to host, and it cannot break at 3am. One small
script adds search, sorting and filtering on top; every page is complete
without it.

What V2 changed, in one paragraph: the site now leads with confirmed price
changes and price history instead of a raw change log; the change log is
interpreted (renames folded, reversals labelled -- see insights.py) without
the raw record being touched; every vendor page has a price-history chart,
a change timeline with before/after figures, an audit trail of every
recorded version, and honest freshness taken from when the page was actually
last checked; there is site-wide search, a data/downloads page, a proper
status page and a 404 page. Every URL from V1 still exists.

The publishing rule, enforced in build(): a page is only written where there
is real data behind it. No data means no page.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import history as hist
from . import insights
from . import storage
from .diff import plan_key
from .interact import FILTER_CSS, FILTER_JS
from .theme import CSS, FONT_LINK, FONT_LINK_XML

SITE_NAME = "PriceTrail"


def _default_base_url() -> str:
    """Work out the site address instead of making you type it.

    Set SITE_BASE_URL yourself once you own a domain.
    """
    explicit = os.environ.get("SITE_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}"
    return "https://example.com"


BASE_URL = "https://getpricetrail.com"
TAGLINE = "The price history of business software."
REPO_URL = "https://github.com/pricetrail1/pricetrail"



def _settings() -> dict:
    """settings.yaml at the top of the repository: the few things a person
    might want to change without touching code (a payment link, a contact
    address, the licence price). A repository variable of the same name, in
    capitals, overrides the file. A missing or broken file means defaults."""
    try:
        data = yaml.safe_load((storage.ROOT / "settings.yaml")
                              .read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


_SETTINGS = _settings()


def _setting(name: str) -> str:
    return (os.environ.get(name.upper(), "") or
            str(_SETTINGS.get(name) or "")).strip()


# Where "report an error", "ask to be removed" and "request data" go. The
# site has no inbox of its own, so by default this opens an issue on the
# public GitHub repository. Set contact_url in settings.yaml (a mailto: link
# or a form address) to send people somewhere else -- no code change.
CONTACT_URL = _setting("contact_url") or f"{REPO_URL}/issues/new"


def signup_fields(url: str) -> tuple[str, bool]:
    """Which field names this mailing service expects.

    Services disagree, and the disagreement fails silently. Buttondown reads
    an input called "email"; Kit reads "email_address". Post the wrong one and
    the service accepts the request, finds no address, and stores nothing.

    Returns the email field name, and whether a hidden "tag" input works.
    """
    host = (url or "").lower()
    if "kit.com/forms" in host or "convertkit.com/forms" in host:
        return "email_address", False
    return "email", True


def _signup_action(raw: str) -> str:
    """Accept a bare Buttondown username as well as a full form address."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")) or "/" in raw:
        return raw
    return f"https://buttondown.com/api/emails/embed-subscribe/{raw}"


SIGNUP_URL = _signup_action(os.environ.get("SIGNUP_URL", ""))

# The commercial licence. LICENCE_URL is a checkout link from whichever
# payment service holds the account (Lemon Squeezy, Stripe Payment Links,
# Paddle, Gumroad -- any of them gives you one). Until it is set, the pricing
# page offers "ask about a licence" instead of a buy button, because a button
# that cannot take money is worse than none. Both are repository variables:
# no code change to switch on, change the price, or switch provider.
def _https_only(url: str) -> str:
    url = (url or "").strip()
    return url if url.startswith("https://") else ""


LICENCE_URL = _https_only(_setting("licence_url"))
LICENCE_PRICE = (_setting("licence_price") or "\u00a319 a month")[:40]

SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "CA$",
           "AUD": "A$", "INR": "₹"}


# ---------------------------------------------------------------- helpers

def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def money(currency: str, value, dash: str = "—") -> str:
    """Format a price. Never raises.

    An archive accumulated over years will contain records written by older
    versions of this code and rows repaired by hand, so anything unusable
    falls back to a dash rather than taking the build down.
    """
    if value is None or value == "" or isinstance(value, bool):
        return dash
    try:
        n = f"{float(value):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return esc(value)
    sym = SYMBOLS.get(str(currency or "").upper(), "")
    if sym:
        return f"{sym}{n}"
    # Currency codes come from an AI reading a third-party page: untrusted.
    code = re.sub(r"[^A-Za-z]", "", str(currency or ""))[:3].upper()
    return f"{n} {esc(code)}" if code else n


def mixed_currency_note(bench: dict) -> str:
    """One short line, shown only when a category holds more than one currency."""
    n = bench.get("excluded_other_currency", 0)
    if not n:
        return ""
    others = [c for c in bench.get("currencies", [])
              if c != bench.get("currency")]
    return (f' <span class="basis">median covers the '
            f'{esc(bench.get("currency", "USD"))} vendors only; '
            f'{n} priced in {esc(", ".join(others))}</span>')


def pretty_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d %b %Y")
    except ValueError:
        return str(iso)[:10]


def _iso_day(value) -> str:
    """YYYY-MM-DD from an ISO timestamp, a date, or a pretty date."""
    s = str(value or "")
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    try:
        return datetime.strptime(s, "%d %b %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_between(a: str, b: str) -> int:
    try:
        return (datetime.strptime(b[:10], "%Y-%m-%d")
                - datetime.strptime(a[:10], "%Y-%m-%d")).days
    except (ValueError, TypeError):
        return 0


ACRONYMS = {"crm": "CRM", "seo": "SEO", "api": "API", "hr": "HR",
            "erp": "ERP", "crm-tools": "CRM", "bi": "BI", "it": "IT",
            "saas": "SaaS", "ai": "AI"}


def title_case(slug: str) -> str:
    if slug.lower() in ACRONYMS:
        return ACRONYMS[slug.lower()]
    return " ".join(ACRONYMS.get(w.lower(), w.title())
                    for w in slug.replace("-", " ").split())


def _norm(text: str) -> str:
    """Search normalisation. Must match norm() in interact.py."""
    t = unicodedata.normalize("NFD", str(text or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _domain(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def diff_html(old, new, currency: str = "", pct: str = "") -> str:
    """Every change on the site renders through here.

    Direction is carried by a glyph AND a sign AND colour, never colour alone,
    so it still reads correctly in greyscale or with colourblindness.
    """
    try:
        rising = float(new) > float(old)
        direction = "up" if rising else "down"
        glyph = "▲" if rising else "▼"
    except (TypeError, ValueError):
        direction, glyph = "", ""
    was = money(currency, old) if currency else esc(old)
    now = money(currency, new) if currency else esc(new)
    badge = f'<span class="pct">{glyph} {esc(pct)}</span>' if pct else ""
    return (f'<span class="diff {direction}">'
            f'<span class="was">{was}</span>'
            f'<span class="arrow" aria-hidden="true">→</span>'
            f'<span class="vh">to</span>'
            f'<span class="now">{now}</span>{badge}</span>')


def pct_badge(pct: float | None) -> str:
    if pct is None:
        return ""
    up = pct > 0
    return (f'<span class="badge {"up" if up else "down"}">'
            f'{"▲" if up else "▼"} {pct:+.1f}%</span>')


def sparkline(points: list[float], width: int = 260, height: int = 56) -> str:
    """Small inline price history chart (kept for older callers)."""
    if len(points) < 2:
        return ""
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1
    step = width / (len(points) - 1)
    coords = [(i * step, height - ((v - lo) / span) * (height - 8) - 4)
              for i, v in enumerate(points)]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    lx, ly = coords[-1]
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="Price history: {money("", points[0])} to '
            f'{money("", points[-1])}">'
            f'<path class="line" d="{path}"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3"/></svg>')


def price_series(changes: list[dict], vendor: str, plan: str,
                 current: float | None) -> list[float]:
    """A plan's monthly price history, walked from the change log."""
    relevant = [c for c in changes
                if c.get("vendor") == vendor and c.get("plan") == plan
                and c.get("field") == "monthly_price"
                and isinstance(c.get("old_value"), (int, float))
                and isinstance(c.get("new_value"), (int, float))]
    relevant.sort(key=lambda c: c.get("detected_at", ""))
    if not relevant:
        return []
    series = [float(relevant[0]["old_value"])]
    series += [float(c["new_value"]) for c in relevant]
    if current is not None and series[-1] != current:
        series.append(float(current))
    return series


# ---------------------------------------------------------------- structured data

DEMO_BANNER = """
<div style="background:#B4531A;color:#fff;padding:0.7rem 1rem;font-family:
  ui-monospace,monospace;font-size:0.8rem;text-align:center">
  <strong>SAMPLE DATA — NOT REAL PRICES.</strong>
  Every figure here was randomly generated. Do not publish this site.
</div>"""

_IS_DEMO = False


def json_ld(data: dict) -> str:
    """Structured data block, escaped so untrusted text cannot close the tag."""
    payload = json.dumps(data, ensure_ascii=False)
    payload = (payload.replace("<", "\\u003c")
                      .replace(">", "\\u003e")
                      .replace("&", "\\u0026"))
    return f'<script type="application/ld+json">{payload}</script>'


def vendor_schema(name: str, record: dict, url: str) -> str:
    offers = []
    for plan in record.get("plans", []):
        price, _basis = plan_price(plan)
        if plan.get("is_addon") or plan.get("is_custom_pricing") or not price:
            continue
        offers.append({
            "@type": "Offer",
            "name": plan["name"],
            "price": price,
            "priceCurrency": record.get("currency") or "USD",
            "availability": "https://schema.org/InStock",
        })
    return json_ld({
        "@context": "https://schema.org",
        "@type": "Product",
        "name": f"{name} pricing",
        "description": f"Current and historical pricing for {name}.",
        "url": url,
        "brand": {"@type": "Brand", "name": name},
        **({"offers": {
            "@type": "AggregateOffer",
            "priceCurrency": record.get("currency") or "USD",
            "lowPrice": min(o["price"] for o in offers),
            "highPrice": max(o["price"] for o in offers),
            "offerCount": len(offers),
            "offers": offers,
        }} if offers else {}),
    })


def site_schema() -> str:
    return json_ld({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": SITE_NAME,
        "url": BASE_URL,
        "description": TAGLINE,
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{BASE_URL}/search.html?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    })


def dataset_schema(vendors: int, changes: int, since: str,
                   downloads: list[dict] | None = None) -> str:
    """Declares the archive as a Dataset, so it can be found by people looking
    for pricing data (Google Dataset Search reads this)."""
    data = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"{SITE_NAME} SaaS pricing archive",
        "description": (f"Daily structured records of published pricing for "
                        f"{vendors} B2B software vendors, recorded since "
                        f"{since}. {changes} changes logged."),
        "url": BASE_URL,
        "creator": {"@type": "Organization", "name": SITE_NAME,
                    "url": BASE_URL},
        "temporalCoverage": f"{_iso_day(since) or since}/..",
        "isAccessibleForFree": True,
        "keywords": ["SaaS pricing", "software prices", "price history",
                     "pricing changes", "helpdesk", "CRM", "email marketing"],
    }
    if downloads:
        data["distribution"] = [{
            "@type": "DataDownload",
            "encodingFormat": d["format"],
            "contentUrl": f"{BASE_URL}/{d['path']}",
            "name": d["title"],
        } for d in downloads]
    return json_ld(data)


# ---------------------------------------------------------------- shell

BRAND_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" '
             'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
             'stroke-linejoin="round"><path d="M3 17h5v-5h5V7h5"/>'
             '<circle cx="20" cy="7" r="1.6" fill="currentColor" stroke="none"/>'
             '<path d="M3 21h18" opacity=".35"/></svg>')

SEARCH_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" '
              'stroke="currentColor" stroke-width="2" stroke-linecap="round">'
              '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>')

INFO_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round">'
            '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/></svg>')

NAV = (("index.html", "Prices"), ("changes.html", "Changes"),
       ("data.html", "Data"), ("about.html", "Method"),
       ("pricing.html", "Pricing"))


def _rel(path: str) -> str:
    """Relative prefix back to site root, so the site works from a file://
    URL and from a subfolder without changes."""
    return "../" * path.count("/")


def page(title: str, description: str, body: str, path: str,
         extra_head: str = "", noindex: bool = False,
         absolute_links: bool = False) -> str:
    """The frame around every page.

    absolute_links is for 404.html, which GitHub Pages serves at whatever
    address was requested -- relative links would point somewhere else.
    """
    rel = "/" if absolute_links else _rel(path)
    canonical = f"{BASE_URL}/{path}".replace("/index.html", "/")
    if canonical.endswith("/index.html"):
        canonical = canonical[: -len("index.html")]
    banner = DEMO_BANNER if _IS_DEMO else ""
    robots = ('<meta name="robots" content="noindex,nofollow">'
              if (_IS_DEMO or noindex) else "")
    active = {p: ' aria-current="page"' for p, _ in NAV if p == path}
    nav = "".join(f'<a href="{rel}{p}"{active.get(p, "")}>{esc(label)}</a>'
                  for p, label in NAV)
    og_image = f"{BASE_URL}/assets/og.png"
    return f"""<!DOCTYPE html>
<html lang="en" data-root="{esc(rel if not absolute_links else '/')}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{robots}
<title>{'[SAMPLE DATA] ' if _IS_DEMO else ''}{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#FFFFFF" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#111722" media="(prefers-color-scheme: dark)">
<link rel="icon" href="{rel}assets/icon.svg" type="image/svg+xml">
<link rel="alternate" type="application/rss+xml" title="{SITE_NAME} changes"
      href="{BASE_URL}/feed.xml">
{FONT_LINK}
<link rel="stylesheet" href="{rel}assets/style.css">
<script defer src="{rel}assets/search-index.js"></script>
<script defer src="{rel}assets/app.js"></script>
{extra_head}
</head>
<body>
{banner}
<a class="skip" href="#main">Skip to content</a>
<header class="site-header"><div class="wrap hdr">
  <a class="brand" href="{rel}index.html" aria-label="{SITE_NAME} home">{BRAND_SVG}PriceTrail</a>
  <nav class="nav" aria-label="Main">{nav}</nav>
  <form class="hdr-search" id="site-search" role="search" action="{rel}search.html" method="get">
    <label class="search-box">{SEARCH_SVG}<span class="vh">Search companies</span>
      <input id="q" name="q" type="search" placeholder="Search companies, plans…"
        autocomplete="off" role="combobox" aria-autocomplete="list"
        aria-controls="search-pop" aria-expanded="false">
      <span class="kbd" aria-hidden="true">/</span></label>
    <div class="search-pop" id="search-pop" role="listbox" aria-label="Search results" hidden></div>
  </form>
</div></header>
<main id="main">{body}</main>
{_footer(rel)}
</body>
</html>
"""


def _footer(rel: str) -> str:
    return f"""<footer class="site-footer"><div class="wrap">
  <div class="foot-grid">
    <div class="foot-brand">
      <a class="brand" href="{rel}index.html">{BRAND_SVG}PriceTrail</a>
      <p>An independent, public record of what business software costs and how
        that changes. Not affiliated with, or paid by, any vendor listed.</p>
    </div>
    <div><h2>Explore</h2><ul>
      <li><a href="{rel}index.html#prices">All prices</a></li>
      <li><a href="{rel}changes.html">Price changes</a></li>
      <li><a href="{rel}week.html">This week</a></li>
      <li><a href="{rel}all.html">Every page</a></li>
      <li><a href="{rel}search.html">Search</a></li></ul></div>
    <div><h2>Data</h2><ul>
      <li><a href="{rel}data.html">Downloads</a></li>
      <li><a href="{rel}pricing.html">Pricing</a></li>
      <li><a href="{BASE_URL}/feed.xml">RSS feed</a></li>
      <li><a href="{rel}about.html">Method</a></li>
      <li><a href="{rel}status.html">System status</a></li></ul></div>
    <div><h2>About</h2><ul>
      <li><a href="{rel}bot.html">About the crawler</a></li>
      <li><a href="{esc(CONTACT_URL)}" rel="noopener">Report an error</a></li>
      <li><a href="{rel}privacy.html">Privacy</a></li>
      <li><a href="{rel}terms.html">Terms of use</a></li></ul></div>
  </div>
  <p class="disclaimer">Figures are recorded from vendors' own public pricing
    pages and may lag a change by a day or more. Every price is shown in the
    currency the vendor's page displayed &mdash; nothing here is converted. This is
    a record, not advice: always confirm with the vendor before you buy.</p>
</div></footer>"""


def subscribe_block(prefix: str = "", compact: bool = False) -> str:
    """The one place a reader can turn into an audience.

    With no mailing service configured there is no form, because a form that
    silently throws addresses away is worse than none. The feed and the data
    downloads are offered instead.
    """
    if not SIGNUP_URL:
        if compact:
            return ""
        return f"""
<section class="section"><div class="wrap"><div class="card cta">
  <div>
    <h2>Follow the changes</h2>
    <p>Every confirmed change is published to an RSS feed you can follow in
      any reader, and the whole archive can be downloaded as CSV or JSON. No
      account, no email.</p>
  </div>
  <div class="hero-actions" style="margin-top:0">
    <a class="btn btn-primary" href="{BASE_URL}/feed.xml">RSS feed</a>
    <a class="btn btn-ghost" href="{prefix}data.html">Get the data</a>
    <a class="btn btn-ghost" href="{prefix}changes.html">Browse all changes</a>
  </div>
</div></div></section>"""

    email_field, _ = signup_fields(SIGNUP_URL)
    form = f"""
  <form class="signup" action="{esc(SIGNUP_URL)}" method="post"
        target="_blank" rel="noopener">
    <label class="vh" for="su-email{'-c' if compact else ''}">Email address</label>
    <input id="su-email{'-c' if compact else ''}" type="email"
           name="{email_field}"
           required autocomplete="email" placeholder="you@company.com">
    <button type="submit">Email me price changes</button>
  </form>
  <p class="signup-note">One email a week, only when something actually
    changed. Unsubscribe in one click. Your address is never sold or shared.</p>"""

    if compact:
        return f"""
<section class="section-tight"><div class="wrap">
  <div class="cta-strip">
    <div><strong>Get told when a price moves.</strong>
      <span>Checked daily. We email only when something changed.</span>
    </div>{form}
  </div>
</div></section>"""

    return f"""
<section class="section"><div class="wrap"><div class="card cta">
  <div>
    <h2>Never be surprised by a price rise</h2>
    <p>When a tracked price changes and the change is confirmed, you get an
      email the same week &mdash; with the old figure, the new one, and the
      date.</p>
    {form}
  </div>
</div></div></section>"""


def back_link(prefix: str = "", label: str = "All prices") -> str:
    return (f'<p class="backlink"><a href="{prefix}index.html">'
            f'← {esc(label)}</a></p>')


def breadcrumb(prefix: str, trail: list[tuple[str, str | None]]) -> str:
    """A real trail home, plus the schema Google reads for the breadcrumb
    line under a search result."""
    items, links = [], []
    for i, (label, href) in enumerate(trail, start=1):
        if href:
            links.append(f'<a href="{prefix}{href}">{esc(label)}</a>')
            url = f"{BASE_URL}/{href}".replace("/index.html", "/")
            items.append({"@type": "ListItem", "position": i,
                          "name": label, "item": url})
        else:
            links.append(f'<span aria-current="page">{esc(label)}</span>')
            items.append({"@type": "ListItem", "position": i, "name": label})
    schema = json.dumps({"@context": "https://schema.org",
                         "@type": "BreadcrumbList",
                         "itemListElement": items},
                        ensure_ascii=False).replace("<", "\\u003c")
    sep = ' <span class="crumb-sep" aria-hidden="true">/</span> '
    return (f'<nav class="crumbs" aria-label="Breadcrumb">{sep.join(links)}</nav>'
            f'<script type="application/ld+json">{schema}</script>')


def stats_row(items: list[tuple[str, str]]) -> str:
    cells = "".join(f'<div class="stat"><span class="v">{v}</span>'
                    f'<span class="l">{esc(l)}</span></div>' for v, l in items)
    return f'<div class="stats" style="--n:{len(items)}">{cells}</div>'


# ---------------------------------------------------------------- pricing maths

def plan_price(plan: dict) -> tuple[float | None, str]:
    """The headline price for a plan, and what it is based on.

    Many pricing pages default their monthly/annual toggle to ANNUAL, so the
    only figure in the HTML is the annual-equivalent monthly price. Falling
    back to it -- and saying so -- is honest.
    """
    if plan.get("monthly_price"):
        return plan["monthly_price"], "monthly"
    if plan.get("annual_price_per_month"):
        return plan["annual_price_per_month"], "annual"
    return None, ""


def headline_prices(record: dict) -> tuple[float | None, float | None, str]:
    """(cheapest, dearest, basis) across a record's real, published plans."""
    priced = []
    for plan in _real_plans(record):
        if plan.get("is_custom_pricing"):
            continue
        value, basis = plan_price(plan)
        if value:
            priced.append((value, basis))
    if not priced:
        return None, None, ""
    basis = "annual" if all(b == "annual" for _, b in priced) else "monthly"
    return min(v for v, _ in priced), max(v for v, _ in priced), basis


def _real_plans(record: dict) -> list[dict]:
    """Subscription tiers only. Add-ons would drag a category's median entry
    price towards zero if counted as plans."""
    return [p for p in record.get("plans", []) if not p.get("is_addon")]


def _benchmarks(cat_names: list[str], records: dict) -> dict:
    """Category averages, computed in ONE currency only.

    The dominant currency in the category wins, vendors quoted in anything
    else are left out of the median, and the count of what was left out is
    returned so the page can say so instead of quietly hiding it.
    """
    free, seat, counted = 0, 0, 0
    by_currency: dict[str, list[float]] = {}
    seen_currencies: set[str] = set()
    for name in cat_names:
        rec = records.get(storage.slugify(name))
        if not rec or not rec.get("plans"):
            continue
        counted += 1
        cur = (rec.get("currency") or "USD").upper()
        seen_currencies.add(cur)
        low, _high, _basis = headline_prices(rec)
        plans = _real_plans(rec)
        if low:
            by_currency.setdefault(cur, []).append(low)
        if any(p.get("is_free") for p in plans):
            free += 1
        if any(p.get("is_per_seat") for p in plans):
            seat += 1
    if by_currency:
        currency = sorted(by_currency, key=lambda c: (-len(by_currency[c]), c))[0]
        entries = sorted(by_currency[currency])
        median = entries[len(entries) // 2]
        excluded = sum(len(v) for k, v in by_currency.items() if k != currency)
    else:
        currency, median, excluded = "USD", None, 0
    return {
        "median_entry": median,
        "currency": currency,
        "priced_in_currency": len(by_currency.get(currency, [])),
        "excluded_other_currency": excluded,
        "currencies": sorted(seen_currencies),
        "pct_free": (free / counted * 100) if counted else 0,
        "pct_per_seat": (seat / counted * 100) if counted else 0,
        "n": counted,
    }


def _priced_count(record: dict) -> int:
    return sum(1 for p in _real_plans(record)
               if not p.get("is_custom_pricing") and plan_price(p)[0])


def _all_features(record: dict) -> set[str]:
    out: set[str] = set()
    for plan in _real_plans(record):
        out.update(plan.get("features") or [])
    return out


def _trial_days(record: dict) -> int | None:
    days = [p.get("trial_days") for p in _real_plans(record)
            if isinstance(p.get("trial_days"), int) and p["trial_days"] > 0]
    return max(days) if days else None


def _how_charged(record: dict) -> str:
    return ("per user" if any(p.get("is_per_seat") for p in _real_plans(record))
            else "flat price")


# ---------------------------------------------------------------- freshness

def _vendor_state(name: str, ctx: dict) -> dict:
    return (ctx.get("state") or {}).get(storage.slugify(name), {}) or {}


def _tier(name: str, ctx: dict) -> str:
    return ((ctx.get("vendors", {}).get(name, {}) or {})
            .get("crawl_tier", "weekly"))


def last_checked(name: str, record: dict, ctx: dict) -> str:
    """YYYY-MM-DD of the last time the page was successfully read.

    Not the same as when the figures last changed. V1 used the record's
    capture date for both, so a page read fine every morning but unchanged
    since 10 September was announced as "23 days old ... not readable" --
    a false alarm on a page that was perfectly current.
    """
    st = _vendor_state(name, ctx)
    return (st.get("last_checked") or str(record.get("captured_at") or ""))[:10]


def staleness_warning(record: dict, name: str, ctx: dict) -> str:
    """Say so when the figures on a page have stopped being re-read.

    Based on the last successful check of the page, so a price that simply
    has not changed is never mistaken for a page that cannot be read.
    """
    checked = last_checked(name, record, ctx)
    if not checked:
        return ""
    days = _days_between(checked, _today())
    limit = 3 if _tier(name, ctx) == "daily" else 10
    if days <= limit:
        return ""
    return (f'<p class="stale-warning"><strong>These figures are '
            f'{days} days old.</strong> {esc(name)}’s pricing page has '
            f'not been read successfully since {esc(pretty_date(checked))}, so '
            f'what is below is the last good reading rather than today’s. '
            f'Check {esc(name)}’s own page before relying on it.</p>')


def _status_notice(name: str, record: dict, ctx: dict) -> str:
    """Plain-English notice for a vendor whose latest reading was held back."""
    st = _vendor_state(name, ctx).get("status", "ok")
    if st == "prices_not_in_page":
        since = _vendor_state(name, ctx).get("no_prices_since")
        return (f'<div class="notice warn">{INFO_SVG}<p><strong>Today’s '
                f'page loaded without its prices.</strong> Some pricing pages '
                f'fill their numbers in after the page loads, and PriceTrail '
                f'only records what the page itself contains. The figures below '
                f'are the last confirmed reading'
                f'{", unchanged since " + esc(pretty_date(since)) if since else ""}.'
                f'</p></div>')
    if st == "holding_for_stability":
        return (f'<div class="notice">{INFO_SVG}<p><strong>A large change is '
                f'being checked.</strong> {esc(name)}’s page currently '
                f'reads very differently from the figures on record. Big '
                f'changes are held for a few days before they are published, '
                f'so the confirmed figures are shown until then.</p></div>')
    if st in ("extraction_lost_all_plans", "suspicious_extraction"):
        return (f'<div class="notice warn">{INFO_SVG}<p><strong>The latest '
                f'reading could not be used.</strong> The last confirmed '
                f'figures are shown instead, and the page has been flagged '
                f'for a check.</p></div>')
    return ""


# ---------------------------------------------------------------- describing changes

def _describe(c: dict, vendors: dict) -> str:
    """One raw change-log entry as a short English phrase (HTML)."""
    t = c["change_type"]
    cur = vendors.get(c["vendor"], {}).get("currency", "USD")
    old, new, pct = c.get("old_value"), c.get("new_value"), c.get("note", "")
    if t in ("price_increase", "price_decrease"):
        period = "annual" if "annual" in (c.get("field") or "") else "monthly"
        return f"{period} " + diff_html(old, new, cur, pct)
    if t == "limit_changed":
        return f"{esc(c.get('field'))} limit " + diff_html(old, new, "", "")
    if t == "plan_added":
        return (f"added a plan at {money(cur, new)}" if new not in (None, "")
                else "added this plan")
    if t == "plan_removed":
        return "removed this plan"
    if t == "pricing_hidden":
        return "took pricing off its public page"
    if t == "feature_added":
        return f"now includes “{esc(new)}”"
    if t == "feature_moved_out":
        return f"no longer includes “{esc(old)}”"
    if t == "plan_renamed":
        return f"renamed from “{esc(old)}”"
    if t == "currency_changed":
        return (f"now shows prices in {esc(new)} instead of {esc(old)} "
                f"— the amounts are not comparable")
    if t == "billing_model_changed":
        return f"billing changed from {esc(old)} to {esc(new)}"
    if t == "custom_pricing_changed":
        hidden = str(new).lower() in ("true", "1")
        return ("replaced its price with “contact sales”" if hidden
                else "published a price where it previously said contact sales")
    if t == "price_availability_changed":
        gone = new in (None, "", "None")
        field = esc(c.get("field") or "price")
        return (f"stopped publishing a {field}" if gone
                else f"started publishing a {field} at {money(cur, new)}")
    if t == "pricing_published":
        return "put pricing back on its public page"
    return esc(t.replace("_", " "))


KIND_LABEL = {
    "rise": "Price rise", "cut": "Price cut", "added": "New plan",
    "removed": "Plan withdrawn", "renamed": "Renamed",
    "pricing_hidden": "Prices hidden", "pricing_published": "Prices shown",
    "custom_pricing_changed": "Contact-sales change",
    "price_availability_changed": "Price shown/withdrawn",
    "limit_changed": "Limit changed", "feature_added": "Feature added",
    "feature_moved_out": "Feature removed", "currency_changed": "Currency changed",
    "billing_model_changed": "Billing changed",
}


def kind_label(e: dict) -> str:
    label = KIND_LABEL.get(e["kind"], e["kind"].replace("_", " ").capitalize())
    if e["group"] == "addon" and e["kind"] in ("added", "removed", "renamed"):
        label = {"added": "New add-on", "removed": "Add-on withdrawn",
                 "renamed": "Add-on renamed"}[e["kind"]]
    return label


def kind_badge(e: dict) -> str:
    cls = {"rise": "up", "cut": "down"}.get(e["kind"], "")
    if not cls and e["group"] == "plan":
        cls = "accent"
    if e["group"] == "price":
        glyph = "▲ " if e["kind"] == "rise" else "▼ "
    else:
        glyph = ""
    return f'<span class="badge {cls}">{glyph}{esc(kind_label(e))}</span>'


def status_badge(e: dict) -> str:
    st = e.get("status", "confirmed")
    cls = {"confirmed": "ok", "reversed": "", "unconfirmed": "warn",
           "corrected": "info", "hidden": ""}.get(st, "")
    return (f'<span class="badge {cls}" title="{esc(e.get("status_note", ""))}">'
            f'{esc(insights.STATUS_LABEL.get(st, st.title()))}</span>')


def _before_after(e: dict) -> tuple[str, str]:
    """(before, after) cells for an event, as HTML."""
    cur = e.get("currency") or "USD"
    k = e["kind"]
    if e["group"] == "price":
        return money(cur, e["old"]), money(cur, e["new"])
    if k == "added":
        return "—", (money(cur, e["new"]) if e["new"] not in (None, "")
                          else "Listed")
    if k == "removed":
        return (money(cur, e["old"]) if e["old"] not in (None, "")
                else "Listed"), "—"
    if k == "renamed":
        return esc(e["old"]), esc(e["new"])
    if k == "pricing_hidden":
        return "Prices shown", "No prices"
    if k == "pricing_published":
        return "No prices", "Prices shown"
    if k == "custom_pricing_changed":
        to_custom = str(e["new"]).lower() in ("true", "1")
        return (("Priced", "Contact sales") if to_custom
                else ("Contact sales", "Priced"))
    if k == "price_availability_changed":
        return money(cur, e["old"]), money(cur, e["new"])
    return esc(e["old"]), esc(e["new"])


def event_sentence(e: dict) -> str:
    """Plain-text one-liner for feeds and tooltips."""
    cur = e.get("currency") or "USD"
    plan = f" {e['plan']}" if e.get("plan") else ""
    k = e["kind"]
    if e["group"] == "price":
        word = "raised" if k == "rise" else "cut"
        bill = " (billed annually)" if e["billing"] == "annual" else ""
        pct = f" ({e['pct']:+.1f}%)" if e.get("pct") is not None else ""
        return (f"{e['vendor']} {word} the{plan} price from "
                f"{_plain_money(cur, e['old'])} to {_plain_money(cur, e['new'])}"
                f"{bill}{pct}")
    if k == "renamed":
        return f"{e['vendor']} renamed “{e['old']}” to “{e['new']}”"
    if k == "added":
        price = (f" at {_plain_money(cur, e['new'])}"
                 if e["new"] not in (None, "") else "")
        what = "add-on" if e["group"] == "addon" else "plan"
        return f"{e['vendor']} added the{plan} {what}{price}"
    if k == "removed":
        what = "add-on" if e["group"] == "addon" else "plan"
        return f"{e['vendor']} withdrew the{plan} {what}"
    text = re.sub(r"<[^>]+>", "", _describe(e["raw"], {e["vendor"]: {"currency": cur}}))
    return f"{e['vendor']}{plan}: {html.unescape(text)}"


def _plain_money(cur, value) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", money(cur, value)))


def _tape(changes: list[dict], vendors: dict, prefix: str = "",
          show_vendor: bool = True) -> str:
    """A simple list of raw change-log entries (used by the digest)."""
    if not changes:
        return ('<p class="empty">No changes recorded yet.</p>')
    out = ['<ul class="provenance">']
    for c in changes:
        slug = storage.slugify(c["vendor"])
        who = (f'<a href="{prefix}v/{esc(slug)}.html">{esc(c["vendor"])}</a> '
               if show_vendor else "")
        plan = f'{esc(c["plan"])} ' if c.get("plan") else ""
        out.append(f'<li><time>{esc(pretty_date(c.get("detected_at")))}</time> '
                   f'{who}{plan}{_describe(c, vendors)}</li>')
    out.append("</ul>")
    return "".join(out)


def change_table(events: list[dict], prefix: str, table_id: str = "",
                 show_vendor: bool = True, caption: str = "",
                 filterable: bool = False) -> str:
    """The standard change table: scannable, sortable, stacks on a phone."""
    if not events:
        return ('<div class="empty"><strong>No changes recorded yet.</strong>'
                'Most software prices change a few times a year, so quiet '
                'stretches are normal.</div>')
    rows = []
    for e in events:
        before, after = _before_after(e)
        quiet = e["status"] != "confirmed"
        vendor_cell = (f'<td class="name"><a href="{prefix}v/{esc(e["slug"])}.html">'
                       f'{esc(e["vendor"])}</a>'
                       f'<span class="sub">{esc(title_case(e["category"]))}</span></td>'
                       if show_vendor else "")
        plan_txt = esc(e.get("plan") or "Whole page")
        if e["group"] == "price" and e["billing"] == "annual":
            plan_txt += '<span class="sub">billed annually</span>'
        lab = "Plan" if show_vendor else ""
        attrs = (f' data-group="{e["group"]}" data-kind="{esc(e["kind"])}"'
                 f' data-status="{esc(e["status"])}" data-vendor="{esc(e["slug"])}"'
                 f' data-cat="{esc(storage.slugify(e["category"] or ""))}"'
                 if filterable else "")
        first = vendor_cell or f'<td class="plan-name">{plan_txt}</td>'
        plan_cell = (f'<td class="c-plan" data-l="Plan">{plan_txt}</td>' if show_vendor else "")
        mob = (f'<td class="c-mob" data-l="">{before} <span class="muted" aria-hidden="true">\u2192</span> '
               f'<strong>{after}</strong> {pct_badge(e["pct"])}</td>')
        rows.append(f"""
      <tr{' class="is-quiet"' if quiet else ''}{attrs}>
        {first}
        {plan_cell}
        <td class="c-kind" data-l="Change">{kind_badge(e)}</td>
        <td class="num c-hide" data-l="Before" data-v="{esc(_num_attr(e['old']))}">{before}</td>
        <td class="num c-hide" data-l="After" data-v="{esc(_num_attr(e['new']))}">{after}</td>
        <td class="num c-hide" data-l="Difference" data-v="{'' if e['pct'] is None else e['pct']}">{pct_badge(e['pct']) or MUTED_DASH}</td>
        <td class="c-date" data-l="Detected" data-v="{esc(e['date'])}"><time datetime="{esc(e['date'])}">{esc(pretty_date(e['date']))}</time></td>
        <td class="c-status" data-l="Status">{status_badge(e)}</td>
        {mob}
        <td class="c-link" data-l=""><a class="row-link" href="{prefix}v/{esc(e['slug'])}.html#c-{esc(e['id'])}">Details<span class="vh"> of the {esc(e['vendor'])} change</span></a></td>
      </tr>""")
    head_vendor = ('<th data-sort="text" scope="col">Company</th>'
                   '<th data-sort="text" scope="col">Plan</th>'
                   if show_vendor else '<th data-sort="text" scope="col">Plan</th>')
    cap = f"<caption class=\"vh\">{esc(caption)}</caption>" if caption else ""
    tid = f' id="{table_id}"' if table_id else ""
    return f"""<div class="tbl-scroll"><table class="stack changes{'' if show_vendor else ' no-co'}" data-sortable{tid}>{cap}
    <thead><tr>{head_vendor}<th data-sort="text" scope="col">Change</th>
      <th class="num" data-sort="num" scope="col">Before</th>
      <th class="num" data-sort="num" scope="col">After</th>
      <th class="num" data-sort="num" scope="col">Difference</th>
      <th data-sort="text" scope="col">Detected</th>
      <th data-sort="text" scope="col">Status</th>
      <th data-sort="off" scope="col"><span class="vh">Link</span></th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>"""


MUTED_DASH = '<span class="muted">\u2014</span>'


def _num_attr(v) -> str:
    if isinstance(v, bool) or v is None:
        return ""
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------- price history chart

SERIES_MAX = 4


def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Round, evenly spaced axis values covering lo..hi."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(n - 1, 1)
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    start = (lo // step) * step
    ticks, v = [], start
    while v <= hi + step * 0.001:
        ticks.append(round(v, 2))
        v += step
    if ticks[-1] < hi:
        ticks.append(round(ticks[-1] + step, 2))
    return ticks


def tier_series(name: str, record: dict, events: list[dict], start: str,
                end: str) -> list[dict]:
    """Confirmed price history for each priced tier, as step-series.

    Built from confirmed price changes walked forward from the start of
    tracking, ending at today's figure. Only confirmed changes draw a step: a
    line that moves on this chart moved on the vendor's page, on two separate
    readings.
    """
    out = []
    tiers = [p for p in _real_plans(record)
             if not p.get("is_custom_pricing") and plan_price(p)[0]]
    for p in tiers:
        value, basis = plan_price(p)
        field = "monthly_price" if basis == "monthly" else "annual_price_per_month"
        key = plan_key(p.get("name") or "")
        evs = sorted((e for e in events
                      if e["vendor"] == name and e["group"] == "price"
                      and e["status"] == "confirmed" and e["plan_key"] == key
                      and e["field"] == field
                      and isinstance(e["old"], (int, float))
                      and isinstance(e["new"], (int, float))
                      and start <= e["date"] <= end),
                     key=lambda e: e["date"])
        pts = [(start, float(evs[0]["old"]) if evs else float(value))]
        for e in evs:
            pts.append((e["date"], float(e["new"])))
        if abs(pts[-1][1] - float(value)) > 0.005:
            # The record moved without a logged price change (a correction of
            # how the page was read). Show today's figure as the end point but
            # do not draw it as a confirmed change.
            pts.append((end, float(value)))
        out.append({"name": p["name"], "basis": basis, "value": float(value),
                    "points": pts, "events": evs})
    # Tiers with history first, then cheapest first; cap the count.
    out.sort(key=lambda s: (not s["events"], s["value"]))
    picked = out[:SERIES_MAX]
    picked.sort(key=lambda s: s["value"])
    return picked


def history_chart(name: str, record: dict, events: list[dict], start: str,
                  end: str, end_label: str = "Today") -> str:
    """An inline SVG step chart of each tier's confirmed price over time.

    Drawn twice -- a wide version with direct labels, and a narrow one for
    phones where text stays readable -- and CSS shows whichever fits. Only one
    is ever visible, so a screen reader meets one chart.
    """
    series = tier_series(name, record, events, start, end)
    if not series or _days_between(start, end) < 1:
        return ""
    wide = _chart_svg(name, record, series, start, end, 720, 250, 52, 96, "chart-lg",
                      end_label=end_label)
    narrow = _chart_svg(name, record, series, start, end, 360, 220, 44, 14, "chart-sm",
                        labels=False, end_label=end_label)
    return wide.replace("<!--NARROW-->", narrow)


def _chart_svg(name, record, series, start, end, W, H, ml, mr, cls,
               labels: bool = True, end_label: str = "Today") -> str:
    cur = record.get("currency") or "USD"
    mt, mb = 14, 30
    pw, ph = W - ml - mr, H - mt - mb
    values = [v for s in series for _, v in s["points"]]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.15, hi * 0.08, 1)
    ticks = _nice_ticks(max(0, lo - pad), hi + pad)
    y0, y1 = ticks[0], ticks[-1]
    span_days = max(_days_between(start, end), 1)

    def X(day: str) -> float:
        return ml + pw * min(max(_days_between(start, day), 0), span_days) / span_days

    def Y(v: float) -> float:
        return mt + ph - (v - y0) / ((y1 - y0) or 1) * ph

    grid = "".join(
        f'<line x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
        for t in ticks)
    ylab = "".join(
        f'<text x="{ml - 8}" y="{Y(t) + 4:.1f}" text-anchor="end">'
        f'{esc(_plain_money(cur, t))}</text>' for t in ticks)

    # Month ticks along the bottom, plus the two ends.
    xl = [(start, pretty_date(start)[:6])]
    d = datetime.strptime(start, "%Y-%m-%d").replace(day=1)
    while True:
        d = (d + timedelta(days=32)).replace(day=1)
        day = d.strftime("%Y-%m-%d")
        if day >= end:
            break
        if _days_between(start, day) > span_days * 0.08 and \
                _days_between(day, end) > span_days * 0.12:
            xl.append((day, d.strftime("%b")))
    xl.append((end, end_label))
    xlab = "".join(
        f'<text x="{X(dd):.1f}" y="{H - 8}" text-anchor="'
        f'{"start" if i == 0 else "end" if i == len(xl) - 1 else "middle"}">'
        f'{esc(label)}</text>' for i, (dd, label) in enumerate(xl))

    show_labels = labels
    lines, pts, labels = [], [], []
    end_ys = []
    for i, s in enumerate(series, start=1):
        p = s["points"]
        path = f"M{X(p[0][0]):.1f},{Y(p[0][1]):.1f}"
        for (dd, v) in p[1:]:
            path += f" H{X(dd):.1f} V{Y(v):.1f}"
        path += f" H{X(end):.1f}"
        lines.append(f'<path class="ln c{i}" d="{path}"/>')
        for e in s["events"]:
            tip = (f"{s['name']}: {_plain_money(cur, e['old'])} → "
                   f"{_plain_money(cur, e['new'])} on {pretty_date(e['date'])}")
            pts.append(f'<circle class="pt c{i}" cx="{X(e["date"]):.1f}" '
                       f'cy="{Y(float(e["new"])):.1f}" r="4.5" tabindex="0" '
                       f'data-tip="{esc(tip)}"><title>{esc(tip)}</title></circle>')
        tip = (f"{s['name']}: {_plain_money(cur, s['value'])} "
               f"{'today' if end_label == 'Today' else 'at the last check, ' + pretty_date(end)}")
        pts.append(f'<circle class="pt c{i}" cx="{X(end):.1f}" '
                   f'cy="{Y(s["value"]):.1f}" r="4" tabindex="0" '
                   f'data-tip="{esc(tip)}"><title>{esc(tip)}</title></circle>')
        end_ys.append(Y(s["value"]))
        labels.append((Y(s["value"]),
                       f'{s["name"][:14]} {_plain_money(cur, s["value"])}'))

    # Direct labels only when they cannot collide; otherwise the legend
    # carries identity on its own.
    ys = sorted(y for y, _ in labels)
    spaced = show_labels and all(b - a >= 13 for a, b in zip(ys, ys[1:]))
    end_lbl = "".join(
        f'<text class="end-lbl" x="{X(end) + 9:.1f}" y="{y + 4:.1f}">{esc(t)}</text>'
        for y, t in labels) if spaced else ""

    legend = "".join(
        f'<span><i class="bg{i}"></i>{esc(s["name"])} '
        f'<span class="muted mono">{esc(_plain_money(cur, s["value"]))}'
        f'{" (annual)" if s["basis"] == "annual" else ""}</span></span>'
        for i, s in enumerate(series, start=1))

    moved = [s for s in series if s["events"]]
    if moved:
        bits = []
        for s in moved:
            first, last = s["events"][0], s["events"][-1]
            bits.append(f"{s['name']} went from {_plain_money(cur, first['old'])} "
                        f"to {_plain_money(cur, last['new'])}")
        summary = "; ".join(bits) + ". Other plans held their price."
    else:
        until = "today" if end_label == "Today" else f"the last successful check on {pretty_date(end)}"
        summary = (f"No confirmed price change for any plan between "
                   f"{pretty_date(start)} and {until}.")
    aria = (f"Price history for {len(series)} {name} plan"
            f"{'s' if len(series) != 1 else ''} from {pretty_date(start)} to "
            f"{'today' if end_label == 'Today' else pretty_date(end)}. {summary}")

    svg = (f'<svg class="chart {cls}" viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="{esc(aria)}"><g class="grid">{grid}</g>'
           f'<g class="axis">{ylab}{xlab}</g>{"".join(lines)}{"".join(pts)}'
           f'{end_lbl}</svg>')
    if cls != "chart-lg":
        return svg
    return f"""
<figure class="chart-wrap" style="margin:0">
  <div class="legend" aria-hidden="true">{legend}</div>
  {svg}<!--NARROW-->
  <figcaption class="chart-note">{esc(summary)} Monthly figures unless marked
    annual. Only confirmed changes move a line.</figcaption>
</figure>"""


def _complete_ctx(ctx: dict) -> dict:
    """Fill in anything a hand-built context leaves out.

    build() always supplies everything; this is for callers (tests, the
    dashboard, one-off scripts) that render a single page from a partial
    context. Missing pieces default to "nothing recorded", never to invented
    data.
    """
    if ctx.get("_complete"):
        return ctx
    ctx.setdefault("changes", [])
    ctx.setdefault("vendors", {})
    ctx.setdefault("records", {})
    ctx.setdefault("history", {})
    ctx.setdefault("state", {})
    ctx.setdefault("versions", {})
    ctx.setdefault("benchmarks", {})
    ctx.setdefault("by_category", {})
    ctx.setdefault("vendor_category", {})
    ctx.setdefault("tracking_since", pretty_date(_today()))
    ctx.setdefault("since_iso", _iso_day(ctx["tracking_since"]) or _today())
    if "events" not in ctx:
        ctx["events"] = insights.build_events(ctx["changes"], ctx["vendors"],
                                              corrections=[])
    ctx.setdefault("snapshot_count", sum(ctx["versions"].values()))
    ctx.setdefault("slug_to_name", {storage.slugify(n): n for n in ctx["vendors"]})
    ctx.setdefault("active_count", len(ctx["records"]))
    ctx.setdefault("last_checked_iso", "")
    if "last_price_change" not in ctx:
        lpc: dict[str, str] = {}
        for e in ctx["events"]:
            if e["group"] == "price" and e["status"] == "confirmed":
                lpc[e["vendor"]] = max(lpc.get(e["vendor"], ""), e["date"])
        ctx["last_price_change"] = lpc
    ctx["_complete"] = True
    return ctx


# ---------------------------------------------------------------- homepage

def _entry_cell(rec: dict, table_currency: str = "") -> tuple[str, str, str]:
    """(from-cell html, top-cell html, sort value) for a price table row.

    A price in a different currency from the rest of its table gets no sort
    value, so sorting puts it last instead of ranking 20 rupees beside $19.
    """
    cur = rec.get("currency") or "USD"
    entry, top, basis = headline_prices(rec)
    plans = _real_plans(rec)
    if entry is None:
        if plans and all(p.get("is_custom_pricing") or p.get("is_free")
                         for p in plans) and any(p.get("is_custom_pricing")
                                                 for p in plans):
            tag = '<span class="tag">Contact sales</span>'
        else:
            tag = ('<span class="tag" title="The page did not show its prices '
                   'in a form PriceTrail could read">Not readable</span>')
        return tag, "—", ""
    note = '<span class="sub">billed annually</span>' if basis == "annual" else ""
    comparable = not table_currency or cur.upper() == table_currency.upper()
    return (money(cur, entry) + note, money(cur, top),
            f"{entry:.2f}" if comparable else "")


def _proof(ctx: dict) -> str:
    """The real product above the fold: the latest confirmed price change."""
    prices = insights.price_events(ctx["events"])
    records = ctx["records"]
    if prices:
        e = prices[0]
        cur = e.get("currency") or "USD"
        rose = e["kind"] == "rise"
        bill = "billed annually" if e["billing"] == "annual" else "billed monthly"
        others = len(prices) - 1
        return f"""
<div class="card">
<figure class="proof">
  <div class="proof-top"><h2 class="eyebrow">Latest confirmed price change</h2>
    {pct_badge(e['pct'])}</div>
  <p class="vendor">{esc(e['vendor'])}</p>
  <p class="plan">{esc(e.get('plan') or '')} plan &middot; {esc(bill)}</p>
  <p class="big"><span class="was">{money(cur, e['old'])}</span>
    <span class="muted" aria-hidden="true">→</span><span class="vh">to</span>
    <span class="now {'up' if rose else 'down'}">{money(cur, e['new'])}</span></p>
  <figcaption class="proof-foot">
    <span>Detected <time datetime="{esc(e['date'])}">{esc(pretty_date(e['date']))}</time>
      &middot; confirmed on two readings</span>
    <a class="link-arrow" href="v/{esc(e['slug'])}.html#c-{esc(e['id'])}">See {esc(e['vendor'])}’s history</a>
  </figcaption>
</figure>
<p class="provenance" style="padding:0 1.25rem 1rem">{others} other confirmed
  price change{'s' if others != 1 else ''} on record &middot;
  <a href="changes.html?type=price">see them all</a></p>
</div>"""
    priced = sum(_priced_count(r) for r in records.values())
    return f"""
<div class="card">
<figure class="proof">
  <div class="proof-top"><h2 class="eyebrow">On record</h2></div>
  <p class="vendor">{priced} prices across {ctx.get("active_count", len(records))} companies</p>
  <p class="plan">Read every day since {esc(ctx['tracking_since'])}</p>
  <figcaption class="proof-foot"><span>No confirmed price change yet. A price
    that holds is worth knowing before you commit to a year of it.</span>
  </figcaption>
</figure></div>"""


def render_index(ctx: dict) -> str:
    """The homepage: what this is, the proof, the prices, the changes."""
    ctx = _complete_ctx(ctx)
    records, by_cat = ctx["records"], ctx["by_category"]
    events = ctx["events"]
    tracked = ctx["active_count"]
    summ = insights.summary(events)
    cats = [title_case(c) for c in sorted(by_cat)
            if any(storage.slugify(n) in records for n in by_cat[c])]
    cat_text = (", ".join(cats[:-1]) + " and " + cats[-1]) if len(cats) > 1 \
        else (cats[0] if cats else "software")
    plans_priced = sum(_priced_count(r) for r in records.values())
    days = max(_days_between(ctx["since_iso"], _today()), 0)
    checked = ctx.get("last_checked_iso", "")

    body = [f"""
<section class="hero"><div class="wrap hero-grid">
  <div>
    <p class="eyebrow">Software pricing, on the record</p>
    <h1>See what software costs — and what it used to cost.</h1>
    <p class="lede">PriceTrail reads the public pricing pages of {tracked}
      {esc(cat_text.lower().replace('crm', 'CRM'))} tools every day, confirms
      each change on two separate readings, and keeps the full history.
      Free to browse. No sign-up.</p>
    <div class="hero-actions">
      <a class="btn btn-primary" href="changes.html">Browse price changes</a>
      <a class="btn btn-ghost" href="#prices">See every price</a>
    </div>
    <p class="fresh">
      <span><i class="dot" aria-hidden="true"></i>Last checked {esc(pretty_date(checked))}</span>
      <span>Recording since {esc(ctx['tracking_since'])}</span>
      <span>{tracked} companies</span>
    </p>
  </div>
  {_proof(ctx)}
</div></section>

<section class="section-tight"><div class="wrap">
  {stats_row([
      (str(tracked), "Companies tracked"),
      (str(plans_priced), "Plans priced"),
      (str(summ['price']), "Confirmed price changes"),
      (f"{days}", "Days of history"),
      (str(ctx['snapshot_count']), "Page versions archived"),
  ])}
</div></section>
"""]

    body.append(subscribe_block(compact=True))

    head = insights.headline(events)
    body.append(f"""
<section class="section"><div class="wrap">
  <div class="sec-head"><div><h2>Recent price changes</h2>
    <p>Confirmed price rises, cuts and plan changes, newest first.
      {summ['reversed']} readings that undid themselves are kept on the record
      but out of this list — <a href="about.html#confirmation">why</a>.</p></div>
    <a class="link-arrow" href="changes.html">All changes</a></div>
  <div class="card">{change_table(head[:8], "", caption="Recent confirmed price changes")}</div>
</div></section>""")

    # ---- every tracked price ----
    blocks = []
    for cat in sorted(by_cat):
        live = sorted(n for n in by_cat[cat] if storage.slugify(n) in records)
        if not live:
            continue
        bench = ctx["benchmarks"].get(cat, {})
        cur = bench.get("currency", "USD")
        rows = []
        for name in live:
            rec = records[storage.slugify(name)]
            entry_cell, top_cell, sort_v = _entry_cell(rec, cur)
            top_v = headline_prices(rec)[1] if sort_v else None
            last = ctx["last_price_change"].get(name)
            free = any(p.get("is_free") for p in _real_plans(rec))
            rows.append(f"""
        <tr>
          <td class="name"><a href="v/{esc(storage.slugify(name))}.html">{esc(name)}</a>
            <span class="sub">{esc(_how_charged(rec))}</span></td>
          <td class="num" data-l="From" data-v="{sort_v}">{entry_cell}</td>
          <td class="num" data-l="Top published" data-v="{'' if top_v is None else f'{top_v:.2f}'}">{top_cell}</td>
          <td data-l="Free plan">{'Yes' if free else 'No'}</td>
          <td data-l="Last price change" data-v="{esc(last or '')}">{esc(pretty_date(last)) if last else '<span class="muted">No change</span>'}</td>
          <td data-l="Checked" data-v="{esc(last_checked(name, rec, ctx))}"><time>{esc(pretty_date(last_checked(name, rec, ctx)))}</time></td>
        </tr>""")
        blocks.append(f"""
  <div class="card cat-block" data-block>
    <div class="cat-head">
      <h3><a href="c/{esc(storage.slugify(cat))}.html">{esc(title_case(cat))}</a></h3>
      <span class="cat-meta">{len(live)} companies &middot; typical starting price
        <strong>{esc(money(cur, bench.get('median_entry')))}</strong>{mixed_currency_note(bench)}</span>
    </div>
    <div class="tbl-scroll"><table class="stack compact" data-sortable>
      <caption class="vh">{esc(title_case(cat))} prices</caption>
      <thead><tr>
        <th data-sort="text" scope="col">Company</th>
        <th class="num" data-sort="num" scope="col">From</th>
        <th class="num" data-sort="num" scope="col">Top published</th>
        <th data-sort="off" scope="col">Free plan</th>
        <th data-sort="text" scope="col">Last price change</th>
        <th data-sort="text" scope="col">Checked</th>
      </tr></thead><tbody>{''.join(rows)}</tbody></table></div>
  </div>""")

    body.append(f"""
<section class="section" id="prices"><div class="wrap">
  <div class="sec-head"><div><h2>Every tracked price</h2>
    <p>Per month, as each vendor publishes it, in the vendor's own currency.
      Click a column heading to sort.</p></div></div>
  <div class="findbar">
    <label class="field" style="flex:1 1 16rem;max-width:26rem">{SEARCH_SVG}
      <span class="vh">Filter companies by name</span>
      <input id="find" type="search" hidden placeholder="Filter by company name"
        aria-label="Filter companies by name"></label>
    <span class="find-count" id="find-count" aria-live="polite"></span>
  </div>
  <p class="find-empty" id="find-empty" hidden></p>
  {''.join(blocks)}
</div></section>""")

    body.append(f"""
<section class="section"><div class="wrap">
  <div class="sec-head"><div><h2>How PriceTrail works</h2>
    <p>Built to be believed. Every figure can be traced to the vendor's own
      page and the day it was read.</p></div>
    <a class="link-arrow" href="about.html">Read the method</a></div>
  <div class="steps">
    <div class="step"><span class="n">01</span><h3>Read every day</h3>
      <p>A crawler visits each vendor's own public pricing page — daily for
        the big names, weekly for the rest — and records every plan and
        price.</p></div>
    <div class="step"><span class="n">02</span><h3>Confirm before publishing</h3>
      <p>A change must appear on two separate readings before it is published.
        Large changes wait three days. Readings that undo themselves are
        labelled, never presented as news.</p></div>
    <div class="step"><span class="n">03</span><h3>Keep the history</h3>
      <p>Every confirmed version is kept with its date, so you can see what a
        tool cost before — and download the whole archive as CSV or
        JSON.</p></div>
  </div>
</div></section>""")

    pairs_here = comparison_pairs(ctx)
    if pairs_here:
        groups = defaultdict(list)
        for a, b in pairs_here:
            groups[ctx["vendor_category"].get(a, "")].append((a, b))
        parts = []
        for cat in sorted(groups):
            links = "".join(f'<a href="compare/{_pair_slug(a, b)}.html">'
                            f'{esc(a)} vs {esc(b)}</a>' for a, b in groups[cat])
            parts.append(f'<div class="all-group" style="margin-top:1rem">'
                         f'<h3 class="eyebrow" style="margin-bottom:.5rem">'
                         f'{esc(title_case(cat))}</h3>'
                         f'<p class="links-cloud">{links}</p></div>')
        body.append(f"""
<section class="section"><div class="wrap">
  <div class="sec-head"><div><h2>Compare side by side</h2>
    <p>{len(pairs_here)} head-to-head pages for companies that publish
      comparable prices.</p></div></div>
  {''.join(parts)}
</div></section>""")

    body.append(subscribe_block())
    return page(f"{SITE_NAME} — SaaS pricing history and price changes",
                f"Current prices and confirmed price changes for {tracked} "
                f"{cat_text} tools, read daily since {ctx['tracking_since']}. "
                f"See what software costs and what it used to cost.",
                "".join(body), "index.html",
                extra_head=site_schema() + dataset_schema(
                    tracked, summ["confirmed"], ctx["tracking_since"]))


# ---------------------------------------------------------------- change log

def render_changes(ctx: dict) -> str:
    ctx = _complete_ctx(ctx)
    events = insights.visible(ctx["events"])
    summ = insights.summary(events)
    companies = sorted({(e["vendor"], e["slug"]) for e in events})
    cats = sorted({e["category"] for e in events if e["category"]})
    co_opts = "".join(f'<option value="{esc(s)}">{esc(n)}</option>'
                      for n, s in companies)
    cat_opts = "".join(f'<option value="{esc(storage.slugify(c))}">'
                       f'{esc(title_case(c))}</option>' for c in cats)
    n_head = len(insights.headline(events))
    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("", [("Prices", "index.html"), ("Changes", None)])}
    <h1>Software price changes</h1>
    <p class="lede">Every change PriceTrail has recorded, newest first. The
      default view shows confirmed price rises, cuts and plan changes. Use the
      filters for renames, add-ons, page events and readings that were later
      reversed — nothing is deleted from the record.</p>
  </header>
  {stats_row([
      (str(summ['price']), "Confirmed price changes"),
      (f"{summ['rises']} / {summ['cuts']}", "Rises / cuts"),
      (str(summ['plan']), "Plan changes"),
      (str(summ['reversed']), "Reversed readings"),
      (str(len(ctx['changes'])), "Raw log entries"),
  ])}
  <section class="section-tight" style="padding-top:1.5rem">
    <div class="filters" id="log-filters" hidden>
      <div class="seg" role="group" aria-label="Type of change">
        <button type="button" data-type="headline" aria-pressed="true">Key changes<span class="n">{n_head}</span></button>
        <button type="button" data-type="price" aria-pressed="false">Prices<span class="n">{summ['price']}</span></button>
        <button type="button" data-type="rise" aria-pressed="false">Rises</button>
        <button type="button" data-type="cut" aria-pressed="false">Cuts</button>
        <button type="button" data-type="plan" aria-pressed="false">Plans &amp; add-ons</button>
        <button type="button" data-type="page" aria-pressed="false">Page events</button>
        <button type="button" data-type="all" aria-pressed="false">Everything<span class="n">{summ['raw']}</span></button>
      </div>
      <label class="field"><span class="vh">Company</span>
        <select id="f-company"><option value="">All companies</option>{co_opts}</select></label>
      <label class="field"><span class="vh">Category</span>
        <select id="f-category"><option value="">All categories</option>{cat_opts}</select></label>
      <label class="field">{SEARCH_SVG}<span class="vh">Search the log</span>
        <input id="f-text" type="search" placeholder="Search plans…"></label>
      <label class="small" style="display:inline-flex;gap:.4rem;align-items:center">
        <input type="checkbox" id="f-all"> Include reversed &amp; unconfirmed</label>
      <button type="button" class="clear" id="f-clear" hidden>Clear filters</button>
      <span class="count" id="f-count" aria-live="polite"></span>
    </div>
    <div class="card">
      {change_table(events, "", table_id="change-log", caption="Every recorded change", filterable=True)}
      <div class="empty" id="f-empty" hidden><strong>No entries match these filters.</strong>
        Try another type of change, or clear the filters.</div>
    </div>
  </section>
  <section class="section">
    <div class="sec-head"><h2>How to read this log</h2></div>
    <div class="grid g2">
      <div class="card card-pad prose"><p><strong>Confirmed</strong> — the
        change appeared on two separate readings of the vendor's page (three
        days for large changes) and was published.</p>
        <p><strong>Reversed</strong> — the entry was undone within three
        weeks: a plan removed and re-added, prices hidden then shown. That
        usually means the page loaded differently on different days, not that
        the vendor changed anything. Both entries stay on the record.</p></div>
      <div class="card card-pad prose"><p><strong>Unconfirmed</strong> — the
        vendor's page has a history of loading inconsistently, so an entry about
        prices appearing or disappearing is not treated as a real change.</p>
        <p><strong>Corrected</strong> — a person reviewed the entry and
        recorded a correction. The original entry is kept; the correction and
        its reason are shown. <a href="about.html#corrections">How corrections
        work</a>.</p></div>
    </div>
    <p class="provenance" style="margin-top:1rem">Download this log as
      <a href="downloads/changes.csv">CSV</a> or
      <a href="downloads/pricetrail.json">JSON</a>, or follow it by
      <a href="{BASE_URL}/feed.xml">RSS</a>.</p>
  </section>
</div>"""
    return page(f"Software price changes — every recorded change — {SITE_NAME}",
                f"{summ['price']} confirmed SaaS price changes and "
                f"{summ['plan']} plan changes recorded since "
                f"{ctx['tracking_since']}, with before and after figures.",
                body, "changes.html")


def render_digest(ctx: dict) -> str:
    """This week's changes as a page."""
    ctx = _complete_ctx(ctx)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    events = insights.visible(ctx["events"])
    recent = [e for e in events if e["date"] >= cutoff]
    confirmed = [e for e in recent if e["status"] == "confirmed"]
    body = [f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("", [("Prices", "index.html"), ("This week", None)])}
    <h1>This week in software pricing</h1>
    <p class="lede">Everything recorded in the last seven days
      ({esc(pretty_date(cutoff))} to {esc(pretty_date(_today()))}):
      {len(confirmed)} confirmed change{'s' if len(confirmed) != 1 else ''}
      across {len({e['vendor'] for e in confirmed})} compan{'ies' if len({e['vendor'] for e in confirmed}) != 1 else 'y'}.</p>
  </header>"""]
    if recent:
        body.append(f'<div class="card">{change_table(recent, "", caption="Changes in the last seven days")}</div>')
    else:
        body.append('<div class="card"><div class="empty"><strong>Nothing moved '
                    'this week.</strong>That is a real finding, not a gap: most '
                    'weeks are quiet, and knowing a price has held is worth '
                    'something. <a href="changes.html">See every recorded '
                    'change</a>.</div></div>')
    body.append("</div>")
    body.append(subscribe_block())
    return page(f"This week in software pricing — {SITE_NAME}",
                f"{len(confirmed)} confirmed pricing changes recorded across "
                "tracked B2B software vendors in the last seven days.",
                "".join(body), "week.html")


# ---------------------------------------------------------------- vendor page

def _vendor_summary(name: str, record: dict, ctx: dict, category: str) -> str:
    """What this vendor charges, written out as sentences.

    Every sentence is arithmetic on figures already in the archive, so nothing
    new is collected and nothing is invented.
    """
    cur = (record.get("currency") or "USD").upper()
    plans = _real_plans(record)
    if not plans:
        return ""
    entry, top, _ = headline_prices(record)
    free = [p for p in plans if p.get("is_free")]
    custom = [p for p in plans if p.get("is_custom_pricing")]
    seat = [p for p in plans if p.get("is_per_seat")]
    addons = [p for p in record.get("plans", []) if p.get("is_addon")]
    trials = [p.get("trial_days") for p in plans
              if isinstance(p.get("trial_days"), int) and p["trial_days"] > 0]
    changes = [c for c in ctx.get("changes", []) if c.get("vendor") == name]

    priced = [p for p in plans if p.get("monthly_price") is not None
              or p.get("annual_price_per_month") is not None]
    paid_priced = [p for p in priced if not p.get("is_free")
                   and (p.get("monthly_price") or p.get("annual_price_per_month"))]
    paid_unpriced = [p for p in plans if not p.get("is_free")
                     and not p.get("is_custom_pricing") and p not in priced]
    if (not priced and not custom) or (not paid_priced and paid_unpriced):
        listed = ", ".join(esc(p["name"]) for p in plans[:6])
        return (f'<div class="summary"><p>{esc(name)} lists {len(plans)} '
                f'plan{"s" if len(plans) != 1 else ""} ({listed}), but the latest '
                f'reading could not establish their prices reliably — often '
                f'because the page fills its prices in after loading, or a '
                f'billing toggle could not be read. Rather than print a figure '
                f'that might be wrong, no price is shown until a clean '
                f'reading.</p></div>')

    out = []
    if entry:
        line = (f"{esc(name)} starts at {esc(money(cur, entry))} a month on its "
                f"cheapest paid plan")
        if seat:
            line += ", per person"
        if top and top != entry:
            line += (f", rising to {esc(money(cur, top))} on the highest plan it "
                     f"publishes")
        out.append(line + ".")

    bench = ctx.get("benchmarks", {}).get(category) or {}
    med = bench.get("median_entry")
    if entry and med and bench.get("currency") == cur and bench.get("n", 0) > 2:
        cat = esc(title_case(category))
        if entry < med * 0.85:
            out.append(f"That is below the {cat} median of {esc(money(cur, med))}, "
                       f"so it is one of the cheaper ways into this category.")
        elif entry > med * 1.15:
            out.append(f"That is above the {cat} median of {esc(money(cur, med))} "
                       f"— it is priced at the expensive end of its category.")
        else:
            out.append(f"That is close to the {cat} median of {esc(money(cur, med))}.")

    tiers = [pl["name"] for pl in plans if str(pl.get("name") or "").strip()]
    if len(tiers) > 1:
        listed = ", ".join(esc(t) for t in tiers[:-1]) + f" and {esc(tiers[-1])}"
        out.append(f"The plans are called {listed}.")
    elif tiers:
        out.append(f"There is one published plan, {esc(tiers[0])}.")

    named_free = any("free" in str(pl.get("name") or "").lower() for pl in plans)
    bits = []
    if free:
        bits.append("there is a free tier")
    elif named_free:
        if trials:
            bits.append(f"there is a {max(trials)}-day trial")
    elif trials:
        bits.append(f"there is no free tier, but a {max(trials)}-day trial")
    else:
        bits.append("no free tier and no trial length is published")
    if custom:
        bits.append("the top tier is quote-only, so enterprise pricing is not public")
    if addons:
        names = [a["name"] for a in addons if str(a.get("name") or "").strip()]
        bits.append("some features are sold as paid add-ons on top of a plan"
                    + (f" ({esc(', '.join(names[:3]))})" if names else ""))
    if bits:
        first = bits[0][0].upper() + bits[0][1:]
        out.append(first + "." if len(bits) == 1
                   else first + ", and " + ", ".join(bits[1:]) + ".")

    since = ctx.get("tracking_since", "")
    price_moves = [c for c in changes
                   if c.get("change_type") in ("price_increase", "price_decrease")]
    if price_moves:
        ups = sum(1 for c in price_moves if c["change_type"] == "price_increase")
        downs = sum(1 for c in price_moves if c["change_type"] == "price_decrease")
        parts = []
        if ups:
            parts.append(f"{ups} rise{'s' if ups != 1 else ''}")
        if downs:
            parts.append(f"{downs} cut{'s' if downs != 1 else ''}")
        latest = max(c.get("detected_at", "") for c in price_moves)[:10]
        out.append(f"Since {esc(since)} we have recorded {esc(' and '.join(parts))}"
                   f" here, the most recent on {esc(pretty_date(latest))}.")
    else:
        checked = last_checked(name, record, ctx) if ctx.get("state") else ""
        if checked and _days_between(checked, _today()) > (3 if _tier(name, ctx) == "daily" else 10):
            out.append(f"No price changed between {esc(since)} and "
                       f"{esc(pretty_date(checked))}, the last time the page "
                       f"could be read.")
        else:
            out.append(f"No price on this page has changed since {esc(since)}, when "
                       f"{esc(name)} was first read. A price that has held is worth "
                       f"knowing before you commit to a year of it.")
    return '<div class="summary">' + "".join(f"<p>{ln}</p>" for ln in out) + "</div>"


def track_block(vendor: str, prefix: str = "") -> str:
    """Track one tool, from that tool's own page. Only with a mailing service."""
    if not SIGNUP_URL:
        return ""
    tag = storage.slugify(vendor)
    email_field, tags_work = signup_fields(SIGNUP_URL)
    tag_input = (f'<input type="hidden" name="tag" value="{tag}">'
                 f'<input type="hidden" name="embed" value="1">'
                 if tags_work else "")
    return f"""
<div class="track">
  <h2>Get told when {esc(vendor)} changes its prices</h2>
  <p>We read {esc(vendor)}'s pricing page every day. Put your email in and
    you'll hear the same week it moves — with the old figure, the new one
    and the date. One tool is free.</p>
  <form class="signup" action="{esc(SIGNUP_URL)}" method="post"
        target="_blank" rel="noopener">
    <label class="vh" for="track-{tag}">Email address</label>
    <input id="track-{tag}" type="email" name="{email_field}" required
           autocomplete="email" placeholder="you@company.com">
    {tag_input}
    <button type="submit">Track {esc(vendor)}</button>
  </form>
  <p class="signup-note">Only about {esc(vendor)}. Unsubscribe in one click.
    Your address is never sold or shared.</p>
</div>"""


def _limits_text(p: dict) -> str:
    parts = []
    for l in (p.get("limits") or [])[:3]:
        v = l.get("value")
        if v is None:
            continue
        try:
            n = "Unlimited" if float(v) < 0 else f"{float(v):,.0f}"
        except (TypeError, ValueError):
            continue
        parts.append(f"{n} {esc(l.get('metric', ''))}".strip())
    return ", ".join(parts) or '<span class="muted">—</span>'


def _plan_rows(record: dict, plans: list[dict]) -> str:
    cur = record.get("currency") or "USD"
    rows = []
    for p in plans:
        if p.get("is_custom_pricing"):
            monthly = '<span class="tag">Contact sales</span>'
        elif p.get("is_free"):
            monthly = '<span class="tag">Free</span>'
        else:
            monthly = money(cur, p.get("monthly_price"))
        rows.append(f"""
      <tr>
        <td class="plan-name">{esc(p['name'])}
          <span class="sub">{'per user' if p.get('is_per_seat') else 'flat price'}</span></td>
        <td class="num" data-l="Monthly">{monthly}</td>
        <td class="num" data-l="Annual, per month">{money(cur, p.get('annual_price_per_month'))}</td>
        <td data-l="Includes">{_limits_text(p)}</td>
      </tr>""")
    return "".join(rows)


def _plans_table(record: dict, plans: list[dict], caption: str) -> str:
    return f"""<div class="tbl-scroll"><table class="stack compact">
      <caption class="vh">{esc(caption)}</caption>
      <thead><tr><th scope="col">Plan</th><th class="num" scope="col">Monthly</th>
        <th class="num" scope="col">Annual, per month</th>
        <th scope="col">Includes</th></tr></thead>
      <tbody>{_plan_rows(record, plans)}</tbody></table></div>"""


def _last_readable(slug: str, record: dict, ctx: dict) -> tuple[dict | None, str]:
    """For a vendor with no readable prices today: the last record that had
    them, from history, and its date."""
    if _priced_count(record):
        return None, ""
    point = hist.last_priced_point(ctx["history"].get(slug, []))
    if not point:
        return None, ""
    plans = [{"name": p["name"], "monthly_price": p.get("m"),
              "annual_price_per_month": p.get("a"), "is_free": p.get("free"),
              "is_custom_pricing": p.get("custom"), "is_addon": p.get("addon"),
              "is_per_seat": p.get("seat"), "limits": []}
             for p in point.get("plans", [])]
    return {"currency": point.get("currency") or record.get("currency"),
            "plans": plans}, point.get("date", "")


def _timeline(events: list[dict], record: dict) -> str:
    if not events:
        return ""
    cur = record.get("currency") or "USD"
    items = []
    for e in events:
        cls = {"rise": "rise", "cut": "cut"}.get(e["kind"], "plan" if e["group"] == "plan" else "")
        title = f'{kind_label(e)}'
        plan = f' <span class="plan">· {esc(e["plan"])}</span>' if e.get("plan") else ""
        ba = ""
        if e["group"] == "price":
            bill = " per month, billed annually" if e["billing"] == "annual" else " per month"
            ba = f"""<div class="ba" role="group" aria-label="Before and after">
          <div class="was"><span class="k">Before</span><span class="v">{money(cur, e['old'])}</span></div>
          <span class="arr" aria-hidden="true">→</span>
          <div><span class="k">After</span><span class="v">{money(cur, e['new'])}</span></div>
        </div><p class="tl-note">{pct_badge(e['pct'])} {esc(bill.strip().capitalize())}.</p>"""
        else:
            before, after = _before_after(e)
            ba = (f'<p class="tl-note">{before} <span aria-hidden="true">→</span>'
                  f'<span class="vh">to</span> {after}</p>')
        note = (f'<p class="tl-note">{status_badge(e)} {esc(e["status_note"])}</p>'
                if e["status"] != "confirmed" or e.get("status_note") else "")
        items.append(f"""
    <li class="tl {cls}" id="c-{esc(e['id'])}">
      <time class="tl-date" datetime="{esc(e['date'])}">{esc(pretty_date(e['date']))}</time>
      <div class="tl-card">
        <p class="tl-title">{esc(title)}{plan}</p>
        {ba}{note}
      </div>
    </li>""")
    return f'<ol class="timeline" style="list-style:none">{"".join(items)}</ol>'


def _versions_table(slug: str, name: str, ctx: dict) -> str:
    points = ctx["history"].get(slug, [])
    if not points:
        return ""
    by_day = defaultdict(list)
    for e in ctx["events"]:
        if e["vendor"] == name and e["status"] == "confirmed":
            by_day[e["date"]].append(e)
    rows = []
    for i, p in enumerate(reversed(points)):
        tiers = [x for x in p.get("plans", []) if not x.get("addon")]
        priced = [hist.priced(x) for x in tiers if hist.priced(x) and not x.get("custom")]
        cur = p.get("currency") or "USD"
        low = money(cur, min(priced)) if priced else '<span class="muted">No readable prices</span>'
        day_events = by_day.get(p["date"], [])
        if i == len(points) - 1:
            what = "First reading"
        elif day_events:
            what = "; ".join(sorted({kind_label(e) for e in day_events}))
        else:
            what = '<span class="muted">Record re-read; no public change logged</span>'
        rows.append(f"""
      <tr><td class="mono"><time datetime="{esc(p['date'])}">{esc(pretty_date(p['date']))}</time></td>
        <td class="num" data-l="Plans">{len(tiers)}</td>
        <td class="num" data-l="Lowest price">{low}</td>
        <td data-l="What changed">{what}</td></tr>""")
    return f"""<div class="tbl-scroll"><table class="stack">
      <caption class="vh">Every recorded version of {esc(name)}'s pricing</caption>
      <thead><tr><th scope="col">Version from</th><th class="num" scope="col">Plans</th>
        <th class="num" scope="col">Lowest price</th><th scope="col">What changed</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


def render_vendor(slug: str, name: str, ctx: dict) -> str:
    ctx = _complete_ctx(ctx)
    record = ctx["records"][slug]
    cur = record.get("currency") or "USD"
    category = ctx["vendor_category"].get(name, "")
    meta = ctx["vendors"].get(name, {}) or {}
    source = meta.get("pricing_url", "")
    mine = [e for e in insights.visible(ctx["events"]) if e["vendor"] == name]
    prices = [e for e in mine if e["group"] == "price" and e["status"] == "confirmed"]
    checked = last_checked(name, record, ctx)
    first_seen = (ctx["history"].get(slug) or [{}])[0].get("date") or ctx["since_iso"]
    start = max(first_seen, ctx["since_iso"]) if first_seen else ctx["since_iso"]
    entry, top, basis = headline_prices(record)
    tiers = _real_plans(record)
    addons = [p for p in record.get("plans", []) if p.get("is_addon")]
    free = any(p.get("is_free") for p in tiers)
    trial = _trial_days(record)
    tier = _tier(name, ctx)
    versions = ctx["versions"].get(slug, 0)

    facts = [
        ("From", money(cur, entry) if entry else "—",
         ("billed annually" if basis == "annual" else _how_charged(record)) if entry else "no readable price"),
        ("Top published", money(cur, top) if top else "—", "per month"),
        ("Free plan", "Yes" if free else "No", ""),
        ("Free trial", f"{trial} days" if trial else "—", ""),
        ("Price changes", str(len(prices)), f"since {pretty_date(start)}"),
    ]
    facts_html = "".join(
        f'<div class="fact"><span class="l">{esc(l)}</span>'
        f'<span class="v{" mono" if l in ("From", "Top published") else ""}">{v}</span>'
        f'<span class="s">{esc(s)}</span></div>' for l, v, s in facts)

    retired = name not in ctx["vendors"]
    if retired:
        notice = (f'<div class="notice">{INFO_SVG}<p><strong>{esc(name)} is no '
                  f'longer tracked.</strong> PriceTrail stopped reading this page '
                  f'after {esc(pretty_date(checked))}. The figures below are the '
                  f'last reading and are kept for reference only.</p></div>')
    else:
        notice = staleness_warning(record, name, ctx) or _status_notice(name, record, ctx)
    last_rec, last_date = _last_readable(slug, record, ctx)
    if last_rec:
        notice += (f'<div class="notice warn" style="margin-top:.75rem">{INFO_SVG}'
                   f'<p><strong>No readable prices on today’s page.</strong> '
                   f'The last reading that included prices was on '
                   f'{esc(pretty_date(last_date))}; those figures are shown '
                   f'further down for reference. They may be out of date.</p></div>')

    plans_block = (_plans_table(record, tiers, f"{name} plans") if tiers
                   else '<div class="empty">No subscription plans on record.</div>')
    addon_block = ""
    if addons:
        names = ", ".join(a["name"] for a in addons[:6] if a.get("name"))
        addon_block = f"""
  <details class="more"><summary>Add-ons ({len(addons)})</summary><div class="inner">
    <p class="provenance" style="margin-bottom:.75rem">Priced separately from
      the plans above, so an extra cost rather than an alternative to them.
      {esc(name)} lists {esc(names)}.</p>
    {_plans_table(record, addons, f"{name} add-ons")}</div></details>"""
    last_block = ""
    if last_rec:
        last_block = f"""
  <details class="more"><summary>Last readable prices ({esc(pretty_date(last_date))})</summary>
    <div class="inner">{_plans_table(last_rec, [p for p in last_rec['plans'] if not p.get('is_addon')], f"{name} last readable prices")}</div></details>"""

    # The chart only claims what was observed: if the page has not been read
    # recently, the line stops at the last successful check, not today.
    stale_days = _days_between(checked, _today()) if checked else 0
    chart_end, chart_label = ((checked, "Last check") if checked and stale_days > 1
                              else (_today(), "Today"))
    chart = history_chart(name, record, ctx["events"], start, chart_end, chart_label)
    chart_card = f"""
<section class="card card-pad" style="margin-top:1.25rem" aria-labelledby="h-history">
  <div class="sec-head" style="margin-bottom:.5rem"><div><h2 id="h-history">Price history</h2>
    <p>Confirmed prices for each plan since {esc(pretty_date(start))}. A flat
      line means the price held.</p></div></div>
  {chart or '<div class="empty"><strong>Nothing to chart yet.</strong>This page has no readable prices on record to draw.</div>'}
</section>"""
    versions_tbl = _versions_table(slug, name, ctx)
    timeline = _timeline(mine, record)

    domain = _domain(source)
    source_link = (f'<a href="{esc(source)}" rel="nofollow noopener" '
                   f'target="_blank">{esc(domain)} pricing page ↗</a>'
                   if source else "")
    siblings = [n for n in ctx["by_category"].get(category, [])
                if n != name and storage.slugify(n) in ctx["records"]
                and comparison_is_worth_a_page(*sorted((name, n)), ctx)]
    compare_links = "".join(
        f'<a href="../compare/{esc(_pair_slug(name, s))}.html">{esc(name)} vs {esc(s)}</a>'
        for s in siblings[:10])
    others = [n for n in ctx["by_category"].get(category, [])
              if n != name and storage.slugify(n) in ctx["records"]]
    other_links = "".join(f'<a href="{esc(storage.slugify(n))}.html">{esc(n)}</a>'
                          for n in sorted(others))
    notes = (record.get("extraction_notes") or "").strip()

    aside = f"""
<aside class="grid" style="align-content:start">
  <div class="card card-pad">
    <h2 class="eyebrow" style="margin-bottom:.75rem">Data provenance</h2>
    <ul class="provenance">
      <li>Source: {source_link or 'vendor pricing page'}</li>
      <li>Last checked: <time datetime="{esc(checked)}">{esc(pretty_date(checked))}</time></li>
      <li>Checked: {'no longer' if retired else ('every day' if tier == 'daily' else 'every Monday')}</li>
      <li>Record last updated: {esc(pretty_date(record.get('captured_at')))}</li>
      <li>Tracked since: {esc(pretty_date(start))}</li>
      <li>Page versions archived: {versions}</li>
      <li>Currency: {esc(cur)}, never converted</li>
    </ul>
    <p class="provenance" style="margin-top:.75rem"><a href="{esc(CONTACT_URL)}" rel="noopener">Report a wrong figure</a></p>
  </div>
  {f'<div class="card card-pad"><h2 class="eyebrow" style="margin-bottom:.75rem">Compare {esc(name)}</h2><p class="links-cloud">{compare_links}</p></div>' if compare_links else ''}
  {f'<div class="card card-pad"><h2 class="eyebrow" style="margin-bottom:.75rem">Other {esc(title_case(category))} tools</h2><p class="links-cloud">{other_links}</p></div>' if other_links else ''}
  {track_block(name, '../')}
</aside>"""

    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("../", [("Prices", "index.html")]
                + ([(title_case(category), f"c/{storage.slugify(category)}.html")]
                   if category else [])
                + [(name, None)])}
    <div class="vhead"><div>
      <h1>{esc(name)} pricing</h1>
      <p class="vmeta">{f'<span class="badge">{esc(title_case(category))}</span>' if category else '<span class="badge">No longer tracked</span>'}
        <span><i class="dot{' warn' if notice else ''}" aria-hidden="true"></i>Last checked {esc(pretty_date(checked))}</span>
        <span>Tracked since {esc(pretty_date(start))}</span>
        {f'<span>{source_link}</span>' if source_link else ''}</p>
    </div></div>
    <div class="facts">{facts_html}</div>
  </header>
  {f'<div style="margin-bottom:1.25rem">{notice}</div>' if notice else ''}
  <div class="split">
    <div>
      <section class="card" aria-labelledby="h-plans">
        <div class="card-pad" style="padding-bottom:.75rem">
          <h2 id="h-plans" style="font-size:1.15rem">What {esc(name)} costs</h2>
          <div style="margin-top:.6rem">{_vendor_summary(name, record, ctx, category)}</div>
        </div>
        {plans_block}
        {addon_block}{last_block}
      </section>
      {chart_card}
      <section class="card card-pad" style="margin-top:1.25rem" aria-labelledby="h-changes">
        <div class="sec-head" style="margin-bottom:.75rem"><div><h2 id="h-changes">What has changed at {esc(name)}</h2>
          <p>{len(mine)} entr{'ies' if len(mine) != 1 else 'y'} on record, newest first, including any that were later reversed.</p></div></div>
        {timeline or f'<div class="empty"><strong>No changes recorded.</strong>Nothing on {esc(name)}’s pricing page has changed since tracking began.</div>'}
      </section>
      <section class="card" style="margin-top:1.25rem">
        <details class="more" style="border-top:0"><summary>Every recorded version ({len(ctx['history'].get(slug, []))})</summary>
          <div class="inner"><p class="provenance" style="margin-bottom:.75rem">Each row is a version of
            {esc(name)}’s pricing as stored in the archive, with the date it took effect.</p>
            {versions_tbl or '<p class="muted">No versions on file.</p>'}</div></details>
        <details class="more"><summary>How this page was read</summary><div class="inner">
          <p class="provenance" style="margin-bottom:.6rem">An automated reader extracts each plan
            from the page text. Its notes on anything ambiguous, verbatim:</p>
          <div class="notes">{esc(notes) if notes else 'No ambiguities noted on the latest reading.'}</div></div></details>
      </section>
    </div>
    {aside}
  </div>
</div>"""

    paid = [p for p in record.get("plans", [])
            if p.get("monthly_price") and not p.get("is_custom_pricing")]
    entry_m = min((p["monthly_price"] for p in paid), default=None)
    if addons:
        title = f"{name} pricing — plans, add-ons and price history"
        named = ", ".join(a["name"] for a in addons[:3] if a.get("name"))
        description = (f"{name} pricing: every plan, what the add-ons cost"
                       f"{' (' + named + ')' if named else ''}, and every change "
                       f"recorded since {ctx['tracking_since']}.")
    else:
        title = f"{name} pricing — current plans and price history"
        description = (
            f"{name} pricing: current plans, historical prices and every "
            f"recorded change. Entry plan {_plain_money(cur, entry_m)}."
            if entry_m else
            f"{name} pricing: current plans and every recorded change.")
    return page(title, description, body, f"v/{slug}.html",
                extra_head=vendor_schema(name, record, f"{BASE_URL}/v/{slug}.html"))


# ---------------------------------------------------------------- category

def render_category(cat: str, ctx: dict) -> str:
    ctx = _complete_ctx(ctx)
    names = [n for n in ctx["by_category"][cat]
             if storage.slugify(n) in ctx["records"]]
    bench = ctx["benchmarks"].get(cat, {})
    cur = bench.get("currency", "USD")
    events = insights.visible(ctx["events"])
    mine = [e for e in insights.headline(events) if e["category"] == cat]
    rows = []
    for name in sorted(names):
        rec = ctx["records"][storage.slugify(name)]
        entry_cell, top_cell, sort_v = _entry_cell(rec, cur)
        top_v = headline_prices(rec)[1] if sort_v else None
        free = any(p.get("is_free") for p in _real_plans(rec))
        custom = any(p.get("is_custom_pricing") for p in _real_plans(rec))
        n_changes = sum(1 for e in events if e["vendor"] == name
                        and e["group"] == "price" and e["status"] == "confirmed")
        rows.append(f"""
      <tr>
        <td class="name"><a href="../v/{esc(storage.slugify(name))}.html">{esc(name)}</a>
          <span class="sub">{esc(_how_charged(rec))}</span></td>
        <td class="num" data-l="From" data-v="{sort_v}">{entry_cell}</td>
        <td class="num" data-l="Top published" data-v="{'' if top_v is None else f'{top_v:.2f}'}">{top_cell}</td>
        <td data-l="Free plan">{'Yes' if free else 'No'}</td>
        <td data-l="Price on request">{'Yes' if custom else 'No'}</td>
        <td class="num" data-l="Price changes" data-v="{n_changes}">{n_changes}</td>
      </tr>""")
    title = title_case(cat)
    pairs = [(a, b) for a, b in comparison_pairs(ctx)
             if ctx["vendor_category"].get(a) == cat]
    cmp_links = "".join(f'<a href="../compare/{_pair_slug(a, b)}.html">{esc(a)} vs {esc(b)}</a>'
                        for a, b in pairs)
    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("../", [("Prices", "index.html"), (title, None)])}
    <h1>{esc(title)} software pricing compared</h1>
    <p class="lede">Current published prices for {len(names)} {esc(title)}
      tools, read from each vendor's own pricing page, with every confirmed
      price change since {esc(ctx['tracking_since'])}.</p>
  </header>
  {stats_row([
      (esc(money(cur, bench.get('median_entry'))), "Typical starting price"),
      (f"{bench.get('pct_free', 0):.0f}%", "Offer a free plan"),
      (f"{bench.get('pct_per_seat', 0):.0f}%", "Charge per user"),
      (str(len(names)), "Companies tracked"),
  ])}
  <p class="provenance" style="margin-top:.5rem">{mixed_currency_note(bench)}</p>
  <section class="section-tight">
    <div class="card"><div class="tbl-scroll"><table class="stack compact" data-sortable>
      <caption class="vh">{esc(title)} prices</caption>
      <thead><tr><th data-sort="text" scope="col">Company</th>
        <th class="num" data-sort="num" scope="col">From</th>
        <th class="num" data-sort="num" scope="col">Top published</th>
        <th data-sort="off" scope="col">Free plan</th>
        <th data-sort="off" scope="col">Price on request</th>
        <th class="num" data-sort="num" scope="col">Price changes</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div></div>
    <p class="provenance" style="margin-top:.75rem">"From" ignores free plans and
      anything sold on request. Quote-only plans are left out of the typical
      starting price, which is why enterprise buyers pay more than these
      figures suggest.</p>
  </section>
  <section class="section-tight">
    <div class="sec-head"><h2>Recent {esc(title)} price changes</h2>
      <a class="link-arrow" href="../changes.html?category={esc(storage.slugify(cat))}">All {esc(title)} changes</a></div>
    <div class="card">{change_table(mine[:10], "../", caption=f"Recent {title} changes")}</div>
  </section>
  {f'<section class="section-tight"><div class="sec-head"><h2>Compare {esc(title)} tools</h2></div><p class="links-cloud">{cmp_links}</p></section>' if cmp_links else ''}
</div>"""
    return page(
        f"{title} software pricing compared — {SITE_NAME}",
        f"Compare pricing across {len(names)} {title.lower() if title != 'CRM' else 'CRM'} tools. "
        f"Typical starting price {_plain_money(cur, bench.get('median_entry'))}, "
        f"with every confirmed price change.",
        body, f"c/{storage.slugify(cat)}.html")


# ---------------------------------------------------------------- compare

def _entry_on(record: dict, basis: str) -> float | None:
    field = "monthly_price" if basis == "monthly" else "annual_price_per_month"
    vals = [p.get(field) for p in _real_plans(record)
            if not p.get("is_custom_pricing") and not p.get("is_free")
            and isinstance(p.get(field), (int, float)) and p.get(field) > 0]
    return min(vals) if vals else None


def _common_entry(ra: dict, rb: dict) -> tuple[float | None, float | None, str]:
    """Entry prices for two vendors on the SAME billing basis.

    One vendor's cheapest figure is often its annual-billing price and the
    other's its monthly price. Ranking those against each other compares a
    discount with a list price. Monthly is preferred; annual is used when
    either side only publishes annual figures. Returns basis "" if no common
    basis exists.
    """
    for basis in ("monthly", "annual"):
        ea, eb = _entry_on(ra, basis), _entry_on(rb, basis)
        if ea and eb:
            return ea, eb, basis
    return headline_prices(ra)[0], headline_prices(rb)[0], ""


def _differences(a: str, b: str, ra: dict, rb: dict,
                 ctx: dict) -> tuple[str, str]:
    """Prose describing how two vendors actually differ, plus a table."""
    cur_a = (ra.get("currency") or "USD").upper()
    cur_b = (rb.get("currency") or "USD").upper()
    ea, ta, _ = headline_prices(ra)
    eb, tb, _ = headline_prices(rb)
    ca, cb, cbasis = _common_entry(ra, rb)
    on = " on annual billing" if cbasis == "annual" else ""
    free_a = any(p.get("is_free") for p in _real_plans(ra))
    free_b = any(p.get("is_free") for p in _real_plans(rb))
    seat_a = any(p.get("is_per_seat") for p in _real_plans(ra))
    seat_b = any(p.get("is_per_seat") for p in _real_plans(rb))
    trial_a, trial_b = _trial_days(ra), _trial_days(rb)

    lines = []
    if ca and cb and cur_a == cur_b and cbasis:
        dearer, cheaper = (a, b) if ca > cb else (b, a)
        hi, lo = max(ca, cb), min(ca, cb)
        gap = (hi - lo) / lo * 100
        if gap < 10:
            lines.append(f"{esc(a)} and {esc(b)} start within {gap:.0f}% of "
                         f"each other{on}, so entry price is unlikely to be what "
                         f"decides between them.")
        elif gap < 100:
            lines.append(f"{esc(cheaper)} is the cheaper way in, by about "
                         f"{gap:.0f}% on the entry plan{on}.")
        else:
            lines.append(f"{esc(cheaper)} is the cheaper way in by a wide "
                         f"margin{on} — {esc(dearer)} costs about "
                         f"{hi / lo:.1f}× as much to start.")
    elif ea and eb and cur_a == cur_b:
        lines.append(f"Their cheapest figures are on different billing terms "
                     f"(one monthly, one annual), so they are not ranked "
                     f"against each other here.")
    if free_a != free_b:
        has, hasnt = (a, b) if free_a else (b, a)
        lines.append(f"{esc(has)} publishes a free tier; {esc(hasnt)} does "
                     f"not, so trying {esc(hasnt)} means either a trial or a "
                     f"card.")
    elif free_a and free_b:
        lines.append("Both publish a free tier.")
    if seat_a != seat_b:
        per, flat = (a, b) if seat_a else (b, a)
        lines.append(f"{esc(per)} charges per person and {esc(flat)} does not "
                     f"— the gap between them widens with every person "
                     f"you add, so team size changes the answer.")
    if trial_a and trial_b and trial_a != trial_b:
        longer = a if trial_a > trial_b else b
        lines.append(f"{esc(longer)} gives you longer to evaluate "
                     f"({max(trial_a, trial_b)} days against "
                     f"{min(trial_a, trial_b)}).")
    only_a = sorted(_all_features(ra) - _all_features(rb))
    only_b = sorted(_all_features(rb) - _all_features(ra))
    if only_a:
        lines.append(f"Named on {esc(a)}'s pricing page but not "
                     f"{esc(b)}'s: {esc(', '.join(only_a[:6]))}.")
    if only_b:
        lines.append(f"Named on {esc(b)}'s pricing page but not "
                     f"{esc(a)}'s: {esc(', '.join(only_b[:6]))}.")

    changes_a = sum(1 for c in ctx["changes"] if c.get("vendor") == a)
    changes_b = sum(1 for c in ctx["changes"] if c.get("vendor") == b)
    if changes_a or changes_b:
        if changes_a and not changes_b:
            lines.append(f"Since recording began {esc(a)} has logged "
                         f"{changes_a} pricing change{'s' if changes_a != 1 else ''} "
                         f"and {esc(b)} none.")
        elif changes_b and not changes_a:
            lines.append(f"Since recording began {esc(b)} has logged "
                         f"{changes_b} pricing change{'s' if changes_b != 1 else ''} "
                         f"and {esc(a)} none.")
        else:
            lines.append(f"Both have logged pricing changes since recording "
                         f"began — {esc(a)} {changes_a}, {esc(b)} {changes_b}.")
    prose = "".join(f'<p>{ln}</p>' for ln in lines)

    def row(label, va, vb):
        return (f'<tr><td class="plan-name">{esc(label)}</td>'
                f'<td class="num" data-l="{esc(a)}">{va}</td>'
                f'<td class="num" data-l="{esc(b)}">{vb}</td></tr>')

    dash = "—"
    table = "".join([
        row("Cheapest paid plan", money(cur_a, ea), money(cur_b, eb)),
        row("Dearest published plan", money(cur_a, ta), money(cur_b, tb)),
        row("Free plan", "Yes" if free_a else "No", "Yes" if free_b else "No"),
        row("Charged", "Per person" if seat_a else "One price",
            "Per person" if seat_b else "One price"),
        row("Free trial", f"{trial_a} days" if trial_a else dash,
            f"{trial_b} days" if trial_b else dash),
        row("Plans published", str(len(_real_plans(ra))), str(len(_real_plans(rb)))),
        row("Top plan is price-on-request",
            "Yes" if any(p.get("is_custom_pricing") for p in _real_plans(ra)) else "No",
            "Yes" if any(p.get("is_custom_pricing") for p in _real_plans(rb)) else "No"),
        row("Pricing changes logged", str(changes_a), str(changes_b)),
    ])
    return prose, table


def render_compare(a: str, b: str, ctx: dict) -> str:
    ctx = _complete_ctx(ctx)
    ra, rb = ctx["records"][storage.slugify(a)], ctx["records"][storage.slugify(b)]
    cat_name = ctx["vendor_category"].get(a) or ctx["vendor_category"].get(b) or ""
    ea, eb = headline_prices(ra)[0], headline_prices(rb)[0]
    ca, cb, cbasis = _common_entry(ra, rb)
    cur_a = (ra.get("currency") or "USD").upper()
    cur_b = (rb.get("currency") or "USD").upper()
    if ea and eb and cur_a != cur_b:
        verdict = (f"These two publish in different currencies "
                   f"({esc(cur_a)} and {esc(cur_b)}), so their entry prices "
                   f"are not directly comparable. Both figures are shown "
                   f"below exactly as each vendor lists them.")
    elif ca and cb and cbasis:
        on = " on annual billing" if cbasis == "annual" else ""
        if abs(ca - cb) / min(ca, cb) < 0.05:
            verdict = f"{esc(a)} and {esc(b)} start at about the same price{on}."
        else:
            cheaper, ratio = (a, cb / ca) if ca < cb else (b, ca / cb)
            verdict = (f"{esc(cheaper)} starts {ratio:.1f}× cheaper on its "
                       f"entry plan{on}.")
    elif ea and eb:
        verdict = ("Their cheapest published figures are on different billing "
                   "terms, so they are shown side by side rather than ranked.")
    else:
        verdict = "One of these does not publish an entry price."
    prose, difftable = _differences(a, b, ra, rb, ctx)
    related = [(x, y) for (x, y) in comparison_pairs(ctx)
               if (x, y) != (a, b) and (a in (x, y) or b in (x, y))]
    related_block = ""
    if related:
        links = "".join(f'<a href="{_pair_slug(x, y)}.html">{esc(x)} vs {esc(y)}</a>'
                        for x, y in related[:12])
        related_block = f"""
  <section class="section-tight"><div class="all-group" style="margin-top:0">
    <h2>Other comparisons with {esc(a)} or {esc(b)}</h2>
    <p class="links-cloud">{links}</p></div></section>"""

    def col(name, rec):
        tiers = _real_plans(rec)
        return f"""
    <section class="card">
      <div class="card-pad" style="padding-bottom:.5rem"><h2 style="font-size:1.05rem">
        <a href="../v/{esc(storage.slugify(name))}.html">{esc(name)}</a></h2>
        <p class="provenance">Last checked {esc(pretty_date(last_checked(name, rec, ctx)))}</p></div>
      {_plans_table(rec, tiers, f"{name} plans")}
    </section>"""

    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("../", [("Prices", "index.html"),
                        (title_case(cat_name), f"c/{storage.slugify(cat_name)}.html"),
                        (f"{a} vs {b}", None)])}
    <h1>{esc(a)} vs {esc(b)} pricing</h1>
    <p class="lede">{verdict} Cheapest paid plan{" (billed annually)" if cbasis == "annual" else ""}:
      {money(cur_a, ca if cbasis else ea)} vs {money(cur_b, cb if cbasis else eb)}.</p>
  </header>
  <section class="card card-pad" style="margin-bottom:1.25rem">
    <h2 style="font-size:1.1rem;margin-bottom:.6rem">How they differ</h2>
    <div class="summary">{prose}</div>
    <div class="tbl-scroll" style="margin-top:1rem"><table class="stack">
      <caption class="vh">{esc(a)} and {esc(b)} side by side</caption>
      <thead><tr><th scope="col"><span class="vh">Measure</span></th>
        <th class="num" scope="col">{esc(a)}</th>
        <th class="num" scope="col">{esc(b)}</th></tr></thead>
      <tbody>{difftable}</tbody></table></div>
  </section>
  <div class="grid g2">{col(a, ra)}{col(b, rb)}</div>
  <p class="provenance" style="margin-top:1rem">Both read from each vendor's
    public pricing page. Feature sets differ, so compare the plans, not only
    the numbers.</p>
  {related_block}
</div>"""
    return page(f"{a} vs {b} pricing compared — {SITE_NAME}",
                f"Side-by-side pricing for {a} and {b}. "
                f"{re.sub(r'<[^>]+>', '', html.unescape(verdict))}",
                body, f"compare/{_pair_slug(a, b)}.html")


def comparison_is_worth_a_page(a: str, b: str, ctx: dict) -> bool:
    """Should this pair get its own indexable page?

    Both sides must publish an entry price, and either one is a big name
    (daily crawl tier) or the pair has recorded history. Thin templated pages
    do not just fail to rank -- they spend the crawl budget good pages need.
    """
    ra = ctx["records"].get(storage.slugify(a))
    rb = ctx["records"].get(storage.slugify(b))
    if not ra or not rb:
        return False
    if headline_prices(ra)[0] is None or headline_prices(rb)[0] is None:
        return False
    tiers = {ctx["vendors"].get(n, {}).get("crawl_tier", "weekly") for n in (a, b)}
    if "daily" in tiers:
        return True
    return any(c.get("vendor") in (a, b) for c in ctx["changes"])


def comparison_pairs(ctx: dict) -> list[tuple[str, str]]:
    """Every same-category pair that earns a page, in stable order."""
    pairs = set()
    for names in ctx["by_category"].values():
        live = sorted(n for n in names if storage.slugify(n) in ctx["records"])
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if comparison_is_worth_a_page(a, b, ctx):
                    pairs.add((a, b))
    return sorted(pairs)


def _pair_slug(a: str, b: str) -> str:
    return "-vs-".join(sorted([storage.slugify(a), storage.slugify(b)]))


# ---------------------------------------------------------------- method / about

def render_about(ctx: dict) -> str:
    ctx = _complete_ctx(ctx)
    summ = insights.summary(ctx["events"])
    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("", [("Prices", "index.html"), ("Method", None)])}
    <h1>How PriceTrail collects and checks its data</h1>
    <p class="lede">What is recorded, how a change gets confirmed, and what the
      data cannot tell you. Written so you can decide how far to trust it.</p>
  </header>
  <div class="split"><div class="grid" style="gap:1.25rem">
  <section class="card card-pad prose" id="reading">
    <h2 style="margin-bottom:.75rem">1. Reading the pages</h2>
    <p>An automated reader visits the public pricing page of every company
      listed here — every day for the most-searched names, every Monday for
      the rest. It strips away navigation, banners and chat widgets and
      compares what remains with the previous version. If the page text is
      identical, nothing more happens; most days, for most pages, that is the
      result.</p>
    <p>When the text has changed, an AI model reads it and records each plan:
      name, monthly price, annual price per month, whether it is free, per
      user, an add-on or “contact sales”, and its main limits. It is told
      to record only what the page states and to leave a value blank rather
      than guess. Struck-through and promotional prices are recorded as the
      standard price.</p>
  </section>
  <section class="card card-pad prose" id="confirmation">
    <h2 style="margin-bottom:.75rem">2. Confirming a change</h2>
    <p><strong>Nothing is published on one reading.</strong> A new figure must
      appear on two separate readings before it replaces the old one. A
      reading that would remove most of a company's prices, or take its
      pricing off the page, must hold for three days.</p>
    <p><strong>A page that loads without its prices is not news.</strong> Some
      pricing pages fill in their numbers after the page has loaded, so the
      reader sometimes gets the page without them. When that happens the last
      confirmed figures stay up and the page is flagged; only if the prices
      stay missing for two weeks is it recorded as a change.</p>
    <p><strong>Reversals are labelled, not deleted.</strong> If an entry is
      undone within three weeks — a plan removed and then re-added, prices
      hidden then shown — both entries are marked “reversed” and kept
      out of the headline figures. So far {summ['reversed']} entries have been
      marked this way. A plan removed and another added on the same day at the
      same price is shown as a rename.</p>
    <p>Tiny moves (under 1%) are treated as rounding, and any move of more than
      four times the old price is held for a person to check, because that
      is almost always a misreading rather than a repricing.</p>
  </section>
  <section class="card card-pad prose" id="statuses">
    <h2 style="margin-bottom:.75rem">3. What the labels mean</h2>
    <p><strong>Confirmed</strong>: published after confirmation.
      <strong>Reversed</strong>: undone within three weeks.
      <strong>Unconfirmed</strong>: about prices appearing or disappearing on a
      page that has loaded inconsistently before.
      <strong>Corrected</strong>: reviewed by a person, with the reason shown.</p>
  </section>
  <section class="card card-pad prose" id="currencies">
    <h2 style="margin-bottom:.75rem">4. Currencies</h2>
    <p><strong>Prices are never converted.</strong> Each price is shown in the
      currency the vendor's own page displayed, and pages are always read as
      the same visitor — one in the United States — so that today's figure
      and last month's are the same measurement. Converting would make a price
      that never moved appear to change every day with the exchange rate.
      Where a company quotes in a different currency from the rest of its
      category, it is left out of that category's typical starting price.</p>
  </section>
  <section class="card card-pad prose" id="limits">
    <h2 style="margin-bottom:.75rem">5. What this cannot tell you</h2>
    <p>Quote-only plans have no public figure, so enterprise pricing is
      largely invisible here. Prices can vary by country, and what you are
      shown may differ. Discounts negotiated with sales are not public. A page
      read this morning may have changed this afternoon. Readings are
      automated and can be wrong — which is why every figure links to its
      source page and the day it was read.</p>
  </section>
  <section class="card card-pad prose" id="corrections">
    <h2 style="margin-bottom:.75rem">6. Corrections</h2>
    <p>The raw change log is never edited. When something on it is wrong, a
      correction is written into a separate, public corrections file with the
      date, what was wrong and why. The site shows the correction next to the
      original entry. To report a wrong figure,
      <a href="{esc(CONTACT_URL)}" rel="noopener">tell us here</a>.</p>
  </section>
  </div>
  <aside class="grid" style="align-content:start">
    <div class="card card-pad"><h2 class="eyebrow" style="margin-bottom:.75rem">On this page</h2>
      <ul class="provenance">
        <li><a href="#reading">Reading the pages</a></li>
        <li><a href="#confirmation">Confirming a change</a></li>
        <li><a href="#statuses">What the labels mean</a></li>
        <li><a href="#currencies">Currencies</a></li>
        <li><a href="#limits">What this cannot tell you</a></li>
        <li><a href="#corrections">Corrections</a></li></ul></div>
    <div class="card card-pad"><h2 class="eyebrow" style="margin-bottom:.75rem">The archive today</h2>
      <ul class="provenance">
        <li>{ctx['active_count']} companies tracked</li>
        <li>{summ['price']} confirmed price changes</li>
        <li>{len(ctx['changes'])} raw log entries</li>
        <li>Recording since {esc(ctx['tracking_since'])}</li>
        <li><a href="status.html">System status</a> · <a href="bot.html">The crawler</a></li></ul></div>
  </aside></div>
</div>"""
    return page(f"Method — how the data is collected — {SITE_NAME}",
                "How PriceTrail reads, confirms and records software pricing, "
                "what its labels mean, and what the data cannot tell you.",
                body, "about.html")


# ---------------------------------------------------------------- data & downloads

DOWNLOADS = [
    {"path": "downloads/prices.csv", "format": "text/csv",
     "title": "Current prices",
     "about": "Every plan and add-on on record today: company, category, plan, "
              "monthly and annual price, currency, flags and last-checked date."},
    {"path": "downloads/changes.csv", "format": "text/csv",
     "title": "Change log",
     "about": "Every logged change with before and after values, percentage, "
              "date, type and status (confirmed, reversed, unconfirmed, corrected)."},
    {"path": "downloads/history.csv", "format": "text/csv",
     "title": "Price history",
     "about": "Every recorded version of every company's prices, one row per "
              "plan per version, with the date that version took effect."},
    {"path": "downloads/pricetrail.json", "format": "application/json",
     "title": "Everything, as JSON",
     "about": "Companies, current plans, interpreted change events and full "
              "history in one file. Same address every day."},
]


def render_data(ctx: dict, sizes: dict[str, int] | None = None) -> str:
    ctx = _complete_ctx(ctx)
    sizes = sizes or {}
    rows = "".join(f"""
    <div class="dl"><div><h3>{esc(d['title'])}</h3><p>{esc(d['about'])}</p></div>
      <a class="btn btn-ghost btn-sm" href="{esc(d['path'])}" download>
        {esc(d['path'].rsplit('.', 1)[1].upper())}{f" · {sizes[d['path']] // 1024 or 1} KB" if sizes.get(d['path']) else ""}</a></div>"""
                   for d in DOWNLOADS)
    summ = insights.summary(ctx["events"])
    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("", [("Prices", "index.html"), ("Data", None)])}
    <h1>Data and downloads</h1>
    <p class="lede">The archive behind this site, as files you can open in a
      spreadsheet or load into code. Regenerated after every daily check, at
      the same addresses.</p>
  </header>
  {stats_row([
      (str(ctx['active_count']), "Companies tracked"),
      (str(sum(len(r.get('plans', [])) for r in ctx['records'].values())), "Plans and add-ons"),
      (str(len(ctx['changes'])), "Logged changes"),
      (str(sum(len(v) for v in ctx['history'].values())), "Recorded versions"),
  ])}
  <div class="split" style="margin-top:1.5rem">
    <div class="grid" style="gap:1.25rem;align-content:start">
      <section class="card" aria-labelledby="h-dl">
        <div class="card-pad" style="padding-bottom:.5rem"><h2 id="h-dl" style="font-size:1.1rem">Downloads</h2>
          <p class="provenance">Updated {esc(pretty_date(ctx.get('last_checked_iso')))}. UTF-8, comma-separated, one header row.</p></div>
        {rows}
      </section>
      <section class="card card-pad prose">
        <h2 style="margin-bottom:.75rem">Using it in code</h2>
        <p>There is no key and no sign-up. The JSON file is a static file at a
          fixed address, refreshed daily, so you can fetch it on a schedule:</p>
        <p><code>{esc(BASE_URL)}/downloads/pricetrail.json</code></p>
        <p>Changes are also published as an RSS feed at
          <code>{esc(BASE_URL)}/feed.xml</code>. This is a static archive, not a
          live API: there is no querying, and figures are as fresh as the last
          daily check.</p>
      </section>
      <section class="card card-pad prose">
        <h2 style="margin-bottom:.75rem">Who can use it</h2>
        <p><strong>Free</strong> for personal use, study, journalism with a
          link back, and for checking prices before you buy a tool — at
          work too.</p>
        <p><strong>Using the data in a business</strong> — in internal tools,
          dashboards, client reports, products or AI systems — needs a
          <a href="pricing.html">commercial licence</a> ({esc(LICENCE_PRICE)}).
          Reselling or republishing the dataset itself is not allowed under
          either. Full terms are in the <a href="terms.html">terms of use</a>.</p>
        <p><strong>Need something else?</strong> More history, another category
          or a different format —
          <a href="{esc(CONTACT_URL)}" rel="noopener">ask here</a>.</p>
      </section>
    </div>
    <aside class="grid" style="align-content:start">
      <div class="card card-pad"><h2 class="eyebrow" style="margin-bottom:.75rem">What is in it</h2>
        <ul class="provenance">
          <li>{ctx['active_count']} companies in {len([c for c in ctx['by_category'] if any(storage.slugify(n) in ctx['records'] for n in ctx['by_category'][c])])} categories</li>
          <li>Recording since {esc(ctx['tracking_since'])}</li>
          <li>Daily or weekly checks per company</li>
          <li>Prices in the vendor's own currency</li>
          <li>Every change with its confirmation status</li></ul></div>
      <div class="card card-pad"><h2 class="eyebrow" style="margin-bottom:.75rem">Read first</h2>
        <ul class="provenance">
          <li><a href="about.html#confirmation">How changes are confirmed</a></li>
          <li><a href="about.html#limits">What the data cannot tell you</a></li>
          <li><a href="status.html">Is the crawler healthy?</a></li></ul></div>
    </aside>
  </div>
</div>"""
    return page(f"SaaS pricing data and downloads — {SITE_NAME}",
                f"Download PriceTrail's software pricing archive as CSV or JSON: "
                f"current prices, every logged change and full price history "
                f"for {len(ctx['records'])} companies.",
                body, "data.html",
                extra_head=dataset_schema(len(ctx["records"]), summ["confirmed"],
                                          ctx["tracking_since"], DOWNLOADS))


def render_search(ctx: dict) -> str:
    ctx = _complete_ctx(ctx)
    fallback = []
    for cat, names in sorted(ctx["by_category"].items()):
        live = sorted(n for n in names if storage.slugify(n) in ctx["records"])
        if live:
            links = "".join(f'<a href="v/{storage.slugify(n)}.html">{esc(n)}</a>' for n in live)
            fallback.append(f'<div class="all-group"><h2>{esc(title_case(cat))}</h2>'
                            f'<p class="links-cloud">{links}</p></div>')
    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("", [("Prices", "index.html"), ("Search", None)])}
    <h1>Search</h1>
  </header>
  <div class="card card-pad">
    <label class="field" style="width:100%;max-width:36rem;height:44px">{SEARCH_SVG}
      <span class="vh">Search companies, categories and plans</span>
      <input id="q2" type="search" placeholder="Company, category or plan name" autofocus></label>
    <p class="provenance" id="search-count" aria-live="polite" style="margin-top:.6rem"></p>
    <div id="search-results" style="margin-top:.5rem"></div>
    <div id="search-fallback"><p class="provenance" style="margin-top:.5rem">Every company on record:</p>{''.join(fallback)}</div>
  </div>
</div>"""
    return page(f"Search — {SITE_NAME}", "Search companies, categories and plans.",
                body, "search.html", noindex=True)


def render_404(ctx: dict) -> str:
    body = f"""
<div class="wrap">
  <header class="page-head">
    <p class="eyebrow">Error 404</p>
    <h1>That page isn’t here</h1>
    <p class="lede">The address may be mistyped, or the page may have moved.
      Every company PriceTrail tracks is still a search away.</p>
  </header>
  <div class="hero-actions" style="margin-top:0">
    <a class="btn btn-primary" href="/">Go to all prices</a>
    <a class="btn btn-ghost" href="/changes.html">Browse price changes</a>
    <a class="btn btn-ghost" href="/all.html">Every page</a>
  </div>
</div>"""
    return page(f"Page not found — {SITE_NAME}", "This page could not be found.",
                body, "404.html", noindex=True, absolute_links=True)


def render_all_pages(ctx: dict, pairs: list[tuple[str, str]]) -> str:
    """One page linking to every other page on the site."""
    ctx = _complete_ctx(ctx)
    cats = []
    for cat, names in sorted(ctx["by_category"].items()):
        live = sorted(n for n in names if storage.slugify(n) in ctx["records"])
        if not live:
            continue
        vend = "".join(f'<a href="v/{storage.slugify(n)}.html">{esc(n)}</a>' for n in live)
        cats.append(f'<div class="all-group"><h2><a href="c/{storage.slugify(cat)}.html">'
                    f'{esc(title_case(cat))}</a></h2><p class="all-links">{vend}</p></div>')
    comp = "".join(f'<a href="compare/{_pair_slug(a, b)}.html">{esc(a)} vs {esc(b)}</a>'
                   for a, b in pairs)
    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("", [("Prices", "index.html"), ("Every page", None)])}
    <h1>Every page on this site</h1>
    <p class="lede">{len(ctx['records'])} companies, every category and
      {len(pairs)} side-by-side comparisons, in one list.</p>
  </header>
  {''.join(cats)}
  <div class="all-group"><h2>Side-by-side comparisons</h2><p class="all-links">{comp}</p></div>
  <div class="all-group"><h2>About this site</h2><p class="all-links">
    <a href="changes.html">Every recorded change</a>
    <a href="week.html">This week in software pricing</a>
    <a href="data.html">Data and downloads</a>
    <a href="pricing.html">Pricing</a>
    <a href="about.html">How this is collected</a>
    <a href="bot.html">About the crawler</a>
    <a href="status.html">System status</a></p></div>
</div>"""
    return page("Every page — " + SITE_NAME,
                f"A full index of all {len(ctx['records'])} tracked companies, "
                f"every category and every price comparison on {SITE_NAME}.",
                body, "all.html")


def _legal(title: str, crumb: str, lede: str, paras: list[str], path: str,
           description: str) -> str:
    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("", [("Prices", "index.html"), (crumb, None)])}
    <h1>{esc(title)}</h1>
    <p class="lede">{lede}</p>
  </header>
  <div class="card card-pad prose">{''.join(f'<p>{p}</p>' for p in paras)}</div>
</div>"""
    return page(f"{title} — {SITE_NAME}", description, body, path)


def render_privacy() -> str:
    return _legal("Privacy", "Privacy", "Plain English, no lawyers.", [
        f"<strong>Reading this site is anonymous.</strong> There is no sign-up, "
        f"no login and no advertising. {SITE_NAME} sets no cookies and runs no "
        f"analytics or tracking scripts. The site's one script runs search and "
        f"filters in your browser and sends nothing anywhere.",
        "The site is served by GitHub Pages, and web fonts are loaded from "
        "Google Fonts. Like any web host, those services see the request your "
        "browser makes, including your IP address.",
        "<strong>If you subscribe to price-change emails</strong> (when that is "
        "offered), the only thing stored is the email address you typed. It is "
        "held by the mailing service that sends the emails, used for nothing "
        "except sending them, and never sold, rented or shared. Every email "
        "carries a one-click unsubscribe link, and using it deletes the address.",
        "You can ask what is held about you and ask for it to be deleted at any "
        "time.",
        "<strong>The pricing data itself is not personal information.</strong> "
        "It is published prices from companies' own public pages.",
    ], "privacy.html",
        f"What {SITE_NAME} does and does not collect. No tracking, no cookies, "
        f"no advertising.")


def render_terms() -> str:
    return _legal("Terms of use", "Terms of use", "What this is and is not.", [
        f"<strong>This is a record, not advice.</strong> Every figure is what a "
        f"vendor published on its own page on the day it was read. Prices "
        f"change, vendors run offers, and regional pricing differs. Always "
        f"confirm with the vendor before you commit money. {SITE_NAME} is not "
        f"responsible for decisions made on the strength of a figure here.",
        "<strong>Mistakes.</strong> The readings are automated and can be "
        "wrong. Where a page cannot be read reliably, nothing is shown rather "
        f"than a guess. If you spot an error, <a href=\"{esc(CONTACT_URL)}\" "
        "rel=\"noopener\">please say so</a> and it will be corrected.",
        "<strong>Using what is here.</strong> Read it, quote it and link to "
        "it. Downloading the data is free for personal use, study, journalism "
        "with a link back, and for checking prices before buying a tool. If "
        "you use figures in something you publish, link back.",
        "<strong>Commercial use.</strong> Using the downloaded data in a "
        "business \u2014 in internal tools, dashboards, client reports, "
        "products or AI systems \u2014 needs a "
        "<a href=\"pricing.html\">commercial licence</a>, one per "
        "organisation. Under any licence, reselling or republishing the "
        "dataset itself, or using it to build a competing price-tracking "
        "service, is not allowed: the compiled archive is protected as a "
        "database. A licence is a monthly subscription: cancel at any time and "
        "it runs to the end of the month already paid. If you are not happy, "
        "ask within 14 days of your first payment for a full refund.",
        f"<strong>Company names</strong> and trademarks belong to their owners. "
        f"{SITE_NAME} is independent and is not affiliated with, endorsed by, "
        f"or paid by any vendor listed. Nothing here is sponsored, and no "
        f"vendor can pay to change what is recorded.",
        "<strong>The crawler</strong> reads public pricing pages slowly, "
        "identifies itself honestly and obeys robots.txt. If you run one of "
        "these sites and would rather not be read, the "
        "<a href=\"bot.html\">crawler page</a> explains how to say so.",
    ], "terms.html",
        f"How to use {SITE_NAME}: a record of published prices, not advice. "
        f"Independent, unsponsored, and corrected when wrong.")


def render_bot() -> str:
    return _legal("About the crawler", "About the crawler",
                  "Pages here are read by an automated crawler identifying "
                  "itself as <code>PriceTrailBot</code>.", [
        "It obeys robots.txt. It waits at least three seconds between requests "
        "to the same site and backs off when a server asks it to slow down. It "
        "reads pricing pages only, at most once a day. It records facts — "
        "prices, plan names, dates — not page text or design.",
        f"If you would rather we did not read your pricing page, "
        f"<a href=\"{esc(CONTACT_URL)}\" rel=\"noopener\">say so</a> and it "
        f"will be removed. No argument, no forms.",
    ], "bot.html", "How the PriceTrail crawler behaves, and how to opt out.")


# ---------------------------------------------------------------- pricing

def render_pricing(ctx: dict) -> str:
    """Free vs commercial licence. Honest in both states: with no checkout
    link configured, the licence is requested rather than bought."""
    ctx = _complete_ctx(ctx)
    if LICENCE_URL:
        buy = (f'<a class="btn btn-primary" href="{esc(LICENCE_URL)}" '
               f'rel="noopener">Buy a licence \u2014 {esc(LICENCE_PRICE)}</a>')
        how = ("Checkout is handled by our payment provider, which emails your "
               "receipt and lets you cancel at any time.")
    else:
        buy = (f'<a class="btn btn-primary" href="{esc(CONTACT_URL)}" '
               f'rel="noopener">Ask about a licence</a>')
        how = ("Online checkout is not open yet. Ask, and you will get a "
               "licence and an invoice directly.")
    n = ctx["active_count"]
    body = f"""
<div class="wrap">
  <header class="page-head">
    {breadcrumb("", [("Prices", "index.html"), ("Pricing", None)])}
    <h1>Pricing</h1>
    <p class="lede">Everything on this site is free to read, for everyone.
      A licence is only needed to use the data inside a business.</p>
  </header>
  <div class="grid g2" style="align-items:stretch">
    <section class="card card-pad" aria-labelledby="h-free">
      <p class="eyebrow">Free</p>
      <h2 id="h-free" style="font-size:1.6rem;margin:.35rem 0 .2rem">\u00a30</h2>
      <p class="muted small">No account, no sign-up</p>
      <ul class="provenance" style="margin:1rem 0 1.25rem">
        <li>\u2713 Every price and every change on the site</li>
        <li>\u2713 Price history for all {n} companies</li>
        <li>\u2713 RSS feed of confirmed changes</li>
        <li>\u2713 Downloads for personal use, study and journalism</li>
        <li>\u2713 Checking prices before you buy a tool, at work too</li>
      </ul>
      <a class="btn btn-ghost" href="index.html">Browse prices</a>
    </section>
    <section class="card card-pad" aria-labelledby="h-lic" style="border-color:var(--accent)">
      <p class="eyebrow" style="color:var(--accent)">Commercial licence</p>
      <h2 id="h-lic" style="font-size:1.6rem;margin:.35rem 0 .2rem">{esc(LICENCE_PRICE)}</h2>
      <p class="muted small">Per organisation \u00b7 cancel any time</p>
      <ul class="provenance" style="margin:1rem 0 1.25rem">
        <li>\u2713 Use the data in internal tools and dashboards</li>
        <li>\u2713 Use it in reports and work for clients</li>
        <li>\u2713 Use it in your own products and AI systems</li>
        <li>\u2713 The same daily-updated CSV and JSON files</li>
        <li>\u2713 Requests for new companies or categories go first</li>
      </ul>
      {buy}
      <p class="provenance" style="margin-top:.75rem">{esc(how)}</p>
    </section>
  </div>
  <section class="section">
    <div class="sec-head"><h2>Questions</h2></div>
    <div class="grid g2">
      <div class="card card-pad prose"><p><strong>Do I need a licence to look
        up a price?</strong> No. Reading the site, at home or at work, is free.
        The licence is for using the downloaded data inside a business.</p>
        <p><strong>What is not allowed, even with a licence?</strong> Reselling
        or republishing the dataset itself, or using it to build a competing
        price-tracking service.</p></div>
      <div class="card card-pad prose"><p><strong>How do I cancel?</strong> Any
        time, from the link in your receipt. The licence runs to the end of the
        month already paid.</p>
        <p><strong>Refunds?</strong> If you are not happy, ask within 14 days of
        your first payment for a full refund.</p>
        <p><strong>Is the data accurate?</strong> Read <a href="about.html">how
        it is collected</a> first \u2014 it is a careful record, not a
        guarantee. Full terms: <a href="terms.html">terms of use</a>.</p></div>
    </div>
  </section>
</div>"""
    return page(f"Pricing \u2014 free to read, licence for business use \u2014 {SITE_NAME}",
                f"PriceTrail is free to read. A commercial licence "
                f"({LICENCE_PRICE}) covers using the pricing data in a business.",
                body, "pricing.html")


# ---------------------------------------------------------------- feeds

FEED_STYLESHEET = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
<xsl:output method="html" encoding="UTF-8" indent="yes"/>
<xsl:template match="/">
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title><xsl:value-of select="rss/channel/title"/></title>
LINKS
</head><body>
<header class="site-header"><div class="wrap hdr">
  <a class="brand" href="/index.html">PriceTrail</a>
  <nav class="nav"><a href="/index.html">Prices</a>
       <a href="/changes.html">Changes</a></nav>
</div></header>
<main><div class="wrap">
  <header class="page-head">
    <p class="backlink"><a href="/index.html">&#8592; All prices</a></p>
    <h1>Change feed</h1>
    <p class="lede">This page is a feed. Paste its address into any feed reader
      and every new confirmed pricing change appears there automatically
      — no signup, no email.</p>
  </header>
  <div class="card"><ul style="list-style:none">
  <xsl:for-each select="rss/channel/item">
    <li class="dl"><div><h3><a><xsl:attribute name="href">
          <xsl:value-of select="link"/></xsl:attribute>
          <xsl:value-of select="title"/></a></h3>
        <p><xsl:value-of select="substring(pubDate, 1, 16)"/></p></div></li>
  </xsl:for-each>
  </ul></div>
</div></main>
</body></html>
</xsl:template>
</xsl:stylesheet>
"""


def _rfc822(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def render_feed(ctx: dict) -> str:
    """Confirmed changes only. Reversed and unconfirmed entries stay on the
    site's change log, labelled, but are not pushed to subscribers as news."""
    ctx = _complete_ctx(ctx)
    items = []
    events = [e for e in ctx.get("events", []) if e["status"] in ("confirmed", "corrected")]
    for e in events[:50]:
        title = event_sentence(e)
        items.append(f"""  <item>
    <title>{esc(title)}</title>
    <link>{BASE_URL}/v/{esc(e['slug'])}.html#c-{esc(e['id'])}</link>
    <guid isPermaLink="false">{esc(e['detected_at'])}-{esc(e['slug'])}-{esc(e['id'])}</guid>
    <pubDate>{esc(_rfc822(e['detected_at']))}</pubDate>
    <category>{esc(title_case(e['category'] or ''))}</category>
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type="text/xsl" href="{BASE_URL}/feed.xsl"?>
<rss version="2.0"><channel>
  <title>{SITE_NAME} — software pricing changes</title>
  <link>{BASE_URL}</link>
  <description>{esc(TAGLINE)} Confirmed price changes for tracked B2B software.</description>
  <language>en</language>
{chr(10).join(items)}
</channel></rss>
"""


def page_lastmod(ctx: dict) -> dict[str, str]:
    """When each page's underlying pricing data genuinely last changed.

    lastmod must mark the last significant change, not the build time, or
    Google learns to ignore it.
    """
    since = storage.recording_since()

    def day(value: str | None) -> str:
        return (value or since)[:10] or since

    latest: dict[str, str] = {}
    for change in ctx["changes"]:
        vendor = change.get("vendor")
        when = day(change.get("detected_at"))
        if vendor and when > latest.get(vendor, ""):
            latest[vendor] = when

    def for_vendor(name: str) -> str:
        return latest.get(name, since)

    newest_overall = max(latest.values(), default=since)
    out: dict[str, str] = {}
    for path in ("index.html", "changes.html", "week.html", "data.html"):
        out[path] = newest_overall
    for path in ("about.html", "bot.html", "status.html", "privacy.html",
                 "terms.html", "all.html", "pricing.html"):
        out[path] = since
    slug_to_name = {storage.slugify(n): n for n in ctx["vendors"]}
    for slug in ctx["records"]:
        out[f"v/{slug}.html"] = for_vendor(slug_to_name.get(slug, slug))
    for cat, names in ctx["by_category"].items():
        live = [n for n in names if storage.slugify(n) in ctx["records"]]
        if live:
            out[f"c/{storage.slugify(cat)}.html"] = max(
                (for_vendor(n) for n in live), default=since)
    for a, b in comparison_pairs(ctx):
        out[f"compare/{_pair_slug(a, b)}.html"] = max(for_vendor(a), for_vendor(b))
    return out


def render_sitemap(paths: list[str], lastmod: dict[str, str] | None = None) -> str:
    lastmod = lastmod or {}
    fallback = storage.recording_since()

    def canonical(p: str) -> str:
        return f"{BASE_URL}/{p}".replace("/index.html", "/")

    urls = "".join(f"  <url><loc>{canonical(p)}</loc>"
                   f"<lastmod>{lastmod.get(p, fallback)}</lastmod></url>\n"
                   for p in paths)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}</urlset>\n")


# ---------------------------------------------------------------- downloads & index

def _csv(rows: list[list], header: list[str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        # A cell starting with = + - @ is executed as a formula by Excel. The
        # values come from third-party pages, so neutralise them.
        w.writerow([("'" + c) if isinstance(c, str) and c[:1] in ("=", "+", "-", "@")
                    else c for c in r])
    return buf.getvalue()


def write_downloads(out: Path, ctx: dict) -> dict[str, int]:
    folder = out / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    slug_to_name = ctx["slug_to_name"]

    price_rows = []
    for slug in sorted(ctx["records"]):
        rec = ctx["records"][slug]
        name = slug_to_name.get(slug, title_case(slug))
        meta = ctx["vendors"].get(name, {}) or {}
        for p in rec.get("plans", []):
            price_rows.append([
                name, meta.get("category", ""), p.get("name", ""),
                "add-on" if p.get("is_addon") else "plan",
                rec.get("currency", ""), p.get("monthly_price"),
                p.get("annual_price_per_month"), bool(p.get("is_free")),
                bool(p.get("is_custom_pricing")), bool(p.get("is_per_seat")),
                last_checked(name, rec, ctx), str(rec.get("captured_at") or "")[:10],
                meta.get("pricing_url", "")])
    prices_csv = _csv(price_rows, [
        "company", "category", "plan", "type", "currency", "monthly_price",
        "annual_price_per_month", "is_free", "contact_sales", "per_user",
        "last_checked", "record_updated", "source_url"])

    change_rows = [[e["id"], e["date"], e["vendor"], e["category"], e.get("plan") or "",
                    e["change_type"], e.get("field") or "",
                    "" if e["old"] is None else e["old"],
                    "" if e["new"] is None else e["new"],
                    "" if e["pct"] is None else e["pct"], e["status"],
                    e.get("status_note", "")]
                   for e in ctx["events"]]
    changes_csv = _csv(change_rows, [
        "id", "date", "company", "category", "plan", "change_type", "field",
        "old_value", "new_value", "pct_change", "status", "status_note"])

    hist_rows = []
    for slug in sorted(ctx["history"]):
        name = slug_to_name.get(slug, title_case(slug))
        for point in ctx["history"][slug]:
            for p in point.get("plans", []):
                hist_rows.append([name, point["date"], point.get("currency", ""),
                                  p["name"], "add-on" if p.get("addon") else "plan",
                                  p.get("m"), p.get("a"), p.get("free"), p.get("custom")])
    history_csv = _csv(hist_rows, [
        "company", "version_from", "currency", "plan", "type", "monthly_price",
        "annual_price_per_month", "is_free", "contact_sales"])

    everything = {
        "name": f"{SITE_NAME} SaaS pricing archive",
        # A date the data is true as of, not a build timestamp: identical data
        # must produce identical files, or every daily commit churns.
        "data_as_of": ctx.get("last_checked_iso", ""),
        "recording_since": ctx["since_iso"],
        "terms": f"{BASE_URL}/terms.html",
        "companies": [{
            "name": slug_to_name.get(slug, title_case(slug)),
            "slug": slug,
            "category": (ctx["vendors"].get(slug_to_name.get(slug, ""), {}) or {}).get("category", ""),
            "source_url": (ctx["vendors"].get(slug_to_name.get(slug, ""), {}) or {}).get("pricing_url", ""),
            "currency": ctx["records"][slug].get("currency", ""),
            "last_checked": last_checked(slug_to_name.get(slug, ""), ctx["records"][slug], ctx),
            "plans": [{k: p.get(k) for k in ("name", "monthly_price",
                       "annual_price_per_month", "is_free", "is_custom_pricing",
                       "is_per_seat", "is_addon")}
                      for p in ctx["records"][slug].get("plans", [])],
            "history": ctx["history"].get(slug, []),
        } for slug in sorted(ctx["records"])],
        "changes": [{k: v for k, v in e.items() if k not in ("raw", "plan_key")}
                    for e in ctx["events"]],
    }
    files = {
        "downloads/prices.csv": prices_csv,
        "downloads/changes.csv": changes_csv,
        "downloads/history.csv": history_csv,
        "downloads/pricetrail.json": json.dumps(everything, ensure_ascii=False,
                                                indent=1, default=str),
    }
    sizes = {}
    for rel, text in files.items():
        data = text.encode("utf-8")
        (out / rel).write_bytes(data)
        sizes[rel] = len(data)
    return sizes


def search_index(ctx: dict, pairs: list[tuple[str, str]]) -> list[dict]:
    items = []
    for slug, rec in sorted(ctx["records"].items()):
        name = ctx["slug_to_name"].get(slug, title_case(slug))
        meta = ctx["vendors"].get(name, {}) or {}
        cat = meta.get("category", "")
        entry = headline_prices(rec)[0]
        words = " ".join([title_case(cat), _domain(meta.get("pricing_url", ""))]
                         + [p.get("name", "") for p in rec.get("plans", [])])
        items.append({"t": name, "k": "Company", "u": f"v/{slug}.html",
                      "m": f"{title_case(cat)}"
                           + (f" · from {_plain_money(rec.get('currency'), entry)}" if entry else ""),
                      "n": _norm(name), "w": _norm(words)})
    for cat, names in sorted(ctx["by_category"].items()):
        live = [n for n in names if storage.slugify(n) in ctx["records"]]
        if live:
            items.append({"t": f"{title_case(cat)} pricing", "k": "Category",
                          "u": f"c/{storage.slugify(cat)}.html",
                          "m": f"{len(live)} companies",
                          "n": _norm(title_case(cat)), "w": _norm(cat + " software pricing compared")})
    for a, b in pairs:
        items.append({"t": f"{a} vs {b}", "k": "Compare",
                      "u": f"compare/{_pair_slug(a, b)}.html", "m": "",
                      "n": _norm(f"{a} vs {b}"), "w": _norm(f"{a} {b} compare comparison")})
    for path, title, words in (("changes.html", "Price changes", "changes log rises cuts history"),
                               ("data.html", "Data and downloads", "csv json download api dataset"),
                               ("pricing.html", "Pricing", "pricing licence license buy commercial"),
                               ("about.html", "Method", "how collected method confirmation"),
                               ("status.html", "System status", "status health crawler")):
        items.append({"t": title, "k": "Page", "u": path, "m": "",
                      "n": _norm(title), "w": _norm(words)})
    return items


ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<rect width="32" height="32" rx="7" fill="#1D3FCF"/>'
            '<path d="M7 22h6v-6h6v-6h6" fill="none" stroke="#fff" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')


# ---------------------------------------------------------------- build

def build(out_dir: Path | None = None) -> dict:
    """Generate the whole site. Returns a summary of what was written."""
    out = out_dir or (storage.ROOT / "site")
    for sub in ("", "v", "c", "compare", "assets", "downloads"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    cfg = yaml.safe_load((storage.ROOT / "vendors.yaml").read_text("utf-8"))
    vendors = {v["name"]: dict(v) for v in cfg["vendors"]}
    vendor_category = {v["name"]: v.get("category", "uncategorised")
                       for v in cfg["vendors"]}
    by_category: dict[str, list[str]] = defaultdict(list)
    for name, cat in vendor_category.items():
        by_category[cat].append(name)

    # Only vendors with real extracted plans get pages.
    records = {}
    for path in sorted(storage.PLANS.glob("*.json")):
        try:
            rec = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(rec, dict) and rec.get("plans"):
            records[path.stem] = rec

    versions = {}
    for slug in records:
        folder = storage.SNAPSHOTS / slug
        versions[slug] = len(list(folder.glob("*.txt"))) if folder.exists() else 1
    snapshot_count = sum(len(list(d.glob("*.txt")))
                         for d in storage.SNAPSHOTS.glob("*") if d.is_dir()) \
        if storage.SNAPSHOTS.exists() else 0

    global _IS_DEMO
    _IS_DEMO = any(r.get("demo") for r in records.values())

    changes = storage.read_changes()
    since_iso = storage.recording_since()
    since = pretty_date(since_iso)
    for name, rec in ((n, records.get(storage.slugify(n))) for n in vendors):
        if rec:
            vendors[name]["currency"] = rec.get("currency", "USD")

    history = hist.load_all()
    # Only history for vendors that are on the site.
    history = {s: h for s, h in history.items() if s in records}
    state = storage.load_state()
    addon_lookup = insights.addon_lookup_from(history, records)
    events = insights.build_events(changes, vendors, addon_lookup=addon_lookup)

    last_price_change: dict[str, str] = {}
    for e in events:
        if e["group"] == "price" and e["status"] == "confirmed":
            if e["date"] > last_price_change.get(e["vendor"], ""):
                last_price_change[e["vendor"]] = e["date"]

    tracked_slugs = {storage.slugify(n) for n in vendors}
    checks = [v.get("last_checked", "") for k, v in state.items()
              if isinstance(v, dict) and k in tracked_slugs]
    last_checked_iso = max(checks, default="") or max(
        (str(r.get("captured_at") or "")[:10] for r in records.values()), default="")

    ctx = {
        "records": records, "changes": changes, "vendors": vendors,
        "by_category": by_category, "vendor_category": vendor_category,
        "versions": versions, "tracking_since": since, "since_iso": since_iso,
        "benchmarks": {cat: _benchmarks(names, records)
                       for cat, names in by_category.items()},
        "history": history, "state": state, "events": events,
        "last_price_change": last_price_change,
        "last_checked_iso": last_checked_iso,
        "snapshot_count": snapshot_count,
        "slug_to_name": {storage.slugify(n): n for n in vendors},
        "active_count": sum(1 for s in records if s in tracked_slugs),
    }

    written: list[str] = []

    def write(path: str, content: str, index: bool = True):
        (out / path).write_text(content, encoding="utf-8")
        if index:
            written.append(path)

    (out / "assets" / "style.css").write_text(CSS + FILTER_CSS, encoding="utf-8")
    (out / "assets" / "app.js").write_text(FILTER_JS, encoding="utf-8")
    (out / "assets" / "icon.svg").write_text(ICON_SVG, encoding="utf-8")
    og = Path(__file__).parent / "static" / "og.png"
    if og.exists():
        (out / "assets" / "og.png").write_bytes(og.read_bytes())

    pairs = comparison_pairs(ctx)
    index_js = ("window.PT_INDEX=" + json.dumps(search_index(ctx, pairs),
                                                 ensure_ascii=False,
                                                 separators=(",", ":"))
                .replace("<", "\\u003c") + ";\n")
    (out / "assets" / "search-index.js").write_text(index_js, encoding="utf-8")
    # Kept so any old cached page that asks for it still gets a valid script.
    (out / "assets" / "find.js").write_text(FILTER_JS, encoding="utf-8")

    sizes = write_downloads(out, ctx)

    write("index.html", render_index(ctx))
    write("changes.html", render_changes(ctx))
    write("week.html", render_digest(ctx))
    write("data.html", render_data(ctx, sizes))
    write("pricing.html", render_pricing(ctx))
    write("about.html", render_about(ctx))
    from . import status as _status
    write("status.html", _status.render(esc, page, back_link, pretty_date,
                                        SITE_NAME))
    write("bot.html", render_bot())
    write("privacy.html", render_privacy())
    write("terms.html", render_terms())

    slug_to_name = ctx["slug_to_name"]
    for slug in sorted(records):
        write(f"v/{slug}.html",
              render_vendor(slug, slug_to_name.get(slug, title_case(slug)), ctx))
    for cat, names in sorted(by_category.items()):
        if any(storage.slugify(n) in records for n in names):
            write(f"c/{storage.slugify(cat)}.html", render_category(cat, ctx))
    for a, b in pairs:
        write(f"compare/{_pair_slug(a, b)}.html", render_compare(a, b, ctx))
    write("all.html", render_all_pages(ctx, pairs))
    # Not listed in the sitemap: a search page and an error page are not
    # pages anyone should land on from a search engine.
    write("search.html", render_search(ctx), index=False)
    write("404.html", render_404(ctx), index=False)

    (out / "feed.xml").write_text(render_feed(ctx), encoding="utf-8")
    (out / "feed.xsl").write_text(
        FEED_STYLESHEET.replace("LINKS", FONT_LINK_XML +
                                f'<link rel="stylesheet" href="{BASE_URL}/assets/style.css"/>'),
        encoding="utf-8")
    (out / "sitemap.xml").write_text(render_sitemap(written, page_lastmod(ctx)),
                                     encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nDisallow: /search.html\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    return {
        "pages": len(written), "vendors": len(records),
        "changes": len(changes), "comparisons": len(pairs), "out": out,
        "demo": _IS_DEMO,
    }
