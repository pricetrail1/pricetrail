"""
What the dashboard shows, with no user interface attached.

Kept separate from dashboard.py on purpose: the window code cannot be tested
without a display, but this can, and this is where anything is likely to go
wrong. Everything here is read from public URLs -- no password, no API key,
no login. Your repository is public, so all of it is readable without
credentials.

Every function returns rather than raises. A monitoring tool that crashes
when the thing it monitors breaks is worse than no monitoring tool.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import requests

# Defaults, used when no project is passed. Every check takes a project dict
# so a second project needs no changes here -- just another entry in
# projects.yaml.
REPO = "pricetrail1/pricetrail"
SITE = "https://getpricetrail.com"
TIMEOUT = 15


def urls(project: dict | None = None) -> tuple[str, str, str]:
    """(site, raw-file base, api base) for a project."""
    site = (project or {}).get("site", SITE)
    repo = (project or {}).get("repo", REPO)
    return (site,
            f"https://raw.githubusercontent.com/{repo}/main",
            f"https://api.github.com/repos/{repo}")

# Above this, something is re-extracting when it should not be.
SPEND_ALARM = 3.0
# GitHub pauses scheduled workflows in quiet repos; this catches that early.
STALE_RUN_HOURS = 36
STALE_VENDOR_DAYS = 14


def next_check() -> str:
    """When the daily crawl is next due, in plain words."""
    now = datetime.now(timezone.utc)
    due = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    hours = (due - now).total_seconds() / 3600
    if hours < 1:
        return f"in {int(hours * 60)} minutes"
    return f"in {int(hours)} hours" if hours >= 2 else "in about an hour"


def _json(url: str, default=None):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        return r.json() if r.status_code == 200 else default
    except (requests.RequestException, ValueError):
        return default


def _text(url: str, default: str = "") -> str:
    try:
        r = requests.get(url, timeout=TIMEOUT)
        return r.text if r.status_code == 200 else default
    except requests.RequestException:
        return default


def check_site(out: dict, project: dict | None = None) -> None:
    site, _raw, _api = urls(project)
    try:
        r = requests.get(site, timeout=TIMEOUT)
        out["site_up"] = r.status_code == 200
        out["site_ms"] = int(r.elapsed.total_seconds() * 1000)
        out["site_code"] = r.status_code
        if r.status_code != 200:
            out["problems"].append(f"Site returned HTTP {r.status_code}")
    except requests.RequestException as exc:
        out.update(site_up=False, site_ms=0, site_code=0)
        out["problems"].append(f"Site unreachable ({type(exc).__name__})")


def check_run(out: dict, project: dict | None = None) -> None:
    _site, _raw, api = urls(project)
    runs = _json(f"{api}/actions/runs?per_page=1", {}) or {}
    workflow = runs.get("workflow_runs") or []
    if not workflow:
        out["run_status"] = "unknown"
        out["problems"].append("Cannot read workflow runs from GitHub")
        return

    last = workflow[0]
    out["run_status"] = last.get("conclusion") or last.get("status") or "unknown"
    out["run_when"] = (last.get("updated_at") or "")[:16].replace("T", " ")
    if out["run_status"] not in ("success", "in_progress", "queued"):
        out["problems"].append(f"Last crawl finished: {out['run_status']}")

    try:
        when = datetime.fromisoformat(
            last["updated_at"].replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
        out["run_hours"] = hours
        if hours > STALE_RUN_HOURS:
            out["problems"].append(
                f"No crawl for {hours:.0f} hours. GitHub pauses scheduled "
                f"runs in repos with no recent commits -- push anything to "
                f"wake it up.")
    except (ValueError, KeyError, TypeError):
        out["run_hours"] = None


def check_vendors(out: dict, project: dict | None = None) -> None:
    _site, raw, _api = urls(project)
    state = _json(f"{raw}/data/state.json", {}) or {}
    ok = bad = 0
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=STALE_VENDOR_DAYS)).strftime("%Y-%m-%d")

    for slug, entry in sorted(state.items()):
        status = entry.get("status", "")
        if status == "ok":
            ok += 1
        elif status:
            bad += 1
            reason = str(entry.get("last_error") or status).replace("_", " ")
            out["problems"].append(f"{slug}: {reason[:70]}")

        last = entry.get("last_checked", "")
        if last and last < cutoff:
            out["problems"].append(f"{slug}: not read since {last}")

    out["vendors_ok"], out["vendors_bad"] = ok, bad


def check_spend(out: dict, project: dict | None = None) -> None:
    _site, raw, _api = urls(project)
    spend = _json(f"{raw}/data/spend.json", {}) or {}
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    out["spend"] = float(spend.get(month, 0.0) or 0.0)
    if out["spend"] > SPEND_ALARM:
        out["problems"].append(
            f"Spend is ${out['spend']:.2f} this month, well above the usual. "
            f"Something may be re-extracting on every run.")


def check_archive(out: dict, project: dict | None = None) -> None:
    _site, raw, _api = urls(project)
    body = _text(f"{raw}/data/changes.jsonl")
    rows = []
    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out["changes"] = len(rows)

    # The recorded price moves are the entire point of the project, so the
    # console should lead with them rather than only counting them.
    rows.sort(key=lambda r: r.get("detected_at", ""), reverse=True)
    out["recent"] = [{
        "vendor": r.get("vendor", ""),
        "plan": r.get("plan") or "",
        "kind": r.get("change_type", ""),
        "old": r.get("old_value"),
        "new": r.get("new_value"),
        "when": (r.get("detected_at") or "")[:10],
        "note": r.get("note", ""),
    } for r in rows[:6]]
    out["since"] = _text(f"{raw}/data/recording-since.txt", "unknown").strip() \
        or "unknown"


# Add a check by writing a function that takes (out, project) and appending
# it here. Anything it puts in out["problems"] turns the banner red.
CHECKS = [check_site, check_run, check_vendors, check_spend, check_archive]


def collect(project: dict | None = None) -> dict:
    """Everything the dashboard shows. Never raises."""
    out: dict = {"problems": [],
                 "checked": datetime.now().strftime("%H:%M:%S")}
    for step in CHECKS:
        try:
            step(out, project)
        except Exception as exc:                      # never take the app down
            out["problems"].append(
                f"Check '{step.__name__}' failed: {type(exc).__name__}")
    out.setdefault("vendors_ok", 0)
    out.setdefault("vendors_bad", 0)
    out.setdefault("changes", 0)
    out.setdefault("spend", 0.0)
    out.setdefault("since", "unknown")
    out.setdefault("recent", [])
    out["next_check"] = next_check()
    out.setdefault("run_status", "unknown")
    out.setdefault("run_when", "")
    out["healthy"] = not out["problems"]
    return out
