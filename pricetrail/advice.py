"""
What to do next.

The console can already tell you what is happening. This works out what that
means and picks the single most useful thing to do about it, so you are not
left reading numbers and deciding for yourself.

Rules are ordered by urgency and the first match wins. That is deliberate: an
app that hands you six suggestions has not actually decided anything, which is
the work you wanted taken off you.

One thing it cannot see: whether anyone is visiting the site. GitHub Pages
keeps no logs, so rather than guess, the rules prompt you to look at Search
Console at the points where the answer would change what you should do.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

# Milestones worth knowing about, as (days, what it unlocks).
MILESTONES = [
    (7, "a week of history"),
    (30, "a month of history \u2014 benchmarks start meaning something"),
    (90, "three months \u2014 long enough to show a trend"),
    (365, "a year \u2014 the point competitors cannot catch up on"),
]


def days_recording(since: str) -> int:
    try:
        start = datetime.strptime(since[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0
    return max(0, (datetime.now(timezone.utc).date() - start).days)


def next_milestone(days: int) -> tuple[int, str] | None:
    for at, what in MILESTONES:
        if days < at:
            return at - days, what
    return None


def advise(d: dict) -> dict:
    """Return {headline, detail, effort} -- the one thing worth doing now."""
    days = days_recording(d.get("since", ""))
    changes = d.get("changes", 0)
    vendors = d.get("vendors_ok", 0)
    problems = d.get("problems", [])

    # --- broken things first; nothing else matters while the data is wrong ---
    if not d.get("site_up", True):
        return {"headline": "Wait, then check GitHub",
                "detail": "Your site is down. It is almost always temporary "
                          "while a rebuild finishes. If it is still down in an "
                          "hour, open GitHub and look at the last run.",
                "effort": "5 minutes"}

    if any("crawl" in str(p).lower() for p in problems):
        return {"headline": "Wake the daily check up",
                "detail": "The crawl has stopped running. GitHub pauses it "
                          "when a project goes quiet. Uploading any file to "
                          "the repository starts it again \u2014 every day it "
                          "stays paused is a day of history you cannot get "
                          "back.",
                "effort": "2 minutes"}

    if problems:
        return {"headline": "Clear the problem above",
                "detail": "One or more companies cannot be read. Use the "
                          "button on it. If a site is blocking the crawler, "
                          "removing it is the right call \u2014 wrong data is "
                          "worse than missing data.",
                "effort": "1 minute"}

    # --- healthy: now it is about what stage the project is at ---

    if days < 7:
        left = 7 - days
        return {"headline": "Nothing. Genuinely.",
                "detail": f"The archive is {days} day{'s' if days != 1 else ''} "
                          f"old and its whole value comes from age. Nothing you "
                          f"build this week beats letting it run. Come back in "
                          f"{left} day{'s' if left != 1 else ''}.",
                "effort": "none"}

    if changes == 0 and days >= 21:
        return {"headline": "Widen the net",
                "detail": f"Three weeks and no price moves recorded. That is "
                          f"normal \u2014 companies reprice a few times a year "
                          f"\u2014 but {vendors} companies is a thin net. More "
                          f"companies means more chances to catch a move, and "
                          f"more pages for people to find you through.",
                "effort": "20 minutes"}

    if changes == 0:
        return {"headline": "Keep waiting",
                "detail": "No price moves yet. With "
                          f"{vendors} companies tracked you should expect one "
                          "every couple of weeks. The first one is worth "
                          "waiting for \u2014 it will be the only structured "
                          "record of exactly when it happened.",
                "effort": "none"}

    if changes >= 1 and days < 30:
        return {"headline": "Tell one person",
                "detail": f"You have recorded {changes} price "
                          f"change{'s' if changes != 1 else ''} that nobody "
                          f"else wrote down. That is the whole argument for "
                          f"this project, and right now nobody knows it "
                          f"exists. One post in r/SaaS asking whether people "
                          f"track this would teach you more than another "
                          f"month of building.",
                "effort": "30 minutes"}

    if days >= 30:
        return {"headline": "Check whether anyone has found you",
                "detail": "A month of history and "
                          f"{changes} changes recorded. Open Search Console "
                          "(the link below) and look at impressions. If people "
                          "are arriving from Google, that is the moment email "
                          "signup starts being worth the setup \u2014 not "
                          "before.",
                "effort": "10 minutes"}

    return {"headline": "Nothing needs you",
            "detail": "Everything is running and the archive is growing. "
                      "Close this.",
            "effort": "none"}


def summarise(d: dict) -> dict:
    """Advice plus the context the console shows alongside it."""
    days = days_recording(d.get("since", ""))
    nxt = next_milestone(days)
    out = advise(d)
    out["days"] = days
    out["milestone"] = (f"{nxt[0]} days to {nxt[1]}" if nxt
                        else "past every milestone")
    return out
