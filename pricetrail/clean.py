"""
Turn a messy pricing page into stable, comparable text.

This is the most important file in the project. If cleaning is bad, every page
looks like it changed every day, you burn API credits on nothing, and you email
customers false alarms. Everything else is plumbing; this is the actual work.
"""

from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup, Comment

# Whole elements that never contain pricing and always contain noise.
DROP_TAGS = [
    "script", "style", "noscript", "svg", "iframe", "canvas",
    "video", "audio", "template", "head", "nav", "header", "footer",
]

# Substrings matched against class/id. Case-insensitive.
# These are the usual suspects for content that changes on every page load.
NOISE_PATTERNS = [
    "cookie", "consent", "gdpr", "banner", "announcement", "promo-bar",
    "topbar", "notification", "carousel", "testimonial",
    "marquee", "ticker", "newsletter", "social-proof",
    "breadcrumb", "skip-link", "cursor", "backdrop", "modal-overlay",
]

# Third-party chat widgets, matched by brand name. These are separated out for
# a reason: when you crawl Intercom's own pricing page, every element on it is
# likely to have "intercom" in its class name. Treating that as noise deletes
# the entire page -- which is exactly what happened to Crisp on the first run.
# So these patterns are skipped whenever the brand matches the site we are on.
WIDGET_BRANDS = [
    "intercom", "drift", "crisp", "zendesk", "livechat", "tidio",
    "hubspot", "tawk", "freshchat", "olark",
]

# Generic widget class names, safe to strip anywhere.
WIDGET_PATTERNS = ["chat-widget", "chat-bubble", "chat-launcher", "help-widget"]

# Volatile strings that leak into markup and change on every request.
VOLATILE = [
    # cache-busting query strings and build hashes
    (re.compile(r"\?v=[\w.-]+"), ""),
    (re.compile(r"\b[0-9a-f]{32,64}\b"), "<HASH>"),
    (re.compile(r"\bnonce-[\w+/=-]+", re.I), "<NONCE>"),
    # ISO timestamps and dates
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?\b"), "<TS>"),
    # UUIDs / session ids
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    # "Trusted by 12,431 teams" style live counters
    (re.compile(r"\b\d[\d,]{3,}\+?\s+(customers|teams|companies|users|"
                r"businesses|developers)\b", re.I), "<COUNT> \\1"),
]

WHITESPACE = re.compile(r"[ \t\xa0\u200b]+")
BLANKLINES = re.compile(r"\n{3,}")


def _is_noise(tag, patterns) -> bool:
    """True if this element's class or id looks like page furniture."""
    ident = " ".join(
        filter(None, [
            " ".join(tag.get("class", [])),
            tag.get("id", "") or "",
            tag.get("data-testid", "") or "",
            tag.get("aria-label", "") or "",
        ])
    ).lower()
    if not ident:
        return False
    return any(p in ident for p in patterns)


def _patterns_for(site_domain: str | None) -> list[str]:
    """Noise patterns to use, minus any brand that IS the site we're on."""
    patterns = list(NOISE_PATTERNS) + list(WIDGET_PATTERNS)
    host = (site_domain or "").lower()
    for brand in WIDGET_BRANDS:
        if brand and brand in host:
            continue  # never strip a company's own name from its own site
        patterns.append(brand)
    return patterns


def clean_html(html: str, site_domain: str | None = None) -> str:
    """Reduce a pricing page to the text that actually matters.

    Pass site_domain (e.g. "crisp.chat") so brand-name filters are not applied
    to that brand's own website. Without it, crawling a chat-widget vendor
    strips their entire page.

    Returns plain text with layout roughly preserved by newlines. Stable
    across reloads on a well-behaved site.
    """
    patterns = _patterns_for(site_domain)
    soup = BeautifulSoup(html, "lxml")

    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()

    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    # Walk a copy of the list: decompose() mutates the tree as we go.
    for tag in list(soup.find_all(True)):
        if tag.decomposed:
            continue
        if _is_noise(tag, patterns):
            tag.decompose()

    # Hidden elements often hold duplicate mobile markup or A/B variants.
    for tag in list(soup.find_all(attrs={"aria-hidden": "true"})):
        if not tag.decomposed:
            tag.decompose()

    text = soup.get_text(separator="\n")

    for pattern, repl in VOLATILE:
        text = pattern.sub(repl, text)

    lines = [WHITESPACE.sub(" ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    # Collapse immediate duplicate lines (common with responsive duplicates).
    deduped: list[str] = []
    for ln in lines:
        if not deduped or deduped[-1] != ln:
            deduped.append(ln)

    return BLANKLINES.sub("\n\n", "\n".join(deduped)).strip()


def content_hash(cleaned_text: str) -> str:
    """Stable fingerprint of a cleaned page."""
    return hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()


def looks_like_pricing_page(cleaned_text: str) -> bool:
    """Cheap sanity check that we fetched a pricing page, not a 404 or a
    cookie wall. Guards against silently archiving rubbish for months."""
    low = cleaned_text.lower()
    if len(cleaned_text) < 200:
        return False
    money = re.search(r"[$£€]\s?\d|\bper (month|user|seat|year)\b"
                      r"|\bfree\b|\bcontact sales\b", low)
    plan_words = sum(w in low for w in
                     ("plan", "pricing", "tier", "billed", "subscription"))
    return bool(money) and plan_words >= 1
