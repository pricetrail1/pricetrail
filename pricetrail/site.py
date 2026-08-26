"""
Data on disk -> a website.

Every page is plain static HTML with no JavaScript. That is a deliberate
choice, not a shortcut: search engines index it instantly, it loads on a bad
phone connection, it costs nothing to host, and it cannot break at 3am.

The publishing rule, enforced in build(): a page is only written where there
is real data behind it. Auto-generated pages with nothing unique on them are
exactly how sites get buried by search engines, so no data means no page.
"""

from __future__ import annotations

import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import storage
from .interact import FILTER_CSS, FILTER_JS
from .theme import CSS, FONT_LINK, FONT_LINK_XML

SITE_NAME = "PriceTrail"


def _default_base_url() -> str:
    """Work out the site address instead of making you type it.

    GitHub sets GITHUB_REPOSITORY to "owner/repo" during a workflow run, which
    is exactly enough to build the GitHub Pages address. So the sitemap and RSS
    feed come out correct with nothing to configure.

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
TAGLINE = "A permanent record of what software costs."

# Paste an email signup form URL here (Buttondown, Beehiiv, Resend, anything
# that gives you a hosted form) and a signup box appears site-wide. Until then
# the site offers RSS instead, which needs no account and no backend.
def _signup_action(raw: str) -> str:
    """Accept a bare Buttondown username as well as a full form address.

    Setting this up meant finding the right endpoint, getting the path exactly
    right, and pasting a long URL into a settings box -- three chances to make
    a silent typo that shows up as a form which appears to work and quietly
    loses every address. A username is one word, and one word is hard to get
    wrong.

    Anything containing "/" is treated as a full address, so MailerLite, Kit
    and EmailOctopus all still work unchanged.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")) or "/" in raw:
        return raw
    # Bare word -> Buttondown, the service the README walks through.
    return f"https://buttondown.com/api/emails/embed-subscribe/{raw}"


SIGNUP_URL = _signup_action(os.environ.get("SIGNUP_URL", ""))

SYMBOLS = {"USD": "$", "GBP": "\u00a3", "EUR": "\u20ac", "CAD": "CA$",
           "AUD": "A$", "INR": "\u20b9"}


# ---------------------------------------------------------------- helpers

def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def money(currency: str, value, dash: str = "\u2014") -> str:
    """Format a price. Never raises.

    An archive accumulated over years will contain records written by older
    versions of this code and rows repaired by hand. A formatting helper that
    throws on unexpected input takes the whole site build down with it, so
    anything unusable falls back to a dash.
    """
    if value is None or value == "":
        return dash
    try:
        n = f"{float(value):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return esc(value)
    sym = SYMBOLS.get(str(currency or "").upper(), "")
    if sym:
        return f"{sym}{n}"
    # An unrecognised currency is printed beside the number, and it came from
    # an AI reading a third-party pricing page -- untrusted input that lands
    # next to every price on the site. extract.normalise already caps it at
    # three characters, but this function is also called on records repaired
    # by hand and written by older versions of the code, so it cannot assume
    # that ran. ISO 4217 codes are three letters, so anything else is bad data
    # regardless of intent.
    code = re.sub(r"[^A-Za-z]", "", str(currency or ""))[:3].upper()
    return f"{n} {esc(code)}" if code else n


def mixed_currency_note(bench: dict) -> str:
    """One short line, shown only when a category holds more than one currency.

    Someone scanning a price column assumes the numbers are comparable. When
    they are not, saying so is the whole job -- silence here is the difference
    between a reference and a trap.
    """
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
        return "\u2014"
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except ValueError:
        return iso[:10]


# Acronyms that .title() mangles into "Crm", "Seo", "Api".
ACRONYMS = {"crm": "CRM", "seo": "SEO", "api": "API", "hr": "HR",
            "erp": "ERP", "crm-tools": "CRM", "bi": "BI", "it": "IT",
            "saas": "SaaS", "ai": "AI"}


def title_case(slug: str) -> str:
    if slug.lower() in ACRONYMS:
        return ACRONYMS[slug.lower()]
    return " ".join(ACRONYMS.get(w.lower(), w.title())
                    for w in slug.replace("-", " ").split())


def diff_html(old, new, currency: str = "", pct: str = "") -> str:
    """The signature element. Every change on the site renders through here.

    Direction is carried by a glyph AND a sign AND colour, never colour alone,
    so it still reads correctly in greyscale or with colourblindness.
    """
    try:
        rising = float(new) > float(old)
        direction = "up" if rising else "down"
        glyph = "\u25b2" if rising else "\u25bc"
    except (TypeError, ValueError):
        direction, glyph = "", ""

    was = money(currency, old) if currency else esc(old)
    now = money(currency, new) if currency else esc(new)
    badge = f'<span class="pct">{glyph} {esc(pct)}</span>' if pct else ""
    return (f'<span class="diff {direction}">'
            f'<span class="was">{was}</span>'
            f'<span class="arrow">\u2192</span>'
            f'<span class="now">{now}</span>{badge}</span>')


def sparkline(points: list[float], width: int = 260, height: int = 56) -> str:
    """Small inline price history chart.

    Kept deliberately quiet. The diff is the signature; this supports it.
    """
    if len(points) < 2:
        return ""
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1
    step = width / (len(points) - 1)
    coords = [
        (i * step, height - ((v - lo) / span) * (height - 8) - 4)
        for i, v in enumerate(points)
    ]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    lx, ly = coords[-1]
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" role="img" '
        f'aria-label="Price history: {money("", points[0])} to '
        f'{money("", points[-1])}">'
        f'<path class="line" d="{path}"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3"/></svg>'
    )


def price_series(changes: list[dict], vendor: str, plan: str,
                 current: float | None) -> list[float]:
    """Rebuild a plan's price history by walking the change log backwards.

    The append-only log means history is derivable rather than stored twice.
    """
    relevant = [
        c for c in changes
        if c["vendor"] == vendor and c.get("plan") == plan
        and c.get("field") == "monthly_price"
        and isinstance(c.get("old_value"), (int, float))
        and isinstance(c.get("new_value"), (int, float))
    ]
    relevant.sort(key=lambda c: c.get("detected_at", ""))
    if not relevant:
        return []
    series = [float(relevant[0]["old_value"])]
    series += [float(c["new_value"]) for c in relevant]
    if current is not None and series[-1] != current:
        series.append(float(current))
    return series


# ---------------------------------------------------------------- shell

DEMO_BANNER = """
<div style="background:#B4531A;color:#fff;padding:0.7rem 0;font-family:
  'IBM Plex Mono',monospace;font-size:0.78rem;text-align:center;
  letter-spacing:0.02em">
  <strong>SAMPLE DATA \u2014 NOT REAL PRICES.</strong>
  Every figure here was randomly generated. Do not publish this site.
</div>"""

# Set by build() when any record is demo data.
_IS_DEMO = False


def json_ld(data: dict) -> str:
    """Structured data block.

    The whole site is machine-readable facts -- plans, prices, dates -- so
    telling search engines that explicitly is the single biggest SEO win
    available. Without it Google has to guess from the HTML.

    The escaping matters. JSON encoding does not touch "</script>", so a plan
    name containing one would close this tag early and everything after it
    would be parsed as HTML. Plan names come from an AI reading third-party
    pages, so that input is not ours to trust.
    """
    payload = json.dumps(data, ensure_ascii=False)
    payload = (payload.replace("<", "\\u003c")
                      .replace(">", "\\u003e")
                      .replace("&", "\\u0026"))
    return f'<script type="application/ld+json">{payload}</script>'


def vendor_schema(name: str, record: dict, url: str) -> str:
    offers = []
    for plan in record.get("plans", []):
        if plan.get("is_addon") or plan.get("monthly_price") is None:
            continue
        offers.append({
            "@type": "Offer",
            "name": plan["name"],
            "price": plan["monthly_price"],
            "priceCurrency": record.get("currency", "USD"),
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
            "priceCurrency": record.get("currency", "USD"),
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
    })


def dataset_schema(vendors: int, changes: int, since: str) -> str:
    """Declares the archive as a Dataset. This is what makes the site
    discoverable to people looking for pricing data, not just pricing."""
    return json_ld({
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"{SITE_NAME} SaaS pricing archive",
        "description": (f"Daily structured records of published pricing for "
                        f"{vendors} B2B software vendors, recorded since "
                        f"{since}. {changes} changes logged."),
        "url": BASE_URL,
        "creator": {"@type": "Organization", "name": SITE_NAME},
        "temporalCoverage": f"{since}/..",
        "isAccessibleForFree": True,
    })


def page(title: str, description: str, body: str, path: str,
         extra_head: str = "") -> str:
    canonical = f"{BASE_URL}/{path}".replace("/index.html", "/")
    banner = DEMO_BANNER if _IS_DEMO else ""
    noindex = ('<meta name="robots" content="noindex,nofollow">'
               if _IS_DEMO else "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{noindex}
<title>{'[SAMPLE DATA] ' if _IS_DEMO else ''}{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:site_name" content="{SITE_NAME}">
<link rel="alternate" type="application/rss+xml" title="{SITE_NAME} changes"
      href="{BASE_URL}/feed.xml">
{FONT_LINK}
<link rel="stylesheet" href="{_rel(path)}assets/style.css">{f'<script defer src="{_rel(path)}assets/find.js"></script>' if path == "index.html" else ""}
{extra_head}
</head>
<body>
{banner}
<header class="masthead"><div class="wrap">
  <a class="wordmark" href="{_rel(path)}index.html">Price<span>Trail</span></a>
  <nav>
    <a href="{_rel(path)}index.html">All prices</a>
    <a href="{_rel(path)}week.html">This week</a>
    <a href="{_rel(path)}changes.html">Changes</a>
    <a href="{_rel(path)}about.html">Method</a>
    <a href="{_rel(path)}status.html">Status</a>
    <a href="{BASE_URL}/feed.xml">RSS</a>
  </nav>
</div></header>
<main>{body}</main>
<footer><div class="wrap">
  <p class="footer-links"><a href="{_rel(path)}all.html">Every page</a> &middot; <a href="{_rel(path)}about.html">Method</a> &middot; <a href="{_rel(path)}bot.html">About the crawler</a></p>
  <p class="disclaimer">Figures are recorded from vendors' own public pricing
    pages and may lag a change by up to 24 hours. Every price is shown in the
    currency that vendor's own page displayed &mdash; nothing here is converted
    between currencies. Always confirm with the vendor before making a
    decision.</p>
  <p>{SITE_NAME}<br>
     <a href="{_rel(path)}about.html">How this is collected</a><br>
     <a href="{_rel(path)}bot.html">About the crawler</a></p>
</div></footer>
</body>
</html>
"""


def subscribe_block(prefix: str = "", compact: bool = False) -> str:
    """The one place a reader can turn into an audience.

    Until this existed, someone could find a page useful and leave with no way
    to hear from you again -- and traffic that cannot be reached again is not
    worth anything. A link that says "Subscribe" is not a conversion point
    either: it is one more click between wanting the thing and having it, on a
    page where most people never scroll at all.

    So this is a real form with one field. Conversion research is consistent
    that fewer fields win and that one to three is the range where they still
    perform; an email address alone is the floor.

    SIGNUP_URL is the form's action -- whatever mailing service you use gives
    you one. Until it is set, there is no form, because a form that silently
    throws addresses away is worse than none: people would believe they had
    subscribed. In that case the RSS feed is offered honestly instead.
    """
    if not SIGNUP_URL:
        # The compact strip exists to put a signup high on the page. With no
        # mailing service there is nothing to sign up to, and falling back to
        # the full panel here printed "Follow the changes" twice on the same
        # homepage -- once near the top and again at the bottom.
        if compact:
            return ""
        return f"""
<section class="section"><div class="panel">
  <div class="section-head" style="border-bottom-width:1px">
    <h2>Follow the changes</h2></div>
  <p style="max-width:52ch;margin-bottom:0.75rem">Every recorded change is
    published to a feed you can subscribe to in any reader.</p>
  <p><a href="{BASE_URL}/feed.xml">RSS feed</a> &middot;
     <a href="{prefix}changes.html">Browse all changes</a></p>
</div></section>"""

    # target="_blank" so the reader is never navigated away from the page they
    # came to read -- the mailing service confirms in its own tab.
    form = f"""
  <form class="signup" action="{esc(SIGNUP_URL)}" method="post"
        target="_blank" rel="noopener">
    <label class="vh" for="su-email{'-c' if compact else ''}">Email address</label>
    <input id="su-email{'-c' if compact else ''}" type="email" name="email"
           required autocomplete="email" placeholder="you@company.com">
    <button type="submit">Email me price changes</button>
  </form>
  <p class="signup-note">One email a week, only when something actually
    changed. Unsubscribe in one click. Your address is never sold or shared.</p>"""

    if compact:
        return f"""
<section class="section" style="padding-top:0">
  <div class="cta-strip">
    <div><strong>Get told when a price moves.</strong>
      <span>24 tools, checked daily. We email only when something changed.</span>
    </div>{form}
  </div>
</section>"""

    return f"""
<section class="section"><div class="panel cta-panel">
  <div class="section-head" style="border-bottom-width:1px">
    <h2>Never be surprised by a price rise</h2></div>
  <p style="max-width:54ch;margin-bottom:1.1rem">We read all 24 pricing pages
    every day. When one of them changes, you get an email the same week
    &mdash; with the old figure, the new one, and the date.</p>
  {form}
</div></section>"""


def back_link(prefix: str = "", label: str = "All prices") -> str:
    """A visible way home on every inner page.

    The logo in the masthead is a link, but nobody should have to know that
    convention to escape a page. This is the fix for "I clicked something and
    couldn't get back".
    """
    return (f'<p class="backlink"><a href="{prefix}index.html">'
            f'\u2190 {esc(label)}</a></p>')


def breadcrumb(prefix: str, trail: list[tuple[str, str | None]]) -> str:
    """A real trail home, plus the schema that tells Google the hierarchy.

    Every inner page used to carry a single flat link back to the homepage,
    which left the category pages -- the hubs the whole site is organised
    around -- with exactly ONE inbound link each. Google's crawl priority
    follows links, so the pages meant to be most important were being treated
    as the least. Routing every vendor and comparison page through its
    category turns that one link into a dozen.

    The JSON-LD is the same trail in the form Google reads for the breadcrumb
    line under a search result, which also makes the listing wider and more
    obviously relevant.
    """
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
    sep = ' <span class="crumb-sep">/</span> '
    return (f'<nav class="crumbs" aria-label="Breadcrumb">{sep.join(links)}</nav>'
            f'<script type="application/ld+json">{schema}</script>')


def _rel(path: str) -> str:
    """Relative prefix back to site root, so the site works from a file:// URL
    and from a subfolder on GitHub Pages without changes."""
    return "../" * path.count("/")


# ---------------------------------------------------------------- pages

def render_index(ctx: dict) -> str:
    """The homepage.

    Rebuilt around PRICES, not changes. The first version led with a change
    feed, which on a new archive is empty -- so the main content area said
    "nothing here" while the actual pricing sat two clicks away. The data that
    exists should be the first thing you see; the change feed earns its place
    at the top only once it has something in it.
    """
    changes, vendors = ctx["changes"], ctx["vendors"]
    records, by_cat = ctx["records"], ctx["by_category"]
    tracked = len(records)

    body = [f"""
<div class="wrap">
  <section class="hero">
    <h1>What software actually costs.</h1>
    <p class="standfirst">Every published price from {tracked} B2B software
      companies, read fresh every day and written down permanently. Nobody can
      sell you this history \u2014 the only way to have it is to have been
      recording all along.</p>
    <ul class="whatis">
      <li><strong>Look up</strong> what any tracked tool charges right now</li>
      <li><strong>Compare</strong> two of them side by side</li>
      <li><strong>See what changed</strong>, with both figures and the date</li>
    </ul>
    <div class="counters">
      <div class="counter"><span class="n">{tracked}</span>
        <span class="l">Vendors</span></div>
      <div class="counter"><span class="n">{sum(len(_real_plans(r)) for r in records.values())}</span>
        <span class="l">Plans priced</span></div>
      <div class="counter"><span class="n">{len(changes)}</span>
        <span class="l">Changes logged</span></div>
      <div class="counter"><span class="n">{esc(ctx['tracking_since'])}</span>
        <span class="l">Recording since</span></div>
    </div>
  </section>
"""]

    # 57% of desktop visitors and 64% on mobile never scroll past the first
    # screen. An offer that only appears at the bottom of the page is an offer
    # most people never see, so the compact strip goes directly under the hero
    # -- close enough to the proof figures to borrow their credibility.
    body.append(subscribe_block(compact=True))

    # ---- the main event: every price, on one screen ----
    if changes:
        body.append('<section class="section"><div class="section-head">'
                    '<h2>Latest changes</h2>'
                    f'<span class="aside">{len(changes)} recorded \u00b7 '
                    f'<a href="changes.html">see all</a></span></div>')
        body.append(_tape(changes[:12], vendors, prefix=""))
        body.append("</section>")
    else:
        body.append(f"""
<section class="section"><div class="section-head">
  <h2>Price changes</h2></div>
  <p class="note">Nothing has moved since recording began on
  {esc(ctx['tracking_since'])}. Software pricing changes a few times a year,
  not weekly, so quiet stretches are normal \u2014 and knowing a category is
  stable is worth something on its own. Every change from here is logged with
  both figures and the date it moved.</p>
</section>""")

    body.append('<section class="section" id="prices">'
                '<div class="findbar">'
                '<input id="find" type="search" hidden '
                'placeholder="Find a tool \u2014 type a name" '
                'aria-label="Find a tool by name">'
                '<span class="find-count" id="find-count"></span>'
                '</div>'
                '<p class="find-empty" id="find-empty" hidden></p>'
                '<div class="section-head"><h2>Every tracked price</h2>'
                '<span class="aside">entry price = cheapest paid plan with a '
                'published figure</span></div>')

    for cat in sorted(by_cat):
        live = sorted(n for n in by_cat[cat] if storage.slugify(n) in records)
        if not live:
            continue
        bench = ctx["benchmarks"].get(cat, {})
        cur = bench.get("currency", "USD")

        body.append(f"""
  <div class="cat-block" data-block>
    <div class="cat-head">
      <h3><a href="c/{esc(storage.slugify(cat))}.html">{esc(title_case(cat))}</a></h3>
      <span class="cat-meta">{len(live)} vendors &middot; median entry
        <strong>{esc(money(cur, bench.get('median_entry')))}</strong>
        {mixed_currency_note(bench)}</span>
    </div>
    <div class="tbl-scroll"><table class="stack">
      <thead><tr>
        <th data-sort="text">Vendor</th><th class="num">Entry</th>
        <th class="num">Top listed</th><th data-sort="off">Free tier</th>
        <th data-sort="off">Billing</th>
      </tr></thead><tbody>""")

        for name in live:
            rec = records[storage.slugify(name)]
            plans = _real_plans(rec)
            entry, top, basis = headline_prices(rec)
            rcur = rec.get("currency", cur)
            note = ('<span class="basis">billed annually</span>'
                    if basis == "annual" else "")
            if entry is None:
                enterprise = any(p.get("is_custom_pricing") for p in plans)
                entry_cell = ('<span class="tag">Enterprise only</span>'
                              if enterprise else "\u2014")
                top_cell = "\u2014"
            else:
                entry_cell = money(rcur, entry) + note
                top_cell = money(rcur, top)
            body.append(f"""
        <tr>
          <td data-l="Vendor"><a class="vlink"
            href="v/{esc(storage.slugify(name))}.html">{esc(name)}</a></td>
          <td class="num big" data-l="Entry">{entry_cell}</td>
          <td class="num" data-l="Top listed">{top_cell}</td>
          <td data-l="Free tier">{'Yes' if any(p['is_free'] for p in plans) else 'No'}</td>
          <td data-l="Billing">{'Per seat' if any(p['is_per_seat'] for p in plans) else 'Flat'}</td>
        </tr>""")
        body.append("</tbody></table></div></div>")
    body.append("</section>")

    # ---- changes: prominent only when there is something to show ----
    body.append(subscribe_block())
    body.append("</div>")
    return page(f"{SITE_NAME} \u2014 {TAGLINE}",
                f"Current and historical pricing for {tracked} B2B software "
                "vendors. Entry prices, plan comparisons and every recorded "
                "change.",
                "".join(body), "index.html",
                extra_head=site_schema() + dataset_schema(
                    tracked, len(changes), ctx["tracking_since"]))


def render_changes(ctx: dict) -> str:
    changes, vendors = ctx["changes"], ctx["vendors"]
    body = ['<div class="wrap"><section class="section">'
            + back_link() +
            '<div class="section-head"><h1>Every recorded change</h1>'
            f'<span class="aside">{len(changes)} entries</span></div>',
            _tape(changes, vendors, prefix=""),
            "</section></div>"]
    return page(f"All pricing changes \u2014 {SITE_NAME}",
                "Complete log of recorded B2B software pricing changes.",
                "".join(body), "changes.html")


def _vendor_summary(name: str, record: dict, ctx: dict, category: str) -> str:
    """What this vendor charges, written out.

    A vendor page was a price table and little else -- around 80 words of
    actual text, thinner than the comparison pages, on the pages that carry
    the queries this site actually gets ("intercom pricing"). A table is data;
    Google reads text. Every sentence below is arithmetic on figures already
    in the archive, so nothing new is collected and nothing is invented.
    """
    cur = (record.get("currency") or "USD").upper()
    plans = _real_plans(record)
    if not plans:
        return ""
    entry, top, _ = headline_prices(record)
    free = [p for p in plans if p["is_free"]]
    custom = [p for p in plans if p["is_custom_pricing"]]
    seat = [p for p in plans if p.get("is_per_seat")]
    addons = [p for p in record["plans"] if p.get("is_addon")]
    trials = [p.get("trial_days") for p in plans
              if isinstance(p.get("trial_days"), int) and p["trial_days"] > 0]
    changes = [c for c in ctx["changes"] if c["vendor"] == name]

    out = []
    paid = len(plans) - len(free)
    if entry:
        line = (f"{esc(name)} starts at {esc(money(cur, entry))} a month on its "
                f"cheapest paid plan")
        if seat:
            line += ", per seat"
        if top and top != entry:
            line += f", rising to {esc(money(cur, top))} on the highest plan it publishes"
        out.append(line + ".")

    # Where it sits in its own category -- the comparison a buyer is making
    # anyway, and something no single vendor's own page can tell them.
    bench = ctx.get("benchmarks", {}).get(category) or {}
    med = bench.get("median_entry")
    if entry and med and bench.get("currency") == cur and bench.get("n", 0) > 2:
        if entry < med * 0.85:
            out.append(f"That is below the {esc(title_case(category))} median of "
                       f"{esc(money(cur, med))}, so it is one of the cheaper ways "
                       f"into this category.")
        elif entry > med * 1.15:
            out.append(f"That is above the {esc(title_case(category))} median of "
                       f"{esc(money(cur, med))} \u2014 it is priced at the "
                       f"expensive end of its category.")
        else:
            out.append(f"That is close to the {esc(title_case(category))} median "
                       f"of {esc(money(cur, med))}.")

    # Plan names are the single most vendor-specific thing on the page: no two
    # companies name their tiers the same way. Naming them lowers how much
    # this paragraph reads like every other one, and it is also what somebody
    # comparing quotes actually needs to match up.
    tiers = [pl["name"] for pl in plans if str(pl.get("name") or "").strip()]
    if len(tiers) > 1:
        listed = ", ".join(esc(t) for t in tiers[:-1]) + f" and {esc(tiers[-1])}"
        out.append(f"The plans are called {listed}.")
    elif tiers:
        out.append(f"There is one published plan, {esc(tiers[0])}.")

    # A plan literally named "Free" while nothing is flagged as free means the
    # two facts disagree. Rather than pick one and risk printing a confident
    # contradiction two lines below the plan list, say nothing about free
    # tiers at all. Silence is recoverable; being visibly wrong is not.
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
        out.append(bits[0][0].upper() + bits[0][1:] + "." if len(bits) == 1
                   else bits[0][0].upper() + bits[0][1:] + ", and " +
                   ", ".join(bits[1:]) + ".")

    # The bit nobody else can tell them.
    since = ctx.get("tracking_since", "")
    if changes:
        ups = sum(1 for c in changes if c["change_type"] == "price_increase")
        downs = sum(1 for c in changes if c["change_type"] == "price_decrease")
        parts = []
        if ups:
            parts.append(f"{ups} rise{'s' if ups != 1 else ''}")
        if downs:
            parts.append(f"{downs} cut{'s' if downs != 1 else ''}")
        latest = max(c.get("detected_at", "") for c in changes)[:10]
        out.append(f"Since {esc(since)} we have recorded "
                   f"{esc(' and '.join(parts) or str(len(changes)) + ' changes')}"
                   f" here, the most recent on {esc(pretty_date(latest))}.")
    else:
        out.append(f"Not one of these {len(plans)} figures has changed since "
                   f"{esc(since)}, when {esc(name)} was first read. A price "
                   f"that has held is worth knowing before you commit to a "
                   f"year of it.")

    body = "".join(f'<p style="max-width:60ch;margin-bottom:0.7rem">{ln}</p>'
                   for ln in out)
    return (f'<div class="panel" style="margin-bottom:1.5rem">'
            f'<div class="section-head" style="border-bottom-width:1px">'
            f'<h2>What {esc(name)} costs</h2>'
            f'<span class="aside">{paid} paid plan'
            f'{"s" if paid != 1 else ""}</span></div>{body}</div>')


def render_vendor(slug: str, name: str, ctx: dict) -> str:
    record = ctx["records"][slug]
    cur = record.get("currency", "USD")
    mine = [c for c in ctx["changes"] if c["vendor"] == name]
    category = ctx["vendor_category"].get(name, "")
    versions = ctx["versions"].get(slug, 0)

    rows = []
    addon_rows = []
    addon_names: list[str] = []
    for p in record["plans"]:
        if p["is_custom_pricing"]:
            monthly = '<span class="tag">Contact sales</span>'
        elif p["is_free"]:
            monthly = '<span class="tag">Free</span>'
        else:
            monthly = money(cur, p["monthly_price"])
        limits = ", ".join(
            f'{int(l["value"]) if l["value"] and l["value"] >= 0 else "Unlimited"} '
            f'{esc(l["metric"])}'.strip()
            for l in p.get("limits", [])[:3]
        ) or "\u2014"
        row = f"""
      <tr>
        <td class="plan-name">{esc(p['name'])}</td>
        <td class="num" data-l="Monthly">{monthly}</td>
        <td class="num" data-l="Annual, per month">{money(cur, p['annual_price_per_month'])}</td>
        <td data-l="Billing">{'Per seat' if p['is_per_seat'] else 'Flat'}</td>
        <td data-l="Stated limits">{limits}</td>
      </tr>"""
        # Add-ons are sold on top of a plan, not instead of one. Listing them
        # in the same table made "Surveys" look like a tier sitting between
        # Pro and Enterprise -- which misreads the vendor's own pricing, and
        # buries the answer for anyone who came looking specifically for what
        # an add-on costs.
        #
        # Names are kept as raw data here rather than read back out of the
        # rendered row. Doing the latter escaped them twice ("A & B" became
        # "A &amp;amp; B") and crashed outright on a plan with an empty name,
        # because the regex needed at least one character to match.
        if p.get("is_addon"):
            addon_rows.append(row)
            if str(p.get("name") or "").strip():
                addon_names.append(p["name"].strip())
        else:
            rows.append(row)

    # Sparkline for whichever paid plan has the most recorded history. Falls
    # back to the cheapest, which is the number people compare on.
    paid = [p for p in record["plans"]
            if p["monthly_price"] and not p["is_custom_pricing"]]
    spark_block = ""
    if paid:
        scored = [
            (p, price_series(ctx["changes"], name, p["name"], p["monthly_price"]))
            for p in paid
        ]
        target, series = max(
            scored, key=lambda s: (len(s[1]), -(s[0]["monthly_price"] or 0))
        )
        if series:
            delta = ((series[-1] - series[0]) / series[0] * 100) if series[0] else 0
            spark_block = f"""
    <div class="panel" style="margin-top:1.5rem">
      <div class="section-head" style="border-bottom-width:1px">
        <h2>{esc(target['name'])} plan over time</h2>
        <span class="aside">{money(cur, series[0])} \u2192
          {money(cur, series[-1])} ({delta:+.0f}%)</span>
      </div>
      {sparkline(series)}
    </div>"""

    summary = _vendor_summary(name, record, ctx, category)
    addon_block = ""
    if addon_rows:
        listed = (f" {esc(name)} lists {esc(', '.join(addon_names[:6]))}."
                  if addon_names else "")
        addon_block = f"""
    <div class="panel" style="margin-top:1.5rem">
      <div class="section-head" style="border-bottom-width:1px">
        <h2>{esc(name)} add-ons</h2>
        <span class="aside">sold on top of a plan</span></div>
      <p style="max-width:62ch;margin-bottom:1rem">These are priced separately
        from the plans above, so they are an extra cost rather than an
        alternative to them.{listed}</p>
      <div class="tbl-scroll"><table class="stack">
        <thead><tr><th>Add-on</th><th class="num">Monthly</th>
          <th class="num">Annual, per month</th><th>Billing</th>
          <th>Stated limits</th></tr></thead>
        <tbody>{''.join(addon_rows)}</tbody>
      </table></div>
    </div>"""

    body = [f"""
<div class="wrap">
  <section class="section">
    {breadcrumb("../", [("All prices", "index.html"),
                        (title_case(category), f"c/{storage.slugify(category)}.html"),
                        (name, None)])}
    <div class="section-head"><h1>{esc(name)} pricing</h1>
      <span class="aside">{esc(title_case(category))}</span></div>
    {summary}
    <div class="tbl-scroll"><table class="stack">
      <thead><tr><th>Plan</th><th class="num">Monthly</th>
        <th class="num">Annual, per month</th><th>Billing</th>
        <th>Stated limits</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table></div>
    {addon_block}
    {spark_block}
    <p class="provenance" style="margin-top:1.5rem">
      Last read {esc(pretty_date(record.get('captured_at')))}.<br>
      {versions} recorded version{'s' if versions != 1 else ''} of this page.<br>
      {len(mine)} change{'s' if len(mine) != 1 else ''} logged.
    </p>
  </section>"""]

    if mine:
        body.append('<section class="section"><div class="section-head">'
                    f'<h2>What has changed at {esc(name)}</h2></div>')
        body.append(_tape(mine, ctx["vendors"], prefix="../", show_vendor=False))
        body.append("</section>")

    siblings = [n for n in ctx["by_category"].get(category, [])
                if n != name and storage.slugify(n) in ctx["records"]
                and comparison_is_worth_a_page(*sorted((name, n)), ctx)]
    if siblings:
        links = " \u00b7 ".join(
            f'<a href="../compare/{esc(_pair_slug(name, s))}.html">'
            f'{esc(name)} vs {esc(s)}</a>' for s in siblings[:8]
        )
        body.append(f'<section class="section"><div class="section-head">'
                    f'<h2>Compare</h2></div><p>{links}</p></section>')

    body.append(subscribe_block(prefix="../"))
    body.append("</div>")
    entry = min((p["monthly_price"] for p in paid), default=None)

    # "intercom surveys pricing" and "intercom product tour pricing" were
    # bringing up this page in search, and neither the title nor the snippet
    # gave any sign the add-on was covered -- so the result did not look like
    # the answer even when it was. Naming the add-ons is what makes it look
    # like one.
    if addon_rows:
        title = f"{name} pricing \u2014 plans, add-ons and price history"
        named = ", ".join(addon_names[:3])
        description = (
            f"{name} pricing: every plan, what the add-ons cost ({named}), "
            f"and every change recorded since {ctx['tracking_since']}."
            if named else
            f"{name} pricing: every plan, what the add-ons cost, and every "
            f"change recorded since {ctx['tracking_since']}.")
    else:
        title = f"{name} pricing \u2014 current plans and history"
        description = (
            f"{name} pricing: current plans, historical prices and every "
            f"recorded change. Entry plan {money(cur, entry)}." if entry else
            f"{name} pricing: current plans and every recorded change.")

    return page(
        title, description,
        "".join(body), f"v/{slug}.html",
        extra_head=vendor_schema(name, record, f"{BASE_URL}/v/{slug}.html"))


def render_category(cat: str, ctx: dict) -> str:
    names = [n for n in ctx["by_category"][cat]
             if storage.slugify(n) in ctx["records"]]
    bench = ctx["benchmarks"].get(cat, {})
    cur = bench.get("currency", "USD")

    rows = []
    for name in sorted(names):
        rec = ctx["records"][storage.slugify(name)]
        entry, top, _basis = headline_prices(rec)
        free = any(p["is_free"] for p in rec["plans"])
        custom = any(p["is_custom_pricing"] for p in rec["plans"])
        n_changes = sum(1 for c in ctx["changes"] if c["vendor"] == name)
        rows.append(f"""
      <tr>
        <td class="plan-name"><a href="../v/{esc(storage.slugify(name))}.html">
          {esc(name)}</a></td>
        <td class="num" data-l="Entry">{money(rec.get('currency', cur), entry)}</td>
        <td class="num" data-l="Highest listed">{money(rec.get('currency', cur), top)}</td>
        <td data-l="Free tier">{'Yes' if free else 'No'}</td>
        <td data-l="Enterprise quote">{'Yes' if custom else 'No'}</td>
        <td class="num" data-l="Changes">{n_changes}</td>
      </tr>""")

    body = [f"""
<div class="wrap">
  <section class="section">
    {breadcrumb("../", [("All prices", "index.html"),
                        (title_case(cat), None)])}
    <div class="section-head"><h1>{esc(title_case(cat))} pricing compared</h1>
      <span class="aside">{len(names)} vendors</span></div>
    <div class="grid grid-3" style="margin-bottom:1.5rem">
      <div class="cell"><span class="stat">{esc(money(cur, bench.get('median_entry')))}</span>
        <p>Median entry price{mixed_currency_note(bench)}</p></div>
      <div class="cell"><span class="stat">{bench.get('pct_free', 0):.0f}%</span>
        <p>Offer a free tier</p></div>
      <div class="cell"><span class="stat">{bench.get('pct_per_seat', 0):.0f}%</span>
        <p>Charge per seat</p></div>
    </div>
    <div class="tbl-scroll"><table class="stack">
      <thead><tr><th>Vendor</th><th class="num">Entry</th>
        <th class="num">Highest listed</th><th>Free tier</th>
        <th>Enterprise quote</th><th class="num">Changes</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table></div>
    <p class="provenance" style="margin-top:1.5rem">
      Entry price is the cheapest paid plan with a published figure. Plans
      priced only on application are excluded from the median, which is why
      these figures sit below what enterprise buyers actually pay.
    </p>
  </section>
</div>"""]
    return page(
        f"{title_case(cat)} software pricing compared \u2014 {SITE_NAME}",
        f"Compare pricing across {len(names)} {title_case(cat).lower()} tools. "
        f"Median entry price {money(cur, bench.get('median_entry'))}.",
        "".join(body), f"c/{storage.slugify(cat)}.html")


def _differences(a: str, b: str, ra: dict, rb: dict,
                 ctx: dict) -> tuple[str, str]:
    """Prose describing how two vendors actually differ, plus a table.

    extract.py has been capturing free tiers, billing models, trial lengths,
    usage limits and a controlled feature list all along, and the comparison
    page displayed none of it -- just plan names and two price columns. That
    is why these pages read as templated: they were. Everything below comes
    from data already in the archive; nothing new is collected.
    """
    cur_a = (ra.get("currency") or "USD").upper()
    cur_b = (rb.get("currency") or "USD").upper()
    ea, ta, _ = headline_prices(ra)
    eb, tb, _ = headline_prices(rb)
    free_a = any(p["is_free"] for p in _real_plans(ra))
    free_b = any(p["is_free"] for p in _real_plans(rb))
    seat_a = any(p.get("is_per_seat") for p in _real_plans(ra))
    seat_b = any(p.get("is_per_seat") for p in _real_plans(rb))
    trial_a, trial_b = _trial_days(ra), _trial_days(rb)

    # ---- prose ----
    lines = []
    if ea and eb and cur_a == cur_b:
        dearer, cheaper = (a, b) if ea > eb else (b, a)
        hi, lo = max(ea, eb), min(ea, eb)
        gap = (hi - lo) / lo * 100
        if gap < 10:
            lines.append(f"{esc(a)} and {esc(b)} start within {gap:.0f}% of "
                         f"each other, so entry price is unlikely to be what "
                         f"decides between them.")
        elif gap < 100:
            # Only meaningful below 100%: nothing can be "150% cheaper".
            lines.append(f"{esc(cheaper)} is the cheaper way in, by about "
                         f"{gap:.0f}% on the entry plan.")
        else:
            lines.append(f"{esc(cheaper)} is the cheaper way in by a wide "
                         f"margin \u2014 {esc(dearer)} costs about "
                         f"{hi / lo:.1f}\u00d7 as much to start.")

    if free_a != free_b:
        has, hasnt = (a, b) if free_a else (b, a)
        lines.append(f"{esc(has)} publishes a free tier; {esc(hasnt)} does "
                     f"not, so trying {esc(hasnt)} means either a trial or a "
                     f"card.")
    elif free_a and free_b:
        lines.append("Both publish a free tier.")

    if seat_a != seat_b:
        per, flat = (a, b) if seat_a else (b, a)
        lines.append(f"{esc(per)} charges per seat and {esc(flat)} does not "
                     f"\u2014 the gap between them widens with every person "
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

    changes_a = sum(1 for c in ctx["changes"] if c["vendor"] == a)
    changes_b = sum(1 for c in ctx["changes"] if c["vendor"] == b)
    if changes_a or changes_b:
        if changes_a and not changes_b:
            lines.append(f"Since recording began {esc(a)} has moved its "
                         f"pricing {changes_a} time"
                         f"{'s' if changes_a != 1 else ''} and {esc(b)} has "
                         f"not moved at all.")
        elif changes_b and not changes_a:
            lines.append(f"Since recording began {esc(b)} has moved its "
                         f"pricing {changes_b} time"
                         f"{'s' if changes_b != 1 else ''} and {esc(a)} has "
                         f"not moved at all.")
        else:
            lines.append(f"Both have repriced since recording began "
                         f"\u2014 {esc(a)} {changes_a} time"
                         f"{'s' if changes_a != 1 else ''}, {esc(b)} "
                         f"{changes_b}.")

    prose = "".join(f'<p style="max-width:62ch;margin-bottom:0.6rem">{ln}</p>'
                    for ln in lines)

    # ---- table ----
    def row(label, va, vb):
        # data-l is what the stacked mobile layout prints as each value's
        # label. Without it these collapse into an unlabelled column of bare
        # numbers on a phone.
        return (f'<tr><td class="plan-name">{esc(label)}</td>'
                f'<td class="num" data-l="{esc(a)}">{va}</td>'
                f'<td class="num" data-l="{esc(b)}">{vb}</td></tr>')

    dash = "\u2014"
    table = "".join([
        row("Cheapest paid plan", money(cur_a, ea), money(cur_b, eb)),
        row("Highest listed plan", money(cur_a, ta), money(cur_b, tb)),
        row("Free tier", "Yes" if free_a else "No",
            "Yes" if free_b else "No"),
        row("Billing", "Per seat" if seat_a else "Flat",
            "Per seat" if seat_b else "Flat"),
        row("Free trial",
            f"{trial_a} days" if trial_a else dash,
            f"{trial_b} days" if trial_b else dash),
        row("Plans published", str(len(_real_plans(ra))),
            str(len(_real_plans(rb)))),
        row("Enterprise quote only",
            "Yes" if any(p["is_custom_pricing"] for p in _real_plans(ra)) else "No",
            "Yes" if any(p["is_custom_pricing"] for p in _real_plans(rb)) else "No"),
        row("Price changes recorded", str(changes_a), str(changes_b)),
    ])
    return prose, table


def render_compare(a: str, b: str, ctx: dict) -> str:
    ra, rb = ctx["records"][storage.slugify(a)], ctx["records"][storage.slugify(b)]
    cat_name = ctx["vendor_category"].get(a) or ctx["vendor_category"].get(b) or ""

    def col(rec):
        cur = rec.get("currency", "USD")
        out = []
        for p in rec["plans"]:
            price = ('<span class="tag">Contact sales</span>'
                     if p["is_custom_pricing"] else
                     '<span class="tag">Free</span>' if p["is_free"] else
                     money(cur, p["monthly_price"]))
            out.append(f'<tr><td class="plan-name">{esc(p["name"])}</td>'
                       f'<td class="num" data-l="Monthly">{price}</td>'
                       f'<td class="num" data-l="Annual">'
                       f'{money(cur, p["annual_price_per_month"])}</td>'
                       f'</tr>')
        return "".join(out)

    def entry(rec):
        low, _high, _basis = headline_prices(rec)
        return low

    ea, eb = entry(ra), entry(rb)
    cur_a = (ra.get("currency") or "USD").upper()
    cur_b = (rb.get("currency") or "USD").upper()

    # "X starts 1.4x cheaper" is a claim about two numbers. If those numbers
    # are in different currencies it is simply false, and it was being put in
    # the headline AND the page description AND the search snippet. Nothing is
    # converted here on purpose, so the honest answer is to decline the
    # comparison and say why.
    if ea and eb and cur_a != cur_b:
        verdict = (f"These two publish in different currencies "
                   f"({esc(cur_a)} and {esc(cur_b)}), so their entry prices "
                   f"are not directly comparable. Both figures are shown "
                   f"below exactly as each vendor lists them.")
    elif ea and eb:
        cheaper, ratio = (a, eb / ea) if ea < eb else (b, ea / eb)
        verdict = (f"{esc(cheaper)} starts {ratio:.1f}\u00d7 cheaper on its "
                   f"entry plan.")
    else:
        verdict = "One of these does not publish an entry price."

    prose, difftable = _differences(a, b, ra, rb, ctx)

    # Comparison pages had two inbound links each -- the two vendor pages --
    # and they are 54 of the 87 pages on the site. Crawl priority follows
    # links, so the largest group of pages was also the least reachable, which
    # is exactly the group Google never got round to. Linking each comparison
    # to the others that share a vendor is both the fix and the thing a reader
    # actually wants: someone weighing A against B usually wants A against C
    # too.
    related = [(x, y) for (x, y) in comparison_pairs(ctx)
               if (x, y) != (a, b) and (a in (x, y) or b in (x, y))]
    related_block = ""
    if related:
        links = " ".join(
            f'<a href="{_pair_slug(x, y)}.html">{esc(x)} vs {esc(y)}</a>'
            for x, y in related[:12])
        related_block = f"""
  <div class="all-group" style="margin-top:2rem">
    <h2>Other comparisons with {esc(a)} or {esc(b)}</h2>
    <p class="all-links">{links}</p>
  </div>"""

    body = f"""
<div class="wrap"><section class="section">
  {breadcrumb("../", [("All prices", "index.html"),
                      (title_case(cat_name), f"c/{storage.slugify(cat_name)}.html"),
                      (f"{a} vs {b}", None)])}
  <div class="section-head"><h1>{esc(a)} vs {esc(b)}</h1>
    <span class="aside">Entry: {esc(money(cur_a, ea))}
      vs {esc(money(cur_b, eb))}</span></div>
  <p class="standfirst" style="margin-bottom:1.5rem">{verdict}</p>

  <div class="panel" style="margin-bottom:1.5rem">
    <div class="section-head" style="border-bottom-width:1px">
      <h2>How they differ</h2></div>
    {prose}
    <div class="tbl-scroll" style="margin-top:1rem"><table class="stack">
      <thead><tr><th></th><th class="num">{esc(a)}</th>
        <th class="num">{esc(b)}</th></tr></thead>
      <tbody>{difftable}</tbody>
    </table></div>
  </div>

  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(18rem,1fr))">
    <div class="cell">
      <h3><a href="../v/{esc(storage.slugify(a))}.html">{esc(a)}</a></h3>
      <table class="stack" style="margin-top:0.75rem"><thead><tr><th>Plan</th>
        <th class="num">Monthly</th><th class="num">Annual</th></tr></thead>
        <tbody>{col(ra)}</tbody></table>
    </div>
    <div class="cell">
      <h3><a href="../v/{esc(storage.slugify(b))}.html">{esc(b)}</a></h3>
      <table class="stack" style="margin-top:0.75rem"><thead><tr><th>Plan</th>
        <th class="num">Monthly</th><th class="num">Annual</th></tr></thead>
        <tbody>{col(rb)}</tbody></table>
    </div>
  </div>
  <p class="provenance" style="margin-top:1.5rem">
    Both figures read from each vendor's public pricing page. Feature sets
    differ, so compare the plans, not only the numbers.</p>
  {related_block}
</section></div>"""
    return page(f"{a} vs {b} pricing compared \u2014 {SITE_NAME}",
                f"Side-by-side pricing for {a} and {b}. {verdict}",
                body, f"compare/{_pair_slug(a, b)}.html")


def render_digest(ctx: dict) -> str:
    """This week's changes as a page. Doubles as the body of the email
    newsletter once you have somewhere to send it."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = [c for c in ctx["changes"]
              if (c.get("detected_at") or "") >= cutoff.isoformat()]

    body = ['<div class="wrap"><section class="section">'
            + back_link() +
            '<div class="section-head"><h1>This week in software pricing</h1>'
            f'<span class="aside">{len(recent)} changes</span></div>']
    if recent:
        body.append(_tape(recent, ctx["vendors"], prefix=""))
    else:
        body.append('<p class="empty">Nothing moved this week. That is a real '
                    'finding, not a gap \u2014 most weeks are quiet, and '
                    'knowing a category is stable is worth something.</p>')
    body.append("</section>")
    body.append(subscribe_block())
    body.append("</div>")
    return page(f"This week in software pricing \u2014 {SITE_NAME}",
                f"{len(recent)} pricing changes recorded across tracked B2B "
                "software vendors in the last seven days.",
                "".join(body), "week.html")


def render_about(ctx: dict) -> str:
    body = f"""
<div class="wrap"><section class="section">
  {back_link()}
  <div class="section-head"><h1>How this is collected</h1></div>
  <div class="panel">
    <p style="max-width:62ch">Once a day an automated reader visits the public
    pricing page of every vendor listed here. It strips away navigation,
    banners and chat widgets, then compares what remains against the previous
    version. When the underlying prices have moved, the change is recorded with
    both figures and the date.</p>

    <p style="max-width:62ch;margin-top:1rem">Most page edits are not price
    changes. A rewritten heading or a new testimonial changes the page but not
    the cost, so a change is only recorded when the extracted price, plan or
    limit is different. Anything the reader is not confident about is held back
    for a person to check before it appears here.</p>

    <p style="max-width:62ch;margin-top:1rem"><strong>Currencies are never
    converted.</strong> Each price appears in the currency that vendor's own
    page displayed, and pages are always read as the same visitor \u2014 a
    United States one \u2014 so that today's figure and last month's figure are
    the same measurement. Converting would be worse than useless here:
    exchange rates move daily, so a plan sitting untouched at $49 would appear
    to change price every morning, and this site would report changes that
    never happened. Where a vendor quotes in something other than the rest of
    its category, it is left out of that category's median rather than
    averaged in.</p>

    <p style="max-width:62ch;margin-top:1rem"><strong>What this cannot tell
    you.</strong> Plans priced on application are recorded as such, with no
    figure, so enterprise pricing is largely invisible here. Prices can vary by
    region, and what you are shown in your own country may differ from what is
    recorded here. And a page read yesterday may have changed this morning.</p>

    <p class="provenance" style="margin-top:1.5rem">
      Currently tracking {len(ctx['records'])} vendors \u00b7
      {len(ctx['changes'])} changes recorded \u00b7
      Recording since {esc(ctx['tracking_since'])}
    </p>
  </div>
</section></div>"""
    return page(f"Method \u2014 {SITE_NAME}",
                "How PriceTrail collects and verifies software pricing data.",
                body, "about.html")


def render_all_pages(ctx: dict, pairs: list[tuple[str, str]]) -> str:
    """One page linking to every other page on the site.

    Google's crawl priority follows links, and 54 comparison pages were
    sitting on two inbound links each -- which is exactly the set that never
    got crawled. A single index gives every page another route in and gives
    the crawler one place to discover the whole site from, instead of walking
    it category by category.

    It is also the page a person wants when the search box has failed them and
    they just want to see everything there is.
    """
    cats = []
    for cat, names in sorted(ctx["by_category"].items()):
        live = sorted(n for n in names if storage.slugify(n) in ctx["records"])
        if not live:
            continue
        vend = " ".join(
            f'<a href="v/{storage.slugify(n)}.html">{esc(n)}</a>' for n in live)
        cats.append(
            f'<div class="all-group"><h2>'
            f'<a href="c/{storage.slugify(cat)}.html">{esc(title_case(cat))}</a>'
            f'</h2><p class="all-links">{vend}</p></div>')

    comp = " ".join(
        f'<a href="compare/{_pair_slug(a, b)}.html">{esc(a)} vs {esc(b)}</a>'
        for a, b in pairs)

    body = f"""
<div class="wrap"><section class="section">
  {breadcrumb("", [("All prices", "index.html"), ("Everything", None)])}
  <div class="section-head"><h1>Every page on this site</h1>
    <span class="aside">{len(ctx['records'])} vendors &middot;
      {len(pairs)} comparisons</span></div>
  <p style="max-width:58ch;margin-bottom:1.5rem">Every tracked vendor, every
    category and every side-by-side comparison, in one list.</p>
  {''.join(cats)}
  <div class="all-group"><h2>Side-by-side comparisons</h2>
    <p class="all-links">{comp}</p></div>
  <div class="all-group"><h2>About this site</h2>
    <p class="all-links">
      <a href="about.html">How this is collected</a>
      <a href="changes.html">Every recorded change</a>
      <a href="week.html">This week in software pricing</a>
      <a href="bot.html">About the crawler</a>
      <a href="status.html">System status</a></p></div>
</section></div>"""
    return page("Every page \u2014 " + SITE_NAME,
                f"A full index of all {len(ctx['records'])} tracked vendors, "
                f"every category and every price comparison on {SITE_NAME}.",
                body, "all.html")


def render_bot() -> str:
    body = f"""
<div class="wrap"><section class="section">
  {back_link()}
  <div class="section-head"><h1>About the crawler</h1></div>
  <div class="panel">
    <p style="max-width:62ch">Pages here are read by an automated crawler
    identifying itself as <code>PriceTrailBot</code>.</p>
    <ul class="provenance" style="margin-top:1rem">
      <li>It obeys robots.txt.</li>
      <li>It waits at least three seconds between requests to the same site.</li>
      <li>It backs off when a server asks it to slow down.</li>
      <li>It reads pricing pages only, once a day.</li>
      <li>It records facts \u2014 prices, plan names, dates. It does not copy
          page text or design.</li>
    </ul>
    <p style="max-width:62ch;margin-top:1rem">If you would rather we did not
    read your pricing page, say so and we will remove you the same day. No
    argument, no forms.</p>
  </div>
</section></div>"""
    return page(f"About the crawler \u2014 {SITE_NAME}",
                "How the PriceTrail crawler behaves, and how to opt out.",
                body, "bot.html")


# ---------------------------------------------------------------- fragments

def _tape(changes: list[dict], vendors: dict, prefix: str = "",
          show_vendor: bool = True) -> str:
    if not changes:
        return ('<p class="empty">No changes recorded yet. Run the crawler '
                'for a few days and they will appear here.</p>')
    out = ['<div class="tape">']
    for c in changes:
        slug = storage.slugify(c["vendor"])
        who = (f'<a class="who" href="{prefix}v/{esc(slug)}.html">'
               f'{esc(c["vendor"])}</a> ') if show_vendor else ""
        plan = f'<span class="plan">{esc(c["plan"])}</span> ' if c.get("plan") else ""
        out.append(f"""
  <div class="entry">
    <span class="when">{esc(pretty_date(c.get('detected_at')))}</span>
    <span class="what">{who}{plan}{_describe(c, vendors)}</span>
  </div>""")
    out.append("</div>")
    return "".join(out)


def _describe(c: dict, vendors: dict) -> str:
    t = c["change_type"]
    cur = vendors.get(c["vendor"], {}).get("currency", "USD")
    old, new, pct = c.get("old_value"), c.get("new_value"), c.get("note", "")

    if t in ("price_increase", "price_decrease"):
        period = "annual" if "annual" in (c.get("field") or "") else "monthly"
        return f"{period} " + diff_html(old, new, cur, pct)
    if t == "limit_changed":
        return f"{esc(c.get('field'))} limit " + diff_html(old, new, "", "")
    if t == "plan_added":
        return f"added a plan at {money(cur, new)}"
    if t == "plan_removed":
        return "removed this plan"
    if t == "pricing_hidden":
        return "took pricing off its public page"
    if t == "feature_added":
        return f"now includes \u201c{esc(new)}\u201d"
    if t == "feature_moved_out":
        return f"no longer includes \u201c{esc(old)}\u201d"
    if t == "plan_renamed":
        return f"renamed from \u201c{esc(old)}\u201d"
    if t == "currency_changed":
        # The type that exists specifically so a currency flip is never
        # reported as a price cut. Printing it as a bare "currency changed"
        # with no values gave a reader nothing to judge, which defeats the
        # point of recording it separately.
        return (f"now shows prices in {esc(new)} instead of {esc(old)} "
                f"\u2014 the amounts are not comparable")
    if t == "billing_model_changed":
        return f"billing changed from {esc(old)} to {esc(new)}"
    if t == "custom_pricing_changed":
        hidden = str(new).lower() in ("true", "1")
        return ("replaced its price with \u201ccontact sales\u201d" if hidden
                else "published a price where it previously said contact sales")
    if t == "price_availability_changed":
        gone = new in (None, "", "None")
        field = esc(c.get("field") or "price")
        return (f"stopped publishing a {field}" if gone
                else f"started publishing a {field} at {money(cur, new)}")
    if t == "pricing_published":
        return "put pricing back on its public page"
    # Anything added later still reads as English rather than a code name.
    return esc(t.replace("_", " "))


def _all_features(record: dict) -> set[str]:
    """Every feature this vendor names anywhere in its plan list."""
    out: set[str] = set()
    for plan in _real_plans(record):
        out.update(plan.get("features") or [])
    return out


def _trial_days(record: dict) -> int | None:
    days = [p.get("trial_days") for p in _real_plans(record)
            if isinstance(p.get("trial_days"), int) and p["trial_days"] > 0]
    return max(days) if days else None


def comparison_is_worth_a_page(a: str, b: str, ctx: dict) -> bool:
    """Should this pair get its own indexable page?

    Generating every possible pair produced 85 near-identical pages off one
    template, on a domain three months old. Google indexed four of them. That
    is the documented outcome for templated comparison pages with nothing
    unique on them -- and worse, the thin ones spend a crawl budget that the
    good pages then never get.

    Two conditions, both cheap to reason about:

      1. Both sides must publish an entry price. Without that the page renders
         "-- vs --" and is worth nothing to anybody.
      2. Either one of them is a name people actually search for, or the pair
         has recorded price history. The daily/weekly crawl tier already
         encodes which vendors are the big names -- reusing it beats inventing
         a second list to keep in sync.

    Condition 2 loosens on its own as the archive grows: a pair with logged
    changes qualifies no matter how obscure, because that history exists
    nowhere else. So the site starts narrow and widens as it earns the right
    to.
    """
    ra = ctx["records"].get(storage.slugify(a))
    rb = ctx["records"].get(storage.slugify(b))
    if not ra or not rb:
        return False

    if headline_prices(ra)[0] is None or headline_prices(rb)[0] is None:
        return False

    tiers = {ctx["vendors"].get(n, {}).get("crawl_tier", "weekly")
             for n in (a, b)}
    if "daily" in tiers:
        return True

    return any(c["vendor"] in (a, b) for c in ctx["changes"])


def comparison_pairs(ctx: dict) -> list[tuple[str, str]]:
    """Every same-category pair that earns a page, in stable order."""
    pairs = set()
    for names in ctx["by_category"].values():
        live = sorted(n for n in names
                      if storage.slugify(n) in ctx["records"])
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if comparison_is_worth_a_page(a, b, ctx):
                    pairs.add((a, b))
    return sorted(pairs)


def _pair_slug(a: str, b: str) -> str:
    return "-vs-".join(sorted([storage.slugify(a), storage.slugify(b)]))


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
<header class="masthead"><div class="wrap">
  <a class="wordmark" href="/index.html">Price<span>Trail</span></a>
  <nav><a href="/index.html">All prices</a>
       <a href="/changes.html">Changes</a></nav>
</div></header>
<main><div class="wrap"><section class="section">
  <p class="backlink"><a href="/index.html">&#8592; All prices</a></p>
  <div class="section-head"><h2>Change feed</h2>
    <span class="aside">RSS</span></div>
  <div class="note" style="margin-bottom:1.5rem">
    This page is a feed. Paste its address into any feed reader and every new
    pricing change appears there automatically \u2014 no signup, no email.
  </div>
  <div class="tape">
  <xsl:for-each select="rss/channel/item">
    <div class="entry">
      <span class="when"><xsl:value-of select="substring(pubDate, 1, 10)"/></span>
      <span class="what">
        <a class="who"><xsl:attribute name="href">
          <xsl:value-of select="link"/></xsl:attribute>
          <xsl:value-of select="title"/></a>
      </span>
    </div>
  </xsl:for-each>
  </div>
</section></div></main>
</body></html>
</xsl:template>
</xsl:stylesheet>
"""


def render_feed(ctx: dict) -> str:
    items = []
    for c in ctx["changes"][:50]:
        slug = storage.slugify(c["vendor"])
        title = f"{c['vendor']}: {_describe(c, ctx['vendors'])}"
        title = re.sub(r"<[^>]+>", "", title)
        items.append(f"""  <item>
    <title>{esc(title)}</title>
    <link>{BASE_URL}/v/{esc(slug)}.html</link>
    <guid isPermaLink="false">{esc(c.get('detected_at'))}-{esc(slug)}</guid>
    <pubDate>{esc(c.get('detected_at', ''))}</pubDate>
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type="text/xsl" href="{BASE_URL}/feed.xsl"?>
<rss version="2.0"><channel>
  <title>{SITE_NAME} \u2014 software pricing changes</title>
  <link>{BASE_URL}</link>
  <description>{TAGLINE}</description>
{chr(10).join(items)}
</channel></rss>
"""


def page_lastmod(ctx: dict) -> dict[str, str]:
    """When each page's underlying pricing data genuinely last changed.

    The old sitemap stamped EVERY page with today's date on EVERY build. The
    daily crawl rebuilds the whole site, so all ~90 URLs claimed to have been
    modified today, every single day, for months -- while almost none of them
    had actually changed.

    Google's own guidance is that lastmod must mark the last *significant*
    change and must not be the generation time, and that it ignores the value
    outright once it finds it unreliable. So the one signal telling Google
    which pages are worth re-crawling was noise, on a site whose entire
    problem is not being crawled.

    A page's real modification date is the last time the pricing behind it
    moved. No change logged for a vendor means its page has said the same
    thing since recording began, and saying so honestly is what makes the
    dates worth reading.
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

    # Pages that genuinely change whenever anything anywhere changes.
    for path in ("index.html", "changes.html", "week.html"):
        out[path] = newest_overall
    # Near-static pages: prose that only changes when the code does.
    for path in ("about.html", "bot.html", "status.html"):
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
        out[f"compare/{_pair_slug(a, b)}.html"] = max(for_vendor(a),
                                                      for_vendor(b))
    return out


def render_sitemap(paths: list[str], lastmod: dict[str, str] | None = None) -> str:
    lastmod = lastmod or {}
    fallback = storage.recording_since()

    def canonical(p: str) -> str:
        # Must match the canonical tag the page itself carries, or Google
        # crawls the URL listed here, reads the tag, finds it points somewhere
        # else, and files the page under "Alternative page with proper
        # canonical tag" -- a crawl spent on a page that was never going to be
        # indexed. Harmless on a big site; not on one that cannot get crawled
        # enough in the first place.
        return f"{BASE_URL}/{p}".replace("/index.html", "/")

    urls = "".join(
        f"  <url><loc>{canonical(p)}</loc>"
        f"<lastmod>{lastmod.get(p, fallback)}</lastmod></url>\n"
        for p in paths
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}</urlset>\n")


# ---------------------------------------------------------------- build

def plan_price(plan: dict) -> tuple[float | None, str]:
    """The headline price for a plan, and what it is based on.

    Many pricing pages default their monthly/annual toggle to ANNUAL, so the
    only figure in the HTML is the annual-equivalent monthly price. Reading
    only monthly_price made Zendesk, Freshdesk and Chatwoot look as though
    they published no prices at all, when they were on the page in plain
    sight. Falling back to the annual figure -- and saying so -- is honest and
    fills a third of the table that was empty.
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
    """Subscription tiers only. Add-ons are priced per-use and would drag a
    category's median entry price towards zero if counted as plans."""
    return [p for p in record.get("plans", []) if not p.get("is_addon")]


def _benchmarks(cat_names: list[str], records: dict) -> dict:
    """Category averages, computed in ONE currency only.

    The old version pooled every vendor's entry price into one list, took the
    median, and labelled it with whichever vendor happened to be read last.
    Mixing $30, £30 and E30 into a single median produces a number that is not
    a price in any currency, presented with a symbol picked essentially at
    random. A median is only meaningful over comparable figures.

    So: the dominant currency in the category wins, vendors quoted in anything
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
        if any(p["is_free"] for p in plans):
            free += 1
        if any(p["is_per_seat"] for p in plans):
            seat += 1

    if by_currency:
        # Most vendors wins; ties break alphabetically so the build is
        # deterministic and the page doesn't flip between rebuilds.
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


def build(out_dir: Path | None = None) -> dict:
    """Generate the whole site. Returns a summary of what was written."""
    out = out_dir or (storage.ROOT / "site")
    for sub in ("", "v", "c", "compare", "assets"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    cfg = yaml.safe_load((storage.ROOT / "vendors.yaml").read_text("utf-8"))
    vendors = {v["name"]: v for v in cfg["vendors"]}
    vendor_category = {v["name"]: v.get("category", "uncategorised")
                       for v in cfg["vendors"]}
    by_category: dict[str, list[str]] = defaultdict(list)
    for name, cat in vendor_category.items():
        by_category[cat].append(name)

    # Only vendors with real extracted plans get pages.
    records = {}
    for path in storage.PLANS.glob("*.json"):
        try:
            rec = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            continue
        if rec.get("plans"):
            records[path.stem] = rec

    versions = {}
    for slug in records:
        folder = storage.SNAPSHOTS / slug
        versions[slug] = len(list(folder.glob("*.txt"))) if folder.exists() else 1

    # If any record is demo data, every page gets a warning banner and a
    # noindex tag. Publishing invented prices attached to real company names
    # would be false statements about real businesses.
    global _IS_DEMO
    _IS_DEMO = any(r.get("demo") for r in records.values())

    changes = storage.read_changes()
    # Not derived from the change log: an archive with no changes yet would
    # report today, and so reset on every rebuild.
    since = pretty_date(storage.recording_since())

    for name, rec in ((n, records.get(storage.slugify(n))) for n in vendors):
        if rec:
            vendors[name]["currency"] = rec.get("currency", "USD")

    ctx = {
        "records": records, "changes": changes, "vendors": vendors,
        "by_category": by_category, "vendor_category": vendor_category,
        "versions": versions, "tracking_since": since,
        "benchmarks": {cat: _benchmarks(names, records)
                       for cat, names in by_category.items()},
    }

    written: list[str] = []

    def write(path: str, content: str):
        (out / path).write_text(content, encoding="utf-8")
        written.append(path)

    (out / "assets" / "style.css").write_text(CSS + FILTER_CSS,
                                              encoding="utf-8")
    (out / "assets" / "find.js").write_text(FILTER_JS, encoding="utf-8")

    write("index.html", render_index(ctx))
    write("changes.html", render_changes(ctx))
    write("about.html", render_about(ctx))
    write("week.html", render_digest(ctx))
    from . import status as _status
    write("status.html", _status.render(esc, page, back_link, pretty_date,
                                        SITE_NAME))
    write("bot.html", render_bot())

    slug_to_name = {storage.slugify(n): n for n in vendors}
    for slug in sorted(records):
        write(f"v/{slug}.html",
              render_vendor(slug, slug_to_name.get(slug, title_case(slug)), ctx))

    for cat, names in sorted(by_category.items()):
        if any(storage.slugify(n) in records for n in names):
            write(f"c/{storage.slugify(cat)}.html", render_category(cat, ctx))

    # Comparison pages, but only for pairs that can actually say something.
    # See comparison_is_worth_a_page: thin templated pages don't just fail to
    # rank, they eat the crawl budget the good pages need.
    pairs = comparison_pairs(ctx)
    for a, b in pairs:
        write(f"compare/{_pair_slug(a, b)}.html", render_compare(a, b, ctx))

    write("all.html", render_all_pages(ctx, pairs))

    (out / "feed.xml").write_text(render_feed(ctx), encoding="utf-8")
    (out / "feed.xsl").write_text(
        FEED_STYLESHEET.replace("LINKS", FONT_LINK_XML +
            f'<link rel="stylesheet" href="{BASE_URL}/assets/style.css"/>'),
        encoding="utf-8")
    (out / "sitemap.xml").write_text(
        render_sitemap(written, page_lastmod(ctx)), encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n",
        encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    return {
        "pages": len(written), "vendors": len(records),
        "changes": len(changes), "comparisons": len(pairs), "out": out,
        "demo": _IS_DEMO,
    }
