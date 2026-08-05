"""
The look of the site.

Design brief: this is a reference source. Its only job is to make someone
believe the number in front of them is accurate and current, and let them find
it in seconds. Everything here serves that.

Direction: a ledger, not a landing page. The audience reads spreadsheets and
changelogs for a living, so a site that looks like SaaS marketing would
undercut the claim to precision. It reads like a record instead.

Signature device: the diff. This business only exists because prices change,
and a change is old value -> new value. So every change on every page renders
as a real diff -- struck-through old figure, bold new figure, direction glyph,
percentage. One device, used everywhere, and unmistakably about change.

Colour carries meaning rather than decoration: rust for a rise, teal for a
cut. Never colour alone -- every direction also carries a glyph and a sign, so
it survives colourblindness and greyscale printing.

Type: Archivo (display and body) against IBM Plex Mono (all figures). Plex was
drawn for technical documentation, which is what this is. Every price, date and
percentage is monospace so columns align and digits are comparable at a glance.
"""

TOKENS = {
    "paper": "#F1F4F6",   # pale cool grey-blue
    "panel": "#FFFFFF",
    "ink": "#10171F",     # deep navy-charcoal
    "muted": "#67747F",
    "rule": "#D8E0E6",
    "rise": "#B4531A",    # rust  - price increases
    "fall": "#0F7A6B",    # teal  - price decreases
    "link": "#1B4B8F",
}

CSS = """
/* ---- reset ---- */
*, *::before, *::after { box-sizing: border-box; }
body, h1, h2, h3, h4, p, ul, ol, figure, table { margin: 0; padding: 0; }
ul, ol { list-style: none; }

:root {
  --paper: #F1F4F6;
  --panel: #FFFFFF;
  --ink: #10171F;
  --muted: #67747F;
  --rule: #D8E0E6;
  --rise: #B4531A;
  --fall: #0F7A6B;
  --link: #1B4B8F;

  --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
  --sans: "Archivo", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;

  --gutter: clamp(1rem, 4vw, 2.5rem);
  --measure: 76rem;
}

html { -webkit-text-size-adjust: 100%; }

body {
  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 16px;
  line-height: 1.55;
  font-variant-numeric: tabular-nums;
}

a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; text-underline-offset: 2px; }

:focus-visible {
  outline: 2px solid var(--link);
  outline-offset: 2px;
}

.wrap { max-width: var(--measure); margin: 0 auto; padding: 0 var(--gutter); }

/* ---- masthead ---- */
.masthead {
  border-bottom: 1px solid var(--rule);
  background: var(--panel);
  position: sticky; top: 0; z-index: 10;
}
.masthead .wrap {
  display: flex; align-items: baseline; gap: 1.5rem;
  padding-block: 0.85rem; flex-wrap: wrap;
}
.wordmark {
  font-weight: 800; font-size: 1.05rem; letter-spacing: -0.03em;
  color: var(--ink); text-transform: uppercase;
}
.wordmark:hover { text-decoration: none; }
.wordmark span { color: var(--rise); }
.masthead nav { display: flex; gap: 1.1rem; margin-left: auto; }
.masthead nav a {
  font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--muted);
}
.masthead nav a:hover { color: var(--ink); }

/* ---- hero ---- */
.hero { padding-block: clamp(2.5rem, 7vw, 4.5rem) 2rem; }
.hero h1 {
  font-size: clamp(2rem, 5.5vw, 3.4rem);
  font-weight: 800; letter-spacing: -0.035em; line-height: 1.05;
  max-width: 20ch;
}
.hero p.standfirst {
  margin-top: 1rem; max-width: 54ch; font-size: 1.08rem; color: var(--muted);
}

/* counters strip */
.counters {
  display: flex; flex-wrap: wrap; gap: 2.25rem;
  margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid var(--rule);
}
.counter .n {
  font-family: var(--mono); font-size: 1.75rem; font-weight: 600;
  letter-spacing: -0.02em; display: block;
}
.counter .l {
  font-family: var(--mono); font-size: 0.68rem; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted);
}

/* ---- section furniture ---- */
.section { padding-block: 2.5rem; }
.section-head {
  display: flex; align-items: baseline; gap: 1rem;
  border-bottom: 2px solid var(--ink); padding-bottom: 0.5rem;
  margin-bottom: 1.25rem;
}
.section-head h2 {
  font-size: 0.78rem; font-weight: 700; letter-spacing: 0.11em;
  text-transform: uppercase;
}
.section-head .aside {
  margin-left: auto; font-family: var(--mono); font-size: 0.72rem;
  color: var(--muted);
}

/* ---- the tape: dated change feed ---- */
.tape { border-top: 1px solid var(--rule); }
.entry {
  display: grid;
  grid-template-columns: 6.5rem 1fr;
  gap: 0 1.25rem;
  padding-block: 0.85rem;
  border-bottom: 1px solid var(--rule);
  align-items: baseline;
}
.entry:hover { background: var(--panel); }
.entry .when {
  font-family: var(--mono); font-size: 0.72rem; color: var(--muted);
  letter-spacing: 0.02em; white-space: nowrap;
}
.entry .what { font-size: 0.95rem; }
.entry .who { font-weight: 700; }
.entry .plan {
  font-family: var(--mono); font-size: 0.78rem; color: var(--muted);
}

/* ---- SIGNATURE: the diff ---- */
.diff {
  font-family: var(--mono); font-size: 0.88rem; white-space: nowrap;
  display: inline-flex; align-items: baseline; gap: 0.4rem;
}
.diff .was {
  text-decoration: line-through; text-decoration-thickness: 1px;
  color: var(--muted);
}
.diff .arrow { color: var(--muted); }
.diff .now { font-weight: 600; }
.diff .pct {
  font-size: 0.74rem; padding: 0.05rem 0.35rem; border-radius: 2px;
  font-weight: 600;
}
.diff.up .now, .diff.up .pct { color: var(--rise); }
.diff.up .pct { background: color-mix(in srgb, var(--rise) 10%, transparent); }
.diff.down .now, .diff.down .pct { color: var(--fall); }
.diff.down .pct { background: color-mix(in srgb, var(--fall) 10%, transparent); }

/* ---- tables ---- */
.tbl-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th {
  text-align: left; font-family: var(--mono); font-weight: 500;
  font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted); padding: 0.5rem 0.9rem 0.5rem 0;
  border-bottom: 1px solid var(--ink); white-space: nowrap;
}
td {
  padding: 0.6rem 0.9rem 0.6rem 0;
  border-bottom: 1px solid var(--rule); vertical-align: baseline;
}
td.num, th.num { font-family: var(--mono); text-align: right; padding-right: 0; }
tr:last-child td { border-bottom: none; }
.plan-name { font-weight: 600; }

.tag {
  font-family: var(--mono); font-size: 0.66rem; letter-spacing: 0.05em;
  text-transform: uppercase; color: var(--muted);
  border: 1px solid var(--rule); border-radius: 2px; padding: 0.05rem 0.3rem;
}

/* ---- panels & grids ---- */
.panel {
  background: var(--panel); border: 1px solid var(--rule);
  padding: 1.25rem 1.4rem;
}
.grid { display: grid; gap: 1px; background: var(--rule); border: 1px solid var(--rule); }
.grid-3 { grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); }
.cell { background: var(--panel); padding: 1.1rem 1.25rem; }
.cell h3 { font-size: 0.95rem; font-weight: 700; letter-spacing: -0.01em; }
.cell p { font-size: 0.85rem; color: var(--muted); margin-top: 0.3rem; }
.cell .stat {
  font-family: var(--mono); font-size: 1.4rem; font-weight: 600;
  display: block; margin-bottom: 0.15rem;
}

/* ---- sparkline ---- */
.spark { display: block; width: 100%; height: 3.5rem; overflow: visible; }
.spark path.line { fill: none; stroke: var(--ink); stroke-width: 1.5; }
.spark circle { fill: var(--rise); }
.spark .base { stroke: var(--rule); stroke-width: 1; }

/* ---- meta / provenance ---- */
.provenance {
  font-family: var(--mono); font-size: 0.72rem; color: var(--muted);
  border-left: 2px solid var(--rule); padding-left: 0.8rem; line-height: 1.7;
}

.empty {
  font-family: var(--mono); font-size: 0.85rem; color: var(--muted);
  padding: 2rem 0; border-bottom: 1px solid var(--rule);
}

/* ---- footer ---- */
footer {
  margin-top: 3rem; border-top: 1px solid var(--rule);
  background: var(--panel); padding-block: 2rem;
}
footer .wrap { display: flex; flex-wrap: wrap; gap: 1.5rem 3rem; }
footer p, footer a {
  font-family: var(--mono); font-size: 0.72rem; color: var(--muted);
  line-height: 1.8;
}
footer .disclaimer { max-width: 46ch; }

/* ---- responsive ---- */
@media (max-width: 40rem) {
  .entry { grid-template-columns: 1fr; gap: 0.2rem; }
  .counters { gap: 1.25rem 2rem; }
  .hero { padding-block: 2rem 1.5rem; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

@media print {
  .masthead { position: static; }
  body { background: #fff; }
}
"""

FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Archivo:wght@400;600;700;800&'
    'family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">'
)
