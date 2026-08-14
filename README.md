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
python -m pricetrail.publish --force        # re-extract everything (see below)
python -m pricetrail.report review          # things needing your judgement
python -m pricetrail.report digest --days 7 # your newsletter
```

**Run `--force` after any change to the extraction prompt in `extract.py`.**
The hash gate means an unchanged page is never re-read, so a better prompt
would otherwise only reach a vendor the next time that vendor edits their
page -- possibly months away. A forced run costs one full extraction of every
vendor (about 20p at 24 vendors) and applies the improvement immediately.

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

**Nothing is published on one reading.** A change has to appear on two
consecutive runs saying the same thing before it reaches the change log. The
first live run proved why: Intercom was read as $29, then $19, then $29 again,
and Mailchimp appeared to pull its pricing when the page had simply half-
loaded. None of those agreed with themselves twice.

Price moves under 1% are ignored outright. Real repricing is 5-30%; $96.00 to
$95.92 is the page being read differently, not a company changing its mind.

The cost is that a genuine change appears a day later than it otherwise would.
That is the right trade: a wrong price emailed to a customer loses them
permanently, a change arriving a day late loses nothing.

**Currencies are never converted.** Every price is stored and shown in the
currency the vendor's own page displayed. This is not laziness, it is the only
correct answer:

- Exchange rates move daily. Store prices in £ and a plan sitting untouched at
  $49 becomes £38.20, then £38.90, then £38.40 — and the crawler reports a
  price change every single morning. The 1% noise filter cannot save you,
  because currencies move more than 1% and stay moved. A price tracker that
  fires on exchange rates is worse than no price tracker.
- Pricing pages pick their currency from the visitor. `CRAWL_LOCALE` in
  `fetch.py` pins every request to one locale (`en-US`, matching where the
  GitHub Actions crawl runs) so today's reading and last month's reading are
  the same measurement. Change it only if you intend to re-baseline everything.
- **A currency flip is never reported as a price change.** When a page moves
  USD → GBP, every figure on it changes, and reporting "Zendesk cut prices 21%"
  would be a confident, specific, false claim about a real company. `diff.py`
  logs a low-confidence `currency_changed` event and compares no prices that
  round. Features, limits and new plans are still diffed normally.
- Category medians are computed in the **dominant currency only**. A vendor
  quoting in something else is excluded and the page says so, because a median
  over mixed currencies is not a price in any currency.
- Comparison pages refuse to rank two vendors quoted in different currencies.
  They show both figures and explain why there is no verdict.

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

## Things that are already handled

You do not need to build any of these -- they are in:

- **Weekly scheduling.** `crawl_tier: weekly` vendors are only checked on
  Mondays, which cuts the API bill by about 60%. `--all` overrides it.
- **Loud failure.** If more than half the vendors fail in a run, the run exits
  non-zero, GitHub marks it red and emails you. A crawler that dies quietly
  costs you archive days you can never recover.
- **Snapshot pruning.** Everything from the last two months is kept, then one
  per month. Without this the repo grows forever.
- **Structured data.** Every vendor page carries Product/Offer JSON-LD, and
  the homepage declares the archive as a schema.org Dataset. That is the
  single biggest SEO lever for a site made of machine-readable facts.
- **A subscribe block** on every major page. With no setup it offers RSS. Set
  a `SIGNUP_URL` repository variable to a hosted email form and it switches to
  a signup button -- no code change.
- **A weekly page** (`/week.html`) summarising the last seven days. That page
  is also the body of your newsletter when you start sending one.
- **A licence** asserting database right over the archive.

## Deliberately not built

- **A search box.** With 25 vendors the category pages are the navigation.
  Worth adding past ~100 vendors, not before, and it would mean adding
  JavaScript to a site that deliberately has none.
- **A headless browser.** Needed for slider-priced vendors (Klaviyo, Loops,
  ActiveCampaign, Groove). Costs real money and complexity -- worth it when a
  paying customer asks for those specific vendors.

## The only thing left that needs you

Email. Everything above runs without you; an audience does not build itself.

1. Sign up for a free email service (Buttondown, Beehiiv or Resend)
2. Add its hosted form URL as a `SIGNUP_URL` repository variable
3. Paste `python -m pricetrail.report digest --days 7` output in weekly

That is the whole job, and it is the step that turns visitors into people you
can eventually sell to.
