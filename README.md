# PriceTrail

Tracks how B2B software companies change their pricing over time.

Every day it visits a list of pricing pages, works out whether anything
actually changed, and records the change permanently. Over months and years
that becomes a dataset nobody else has — because the only way to get it is to
have been recording the whole time.

---

## Start here

```bash
python3 start.py
```

One command. Checks your Python, installs what's needed, runs the tests,
finds any pricing URLs that have moved and repairs them, then prints exactly
what is left for you to do. Costs nothing, needs no API key, safe to re-run
as often as you like.

Then to see the finished site immediately:

```bash
python3 -m pricetrail.publish --demo --serve
```

That fills the archive with invented sample data, builds the whole website, and
opens it in your browser. No API key, no internet, about ten seconds.

Look around it, then delete `data/` and do it properly below.

**The demo data is fake.** Never publish it. `--demo` refuses to run if real
crawl data exists, so it cannot overwrite your archive.

---

## Doing it for real

### 1. Check nothing is broken

```bash
python3 tests/test_pipeline.py
```

Should print `32 passed, 0 failed`. This needs no internet and no API key.

### 2. Do a dry run

```bash
python -m pricetrail.run --dry-run
```

This fetches every pricing page and hashes it, but **never calls the paid API**.
Some URLs in `vendors.yaml` will be dead — companies rebrand constantly. Fix
anything marked `FIX THIS URL` and run again. Budget twenty minutes for this;
it is normal.

**Keep using `--dry-run` while you are still changing `clean.py`.** It is the
difference between a free debugging session and an expensive one.

### 3. Add your API key

Get one from the Claude Console. Then:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # macOS / Linux
setx ANTHROPIC_API_KEY "sk-ant-..."     # Windows, then reopen the terminal
```

Never put the key in a file you commit to git.

### 4. First real run

Start with two vendors so a mistake costs pennies:

```bash
python -m pricetrail.run --only intercom --only mailchimp --budget 0.10
```

Then the full list:

```bash
python -m pricetrail.run
```

### 5. Build the website

```bash
python -m pricetrail.publish --no-crawl --serve
```

Generates every page into `site/` and previews it locally. With 22 vendors
that is about 140 pages: one per vendor, one per category, and one for every
same-category pair.

Only pages with real data behind them get written. Thin auto-generated pages
with nothing unique on them are how sites get buried by search engines, so if
a vendor has no extracted pricing, it gets no page.

**Before publishing:** open `pricetrail/site.py` and set `BASE_URL` to your
real address. Otherwise the sitemap and RSS feed point at `example.com`.

### 6. Put it online, free

Push to GitHub and follow the setup steps at the top of
`.github/workflows/crawl.yml`. After that it crawls daily, rebuilds the site,
publishes it to GitHub Pages, and commits the day's data back to the repo —
all on GitHub's servers, at no cost.

---

## Daily use

```bash
python -m pricetrail.publish                # crawl, then rebuild the site
python -m pricetrail.publish --no-crawl     # rebuild the site only, no spend
python -m pricetrail.publish --serve        # preview locally
python -m pricetrail.report review          # things needing your judgement
python -m pricetrail.report digest --days 7 # your newsletter
```

The review queue is the only genuine manual job. About ten minutes a morning
at first, shrinking as you improve the prompts in `extract.py`.

---

## How it works

```
fetch → clean → hash → [unchanged? STOP] → extract → diff → store
```

The hash gate is the entire cost model. Roughly 95% of checks stop there
having cost nothing. Remove it and running 250 vendors goes from about £4 a
month to about £50 for identical output.

A change is defined at the **structured data** level, never the hash level.
Plenty of pages rewrite their HTML daily without touching a price. If you
alerted on hash changes you would spam your customers into cancelling in
week one.

| File | Job |
|---|---|
| `clean.py` | Strip page furniture so unchanged pricing gives an unchanged hash. **The hardest and most important file.** |
| `fetch.py` | Fetch politely: obeys robots.txt, rate limits, backs off on 429 |
| `extract.py` | Cleaned text → structured JSON, via forced tool use |
| `diff.py` | Two records → typed change events with confidence scores |
| `storage.py` | JSON files on disk; git history is the archive |
| `run.py` | The daily loop |
| `report.py` | Digests and the review queue |
| `theme.py` | Design tokens and the stylesheet |
| `site.py` | Data → static HTML, RSS, sitemap |
| `demo.py` | Sample data so the site is not empty on day one |
| `publish.py` | The one command that runs all of it |

---

## Costs

Haiku 4.5 is $1 per million input tokens and $5 per million output. One
extraction is roughly 0.8p.

| Vendors | Checks/month | Extractions | Cost |
|---|---|---|---|
| 22 (as shipped) | ~660 | ~130 | **~£0.90** |
| 250 | ~7,500 | ~750 | ~£5 |
| 1,000 | ~30,000 | ~2,400 | ~£15 |

`--budget` caps spend per run (default $0.50). `data/spend.json` tracks the
month-to-date total.

---

## Playing fair

This crawler identifies itself honestly, obeys robots.txt, waits three seconds
between requests to the same site, and backs off when asked. Keep it that way.
It is what keeps you unblocked and out of arguments.

Two rules that matter:

1. **Publish facts, never their words.** "Their Pro plan went from $49 to $59"
   is a fact you recorded. Copying paragraphs off their page is not. Facts are
   not copyrightable; page copy is.
2. **If a company asks you to stop, stop immediately and without arguing.**
   Remove them from `vendors.yaml` and reply politely. One annoyed vendor is
   never worth it.

Before you go live, change `USER_AGENT` in `fetch.py` to your real domain and
put a page there explaining what the bot does and how to contact you. Site
owners who can reach you rarely block you.

---

## Before you charge anyone

Payment processors require the account holder to be 18 or over — no
exceptions, and signing up with false details gets accounts closed and funds
frozen. Until that is sorted with a parent or guardian, run it free.

That costs you far less than it sounds like. The archive is the asset, and it
compounds whether or not anyone is paying yet.

---

## What to build next

1. **Weekly-tier scheduling.** `crawl_tier` is in `vendors.yaml` but `run.py`
   ignores it. Wire it up and cut your bill roughly in half.
2. **Email alerts.** Resend's free tier does 3,000 a month. This is the first
   thing anyone would actually pay for.
3. **A search box** on the site. Static JSON index, no server needed.
4. **More vendors**, one category at a time. Don't jump to 1,000 until the
   review queue for 22 is boring.

Do them in that order. Adding vendors before the review queue is quiet just
multiplies the mess.
