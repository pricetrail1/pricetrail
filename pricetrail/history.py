"""
Price history, as a first-class part of the archive.

Until V2 the only history the site could show was what the change log said,
walked backwards. That loses everything the log never recorded as a "change"
-- and it is the history that is the product. The repository already held the
full record: every time a vendor's published pricing changed, the crawler
rewrote data/plans/<vendor>.json and committed it, so git history contains
every version of every vendor's prices since the first crawl.

This module turns that into data/history/<vendor>.json: one entry per distinct
published price list, with the date it took effect. It is

  * rebuilt from git when git is available (the daily GitHub run fetches full
    history for exactly this), so nothing already recorded can be lost,
  * topped up from the current data/plans files when git is not available, so
    a local preview still works,
  * append-only in effect: points are merged by their capture timestamp and
    never rewritten, and the file is regenerated deterministically.

Nothing here changes data/plans or data/changes.jsonl. Those stay exactly as
the crawler wrote them.

    python -m pricetrail.history            # sync and print a summary
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import storage

PLAN_FIELDS = ("monthly_price", "annual_price_per_month")


# ---------------------------------------------------------------- points

def _compact_plan(plan: dict) -> dict:
    from .diff import plan_key
    return {
        "name": str(plan.get("name") or "").strip(),
        "key": plan_key(str(plan.get("name") or "")),
        "m": _num(plan.get("monthly_price")),
        "a": _num(plan.get("annual_price_per_month")),
        "free": bool(plan.get("is_free")),
        "custom": bool(plan.get("is_custom_pricing")),
        "addon": bool(plan.get("is_addon")),
        "seat": bool(plan.get("is_per_seat")),
    }


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def point_from_record(record: dict) -> dict | None:
    """One history point from a stored pricing record. None if unusable."""
    if not isinstance(record, dict) or record.get("demo"):
        return None
    plans = record.get("plans")
    if not isinstance(plans, list) or not plans:
        return None
    at = str(record.get("captured_at") or "")
    if len(at) < 10:
        return None
    compact = [_compact_plan(p) for p in plans if isinstance(p, dict)
               and str(p.get("name") or "").strip()]
    compact.sort(key=lambda p: (p["addon"], p["key"]))
    return {
        "at": at,
        "date": at[:10],
        "currency": str(record.get("currency") or "").upper()[:3],
        "public": bool(record.get("pricing_is_public", True)),
        "plans": compact,
    }


def _signature(point: dict) -> str:
    """What a reader would see change. Names and figures, nothing else."""
    return json.dumps([point.get("currency"), point.get("public"),
                       [(p["key"], p["m"], p["a"], p["free"], p["custom"],
                         p["addon"]) for p in point.get("plans", [])]],
                      sort_keys=True)


def merge(points: list[dict]) -> list[dict]:
    """Deduplicate by timestamp, order by time, collapse repeats.

    Two consecutive points that show the same prices are one period of
    history, not two, so only the first is kept: its date is when that price
    list took effect.
    """
    by_at: dict[str, dict] = {}
    for p in points:
        if p and p.get("at"):
            by_at.setdefault(p["at"], p)
    out: list[dict] = []
    for p in sorted(by_at.values(), key=lambda p: p["at"]):
        if out and _signature(out[-1]) == _signature(p):
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------- disk

def path_for(slug: str) -> Path:
    return storage.history_dir() / f"{slug}.json"


def load(slug: str) -> list[dict]:
    path = path_for(slug)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    pts = data.get("points") if isinstance(data, dict) else None
    return [p for p in (pts or []) if isinstance(p, dict) and p.get("at")]


def save(slug: str, points: list[dict]) -> None:
    payload = {"vendor": slug,
               "note": "Every distinct published price list, oldest first. "
                       "Generated from git history of data/plans.",
               "points": points}
    storage.write_atomic(path_for(slug),
                         json.dumps(payload, indent=1, ensure_ascii=False))


def load_all() -> dict[str, list[dict]]:
    out = {}
    if storage.history_dir().exists():
        for path in sorted(storage.history_dir().glob("*.json")):
            pts = load(path.stem)
            if pts:
                out[path.stem] = pts
    return out


# ---------------------------------------------------------------- git

def _git(args: list[str], stdin: bytes | None = None) -> bytes | None:
    try:
        res = subprocess.run(["git", "-C", str(storage.ROOT), *args],
                             input=stdin, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def points_from_git() -> dict[str, list[dict]]:
    """Every committed version of every data/plans file, as history points.

    Returns {} when git is missing, the folder is not a repository, or the
    clone is shallow with no plan history -- callers fall back to what is on
    disk, so a missing git never breaks a build.
    """
    log = _git(["log", "--reverse", "--format=@@%H", "--name-only",
                "--", "data/plans"])
    if not log:
        return {}
    wanted: list[tuple[str, str]] = []
    sha = None
    for line in log.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("@@"):
            sha = line[2:]
        elif line.startswith("data/plans/") and line.endswith(".json") and sha:
            wanted.append((sha, line))
    if not wanted:
        return {}

    # One git process for every blob, rather than one per file per commit.
    request = "".join(f"{s}:{p}\n" for s, p in wanted).encode()
    raw = _git(["cat-file", "--batch"], stdin=request)
    if raw is None:
        return {}

    out: dict[str, list[dict]] = {}
    pos = 0
    for _sha, path in wanted:
        nl = raw.find(b"\n", pos)
        if nl < 0:
            break
        header = raw[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        blob = raw[pos:pos + size]
        pos += size + 1  # trailing newline after each blob
        try:
            record = json.loads(blob.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        point = point_from_record(record)
        if point:
            out.setdefault(Path(path).stem, []).append(point)
    return out


# ---------------------------------------------------------------- sync

def sync() -> dict:
    """Bring data/history up to date. Safe to run any number of times."""
    from_git = points_from_git()
    slugs = set(from_git)
    slugs.update(p.stem for p in storage.PLANS.glob("*.json"))
    if storage.history_dir().exists():
        slugs.update(p.stem for p in storage.history_dir().glob("*.json"))

    written = points_total = 0
    for slug in sorted(slugs):
        candidates = list(load(slug))
        candidates += from_git.get(slug, [])
        current = point_from_record(storage.load_plans(slug) or {})
        if current:
            candidates.append(current)
        merged = merge(candidates)
        if not merged:
            continue
        if merged != load(slug):
            save(slug, merged)
            written += 1
        points_total += len(merged)
    return {"vendors": len(slugs), "updated": written, "points": points_total,
            "git": bool(from_git)}


# ---------------------------------------------------------------- queries

def priced(plan: dict) -> float | None:
    """The figure a plan is shown at: monthly if published, else annual."""
    return plan.get("m") if plan.get("m") else (plan.get("a") or None)


def has_prices(point: dict) -> bool:
    return any(priced(p) for p in point.get("plans", []) if not p.get("addon"))


def last_priced_point(points: list[dict]) -> dict | None:
    """The most recent point that actually carried figures."""
    for p in reversed(points):
        if has_prices(p):
            return p
    return None


def series(points: list[dict], key: str, field: str) -> list[tuple[str, float | None]]:
    """(date, value) for one plan's monthly ("m") or annual ("a") figure."""
    out = []
    for p in points:
        val = None
        for plan in p.get("plans", []):
            if plan["key"] == key:
                val = plan.get(field)
                break
        out.append((p["date"], val))
    return out


def main() -> int:
    result = sync()
    print(f"History: {result['vendors']} vendors, {result['points']} price "
          f"lists on record, {result['updated']} files updated "
          f"({'from git history' if result['git'] else 'git not available'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
