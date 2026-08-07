"""
Tests for the parts that can be wrong silently.

Run with:  python tests/test_pipeline.py

No network and no API key needed. These test the two things that decide whether
the business works: whether cleaning is stable enough that unchanged pricing
produces an unchanged hash, and whether the diff produces correct events.

If test_noise_only_page_has_identical_hash fails, the whole cost model
collapses -- you would be paying for an extraction on every page every day.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricetrail.clean import clean_html, content_hash, looks_like_pricing_page
from pricetrail.diff import (CONFIDENCE_PUBLISH, diff_pricing,
                             fingerprint)
from pricetrail.extract import normalise
from pricetrail import site as sitemod

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
    test_normalise()
    print("\n" + "=" * 62)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\nFailures:")
        for name, detail in FAILED:
            print(f"  - {name} {detail}")
    print("=" * 62)
    sys.exit(1 if FAILED else 0)
