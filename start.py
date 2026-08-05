#!/usr/bin/env python3
"""
Start here.

    python3 start.py

Does every setup step that can be done without a card or an account: checks
your Python, installs what's needed, runs the tests, hunts down any pricing
URLs that have moved, and tells you exactly what is left.

Safe to run as many times as you like. It never spends money -- the crawl it
runs is a dry run, which fetches and hashes pages but never calls the paid API.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# On Windows the command is "py" (or "python"), never "python3".
PY_CMD = "py" if os.name == "nt" else "python3"

GREEN, RED, YELLOW, DIM, BOLD, OFF = (
    ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m")
    if sys.stdout.isatty() and os.name != "nt" else ("",) * 6
)

todo: list[str] = []


def say(state: str, msg: str, detail: str = "") -> None:
    mark = {"ok": f"{GREEN}OK{OFF}  ", "no": f"{RED}FAIL{OFF}",
            "warn": f"{YELLOW}TODO{OFF}", "..": f"{DIM}..{OFF}  "}[state]
    print(f"  {mark}  {msg}")
    if detail:
        for line in detail.strip().split("\n"):
            print(f"        {DIM}{line}{OFF}")


def rule(title: str) -> None:
    print(f"\n{BOLD}{title}{OFF}\n" + "-" * 58)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=ROOT, **kw)


# ------------------------------------------------------------------ checks

def check_python() -> bool:
    v = sys.version_info
    if v < (3, 10):
        say("no", f"Python {v.major}.{v.minor} is too old",
            "Install 3.10 or newer from python.org.\n"
            "On Windows, tick 'Add Python to PATH' during setup.")
        return False
    say("ok", f"Python {v.major}.{v.minor}.{v.micro}")
    return True


def install_deps() -> bool:
    try:
        import bs4, lxml, requests, yaml  # noqa: F401
        say("ok", "Dependencies already installed")
        return True
    except ImportError:
        pass

    say("..", "Installing dependencies (this takes a minute)")
    result = run([sys.executable, "-m", "pip", "install", "-q",
                  "-r", "requirements.txt"])
    if result.returncode != 0:
        # Some systems refuse a plain install; --user usually gets past it.
        result = run([sys.executable, "-m", "pip", "install", "-q", "--user",
                      "-r", "requirements.txt"])
    if result.returncode != 0:
        say("no", "Could not install dependencies",
            (result.stderr or "").strip()[:300] +
            "\n\nTry manually:  pip install -r requirements.txt")
        return False
    say("ok", "Dependencies installed")
    return True


def run_tests() -> bool:
    result = run([sys.executable, "tests/test_pipeline.py"])
    last = [ln for ln in result.stdout.strip().split("\n") if "passed" in ln]
    if result.returncode == 0:
        say("ok", last[-1] if last else "Tests passed")
        return True
    say("no", "Tests failed", "\n".join(result.stdout.split("\n")[-12:]))
    return False


def check_key() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        say("ok", "API key found")
        return True
    say("warn", "No API key yet (not needed for the dry run below)")
    todo.append(
        "Get API credits. A parent or guardian creates an account at\n"
        "     console.anthropic.com (18+, card required), tops up about \u00a310,\n"
        "     and sets a spend limit. Then, in your terminal:\n"
        f"       {'setx' if os.name == 'nt' else 'export'} "
        f"ANTHROPIC_API_KEY{'=' if os.name != 'nt' else ' '}"
        f"\"sk-ant-...\"\n"
        "     On Windows, reopen the terminal afterwards.")
    return False


def check_identity() -> None:
    text = (ROOT / "pricetrail" / "fetch.py").read_text(encoding="utf-8")
    if "YOURDOMAIN.com" in text:
        say("warn", "Crawler still uses the placeholder domain")
        todo.append(
            "Open pricetrail/fetch.py, find USER_AGENT, and replace\n"
            "     YOURDOMAIN.com with the address you'll publish at. Site\n"
            "     owners who can identify your bot rarely block it.")
    else:
        say("ok", "Crawler identifies itself with your domain")

    site = (ROOT / "pricetrail" / "site.py").read_text(encoding="utf-8")
    if 'BASE_URL = "https://example.com"' in site:
        say("warn", "Site URL is still example.com")
        todo.append(
            "Open pricetrail/site.py and set BASE_URL to your real address,\n"
            "     or the sitemap and RSS feed will point at nothing.")
    else:
        say("ok", "Site URL is set")


def dry_run() -> None:
    say("..", "Checking all pricing URLs and repairing any that moved",
        "No API key needed. Costs nothing. Takes a couple of minutes.")
    print()
    result = subprocess.run(
        [sys.executable, "-m", "pricetrail.run", "--dry-run", "--fix-urls"],
        cwd=ROOT, text=True)
    if result.returncode != 0:
        say("warn", "Crawl check did not finish cleanly",
            "Usually a network problem. Try again in a minute.")
        return
    say("ok", "URL check finished")
    todo.append(
        "Read the crawl output above. Anything still marked FAIL needs a\n"
        "     new URL: search the company name plus 'pricing', paste the real\n"
        "     link into vendors.yaml, then run this script again.")


# ------------------------------------------------------------------ main

def main() -> int:
    print(f"\n{BOLD}PriceTrail setup{OFF}")
    print("Doing everything that doesn't need a card or an account.")

    rule("1. Your machine")
    if not check_python():
        return 1
    if not install_deps():
        return 1

    rule("2. Does the code work")
    if not run_tests():
        say("no", "Stopping. The pipeline is broken, so nothing else matters.")
        return 1

    rule("3. Settings")
    check_identity()
    has_key = check_key()

    rule("4. Vendor URLs")
    dry_run()

    rule("What's left for you")
    if not todo:
        print("  Nothing. You're ready.\n")
        print(f"  {BOLD}Next:{OFF}  {PY_CMD} -m pricetrail.publish "
              f"--demo --serve\n")
        return 0

    for i, item in enumerate(todo, 1):
        print(f"  {i}. {item}\n")

    print(f"{BOLD}Right now, without waiting for anything:{OFF}")
    print(f"  {PY_CMD} -m pricetrail.publish --demo --serve")
    print(f"  {DIM}Builds the whole site with sample data so you can see it "
          f"working.{OFF}")
    if not has_key:
        print(f"\n{BOLD}Once you have an API key:{OFF}")
        print(f"  {PY_CMD} -m pricetrail.run --only help-scout --budget 0.10")
        print(f"  {DIM}One vendor, about 1p. Check the result in "
              f"data/plans/ against the live page.{OFF}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
