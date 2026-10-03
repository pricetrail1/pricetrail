"""
The site's one script -- search, sorting, filtering, chart tooltips.

Everything here is progressive enhancement. Every page is complete and
correct as plain HTML: tables are full, links work, the change log is all
there. If this script never loads, nothing is missing; you just cannot filter
or sort. Nothing here fetches anything over the network -- the search index is
a small script file written at build time -- so it works from a file:// URL,
behind a strict firewall and offline.

Features, all small:

  * Header search on every page. Instant results as you type, ranked by
    company name first, then category and plan names, with a forgiving match
    for one-letter typos. Arrow keys, Enter and Escape work; "/" focuses it.
    A useful message instead of a blank box when nothing matches.
  * search.html renders the full result list for ?q=... (the header form's
    no-script fallback lands there too).
  * Sortable table columns (click or Enter on the heading). Blanks last.
  * The change log filters: type, company, category, free text, and whether
    to include reversed readings. Filters can be set from the URL
    (?type=price, ?company=intercom) so other pages can link to a view.
  * The homepage price list filters by name as you type.
  * Chart points show a tooltip on hover and keyboard focus.
"""

FILTER_JS = r"""
(function () {
  'use strict';
  var doc = document, root = doc.documentElement.getAttribute('data-root') || '';
  var INDEX = window.PT_INDEX || [];

  function norm(s) {
    return (s || '').toString().toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, ' ').trim();
  }
  // Damerau-free edit distance, capped: only "is it within 1" matters.
  function near(a, b) {
    if (Math.abs(a.length - b.length) > 1) return false;
    var i = 0, j = 0, edits = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { i++; j++; continue; }
      if (++edits > 1) return false;
      if (a.length > b.length) i++; else if (b.length > a.length) j++; else { i++; j++; }
    }
    return edits + (a.length - i) + (b.length - j) <= 1;
  }
  function score(item, q) {
    var name = item.n, words = item.w;
    if (!q) return 0;
    if (name === q) return 100;
    if (name.indexOf(q) === 0) return 90;
    if ((' ' + name).indexOf(' ' + q) >= 0) return 80;
    if (name.indexOf(q) >= 0) return 70;
    var terms = q.split(' '), total = 0;
    for (var t = 0; t < terms.length; t++) {
      var term = terms[t], best = 0;
      if (!term) continue;
      if (words.indexOf(term) >= 0) best = 40;
      else if ((' ' + words).indexOf(' ' + term) >= 0) best = 30;
      else if (term.length >= 4) {
        var ws = (name + ' ' + words).split(' ');
        for (var k = 0; k < ws.length; k++) { if (near(term, ws[k])) { best = 20; break; } }
      }
      if (!best) return 0;
      total += best;
    }
    return total / terms.length;
  }
  function search(q, limit) {
    q = norm(q);
    if (!q) return [];
    var out = [];
    var PRIORITY = {Company: 6, Category: 4, Page: 2, Compare: 0};
    for (var i = 0; i < INDEX.length; i++) {
      var s = score(INDEX[i], q);
      if (s > 0) out.push([s + (PRIORITY[INDEX[i].k] || 0), INDEX[i]]);
    }
    out.sort(function (a, b) { return b[0] - a[0] || (a[1].t > b[1].t ? 1 : -1); });
    return out.slice(0, limit || 8).map(function (p) { return p[1]; });
  }
  function esc(s) {
    return (s || '').toString().replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function resultHTML(r, id) {
    return '<a class="sr" role="option" id="' + id + '" href="' + root + r.u + '">' +
      '<b>' + esc(r.t) + '</b><span class="sr-kind">' + esc(r.k) + '</span>' +
      '<span class="sr-meta">' + esc(r.m || '') + '</span></a>';
  }
  function emptyHTML(q) {
    return '<p class="sr-empty">No company or page matches “' + esc(q) +
      '”. Try a company name such as Zendesk, or <a href="' + root +
      'all.html">browse every company</a>.</p>';
  }

  // ---------------------------------------------------------------- header search
  var form = doc.getElementById('site-search');
  var input = doc.getElementById('q');
  var pop = doc.getElementById('search-pop');
  if (form && input && pop) {
    var active = -1, items = [];
    function render() {
      var q = input.value.trim();
      items = search(q, 8);
      active = -1;
      if (!q) { pop.hidden = true; input.setAttribute('aria-expanded', 'false'); return; }
      pop.innerHTML = items.length
        ? items.map(function (r, i) { return resultHTML(r, 'sr-' + i); }).join('')
        : emptyHTML(q);
      pop.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }
    function move(d) {
      var links = pop.querySelectorAll('.sr');
      if (!links.length) return;
      active = (active + d + links.length) % links.length;
      for (var i = 0; i < links.length; i++) links[i].setAttribute('aria-selected', i === active ? 'true' : 'false');
      input.setAttribute('aria-activedescendant', links[active].id);
      links[active].scrollIntoView({block: 'nearest'});
    }
    input.addEventListener('input', render);
    input.addEventListener('focus', function () { if (input.value.trim()) render(); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Escape') { input.value = ''; pop.hidden = true; input.blur(); }
      else if (e.key === 'Enter') {
        var links = pop.querySelectorAll('.sr');
        if (links.length) { e.preventDefault(); (links[active >= 0 ? active : 0]).click(); }
      }
    });
    doc.addEventListener('click', function (e) {
      if (!form.contains(e.target)) pop.hidden = true;
    });
    doc.addEventListener('keydown', function (e) {
      var tag = (e.target.tagName || '').toLowerCase();
      if (e.key === '/' && tag !== 'input' && tag !== 'textarea' && tag !== 'select') {
        e.preventDefault(); input.focus();
      }
    });
  }

  // ---------------------------------------------------------------- search page
  var full = doc.getElementById('search-results');
  if (full) {
    var params = new URLSearchParams(location.search);
    var q0 = params.get('q') || '';
    var box = doc.getElementById('q2');
    var countEl = doc.getElementById('search-count');
    function renderFull(q) {
      var res = search(q, 50);
      if (!q.trim()) {
        full.innerHTML = '';
        countEl.textContent = 'Type a company, category or plan name.';
        return;
      }
      countEl.textContent = res.length + (res.length === 1 ? ' result' : ' results');
      full.innerHTML = res.length
        ? res.map(function (r, i) { return resultHTML(r, 'fr-' + i); }).join('')
        : emptyHTML(q);
    }
    if (box) {
      box.value = q0;
      box.addEventListener('input', function () { renderFull(box.value); });
    }
    var fallback = doc.getElementById('search-fallback');
    if (fallback) fallback.hidden = true;
    renderFull(q0);
  }

  // ---------------------------------------------------------------- sortable tables
  [].forEach.call(doc.querySelectorAll('table[data-sortable]'), function (table) {
    var heads = table.querySelectorAll('th[data-sort]');
    [].forEach.call(heads, function (th, col) {
      if (th.getAttribute('data-sort') === 'off') return;
      var idx = [].indexOf.call(th.parentNode.children, th);
      th.classList.add('sortable');
      th.tabIndex = 0;
      th.setAttribute('role', 'columnheader');
      th.setAttribute('aria-sort', 'none');
      function sort() {
        var asc = th.getAttribute('aria-sort') !== 'ascending';
        [].forEach.call(heads, function (h) { if (h !== th && h.getAttribute('data-sort') !== 'off') h.setAttribute('aria-sort', 'none'); });
        th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
        var numeric = th.getAttribute('data-sort') === 'num';
        var body = table.tBodies[0];
        var rows = [].slice.call(body.rows);
        rows.sort(function (a, b) {
          var ca = a.cells[idx], cb = b.cells[idx];
          function val(c) {
            if (!c) return '';
            return c.hasAttribute('data-v') ? c.getAttribute('data-v') : c.textContent.trim();
          }
          var va = val(ca), vb = val(cb);
          if (numeric) { va = parseFloat(va); vb = parseFloat(vb); }
          // blanks last, whichever way the column is sorted
          var ea = numeric ? isNaN(va) : (va === '' || va === '—');
          var eb = numeric ? isNaN(vb) : (vb === '' || vb === '—');
          if (ea !== eb) return ea ? 1 : -1;
          if (ea && eb) return 0;
          if (numeric) return asc ? va - vb : vb - va;
          return asc ? va.localeCompare(vb) : vb.localeCompare(va);
        });
        rows.forEach(function (r) { body.appendChild(r); });
      }
      th.addEventListener('click', sort);
      th.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sort(); } });
    });
  });

  // ---------------------------------------------------------------- homepage price filter
  var find = doc.getElementById('find');
  var scope = doc.getElementById('prices');
  if (find && scope) {
    find.hidden = false;
    var blocks = [].slice.call(scope.querySelectorAll('[data-block]'));
    var rows = [].slice.call(scope.querySelectorAll('tbody tr'));
    var findCount = doc.getElementById('find-count');
    var findEmpty = doc.getElementById('find-empty');
    rows.forEach(function (tr) { tr.setAttribute('data-name', norm(tr.textContent)); });
    function applyFind() {
      var term = norm(find.value), shown = 0;
      rows.forEach(function (tr) {
        var hit = !term || tr.getAttribute('data-name').indexOf(term) !== -1;
        tr.hidden = !hit; if (hit) shown++;
      });
      blocks.forEach(function (b) {
        b.hidden = !b.querySelector('tbody tr:not([hidden])');
      });
      if (findCount) findCount.textContent = term ? shown + ' of ' + rows.length + ' shown' : rows.length + ' companies';
      if (findEmpty) {
        findEmpty.hidden = shown !== 0;
        findEmpty.textContent = shown ? '' : 'Nothing tracked matches “' + find.value + '” — clear the box to see every company.';
      }
    }
    find.addEventListener('input', applyFind);
    find.addEventListener('keydown', function (e) { if (e.key === 'Escape') { find.value = ''; applyFind(); } });
    applyFind();
  }

  // ---------------------------------------------------------------- change log filters
  var log = doc.getElementById('change-log');
  if (log) {
    var lrows = [].slice.call(log.querySelectorAll('tbody tr[data-group]'));
    var state = {type: 'headline', company: '', cat: '', text: '', all: false};
    var p = new URLSearchParams(location.search);
    if (p.get('type')) state.type = p.get('type');
    if (p.get('company')) state.company = p.get('company');
    if (p.get('category')) state.cat = p.get('category');
    var typeBtns = [].slice.call(doc.querySelectorAll('[data-type]'));
    var selCo = doc.getElementById('f-company'), selCat = doc.getElementById('f-category');
    var txt = doc.getElementById('f-text'), allBox = doc.getElementById('f-all');
    var cnt = doc.getElementById('f-count'), clr = doc.getElementById('f-clear');
    var none = doc.getElementById('f-empty');
    var filtersEl = doc.getElementById('log-filters');
    if (filtersEl) filtersEl.hidden = false;
    if (selCo) selCo.value = state.company;
    if (selCat) selCat.value = state.cat;
    function match(tr) {
      var g = tr.getAttribute('data-group'), k = tr.getAttribute('data-kind'), st = tr.getAttribute('data-status');
      if (!state.all && st !== 'confirmed') return false;
      if (state.type === 'headline' && !(g === 'price' || (g === 'plan' && (k === 'added' || k === 'removed')))) return false;
      if (state.type === 'price' && g !== 'price') return false;
      if (state.type === 'rise' && k !== 'rise') return false;
      if (state.type === 'cut' && k !== 'cut') return false;
      if (state.type === 'plan' && g !== 'plan' && g !== 'addon') return false;
      if (state.type === 'page' && g !== 'page' && g !== 'detail') return false;
      if (state.company && tr.getAttribute('data-vendor') !== state.company) return false;
      if (state.cat && tr.getAttribute('data-cat') !== state.cat) return false;
      if (state.text && norm(tr.textContent).indexOf(norm(state.text)) === -1) return false;
      return true;
    }
    function applyLog() {
      var shown = 0;
      lrows.forEach(function (tr) { var ok = match(tr); tr.hidden = !ok; if (ok) shown++; });
      typeBtns.forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-type') === state.type ? 'true' : 'false'); });
      if (cnt) cnt.textContent = shown + (shown === 1 ? ' entry' : ' entries');
      if (none) none.hidden = shown !== 0;
      var dirty = state.type !== 'headline' || state.company || state.cat || state.text || state.all;
      if (clr) clr.hidden = !dirty;
    }
    typeBtns.forEach(function (b) { b.addEventListener('click', function () { state.type = b.getAttribute('data-type'); applyLog(); }); });
    if (selCo) selCo.addEventListener('change', function () { state.company = selCo.value; applyLog(); });
    if (selCat) selCat.addEventListener('change', function () { state.cat = selCat.value; applyLog(); });
    if (txt) txt.addEventListener('input', function () { state.text = txt.value; applyLog(); });
    if (allBox) { allBox.checked = state.all; allBox.addEventListener('change', function () { state.all = allBox.checked; applyLog(); }); }
    if (clr) clr.addEventListener('click', function () {
      state = {type: 'headline', company: '', cat: '', text: '', all: false};
      if (selCo) selCo.value = ''; if (selCat) selCat.value = ''; if (txt) txt.value = ''; if (allBox) allBox.checked = false;
      applyLog();
    });
    applyLog();
  }

  // ---------------------------------------------------------------- chart tooltips
  [].forEach.call(doc.querySelectorAll('.chart-wrap'), function (wrap) {
    var tip = doc.createElement('div');
    tip.className = 'tip'; tip.hidden = true; wrap.appendChild(tip);
    function show(e) {
      var t = e.target.getAttribute('data-tip'); if (!t) return;
      var r = wrap.getBoundingClientRect(), b = e.target.getBoundingClientRect();
      tip.textContent = t; tip.hidden = false;
      tip.style.left = (b.left + b.width / 2 - r.left) + 'px';
      tip.style.top = (b.top - r.top) + 'px';
    }
    function hide() { tip.hidden = true; }
    [].forEach.call(wrap.querySelectorAll('[data-tip]'), function (pt) {
      pt.addEventListener('mouseenter', show); pt.addEventListener('focus', show);
      pt.addEventListener('mouseleave', hide); pt.addEventListener('blur', hide);
    });
  });
})();
"""

# Kept for older imports; all styling now lives in theme.CSS.
FILTER_CSS = ""
APP_JS = FILTER_JS
