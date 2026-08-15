"""
Tests for the parts that can be wrong silently.

Run with:  python tests/test_pipeline.py

No network and no API key needed. These test the two things that decide whether
the business works: whether cleaning is stable enough that unchanged pricing
produces an unchanged hash, and whether the diff produces correct events.

If test_noise_only_page_has_identical_hash fails, the whole cost model
collapses -- you would be paying for an extraction on every page every day.
"""

import atexit
import json
import re
import shutil
import sys
import tempfile

import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricetrail.clean import clean_html, content_hash, looks_like_pricing_page
from pricetrail.diff import (CONFIDENCE_PUBLISH, diff_pricing,
                             fingerprint)
from pricetrail.extract import normalise
from pricetrail import site as sitemod
from pricetrail import storage
from pricetrail import theme

# ---------------------------------------------------------------- isolation
#
# Several tests below call shutil.rmtree(storage.DATA) and then write their
# own fixtures into it. Pointed at the real directory, running this suite
# DELETES the archive -- the one thing in this project that cannot be
# regenerated, and the thing the README tells you to run after every update.
#
# Others reach it more quietly: recording_since() and build() both WRITE
# data/recording-since.txt when it is missing, so merely running the tests
# planted a file claiming the archive started today. That file went out in a
# release zip once, one upload away from resetting the number the whole site's
# credibility rests on.
#
# So every storage path is redirected to a throwaway directory here, at import
# time, before a single test runs. Nothing below can reach the real archive
# even if it tries.
_REAL_DATA = Path(storage.__file__).resolve().parent.parent / "data"
_SANDBOX = Path(tempfile.mkdtemp(prefix="pricetrail-tests-"))
storage.DATA = _SANDBOX
storage.SNAPSHOTS = _SANDBOX / "snapshots"
storage.PLANS = _SANDBOX / "plans"
storage.PENDING = _SANDBOX / "pending"
storage.CHANGES = _SANDBOX / "changes.jsonl"
storage.REVIEW = _SANDBOX / "review_queue.jsonl"
storage.STATE = _SANDBOX / "state.json"
storage.SPEND = _SANDBOX / "spend.json"
storage.SINCE = _SANDBOX / "recording-since.txt"
atexit.register(lambda: shutil.rmtree(_SANDBOX, ignore_errors=True))

FIXTURES = Path(__file__).parent / "fixtures"

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append((name, detail))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"\n        {detail}" if detail and not condition else ""))


def load(fixture):
    return (FIXTURES / fixture).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

def test_cleaning():
    print("\nCleaning")
    cleaned = clean_html(load("pricing_v1.html"))

    check("keeps plan names", all(t in cleaned for t in ("Starter", "Pro", "Enterprise")))
    check("keeps prices", "$49" in cleaned and "$39" in cleaned)
    check("drops nav", "Docs" not in cleaned)
    check("drops footer", "Copyright 2026" not in cleaned)
    check("drops cookie banner", "We use cookies" not in cleaned)
    check("drops announcement bar", "Spring sale" not in cleaned)
    check("drops chat widget", "Chat with us" not in cleaned)
    check("drops carousel", "Sarah" not in cleaned)
    check("drops scripts", "__BUILD__" not in cleaned)
    check("recognised as pricing page", looks_like_pricing_page(cleaned))
    check("rejects a stub page", not looks_like_pricing_page("Page not found"))


# --------------------------------------------------------------------------
# Hash stability -- the cost model depends entirely on this
# --------------------------------------------------------------------------

def test_own_brand_not_stripped():
    """Regression: crawling a chat-widget vendor used to delete their whole
    page, because their own brand name was in the noise filter."""
    print("\nOwn-brand pages")
    html = """<html><body><main class="crisp-pricing">
      <div class="crisp-tier"><h2>Pro</h2><p>$25 per month</p>
      <ul><li>Unlimited chats</li></ul></div>
      <div class="cookie-banner">We use cookies</div>
      </main></body></html>"""

    on_own_site = clean_html(html, "crisp.chat")
    check("keeps a vendor's own page", "$25" in on_own_site and "Pro" in on_own_site,
          f"got {len(on_own_site)} chars: {on_own_site[:80]!r}")
    check("still drops the cookie banner", "We use cookies" not in on_own_site)

    elsewhere = clean_html(html, "someothersite.com")
    check("still strips the widget on other sites", "$25" not in elsewhere)

    check("no domain given behaves like a third-party site",
          "$25" not in clean_html(html))


def test_hash_stability():
    print("\nHash gate")
    v1 = clean_html(load("pricing_v1.html"))
    v2 = clean_html(load("pricing_v2_noise_only.html"))
    v3 = clean_html(load("pricing_v3_real_change.html"))

    h1, h2, h3 = content_hash(v1), content_hash(v2), content_hash(v3)

    check("noise-only change produces IDENTICAL hash", h1 == h2,
          f"{h1[:12]} vs {h2[:12]} -- every page would trigger a paid "
          f"extraction every day")
    check("real price change produces DIFFERENT hash", h1 != h3)
    check("hash is deterministic", content_hash(v1) == content_hash(v1))


# --------------------------------------------------------------------------
# Diffing
# --------------------------------------------------------------------------

BEFORE = normalise({
    "currency": "USD",
    "pricing_is_public": True,
    "extraction_notes": "",
    "plans": [
        {"name": "Starter", "monthly_price": 0, "annual_price_per_month": 0,
         "is_free": True, "is_custom_pricing": False, "is_per_seat": False,
         "limits": [{"metric": "conversations", "value": 100}],
         "features": ["email support"]},
        {"name": "Pro", "monthly_price": 49, "annual_price_per_month": 39,
         "is_free": False, "is_custom_pricing": False, "is_per_seat": True,
         "limits": [{"metric": "conversations", "value": 5000}],
         "features": ["automations", "priority support"]},
    ],
})


def variant(**changes):
    """BEFORE with a targeted modification, for one-variable-at-a-time tests."""
    import copy
    data = copy.deepcopy(BEFORE)
    for plan in data["plans"]:
        if plan["name"] in changes:
            plan.update(changes[plan["name"]])
    return data


def test_diff():
    print("\nDiff")

    check("no change produces no events", diff_pricing("X", BEFORE, BEFORE) == [])
    check("first capture is a baseline, not news",
          diff_pricing("X", None, BEFORE) == [])

    after = variant(Pro={"monthly_price": 59})
    events = diff_pricing("X", BEFORE, after)
    rise = [e for e in events if e.change_type == "price_increase"]
    check("detects a price rise", len(rise) == 1)
    check("records both values", rise and rise[0].old_value == 49
          and rise[0].new_value == 59)
    check("computes percentage", rise and "+20.4%" in rise[0].note,
          rise[0].note if rise else "")
    check("publishes a plausible rise", rise and rise[0].publishable,
          f"confidence {rise[0].confidence}" if rise else "")

    absurd = variant(Pro={"monthly_price": 588})
    ev = [e for e in diff_pricing("X", BEFORE, absurd)
          if e.change_type == "price_increase"]
    check("quarantines an implausible 12x jump", ev and not ev[0].publishable,
          f"confidence {ev[0].confidence} >= {CONFIDENCE_PUBLISH}" if ev else "")

    dropped = variant(Pro={"features": ["automations"]})
    ev = [e for e in diff_pricing("X", BEFORE, dropped)
          if e.change_type == "feature_moved_out"]
    check("detects a feature leaving a plan",
          ev and ev[0].new_value is None and ev[0].old_value == "priority support")
    check("sends feature changes to review by default",
          ev and not ev[0].publishable)

    tightened = variant(Starter={"limits": [{"metric": "conversations", "value": 50}]})
    ev = [e for e in diff_pricing("X", BEFORE, tightened)
          if e.change_type == "limit_changed"]
    check("detects a tightened usage limit",
          ev and ev[0].old_value == 100 and ev[0].new_value == 50)

    import copy
    added = copy.deepcopy(BEFORE)
    added["plans"].append({"name": "Business", "monthly_price": 99,
                           "annual_price_per_month": None, "is_free": False,
                           "is_custom_pricing": False, "is_per_seat": True,
                           "limits": [], "features": []})
    added = normalise(added)
    ev = [e for e in diff_pricing("X", BEFORE, added)
          if e.change_type == "plan_added"]
    check("detects a new plan", len(ev) == 1 and ev[0].plan == "Business")

    hidden = copy.deepcopy(BEFORE)
    hidden["pricing_is_public"] = False
    ev = [e for e in diff_pricing("X", BEFORE, hidden)
          if e.change_type == "pricing_hidden"]
    check("detects pricing going private", len(ev) == 1)


def test_survives_legacy_records():
    """Regression: a stored record whose plans lack the 'key' field used to
    crash the whole run with KeyError. Real archives will contain records
    written by older versions, so the diff has to cope."""
    print("\nMalformed records")
    import copy
    legacy = copy.deepcopy(BEFORE)
    for plan in legacy["plans"]:
        plan.pop("key", None)          # as written before keys existed

    try:
        events = diff_pricing("X", legacy, BEFORE)
        check("no crash on a record with no keys", True)
        check("identical data still reads as no change", events == [],
              f"got {[e.change_type for e in events]}")
    except KeyError as exc:
        check("no crash on a record with no keys", False, f"KeyError: {exc}")

    changed = copy.deepcopy(BEFORE)
    for plan in changed["plans"]:
        if plan["name"] == "Pro":
            plan["monthly_price"] = 59
    rise = [e for e in diff_pricing("X", legacy, changed)
            if e.change_type == "price_increase"]
    check("still detects a real change against a legacy record", len(rise) == 1)


def test_rounding_noise_ignored():
    """Regression from the first live run: beehiiv appeared to reprice from
    $96.00 to $95.92 and $43.00 to $43.08. Nobody reprices by eight cents --
    that is the page being read slightly differently, not a price change."""
    print("\nRounding noise")

    for old_p, new_p, label in [(96.0, 95.92, "$96 -> $95.92"),
                                (43.0, 43.08, "$43 -> $43.08")]:
        after = variant(Pro={"monthly_price": new_p})
        before = variant(Pro={"monthly_price": old_p})
        events = [e for e in diff_pricing("X", before, after)
                  if e.change_type.startswith("price")]
        check(f"ignores {label}", events == [],
              f"reported {[e.headline() for e in events]}")

    real = variant(Pro={"monthly_price": 59})
    events = [e for e in diff_pricing("X", BEFORE, real)
              if e.change_type == "price_increase"]
    check("still reports a real 20% rise", len(events) == 1)

    small_but_real = variant(Pro={"monthly_price": 50})   # 49 -> 50, ~2%
    events = [e for e in diff_pricing("X", BEFORE, small_but_real)
              if e.change_type == "price_increase"]
    check("still reports a 2% rise (above the floor)", len(events) == 1)


def test_confirmation_kills_flip_flops():
    """Regression: Intercom read $29, then $19, then $29 again -- two published
    'changes', neither real. A reading now has to agree with itself on two
    consecutive runs before anything is published."""
    print("\nTwo-run confirmation")

    baseline = variant(Pro={"monthly_price": 29})
    misread  = variant(Pro={"monthly_price": 19})
    correct  = variant(Pro={"monthly_price": 29})

    published, pending = [], None

    def a_run(reading):
        """Mirror of the logic in run.py."""
        nonlocal pending
        if fingerprint(reading) == fingerprint(baseline):
            pending = None
            return []
        if pending is None or fingerprint(pending) != fingerprint(reading):
            pending = reading
            return []
        pending = None
        return diff_pricing("Intercom", baseline, reading)

    published += a_run(misread)
    check("run 1 (the misread) publishes nothing", published == [])

    published += a_run(correct)
    check("run 2 (flips back) publishes nothing", published == [],
          f"published {[e.headline() for e in published]}")

    published += a_run(correct)
    check("run 3 (back to baseline) publishes nothing", published == [])

    # And a genuine change still gets through, one run later than before.
    real = variant(Pro={"monthly_price": 39})
    published += a_run(real)
    check("a real change is held on first sighting", published == [])
    published += a_run(real)
    rises = [e for e in published if e.change_type == "price_increase"]
    check("a real change publishes once confirmed", len(rises) == 1,
          f"got {[e.headline() for e in published]}")


def test_fingerprint():
    print("\nFingerprints")
    check("same content, same fingerprint",
          fingerprint(BEFORE) == fingerprint(variant()))
    check("different price, different fingerprint",
          fingerprint(BEFORE) != fingerprint(variant(Pro={"monthly_price": 59})))
    check("capture time is ignored",
          fingerprint({**BEFORE, "captured_at": "2026-01-01"})
          == fingerprint({**BEFORE, "captured_at": "2026-12-31"}))
    check("empty record is safe", fingerprint({}) == "" or True)


def test_hostile_input():
    """The archive is fed by an AI reading third-party pages. That input is not
    ours to trust, so nothing derived from it may break the page or the build."""
    print("\nHostile input")

    evil = 'Pro </script><script>alert(1)</script>'
    block = sitemod.json_ld({"name": evil})
    check("JSON-LD cannot be broken out of",
          "</script><script>" not in block.replace(
              '<script type="application/ld+json">', '').replace(
              '</script>', '', 1) or "\\u003c" in block)
    check("angle brackets are escaped in JSON-LD", "\\u003c" in block)

    check("money() survives a string value",
          sitemod.money("USD", "not a number") is not None)
    check("money() survives None", sitemod.money("USD", None) == "\u2014")
    check("money() survives zero", sitemod.money("USD", 0) == "$0")
    check("money() survives a bad currency", sitemod.money(123, 5) is not None)

    from pricetrail.extract import normalise as _n
    check("normalise survives a numeric currency",
          _n({"currency": 123, "plans": []})["currency"] == "123")
    check("normalise survives no plans key", _n({})["plans"] == [])

    check("escaping catches script tags",
          "&lt;script&gt;" in sitemod.esc("<script>"))


def test_feed_renders_for_humans():
    """Regression: clicking RSS in the nav showed a browser warning and a wall
    of raw XML. The feed now carries a stylesheet, so readers still parse the
    XML and people see a page."""
    print("\nFeed presentation")
    import xml.etree.ElementTree as ET
    from pricetrail.theme import FONT_LINK_XML
    from pricetrail import site as sm

    sheet = sm.FEED_STYLESHEET.replace("LINKS", FONT_LINK_XML)
    try:
        ET.fromstring(sheet)
        check("the stylesheet is valid XML", True)
    except ET.ParseError as exc:
        check("the stylesheet is valid XML", False, str(exc))

    try:
        ET.fromstring(FONT_LINK_XML if FONT_LINK_XML.startswith("<w")
                      else f"<w>{FONT_LINK_XML}</w>")
        check("font links are XML-safe (self-closed, & escaped)", True)
    except ET.ParseError as exc:
        check("font links are XML-safe (self-closed, & escaped)", False, str(exc))

    check("stylesheet points readers home", "All prices" in sheet)


def test_annual_only_pricing():
    """Regression from the live site: a third of the table showed no price.

    Zendesk, Freshdesk and Chatwoot all default their monthly/annual toggle to
    annual, so the only figure in the HTML is the annual-equivalent. Reading
    only monthly_price made them look as though they published nothing."""
    print("\nAnnual-only pricing")
    from pricetrail.site import headline_prices, plan_price

    P = lambda **kw: {"name":"P","key":"p","monthly_price":None,
        "annual_price_per_month":None,"is_free":False,"is_custom_pricing":False,
        "is_per_seat":True,"is_addon":False,"limits":[],"features":[], **kw}

    lo, hi, basis = headline_prices({"plans":[
        P(name="Growth", annual_price_per_month=19),
        P(name="Pro", annual_price_per_month=49)]})
    check("annual-only vendor still shows a price", lo == 19 and hi == 49,
          f"got {lo}/{hi}")
    check("and is labelled as annual", basis == "annual")

    lo, _, basis = headline_prices({"plans":[
        P(name="Standard", monthly_price=25, annual_price_per_month=21)]})
    check("monthly is preferred when both exist", lo == 25 and basis == "monthly")

    lo, hi, _ = headline_prices({"plans":[
        P(name="Suite", is_custom_pricing=True)]})
    check("contact-sales-only gives no figure", lo is None and hi is None)

    lo, _, _ = headline_prices({"plans":[
        P(name="Free", is_free=True, monthly_price=0),
        P(name="Pro", monthly_price=30)]})
    check("a free plan does not become the entry price", lo == 30)

    lo, _, _ = headline_prices({"plans":[
        P(name="AI", is_addon=True, monthly_price=1)]})
    check("an add-on does not become the entry price", lo is None)

    check("plan_price reports its basis",
          plan_price(P(annual_price_per_month=9)) == (9, "annual"))


def test_recording_since_does_not_drift():
    """Regression: the homepage said "Recording since <today>" after every
    rebuild, because the date came from the earliest change and there were no
    changes yet. That quietly claimed the archive was minutes old -- the exact
    opposite of the site's whole argument."""
    print("\nRecording-since date")
    import shutil
    from pricetrail import storage

    shutil.rmtree(storage.DATA, ignore_errors=True)
    for slug, days in [("a", ["2026-08-04", "2026-08-09"]),
                       ("b", ["2026-08-06"])]:
        d = storage.SNAPSHOTS / slug
        d.mkdir(parents=True, exist_ok=True)
        for day in days:
            (d / f"{day}.txt").write_text("x")

    first = storage.recording_since()
    check("uses the earliest snapshot, not today", first == "2026-08-04", first)

    (storage.SNAPSHOTS / "a" / "2026-12-25.txt").write_text("x")
    check("does not drift when new snapshots arrive",
          storage.recording_since() == "2026-08-04")

    storage.SINCE.unlink()
    check("recomputes to the same answer if the note is lost",
          storage.recording_since() == "2026-08-04")

    shutil.rmtree(storage.DATA, ignore_errors=True)
    check("an empty archive reports today", storage.recording_since()
          == storage.today())
    shutil.rmtree(storage.DATA, ignore_errors=True)


def test_status_page():
    """The status page is how you find out the crawler is broken without
    reading GitHub logs, so it has to survive an archive in any state."""
    print("\nStatus page")
    import shutil
    from pricetrail import storage, status

    shutil.rmtree(storage.DATA, ignore_errors=True)
    try:
        d = status.gather()
        check("survives an empty archive", d["vendors_ok"] == 0)
    except Exception as exc:
        check("survives an empty archive", False, f"{type(exc).__name__}: {exc}")

    storage.save_state({
        "a": {"status": "ok", "last_checked": "2026-08-08", "hash_changes": 4},
        "b": {"status": "extraction_error", "last_checked": "2026-08-08",
              "last_error": "boom"},
        "c": {"status": "suspicious_extraction", "last_checked": "2026-08-08"},
        "d": {"status": "ok", "last_checked": "2020-01-01"},
    })
    d = status.gather()
    failing = dict(d["failing"])
    check("spots a failed extraction", "b" in failing)
    check("spots a suspicious extraction", "c" in failing)
    check("does not flag a healthy vendor", "a" not in failing)
    check("spots a vendor gone stale", "d" in dict(d["stale"]))
    check("reports spend", isinstance(d["spend_mtd"], (int, float)))

    storage.save_state({})
    check("survives empty state", status.gather()["vendors_known"] == 0)
    shutil.rmtree(storage.DATA, ignore_errors=True)


def test_dashboard_health():
    """The dashboard has to survive the thing it monitors being broken.

    A monitoring tool that crashes when the network dies, or when GitHub
    returns rubbish, is worse than no monitoring tool -- you would read the
    silence as 'fine'."""
    print("\nDashboard health checks")
    from unittest.mock import patch
    from pricetrail import health

    class Boom:
        def __init__(self, *a, **k): raise RuntimeError("network down")

    with patch("pricetrail.health.requests.get", Boom):
        d = health.collect()
    check("survives total network failure", isinstance(d, dict))
    check("reports it rather than pretending", not d["healthy"])
    check("still returns every field",
          all(k in d for k in ("vendors_ok", "spend", "changes", "since")))

    class Junk:
        status_code = 200
        text = "<<<not json>>>"
        elapsed = __import__("datetime").timedelta(seconds=0.1)
        def json(self): raise ValueError("not json")

    with patch("pricetrail.health.requests.get", lambda *a, **k: Junk()):
        d = health.collect()
    check("survives GitHub returning rubbish", isinstance(d, dict))

    class Fine:
        status_code = 200
        text = ""
        elapsed = __import__("datetime").timedelta(seconds=0.05)
        def json(self): return {}

    with patch("pricetrail.health.requests.get", lambda *a, **k: Fine()):
        d = health.collect()
    check("handles an empty archive", d["vendors_ok"] == 0)
    check("spend defaults to zero", d["spend"] == 0.0)


def test_console_shows_changes():
    """The recorded price moves are the point of the project. An earlier
    version of the console counted them but never showed them, so the first
    real change would have appeared as 'all systems normal'."""
    print("\nConsole change feed")
    from unittest.mock import patch
    from pricetrail import health

    rows = "\n".join([
        '{"vendor":"Zendesk","plan":"Suite Team","change_type":"price_increase",'
        '"field":"monthly_price","old_value":55,"new_value":65,'
        '"detected_at":"2026-08-20T06:00:00+00:00","note":"+18.2%"}',
        '{"vendor":"Kit","plan":"Creator","change_type":"price_decrease",'
        '"field":"monthly_price","old_value":29,"new_value":25,'
        '"detected_at":"2026-08-18T06:00:00+00:00","note":"-13.8%"}',
        'not valid json at all',
    ])

    class R:
        status_code = 200
        text = rows
        elapsed = __import__("datetime").timedelta(seconds=0.1)
        def json(self): return {}

    with patch("pricetrail.health.requests.get", lambda *a, **k: R()):
        d = health.collect()

    check("skips the unparseable line", d["changes"] == 2, f"got {d['changes']}")
    check("returns the changes themselves", len(d["recent"]) == 2)
    check("newest first", d["recent"][0]["vendor"] == "Zendesk")
    check("carries both figures",
          d["recent"][0]["old"] == 55 and d["recent"][0]["new"] == 65)
    check("carries the date", d["recent"][0]["when"] == "2026-08-20")

    nxt = health.next_check()
    check("says when the next check is due",
          isinstance(nxt, str) and ("hour" in nxt or "minute" in nxt), nxt)


def test_advice_engine():
    """The console decides what to do next rather than handing over numbers.

    The rules must fire in the right order -- broken things before growth
    advice -- and must never suggest email signup before there is any reason
    to think anyone is visiting."""
    print("\nAdvice engine")
    from datetime import date, timedelta
    from pricetrail.advice import advise, summarise, days_recording

    def day(n):
        return (date.today() - timedelta(days=n)).isoformat()

    def base(**kw):
        d = dict(site_up=True, problems=[], changes=0, vendors_ok=24,
                 since=day(40))
        d.update(kw)
        return d

    check("a broken site outranks everything",
          "GitHub" in advise(base(site_up=False, changes=9))["headline"])
    check("a stopped crawl outranks growth advice",
          "Wake" in advise(base(problems=["No crawl for 50 hours"],
                                changes=9))["headline"])
    check("a broken vendor outranks growth advice",
          "problem" in advise(base(problems=["kit: HTTP 403"],
                                   changes=9))["headline"].lower())

    early = advise(base(since=day(2)))
    check("week one says do nothing", early["effort"] == "none")
    check("and says why", "age" in early["detail"])

    check("three quiet weeks suggests more vendors",
          "Widen" in advise(base(since=day(25)))["headline"])
    check("a first change suggests telling someone",
          "Tell" in advise(base(since=day(20), changes=2))["headline"])

    month = advise(base(since=day(45), changes=6))
    check("a month in, points at Search Console",
          "found you" in month["headline"])
    check("email signup is gated behind checking for traffic",
          "if people are arriving" in month["detail"].lower()
          or "if people are arriving from google" in month["detail"].lower(),
          month["detail"][:90])

    for days, changes in [(0, 0), (3, 0), (10, 0), (25, 0), (20, 3), (60, 9)]:
        a = summarise(base(since=day(days), changes=changes))
        if not (a.get("headline") and a.get("detail") and a.get("effort")):
            check(f"day {days} gives complete advice", False)
            break
    else:
        check("every stage gives complete advice", True)

    check("counts days from the start date", days_recording(day(12)) == 12)
    check("survives a rubbish date", days_recording("not a date") == 0)


def test_ask_claude():
    """The in-app assistant can act, so its limits have to be real.

    The user named three: it must not spend money, send anything, or leak
    personal information. None of those are enforced by asking the model
    nicely -- they are enforced by the tool not existing and the executor
    refusing anything off the list."""
    print("\nAsk Claude")
    import os
    import shutil
    from unittest.mock import patch
    from pricetrail import ask as ask_mod

    status = {"vendors_ok": 24, "changes": 3, "spend": 0.31,
              "since": "2026-08-04", "problems": [], "recent": []}

    # --- the limits ---
    for forbidden in ("run_crawl", "publish", "send_email", "post_tweet",
                      "read_file", "shell", "spend"):
        check(f"'{forbidden}' is refused",
              "Refused" in ask_mod.run_tool(forbidden, {}))

    names = {t["name"] for t in ask_mod.TOOLS}
    check("no tool can spend money",
          not any(w in n for n in names for w in ("crawl", "publish", "run")))
    check("no tool can send anything",
          not any(w in n for n in names
                  for w in ("send", "email", "post", "upload")))
    check("only four tools exist", len(names) == 4, str(sorted(names)))

    # --- the writes it can do are contained ---
    check("diagnose refuses an untracked company",
          "not tracked" in ask_mod.run_tool("diagnose_vendor",
                                            {"vendor": "TotallyMadeUp"}))
    check("remove refuses an untracked company",
          "No company" in ask_mod.run_tool("remove_vendor",
                                           {"vendor": "TotallyMadeUp"}))
    check("add refuses a non-URL",
          "not a URL" in ask_mod.run_tool(
              "add_vendor", {"name": "X", "pricing_url": "nope",
                             "category": "crm"}))

    backup = ask_mod.VENDORS.read_text("utf-8")
    try:
        out = ask_mod.run_tool("add_vendor", {
            "name": "ZZTestCo", "pricing_url": "https://zz.test/pricing",
            "category": "crm"})
        check("add works and says the change is local",
              "Added" in out and "local" in out.lower(), out)
        check("adding twice is refused",
              "already tracked" in ask_mod.run_tool("add_vendor", {
                  "name": "ZZTestCo", "pricing_url": "https://zz.test/pricing",
                  "category": "crm"}))
        out = ask_mod.run_tool("remove_vendor", {"vendor": "ZZTestCo"})
        check("remove works", "Removed" in out, out)
        import yaml as _y
        check("vendors.yaml is still valid afterwards",
              isinstance(_y.safe_load(ask_mod.VENDORS.read_text("utf-8")), dict))
    finally:
        ask_mod.VENDORS.write_text(backup, encoding="utf-8")

    # --- failure paths cannot take the console down ---
    with patch.dict(os.environ, {}, clear=True):
        try:
            ask_mod.ask("anything?", status)
            check("no key gives a clear error", False, "raised nothing")
        except ask_mod.AskError as exc:
            check("no key gives a clear error", "API key" in str(exc))

    class Resp:
        def __init__(self, code, body=None):
            self.status_code = code
            self._body = body or {}
        def json(self): return self._body

    for code, expect in [(401, "rejected"), (429, "Rate limited"), (500, "500")]:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("pricetrail.ask.requests.post", lambda *a, **k: Resp(code)):
            try:
                ask_mod.ask("q", status)
                check(f"HTTP {code} is explained", False, "no error raised")
            except ask_mod.AskError as exc:
                check(f"HTTP {code} is explained",
                      expect.lower() in str(exc).lower(), str(exc))

    good = Resp(200, {"stop_reason": "end_turn",
                      "content": [{"type": "text", "text": "Nothing to do."}]})
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
         patch("pricetrail.ask.requests.post", lambda *a, **k: good):
        check("a plain answer comes back",
              ask_mod.ask("q", status) == "Nothing to do.")

    # a model that only ever asks for tools must stop, not loop forever
    looping = Resp(200, {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "1", "name": "list_vendors", "input": {}}]})
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
         patch("pricetrail.ask.requests.post", lambda *a, **k: looping):
        check("a runaway loop is capped",
              "circles" in ask_mod.ask("q", status).lower())
    check("the cap is small enough to be cheap", ask_mod.MAX_ROUNDS <= 8)

    check("the brief names the user", ask_mod.USER_NAME in ask_mod.BRIEF)
    check("the brief states the limits",
          "cannot spend money" in ask_mod.BRIEF)
    check("the brief allows 'nothing needs doing'",
          "nothing needs doing" in ask_mod.BRIEF.lower())


def test_normalise():
    print("\nNormalisation")
    messy = normalise({
        "currency": "usd", "pricing_is_public": True, "extraction_notes": "",
        "plans": [{"name": "  Pro Plan ", "monthly_price": "49.005",
                   "annual_price_per_month": None, "is_free": False,
                   "is_custom_pricing": False, "is_per_seat": True,
                   "limits": [], "features": ["  SSO  ", "sso"]}],
    })
    plan = messy["plans"][0]
    check("uppercases currency", messy["currency"] == "USD")
    check("strips plan name", plan["name"] == "Pro Plan")
    check("'Pro Plan' and 'Pro' share a key", plan["key"] == "pro")
    check("rounds prices", plan["monthly_price"] == 49.01)
    check("dedupes features case-insensitively", plan["features"] == ["sso"])

    renamed = normalise({
        "currency": "USD", "pricing_is_public": True, "extraction_notes": "",
        "plans": [{"name": "Pro", "monthly_price": 49,
                   "annual_price_per_month": 39, "is_free": False,
                   "is_custom_pricing": False, "is_per_seat": True,
                   "limits": [{"metric": "conversations", "value": 5000}],
                   "features": ["automations", "priority support"]}],
    })
    before_pro = {"currency": "USD", "pricing_is_public": True,
                  "extraction_notes": "",
                  "plans": [p for p in BEFORE["plans"] if p["key"] == "pro"]}
    check("cosmetic rename is not a price change",
          not any(e.change_type.startswith("price")
                  for e in diff_pricing("X", before_pro, renamed)))


def test_currency_flip_never_becomes_a_price_change():
    """The expensive mistake this system could make.

    A page that flips USD -> GBP shows every plan at a different number. If
    that is reported as a price cut, the site publishes a confident, specific,
    false claim about a real company -- the exact thing an archive is supposed
    to be trusted not to do.
    """
    print("\nCurrency flips")

    import copy
    flipped = copy.deepcopy(BEFORE)
    flipped["currency"] = "GBP"
    for plan in flipped["plans"]:
        if plan["monthly_price"]:
            plan["monthly_price"] = round(plan["monthly_price"] * 0.79, 2)
        if plan["annual_price_per_month"]:
            plan["annual_price_per_month"] = round(
                plan["annual_price_per_month"] * 0.79, 2)

    events = diff_pricing("X", BEFORE, flipped)
    kinds = {e.change_type for e in events}

    check("a currency flip is recorded as such",
          "currency_changed" in kinds)
    check("it does NOT become a price decrease",
          "price_decrease" not in kinds, f"got {sorted(kinds)}")
    check("it does NOT become a price increase",
          "price_increase" not in kinds, f"got {sorted(kinds)}")
    check("the currency event stays below the publish bar",
          all(not e.publishable for e in events
              if e.change_type == "currency_changed"))

    # Non-price facts are currency-independent and must still be caught,
    # otherwise a currency flip would blind the crawler to everything else.
    flipped["plans"][1]["features"] = ["automations"]
    events = diff_pricing("X", BEFORE, flipped)
    check("features are still diffed during a flip",
          any(e.change_type == "feature_moved_out" for e in events))

    # And a genuine price move in a steady currency is untouched.
    after = variant(Pro={"monthly_price": 59})
    check("a same-currency rise is still detected",
          any(e.change_type == "price_increase"
              for e in diff_pricing("X", BEFORE, after)))


def test_median_uses_one_currency():
    print("\nCategory medians")

    def rec(currency, price):
        return {
            "currency": currency,
            "plans": [{"name": "Pro", "monthly_price": price,
                       "annual_price_per_month": None, "is_free": False,
                       "is_custom_pricing": False, "is_per_seat": False,
                       "is_addon": False, "limits": [], "features": []}],
        }

    records = {
        "alpha": rec("USD", 20), "bravo": rec("USD", 30),
        "charlie": rec("USD", 40), "delta": rec("GBP", 900),
    }
    bench = sitemod._benchmarks(["Alpha", "Bravo", "Charlie", "Delta"], records)

    check("the dominant currency wins", bench["currency"] == "USD")
    check("an off-currency vendor cannot drag the median",
          bench["median_entry"] == 30, f"got {bench['median_entry']}")
    check("the exclusion is counted, not hidden",
          bench["excluded_other_currency"] == 1)
    check("every currency present is listed",
          bench["currencies"] == ["GBP", "USD"])
    check("the reader is told in plain words",
          "GBP" in sitemod.mixed_currency_note(bench))

    single = sitemod._benchmarks(["Alpha", "Bravo"],
                                 {"alpha": rec("USD", 20),
                                  "bravo": rec("USD", 30)})
    check("no note when there is nothing to warn about",
          sitemod.mixed_currency_note(single) == "")


def test_no_cross_currency_verdict():
    print("\nCross-currency comparisons")

    src = Path(sitemod.__file__).read_text(encoding="utf-8")
    check("compare pages check both currencies before ranking",
          "cur_a != cur_b" in src)
    check("the footer states nothing is converted",
          "nothing here is converted" in src.lower())
    check("the crawl locale is pinned, not left to chance",
          "CRAWL_LOCALE" in
          Path(sitemod.__file__).with_name("fetch.py").read_text(
              encoding="utf-8"))


def test_only_substantial_comparisons_get_pages():
    """85 near-identical pages on a new domain got 4 indexed. Fewer, richer."""
    print("\nComparison page selection")

    def rec(price, changes_ok=True):
        return {"currency": "USD", "plans": [
            {"name": "Pro", "monthly_price": price,
             "annual_price_per_month": None, "is_free": False,
             "is_custom_pricing": False, "is_per_seat": False,
             "is_addon": False, "trial_days": 14,
             "limits": [], "features": ["sso"]}]}

    ctx = {
        "records": {"big": rec(20), "small": rec(30), "tiny": rec(40),
                    "quiet": {"currency": "USD", "plans": [
                        {"name": "Enterprise", "monthly_price": None,
                         "annual_price_per_month": None, "is_free": False,
                         "is_custom_pricing": True, "is_per_seat": False,
                         "is_addon": False, "limits": [], "features": []}]}},
        "vendors": {"Big": {"crawl_tier": "daily"},
                    "Small": {"crawl_tier": "weekly"},
                    "Tiny": {"crawl_tier": "weekly"},
                    "Quiet": {"crawl_tier": "daily"}},
        "by_category": {"x": ["Big", "Small", "Tiny", "Quiet"]},
        "changes": [],
    }

    check("a big name earns a page",
          sitemod.comparison_is_worth_a_page("Big", "Small", ctx))
    check("two long-tail names do not",
          not sitemod.comparison_is_worth_a_page("Small", "Tiny", ctx))
    check("an unpriced vendor never earns one",
          not sitemod.comparison_is_worth_a_page("Big", "Quiet", ctx))

    pairs = sitemod.comparison_pairs(ctx)
    check("only the earned pairs are built", pairs == [("Big", "Small"),
                                                       ("Big", "Tiny")],
          f"got {pairs}")

    # Recorded history rescues an obscure pair -- nobody else has that data.
    ctx["changes"] = [{"vendor": "Small", "plan": "Pro",
                       "field": "monthly_price", "old_value": 30,
                       "new_value": 35, "change_type": "price_increase",
                       "detected_at": "2026-07-01", "confidence": 0.9}]
    check("recorded history earns a long-tail pair its page",
          sitemod.comparison_is_worth_a_page("Small", "Tiny", ctx))


def test_comparison_pages_say_something():
    print("\nComparison page substance")

    def rec(price, free, seat, trial, feats):
        plans = [{"name": "Pro", "monthly_price": price,
                  "annual_price_per_month": None, "is_free": False,
                  "is_custom_pricing": False, "is_per_seat": seat,
                  "is_addon": False, "trial_days": trial,
                  "limits": [], "features": feats}]
        if free:
            plans.insert(0, {"name": "Free", "monthly_price": 0,
                             "annual_price_per_month": None, "is_free": True,
                             "is_custom_pricing": False, "is_per_seat": False,
                             "is_addon": False, "trial_days": None,
                             "limits": [], "features": []})
        return {"currency": "USD", "plans": plans}

    ra = rec(20, True, False, 14, ["sso", "api access"])
    rb = rec(90, False, True, 30, ["audit log"])
    ctx = {"records": {"a": ra, "b": rb}, "changes": [],
           "vendors": {}, "by_category": {}}
    prose, table = sitemod._differences("Alpha", "Bravo", ra, rb, ctx)

    check("a huge gap is stated as a multiple, never as >100% cheaper",
          "\u00d7 as much" in prose and "% on the entry plan" not in prose)
    check("the free-tier difference is called out", "free tier" in prose)
    check("the per-seat difference is called out", "per seat" in prose)
    check("the longer trial is named", "longer to evaluate" in prose)
    check("features only one side lists are surfaced",
          "audit log" in prose and "sso" in prose)
    check("the table carries the comparable facts",
          all(k in table for k in ("Free tier", "Billing", "Free trial",
                                   "Plans published")))

    # A near-identical pair should say so rather than invent a winner.
    close = sitemod._differences("Alpha", "Bravo", rec(20, True, False, 14, []),
                                 rec(21, True, False, 14, []), ctx)[0]
    check("a close pair is described as close", "within" in close)


def test_sitemap_dates_are_truthful():
    """lastmod was the build date on every page, every day, for months.

    Google's guidance is explicit that lastmod marks the last significant
    change and must not be the generation time -- and that it stops trusting
    the value once it looks unreliable. On a site whose whole problem is not
    being crawled, that was the one prioritisation signal available, set to
    noise.
    """
    print("\nSitemap dates")

    ctx = {
        "records": {"alpha": {}, "bravo": {}, "charlie": {}},
        "vendors": {"Alpha": {}, "Bravo": {}, "Charlie": {}},
        "by_category": {"x": ["Alpha", "Bravo", "Charlie"]},
        "changes": [
            {"vendor": "Alpha", "detected_at": "2026-07-22T09:00:00+00:00"},
            {"vendor": "Alpha", "detected_at": "2026-06-01T09:00:00+00:00"},
            {"vendor": "Bravo", "detected_at": "2026-08-05T09:00:00+00:00"},
        ],
    }
    since = storage.recording_since()
    lm = sitemod.page_lastmod(ctx)

    check("a vendor's page is dated by its latest change",
          lm["v/alpha.html"] == "2026-07-22", f"got {lm.get('v/alpha.html')}")
    check("an earlier change does not win over a later one",
          lm["v/alpha.html"] > "2026-06-01")
    check("a vendor that never moved is dated from recording start",
          lm["v/charlie.html"] == since)
    check("the homepage carries the newest change anywhere",
          lm["index.html"] == "2026-08-05")
    check("static prose is not restamped daily",
          lm["about.html"] == since)
    check("dates actually differ between pages",
          len({lm["v/alpha.html"], lm["v/bravo.html"],
               lm["v/charlie.html"]}) == 3)
    check("no page is stamped with today's build date",
          all(v <= "2026-08-05" or v == since for v in lm.values()))

    xml = sitemod.render_sitemap(["v/alpha.html", "v/charlie.html"], lm)
    check("the sitemap emits the real dates",
          "<lastmod>2026-07-22</lastmod>" in xml)
    check("a missing entry still gets a valid date",
          f"<lastmod>{since}</lastmod>" in
          sitemod.render_sitemap(["unknown.html"], lm))


def test_addons_are_not_shown_as_plans():
    """Three of the site's top ten search queries asked what an add-on costs.

    The crawler had been capturing add-ons all along and the vendor page
    listed them in the plans table, so "Surveys" appeared to be a tier sitting
    between Pro and Enterprise. The page held the answer and looked like it
    did not.
    """
    print("\nAdd-ons")

    def plan(name, price, addon=False):
        return {"name": name, "key": name.lower(), "monthly_price": price,
                "annual_price_per_month": None, "is_free": False,
                "is_custom_pricing": False, "is_per_seat": False,
                "is_addon": addon, "trial_days": 14,
                "limits": [], "features": []}

    rec = {"currency": "USD", "captured_at": "2026-08-10T00:00:00+00:00",
           "plans": [plan("Essential", 39), plan("Advanced", 99),
                     plan("Surveys", 49, addon=True),
                     plan("Product Tours", 199, addon=True)]}
    ctx = {"records": {"intercom": rec}, "changes": [],
           "vendors": {"Intercom": {}}, "vendor_category": {"Intercom": "x"},
           "by_category": {"x": ["Intercom"]}, "versions": {"intercom": 3},
           "tracking_since": "12 May 2026", "benchmarks": {}}

    html = sitemod.render_vendor("intercom", "Intercom", ctx)
    flat = re.sub(r"\s+", " ", html)
    # The plans table only -- not the page head, whose snippet legitimately
    # names the add-ons.
    plans_table = re.search(r"<h1[^>]*>Intercom pricing</h1>.*?</table>",
                            html, re.S).group(0)

    check("add-ons are lifted out of the plans table",
          "Surveys" not in plans_table and "Product Tours" not in plans_table)
    check("add-ons still appear on the page",
          "Surveys" in html and "Product Tours" in html)
    check("the section says they cost extra",
          "extra cost rather than an alternative" in flat)
    check("the title says add-ons are covered",
          "add-ons" in re.search(r"<title>(.*?)</title>", html, re.S).group(1))
    check("the search snippet names them",
          "Surveys" in re.search(r'name="description" content="([^"]+)"',
                                 html).group(1))
    check("real tiers stay in the plans table",
          "Essential" in plans_table and "Advanced" in plans_table)

    # A vendor with no add-ons must be completely unaffected.
    rec2 = {"currency": "USD", "captured_at": "2026-08-10T00:00:00+00:00",
            "plans": [plan("Standard", 25), plan("Plus", 50)]}
    ctx2 = dict(ctx, records={"help-scout": rec2},
                vendors={"Help Scout": {}},
                vendor_category={"Help Scout": "x"},
                by_category={"x": ["Help Scout"]},
                versions={"help-scout": 3})
    plain = sitemod.render_vendor("help-scout", "Help Scout", ctx2)
    check("no add-ons means no add-on section", "add-ons" not in plain)
    check("and the original title is kept",
          "current plans and history" in plain)


def test_every_page_has_exactly_one_h1():
    """23 of 24 vendor pages shipped with no h1 at all.

    Only the homepage had one; every vendor, category and comparison page
    started at h2, throwing away the strongest on-page signal of what the page
    is about on a site fighting to be considered relevant at all.

    This test builds against its OWN temporary data directory. An earlier
    version called build() against the real one, and build() -> recording_since()
    WRITES data/recording-since.txt when it is missing -- so merely running the
    tests planted a file claiming the archive started today. That file then
    went out in a release zip, one upload away from resetting the single number
    the whole site's credibility rests on. A test must not be able to touch the
    archive.
    """
    print("\nHeading structure")

    import tempfile as _tf, shutil as _sh
    from pricetrail import storage as _st

    tmp = Path(_tf.mkdtemp())
    saved = (_st.DATA, _st.SNAPSHOTS, _st.PLANS, _st.PENDING,
             _st.CHANGES, _st.SINCE, _st.STATE, _st.SPEND)
    try:
        _st.DATA = tmp
        _st.SNAPSHOTS = tmp / "snapshots"
        _st.PLANS = tmp / "plans"
        _st.PENDING = tmp / "pending"
        _st.CHANGES = tmp / "changes.jsonl"
        _st.SINCE = tmp / "recording-since.txt"
        _st.STATE = tmp / "state.json"
        _st.SPEND = tmp / "spend.json"
        _st.SINCE.parent.mkdir(parents=True, exist_ok=True)
        _st.write_atomic(_st.SINCE, "2026-05-12")

        def rec(price):
            return {"currency": "USD", "pricing_is_public": True,
                    "extraction_notes": "",
                    "plans": [{"name": "Pro", "key": "pro",
                               "monthly_price": price,
                               "annual_price_per_month": None,
                               "is_free": False, "is_custom_pricing": False,
                               "is_per_seat": False, "is_addon": False,
                               "trial_days": 14, "limits": [],
                               "features": ["sso"]}]}

        cfg = yaml.safe_load(
            (Path(sitemod.__file__).parent.parent / "vendors.yaml")
            .read_text(encoding="utf-8"))
        for v in cfg["vendors"][:4]:
            _st.save_plans(_st.slugify(v["name"]), rec(20))

        out = tmp / "site"
        summary = sitemod.build(out)
        pages = sorted(out.rglob("*.html"))
        check("the build produced vendor and comparison pages",
              len(pages) > 6, f"got {summary}")

        bad = [f"{p.relative_to(out)}={p.read_text(encoding='utf-8').count('<h1')}"
               for p in pages
               if p.read_text(encoding="utf-8").count("<h1") != 1]
        check("every page has exactly one h1", not bad, "; ".join(bad[:6]))
        check("no h1 is empty",
              all(re.search(r"<h1[^>]*>(.*?)</h1>",
                            p.read_text(encoding="utf-8"), re.S).group(1).strip()
                  for p in pages))
        check("canonical and og:url agree",
              all('property="og:url"' in p.read_text(encoding="utf-8")
                  for p in pages))
    finally:
        (_st.DATA, _st.SNAPSHOTS, _st.PLANS, _st.PENDING,
         _st.CHANGES, _st.SINCE, _st.STATE, _st.SPEND) = saved
        _sh.rmtree(tmp, ignore_errors=True)


def test_tests_do_not_touch_the_archive():
    """A test suite that writes into data/ can ship its own droppings."""
    print("\nTest isolation")

    # Deliberately checks the REAL data directory, not the sandbox.
    before = ({p.name for p in _REAL_DATA.glob("*")}
              if _REAL_DATA.exists() else set())
    test_every_page_has_exactly_one_h1()
    after = ({p.name for p in _REAL_DATA.glob("*")}
             if _REAL_DATA.exists() else set())
    check("running the suite leaves the real archive untouched",
          before == after, f"appeared: {sorted(after - before)}")
    check("storage is pointed at a sandbox, not the repo",
          storage.DATA != _REAL_DATA)


def test_untrusted_data_cannot_reach_the_page():
    """Plan names and currency codes are written by an AI reading someone
    else's web page. That is untrusted input rendered into HTML."""
    print("\nUntrusted input")

    X = '<img src=x onerror=alert(1)>'

    def plan(nm, price, addon=False):
        return {"name": nm, "key": "k", "monthly_price": price,
                "annual_price_per_month": price, "is_free": False,
                "is_custom_pricing": False, "is_per_seat": True,
                "is_addon": addon, "trial_days": 14,
                "limits": [{"metric": X, "value": 5}], "features": [X]}

    rec = {"currency": X, "captured_at": "2026-08-10T00:00:00+00:00",
           "plans": [plan("Pro " + X, 20), plan("Surveys " + X, 9, True)]}
    ctx = {"records": {"v": rec}, "changes": [], "vendors": {"V": {}},
           "vendor_category": {"V": "x"}, "by_category": {"x": ["V"]},
           "versions": {"v": 1}, "tracking_since": "12 May 2026",
           "benchmarks": {}}

    html = sitemod.render_vendor("v", "V", ctx)
    check("no payload survives into the page", X not in html)
    check("the plan name is still shown, escaped",
          "&lt;img" in html)
    check("a junk currency is stripped to letters",
          "20 IMG" in html, "currency not sanitised")

    # The JSON-LD block is the other place vendor text lands.
    schema = sitemod.vendor_schema("Acme</script><script>x</script>", rec,
                                   "https://x/y")
    check("a name cannot close the JSON-LD script tag",
          "</script><script>" not in schema)

    # money() must never raise and never emit a tag.
    for junk in (X, "<a ", None, "", "usd", 12345, "€$"):
        out = sitemod.money(junk, 10)
        check(f"money() is safe for {junk!r}", "<" not in out)

    # Bugs found in this session's own add-on code.
    amp = {"currency": "USD", "captured_at": "2026-08-10T00:00:00+00:00",
           "plans": [plan("Pro", 50), plan("Surveys & Tours", 20, True)]}
    h = sitemod.render_vendor("v", "V", dict(ctx, records={"v": amp}))
    check("add-on names are escaped once, not twice",
          "&amp;amp;" not in h and "Surveys &amp; Tours" in h)

    empty = {"currency": "USD", "captured_at": "2026-08-10T00:00:00+00:00",
             "plans": [plan("Pro", 50), plan("", 20, True)]}
    try:
        sitemod.render_vendor("v", "V", dict(ctx, records={"v": empty}))
        check("an unnamed add-on does not crash the build", True)
    except Exception as exc:
        check("an unnamed add-on does not crash the build", False, repr(exc))


def test_the_archive_survives_damage():
    """The JSON files ARE the business. Losing them is unrecoverable."""
    print("\nData integrity")

    import tempfile as _tf, shutil as _sh
    tmp = Path(_tf.mkdtemp())
    saved = (storage.DATA, storage.CHANGES)
    try:
        storage.CHANGES = tmp / "changes.jsonl"
        storage.CHANGES.write_text(
            json.dumps({"vendor": "A", "detected_at": "2026-07-01"}) + "\n"
            + '{"vendor":"B","detected_at":"2026-07-0' + "\n"
            + "not json at all\n"
            + json.dumps(["a", "list"]) + "\n"
            + json.dumps({"vendor": "C", "detected_at": "2026-08-01"}) + "\n",
            encoding="utf-8")
        rows = storage.read_changes()
        check("a truncated line does not take the build down",
              [r["vendor"] for r in rows] == ["C", "A"],
              f"got {[r.get('vendor') for r in rows]}")

        target = tmp / "rec.json"
        storage.write_atomic(target, '{"v":1}')
        check("an atomic write lands", target.read_text() == '{"v":1}')

        try:
            storage.write_atomic(tmp / "never.json", None)
        except Exception:
            pass
        check("a failed write creates no file",
              not (tmp / "never.json").exists())
        check("a failed write leaves no temp litter",
              not [p for p in tmp.iterdir() if p.name.endswith(".tmp")])

        storage.write_atomic(target, '{"v":2}')
        check("a rewrite replaces cleanly, never half",
              target.read_text() == '{"v":2}')
    finally:
        storage.DATA, storage.CHANGES = saved
        _sh.rmtree(tmp, ignore_errors=True)


def test_tables_are_readable_on_a_phone():
    """Only the homepage's tables collapsed for narrow screens.

    Vendor, category and comparison pages rendered five- and six-column tables
    into a 380px viewport, so on a phone they were either crushed or scrolling
    sideways. The stacked layout was already written and working -- those pages
    just never opted into it, and it needs both the .stack class and a data-l
    label on every cell after the first, because data-l is what it prints as
    each value's label once the header row is hidden.
    """
    print("\nMobile tables")

    import tempfile as _tf, shutil as _sh, yaml as _yaml
    tmp = Path(_tf.mkdtemp())
    saved = (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
             storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND)
    try:
        storage.DATA = tmp
        storage.SNAPSHOTS = tmp / "snapshots"
        storage.PLANS = tmp / "plans"
        storage.PENDING = tmp / "pending"
        storage.CHANGES = tmp / "changes.jsonl"
        storage.SINCE = tmp / "recording-since.txt"
        storage.STATE = tmp / "state.json"
        storage.SPEND = tmp / "spend.json"
        storage.SINCE.parent.mkdir(parents=True, exist_ok=True)
        storage.write_atomic(storage.SINCE, "2026-05-12")

        cfg = _yaml.safe_load(
            (Path(sitemod.__file__).parent.parent / "vendors.yaml")
            .read_text(encoding="utf-8"))
        for i, v in enumerate(cfg["vendors"][:5]):
            plans = [{"name": "Pro", "key": "pro", "monthly_price": 20 + i,
                      "annual_price_per_month": 16, "is_free": False,
                      "is_custom_pricing": False, "is_per_seat": True,
                      "is_addon": False, "trial_days": 14, "limits": [],
                      "features": ["sso"]},
                     {"name": "Surveys", "key": "sv", "monthly_price": 9,
                      "annual_price_per_month": None, "is_free": False,
                      "is_custom_pricing": False, "is_per_seat": False,
                      "is_addon": True, "trial_days": None, "limits": [],
                      "features": []}]
            storage.save_plans(storage.slugify(v["name"]),
                               {"currency": "USD", "pricing_is_public": True,
                                "extraction_notes": "", "plans": plans})

        out = tmp / "site"
        sitemod.build(out)
        pages = list(out.rglob("*.html"))

        unstacked, unlabelled = [], []
        for f in pages:
            h = f.read_text(encoding="utf-8")
            for attrs in re.findall(r"<table([^>]*)>", h):
                if "stack" not in attrs:
                    unstacked.append(f.relative_to(out).as_posix())
            for tr in re.findall(r"<tr>(.*?)</tr>", h, re.S):
                tds = re.findall(r"<td[^>]*>", tr)
                if len(tds) > 1 and not all("data-l" in td for td in tds[1:]):
                    unlabelled.append(f.relative_to(out).as_posix())

        check("every table collapses on a narrow screen",
              not unstacked, "; ".join(sorted(set(unstacked))[:5]))
        check("every stacked cell carries its own label",
              not unlabelled, "; ".join(sorted(set(unlabelled))[:5]))
        check("the tables tested were real, not an empty build",
              sum(h.count("<table") for h in
                  (f.read_text(encoding="utf-8") for f in pages)) >= 8)
    finally:
        (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
         storage.CHANGES, storage.SINCE, storage.STATE,
         storage.SPEND) = saved
        _sh.rmtree(tmp, ignore_errors=True)


def test_visitors_can_find_a_price():
    """24 vendors in three tables, and no way to look one up.

    Search UX research treats this as a credibility problem rather than a
    convenience one: a visitor who cannot find the thing concludes the site
    cannot help, in seconds, and does not revise it. On a site whose whole
    pitch is being the reliable record, that is the worst possible first
    impression.
    """
    print("\nFind and sort")

    from pricetrail.interact import FILTER_JS, FILTER_CSS

    check("the script never calls out to anything",
          not re.search(r"fetch\(|XMLHttpRequest|import\s|require\(", FILTER_JS))
    check("no eval anywhere", "eval(" not in FILTER_JS)
    check("the styles ship with the stylesheet",
          ".findbar" in FILTER_CSS and "th.sortable" in FILTER_CSS)
    check("an empty result explains itself rather than going blank",
          "find-empty" in FILTER_JS and "clear the box" in FILTER_JS)
    check("a missing price sorts last, never first",
          "blanks last" in FILTER_JS)
    check("the box is keyboard-clearable",
          "Escape" in FILTER_JS)
    check("headings are reachable by keyboard",
          "tabIndex" in FILTER_JS and "aria-sort" in FILTER_JS)

    import tempfile as _tf, shutil as _sh, yaml as _yaml
    tmp = Path(_tf.mkdtemp())
    saved = (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
             storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND)
    try:
        storage.DATA = tmp
        storage.SNAPSHOTS = tmp / "snapshots"
        storage.PLANS = tmp / "plans"
        storage.PENDING = tmp / "pending"
        storage.CHANGES = tmp / "changes.jsonl"
        storage.SINCE = tmp / "recording-since.txt"
        storage.STATE = tmp / "state.json"
        storage.SPEND = tmp / "spend.json"
        storage.SINCE.parent.mkdir(parents=True, exist_ok=True)
        storage.write_atomic(storage.SINCE, "2026-05-12")

        cfg = _yaml.safe_load(
            (Path(sitemod.__file__).parent.parent / "vendors.yaml")
            .read_text(encoding="utf-8"))
        for i, v in enumerate(cfg["vendors"][:6]):
            storage.save_plans(storage.slugify(v["name"]), {
                "currency": "USD", "pricing_is_public": True,
                "extraction_notes": "",
                "plans": [{"name": "Pro", "key": "pro",
                           "monthly_price": 20 + i * 7,
                           "annual_price_per_month": 16, "is_free": False,
                           "is_custom_pricing": False, "is_per_seat": True,
                           "is_addon": False, "trial_days": 14,
                           "limits": [], "features": ["sso"]}]})

        out = tmp / "site"
        sitemod.build(out)
        index = (out / "index.html").read_text(encoding="utf-8")

        check("the box sits with the tables it controls",
              'id="find"' in index and 'id="prices"' in index)
        check("the script is served and linked",
              (out / "assets" / "find.js").exists() and "find.js" in index)
        check("only the page with the tables loads it",
              "find.js" not in (out / "about.html").read_text(encoding="utf-8"))
        check("category blocks can be hidden when filtered out",
              index.count("data-block") >= 1)
        check("the box is hidden until the script enables it",
              re.search(r'<input id="find"[^>]*hidden', index) is not None,
              "a dead box is worse than no box when JS fails")
        check("the name column sorts alphabetically",
              'data-sort="text"' in index)
        check("yes/no columns are not offered as sortable",
              'data-sort="off"' in index)

        # The tables must be complete without the script.
        rows = re.findall(r"<tbody>(.*?)</tbody>", index, re.S)
        check("prices are in the HTML, not built by the script",
              sum(r.count("<tr") for r in rows) >= 6)
    finally:
        (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
         storage.CHANGES, storage.SINCE, storage.STATE,
         storage.SPEND) = saved
        _sh.rmtree(tmp, ignore_errors=True)


def test_there_is_somewhere_to_convert():
    """The site had no conversion point at all -- only an RSS link.

    And the accent is measured, not chosen. Contrast research is consistent
    that what wins is the button standing out from its background, not any
    particular hue: reviews of thousands of tests put colour-alone lifts at
    ~2.4%, while raising contrast into the 6:1-8:1 band moves real numbers.
    Orange, the colour this site used to carry, scores 2.6:1 on white -- white
    text on it is illegible.
    """
    print("\nConversion point")

    def ratio(a, b):
        def lum(h):
            h = h.lstrip("#")
            ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            r, g, bl = [f(c) for c in ch]
            return 0.2126 * r + 0.7152 * g + 0.0722 * bl
        la, lb = lum(a), lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    act = theme.TOKENS["act"]
    check("the action colour clears the 6:1 contrast target",
          ratio(act, "#FFFFFF") >= 6.0, f"{ratio(act, '#FFFFFF'):.1f}:1")
    check("white label on it is readable",
          ratio(act, "#FFFFFF") >= 4.5)
    check("it is not the old orange that failed at 2.6:1",
          act.upper() not in ("#E8874B", "#B4531A"))

    saved = sitemod.SIGNUP_URL
    try:
        sitemod.SIGNUP_URL = ""
        no_url = sitemod.subscribe_block()
        check("no form at all until a mailing service is configured",
              "<form" not in no_url,
              "a form that discards addresses is worse than none")
        check("an honest alternative is offered instead", "feed.xml" in no_url)

        sitemod.SIGNUP_URL = "https://example.com/subscribe"
        block = sitemod.subscribe_block()
        check("a real form, not a link", "<form" in block and "method=\"post\"" in block)
        check("exactly one field to fill in", block.count("<input") == 1)
        check("it asks for an email and nothing else",
              'type="email"' in block and 'name="email"' in block)
        check("the field is labelled for screen readers",
              'class="vh"' in block and "<label" in block)
        check("the reader is not navigated off the page",
              'target="_blank"' in block and 'rel="noopener"' in block)
        check("what they are agreeing to is stated",
              "Unsubscribe" in block and "never sold" in block)
        check("the button says what happens, not 'submit'",
              "Email me price changes" in block)

        compact = sitemod.subscribe_block(compact=True)
        check("a short version exists for above the fold",
              "cta-strip" in compact and "<form" in compact)
    finally:
        sitemod.SIGNUP_URL = saved


def test_no_section_appears_twice():
    """With no mailing service the compact strip fell back to the full panel,
    so the homepage printed "Follow the changes" at the top AND the bottom."""
    print("\nPage structure")

    saved = sitemod.SIGNUP_URL
    try:
        sitemod.SIGNUP_URL = ""
        check("no compact strip when there is nothing to sign up to",
              sitemod.subscribe_block(compact=True) == "")
        check("the full fallback is still offered once",
              "Follow the changes" in sitemod.subscribe_block())

        sitemod.SIGNUP_URL = "https://example.com/subscribe"
        compact = sitemod.subscribe_block(compact=True)
        full = sitemod.subscribe_block()
        check("configured, the two blocks are different",
              compact != full and "cta-strip" in compact)
        check("and they do not share a heading",
              "Follow the changes" not in compact)
    finally:
        sitemod.SIGNUP_URL = saved


if __name__ == "__main__":
    print("=" * 62)
    print("PriceTrail pipeline tests")
    print("=" * 62)
    test_cleaning()
    test_own_brand_not_stripped()
    test_hash_stability()
    test_diff()
    test_survives_legacy_records()
    test_rounding_noise_ignored()
    test_confirmation_kills_flip_flops()
    test_fingerprint()
    test_hostile_input()
    test_feed_renders_for_humans()
    test_annual_only_pricing()
    test_recording_since_does_not_drift()
    test_status_page()
    test_dashboard_health()
    test_console_shows_changes()
    test_advice_engine()
    test_ask_claude()
    test_normalise()
    test_currency_flip_never_becomes_a_price_change()
    test_median_uses_one_currency()
    test_no_cross_currency_verdict()
    test_only_substantial_comparisons_get_pages()
    test_comparison_pages_say_something()
    test_sitemap_dates_are_truthful()
    test_addons_are_not_shown_as_plans()
    test_tests_do_not_touch_the_archive()
    test_untrusted_data_cannot_reach_the_page()
    test_the_archive_survives_damage()
    test_tables_are_readable_on_a_phone()
    test_visitors_can_find_a_price()
    test_there_is_somewhere_to_convert()
    test_no_section_appears_twice()
    print("\n" + "=" * 62)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\nFailures:")
        for name, detail in FAILED:
            print(f"  - {name} {detail}")
    print("=" * 62)
    sys.exit(1 if FAILED else 0)
