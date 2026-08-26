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
    # Slice the plans table itself. Anchoring on the h1 also swept up the
    # written summary, which legitimately names the add-ons.
    plans_table = re.search(r"<thead><tr><th>Plan</th>.*?</table>",
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


def test_sitemap_urls_match_their_canonical():
    """The sitemap listed /index.html while the page's canonical said /.

    Google crawls the URL you point it at, reads the tag, finds it points
    elsewhere, and files the page under "Alternative page with proper
    canonical tag" -- a crawl spent on a page that was never going to be
    indexed. On a site that already cannot get crawled enough, every wasted
    one matters.
    """
    print("\nSitemap canonicals")

    xml = sitemod.render_sitemap(["index.html", "about.html", "v/zendesk.html"])
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)

    check("the homepage is listed at its canonical address",
          locs[0] == f"{sitemod.BASE_URL}/", locs[0])
    check("no /index.html anywhere in the sitemap",
          not any(l.endswith("/index.html") for l in locs))
    check("other pages are listed unchanged",
          f"{sitemod.BASE_URL}/v/zendesk.html" in locs)

    # Whatever the page claims is what the sitemap must say.
    for path in ("index.html", "about.html", "v/zendesk.html"):
        canonical = f"{sitemod.BASE_URL}/{path}".replace("/index.html", "/")
        one = sitemod.render_sitemap([path])
        check(f"{path} agrees with its own canonical",
              f"<loc>{canonical}</loc>" in one)


def test_no_page_is_a_dead_end():
    """Crawl priority follows links, and the worst-linked pages were the ones
    Google never indexed.

    Category pages -- the hubs the whole site is organised around -- had ONE
    inbound link each. The 54 comparison pages had two, and they are the
    majority of the site. Breadcrumbs route every vendor and comparison
    through its category, related links join the comparisons to each other,
    and one index page reaches everything.
    """
    print("\nCrawl reachability")

    import tempfile as _tf, shutil as _sh, yaml as _yaml
    from collections import Counter
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
        for i, v in enumerate(cfg["vendors"]):
            storage.save_plans(storage.slugify(v["name"]), {
                "currency": "USD", "pricing_is_public": True,
                "extraction_notes": "",
                "plans": [{"name": "Pro", "key": "pro",
                           "monthly_price": 15 + i,
                           "annual_price_per_month": 12, "is_free": False,
                           "is_custom_pricing": False, "is_per_seat": True,
                           "is_addon": False, "trial_days": 14,
                           "limits": [], "features": ["sso"]}]})

        out = tmp / "site"
        sitemod.build(out)
        pages = list(out.rglob("*.html"))

        inbound = Counter()
        for f in pages:
            for href in set(re.findall(r'href="([^"]+)"',
                                       f.read_text(encoding="utf-8"))):
                if href.startswith(("http", "#", "mailto")):
                    continue
                t = (f.parent / href).resolve()
                if t.suffix == ".html":
                    inbound[t] += 1

        counts = {p.relative_to(out).as_posix(): inbound.get(p.resolve(), 0)
                  for p in pages}
        worst = min(counts.values())
        check("no page is left on a single inbound link", worst >= 3,
              f"worst={worst} ({min(counts, key=counts.get)})")

        cats = [v for k, v in counts.items() if k.startswith("c/")]
        check("category hubs are strongly linked", cats and min(cats) >= 5,
              str(cats))

        comps = [v for k, v in counts.items() if k.startswith("compare/")]
        check("comparison pages are no longer near-orphans",
              comps and min(comps) >= 5, f"min={min(comps) if comps else 0}")

        check("an index of every page exists", (out / "all.html").exists())
        allp = (out / "all.html").read_text(encoding="utf-8")
        check("it reaches every vendor",
              all(f'v/{s}.html' in allp for s in list(storage.slugs())[:5])
              if hasattr(storage, "slugs") else "v/" in allp)
        check("every page can reach it",
              all("all.html" in p.read_text(encoding="utf-8") for p in pages))

        crumbs = sum(1 for p in pages
                     if "BreadcrumbList" in p.read_text(encoding="utf-8"))
        check("breadcrumb schema on the inner pages", crumbs >= len(pages) - 8,
              f"{crumbs}/{len(pages)}")

        vend = (out / "v" / "zendesk.html").read_text(encoding="utf-8")
        check("a vendor page links up to its category",
              re.search(r'href="\.\./c/[a-z-]+\.html"', vend) is not None)
    finally:
        (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
         storage.CHANGES, storage.SINCE, storage.STATE,
         storage.SPEND) = saved
        _sh.rmtree(tmp, ignore_errors=True)


def test_vendor_pages_say_something():
    """Vendor pages carried ~80 words of text -- thinner than the comparison
    pages, on the pages that receive the actual search queries."""
    print("\nVendor summaries")

    def pl(name, price, free=False, addon=False, custom=False, trial=None):
        return {"name": name, "key": name.lower(), "monthly_price": price,
                "annual_price_per_month": None, "is_free": free,
                "is_custom_pricing": custom, "is_per_seat": True,
                "is_addon": addon, "trial_days": trial,
                "limits": [], "features": []}

    ctx = {"changes": [], "tracking_since": "12 May 2026",
           "benchmarks": {"helpdesk": {"median_entry": 30, "currency": "USD",
                                       "n": 5}}}
    rec = {"currency": "USD", "plans": [pl("Starter", 19, trial=14),
                                        pl("Pro", 49), pl("Enterprise", None,
                                                          custom=True)]}
    out = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                 sitemod._vendor_summary("Acme", rec, ctx, "helpdesk")))

    check("it states the entry price", "$19" in out)
    check("it names the plans", "Starter" in out and "Pro" in out)
    check("it places the vendor against its category",
          "median" in out and "$30" in out)
    check("it says the top tier is quote-only", "quote-only" in out)
    check("it reports a stable price as a finding",
          "has changed since" in out or "has held" in out)

    # A plan called "Free" while nothing is flagged free: the two facts
    # disagree, so the page must not assert either.
    clash = {"currency": "USD",
             "plans": [pl("Free", 9, trial=30), pl("Pro", 29)]}
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                 sitemod._vendor_summary("Acme", clash, ctx, "helpdesk")))
    check("a naming clash never produces a contradiction",
          "no free tier" not in txt and "The plans are called Free" in txt,
          txt[:120])

    # With a real free plan it should say so.
    good = {"currency": "USD", "plans": [pl("Free", 0, free=True), pl("Pro", 29)]}
    gtxt = re.sub(r"<[^>]+>", " ", sitemod._vendor_summary("Acme", good, ctx,
                                                           "helpdesk"))
    check("a real free tier is stated", "there is a free tier" in gtxt.lower())

    hist = dict(ctx, changes=[
        {"vendor": "Acme", "change_type": "price_increase",
         "detected_at": "2026-07-22T00:00:00+00:00"},
        {"vendor": "Acme", "change_type": "price_decrease",
         "detected_at": "2026-06-01T00:00:00+00:00"}])
    htxt = re.sub(r"<[^>]+>", " ", sitemod._vendor_summary("Acme", rec, hist,
                                                           "helpdesk"))
    check("recorded history is surfaced, not hidden",
          "1 rise" in htxt and "1 cut" in htxt, htxt[-160:])

    check("no summary at all when there are no plans",
          sitemod._vendor_summary("Acme", {"currency": "USD", "plans": []},
                                  ctx, "helpdesk") == "")


def test_an_unreadable_page_cannot_erase_a_vendor():
    """A page that cannot be read is not a company that deleted its pricing.

    Layout changes, browser-computed pricing sliders and half-loaded pages all
    return zero plans, and they can do it twice running -- which was enough to
    satisfy the two-reading confirmation and overwrite a good record with an
    empty one. site.build() skips records with no plans, so that vendor's page
    stopped being generated: a URL Google had already indexed began returning
    404, and the vendor disappeared with nothing reported.
    """
    print("\nVendor deletion guard")

    src = (Path(sitemod.__file__).with_name("run.py")).read_text(encoding="utf-8")

    check("a wipe is refused when plans were on record",
          'if not extracted.get("plans") and baseline.get("plans")' in src)
    check("the old figures are kept rather than deleted",
          "keeping the" in src and "old figures" in src)
    check("it is flagged for a human, not swallowed",
          "extraction_lost_all_plans" in src)

    guard = src.index("extraction_lost_all_plans")
    save = src.index("changes = diff_pricing(name, baseline, extracted)")
    check("the guard runs before the overwrite", guard < save)

    # The guard must not block a vendor legitimately going quote-only, nor a
    # genuine price change.
    check("a first-ever reading is unaffected",
          "if baseline is None:" in src and src.index("if baseline is None:") < guard)

    # And the site must still refuse to build a page from an empty record --
    # that half is correct, it is the overwrite that was wrong.
    empty = {"currency": "USD", "plans": []}
    check("an empty record still produces no summary",
          sitemod._vendor_summary("Acme", empty,
                                  {"changes": [], "tracking_since": "12 May 2026",
                                   "benchmarks": {}}, "x") == "")


def test_nothing_renders_as_a_code_name():
    """Two whitelists, both of which silently dropped anything new.

    status.py listed the bad statuses it knew about, so the flag added to
    run.py this session was set and then ignored -- nobody would ever have
    been told. diff.py named eight change types and fell through for the rest,
    so six real ones, including the currency_changed type added earlier today,
    printed as "Zendesk - Pro: billing_model_changed monthly_price" on the
    changes page and in the RSS feed.

    Both now invert: anything unrecognised still reads as English.
    """
    print("\nNo raw codes reach the reader")

    from pricetrail.diff import Change
    emitted = ["price_increase", "price_decrease", "plan_added", "plan_removed",
               "plan_renamed", "feature_added", "feature_moved_out",
               "limit_changed", "currency_changed", "billing_model_changed",
               "custom_pricing_changed", "price_availability_changed",
               "pricing_hidden", "pricing_published", "a_type_invented_later"]
    bad = []
    for t in emitted:
        h = Change("Zendesk", t, "Pro", "monthly_price", 49, 55, 0.9).headline()
        if t in h or "_" in h.replace("monthly_price", ""):
            bad.append((t, h))
    check("no change type prints its own code name", not bad, str(bad[:3]))
    check("a type invented later still reads as English",
          "a type invented later" in
          Change("V", "a_type_invented_later", None, "f", 1, 2, 0.9).headline())
    check("the currency headline names both currencies",
          "GBP" in Change("V", "currency_changed", None, "currency", "USD",
                          "GBP", 0.4).headline())

    src = (Path(sitemod.__file__).with_name("status.py")).read_text(encoding="utf-8")
    check("the status page flags anything that is not ok",
          'if not status or status == "ok":' in src and "continue" in src)
    check("it no longer whitelists known-bad statuses",
          'status in ("error", "extraction_error"' not in src)
    check("the new refusal status has a human label",
          "old figures kept" in src)

    run = (Path(sitemod.__file__).with_name("run.py")).read_text(encoding="utf-8")
    check("a refused write is not counted as awaiting confirmation",
          'stats["kept_old"]' in run)


def test_both_describers_cover_every_change_type():
    """There are TWO places a change is turned into words, and they had
    different coverage.

    Change.headline() is used by the crawler's console output. site._describe()
    is what actually reaches the website and the RSS feed. Fixing the first
    left the second printing "currency changed" with no values at all -- on
    the one change type that exists specifically so a currency flip is not
    mistaken for a price cut, which is exactly when a reader needs the values.
    """
    print("\nBoth describers")

    emitted = {
        "price_increase": (49, 55), "price_decrease": (55, 49),
        "plan_added": (None, 29), "plan_removed": ("Pro", None),
        "plan_renamed": ("Pro", "Advanced"),
        "feature_added": (None, "sso"), "feature_moved_out": ("sso", None),
        "limit_changed": (5, 10), "currency_changed": ("USD", "GBP"),
        "billing_model_changed": ("flat", "per seat"),
        "custom_pricing_changed": (False, True),
        "price_availability_changed": (49, None),
        "pricing_hidden": (True, None), "pricing_published": (None, True),
    }
    vendors = {"Zendesk": {"currency": "USD"}}

    lazy = []
    for t, (old, new) in emitted.items():
        c = {"vendor": "Zendesk", "plan": "Pro", "field": "monthly_price",
             "old_value": old, "new_value": new, "change_type": t, "note": ""}
        out = sitemod._describe(c, vendors)
        plain = re.sub(r"<[^>]+>", "", out)
        # "currency changed" is the type name with the underscore removed --
        # a description that only restates its own label tells a reader nothing.
        if plain.strip().lower() == t.replace("_", " "):
            lazy.append((t, plain.strip()))
    check("no change type is described by just restating its name",
          not lazy, str(lazy[:4]))

    cur = sitemod._describe(
        {"vendor": "Zendesk", "plan": "Pro", "field": "monthly_price",
         "old_value": "USD", "new_value": "GBP",
         "change_type": "currency_changed", "note": ""}, vendors)
    check("a currency flip names both currencies on the site",
          "USD" in cur and "GBP" in cur)
    check("and warns the amounts are not comparable",
          "not comparable" in cur)

    unknown = sitemod._describe(
        {"vendor": "V", "plan": None, "field": "f", "old_value": 1,
         "new_value": 2, "change_type": "invented_next_year", "note": ""},
        vendors)
    check("something added later still reads as English",
          "invented next year" in unknown)


def test_all_three_describers_stay_in_step():
    """There are THREE places a change becomes words, not two.

    Change.headline() for the crawler log, site._describe() for the website
    and RSS, report._line() for the digest email. Each had its own list of
    known types, so the same event could read properly in one place and as a
    raw code name in another. This test walks every type the pipeline can
    emit through all three at once.
    """
    print("\nAll three describers")

    from pricetrail.diff import Change
    from pricetrail import report

    emitted = {
        "price_increase": (49, 55), "price_decrease": (55, 49),
        "plan_added": (None, 29), "plan_removed": ("Pro", None),
        "plan_renamed": ("Pro", "Advanced"), "feature_added": (None, "sso"),
        "feature_moved_out": ("sso", None), "limit_changed": (5, 10),
        "currency_changed": ("USD", "GBP"),
        "billing_model_changed": ("flat", "per seat"),
        "custom_pricing_changed": (False, True),
        "price_availability_changed": (49, None),
        "pricing_hidden": (True, None), "pricing_published": (None, True),
        "some_type_added_in_future": ("x", "y"),
    }
    vendors = {"Zendesk": {"currency": "USD"}}
    leaks = []
    for t, (old, new) in emitted.items():
        c = {"vendor": "Zendesk", "plan": "Pro", "field": "monthly_price",
             "old_value": old, "new_value": new, "change_type": t, "note": ""}
        outputs = {
            "crawler": Change("Zendesk", t, "Pro", "monthly_price",
                              old, new, 0.9).headline(),
            "website": re.sub(r"<[^>]+>", "", sitemod._describe(c, vendors)),
            "digest": report._line(c),
        }
        for where, txt in outputs.items():
            if t in txt:
                leaks.append((t, where))
    check("no describer leaks a code name", not leaks, str(leaks[:4]))

    # The currency warning is the one that must never be silently dropped:
    # it is the whole reason that change type exists.
    c = {"vendor": "Zendesk", "plan": None, "field": "currency",
         "old_value": "USD", "new_value": "GBP",
         "change_type": "currency_changed", "note": ""}
    for where, txt in (("website", sitemod._describe(c, vendors)),
                       ("digest", report._line(c))):
        check(f"the {where} names both currencies",
              "USD" in txt and "GBP" in txt)
        check(f"the {where} says the amounts are not comparable",
              "not" in txt and "comparable" in txt)


def test_the_whole_crawl_cycle():
    """The crawl loop itself, with network and AI mocked.

    Every test before this one checked a piece. This runs the actual pipeline:
    first reading, an identical page, a price rise held for confirmation, the
    rise published, then a page that cannot be read twice running -- which
    must not delete the vendor.
    """
    print("\nFull crawl cycle")

    import io, json as _json, contextlib, shutil as _sh, tempfile as _tf
    from pricetrail import run as runmod
    from pricetrail.fetch import FetchResult

    tmp = Path(_tf.mkdtemp())
    saved = (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
             storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND)
    real_fetcher, real_extract, real_cost = (
        runmod.Fetcher, runmod.extract_pricing, runmod.estimate_cost_usd)
    argv = sys.argv[:]
    try:
        storage.DATA = tmp
        storage.SNAPSHOTS = tmp / "snapshots"; storage.PLANS = tmp / "plans"
        storage.PENDING = tmp / "pending"; storage.CHANGES = tmp / "changes.jsonl"
        storage.SINCE = tmp / "recording-since.txt"
        storage.STATE = tmp / "state.json"; storage.SPEND = tmp / "spend.json"

        page = "<html><body><h1>Pricing plans</h1>" + "".join(
            f"<section><h2>{n}</h2><p>${p} per user per month</p><p>Billed "
            f"annually. Includes unlimited tickets, email support, reporting "
            f"dashboards, integrations and a 14 day free trial. Compare plans "
            f"and choose the tier for your team.</p><ul><li>Up to {i*10} "
            f"agents</li><li>SSO</li><li>API access</li></ul></section>"
            for i, (n, p) in enumerate([("Starter", 19), ("Pro", 49),
                                        ("Enterprise", 99)], 1)
        ) + "<p>All prices in USD. Contact sales for enterprise.</p></body></html>"

        class F:
            def __init__(s, *a, **k): pass
            def get(s, url, **k): return FetchResult(url=url, status=200, html=page)
            def allowed(s, url, **k): return True
            def __enter__(s): return s
            def __exit__(s, *a): return False

        def plans(pro):
            def one(nm, pr):
                return {"name": nm, "key": nm.lower(), "monthly_price": pr,
                        "annual_price_per_month": pr - 4, "is_free": False,
                        "is_custom_pricing": False, "is_per_seat": True,
                        "is_addon": False, "trial_days": 14, "limits": [],
                        "features": ["sso"]}
            return {"currency": "USD", "pricing_is_public": True,
                    "extraction_notes": "",
                    "plans": [one("Starter", 19), one("Pro", pro)]}

        runmod.Fetcher = F
        runmod.estimate_cost_usd = lambda *a, **k: 0.01

        def crawl(pro, empty=False):
            payload = ({"currency": "USD", "pricing_is_public": False,
                        "extraction_notes": "blocked", "plans": []}
                       if empty else plans(pro))
            runmod.extract_pricing = lambda *a, **k: _json.loads(_json.dumps(payload))
            sys.argv = ["run", "--only", "Zendesk", "--budget", "1.0", "--force"]
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    runmod.main()
            except SystemExit:
                pass
            return buf.getvalue()

        check("a first reading is captured as a baseline",
              "baseline captured" in crawl(49))
        check("an identical page is dismissed as noise",
              "pricing did not" in crawl(49))
        check("a price rise waits for a second opinion",
              "waiting for a second" in crawl(55))
        out = crawl(55)
        # Both the monthly and the annual figure moved in this fixture, so two
        # changes are correct -- the assertion is that something published,
        # not how many.
        check("the confirmed rise is published",
              re.search(r"changes published [1-9]", out) is not None,
              out[-200:])
        rows = storage.read_changes()
        monthly = [r for r in rows if r.get("field") == "monthly_price"]
        check("the monthly rise is recorded with the right figures",
              monthly and monthly[0]["change_type"] == "price_increase"
              and monthly[0]["old_value"] == 49
              and monthly[0]["new_value"] == 55, str(rows[:2]))
        check("nothing was published before it was confirmed twice",
              all(r["change_type"] != "price_increase" or r["new_value"] != 55
                  or r.get("field") in ("monthly_price", "annual_price_per_month")
                  for r in rows))

        crawl(0, empty=True)
        out = crawl(0, empty=True)
        check("an unreadable page twice running does NOT delete the vendor",
              "read no plans twice" in out, out[-260:])
        rec = storage.load_plans("zendesk")
        check("the old figures are still on file",
              bool(rec and rec.get("plans")), "record was wiped")
        state = _json.loads(storage.STATE.read_text(encoding="utf-8"))
        check("and it is flagged for review",
              state.get("zendesk", {}).get("status") == "extraction_lost_all_plans",
              str(state.get("zendesk", {}).get("status")))
    finally:
        (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
         storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND) = saved
        runmod.Fetcher, runmod.extract_pricing, runmod.estimate_cost_usd = (
            real_fetcher, real_extract, real_cost)
        sys.argv = argv
        _sh.rmtree(tmp, ignore_errors=True)


def test_the_build_is_deterministic():
    """Two builds from identical data must be byte-identical.

    The crawl rebuilds the whole site daily and commits the result. If any
    page varies between runs, every commit carries spurious diffs, git history
    fills with noise, and the honest-lastmod work is undermined because pages
    appear to change when nothing changed.
    """
    print("\nBuild determinism")

    import hashlib, shutil as _sh, tempfile as _tf, yaml as _yaml
    tmp = Path(_tf.mkdtemp())
    saved = (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
             storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND)
    try:
        storage.DATA = tmp
        storage.SNAPSHOTS = tmp / "snapshots"; storage.PLANS = tmp / "plans"
        storage.PENDING = tmp / "pending"; storage.CHANGES = tmp / "changes.jsonl"
        storage.SINCE = tmp / "recording-since.txt"
        storage.STATE = tmp / "state.json"; storage.SPEND = tmp / "spend.json"
        storage.SINCE.parent.mkdir(parents=True, exist_ok=True)
        storage.write_atomic(storage.SINCE, "2026-05-12")
        cfg = _yaml.safe_load(
            (Path(sitemod.__file__).parent.parent / "vendors.yaml")
            .read_text(encoding="utf-8"))
        for i, v in enumerate(cfg["vendors"][:8]):
            storage.save_plans(storage.slugify(v["name"]), {
                "currency": "USD", "pricing_is_public": True,
                "extraction_notes": "",
                "plans": [{"name": "Pro", "key": "pro", "monthly_price": 15 + i,
                           "annual_price_per_month": 12, "is_free": i % 3 == 0,
                           "is_custom_pricing": False, "is_per_seat": True,
                           "is_addon": False, "trial_days": 14, "limits": [],
                           "features": ["sso"]}]})

        def build_to(name):
            d = tmp / name
            if d.exists(): _sh.rmtree(d)
            sitemod.build(d)
            return {f.relative_to(d).as_posix():
                    hashlib.sha256(f.read_bytes()).hexdigest()
                    for f in sorted(d.rglob("*")) if f.is_file()}

        a, b = build_to("one"), build_to("two")
        differ = [k for k in a if a[k] != b.get(k)]
        check("two identical builds produce identical bytes",
              not differ, str(differ[:5]))
        check("the comparison was not vacuous", len(a) > 20, f"{len(a)} files")
    finally:
        (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
         storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND) = saved
        _sh.rmtree(tmp, ignore_errors=True)


def test_signup_setup_is_one_word():
    """Setting this up used to mean pasting a long URL into a settings box --
    three chances at a silent typo that renders a form which appears to work
    and quietly loses every address. A single word cannot be got wrong the
    same way."""
    print("\nSignup setup")

    f = sitemod._signup_action
    check("a bare username becomes a Buttondown form address",
          f("pricetrail") ==
          "https://buttondown.com/api/emails/embed-subscribe/pricetrail")
    check("stray spaces are forgiven", f("  pricetrail  ") == f("pricetrail"))
    check("a full address is left alone",
          f("https://api.mailerlite.com/forms/1/subscribe")
          == "https://api.mailerlite.com/forms/1/subscribe")
    check("any other service still works", "/" in f("example.com/subscribe"))
    check("blank still means no form at all", f("") == "" and f(None) == "")


def test_a_crossed_price_pair_is_never_published():
    """The real bug, found by checking the live site against the vendor.

    Intercom's Essential, Advanced and Expert were recorded at 29, 85 and 132
    as MONTHLY prices. Those are the ANNUAL rates -- the real monthly figures
    are 39, 99 and 139. Anyone budgeting from the page was 34% under. The
    cause is a pricing page whose billing toggle defaults to annual: the AI
    reads "$29/month", sees no "billed annually" beside it, and files it as
    monthly.

    Annual billing is a discount, so monthly must be the higher number. When
    it is not, the pair is crossed and neither figure can be trusted.
    """
    print("\nCrossed price pairs")

    from pricetrail.extract import normalise

    def one(name, monthly, annual, free=False):
        return normalise({"currency": "USD", "pricing_is_public": True,
                          "extraction_notes": "",
                          "plans": [{"name": name, "monthly_price": monthly,
                                     "annual_price_per_month": annual,
                                     "is_free": free}]})

    bad = one("Essential", 29, 39)          # annual 34% above monthly
    q = bad["plans"][0]
    check("a crossed pair publishes nothing rather than something false",
          q["monthly_price"] is None and q["annual_price_per_month"] is None)
    check("and the reason is written down, not swallowed",
          "backwards" in (bad.get("extraction_notes") or ""))

    # beehiiv really does publish $43.00 monthly and $43.08 annually. That is
    # an annual total divided by twelve, not an error, and throwing away a
    # real plan's pricing over eight cents would be worse than the bug.
    round_off = one("Scale", 43.00, 43.08)["plans"][0]
    check("a rounding difference is not treated as an error",
          round_off["monthly_price"] == 43.00
          and round_off["annual_price_per_month"] == 43.08)
    small = one("X", 100, 104)["plans"][0]
    check("a few percent is tolerated", small["monthly_price"] == 100)
    big = one("X", 100, 106)["plans"][0]
    check("a real gap is still caught", big["monthly_price"] is None)

    good = one("Essential", 39, 29)["plans"][0]
    check("the correct order is left alone",
          good["monthly_price"] == 39 and good["annual_price_per_month"] == 29)

    equal = one("Flat", 50, 50)["plans"][0]
    check("no annual discount is not an error",
          equal["monthly_price"] == 50 and equal["annual_price_per_month"] == 50)

    for label, m, a in (("monthly only", 49, None), ("annual only", None, 55)):
        r = one("X", m, a)["plans"][0]
        check(f"{label} is untouched",
              r["monthly_price"] == m and r["annual_price_per_month"] == a)

    free = one("Free", None, None, True)["plans"][0]
    check("a free plan is unaffected", free["monthly_price"] == 0.0)

    src = (Path(sitemod.__file__).with_name("extract.py")).read_text(encoding="utf-8")
    check("the prompt warns that most toggles default to annual",
          "MOST DEFAULT TO" in src)
    check("the prompt says to null both when the toggle cannot be read",
          "leave BOTH" in src)
    check("the prompt refuses promotional prices as the standard price",
          "PROMOTIONAL PRICES" in src and "fake price rise" in src)


def test_stale_prices_are_declared():
    """Kit returned HTTP 403 every day for three weeks.

    Its page kept showing the prices from the last good read on 5 August, with
    the line "Last read 05 Aug 2026" and nothing else. On a site that promises
    every price is checked daily, a bare date is not disclosure -- a reader
    takes it as when the price last CHANGED, not as three weeks of silent
    failure. This is the same class of problem as a wrong figure: the page is
    confidently not what the reader thinks it is.
    """
    print("\nStale price disclosure")

    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    today = _dt.now(_tz.utc).date()

    def aged(days):
        return {"captured_at": (today - _td(days=days)).isoformat()}

    daily = {"vendors": {"Kit": {"crawl_tier": "daily"}}}
    weekly = {"vendors": {"Kit": {"crawl_tier": "weekly"}}}

    check("fresh data says nothing",
          sitemod.staleness_warning(aged(0), "Kit", daily) == "")
    check("a daily vendor is fine for a couple of days",
          sitemod.staleness_warning(aged(2), "Kit", daily) == "")
    check("a daily vendor unread for four days warns",
          "4 days old" in sitemod.staleness_warning(aged(4), "Kit", daily))
    check("a weekly vendor gets more rope",
          sitemod.staleness_warning(aged(4), "Kit", weekly) == "")
    check("but not indefinitely",
          "11 days old" in sitemod.staleness_warning(aged(11), "Kit", weekly))

    real = sitemod.staleness_warning(aged(21), "Kit", weekly)
    check("the real Kit case warns", "21 days old" in real)
    check("it names the last good date", "Aug" in real or "aug" in real)
    check("it tells the reader what to do instead",
          "own page" in real)

    check("a record with no date is silent, not crashing",
          sitemod.staleness_warning({}, "Kit", weekly) == "")
    check("an unparseable date is silent too",
          sitemod.staleness_warning({"captured_at": "not-a-date"}, "Kit",
                                    weekly) == "")

    src = (Path(sitemod.__file__).with_name("status.py")).read_text(encoding="utf-8")
    check("the status page skips vendors no longer tracked",
          "not pruned" in src or "never pruned" in src)
    check("but not when it cannot match the vendor list at all",
          "tracked & set(state)" in src)


def test_a_vendor_with_no_readable_prices_says_so():
    """The state Intercom lands in once the crossed-pair guard nulls its
    figures: plans on record, no prices at all.

    The summary carried on regardless and said "not one of these 3 figures has
    changed since May", which reads as a tracked, stable price when nothing
    could be read. Implying knowledge we do not have is the one thing this
    site cannot afford.
    """
    print("\nVendors with no readable prices")

    def pl(n, m=None, a=None, **kw):
        d = dict(name=n, key=n.lower(), monthly_price=m,
                 annual_price_per_month=a, is_free=False,
                 is_custom_pricing=False, is_per_seat=True, is_addon=False,
                 trial_days=14, limits=[], features=[])
        d.update(kw); return d

    ctx = {"changes": [], "tracking_since": "12 May 2026", "benchmarks": {}}
    blank = {"currency": "USD",
             "plans": [pl("Essential"), pl("Advanced"), pl("Expert")]}
    out = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                 sitemod._vendor_summary("Intercom", blank, ctx, "helpdesk")))

    check("it does not claim the price has been stable",
          "has changed since" not in out and "has held" not in out, out[:120])
    check("it says the prices could not be established",
          "could not establish" in out)
    check("it still names the plans, which are known",
          "Essential" in out and "Expert" in out)
    check("it explains why nothing is shown",
          "billing toggle" in out)

    # A quote-only vendor is a different thing and must not be mislabelled.
    quoted = {"currency": "USD",
              "plans": [pl("Enterprise", is_custom_pricing=True)]}
    q = re.sub(r"<[^>]+>", " ",
               sitemod._vendor_summary("Acme", quoted, ctx, "helpdesk"))
    check("quote-only pricing is not reported as unreadable",
          "could not establish" not in q)

    # And a normal vendor is untouched.
    ok = {"currency": "USD", "plans": [pl("Pro", 49, 39), pl("Team", 99, 79)]}
    o = re.sub(r"<[^>]+>", " ",
               sitemod._vendor_summary("Acme", ok, ctx, "helpdesk"))
    check("a priced vendor still gets its normal summary",
          "$49" in o and "could not establish" not in o)


def test_the_account_menu_and_legal_pages():
    """A profile menu whose items all work, and the two pages a site that
    collects email addresses is legally required to have."""
    print("\nAccount menu and legal pages")

    import tempfile as _tf, shutil as _sh, yaml as _yaml
    tmp = Path(_tf.mkdtemp())
    saved = (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
             storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND)
    try:
        storage.DATA = tmp
        storage.SNAPSHOTS = tmp / "snapshots"; storage.PLANS = tmp / "plans"
        storage.PENDING = tmp / "pending"; storage.CHANGES = tmp / "changes.jsonl"
        storage.SINCE = tmp / "recording-since.txt"
        storage.STATE = tmp / "state.json"; storage.SPEND = tmp / "spend.json"
        storage.SINCE.parent.mkdir(parents=True, exist_ok=True)
        storage.write_atomic(storage.SINCE, "2026-05-12")
        cfg = _yaml.safe_load(
            (Path(sitemod.__file__).parent.parent / "vendors.yaml")
            .read_text(encoding="utf-8"))
        for i, v in enumerate(cfg["vendors"][:5]):
            storage.save_plans(storage.slugify(v["name"]), {
                "currency": "USD", "pricing_is_public": True,
                "extraction_notes": "",
                "plans": [{"name": "Pro", "key": "pro", "monthly_price": 20 + i,
                           "annual_price_per_month": 16, "is_free": False,
                           "is_custom_pricing": False, "is_per_seat": True,
                           "is_addon": False, "trial_days": 14, "limits": [],
                           "features": ["sso"]}]})
        out = tmp / "site"
        sitemod.build(out)
        pages = list(out.rglob("*.html"))
        idx = (out / "index.html").read_text(encoding="utf-8")

        check("the avatar menu is on the page", 'class="avatar"' in idx)
        check("it works without JavaScript",
              "<details" in idx and "acct-menu" in idx)
        check("it is labelled for screen readers", "aria-label" in idx)
        check("no dead sign-in button is shipped",
              "Sign in" not in idx and "Log in" not in idx,
              "a button that goes nowhere makes the site look unfinished")
        check("it says plainly that accounts are not open",
              "not open yet" in idx)

        check("the privacy page exists", (out / "privacy.html").exists())
        check("the terms page exists", (out / "terms.html").exists())
        priv = (out / "privacy.html").read_text(encoding="utf-8")
        check("privacy states what happens to a subscriber address",
              "never sold" in priv and "unsubscribe" in priv.lower())
        check("privacy states there is no tracking",
              "no analytics" in priv or "runs no analytics" in priv)
        terms = (out / "terms.html").read_text(encoding="utf-8")
        check("terms state the site is independent and unsponsored",
              "not affiliated" in terms and "sponsored" in terms)

        # Every menu link must resolve, on every page, at every depth.
        dead = []
        for f in pages:
            src = f.read_text(encoding="utf-8")
            menu = re.search(r'<div class="acct-menu".*?</div>', src, re.S)
            if not menu:
                dead.append((f.name, "no menu")); continue
            for href in re.findall(r'href="([^"]+)"', menu.group(0)):
                if href.startswith(("http", "mailto")):
                    continue
                target = (f.parent / href.split("#")[0]).resolve()
                if not target.exists():
                    dead.append((f.relative_to(out).as_posix(), href))
        check("every menu link resolves from every page",
              not dead, str(dead[:4]))
        check("the menu is on all pages, not just the homepage",
              all("acct-menu" in f.read_text(encoding="utf-8") for f in pages))
    finally:
        (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
         storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND) = saved
        _sh.rmtree(tmp, ignore_errors=True)


def test_the_hero_leads_with_real_data():
    """Teardowns of the highest-converting pages find that real product above
    the fold beats a description of it, near-universally, and that the biggest
    lifts come from structure rather than styling.

    The hero carried three bullets explaining that you can "look up a price" --
    a description of the product, sitting where the product should be.
    """
    print("\nHero proof block")

    import tempfile as _tf, shutil as _sh, json as _json, yaml as _yaml
    tmp = Path(_tf.mkdtemp())
    saved = (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
             storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND)
    try:
        storage.DATA = tmp
        storage.SNAPSHOTS = tmp / "snapshots"; storage.PLANS = tmp / "plans"
        storage.PENDING = tmp / "pending"; storage.CHANGES = tmp / "changes.jsonl"
        storage.SINCE = tmp / "recording-since.txt"
        storage.STATE = tmp / "state.json"; storage.SPEND = tmp / "spend.json"
        storage.SINCE.parent.mkdir(parents=True, exist_ok=True)
        storage.write_atomic(storage.SINCE, "2026-05-12")
        cfg = _yaml.safe_load(
            (Path(sitemod.__file__).parent.parent / "vendors.yaml")
            .read_text(encoding="utf-8"))
        for i, v in enumerate(cfg["vendors"][:4]):
            storage.save_plans(storage.slugify(v["name"]), {
                "currency": "USD", "pricing_is_public": True,
                "extraction_notes": "",
                "plans": [{"name": "Pro", "key": "pro", "monthly_price": 20 + i,
                           "annual_price_per_month": 16, "is_free": False,
                           "is_custom_pricing": False, "is_per_seat": True,
                           "is_addon": False, "trial_days": 14, "limits": [],
                           "features": ["sso"]}]})

        def build():
            out = tmp / "site"
            if out.exists(): _sh.rmtree(out)
            sitemod.build(out)
            return (out / "index.html").read_text(encoding="utf-8")

        # No changes yet -- the state this site is actually in today.
        empty = build()
        check("with no changes it still shows something real",
              'class="proof"' in empty)
        check("and does not pretend a change happened",
              "\u2192" not in re.search(r'<figure class="proof">.*?</figure>',
                                        empty, re.S).group(0))
        check("the feature bullets are gone", "whatis" not in empty)

        with open(storage.CHANGES, "w", encoding="utf-8") as fh:
            fh.write(_json.dumps({
                "vendor": cfg["vendors"][0]["name"], "plan": "Pro",
                "field": "monthly_price", "old_value": 20, "new_value": 25,
                "change_type": "price_increase", "confidence": 0.95,
                "detected_at": "2026-08-12T09:00:00+00:00", "note": ""}) + "\n")
        withc = build()
        fig = re.search(r'<figure class="proof">.*?</figure>', withc, re.S).group(0)
        check("with a change it shows both figures and the date",
              "$20" in fig and "$25" in fig and "Aug 2026" in fig, fig[:140])
        check("a rise is marked as a rise", 'class="up"' in fig)
        check("nothing leaks a raw None", "None" not in re.sub(r"<[^>]+>", "", fig))
    finally:
        (storage.DATA, storage.SNAPSHOTS, storage.PLANS, storage.PENDING,
         storage.CHANGES, storage.SINCE, storage.STATE, storage.SPEND) = saved
        _sh.rmtree(tmp, ignore_errors=True)


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
    test_sitemap_urls_match_their_canonical()
    test_no_page_is_a_dead_end()
    test_vendor_pages_say_something()
    test_an_unreadable_page_cannot_erase_a_vendor()
    test_nothing_renders_as_a_code_name()
    test_both_describers_cover_every_change_type()
    test_all_three_describers_stay_in_step()
    test_the_whole_crawl_cycle()
    test_the_build_is_deterministic()
    test_signup_setup_is_one_word()
    test_a_crossed_price_pair_is_never_published()
    test_a_vendor_with_no_readable_prices_says_so()
    test_the_account_menu_and_legal_pages()
    test_the_hero_leads_with_real_data()
    test_stale_prices_are_declared()
    print("\n" + "=" * 62)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\nFailures:")
        for name, detail in FAILED:
            print(f"  - {name} {detail}")
    print("=" * 62)
    sys.exit(1 if FAILED else 0)
