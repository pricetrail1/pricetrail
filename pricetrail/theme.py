"""
The look of the site -- V2.

Brief: a research instrument, not a landing page. The reader came to answer a
question ("what does Zendesk charge, and has that moved?") and the design's
only job is to get that answer in front of them fast, and make it believable.

Principles, in priority order:

  1. The data is the visual hero. No illustrations, gradients or decoration
     compete with figures. Colour is reserved for meaning.
  2. Direction is never colour alone. A rise is red AND carries an up-glyph
     AND a plus sign; a cut is green, a down-glyph and a minus. It reads in
     greyscale and to colour-blind readers.
  3. Figures align. Every price column uses tabular figures in a monospace
     face so digits line up and compare at a glance.
  4. Honest freshness. Dates are always in view, set in the same quiet mono
     style everywhere, so "when was this true?" never needs hunting for.
  5. Fast. One stylesheet, one small script, no frameworks, no images above
     the fold. Charts are inline SVG drawn at build time.

Type: Geist for text, Geist Mono for figures, dates and labels.
Light theme by default, with a dark theme that follows the reader's system
setting -- its own colours, checked for contrast, not an inversion.
"""

# Kept as plain values so the tests can check contrast numerically.
TOKENS = {
    "paper": "#F6F7F9",
    "panel": "#FFFFFF",
    "ink": "#0B1324",
    "muted": "#556072",
    "rule": "#E3E7ED",
    "rise": "#B42318",
    "fall": "#067647",
    "link": "#1D3FCF",
    "act": "#1D3FCF",     # primary buttons; 7.4:1 against white text
    "head": "#0B1324",
}

FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Geist:wght@400..700&amp;family=Geist+Mono:wght@400..600'
    '&amp;display=swap">'
)

# The same, but well-formed XML for the RSS stylesheet (feed.xsl).
FONT_LINK_XML = (
    '<link rel="preconnect" href="https://fonts.googleapis.com"/>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Geist:wght@400..700&amp;family=Geist+Mono:wght@400..600'
    '&amp;display=swap"/>'
)

CSS = r"""
/* ================================================================ tokens */
:root{
  color-scheme:light;
  --bg:#F6F7F9; --surface:#FFFFFF; --surface-2:#F1F3F6; --surface-3:#E9EDF2;
  --ink:#0B1324; --ink-2:#344054; --muted:#556072; --faint:#8A94A6;
  --line:#E3E7ED; --line-2:#CDD4DE;
  --accent:#1D3FCF; --accent-ink:#1733A8; --accent-soft:#EDF1FE;
  --rise:#B42318; --rise-soft:#FDEDEB; --cut:#067647; --cut-soft:#E6F5EC;
  --warn:#9A4A06; --warn-soft:#FEF3E5; --info-soft:#EEF2F7;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 1px 3px rgba(16,24,40,.06);
  --shadow-lg:0 12px 32px -8px rgba(16,24,40,.18),0 4px 8px -4px rgba(16,24,40,.08);
  --radius:10px; --radius-sm:6px;
  --sans:"Geist",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --wrap:1180px;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --bg:#0A0E15; --surface:#111722; --surface-2:#161D2A; --surface-3:#1D2635;
    --ink:#E9EDF4; --ink-2:#C2CAD6; --muted:#97A2B4; --faint:#6E7A8E;
    --line:#222B3A; --line-2:#334055;
    --accent:#8EA2FF; --accent-ink:#B5C2FF; --accent-soft:#1A2240;
    --rise:#FF8F84; --rise-soft:#3A1715; --cut:#5BD69A; --cut-soft:#0F2E21;
    --warn:#F5B76B; --warn-soft:#33240F; --info-soft:#18202E;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
    --shadow:0 1px 2px rgba(0,0,0,.4); --shadow-lg:0 16px 40px -10px rgba(0,0,0,.6);
  }
}

/* ================================================================ base */
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%;scroll-padding-top:5rem}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility}
img,svg{display:block;max-width:100%}
h1,h2,h3,h4{margin:0;line-height:1.2;letter-spacing:-.015em;font-weight:650;color:var(--ink)}
p{margin:0}
a{color:var(--accent);text-decoration:none;text-underline-offset:.18em}
a:hover{text-decoration:underline}
/* Links inside running text are underlined, so they never depend on colour
   alone to be found (WCAG 1.4.1). Navigation, buttons and cards are not. */
p:not(.links-cloud):not(.all-links) a:not(.btn):not(.link-arrow),.prose a,.provenance a,.lede a{text-decoration:underline;
  text-decoration-thickness:1px;text-decoration-color:color-mix(in srgb,currentColor 45%,transparent)}
p.links-cloud a,p.all-links a{text-decoration:none}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
code{font-family:var(--mono);font-size:.88em;background:var(--surface-2);
  border:1px solid var(--line);border-radius:4px;padding:.05em .35em}
ul,ol{margin:0;padding:0}
.vh{position:absolute!important;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
  white-space:nowrap;border:0;padding:0;margin:-1px}
.skip{position:absolute;left:1rem;top:-3rem;background:var(--ink);color:var(--surface);
  padding:.5rem .8rem;border-radius:6px;z-index:100}
.skip:focus{top:.75rem}
.wrap{max-width:var(--wrap);margin:0 auto;padding:0 16px}
@media (min-width:720px){.wrap{padding:0 24px}}
.mono,td.num,time{font-family:var(--mono);font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
.small{font-size:.875rem}
.prose p{max-width:66ch;color:var(--ink-2)}
.prose p+p{margin-top:.85rem}
.prose strong{color:var(--ink)}

/* ================================================================ header */
.site-header{position:sticky;top:0;z-index:40;background:color-mix(in srgb,var(--surface) 92%,transparent);
  backdrop-filter:saturate(1.4) blur(10px);-webkit-backdrop-filter:saturate(1.4) blur(10px);
  border-bottom:1px solid var(--line)}
.hdr{display:flex;align-items:center;gap:1rem;height:60px}
.brand{display:flex;align-items:center;gap:.5rem;color:var(--ink);font-weight:700;
  letter-spacing:-.02em;font-size:1.06rem;white-space:nowrap}
.brand:hover{text-decoration:none}
.brand svg{width:22px;height:22px;color:var(--accent)}
.brand span{color:var(--muted);font-weight:500}
.nav{display:flex;gap:.15rem;margin-left:.75rem}
.nav a{color:var(--ink-2);font-size:.92rem;font-weight:500;padding:.4rem .65rem;border-radius:6px}
.nav a:hover{background:var(--surface-2);text-decoration:none;color:var(--ink)}
.nav a[aria-current="page"]{color:var(--ink);background:var(--surface-2)}
.hdr-search{margin-left:auto;position:relative;flex:0 1 320px}
.search-box{display:flex;align-items:center;gap:.45rem;background:var(--surface-2);
  border:1px solid var(--line);border-radius:8px;padding:0 .6rem;height:38px}
.search-box:focus-within{border-color:var(--accent);background:var(--surface);
  box-shadow:0 0 0 3px var(--accent-soft)}
.search-box svg{width:16px;height:16px;color:var(--muted);flex:none}
.search-box input{all:unset;flex:1;min-width:0;font-size:.92rem;color:var(--ink);height:100%}
.search-box input::placeholder{color:var(--faint)}
.kbd{font-family:var(--mono);font-size:.72rem;color:var(--muted);border:1px solid var(--line-2);
  border-radius:4px;padding:0 .3rem;line-height:1.35}
.search-pop{position:absolute;left:0;right:0;top:calc(100% + 6px);background:var(--surface);
  border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow-lg);padding:.35rem;
  max-height:min(70vh,440px);overflow:auto;z-index:60}
.search-pop[hidden]{display:none}
.sr{display:flex;align-items:center;gap:.75rem;padding:.55rem .6rem;border-radius:7px;color:var(--ink)}
.sr:hover,.sr[aria-selected="true"]{background:var(--surface-2);text-decoration:none}
.sr b{font-weight:600}
.sr .sr-meta{margin-left:auto;font-family:var(--mono);font-size:.78rem;color:var(--muted);white-space:nowrap}
.sr .sr-kind{font-size:.72rem;color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:0 .3rem}
.sr-empty{padding:.8rem .7rem;color:var(--muted);font-size:.9rem}
.sr-empty a{white-space:nowrap}
@media (max-width:860px){
  .hdr{flex-wrap:wrap;height:auto;padding:.6rem 0;row-gap:.5rem}
  .nav{order:3;width:100%;margin:0;overflow-x:auto;scrollbar-width:none}
  .nav::-webkit-scrollbar{display:none}
  .hdr-search{flex:1 1 180px}
  .kbd{display:none}
}

/* ================================================================ layout */
main{display:block;min-height:60vh}
.section{padding:2.5rem 0}
.section-tight{padding:1.5rem 0}
.sec-head{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;
  flex-wrap:wrap;margin-bottom:1rem}
.sec-head h2{font-size:1.25rem}
.sec-head p{color:var(--muted);font-size:.92rem;margin-top:.25rem;max-width:62ch}
.sec-head .aside{color:var(--muted);font-size:.875rem}
.eyebrow{font-family:var(--mono);font-size:.75rem;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);font-weight:500}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow)}
.card-pad{padding:1.25rem}
@media (min-width:720px){.card-pad{padding:1.5rem}}
.grid{display:grid;gap:1rem}
.g2{grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr))}
.g3{grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr))}
.split{display:grid;gap:2rem}
@media (min-width:980px){.split{grid-template-columns:minmax(0,1fr) 340px}}
.page-head{padding:2rem 0 1.25rem}
.page-head h1{font-size:clamp(1.6rem,3.2vw,2.3rem)}
.page-head .lede{color:var(--ink-2);font-size:1.05rem;margin-top:.6rem;max-width:68ch}
.crumbs{font-size:.85rem;color:var(--muted);margin-bottom:.9rem;display:flex;flex-wrap:wrap;gap:.35rem}
.crumbs a{color:var(--muted)}
.crumbs a:hover{color:var(--ink)}
.crumb-sep{color:var(--faint)}
.backlink{margin:0 0 .75rem;font-size:.88rem}
.backlink a{color:var(--muted)}

/* ================================================================ buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.45rem;height:42px;
  padding:0 1.05rem;border-radius:8px;font-weight:600;font-size:.94rem;border:1px solid transparent;
  cursor:pointer;font-family:inherit;white-space:nowrap}
.btn:hover{text-decoration:none}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-ink)}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .btn-primary{color:#0A0E15}}
.btn-ghost{background:var(--surface);color:var(--ink);border-color:var(--line-2)}
.btn-ghost:hover{background:var(--surface-2)}
.btn-sm{height:34px;padding:0 .75rem;font-size:.86rem}
.link-arrow{font-weight:600;font-size:.92rem;white-space:nowrap}
.link-arrow::after{content:" \2192"}

/* ================================================================ hero */
.hero{padding:3rem 0 2rem}
.hero-grid{display:grid;gap:2rem;align-items:center}
@media (min-width:980px){.hero-grid{grid-template-columns:minmax(0,1.1fr) minmax(0,.9fr);gap:3rem}}
.hero h1{font-size:clamp(2rem,4.6vw,3.25rem);letter-spacing:-.03em;line-height:1.06;max-width:16ch}
.hero .lede{font-size:clamp(1.02rem,1.6vw,1.16rem);color:var(--ink-2);margin-top:1.1rem;max-width:58ch}
.hero-actions{display:flex;flex-wrap:wrap;gap:.6rem;margin-top:1.6rem}
.fresh{display:flex;flex-wrap:wrap;gap:.4rem 1rem;margin-top:1.4rem;font-size:.84rem;color:var(--muted)}
.fresh span{display:inline-flex;align-items:center;gap:.4rem}
.dot{width:8px;height:8px;border-radius:50%;background:var(--cut);
  box-shadow:0 0 0 3px var(--cut-soft);flex:none}
.dot.warn{background:var(--warn);box-shadow:0 0 0 3px var(--warn-soft)}
.dot.bad{background:var(--rise);box-shadow:0 0 0 3px var(--rise-soft)}
.proof{padding:1.25rem 1.25rem 1rem;margin:0}
.proof-top{display:flex;justify-content:space-between;align-items:center;gap:.75rem;margin-bottom:.9rem}
.proof h2{font-size:1rem;font-weight:600}
.proof .vendor{font-size:1.35rem;font-weight:650;letter-spacing:-.02em}
.proof .plan{color:var(--muted);font-size:.92rem}
.proof .big{display:flex;align-items:baseline;flex-wrap:wrap;gap:.6rem;margin:.65rem 0 .25rem}
.proof .big .was{font-family:var(--mono);color:var(--muted);text-decoration:line-through;font-size:1.15rem}
.proof .big .now{font-family:var(--mono);font-size:2rem;font-weight:600;letter-spacing:-.02em}
.proof-foot{display:flex;justify-content:space-between;gap:.75rem;flex-wrap:wrap;border-top:1px solid var(--line);
  margin-top:.9rem;padding-top:.8rem;font-size:.84rem;color:var(--muted)}

/* ================================================================ stats */
.stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border:1px solid var(--line);
  border-radius:var(--radius);background:var(--surface);overflow:hidden}
@media (min-width:720px){.stats{grid-template-columns:repeat(var(--n,4),minmax(0,1fr))}}
.stat{padding:1rem 1.15rem;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}
.stat .v{display:block;font-size:1.6rem;font-weight:650;letter-spacing:-.02em;line-height:1.15}
.stat .l{display:block;color:var(--muted);font-size:.84rem;margin-top:.15rem}
@media (min-width:720px){.stat{border-bottom:0}.stat:last-child{border-right:0}}
@media (max-width:719px){.stat:last-child:nth-child(odd){grid-column:span 2}.stat:nth-child(2n){border-right:0}}

/* ================================================================ badges & deltas */
.badge{display:inline-flex;align-items:center;gap:.25rem;font-family:var(--mono);font-size:.76rem;
  font-weight:500;padding:.1rem .45rem;border-radius:999px;border:1px solid var(--line);
  color:var(--ink-2);background:var(--surface-2);white-space:nowrap;line-height:1.5}
.badge.up{color:var(--rise);background:var(--rise-soft);border-color:transparent}
.badge.down{color:var(--cut);background:var(--cut-soft);border-color:transparent}
.badge.warn{color:var(--warn);background:var(--warn-soft);border-color:transparent}
.badge.ok{color:var(--cut);background:var(--cut-soft);border-color:transparent}
.badge.info{background:var(--info-soft);border-color:transparent}
.badge.accent{color:var(--accent);background:var(--accent-soft);border-color:transparent}
.tag{display:inline-block;font-size:.76rem;font-weight:500;color:var(--ink-2);background:var(--surface-2);
  border:1px solid var(--line);border-radius:5px;padding:.05rem .4rem;white-space:nowrap}
.diff{display:inline-flex;align-items:baseline;gap:.4rem;flex-wrap:wrap;font-family:var(--mono);
  font-variant-numeric:tabular-nums}
.diff .was{color:var(--muted);text-decoration:line-through;text-decoration-thickness:1px}
.diff .arrow{color:var(--faint)}
.diff .now{font-weight:600;color:var(--ink)}
.diff.up .pct{color:var(--rise)} .diff.down .pct{color:var(--cut)}
.diff .pct{font-size:.85em}
.up{color:var(--rise)} .down{color:var(--cut)}

/* ================================================================ tables */
.tbl-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:.92rem}
caption{text-align:left;color:var(--muted);font-size:.85rem;padding:0 0 .5rem;caption-side:top}
th{font-weight:500;color:var(--muted);font-size:.8rem;text-align:left;padding:.6rem .75rem;
  border-bottom:1px solid var(--line-2);white-space:nowrap;background:var(--surface)}
td{padding:.7rem .75rem;border-bottom:1px solid var(--line);vertical-align:middle;color:var(--ink)}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--surface-2)}
th.num,td.num{text-align:right}
td.num{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
td .sub,.sub{display:block;font-size:.78rem;color:var(--muted);font-family:var(--sans);font-weight:400;margin-top:.1rem}
td.name a,td.plan-name a{font-weight:600;color:var(--ink)}
td.name a:hover,td.plan-name a:hover{color:var(--accent)}
td.plan-name{font-weight:600}
.card>.tbl-scroll>table th:first-child,.card>.tbl-scroll>table td:first-child{padding-left:1.25rem}
.card>.tbl-scroll>table th:last-child,.card>.tbl-scroll>table td:last-child{padding-right:1.25rem}
th[data-sort]{cursor:pointer;user-select:none}
th[data-sort]:hover{color:var(--ink)}
th.sortable::after{content:"\2195";margin-left:.3rem;color:var(--faint);font-size:.8em}
th[aria-sort="ascending"]::after{content:"\2191";color:var(--ink)}
th[aria-sort="descending"]::after{content:"\2193";color:var(--ink)}
tr.is-quiet td{color:var(--muted)}
tr.is-quiet td .diff .now{color:var(--muted);font-weight:500}
.row-link{font-size:.84rem;white-space:nowrap}

/* stacked tables on narrow screens: each row becomes a card */
@media (max-width:719px){
  table.stack thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
  table.stack,table.stack tbody,table.stack tr,table.stack td{display:block;width:100%}
  table.stack tr{padding:.75rem 1rem;border-bottom:1px solid var(--line)}
  table.stack tbody tr:last-child{border-bottom:0}
  table.stack td{border:0;padding:.18rem 0;text-align:left!important;display:flex;
    justify-content:space-between;gap:1rem;align-items:baseline}
  table.stack td:first-child{display:block;padding-bottom:.35rem;font-size:1rem}
  table.stack td[data-l]::before{content:attr(data-l);color:var(--muted);font-size:.8rem;
    font-family:var(--sans);font-weight:400;flex:none}
  table.stack td[data-l=""]::before{content:none}
  table.stack td.num{white-space:normal}
  table.stack tbody tr:hover td{background:transparent}
  .card>.tbl-scroll>table.stack td:first-child,.card>.tbl-scroll>table.stack td:last-child{padding-left:0;padding-right:0}
}

/* compact mobile layouts: price lists and change lists read as dense cards */
td.c-mob{display:none}
@media (max-width:719px){
  table.stack.compact tr,table.stack.changes tr{display:grid;grid-template-columns:minmax(0,1fr) auto;
    column-gap:1rem;row-gap:.2rem;align-items:baseline}
  table.stack.compact td,table.stack.changes td{display:block;padding:0;font-size:.84rem;color:var(--muted)}
  table.stack.compact td::before,table.stack.changes td::before{content:none!important}
  table.stack.compact td:first-child,table.stack.changes td:first-child{font-size:1rem;color:var(--ink);padding:0}
  table.stack.compact td:nth-child(even){text-align:right!important}
  table.stack.compact td:nth-child(2){font-size:1rem;color:var(--ink);font-weight:600}
  table.stack.compact td[data-l]:not(:nth-child(2))::before{content:attr(data-l) ": "!important;display:inline;font-size:inherit}
  table.stack.compact td .sub{display:inline;margin-left:.3rem}
  table.stack.changes td:first-child{grid-column:1;grid-row:1}
  table.stack.changes td.c-date{grid-column:2;grid-row:1;text-align:right!important}
  table.stack.changes td.c-plan{grid-column:1;grid-row:2;color:var(--ink-2)}
  table.stack.changes td.c-plan .sub{display:inline;margin-left:.3rem}
  table.stack.changes td.c-kind{grid-column:2;grid-row:2;text-align:right!important}
  table.stack.changes.no-co td.c-kind{grid-row:1;grid-column:2}
  table.stack.changes.no-co td.c-date{grid-row:2;grid-column:1;text-align:left!important}
  table.stack.changes td.c-hide{display:none}
  table.stack.changes td.c-mob{display:block;grid-column:1/-1;font-family:var(--mono);color:var(--ink);font-size:.92rem;margin-top:.2rem}
  table.stack.changes td.c-mob{grid-row:3}
  table.stack.changes td.c-status{grid-column:1;grid-row:4}
  table.stack.changes td.c-link{grid-column:2;grid-row:4;text-align:right!important}
  table.stack.changes td:first-child .sub{display:inline;margin-left:.35rem}
}

/* ================================================================ filters */
.filters{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;margin-bottom:1rem}
.seg{display:inline-flex;flex-wrap:wrap;background:var(--surface-2);border:1px solid var(--line);
  border-radius:8px;padding:3px;gap:2px}
.seg button{all:unset;cursor:pointer;font-size:.86rem;font-weight:500;color:var(--ink-2);
  padding:.3rem .7rem;border-radius:6px;white-space:nowrap}
.seg button:hover{color:var(--ink)}
.seg button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:var(--shadow)}
.seg button:focus-visible{outline:2px solid var(--accent)}
.seg .n{font-family:var(--mono);font-size:.76rem;color:var(--muted);margin-left:.3rem}
.field{display:inline-flex;align-items:center;gap:.4rem;background:var(--surface);border:1px solid var(--line-2);
  border-radius:8px;height:36px;padding:0 .6rem;font-size:.88rem}
.field select,.field input{all:unset;font-size:.88rem;color:var(--ink);min-width:0}
.field select{padding-right:.2rem;cursor:pointer}
.field:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.field svg{width:15px;height:15px;color:var(--muted)}
.filters .count{margin-left:auto;font-size:.85rem;color:var(--muted)}
.clear{all:unset;cursor:pointer;font-size:.85rem;color:var(--accent);font-weight:500}
.clear[hidden]{display:none}
.empty{padding:2rem 1.25rem;text-align:center;color:var(--muted)}
.empty strong{display:block;color:var(--ink);margin-bottom:.25rem}
.findbar{display:flex;gap:.75rem;align-items:center;flex-wrap:wrap;margin-bottom:1rem}
.find-count{font-size:.85rem;color:var(--muted)}
.find-empty{padding:1rem 0;color:var(--muted)}
[hidden]{display:none!important}

/* ================================================================ category blocks */
.cat-block{margin-top:1.25rem}
.cat-head{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;
  padding:1rem 1.25rem;border-bottom:1px solid var(--line)}
.cat-head h3{font-size:1.02rem}
.cat-head h3 a{color:var(--ink)}
.cat-meta{font-size:.85rem;color:var(--muted)}
.cat-meta strong{color:var(--ink);font-family:var(--mono);font-weight:600}
.basis{font-size:.78rem;color:var(--muted)}

/* ================================================================ notices */
.notice{display:flex;gap:.75rem;padding:.9rem 1rem;border-radius:var(--radius);border:1px solid var(--line);
  background:var(--info-soft);font-size:.92rem;color:var(--ink-2)}
.notice strong{color:var(--ink)}
.notice.warn{background:var(--warn-soft);border-color:color-mix(in srgb,var(--warn) 30%,transparent)}
.notice.bad,.stale-warning{background:var(--rise-soft);border-color:color-mix(in srgb,var(--rise) 30%,transparent)}
.stale-warning{display:block;padding:.9rem 1rem;border-radius:var(--radius);border:1px solid;font-size:.92rem;color:var(--ink-2)}
.notice svg{width:18px;height:18px;flex:none;margin-top:.1rem}

/* ================================================================ vendor page */
.vhead{display:flex;flex-wrap:wrap;gap:1rem 2rem;align-items:flex-end;justify-content:space-between}
.vhead h1{font-size:clamp(1.7rem,3.4vw,2.4rem)}
.vmeta{display:flex;flex-wrap:wrap;gap:.4rem .9rem;color:var(--muted);font-size:.86rem;margin-top:.6rem}
.vmeta span{display:inline-flex;gap:.35rem;align-items:center}
.facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0;border:1px solid var(--line);
  border-radius:var(--radius);background:var(--surface);overflow:hidden;margin-top:1.25rem}
@media (min-width:720px){.facts{grid-template-columns:repeat(5,minmax(0,1fr))}}
.fact{padding:.9rem 1.1rem;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}
.fact .l{font-size:.78rem;color:var(--muted);display:block}
.fact .v{font-size:1.15rem;font-weight:650;display:block;margin-top:.15rem;letter-spacing:-.01em}
.fact .v.mono{font-weight:600}
.fact .s{font-size:.76rem;color:var(--muted);display:block}
@media (min-width:720px){.fact{border-bottom:0}.fact:last-child{border-right:0}}
@media (max-width:719px){.fact:last-child:nth-child(odd){grid-column:span 2}.fact:nth-child(2n){border-right:0}}
.summary p{color:var(--ink-2);max-width:68ch}
.summary p+p{margin-top:.6rem}
details.more{border-top:1px solid var(--line)}
details.more>summary{cursor:pointer;list-style:none;padding:.85rem 1.25rem;font-weight:600;font-size:.92rem;
  display:flex;justify-content:space-between;align-items:center}
details.more>summary::-webkit-details-marker{display:none}
details.more>summary::after{content:"+";color:var(--muted);font-weight:400;font-size:1.2rem}
details.more[open]>summary::after{content:"\2212"}
details.more>.inner{padding:0 1.25rem 1.25rem}
.provenance{font-size:.84rem;color:var(--muted)}
.provenance li{list-style:none;margin:.2rem 0}
.notes{font-size:.86rem;color:var(--ink-2);background:var(--surface-2);border:1px solid var(--line);
  border-radius:8px;padding:.8rem 1rem;white-space:pre-wrap;max-width:80ch}

/* timeline */
.timeline{position:relative;padding-left:1.4rem}
.timeline::before{content:"";position:absolute;left:5px;top:.4rem;bottom:.4rem;width:2px;background:var(--line)}
.tl{position:relative;padding:.1rem 0 1.25rem}
.tl:last-child{padding-bottom:0}
.tl::before{content:"";position:absolute;left:-1.4rem;top:.38rem;width:12px;height:12px;border-radius:50%;
  background:var(--surface);border:2px solid var(--line-2)}
.tl.rise::before{border-color:var(--rise)} .tl.cut::before{border-color:var(--cut)}
.tl.plan::before{border-color:var(--accent)}
.tl:target .tl-card{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.tl-date{font-family:var(--mono);font-size:.78rem;color:var(--muted)}
.tl-card{margin-top:.3rem;border:1px solid var(--line);border-radius:8px;background:var(--surface);padding:.8rem 1rem}
.tl-title{font-weight:600}
.tl-title .plan{color:var(--muted);font-weight:500}
.ba{display:grid;grid-template-columns:1fr auto 1fr;gap:.75rem;align-items:center;margin-top:.7rem;max-width:30rem}
.ba div{background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:.5rem .75rem}
.ba .k{display:block;font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-family:var(--mono)}
.ba .v{display:block;font-family:var(--mono);font-size:1.15rem;font-weight:600}
.ba .was .v{color:var(--muted);text-decoration:line-through;text-decoration-thickness:1px}
.ba .arr{color:var(--faint)}
.tl-note{margin-top:.55rem;font-size:.84rem;color:var(--muted)}

/* ================================================================ charts */
.chart-wrap{position:relative}
.legend{display:flex;flex-wrap:wrap;gap:.35rem 1rem;font-size:.84rem;color:var(--ink-2);margin:.25rem 0 .75rem}
.legend span{display:inline-flex;align-items:center;gap:.4rem}
.legend i{width:14px;height:3px;border-radius:2px;display:inline-block}
svg.chart{width:100%;height:auto;overflow:visible}
svg.chart .grid line{stroke:var(--line);stroke-width:1}
svg.chart .axis text,svg.chart .lbl{fill:var(--muted);font-family:var(--mono);font-size:11px}
svg.chart .end-lbl{fill:var(--ink-2);font-family:var(--mono);font-size:11px}
svg.chart .ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg.chart .pt{stroke:var(--surface);stroke-width:2;cursor:pointer}
svg.chart .pt:hover,svg.chart .pt:focus{r:6}
svg.chart .mark{stroke:var(--line-2);stroke-dasharray:none;stroke-width:1}
.c1{stroke:var(--s1);fill:var(--s1)} .c2{stroke:var(--s2);fill:var(--s2)}
.c3{stroke:var(--s3);fill:var(--s3)} .c4{stroke:var(--s4);fill:var(--s4)}
.bg1{background:var(--s1)} .bg2{background:var(--s2)} .bg3{background:var(--s3)} .bg4{background:var(--s4)}
svg.chart path.ln.c1,svg.chart path.ln.c2,svg.chart path.ln.c3,svg.chart path.ln.c4{fill:none}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--surface);font-size:.8rem;
  padding:.35rem .55rem;border-radius:6px;white-space:nowrap;transform:translate(-50%,-120%);z-index:5;
  font-family:var(--mono)}
.chart-sm{display:none}
@media (max-width:719px){.chart-lg{display:none}.chart-sm{display:block}}
.chart-note{font-size:.82rem;color:var(--muted);margin-top:.5rem}
.spark{width:100%;height:56px}
.spark .line{fill:none;stroke:var(--accent);stroke-width:2}
.spark circle{fill:var(--accent)}

/* ================================================================ steps / feature rows */
.steps{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr));counter-reset:s}
.step{padding:1.25rem;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.step h3{font-size:1rem;margin:.6rem 0 .35rem}
.step p{color:var(--ink-2);font-size:.92rem}
.step .n{font-family:var(--mono);font-size:.78rem;color:var(--accent);background:var(--accent-soft);
  border-radius:5px;padding:.1rem .45rem}
.links-cloud{display:flex;flex-wrap:wrap;gap:.4rem}
.links-cloud a{font-size:.86rem;color:var(--ink-2);border:1px solid var(--line);background:var(--surface);
  border-radius:6px;padding:.25rem .55rem}
.links-cloud a:hover{border-color:var(--line-2);color:var(--ink);text-decoration:none}
.all-links{display:flex;flex-wrap:wrap;gap:.4rem}
.all-links a{font-size:.86rem;color:var(--ink-2);border:1px solid var(--line);background:var(--surface);
  border-radius:6px;padding:.25rem .55rem}
.all-group{margin-top:1.75rem}
.all-group h2{font-size:1.05rem;margin-bottom:.6rem}
.dl{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:1rem 1.25rem;
  border-bottom:1px solid var(--line)}
.dl:last-child{border-bottom:0}
.dl h3{font-size:.98rem}
.dl p{font-size:.86rem;color:var(--muted);margin-top:.15rem}

/* ================================================================ signup / cta */
.cta{display:grid;gap:1.25rem;align-items:center;padding:1.5rem}
@media (min-width:860px){.cta{grid-template-columns:1fr auto;padding:2rem}}
.cta h2{font-size:1.3rem}
.cta p{color:var(--ink-2);margin-top:.35rem;max-width:60ch}
.signup{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.75rem}
.signup input[type=email]{flex:1 1 14rem;height:42px;border:1px solid var(--line-2);border-radius:8px;
  padding:0 .8rem;font:inherit;background:var(--surface);color:var(--ink)}
.signup button{height:42px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;
  font-weight:600;padding:0 1rem;cursor:pointer}
.signup-note{font-size:.8rem;color:var(--muted);margin-top:.5rem}
.cta-strip{display:flex;flex-wrap:wrap;gap:1rem;justify-content:space-between;align-items:center;padding:1rem 1.25rem;
  border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.cta-strip span{display:block;color:var(--muted);font-size:.88rem}
.track{margin:1.25rem 0;padding:1.25rem;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.track h2{font-size:1.05rem}
.track p{color:var(--ink-2);font-size:.92rem;margin-top:.3rem}

/* ================================================================ status */
.health{display:flex;gap:1rem;align-items:center;padding:1.1rem 1.25rem}
.health .dot{width:12px;height:12px}
.health h2{font-size:1.1rem}
.health p{color:var(--muted);font-size:.9rem}
.bars{display:flex;gap:3px;align-items:flex-end;height:36px}
.bars i{flex:1;min-width:4px;border-radius:2px 2px 0 0;background:var(--cut)}
.bars i.warn{background:var(--warn)} .bars i.bad{background:var(--rise)}

/* ================================================================ footer */
.site-footer{margin-top:3rem;border-top:1px solid var(--line);background:var(--surface);padding:2.5rem 0 2rem;
  font-size:.88rem;color:var(--muted)}
.foot-grid{display:grid;gap:2rem;grid-template-columns:repeat(auto-fit,minmax(min(100%,10rem),1fr))}
.foot-grid h2{font-size:.8rem;font-weight:600;color:var(--ink);margin-bottom:.6rem}
.foot-grid ul{list-style:none}
.foot-grid li{margin:.3rem 0}
.foot-grid a{color:var(--muted)}
.foot-grid a:hover{color:var(--ink)}
.foot-brand p{margin-top:.6rem;max-width:34ch}
.disclaimer{border-top:1px solid var(--line);margin-top:2rem;padding-top:1.25rem;font-size:.8rem;max-width:90ch}
.footer-links{margin-top:.5rem}

/* ================================================================ print & motion */
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
@media print{.site-header,.site-footer,.filters,.hdr-search,.cta,.track{display:none!important}
  body{background:#fff}.card{box-shadow:none}}
@media (forced-colors:active){.badge,.tag,.btn{border:1px solid CanvasText}}
"""
