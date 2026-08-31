#!/usr/bin/env python3
"""
PriceTrail -- status console.

    Double-click 3-DASHBOARD.bat

Opens a page in your browser served from your own machine. Built as a web page
rather than a Windows window because glow, motion and decent typography are
things a browser does well and Python's built-in windowing kit cannot do at all.

Nothing is uploaded and nothing is stored. The little server exists only so the
page can ask Python for data without the browser blocking it, and it stops the
moment you close the console window.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
import webbrowser
from functools import partial

from pricetrail.health import SITE, collect

PORT = 8777


PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PriceTrail</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --void:#0B0A12; --plate:#13121C; --edge:#211F2E;
  --text:#E8E4DD; --dim:#7A7590;
  --ok:#4ADE9B; --warn:#FF9448; --idle:#8B7CF0;
  --mono:'IBM Plex Mono',monospace; --sans:'Archivo',system-ui,sans-serif;
}
body{
  background:var(--void); color:var(--text); font-family:var(--sans);
  min-height:100vh; display:flex; align-items:center; justify-content:center;
  padding:2rem; overflow-x:hidden;
}
/* faint drifting grid, for the console feel */
body::before{
  content:''; position:fixed; inset:-50%; z-index:0; opacity:.35;
  background-image:linear-gradient(var(--edge) 1px,transparent 1px),
                   linear-gradient(90deg,var(--edge) 1px,transparent 1px);
  background-size:52px 52px;
  animation:drift 60s linear infinite;
  mask-image:radial-gradient(ellipse at center,#000 10%,transparent 65%);
}
@keyframes drift{to{transform:translate(52px,52px)}}

.shell{position:relative;z-index:1;width:min(680px,100%);text-align:center}

/* ---- the ring ---- */
.ring{position:relative;width:210px;height:210px;margin:0 auto 2.2rem}
.ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.ring circle{fill:none;stroke-linecap:round}
.track{stroke:var(--edge);stroke-width:2}
.arc{
  stroke:var(--accent);stroke-width:3;
  filter:drop-shadow(0 0 14px var(--accent));
  transition:stroke .6s ease;
}
.core{
  position:absolute;inset:0;display:grid;place-items:center;
}
.core b{
  width:26px;height:26px;border-radius:50%;background:var(--accent);
  box-shadow:0 0 26px 6px var(--accent);
  animation:breathe 3.2s ease-in-out infinite;
  transition:background .6s ease;
}
@keyframes breathe{0%,100%{transform:scale(1);opacity:.9}50%{transform:scale(1.22);opacity:1}}
.scanning .arc{animation:sweep 1.1s cubic-bezier(.5,0,.5,1) infinite}
@keyframes sweep{to{stroke-dashoffset:-565}}

h1{
  font-size:clamp(1.1rem,3vw,1.5rem);font-weight:800;letter-spacing:.34em;
  text-transform:uppercase;color:var(--accent);transition:color .6s ease;
  text-shadow:0 0 30px color-mix(in srgb,var(--accent) 45%,transparent);
}
.stats{
  font-family:var(--mono);font-size:.74rem;color:var(--dim);
  letter-spacing:.1em;margin-top:1rem;
}

/* ---- panels ---- */
.advice{
  background:linear-gradient(180deg,
    color-mix(in srgb,var(--accent) 9%,var(--plate)), var(--plate));
  border:1px solid color-mix(in srgb,var(--accent) 30%,var(--edge));
  padding:1.5rem 1.7rem;text-align:left;margin-top:2.2rem
}
.advice .tag{font-family:var(--mono);font-size:.6rem;letter-spacing:.2em;
  text-transform:uppercase;color:var(--accent);display:block;margin-bottom:.6rem}
.advice h2{font-size:1.12rem;font-weight:800;letter-spacing:-.01em;
  margin-bottom:.5rem}
.advice p{font-size:.88rem;color:var(--dim);line-height:1.65}
.advice .foot{display:flex;gap:1.2rem;margin-top:1rem;flex-wrap:wrap;
  font-family:var(--mono);font-size:.62rem;letter-spacing:.12em;
  text-transform:uppercase;color:#4A465E}

.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:1px;background:var(--edge);border:1px solid var(--edge);
  border-top:none;margin-top:1px}
.cell{background:var(--plate);padding:1.1rem .9rem}
.cell .n{font-family:var(--mono);font-size:1.5rem;font-weight:500;display:block}
.cell .l{font-family:var(--mono);font-size:.6rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--dim);margin-top:.3rem;display:block}

.report{
  background:var(--plate);border:1px solid var(--edge);margin-top:1px;
  padding:1.6rem 1.7rem;text-align:left
}
.item{padding:.85rem 0;border-bottom:1px solid var(--edge)}
.item:last-child{border-bottom:none;padding-bottom:0}
.item:first-child{padding-top:0}
.item h3{font-size:.98rem;font-weight:600;margin-bottom:.3rem}
.item p{font-size:.85rem;color:var(--dim);line-height:1.6}
.fix{
  margin-top:.7rem;font-family:var(--mono);font-size:.66rem;
  letter-spacing:.14em;text-transform:uppercase;
  background:transparent;color:var(--accent);cursor:pointer;
  border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);
  padding:.5rem .9rem;transition:all .2s
}
.fix:hover{background:color-mix(in srgb,var(--accent) 14%,transparent)}
.fix:disabled{opacity:.4;cursor:default}
.feed{background:var(--plate);border:1px solid var(--edge);border-bottom:none;
  padding:1.3rem 1.7rem;text-align:left}
.feed h2{font-family:var(--mono);font-size:.62rem;letter-spacing:.18em;
  text-transform:uppercase;color:var(--dim);margin-bottom:.9rem}
.chg{display:flex;gap:.9rem;align-items:baseline;padding:.5rem 0;
  border-bottom:1px solid var(--edge);font-size:.85rem}
.chg:last-child{border-bottom:none}
.chg .when{font-family:var(--mono);font-size:.66rem;color:var(--dim);
  white-space:nowrap;min-width:5.2rem}
.chg .who{font-weight:600}
.chg .plan{font-family:var(--mono);font-size:.72rem;color:var(--dim)}
.diff{font-family:var(--mono);font-size:.8rem;white-space:nowrap}
.diff .was{text-decoration:line-through;color:var(--dim)}
.diff .now{font-weight:500}
.up .now{color:var(--warn)} .down .now{color:var(--ok)}
pre.out{
  font-family:var(--mono);font-size:.68rem;color:var(--dim);
  white-space:pre-wrap;line-height:1.55;margin-top:.9rem;
  max-height:230px;overflow:auto
}
.calm{font-size:.92rem;color:var(--dim);line-height:1.75}
.calm strong{color:var(--text);font-weight:600}

.ask{background:var(--plate);border:1px solid var(--edge);border-top:none;
  padding:1.3rem 1.7rem;text-align:left}
.ask h2{font-family:var(--mono);font-size:.6rem;letter-spacing:.2em;
  text-transform:uppercase;color:var(--dim);margin-bottom:.8rem}
.askrow{display:flex;gap:.6rem}
.ask input{
  flex:1;background:var(--void);border:1px solid var(--edge);color:var(--text);
  font-family:var(--sans);font-size:.88rem;padding:.7rem .9rem;outline:none
}
.ask input:focus{border-color:color-mix(in srgb,var(--accent) 50%,var(--edge))}
.ask button{
  background:transparent;border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);
  color:var(--accent);font-family:var(--mono);font-size:.66rem;
  letter-spacing:.14em;text-transform:uppercase;padding:0 1.1rem;cursor:pointer;
  transition:background .2s
}
.ask button:hover{background:color-mix(in srgb,var(--accent) 14%,transparent)}
.ask button:disabled{opacity:.4;cursor:default}
.answer{margin-top:1rem;font-size:.88rem;line-height:1.7;color:var(--text);
  white-space:pre-wrap}
.answer:empty{display:none}
.hints{margin-top:.7rem;display:flex;gap:.5rem;flex-wrap:wrap}
.hints span{
  font-size:.7rem;color:var(--dim);border:1px solid var(--edge);
  padding:.28rem .6rem;cursor:pointer;transition:all .2s
}
.hints span:hover{color:var(--accent);border-color:var(--accent)}

.links{margin-top:1.8rem;display:flex;gap:1.6rem;justify-content:center;flex-wrap:wrap}
.links a{
  font-family:var(--mono);font-size:.68rem;letter-spacing:.18em;
  text-transform:uppercase;color:var(--dim);text-decoration:none;
  cursor:pointer;transition:color .2s
}
.links a:hover{color:var(--accent)}
.stamp{font-family:var(--mono);font-size:.62rem;letter-spacing:.16em;
  color:#3B3850;margin-top:1.4rem}
@media(prefers-reduced-motion:reduce){*{animation:none!important}}
</style></head><body>

<div class="shell scanning" id="shell" style="--accent:var(--idle)">
  <div class="ring">
    <svg viewBox="0 0 200 200">
      <circle class="track" cx="100" cy="100" r="90"/>
      <circle class="arc" id="arc" cx="100" cy="100" r="90"
              stroke-dasharray="90 475"/>
    </svg>
    <div class="core"><b></b></div>
  </div>

  <h1 id="headline">Scanning</h1>
  <div class="stats" id="stats">&nbsp;</div>

  <div class="advice" id="advice"></div>
  <div class="grid" id="grid"></div>
  <div class="feed" id="feed" style="display:none"></div>
  <div class="report" id="report"></div>
  <div class="ask">
    <h2>Ask about your project</h2>
    <div class="askrow">
      <input id="q" placeholder="e.g. is anything worth doing today?"
             onkeydown="if(event.key==='Enter')askIt()">
      <button id="askBtn" onclick="askIt()">Ask</button>
    </div>
    <div class="hints">
      <span onclick="preset(this)">Is anything worth doing today?</span>
      <span onclick="preset(this)">Explain the problem above</span>
      <span onclick="preset(this)">Am I spending too much?</span>
      <span onclick="preset(this)">What happens next?</span>
      <span onclick="preset(this)">Fix whatever is broken</span>
    </div>
    <div class="answer" id="answer"></div>
  </div>

  <div class="links">
    <a onclick="load()">Rescan</a>
    <a id="siteLink" target="_blank">Open website</a>
    <a href="https://search.google.com/search-console" target="_blank">Search stats</a>
    <a href="https://mail.google.com/mail/u/0/#search/from%3Agithub.com+OR+pricetrail" target="_blank">Emails</a>
    <a id="ghLink" target="_blank">GitHub</a>
  </div>
  <div class="stamp" id="stamp"></div>
</div>

<script>
const $ = id => document.getElementById(id);

function tile(n, l){ return `<div class="cell"><span class="n">${n}</span><span class="l">${l}</span></div>`; }

async function load(){
  const shell = $('shell');
  shell.classList.add('scanning');
  shell.style.setProperty('--accent','var(--idle)');
  $('headline').textContent = 'Scanning';
  $('report').innerHTML = '';

  let d;
  try { d = await (await fetch('/api/status')).json(); }
  catch(e){
    shell.classList.remove('scanning');
    shell.style.setProperty('--accent','var(--warn)');
    $('headline').textContent = 'Console offline';
    $('report').innerHTML = '<p class="calm">The little server stopped. Close this tab and run 3-DASHBOARD.bat again.</p>';
    return;
  }

  shell.classList.remove('scanning');
  $('arc').setAttribute('stroke-dasharray','565');
  const good = d.healthy;
  shell.style.setProperty('--accent', good ? 'var(--ok)' : 'var(--warn)');
  $('headline').textContent = good ? 'All systems normal'
    : (d.problems.length === 1 ? 'Attention required' : d.problems.length + ' issues');

  $('stats').textContent = `${d.site_up ? 'ONLINE' : 'OFFLINE'} · ${d.site_ms}MS · SINCE ${d.since} · NEXT CHECK ${(d.next_check||'').toUpperCase()}`;
  $('grid').innerHTML =
      tile(d.vendors_ok, 'Companies tracked')
    + tile(d.changes, 'Price changes')
    + tile('$' + d.spend.toFixed(2), 'Spent this month')
    + tile(d.run_status, 'Last daily check');

  $('report').innerHTML = good
    ? `<p class="calm">Everything is running.<br><br>Your website is <strong>online</strong>,
       the daily check <strong>completed</strong>, and all ${d.vendors_ok} tracked companies
       are being read correctly.<br><br>Next check ${d.next_check}. Nothing
       requires your attention.</p>`
    : d.problems.slice(0,5).map((p,i) =>
        `<div class="item"><h3>${p.what}</h3><p>${p.means || ''}</p>` +
        (p.fix ? `<button class="fix" onclick="act(this,'${p.fix.action}','${p.fix.arg}')">${p.fix.label}</button>` : '') +
        `<pre class="out" id="out${i}"></pre></div>`).join('');

  const a = d.advice || {};
  $('advice').innerHTML =
      `<span class="tag">Do this next</span>
       <h2>${a.headline || ''}</h2>
       <p>${a.detail || ''}</p>
       <div class="foot"><span>Takes: ${a.effort || '\u2014'}</span>
       <span>Day ${a.days} \u00b7 ${a.milestone || ''}</span></div>`;

  const feed = $('feed');
  if (d.recent && d.recent.length){
    feed.style.display = 'block';
    feed.innerHTML = '<h2>Recorded price changes</h2>' + d.recent.map(c => {
      const rising = Number(c.new) > Number(c.old);
      const money = v => (v === null || v === undefined || v === '') ? '\u2014' : '$' + v;
      const shift = (c.old !== null && c.new !== null && c.old !== undefined)
        ? `<span class="diff ${rising?'up':'down'}"><span class="was">${money(c.old)}</span>
           \u2192 <span class="now">${money(c.new)}</span></span>`
        : `<span class="plan">${c.kind.replace(/_/g,' ')}</span>`;
      return `<div class="chg"><span class="when">${c.when}</span>
              <span class="who">${c.vendor}</span>
              <span class="plan">${c.plan}</span>${shift}</div>`;
    }).join('');
  } else { feed.style.display = 'none'; }

  $('siteLink').href = d.site;
  $('ghLink').href = 'https://github.com/' + d.repo + '/actions';
  lastStatus = d;
  $('stamp').textContent = 'LAST SCAN ' + d.checked;
}
let lastStatus = {};

function preset(el){ $('q').value = el.textContent; askIt(); }

async function askIt(){
  const q = $('q').value.trim();
  if(!q) return;
  const btn = $('askBtn'), box = $('answer');
  btn.disabled = true; btn.textContent = 'Thinking';
  box.textContent = '';
  try{
    const r = await (await fetch('/api/ask', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question:q})
    })).json();
    box.textContent = r.answer || r.error || 'No answer came back.';
    if(r.answer) load();          // it may have changed something
  }catch(e){
    box.textContent = 'Could not ask. Is the console window still open?';
  }
  btn.disabled = false; btn.textContent = 'Ask';
}

async function act(btn, action, arg){
  const box = btn.parentElement.querySelector('pre.out');
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = 'Working\u2026';
  try{
    const r = await (await fetch(`/api/act?action=${action}&arg=${encodeURIComponent(arg)}`)).json();
    box.textContent = r.output.trim();
    btn.textContent = 'Done';
  }catch(e){
    box.textContent = 'That did not work. Is the console window still open?';
    btn.textContent = was;
    btn.disabled = false;
  }
}

load();
setInterval(load, 300000);
</script></body></html>
"""


def plain_english(problem: str) -> dict:
    """A problem as {what happened, what it means, and what to do about it}.

    The `fix` field is what turns this from a status light into something
    useful: where there is a concrete action, the console offers a button that
    performs it rather than telling you to go and type something.
    """
    import re
    p = problem.strip()

    if "No crawl for" in p:
        n = (re.search(r"(\d+) hours", p) or [None, "a while"])[1]
        return {"what": f"The daily check hasn't run for {n} hours",
                "means": "GitHub pauses it when a project goes quiet. "
                         "Uploading anything wakes it up."}
    if "Site unreachable" in p or "Site DOWN" in p:
        return {"what": "Your website isn't loading",
                "means": "Usually temporary. If it lasts an hour, check "
                         "GitHub Pages settings."}
    if p.startswith("Site returned HTTP"):
        return {"what": f"Your website returned an error ({p.split()[-1]})",
                "means": "Usually temporary while it rebuilds."}
    if "Last crawl finished" in p:
        return {"what": "The last daily check failed",
                "means": "Open GitHub and look at the red run to see why."}
    if "Cannot read workflow runs" in p:
        return {"what": "Couldn't reach GitHub just now",
                "means": "Almost always your internet, not the project."}
    if "Spend is" in p:
        amount = re.search(r"\$[\d.]+", p)
        return {"what": f"AI spend is {amount.group() if amount else 'above normal'} this month",
                "means": "Higher than the usual 50p. Worth watching."}
    if "not read since" in p or "not read in over two weeks" in p:
        who = p.split(":")[0].strip()
        return {"what": f"{who.title()} hasn't been checked in a fortnight",
                "means": "Its pricing page has probably moved. Diagnosing it "
                         "will find the new address if there is one.",
                "fix": {"label": "Diagnose it", "action": "diagnose",
                        "arg": who}}
    if ":" in p:
        who, why = p.split(":", 1)
        why = why.strip()
        if "403" in why or "429" in why:
            return {"what": f"{who.title()} is blocking the crawler",
                    "means": "Their bot protection, not a fault your end. "
                             "Often temporary \u2014 worth leaving a day. If "
                             "it persists, drop them; 23 of 24 is fine.",
                    "fix": {"label": f"Remove {who.title()}",
                            "action": "remove", "arg": who}}
        if "404" in why or "not a pricing page" in why.lower():
            return {"what": f"{who.title()}'s pricing page has moved",
                    "means": "The old address no longer works. Diagnosing it "
                             "will hunt for the new one automatically.",
                    "fix": {"label": "Find the new page", "action": "diagnose",
                            "arg": who}}
        if "plans" in why.lower():
            return {"what": f"{who.title()}'s prices could not be read properly",
                    "means": "Usually a pricing slider, where the figure is "
                             "worked out in your browser and never appears in "
                             "the page.",
                    "fix": {"label": f"Remove {who.title()}",
                            "action": "remove", "arg": who}}
        return {"what": f"Couldn't read {who.title()}'s prices",
                "means": why,
                "fix": {"label": "Diagnose it", "action": "diagnose",
                        "arg": who}}
    return {"what": p, "means": ""}


def slugify(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def run_diagnose(name: str) -> str:
    import subprocess
    import sys as _sys
    from pathlib import Path as _P
    try:
        r = subprocess.run(
            [_sys.executable, "-m", "pricetrail.diagnose", slugify(name)],
            cwd=_P(__file__).resolve().parent, capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return f"Could not run the diagnosis: {type(exc).__name__}: {exc}"


def remove_vendor(name: str) -> str:
    """Take a vendor out of vendors.yaml. Its recorded history is kept."""
    from pathlib import Path as _P
    path = _P(__file__).resolve().parent / "vendors.yaml"
    try:
        lines = path.read_text("utf-8").split("\n")
    except OSError as exc:
        return f"Could not open vendors.yaml: {exc}"

    keep, skipping, found = [], False, False
    for line in lines:
        if line.strip().startswith("- name:"):
            this = line.split("- name:", 1)[1].strip()
            skipping = slugify(this) == slugify(name)
            found = found or skipping
        elif skipping and line.strip() and not line.startswith("    "):
            skipping = False
        if not skipping:
            keep.append(line)

    if not found:
        return f"No vendor called {name} in vendors.yaml."
    path.write_text("\n".join(keep), encoding="utf-8")
    return (f"Removed {name} from vendors.yaml.\n\n"
            f"Upload vendors.yaml to GitHub to make it live. Everything "
            f"already recorded about {name} stays in the archive \u2014 "
            f"nothing is lost.")


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/status"):
            from pricetrail.health import REPO
            d = collect()
            from pricetrail.advice import summarise
            d["advice"] = summarise(d)
            d["problems"] = [plain_english(p) for p in d["problems"]]
            d["site"], d["repo"] = SITE, REPO
            body = json.dumps(d).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/api/act"):
            from urllib.parse import parse_qs, urlparse as _u
            q = parse_qs(_u(self.path).query)
            action = (q.get("action") or [""])[0]
            arg = (q.get("arg") or [""])[0]
            if action == "diagnose":
                text = run_diagnose(arg)
            elif action == "remove":
                text = remove_vendor(arg)
            else:
                text = "Unknown action."
            out = json.dumps({"output": text}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.path.startswith("/api/ask"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            question = json.loads(self.rfile.read(length) or b"{}").get(
                "question", "").strip()
        except (ValueError, TypeError):
            question = ""

        from pricetrail.advice import summarise
        from pricetrail.ask import AskError, ask as ask_claude

        if not question:
            payload = {"error": "Ask something first."}
        else:
            status = collect()
            status["advice"] = summarise(status)
            try:
                payload = {"answer": ask_claude(question, status)}
            except AskError as exc:
                payload = {"error": str(exc)}
            except Exception as exc:
                payload = {"error": f"Something went wrong: "
                                    f"{type(exc).__name__}"}

        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass          # keep the console window quiet


def main() -> int:
    socketserver.TCPServer.allow_reuse_address = True
    for port in range(PORT, PORT + 10):
        try:
            with socketserver.TCPServer(("127.0.0.1", port), Handler) as srv:
                url = f"http://127.0.0.1:{port}"
                print(f"\n  PriceTrail console running at {url}")
                print("  Close this window to stop it.\n")
                threading.Timer(0.6, lambda: webbrowser.open(url)).start()
                srv.serve_forever()
        except OSError:
            continue
        except KeyboardInterrupt:
            return 0
    print("Could not find a free port.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
