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
SIGNUP_URL = os.environ.get("SIGNUP_URL", "")

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
    return f"{sym}{n}" if sym else f"{n} {currency or ''}".strip()


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
<link rel="alternate" type="application/rss+xml" title="{SITE_NAME} changes"
      href="{BASE_URL}/feed.xml">
{FONT_LINK}
<link rel="stylesheet" href="{_rel(path)}assets/style.css">
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
    <a href="{BASE_URL}/feed.xml">RSS</a>
  </nav>
</div></header>
<main>{body}</main>
<footer><div class="wrap">
  <p class="disclaimer">Figures are recorded from vendors' own public pricing
    pages and may lag a change by up to 24 hours. Always confirm with the
    vendor before making a decision.</p>
  <p>{SITE_NAME}<br>
     <a href="{_rel(path)}about.html">How this is collected</a><br>
     <a href="{_rel(path)}bot.html">About the crawler</a></p>
</div></footer>
</body>
</html>
"""


def subscribe_block(prefix: str = "") -> str:
    """Somewhere for an interested reader to go.

    Without this, someone finds a page useful and leaves with no way to hear
    from you again. That is the difference between traffic and an audience,
    and only one of them ever pays you.
    """
    if SIGNUP_URL:
        return f"""
<section class="section"><div class="panel">
  <div class="section-head" style="border-bottom-width:1px">
    <h2>Get the weekly change report</h2></div>
  <p style="max-width:52ch;margin-bottom:1rem">One email a week listing every
    price change we recorded. Free, and you can stop any time.</p>
  <p><a href="{esc(SIGNUP_URL)}" class="tag" style="font-size:0.85rem;
    padding:0.5rem 1rem;border-color:var(--ink)">Subscribe &rarr;</a></p>
</div></section>"""
    return f"""
<section class="section"><div class="panel">
  <div class="section-head" style="border-bottom-width:1px">
    <h2>Follow the changes</h2></div>
  <p style="max-width:52ch;margin-bottom:0.75rem">Every recorded change is
    published to a feed you can subscribe to in any reader.</p>
  <p><a href="{BASE_URL}/feed.xml">RSS feed</a> &middot;
     <a href="{prefix}changes.html">Browse all changes</a></p>
</div></section>"""


def back_link(prefix: str = "", label: str = "All prices") -> str:
    """A visible way home on every inner page.

    The logo in the masthead is a link, but nobody should have to know that
    convention to escape a page. This is the fix for "I clicked something and
    couldn't get back".
    """
    return (f'<p class="backlink"><a href="{prefix}index.html">'
            f'\u2190 {esc(label)}</a></p>')


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

    # ---- the main event: every price, on one screen ----
    body.append('<section class="section" id="prices">'
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
  <div class="cat-block">
    <div class="cat-head">
      <h3><a href="c/{esc(storage.slugify(cat))}.html">{esc(title_case(cat))}</a></h3>
      <span class="cat-meta">{len(live)} vendors &middot; median entry
        <strong>{esc(money(cur, bench.get('median_entry')))}</strong></span>
    </div>
    <div class="tbl-scroll"><table class="stack">
      <thead><tr>
        <th>Vendor</th><th class="num">Entry</th>
        <th class="num">Top listed</th><th>Free tier</th><th>Billing</th>
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
          <td data-l="Free tier">{'Yes' if any(p['is_free'] for p in plans) else '\u2014'}</td>
          <td data-l="Billing">{'Per seat' if any(p['is_per_seat'] for p in plans) else 'Flat'}</td>
        </tr>""")
        body.append("</tbody></table></div></div>")
    body.append("</section>")

    # ---- changes: prominent only when there is something to show ----
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
            '<div class="section-head"><h2>Every recorded change</h2>'
            f'<span class="aside">{len(changes)} entries</span></div>',
            _tape(changes, vendors, prefix=""),
            "</section></div>"]
    return page(f"All pricing changes \u2014 {SITE_NAME}",
                "Complete log of recorded B2B software pricing changes.",
                "".join(body), "changes.html")


def render_vendor(slug: str, name: str, ctx: dict) -> str:
    record = ctx["records"][slug]
    cur = record.get("currency", "USD")
    mine = [c for c in ctx["changes"] if c["vendor"] == name]
    category = ctx["vendor_category"].get(name, "")
    versions = ctx["versions"].get(slug, 0)

    rows = []
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
        rows.append(f"""
      <tr>
        <td class="plan-name">{esc(p['name'])}</td>
        <td class="num">{monthly}</td>
        <td class="num">{money(cur, p['annual_price_per_month'])}</td>
        <td>{'Per seat' if p['is_per_seat'] else 'Flat'}</td>
        <td>{limits}</td>
      </tr>""")

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

    body = [f"""
<div class="wrap">
  <section class="section">
    {back_link("../")}
    <div class="section-head"><h2>{esc(name)} pricing</h2>
      <span class="aside">{esc(title_case(category))}</span></div>
    <div class="tbl-scroll"><table>
      <thead><tr><th>Plan</th><th class="num">Monthly</th>
        <th class="num">Annual, per month</th><th>Billing</th>
        <th>Stated limits</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table></div>
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
                if n != name and storage.slugify(n) in ctx["records"]]
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
    return page(
        f"{name} pricing \u2014 current plans and history",
        f"{name} pricing: current plans, historical prices and every recorded "
        f"change. Entry plan {money(cur, entry)}." if entry else
        f"{name} pricing: current plans and every recorded change.",
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
        <td class="num">{money(rec.get('currency', cur), entry)}</td>
        <td class="num">{money(rec.get('currency', cur), top)}</td>
        <td>{'Yes' if free else 'No'}</td>
        <td>{'Yes' if custom else 'No'}</td>
        <td class="num">{n_changes}</td>
      </tr>""")

    body = [f"""
<div class="wrap">
  <section class="section">
    {back_link("../")}
    <div class="section-head"><h2>{esc(title_case(cat))} pricing compared</h2>
      <span class="aside">{len(names)} vendors</span></div>
    <div class="grid grid-3" style="margin-bottom:1.5rem">
      <div class="cell"><span class="stat">{esc(money(cur, bench.get('median_entry')))}</span>
        <p>Median entry price</p></div>
      <div class="cell"><span class="stat">{bench.get('pct_free', 0):.0f}%</span>
        <p>Offer a free tier</p></div>
      <div class="cell"><span class="stat">{bench.get('pct_per_seat', 0):.0f}%</span>
        <p>Charge per seat</p></div>
    </div>
    <div class="tbl-scroll"><table>
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


def render_compare(a: str, b: str, ctx: dict) -> str:
    ra, rb = ctx["records"][storage.slugify(a)], ctx["records"][storage.slugify(b)]

    def col(rec):
        cur = rec.get("currency", "USD")
        out = []
        for p in rec["plans"]:
            price = ('<span class="tag">Contact sales</span>'
                     if p["is_custom_pricing"] else
                     '<span class="tag">Free</span>' if p["is_free"] else
                     money(cur, p["monthly_price"]))
            out.append(f'<tr><td class="plan-name">{esc(p["name"])}</td>'
                       f'<td class="num">{price}</td>'
                       f'<td class="num">{money(cur, p["annual_price_per_month"])}</td>'
                       f'</tr>')
        return "".join(out)

    def entry(rec):
        low, _high, _basis = headline_prices(rec)
        return low

    ea, eb = entry(ra), entry(rb)
    if ea and eb:
        cheaper, ratio = (a, eb / ea) if ea < eb else (b, ea / eb)
        verdict = (f"{esc(cheaper)} starts {ratio:.1f}\u00d7 cheaper on its "
                   f"entry plan.")
    else:
        verdict = "One of these does not publish an entry price."

    body = f"""
<div class="wrap"><section class="section">
  {back_link("../")}
  <div class="section-head"><h2>{esc(a)} vs {esc(b)}</h2>
    <span class="aside">Entry: {esc(money(ra.get('currency','USD'), ea))}
      vs {esc(money(rb.get('currency','USD'), eb))}</span></div>
  <p class="standfirst" style="margin-bottom:1.5rem">{verdict}</p>
  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(18rem,1fr))">
    <div class="cell">
      <h3><a href="../v/{esc(storage.slugify(a))}.html">{esc(a)}</a></h3>
      <table style="margin-top:0.75rem"><thead><tr><th>Plan</th>
        <th class="num">Monthly</th><th class="num">Annual</th></tr></thead>
        <tbody>{col(ra)}</tbody></table>
    </div>
    <div class="cell">
      <h3><a href="../v/{esc(storage.slugify(b))}.html">{esc(b)}</a></h3>
      <table style="margin-top:0.75rem"><thead><tr><th>Plan</th>
        <th class="num">Monthly</th><th class="num">Annual</th></tr></thead>
        <tbody>{col(rb)}</tbody></table>
    </div>
  </div>
  <p class="provenance" style="margin-top:1.5rem">
    Both figures read from each vendor's public pricing page. Feature sets
    differ, so compare the plans, not only the numbers.</p>
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
            '<div class="section-head"><h2>This week in software pricing</h2>'
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
  <div class="section-head"><h2>How this is collected</h2></div>
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

    <p style="max-width:62ch;margin-top:1rem"><strong>What this cannot tell
    you.</strong> Plans priced on application are recorded as such, with no
    figure, so enterprise pricing is largely invisible here. Prices can vary by
    region. And a page read yesterday may have changed this morning.</p>

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


def render_bot() -> str:
    body = f"""
<div class="wrap"><section class="section">
  {back_link()}
  <div class="section-head"><h2>About the crawler</h2></div>
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
    return esc(t.replace("_", " "))


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


def render_sitemap(paths: list[str]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = "".join(
        f"  <url><loc>{BASE_URL}/{p}</loc><lastmod>{today}</lastmod></url>\n"
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
    entries, free, seat, currency = [], 0, 0, "USD"
    counted = 0
    for name in cat_names:
        rec = records.get(storage.slugify(name))
        if not rec or not rec.get("plans"):
            continue
        counted += 1
        currency = rec.get("currency") or currency
        low, _high, _basis = headline_prices(rec)
        plans = _real_plans(rec)
        if low:
            entries.append(low)
        if any(p["is_free"] for p in plans):
            free += 1
        if any(p["is_per_seat"] for p in plans):
            seat += 1
    entries.sort()
    median = entries[len(entries) // 2] if entries else None
    return {
        "median_entry": median,
        "currency": currency,
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

    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")

    write("index.html", render_index(ctx))
    write("changes.html", render_changes(ctx))
    write("about.html", render_about(ctx))
    write("week.html", render_digest(ctx))
    write("bot.html", render_bot())

    slug_to_name = {storage.slugify(n): n for n in vendors}
    for slug in sorted(records):
        write(f"v/{slug}.html",
              render_vendor(slug, slug_to_name.get(slug, title_case(slug)), ctx))

    for cat, names in sorted(by_category.items()):
        if any(storage.slugify(n) in records for n in names):
            write(f"c/{storage.slugify(cat)}.html", render_category(cat, ctx))

    # Comparison pages, but only for same-category pairs where both sides
    # have real data. No data, no page.
    pairs = set()
    for names in by_category.values():
        live = sorted(n for n in names if storage.slugify(n) in records)
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                pairs.add((a, b))
    for a, b in sorted(pairs):
        write(f"compare/{_pair_slug(a, b)}.html", render_compare(a, b, ctx))

    (out / "feed.xml").write_text(render_feed(ctx), encoding="utf-8")
    (out / "feed.xsl").write_text(
        FEED_STYLESHEET.replace("LINKS", FONT_LINK_XML +
            f'<link rel="stylesheet" href="{BASE_URL}/assets/style.css"/>'),
        encoding="utf-8")
    (out / "sitemap.xml").write_text(render_sitemap(written), encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n",
        encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    return {
        "pages": len(written), "vendors": len(records),
        "changes": len(changes), "comparisons": len(pairs), "out": out,
        "demo": _IS_DEMO,
    }
