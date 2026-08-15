"""
Finding a price, and ordering the list.

Why this exists: the homepage lists 24 vendors as three category tables. To
answer "what does Zendesk charge" a first-time visitor had to read down all
three. Research on search UX is blunt about what that costs -- a findability
failure is read as a credibility failure, and the judgement forms in seconds
and is rarely revised. On a site whose entire pitch is "we are the reliable
record", being hard to look something up in undermines the claim directly.

Two controls, no more. One box that filters as you type, and column headings
that sort. Both are conventions borrowed from spreadsheets, which is what this
audience reads all day, so neither needs explaining.

Everything here is progressive enhancement: the tables are complete, correct
and readable in the HTML. If the script never runs, the page is exactly what
it was before. Nothing here fetches anything.
"""

FILTER_JS = """
(function () {
  'use strict';

  var box = document.getElementById('find');
  var count = document.getElementById('find-count');
  var empty = document.getElementById('find-empty');
  var scope = document.getElementById('prices');
  if (!scope) { return; }

  var blocks = [].slice.call(scope.querySelectorAll('[data-block]'));
  var rows = [].slice.call(scope.querySelectorAll('tbody tr'));
  rows.forEach(function (tr) {
    tr.setAttribute('data-name', (tr.textContent || '').toLowerCase());
  });

  function apply(term) {
    term = (term || '').trim().toLowerCase();
    var shown = 0;
    rows.forEach(function (tr) {
      var hit = !term || tr.getAttribute('data-name').indexOf(term) !== -1;
      tr.hidden = !hit;
      if (hit) { shown++; }
    });
    // A category with nothing left in it is noise, not information.
    blocks.forEach(function (b) {
      var any = [].slice.call(b.querySelectorAll('tbody tr')).some(
        function (tr) { return !tr.hidden; });
      b.hidden = !any;
    });
    if (count) {
      count.textContent = term
        ? shown + ' of ' + rows.length + ' shown'
        : rows.length + ' tools tracked';
    }
    // An empty result is an active signal that the site cannot help. Say what
    // to do instead of showing a blank space.
    if (empty) {
      empty.hidden = shown !== 0;
      if (shown === 0) {
        empty.textContent = 'Nothing matches \\u201c' + term +
          '\\u201d. This site tracks ' + rows.length +
          ' tools \\u2014 clear the box to see them all.';
      }
    }
  }

  if (box) {
    box.hidden = false;
    box.addEventListener('input', function () { apply(box.value); });
    // Escape clears, which is what every search box on the web does.
    box.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { box.value = ''; apply(''); }
    });
  }

  // ---- sorting ----------------------------------------------------------
  // Prices are text like "$1,299" or an em dash. Parse to a number, and send
  // anything unparseable to the bottom in both directions: a missing price is
  // not a cheap one, and ranking it first would be a lie about the vendor.
  function value(cell) {
    var raw = (cell.textContent || '').replace(/[^0-9.\\-]/g, '');
    if (raw === '' || raw === '-' || raw === '.') { return null; }
    var n = parseFloat(raw);
    return isNaN(n) ? null : n;
  }

  [].slice.call(scope.querySelectorAll('table')).forEach(function (table) {
    var heads = [].slice.call(table.querySelectorAll('thead th'));
    heads.forEach(function (th, i) {
      if (th.getAttribute('data-sort') === 'off') { return; }
      th.tabIndex = 0;
      th.setAttribute('role', 'button');
      th.classList.add('sortable');

      function sort() {
        var body = table.querySelector('tbody');
        var trs = [].slice.call(body.querySelectorAll('tr'));
        var asc = th.getAttribute('aria-sort') !== 'ascending';
        heads.forEach(function (h) { h.removeAttribute('aria-sort'); });
        th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');

        trs.sort(function (a, b) {
          var ca = a.children[i], cb = b.children[i];
          if (!ca || !cb) { return 0; }
          var na = value(ca), nb = value(cb);
          if (na === null && nb === null) { return 0; }
          if (na === null) { return 1; }   // blanks last, both ways
          if (nb === null) { return -1; }
          if (na !== nb) { return asc ? na - nb : nb - na; }
          return 0;
        });
        if (heads[i].getAttribute('data-sort') === 'text') {
          trs.sort(function (a, b) {
            var ta = (a.children[i].textContent || '').trim().toLowerCase();
            var tb = (b.children[i].textContent || '').trim().toLowerCase();
            return asc ? ta.localeCompare(tb) : tb.localeCompare(ta);
          });
        }
        trs.forEach(function (tr) { body.appendChild(tr); });
      }

      th.addEventListener('click', sort);
      th.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sort(); }
      });
    });
  });

  apply('');
})();
"""


FILTER_CSS = """
/* ---- find and sort ---- */
.findbar {
  display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
  margin-bottom: 1.25rem;
}
.findbar input {
  flex: 1 1 18rem; min-width: 0;
  font-family: var(--sans); font-size: 1rem;
  padding: 0.7rem 0.9rem;
  background: var(--panel);
  border: 1px solid var(--rule);
  color: var(--ink);
}
.findbar input:focus-visible {
  outline: 2px solid var(--link); outline-offset: -1px;
}
.findbar input::placeholder { color: var(--muted); }
.find-count {
  font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.07em;
  text-transform: uppercase; color: var(--muted); flex: 0 0 auto;
}
.find-empty {
  background: var(--panel); border: 1px solid var(--rule);
  padding: 1.25rem; color: var(--muted); max-width: 54ch;
}
th.sortable { cursor: pointer; user-select: none; }
th.sortable:hover { color: var(--ink); }
th.sortable:focus-visible { outline: 2px solid var(--link); }
th.sortable::after {
  content: "\\2195"; opacity: 0.28; margin-left: 0.35em;
  font-weight: 400;
}
th[aria-sort="ascending"]::after { content: "\\2191"; opacity: 1; }
th[aria-sort="descending"]::after { content: "\\2193"; opacity: 1; }
@media (max-width: 46rem) {
  /* The stacked layout hides the header row, so there is nothing to click. */
  th.sortable { cursor: default; }
}
"""
