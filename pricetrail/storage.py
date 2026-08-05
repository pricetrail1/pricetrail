"""
Where the moat physically lives.

Deliberately plain JSON files on disk rather than a database. Committed to git
after every run, the repository becomes the archive: every price on every day,
with an immutable, timestamped, tamper-evident history, hosted free.

That is the entire competitive advantage of this business stored in a folder.
Back it up somewhere that is not GitHub as soon as it matters to you.

Swap this module for Postgres when you outgrow it (roughly 1,000+ vendors).
Nothing else in the codebase needs to know.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
PLANS = DATA / "plans"
PENDING = DATA / "pending"
CHANGES = DATA / "changes.jsonl"
REVIEW = DATA / "review_queue.jsonl"
STATE = DATA / "state.json"
SPEND = DATA / "spend.json"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _ensure() -> None:
    for d in (DATA, SNAPSHOTS, PLANS, PENDING):
        d.mkdir(parents=True, exist_ok=True)


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------- raw page snapshots ----------

def save_snapshot(slug: str, cleaned_text: str) -> Path:
    """Keep the cleaned text of every page version we have ever seen.

    Only written when the hash changed, so this grows slowly -- a few hundred
    KB per vendor per year.
    """
    _ensure()
    folder = SNAPSHOTS / slug
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{today()}.txt"
    path.write_text(cleaned_text, encoding="utf-8")
    return path


# ---------- structured pricing ----------

def load_plans(slug: str) -> dict | None:
    path = PLANS / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_plans(slug: str, record: dict) -> None:
    _ensure()
    record = dict(record)
    record["captured_at"] = datetime.now(timezone.utc).isoformat()
    (PLANS / f"{slug}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------- unconfirmed readings ----------
#
# A reading waits here until the next run agrees with it. Only then does it
# become the published baseline. This is what stops a one-off misreading being
# emailed to a customer as a price change.

def load_pending(slug: str) -> dict | None:
    path = PENDING / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_pending(slug: str, record: dict) -> None:
    _ensure()
    (PENDING / f"{slug}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_pending(slug: str) -> None:
    path = PENDING / f"{slug}.json"
    if path.exists():
        path.unlink()


# ---------- change log ----------

def append_changes(changes) -> tuple[int, int]:
    """Route each change to the public log or the review queue.

    Returns (published, queued).
    """
    _ensure()
    published = queued = 0
    for change in changes:
        line = json.dumps(change.to_dict(), ensure_ascii=False)
        target = CHANGES if change.publishable else REVIEW
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if change.publishable:
            published += 1
        else:
            queued += 1
    return published, queued


def read_changes(limit: int | None = None) -> list[dict]:
    if not CHANGES.exists():
        return []
    rows = [json.loads(ln) for ln in
            CHANGES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows.sort(key=lambda r: r.get("detected_at", ""), reverse=True)
    return rows[:limit] if limit else rows


# ---------- crawl state ----------

def load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    _ensure()
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------- spend tracking ----------

def record_spend(usd: float) -> float:
    """Running API spend total, so a runaway loop cannot quietly drain the
    budget. Returns the new month-to-date total."""
    _ensure()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    data = {}
    if SPEND.exists():
        try:
            data = json.loads(SPEND.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data[month] = round(data.get(month, 0.0) + usd, 6)
    SPEND.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data[month]


def month_to_date_spend() -> float:
    if not SPEND.exists():
        return 0.0
    try:
        data = json.loads(SPEND.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0.0
    return data.get(datetime.now(timezone.utc).strftime("%Y-%m"), 0.0)
