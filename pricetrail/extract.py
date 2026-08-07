"""
Cleaned page text -> structured pricing data.

This is the only step that costs money, which is why run.py never calls it
unless the content hash actually moved. Haiku is used deliberately: this is a
reading-comprehension task, not a reasoning task, and Haiku is roughly 1/5th
the price of Sonnet for it.

Structured output is forced via tool use rather than "please reply in JSON",
because a forced tool call cannot come back with a preamble, an apology, or a
markdown fence around the payload.
"""

from __future__ import annotations

import json
import os

import requests

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
MAX_INPUT_CHARS = 24_000  # ~6k tokens; pricing pages are rarely longer

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "currency": {
            "type": "string",
            "description": "ISO 4217 code shown on the page, e.g. USD, GBP, EUR.",
        },
        "plans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "monthly_price": {
                        "type": ["number", "null"],
                        "description": (
                            "Price for ONE month when billed monthly. Null if "
                            "the plan is custom/contact-sales or genuinely free."
                        ),
                    },
                    "annual_price_per_month": {
                        "type": ["number", "null"],
                        "description": (
                            "Effective per-month price when billed annually. "
                            "Null if not offered."
                        ),
                    },
                    "is_free": {"type": "boolean"},
                    "is_custom_pricing": {
                        "type": "boolean",
                        "description": "True for 'Contact sales' / 'Custom' tiers.",
                    },
                    "is_per_seat": {
                        "type": "boolean",
                        "description": "True if price is per user/seat/agent.",
                    },
                    "min_seats": {"type": ["integer", "null"]},
                    "trial_days": {"type": ["integer", "null"]},
                    "limits": {
                        "type": "array",
                        "description": (
                            "Hard usage caps stated for this plan, e.g. "
                            "{'metric': 'contacts', 'value': 500}. Use -1 for "
                            "unlimited."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "metric": {"type": "string"},
                                "value": {"type": "number"},
                                "unit": {"type": ["string", "null"]},
                            },
                            "required": ["metric", "value"],
                        },
                    },
                    "features": {
                        "type": "array",
                        "description": (
                            "ONLY features from this exact list that the page "
                            "states are included in this plan: sso, saml, "
                            "api access, priority support, phone support, "
                            "dedicated account manager, custom reporting, "
                            "advanced workflows, basic workflows, live chat, "
                            "whatsapp, sla policies, audit log, hipaa, "
                            "custom branding, integrations, ai assistant, "
                            "ai chatbot, onboarding support, sandbox, "
                            "role permissions, single sign-on. "
                            "Use the exact wording from this list, not the "
                            "page's wording. Omit anything not on the list. "
                            "Never include marketing phrases, plan names, or "
                            "fragments such as 'everything in X'."
                        ),
                        "items": {"type": "string"},
                    },
                    "is_addon": {
                        "type": "boolean",
                        "description": (
                            "True if this is an add-on or usage-based extra "
                            "sold alongside a plan (e.g. an AI chatbot priced "
                            "per resolution, extra seats, extra storage), "
                            "rather than a subscription tier a customer "
                            "chooses between."
                        ),
                    },
                },
                "required": [
                    "name", "monthly_price", "annual_price_per_month",
                    "is_free", "is_custom_pricing", "is_per_seat",
                    "limits", "features",
                ],
            },
        },
        "pricing_is_public": {
            "type": "boolean",
            "description": (
                "False if the page shows no numbers at all and routes every "
                "tier to sales."
            ),
        },
        "extraction_notes": {
            "type": "string",
            "description": (
                "Anything ambiguous or unreadable. Empty string if clean."
            ),
        },
    },
    "required": ["currency", "plans", "pricing_is_public", "extraction_notes"],
}

SYSTEM = """You extract pricing data from software pricing pages.

Rules:
- Record only what the page states. Never infer, estimate, or fill gaps.
- If a value is not shown, use null. Do not guess.
- Prices are numbers only, no currency symbols, no thousands separators.
- If a page shows both monthly and annual pricing, record both.
- Ignore promotional or time-limited discount prices; record the standard price.
- Preserve plan names exactly as written, including capitalisation.
- If the page is not a pricing page, return an empty plans array and say so in
  extraction_notes.
- Limits: take the number from the plan's own card or column. If a comparison
  table and a plan card disagree, use the plan card and say so in
  extraction_notes. Never merge two different numbers for the same metric.
- Record at most 6 limits per plan: the ones a buyer would actually compare
  (seats/users, and the main usage metric). Ignore minor feature caps.
- Add-ons are not plans. Set is_addon true for anything sold on top of a
  subscription rather than instead of one."""


class ExtractionError(RuntimeError):
    pass


def extract_pricing(cleaned_text: str, vendor_name: str,
                    api_key: str | None = None,
                    model: str = MODEL) -> dict:
    """Return structured pricing data for one page.

    Raises ExtractionError on transport or protocol failure so the caller can
    quarantine the vendor rather than write junk into the archive.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionError("ANTHROPIC_API_KEY is not set")

    text = cleaned_text[:MAX_INPUT_CHARS]

    payload = {
        "model": model,
        "max_tokens": 4000,
        "system": SYSTEM,
        "tools": [{
            "name": "record_pricing",
            "description": "Record the pricing structure found on the page.",
            "input_schema": PLAN_SCHEMA,
        }],
        "tool_choice": {"type": "tool", "name": "record_pricing"},
        "messages": [{
            "role": "user",
            "content": (
                f"Pricing page for {vendor_name}. Extract every plan.\n\n"
                f"<page>\n{text}\n</page>"
            ),
        }],
    }

    try:
        resp = requests.post(
            API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise ExtractionError(f"request failed: {exc}") from exc

    if resp.status_code != 200:
        raise ExtractionError(f"API {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    for block in body.get("content", []):
        if block.get("type") == "tool_use":
            return normalise(block["input"])

    raise ExtractionError(f"no tool_use block returned: {json.dumps(body)[:300]}")


def normalise(data: dict) -> dict:
    """Make extracted data canonical so diffs compare like with like.

    Without this, 'Pro' vs 'pro ' vs 'Pro Plan' read as three different plans
    and you generate a fake change event every time the marketing team edits a
    heading.
    """
    out = {
        "currency": str(data.get("currency") or "").upper()[:3],
        "pricing_is_public": bool(data.get("pricing_is_public", True)),
        "extraction_notes": (data.get("extraction_notes") or "").strip(),
        "plans": [],
    }

    for plan in data.get("plans") or []:
        name = (plan.get("name") or "").strip()
        if not name:
            continue
        monthly = _num(plan.get("monthly_price"))
        if plan.get("is_free") and monthly is None:
            monthly = 0.0

        out["plans"].append({
            "name": name,
            "key": _plan_key(name),
            "monthly_price": monthly,
            "annual_price_per_month": _num(plan.get("annual_price_per_month")),
            "is_free": bool(plan.get("is_free")),
            "is_custom_pricing": bool(plan.get("is_custom_pricing")),
            "is_per_seat": bool(plan.get("is_per_seat")),
            "is_addon": bool(plan.get("is_addon")),
            "min_seats": plan.get("min_seats"),
            "trial_days": plan.get("trial_days"),
            "limits": sorted(
                [
                    {
                        "metric": str(l.get("metric", "")).strip().lower(),
                        "value": _num(l.get("value")),
                        "unit": (l.get("unit") or None),
                    }
                    for l in (plan.get("limits") or [])
                    if l.get("metric")
                ],
                key=lambda l: l["metric"],
            ),
            "features": sorted({
                str(f).strip().lower()
                for f in (plan.get("features") or []) if str(f).strip()
            }),
        })

    out["plans"].sort(key=lambda p: (p["monthly_price"] is None,
                                     p["monthly_price"] or 0, p["key"]))
    return out


def _plan_key(name: str) -> str:
    """Loose identity for a plan, so cosmetic renames don't look like churn."""
    key = name.lower().strip()
    for suffix in (" plan", " tier", " package", " edition"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    return "".join(ch for ch in key if ch.isalnum())


def _num(value):
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, 2)


def estimate_cost_usd(cleaned_text: str) -> float:
    """Rough per-extraction cost at Haiku 4.5 rates ($1/$5 per Mtok).

    Useful for keeping a running total so you never get a surprise bill.
    """
    input_tokens = min(len(cleaned_text), MAX_INPUT_CHARS) / 4
    output_tokens = 900
    return (input_tokens / 1_000_000) * 1.0 + (output_tokens / 1_000_000) * 5.0
