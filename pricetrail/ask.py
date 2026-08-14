"""
Asking Claude about the project -- and letting it act, within hard limits.

This is not a continuation of whatever conversation built the project. It is a
fresh Claude with no memory of it, so BRIEF below tells it who you are, what
PriceTrail is, how it works and where it usually goes wrong.

WHAT IT CAN DO
Look at everything: vendors, prices, changes, spend, failures. Fetch a page to
work out why a vendor is failing. Add or remove a vendor from vendors.yaml.

WHAT IT CANNOT DO, ENFORCED IN CODE RATHER THAN ASKED FOR NICELY
- Spend money. The crawl and the extraction API are not among its tools, so it
  cannot trigger them however it is asked.
- Send anything anywhere. No email, no posting, no uploads. The only outbound
  request is to the Anthropic API.
- Touch any file except vendors.yaml, and that path is resolved and checked to
  sit inside the project before any write happens.
- Run arbitrary commands. Tools are a fixed list; anything not on it is
  refused by the executor before it reaches the system.

A prompt cannot talk its way past those, because they are not instructions --
they are the absence of a capability.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
import yaml

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"

ROOT = Path(__file__).resolve().parent.parent
VENDORS = ROOT / "vendors.yaml"

MAX_ROUNDS = 6          # stops a loop costing you money
DIAGNOSE_TIMEOUT = 120

# Change this to whatever you want it to call you.
USER_NAME = "Sebastian"

BRIEF = f"""You are the assistant inside {USER_NAME}'s own desktop console for
a project called PriceTrail. You are talking to {USER_NAME} directly.

YOUR JOB
Make this simpler for him. He would rather you did the work than told him how
to do it, so use your tools before answering: look things up, diagnose the
failure, make the change. Then report what you did in two or three sentences.

Be direct and plain. No preamble, no "great question", no bullet lists unless
they genuinely help. If the honest answer is that nothing needs doing, say so
plainly -- he would rather hear that than be handed busywork.

THE PROJECT
PriceTrail records how B2B software companies change their pricing. A crawler
runs on GitHub Actions every day at 06:00 UTC, reads each company's public
pricing page, and stores what it finds. The archive is the whole point: it is
valuable because it holds prices recorded on days nobody else recorded them,
so it grows more valuable with age and cannot be caught up on.

HOW IT WORKS
- Pages are fetched, stripped of navigation and banners, then hashed. Unchanged
  hash means nothing else happens, which keeps the bill near zero.
- When a page has changed, an AI reads it and returns structured plans.
- A change is published only after two consecutive runs agree, because single
  readings produced false alarms.
- Price moves under 1% are ignored as rounding noise.
- The site is static HTML on GitHub Pages at getpricetrail.com. Around 50p a
  month in AI costs.

WHAT USUALLY GOES WRONG
- 403 or 429: the site is blocking the crawler. Often temporary. If it
  persists, remove that company -- wrong data is worse than missing data.
- A slider pricing page calculates the figure in the browser, so there is
  nothing in the page to read. Those companies get removed.
- A moved pricing page: diagnosing it hunts for the new address automatically.
- The daily run stopping: GitHub pauses scheduled jobs in quiet repositories,
  and pushing anything restarts it.

YOUR LIMITS
You cannot spend money, send anything anywhere, or touch any file other than
vendors.yaml. These are not rules you are being asked to follow -- the tools
simply do not exist, so do not offer to do those things. If {USER_NAME} asks
for one, say plainly that you cannot and what he would do instead.

Changes you make to vendors.yaml are local. Say so: he needs to upload the
file to GitHub for them to take effect."""


TOOLS = [
    {
        "name": "list_vendors",
        "description": "List every company currently tracked, with category "
                       "and pricing page URL.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "diagnose_vendor",
        "description": "Fetch one company's pricing page and report why it is "
                       "failing: whether it blocks the crawler, whether prices "
                       "are in the HTML at all, and whether the page has moved. "
                       "Free and read-only.",
        "input_schema": {
            "type": "object",
            "properties": {"vendor": {"type": "string",
                                      "description": "Company name or slug."}},
            "required": ["vendor"],
        },
    },
    {
        "name": "remove_vendor",
        "description": "Remove a company from vendors.yaml. Its recorded "
                       "history is kept. Use when a site blocks the crawler "
                       "persistently or prices cannot be read.",
        "input_schema": {
            "type": "object",
            "properties": {"vendor": {"type": "string"}},
            "required": ["vendor"],
        },
    },
    {
        "name": "add_vendor",
        "description": "Add a company to vendors.yaml.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "pricing_url": {"type": "string"},
                "category": {"type": "string"},
            },
            "required": ["name", "pricing_url", "category"],
        },
    },
]

ALLOWED = {t["name"] for t in TOOLS}


class AskError(RuntimeError):
    pass


# --------------------------------------------------------------- tool bodies

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")


def _vendor_list() -> list[dict]:
    try:
        return yaml.safe_load(VENDORS.read_text("utf-8")).get("vendors", []) or []
    except (OSError, yaml.YAMLError, AttributeError):
        return []


def _safe_vendors_path() -> Path:
    """Refuse to write anywhere except the project's own vendors.yaml."""
    path = VENDORS.resolve()
    if path.parent != ROOT.resolve() or path.name != "vendors.yaml":
        raise AskError("Refused: that is not the vendors file.")
    return path


def _tool_list_vendors() -> str:
    rows = [f"{v.get('name')} | {v.get('category')} | {v.get('pricing_url')}"
            for v in _vendor_list()]
    return f"{len(rows)} tracked:\n" + "\n".join(rows) if rows else "None."


def _tool_diagnose(vendor: str) -> str:
    known = {_slug(v.get("name", "")) for v in _vendor_list()}
    slug = _slug(vendor)
    if slug not in known:
        return (f"'{vendor}' is not tracked. Tracked: "
                f"{', '.join(sorted(known))}")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pricetrail.diagnose", slug],
            cwd=ROOT, capture_output=True, text=True,
            timeout=DIAGNOSE_TIMEOUT, encoding="utf-8", errors="replace")
        return ((r.stdout or "") + (r.stderr or ""))[:4000] or "No output."
    except subprocess.TimeoutExpired:
        return "The page took too long to respond."
    except Exception as exc:
        return f"Could not run it: {type(exc).__name__}"


def _tool_remove(vendor: str) -> str:
    path = _safe_vendors_path()
    keep, skipping, found = [], False, False
    for line in path.read_text("utf-8").split("\n"):
        if line.strip().startswith("- name:"):
            this = line.split("- name:", 1)[1].strip()
            skipping = _slug(this) == _slug(vendor)
            found = found or skipping
        elif skipping and line.strip() and not line.startswith("    "):
            skipping = False
        if not skipping:
            keep.append(line)
    if not found:
        return f"No company called '{vendor}' in vendors.yaml."
    path.write_text("\n".join(keep), encoding="utf-8")
    return (f"Removed {vendor}. {len(_vendor_list())} left. This is a local "
            f"change -- vendors.yaml needs uploading to GitHub to take effect.")


def _tool_add(name: str, pricing_url: str, category: str) -> str:
    if not str(pricing_url).startswith("http"):
        return "That is not a URL."
    if _slug(name) in {_slug(v.get("name", "")) for v in _vendor_list()}:
        return f"{name} is already tracked."

    path = _safe_vendors_path()
    block = (f"\n  - name: {str(name).strip()}\n"
             f"    pricing_url: {str(pricing_url).strip()}\n"
             f"    category: {str(category).strip()}\n"
             f"    crawl_tier: weekly\n")
    text = path.read_text("utf-8")
    marker = "\n# ---------- removed, revisit later ----------"
    path.write_text(text.replace(marker, block + marker) if marker in text
                    else text + block, encoding="utf-8")
    return (f"Added {name}. {len(_vendor_list())} tracked. Local change -- "
            f"upload vendors.yaml to GitHub for it to take effect.")


def run_tool(name: str, args: dict) -> str:
    """Execute a tool. Anything not on the list is refused here, before it
    could reach the system -- the model cannot argue its way past this."""
    if name not in ALLOWED:
        return f"Refused: '{name}' is not an available tool."
    try:
        if name == "list_vendors":
            return _tool_list_vendors()
        if name == "diagnose_vendor":
            return _tool_diagnose(args.get("vendor", ""))
        if name == "remove_vendor":
            return _tool_remove(args.get("vendor", ""))
        if name == "add_vendor":
            return _tool_add(args.get("name", ""), args.get("pricing_url", ""),
                             args.get("category", ""))
    except AskError as exc:
        return str(exc)
    except Exception as exc:
        return f"That failed: {type(exc).__name__}"
    return "Unknown tool."


# --------------------------------------------------------------- the loop

def _post(key: str, body: dict) -> dict:
    try:
        resp = requests.post(
            API_URL,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=body, timeout=90)
    except requests.RequestException as exc:
        raise AskError(f"Could not reach the API: {type(exc).__name__}") from exc

    if resp.status_code == 401:
        raise AskError("The API key was rejected. It may have been deleted.")
    if resp.status_code == 429:
        raise AskError("Rate limited. Wait a moment and ask again.")
    if resp.status_code != 200:
        raise AskError(f"API returned {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise AskError("The reply could not be read.") from exc


def ask(question: str, status: dict) -> str:
    """Put a question to Claude, letting it use its tools before answering."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AskError("No API key found. It is the same one the crawler uses "
                       "\u2014 set it with setx and reopen the console.")

    facts = {
        "companies_tracked": status.get("vendors_ok"),
        "companies_failing": status.get("vendors_bad"),
        "price_changes_recorded": status.get("changes"),
        "recording_since": status.get("since"),
        "days_recording": (status.get("advice") or {}).get("days"),
        "spend_this_month_usd": status.get("spend"),
        "website_up": status.get("site_up"),
        "last_daily_check": status.get("run_status"),
        "next_check": status.get("next_check"),
        "current_problems": [p.get("what") if isinstance(p, dict) else p
                             for p in status.get("problems", [])],
        "recent_price_changes": status.get("recent", [])[:6],
    }

    messages = [{"role": "user",
                 "content": (f"Current status:\n"
                             f"{json.dumps(facts, indent=2, default=str)}"
                             f"\n\n{question}")}]
    did = []

    for _ in range(MAX_ROUNDS):
        data = _post(key, {"model": MODEL, "max_tokens": 1200,
                           "system": BRIEF, "tools": TOOLS,
                           "messages": messages})
        blocks = data.get("content", [])

        if data.get("stop_reason") != "tool_use":
            text = "\n".join(b.get("text", "") for b in blocks
                             if b.get("type") == "text").strip()
            if did:
                text += "\n\n\u2014 " + "; ".join(did)
            return text or "No answer came back. Try rephrasing."

        messages.append({"role": "assistant", "content": blocks})
        results = []
        for b in blocks:
            if b.get("type") != "tool_use":
                continue
            out = run_tool(b.get("name", ""), b.get("input") or {})
            did.append(b.get("name", "").replace("_", " "))
            results.append({"type": "tool_result", "tool_use_id": b.get("id"),
                            "content": out})
        messages.append({"role": "user", "content": results})

    return ("I went round in circles on that one. Try asking something "
            "narrower.")
