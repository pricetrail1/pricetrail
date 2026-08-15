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
    "paper": "#FFFFFF",   # white. the page is a document, not a dashboard
    "panel": "#FFFFFF",
    "ink": "#0A0A0A",     # neutral near-black, no blue cast
    "muted": "#737373",
    "rule": "#E4E4E4",
    "rise": "#C2261B",    # a price went up
    "fall": "#12693F",    # a price came down
    "link": "#0A0A0A",    # links are ink; weight and underline carry them
    "act": "#1B4B8F",     # 8.6:1 on white. used ONLY on the signup button
}

CSS = """
/* ---- reset ---- */
*, *::before, *::after { box-sizing: border-box; }
[hidden] { display: none !important; }
body, h1, h2, h3, h4, p, ul, ol, figure, table { margin: 0; padding: 0; }
ul, ol { list-style: none; }

:root {
  --paper: #FFFFFF;
  --panel: #FFFFFF;
  --ink: #0A0A0A;
  --muted: #737373;
  --rule: #E4E4E4;
  --rule-soft: #EFEFEF;
  --rise: #C2261B;
  --fall: #12693F;
  --link: #0A0A0A;
  --act: #1B4B8F;

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
td a, .cat-head a { font-weight: 500; }
a:hover { text-decoration: underline; text-underline-offset: 2px; }

:focus-visible {
  outline: 2px solid var(--link);
  outline-offset: 2px;
}

.wrap { max-width: var(--measure); margin: 0 auto; padding: 0 var(--gutter); }

/* ---- masthead ---- */
.masthead {
  border-bottom: 1px solid var(--ink);
  background: #0A0A0A;
  position: sticky; top: 0; z-index: 10;
}
.masthead .wrap {
  display: flex; align-items: baseline; gap: 1.5rem;
  padding-block: 0.85rem; flex-wrap: wrap;
}
.wordmark {
  font-weight: 800; font-size: 1.05rem; letter-spacing: -0.03em;
  color: #FFFFFF; text-transform: uppercase;
}
.wordmark:hover { text-decoration: none; }
.wordmark span { color: inherit; }
.masthead nav { display: flex; gap: 1.1rem; margin-left: auto; }
.masthead nav a {
  font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.06em;
  text-transform: uppercase; color: #8A8A8A;
}
.masthead nav a:hover { color: #FFFFFF; }
.masthead .wordmark span { color: #FFFFFF; }

/* ---- hero ---- */
.hero { padding-block: clamp(2.25rem, 6vw, 3.75rem) 2rem; }
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
  display: flex; flex-wrap: wrap; gap: 0;
  margin-top: 2.25rem;
  border-top: 2px solid var(--ink);
  border-bottom: 1px solid var(--rule);
}
.counter { flex: 1 1 9rem; padding: 0.9rem 1.25rem 0.9rem 0; }
.counter .n {
  font-family: var(--mono); font-size: 2rem; font-weight: 600;
  letter-spacing: -0.03em; display: block; line-height: 1.1;
}
.counter .l {
  font-family: var(--mono); font-size: 0.68rem; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted);
}

/* ---- section furniture ---- */
.section { padding-block: 2.75rem; }
.section-head {
  display: flex; align-items: baseline; gap: 1rem;
  border-bottom: 2px solid var(--ink); padding-bottom: 0.5rem;
  margin-bottom: 1.25rem;
}
.section-head h1, .section-head h2 {
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
  padding: 0.75rem 0.9rem 0.75rem 0;
  border-bottom: 1px solid var(--rule-soft); vertical-align: baseline;
}
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: #FAFAFA; }
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


/* ---- "what is this" strip ---- */
.whatis {
  display: flex; flex-wrap: wrap; gap: 0.5rem 2rem;
  margin-top: 1.5rem; padding: 0.9rem 0 0;
  border-top: 1px solid var(--rule);
}
.whatis li { font-size: 0.92rem; color: var(--muted); }
.whatis strong { color: var(--ink); font-weight: 600; }

/* ---- the way back ---- */
.backlink { margin-bottom: 1rem; }
.backlink a {
  font-family: var(--mono); font-size: 0.75rem; letter-spacing: 0.04em;
  color: var(--muted); text-transform: uppercase;
}
.backlink a:hover { color: var(--link); }

/* the logo is a link -- make that visible on hover */
.wordmark:hover { opacity: 0.75; }

/* ---- category blocks on the homepage ---- */
.cat-block {
  background: var(--panel);
  margin-bottom: 2.25rem;
}
.cat-head {
  display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
  padding: 0 0 0.5rem;
  border-bottom: 2px solid var(--ink);
  background: var(--panel);
}
.cat-head h3 { font-size: 1rem; font-weight: 700; letter-spacing: -0.01em; }
.cat-head h3 a { color: var(--ink); }
.cat-meta {
  margin-left: auto; font-family: var(--mono); font-size: 0.75rem;
  color: var(--muted);
}
.cat-meta strong { color: var(--ink); font-weight: 600; }
.cat-block table { margin: 0; }
.cat-block th { padding-left: 1.25rem; }
.cat-block th:last-child, .cat-block td:last-child { padding-right: 1.25rem; }
.cat-block td { padding-left: 1.25rem; }
.cat-block th.num, .cat-block td.num { padding-left: 0.9rem; }

/* make the price the thing your eye lands on */
td.big {
  font-size: 1.05rem; font-weight: 600; letter-spacing: -0.01em;
}

.basis {
  display: block; font-family: var(--mono); font-size: 0.62rem;
  letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted);
  font-weight: 400; margin-top: 0.1rem;
}

/* links that look like links */
a.vlink { font-weight: 600; color: var(--ink); }
a.vlink:hover { color: var(--link); text-decoration: underline; }
tbody tr:hover { background: color-mix(in srgb, var(--paper) 45%, transparent); }

.note {
  max-width: 62ch; color: var(--muted); font-size: 0.95rem;
  background: var(--panel); border: 1px solid var(--rule);
  border-left: 3px solid var(--rule);
  padding: 1.1rem 1.25rem;
}

/* ---- tables become cards on a narrow screen ---- */
@media (max-width: 46rem) {
  table.stack thead { display: none; }
  table.stack, table.stack tbody, table.stack tr, table.stack td {
    display: block; width: 100%;
  }
  table.stack tr {
    border-bottom: 1px solid var(--rule);
    padding: 0.85rem 1.25rem;
  }
  table.stack tr:last-child { border-bottom: none; }
  table.stack td {
    border: none; padding: 0.15rem 0;
    display: flex; justify-content: space-between; gap: 1rem;
    text-align: right;
  }
  table.stack td::before {
    content: attr(data-l);
    font-family: var(--mono); font-size: 0.68rem; letter-spacing: 0.07em;
    text-transform: uppercase; color: var(--muted);
    text-align: left; flex: 0 0 auto;
  }
  table.stack td:first-child {
    display: block; text-align: left; font-size: 1.05rem;
    margin-bottom: 0.35rem;
  }
  table.stack td:first-child::before { content: none; }
  .cat-head { padding: 0 0 0.5rem; }
  .cat-meta { margin-left: 0; width: 100%; }
}

/* ---- responsive ---- */
@media (max-width: 40rem) {
  .entry { grid-template-columns: 1fr; gap: 0.2rem; }
  .counter { flex: 1 1 100%; border-right: none;
             border-bottom: 1px solid var(--rule-soft); }
  .counter:last-child { border-bottom: none; }
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

# The same font links, but valid XML: tags self-closed, attributes given
# values, ampersands escaped. Needed inside feed.xsl, which is XML rather
# than HTML and so is parsed strictly.
FONT_LINK_XML = (
    '<link rel="preconnect" href="https://fonts.googleapis.com"/>'
    '<link rel="preconnect" href="https://fonts.gstatic.com" '
    'crossorigin="anonymous"/>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Archivo:wght@400;600;700;800&amp;'
    'family=IBM+Plex+Mono:wght@400;500;600&amp;display=swap"/>'
)


CSS += """
/* ---- the one conversion point ---- */
.vh {
  position: absolute; width: 1px; height: 1px; overflow: hidden;
  clip: rect(0 0 0 0); white-space: nowrap;
}
.signup { display: flex; gap: 0.6rem; flex-wrap: wrap; max-width: 34rem; }
.signup input {
  flex: 1 1 15rem; min-width: 0;
  font-family: var(--sans); font-size: 1rem;
  padding: 0.8rem 0.9rem;
  background: var(--panel); border: 1px solid var(--ink); color: var(--ink);
}
.signup input:focus-visible { outline: 2px solid var(--act); outline-offset: 1px; }
.signup button {
  flex: 0 0 auto; cursor: pointer;
  font-family: var(--sans); font-size: 0.95rem; font-weight: 600;
  padding: 0.8rem 1.4rem;
  background: var(--act); color: #FFFFFF; border: 1px solid var(--act);
}
.signup button:hover { background: #16386B; border-color: #16386B; }
.signup button:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
.signup-note {
  margin-top: 0.7rem; max-width: 44ch;
  font-family: var(--mono); font-size: 0.72rem; line-height: 1.6;
  color: var(--muted);
}
.cta-panel { border-top: 2px solid var(--ink); }
.cta-strip {
  display: flex; gap: 1.5rem; flex-wrap: wrap; align-items: center;
  justify-content: space-between;
  padding: 1.1rem 1.25rem;
  background: var(--panel); border: 1px solid var(--rule);
  border-left: 3px solid var(--act);
}
.cta-strip strong { display: block; font-size: 1.02rem; letter-spacing: -0.01em; }
.cta-strip span {
  display: block; margin-top: 0.15rem;
  font-family: var(--mono); font-size: 0.72rem; color: var(--muted);
}
.cta-strip .signup-note { display: none; }
@media (max-width: 40rem) {
  .signup button { flex: 1 1 100%; }
}
"""
