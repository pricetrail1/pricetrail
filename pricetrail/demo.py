"""
Fills the archive with plausible sample data.

Purpose is narrow: let you see and share the finished site on day one, before
six months of real crawling exists. Without this you build the site, open it,
and find an empty page -- which tells you nothing about whether it works.

Everything it writes is invented. Never publish it. `publish.py --demo` stamps
every generated record with `"demo": true` and refuses to overwrite real data.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import yaml

from . import storage
from .extract import normalise

# Realistic shapes per category: entry price band, plan names, common limits.
SHAPES = {
    "helpdesk": {
        "plans": ["Starter", "Growth", "Pro", "Enterprise"],
        "entry": (15, 39),
        "metric": "conversations",
        "features": ["email support", "automations", "sso and saml",
                     "priority support", "custom reporting", "api access",
                     "live chat", "knowledge base"],
    },
    "email-marketing": {
        "plans": ["Free", "Standard", "Premium", "Enterprise"],
        "entry": (9, 45),
        "metric": "contacts",
        "features": ["a/b testing", "automations", "sso and saml",
                     "landing pages", "send time optimisation", "api access",
                     "segmentation", "phone support"],
    },
}

DEFAULT_SHAPE = SHAPES["helpdesk"]


def _plan_set(shape: dict, rng: random.Random, drift: float = 1.0) -> list[dict]:
    entry = round(rng.uniform(*shape["entry"]) * drift)
    plans, price = [], entry
    for i, name in enumerate(shape["plans"]):
        is_free = i == 0 and name.lower() == "free"
        is_custom = i == len(shape["plans"]) - 1
        monthly = None if (is_free or is_custom) else price
        plans.append({
            "name": name,
            "monthly_price": 0 if is_free else monthly,
            "annual_price_per_month": (None if monthly is None
                                       else round(monthly * 0.8)),
            "is_free": is_free,
            "is_custom_pricing": is_custom,
            "is_per_seat": not is_free and rng.random() < 0.6,
            "min_seats": None,
            "trial_days": rng.choice([None, 14, 14, 30]),
            "limits": ([] if is_custom else
                       [{"metric": shape["metric"],
                         "value": [500, 5000, 50000][min(i, 2)],
                         "unit": None}]),
            "features": sorted(rng.sample(shape["features"],
                                          k=min(3 + i, len(shape["features"])))),
        })
        if monthly is not None:
            price = round(price * rng.uniform(2.0, 2.8))
    return plans


def generate(seed: int = 7) -> dict:
    """Write demo plans, change history and snapshot stubs."""
    rng = random.Random(seed)
    cfg = yaml.safe_load((storage.ROOT / "vendors.yaml").read_text("utf-8"))
    now = datetime.now(timezone.utc)

    storage.DATA.mkdir(parents=True, exist_ok=True)
    storage.PLANS.mkdir(parents=True, exist_ok=True)
    if storage.CHANGES.exists():
        storage.CHANGES.unlink()

    changes: list[dict] = []
    vendors = cfg["vendors"]

    for vendor in vendors:
        name = vendor["name"]
        slug = storage.slugify(name)
        shape = SHAPES.get(vendor.get("category"), DEFAULT_SHAPE)
        plans = _plan_set(shape, rng)

        # Fake ~6 months of snapshot history so version counts look right.
        folder = storage.SNAPSHOTS / slug
        folder.mkdir(parents=True, exist_ok=True)
        for k in range(rng.randint(3, 14)):
            day = (now - timedelta(days=rng.randint(1, 180))).strftime("%Y-%m-%d")
            (folder / f"{day}.txt").write_text(
                "demo snapshot placeholder\n", encoding="utf-8")

        # Roughly a third of vendors changed something recently.
        if rng.random() < 0.45:
            changes.extend(_invent_changes(name, plans, rng, now))

        # Normalise exactly like a real extraction, or demo records end up a
        # different shape and crash the diff the first time a real crawl runs
        # against them.
        record = normalise({
            "currency": "USD",
            "pricing_is_public": True,
            "extraction_notes": "",
            "plans": plans,
        })
        record["demo"] = True
        storage.save_plans(slug, record)

    changes.sort(key=lambda c: c["detected_at"])
    with storage.CHANGES.open("w", encoding="utf-8") as fh:
        for c in changes:
            fh.write(json.dumps(c) + "\n")

    return {"vendors": len(vendors), "changes": len(changes)}


def _invent_changes(name: str, plans: list[dict], rng: random.Random,
                    now: datetime) -> list[dict]:
    out = []
    paid = [p for p in plans if p["monthly_price"]]
    if not paid:
        return out

    # Price history has to be a coherent chain: each change starts where the
    # last one ended, and the final value equals the plan's current price.
    # Work backwards from today's price to derive where it started.
    plan = rng.choice(paid)
    n_price = rng.randint(1, 3)
    mults = [rng.choice([1.1, 1.2, 1.25, 0.9, 1.15]) for _ in range(n_price)]
    ladder = [plan["monthly_price"]]
    for m in reversed(mults):
        ladder.insert(0, max(1, round(ladder[0] / m)))

    dates = sorted(
        now - timedelta(days=rng.randint(1, 150), hours=rng.randint(0, 23))
        for _ in range(n_price)
    )
    for i, when in enumerate(dates):
        old, new = ladder[i], ladder[i + 1]
        if old == new:
            continue
        out.append(_c(name, "price_increase" if new > old else "price_decrease",
                      plan["name"], "monthly_price", old, new, 0.95, when,
                      f"{(new - old) / old * 100:+.1f}%"))

    for _ in range(rng.randint(0, 2)):
        when = now - timedelta(days=rng.randint(1, 120),
                               hours=rng.randint(0, 23))
        kind = rng.choice(["limit", "feature", "plan"])
        plan = rng.choice(paid)

        if kind == "limit" and plan["limits"]:
            lim = plan["limits"][0]
            out.append(_c(name, "limit_changed", plan["name"], lim["metric"],
                          lim["value"], round(lim["value"] * 0.5), 0.85, when))
        elif kind == "feature" and plan["features"]:
            out.append(_c(name, "feature_moved_out", plan["name"], "features",
                          rng.choice(plan["features"]), None, 0.9, when))
        else:
            out.append(_c(name, "plan_added", plan["name"], None, None,
                          plan["monthly_price"], 0.9, when))
    return out


def _c(vendor, change_type, plan, field, old, new, conf, when, note=""):
    return {
        "vendor": vendor, "change_type": change_type, "plan": plan,
        "field": field, "old_value": old, "new_value": new,
        "confidence": conf, "detected_at": when.isoformat(), "note": note,
    }
