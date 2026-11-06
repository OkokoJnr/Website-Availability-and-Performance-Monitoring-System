#!/usr/bin/env python3
"""
WEBSITE STATUS CHECKER - WEB GUI PROTOTYPE
Standard library only. Needs website_status_checker.py in the same folder
(and test_server.py, which gives you the built-in test cases).

Run:
    python status_checker_gui.py
It opens http://127.0.0.1:8000 in your browser. Stop it with Ctrl+C.

How it works
    A web page cannot check other websites itself (browsers hide status codes
    and block cross-site requests), so this program is a tiny local web server:
      browser page  --JSON-->  this server  --calls-->  website_status_checker.py
    Every result you see comes from the same functions the command-line tool uses.

Options:
    --port N        use another port (default 8000)
    --no-browser    do not open the browser automatically
"""

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import website_status_checker as wsc
except ImportError:
    print("Could not import website_status_checker.py. Put it in the same folder as this file.")
    sys.exit(1)

try:
    import test_server
except ImportError:
    test_server = None

HISTORY_FILE = os.path.join(HERE, "gui_status_history.csv")
MAX_URLS = 100
MAX_BODY = 1_000_000
STATE = {"test_server": False, "test_port": None}


# ---------------------------------------------------------------------------
# Server-side logic (thin wrappers around website_status_checker.py)
# ---------------------------------------------------------------------------
def number(payload, key, default, low, high, cast=float):
    """Read a numeric option, falling back to a default and clamping to a safe range."""
    try:
        value = cast(payload.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def parse_text(text):
    """
    Same rules as the command-line tool: skip blank and # lines silently,
    warn (with the line number) about invalid entries and duplicates.
    Uses the checker's own validate_url(), so the rules cannot drift apart.
    """
    warnings, valid, seen = [], [], set()
    for line_no, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        reason = wsc.validate_url(entry)
        if reason:
            warnings.append({"line": line_no, "url": entry, "reason": reason})
            continue
        if entry in seen:
            warnings.append({"line": line_no, "url": entry, "reason": "duplicate ignored"})
            continue
        if len(valid) >= MAX_URLS:
            warnings.append({"line": line_no, "url": entry, "reason": f"over the {MAX_URLS} URL limit"})
            continue
        seen.add(entry)
        valid.append(entry)
    return valid, warnings


def api_check(payload):
    urls, warnings = parse_text(str(payload.get("urls_text", "")))
    if not urls:
        return {"requested": [], "results": [], "warnings": warnings, "elapsed": 0,
                "up": 0, "down": 0, "message": "No valid URLs provided to check."}

    timeout = number(payload, "timeout", 5, 0.1, 30)
    retries = int(number(payload, "retries", 1, 1, 5, int))
    delay = number(payload, "delay", 1, 0, 10)
    workers = int(number(payload, "workers", 5, 1, 20, int))
    max_redirects = int(number(payload, "max_redirects", 5, 0, 30, int))
    allow = bool(payload.get("allow_redirects"))

    started = time.perf_counter()
    results = wsc.run_concurrent_checks(urls, timeout, retries, delay, allow, max_redirects, workers)
    elapsed = round(time.perf_counter() - started, 2)

    if payload.get("save_history"):
        wsc.save_reports(results, None, None, HISTORY_FILE)   # BEFORE adding extra keys

    for r in results:
        r["cli"] = wsc.format_result_line(r)                  # the exact CLI-style line
    up = sum(1 for r in results if r["status"] == "UP")
    return {"requested": urls, "results": results, "warnings": warnings,
            "elapsed": elapsed, "up": up, "down": len(results) - up, "message": ""}


def api_stats():
    result = wsc.compute_uptime_statistics(HISTORY_FILE)
    if result is None or not result[0]:
        return {"summary": [], "slowest": None}
    summary, slowest = result
    return {"summary": summary, "slowest": list(slowest) if slowest[0] else None}


def api_info():
    return {"test_server": STATE["test_server"], "test_port": STATE["test_port"],
            "history_file": os.path.basename(HISTORY_FILE), "csv_fields": wsc.CSV_FIELDS}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_body(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_body(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def host_ok(self):
        # Refuse requests that did not come to localhost (blocks DNS-rebinding tricks).
        return (self.headers.get("Host") or "").split(":")[0].lower() in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.host_ok():
            return self.send_json({"error": "forbidden"}, 403)
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send_body(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/info":
            self.send_json(api_info())
        elif path == "/api/stats":
            self.send_json(api_stats())
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.host_ok() or self.headers.get("X-Requested-With") != "status-checker-gui":
            return self.send_json({"error": "forbidden"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_json({"error": "bad request"}, 400)
        if length > MAX_BODY:
            return self.send_json({"error": "request too large"}, 413)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            return self.send_json({"error": "invalid JSON"}, 400)

        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/check":
                self.send_json(api_check(payload))
            elif path == "/api/history/clear":
                if os.path.exists(HISTORY_FILE):
                    os.remove(HISTORY_FILE)
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:                      # never crash the server on one bad request
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)


# ---------------------------------------------------------------------------
# The page (HTML, CSS and JavaScript in one string; no external files or CDNs)
# ---------------------------------------------------------------------------
PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Website Status Checker - prototype</title>
<style>
:root{--bg:#f6f5f1;--panel:#ffffff;--ink:#1e2420;--muted:#6a716c;--line:#dedbd2;--accent:#0f6e56;--accent-ink:#ffffff;
--up:#18794a;--upbg:#e1f3e8;--down:#b3261e;--downbg:#fbe6e3;--warn:#85560a;--warnbg:#fcf0d6;--code:#f0eee7;--idle:#e8e6df}
@media (prefers-color-scheme:dark){:root{--bg:#161a18;--panel:#1f2522;--ink:#e8ebe8;--muted:#98a29b;--line:#35403a;--accent:#3cb58f;--accent-ink:#0b1a14;
--up:#6fd6a0;--upbg:#1c3a2b;--down:#ff9f97;--downbg:#4a2421;--warn:#f0c46a;--warnbg:#3d3118;--code:#2a312d;--idle:#2e3631}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
h1{font-size:18px;margin:0}h2{font-size:13px;margin:16px 0 8px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
h2:first-child{margin-top:0}h3{font-size:14px;margin:14px 0 6px}
.sub{color:var(--muted)}.pill{margin-left:auto;padding:2px 10px;border-radius:12px;font-size:12px;background:var(--idle)}
.pill.ok{background:var(--upbg);color:var(--up)}.pill.bad{background:var(--warnbg);color:var(--warn)}
main{display:grid;grid-template-columns:minmax(300px,370px) minmax(0,1fr);gap:16px;padding:16px 20px;align-items:start}
@media (max-width:900px){main{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
textarea{width:100%;font:12.5px/1.5 ui-monospace,Menlo,Consolas,monospace;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);resize:vertical}
input[type=number]{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);font:inherit}
label{display:block;font-size:12px;color:var(--muted)}label.check{display:flex;gap:8px;align-items:center;color:var(--ink);font-size:13px;margin:6px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px 10px}.row{display:flex;gap:8px;align-items:center;margin:8px 0;flex-wrap:wrap}
.inline{display:flex;gap:6px;align-items:center;color:var(--ink)}.inline input{width:70px}
button{font:inherit;cursor:pointer;border-radius:8px;padding:7px 14px;border:1px solid var(--line);background:var(--panel);color:var(--ink)}
button:hover{border-color:var(--accent)}button:disabled{opacity:.5;cursor:default}
button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
button.small{padding:3px 10px;font-size:12px}
.rule{font-size:12px;padding:8px 10px;border-radius:8px;background:var(--code);color:var(--muted);margin:8px 0}
.msg{min-height:20px;font-size:13px;color:var(--muted)}.msg.err{color:var(--down)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:-4px -4px 12px;flex-wrap:wrap}
.tab{border:none;border-bottom:2px solid transparent;border-radius:6px 6px 0 0;background:none;padding:8px 12px;color:var(--muted)}
.tab.active{color:var(--ink);border-bottom-color:var(--accent);font-weight:600}
.count{display:inline-block;min-width:18px;padding:0 5px;border-radius:9px;background:var(--warnbg);color:var(--warn);font-size:11px;text-align:center}
.tabpanel{display:none}.tabpanel.active{display:block}
.empty{color:var(--muted);padding:20px 4px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.chip{padding:6px 12px;border-radius:8px;background:var(--code);min-width:92px}.chip b{display:block;font-size:18px}.chip span{font-size:11px;color:var(--muted)}
.chip.up b{color:var(--up)}.chip.down b{color:var(--down)}
.scroll{overflow-x:auto}table.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.03em;color:var(--muted);padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.url{word-break:break-all;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.badge{display:inline-block;padding:1px 9px;border-radius:10px;font-weight:600;font-size:12px}
.b-up,.b-pass{background:var(--upbg);color:var(--up)}.b-down,.b-fail{background:var(--downbg);color:var(--down)}
.b-idle,.b-manual{background:var(--idle);color:var(--muted)}.b-run{background:var(--warnbg);color:var(--warn)}
.chg{font-weight:600;color:var(--warn)}
pre{background:var(--code);padding:10px;border-radius:8px;overflow-x:auto;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;margin:8px 0;white-space:pre-wrap;word-break:break-all}
details{margin:10px 0}summary{cursor:pointer;color:var(--muted)}
.card{border:1px solid var(--line);border-radius:10px;padding:12px;margin:10px 0}
.card .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.card .title{font-weight:600}
.tag{font-size:11px;padding:1px 7px;border-radius:8px;background:var(--code);color:var(--muted)}
.card p{margin:6px 0;color:var(--muted);font-size:13px}.card .exp{font-size:12.5px}
.sp{flex:1}
</style>
</head>
<body>
<header>
  <h1>Website Status Checker</h1>
  <span class="sub">working prototype - every result comes from the real Python checker</span>
  <span id="serverPill" class="pill">test server: ...</span>
</header>
<main>
<section class="panel">
  <h2>URLs</h2>
  <textarea id="urls" rows="12" spellcheck="false" placeholder="One URL per line. Lines starting with # are comments."></textarea>
  <div class="row"><button id="sampleBtn" class="small">Load sample file (live sites)</button><button id="clearBtn" class="small">Clear</button></div>
  <h2>Options</h2>
  <div class="grid2">
    <label>Timeout (s)<input id="timeout" type="number" min="0.1" max="30" step="0.5" value="5"></label>
    <label>Retries (attempts)<input id="retries" type="number" min="1" max="5" value="1"></label>
    <label>Delay between tries (s)<input id="delay" type="number" min="0" max="10" step="0.1" value="1"></label>
    <label>Workers (threads)<input id="workers" type="number" min="1" max="20" value="5"></label>
    <label>Max redirects<input id="maxr" type="number" min="0" max="30" value="5"></label>
  </div>
  <label class="check"><input id="allow" type="checkbox"> Allow redirects (--allow-redirects)</label>
  <label class="check"><input id="hist" type="checkbox"> Append results to history file</label>
  <div class="rule" id="ruleNote"></div>
  <div class="row"><button id="runBtn" class="primary">Run check</button></div>
  <h2>Watch mode</h2>
  <div class="row"><label class="inline">Every <input id="interval" type="number" min="1" max="3600" value="10"> s</label><button id="watchBtn">Start watch</button></div>
  <div id="msg" class="msg"></div>
</section>

<section class="panel">
  <div class="tabs" role="tablist">
    <button class="tab active" data-tab="results">Results</button>
    <button class="tab" data-tab="warnings">Warnings <span id="warnCount" class="count">0</span></button>
    <button class="tab" data-tab="watch">Watch</button>
    <button class="tab" data-tab="stats">Stats</button>
    <button class="tab" data-tab="tests">Test cases</button>
  </div>
  <div id="tab-results" class="tabpanel active"><div id="resultsBody" class="empty">Run a check to see results here.</div></div>
  <div id="tab-warnings" class="tabpanel"><div id="warningsBody" class="empty">No warnings yet.</div></div>
  <div id="tab-watch" class="tabpanel">
    <div id="watchMsg" class="msg">Press "Start watch" to re-check on a timer and see changes since the last check.</div>
    <div id="watchBody"></div><div id="stateBody"></div>
  </div>
  <div id="tab-stats" class="tabpanel">
    <div class="row"><button id="statsBtn" class="small">Refresh</button><button id="clearHistBtn" class="small">Clear history file</button><span class="sub" id="histName"></span></div>
    <div id="statsBody" class="empty">Tick "Append results to history file", run some checks, then press Refresh.</div>
  </div>
  <div id="tab-tests" class="tabpanel">
    <div class="row"><button id="runAllBtn" class="primary">Run all local test cases</button><span id="allSummary" class="sub"></span></div>
    <div class="sub" id="testNote"></div>
    <div id="cards"></div>
  </div>
</section>
</main>

<script>
"use strict";
const $ = (id) => document.getElementById(id);
let info = {test_server: false, test_port: null, csv_fields: [], history_file: ""};
let lastData = null;
let scenarios = [];

/* ---------- small DOM helpers (all text goes through textContent: safe against HTML in URLs) ---------- */
function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  kids.flat().forEach((c) => { if (c !== null && c !== undefined && c !== false) e.append(c); });
  return e;
}
function badge(status) { return h("span", {class: "badge " + (status === "UP" ? "b-up" : "b-down"), text: status}); }
function chip(label, value, cls) { return h("div", {class: "chip " + (cls || "")}, h("b", {text: String(value)}), h("span", {text: label})); }
function table(headers, rows) {
  return h("div", {class: "scroll"}, h("table", {class: "tbl"},
    h("thead", {}, h("tr", {}, headers.map((x) => h("th", {text: x})))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
}
function setMsg(text, isErr) { const m = $("msg"); m.textContent = text || ""; m.className = "msg" + (isErr ? " err" : ""); }
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
}

/* ---------- talking to the Python server ---------- */
async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST",
    headers: {"Content-Type": "application/json", "X-Requested-With": "status-checker-gui"}, body: JSON.stringify(body)};
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({error: "Bad response from server"}));
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
function readOptions() {
  return {timeout: +$("timeout").value, retries: +$("retries").value, delay: +$("delay").value,
    workers: +$("workers").value, max_redirects: +$("maxr").value,
    allow_redirects: $("allow").checked, save_history: $("hist").checked};
}
function applyOptions(o) {
  $("timeout").value = o.timeout; $("retries").value = o.retries; $("delay").value = o.delay;
  $("workers").value = o.workers; $("maxr").value = o.max_redirects;
  $("allow").checked = !!o.allow_redirects; $("hist").checked = !!o.save_history; updateRule();
}
const check = (text, opts) => api("/api/check", Object.assign({urls_text: text}, opts));

function updateRule() {
  $("ruleNote").textContent = $("allow").checked
    ? "3xx rule: 301/302/303/307/308 are followed (up to " + $("maxr").value + " hops). The final status decides UP or DOWN. More hops = DOWN, too many redirects."
    : "3xx rule: any 3xx is DOWN and is not followed. The code and redirect target are reported. 4xx and 5xx are always DOWN.";
}

/* ---------- Results and Warnings ---------- */
function resultsTable(results) {
  return table(["#", "URL", "Status", "Code", "Time", "Tries", "Redirect target", "Error", "Checked at"],
    results.map((r, i) => [String(i + 1), h("span", {class: "url", text: r.url}), badge(r.status),
      r.status_code === null ? "-" : String(r.status_code), r.response_time.toFixed(2) + "s", String(r.attempts),
      r.redirect_target ? h("span", {class: "url", text: r.redirect_target}) : "-", r.error_type || "-", r.timestamp]));
}
function toCsv(results, fields) {
  const esc = (v) => {
    if (v === null || v === undefined) return "";
    if (v === true) v = "True"; else if (v === false) v = "False";
    v = String(v);
    return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  };
  return [fields.join(",")].concat(results.map((r) => fields.map((f) => esc(r[f])).join(","))).join("\r\n") + "\r\n";
}
function download(name, mime, content) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([content], {type: mime}));
  a.download = name; document.body.append(a); a.click(); a.remove();
}
function renderResults(data) {
  const box = $("resultsBody"); box.className = ""; box.replaceChildren();
  if (!data.results.length) { box.className = "empty"; box.textContent = data.message || "Nothing to show."; return; }
  const lines = data.results.map((r) => r.cli).concat(["Summary: " + data.up + " up, " + data.down + " down"]);
  const fields = info.csv_fields;
  const clean = (r) => { const c = Object.assign({}, r); delete c.cli; return c; };
  box.append(
    h("div", {class: "chips"}, chip("sites checked", data.results.length), chip("up", data.up, "up"),
      chip("down", data.down, "down"), chip("seconds", data.elapsed), chip("warnings", data.warnings.length)),
    resultsTable(data.results),
    h("details", {open: ""}, h("summary", {text: "CLI-style output (exactly what the command line prints)"}), h("pre", {text: lines.join("\n")})),
    h("div", {class: "row"},
      h("button", {class: "small", text: "Download CSV", onclick: () => download("status_report.csv", "text/csv", toCsv(data.results, fields))}),
      h("button", {class: "small", text: "Download JSON", onclick: () => download("status_report.json", "application/json",
        JSON.stringify(data.results.map(clean), null, 4))})));
}
function renderWarnings(warnings) {
  $("warnCount").textContent = String(warnings.length);
  const box = $("warningsBody"); box.replaceChildren();
  if (!warnings.length) { box.className = "empty"; box.textContent = "No warnings: every non-comment line was a valid, unique URL."; return; }
  box.className = "";
  box.append(table(["Line", "Entry", "Reason"], warnings.map((w) => [String(w.line), h("span", {class: "url", text: w.url}), w.reason])),
    h("details", {}, h("summary", {text: "CLI-style warnings"}),
      h("pre", {text: warnings.map((w) => "[!] Warning [line " + w.line + "]: " + w.url + " - " + w.reason).join("\n")})));
}
async function runOnce() {
  $("runBtn").disabled = true; setMsg("Checking...");
  try {
    const data = await check($("urls").value, readOptions());
    lastData = data; renderResults(data); renderWarnings(data.warnings); showTab("results");
    setMsg(data.message || ("Done in " + data.elapsed + " s."));
  } catch (e) { setMsg("Error: " + e.message, true); }
  $("runBtn").disabled = false;
}

/* ---------- Watch mode (requirement 17) ---------- */
const watch = {running: false, timer: null, cycle: 0, history: {}};
function changeFor(url, status) {
  const hst = watch.history[url];
  if (!hst || !hst.length) return "first check";
  const prev = hst[hst.length - 1].status;
  return prev === status ? "no change" : prev + " -> " + status;
}
function renderStateHistory() {
  const box = $("stateBody"); box.replaceChildren();
  const urls = Object.keys(watch.history); if (!urls.length) return;
  box.append(h("h3", {text: "State history (this session, in memory)"}));
  urls.forEach((u) => {
    const e = watch.history[u], flips = [];
    for (let i = 1; i < e.length; i++) if (e[i].status !== e[i - 1].status) flips.push("[" + e[i].t + "] " + e[i - 1].status + " -> " + e[i].status);
    box.append(h("div", {}, h("span", {class: "url", text: u}), " - " + e.length + " checks, " + flips.length + " state changes"),
      flips.length ? h("pre", {text: flips.join("\n")}) : null);
  });
}
function renderWatch(data, changes) {
  const box = $("watchBody"); box.replaceChildren();
  const n = changes.filter((c) => c.includes("->")).length;
  box.append(h("h3", {text: "Check #" + watch.cycle + " at " + (data.results[0] ? data.results[0].timestamp : "")}),
    table(["URL", "Status", "Code", "Time", "Tries", "Change", "Error"], data.results.map((r, i) => [
      h("span", {class: "url", text: r.url}), badge(r.status), r.status_code === null ? "-" : String(r.status_code),
      r.response_time.toFixed(2) + "s", String(r.attempts),
      h("span", {class: changes[i].includes("->") ? "chg" : "", text: changes[i]}), r.error_type || "-"])),
    h("div", {class: "sub", text: "Changes since last check: " + n}));
  renderStateHistory();
}
async function watchCycle() {
  if (!watch.running) return;
  watch.cycle++;
  try {
    const data = await check($("urls").value, readOptions());
    if (!watch.running) return;
    const changes = data.results.map((r) => changeFor(r.url, r.status));
    data.results.forEach((r) => (watch.history[r.url] = watch.history[r.url] || []).push({t: r.timestamp, status: r.status}));
    renderWatch(data, changes); renderWarnings(data.warnings);
    $("watchMsg").textContent = "Watching every " + $("interval").value + " s. Press Stop watch to end.";
  } catch (e) { $("watchMsg").textContent = "Error: " + e.message; }
  if (watch.running) watch.timer = setTimeout(watchCycle, Math.max(1, +$("interval").value) * 1000);
}
function startWatch() {
  watch.running = true; watch.cycle = 0; watch.history = {};
  $("watchBtn").textContent = "Stop watch"; $("watchBody").replaceChildren(); $("stateBody").replaceChildren();
  $("watchMsg").textContent = "Starting..."; showTab("watch"); watchCycle();
}
function stopWatch() {
  watch.running = false; clearTimeout(watch.timer);
  $("watchBtn").textContent = "Start watch"; $("watchMsg").textContent = "Watch stopped."; renderStateHistory();
}

/* ---------- Stats (requirement 19) ---------- */
async function loadStats() {
  const box = $("statsBody"); $("histName").textContent = "file: " + info.history_file;
  try {
    const d = await api("/api/stats"); box.replaceChildren();
    if (!d.summary.length) { box.className = "empty"; box.textContent = "No history yet. Tick \"Append results to history file\" and run some checks."; return; }
    box.className = "";
    box.append(table(["URL", "Checks", "Uptime %", "Avg time (UP checks)", "Most frequent failure"],
      d.summary.map((s) => [h("span", {class: "url", text: s.url}), String(s.checks), s.uptime_pct + "%", s.avg_response_time + "s", s.most_frequent_failure])),
      d.slowest ? h("div", {class: "sub", text: "Slowest single response: " + d.slowest[0] + " (" + d.slowest[1] + "s)"}) : null);
  } catch (e) { box.className = "empty"; box.textContent = "Error: " + e.message; }
}
async function clearHistory() {
  if (!confirm("Delete the history file? This cannot be undone.")) return;
  try { await api("/api/history/clear", {}); await loadStats(); } catch (e) { setMsg("Error: " + e.message, true); }
}

/* ---------- Test cases ---------- */
const SAMPLE = [
  "# Sample file: real websites plus invalid entries (needs internet)",
  "https://example.com", "https://www.python.org", "https://httpbin.org/status/200",
  "https://httpbin.org/status/404", "https://httpbin.org/status/503",
  "https://httpbin.org/redirect/2", "http://github.com", "https://httpbin.org/redirect/10",
  "https://httpbin.org/delay/10", "https://expired.badssl.com", "https://nonexistent.invalid",
  "", "# invalid entries", "ftp://files.example.com/data", "example.com", "http://", "not a url",
  "http://example.com:99999", "", "# duplicate", "https://example.com"].join("\n");

function makeScenarios(tp) {
  const B = "http://127.0.0.1:" + tp;
  const base = {timeout: 5, retries: 1, delay: 0.2, workers: 5, max_redirects: 5, allow_redirects: false, save_history: false};
  const o = (x) => Object.assign({}, base, x);
  const L = (id, title, reqs, desc, urls, opts, expect) => ({id, title, reqs, desc, urls, opts: o(opts), expect, local: true});
  const slow = [1, 2, 3, 4].map((i) => B + "/slow?n=" + i);
  return [
    L("up", "A healthy site is UP", "Req 3, 6, 7", "A 200 answer is UP and shows status code and response time.", B + "/ok", {},
      {items: [{status: "UP", code: 200, error: null}]}),
    L("errors", "HTTP error codes are DOWN", "Req 15", "Non-2xx answers are DOWN and keep their status code.", B + "/missing\n" + B + "/boom", {},
      {items: [{status: "DOWN", code: 404, error: "HTTPStatus_404"}, {status: "DOWN", code: 503, error: "HTTPStatus_503"}]}),
    L("3xx-default", "A 3xx is DOWN by default", "Req 15", "The redirect is not followed; the code and target are reported.", B + "/redir", {},
      {items: [{status: "DOWN", code: 302, error: "HTTPStatus_302", redirected: true}]}),
    L("3xx-allow", "Redirects followed with allow-redirects", "Req 10, 15", "Absolute and relative Location headers both work; the final 200 is UP.",
      B + "/redir\n" + B + "/rel", {allow_redirects: true},
      {items: [{status: "UP", code: 200, error: null, redirected: true}, {status: "UP", code: 200, error: null, redirected: true}]}),
    L("redir-404", "Redirect that ends in a 404", "Req 15", "The final status decides: followed, then DOWN 404.", B + "/redir404", {allow_redirects: true},
      {items: [{status: "DOWN", code: 404, error: "HTTPStatus_404", redirected: true}]}),
    L("non-redirect", "3xx codes that are not real redirects", "Req 15", "A 3xx with no Location, and 304, are never followed.", B + "/noloc\n" + B + "/notmod", {allow_redirects: true},
      {items: [{status: "DOWN", code: 302, error: "HTTPStatus_302", redirected: false}, {status: "DOWN", code: 304, error: "HTTPStatus_304"}]}),
    L("loop", "Redirect loop", "Req 20", "A page that redirects to itself ends as too many redirects.", B + "/loop", {allow_redirects: true},
      {items: [{status: "DOWN", error: "too many redirects"}]}),
    L("cap-default", "A long chain exceeds the default cap", "Req 20", "A 20-hop chain with the default cap of 5.", B + "/chain/1", {allow_redirects: true},
      {items: [{status: "DOWN", error: "too many redirects"}]}),
    L("cap-raised", "Raising the hop cap", "Req 20", "The same chain succeeds with a cap of 25.", B + "/chain/1", {allow_redirects: true, max_redirects: 25},
      {items: [{status: "UP", code: 200, error: null, redirected: true}]}),
    L("bad-redirect", "Redirect to a non-http address", "Req 20", "A redirect to ftp:// is refused.", B + "/badredirect", {allow_redirects: true},
      {items: [{status: "DOWN", error: "BadRedirect"}]}),
    L("timeout", "Server hangs until the timeout", "Req 9, 20", "The server waits 6 s; the timeout is 1 s.", B + "/slow", {timeout: 1},
      {items: [{status: "DOWN", code: null, error: "TimeoutError"}]}),
    L("retries", "Retries and attempts reported", "Req 14", "Three attempts are made and the count is reported.", B + "/slow", {timeout: 1, retries: 3},
      {items: [{status: "DOWN", error: "TimeoutError", attempts: 3}]}),
    L("closed", "Server closes the connection early", "Req 20", "No reply at all is a ConnectionClosedEarly.", B + "/close", {},
      {items: [{status: "DOWN", code: null, error: "ConnectionClosedEarly"}]}),
    L("closed-retry", "Early close is retried", "Req 14, 20", "A dropped connection is a transient error, so it is retried.", B + "/close", {retries: 3},
      {items: [{status: "DOWN", error: "ConnectionClosedEarly", attempts: 3}]}),
    L("no-retry", "HTTP errors are never retried", "Req 14, 15", "A 503 is a real answer, so only one attempt is made.", B + "/boom", {retries: 3},
      {items: [{status: "DOWN", code: 503, attempts: 1}]}),
    L("tls", "TLS / secure-connection error", "Req 20", "HTTPS aimed at a plain-HTTP port fails the handshake (SSLError, or a dropped connection on some systems).",
      "https://127.0.0.1:" + tp + "/ok", {}, {items: [{status: "DOWN", errorAny: ["SSLError", "ConnectionClosedEarly"]}]}),
    L("dns", "Domain that does not exist", "Req 9, 14", "A DNS failure is retried and labelled.", "https://nonexistent.invalid", {timeout: 3, retries: 2},
      {items: [{status: "DOWN", code: null, error: "DNS error", attempts: 2}]}),
    L("validate", "Validation and per-line warnings", "Req 12, 13",
      "Comments and blank lines are skipped silently; 5 invalid entries and 1 duplicate each get a warning.",
      ["# comment", B + "/ok", "", "# another comment", B + "/ok", "ftp://files.example.com/data", "not a url", "http://",
        "http://example.com:99999", "example.com", B + "/missing"].join("\n"), {},
      {sites: 2, warnings: 6, items: [{status: "UP", code: 200}, {status: "DOWN", code: 404}]}),
    L("order", "Concurrent checks keep input order", "Req 16",
      "Four slow URLs (each would hang 6 s) plus a fast one run together: the total stays near the timeout, and results keep the input order.",
      slow.concat([B + "/ok"]).join("\n"), {timeout: 2, workers: 5},
      {sites: 5, order: true, maxSeconds: 4.5, items: [{error: "TimeoutError"}, {error: "TimeoutError"}, {error: "TimeoutError"}, {error: "TimeoutError"}, {status: "UP", code: 200}]}),
    {id: "reports", title: "CSV and JSON reports", reqs: "Req 4, 11, 18", manual: true, local: true,
      desc: "Run any check, then use Download CSV and Download JSON on the Results tab. Columns: " + info.csv_fields.join(", ") + ".",
      urls: B + "/ok\n" + B + "/missing\n" + B + "/redir", opts: o({}), expect: {}},
    {id: "history", title: "History file and statistics", reqs: "Req 18, 19", manual: true, local: true,
      desc: "Run this twice (history is appended each time), then open the Stats tab: uptime %, average time, slowest URL, most frequent failure.",
      urls: B + "/ok\n" + B + "/missing\n" + B + "/boom\n" + B + "/slow", opts: o({timeout: 1, save_history: true}), expect: {}},
    {id: "watch", title: "Watch mode with changes", reqs: "Req 17", manual: true, local: true, interval: 2,
      desc: "Load, then press Start watch. /flip alternates UP and DOWN, so the Change column shows UP -> DOWN and DOWN -> UP. Stop to see the state history.",
      urls: B + "/flip\n" + B + "/ok", opts: o({}), expect: {}},
    {id: "live-default", title: "Live sites: default rules", reqs: "Req 12-15", live: true,
      desc: "Real websites (needs internet; results can vary). Redirects are DOWN by default.", urls: SAMPLE, opts: o({timeout: 5}),
      expect: {sites: 11, warnings: 6, items: [{status: "UP", code: 200}, {status: "UP", code: 200}, {status: "UP", code: 200},
        {status: "DOWN", code: 404}, {status: "DOWN", code: 503}, {status: "DOWN", code: 302}, {status: "DOWN", code: 301}, {status: "DOWN", code: 302},
        {status: "DOWN", error: "TimeoutError"}, {status: "DOWN", error: "SSLError"}, {status: "DOWN", error: "DNS error"}]}},
    {id: "live-allow", title: "Live sites: allow redirects", reqs: "Req 10, 15, 20", live: true,
      desc: "Same sites with redirects followed: the two redirecting sites become UP; the 10-hop one still exceeds the cap of 5.", urls: SAMPLE,
      opts: o({timeout: 5, allow_redirects: true}),
      expect: {sites: 11, warnings: 6, items: [{status: "UP", code: 200}, {status: "UP", code: 200}, {status: "UP", code: 200},
        {status: "DOWN", code: 404}, {status: "DOWN", code: 503}, {status: "UP", code: 200, redirected: true}, {status: "UP", code: 200, redirected: true},
        {status: "DOWN", error: "too many redirects"}, {status: "DOWN", error: "TimeoutError"}, {status: "DOWN", error: "SSLError"}, {status: "DOWN", error: "DNS error"}]}}
  ];
}
function describeExpect(e) {
  const parts = [];
  if (e.sites !== undefined) parts.push(e.sites + " sites checked");
  if (e.warnings !== undefined) parts.push(e.warnings + " warnings");
  (e.items || []).forEach((x) => parts.push([x.status, x.code, x.error || (x.errorAny ? x.errorAny.join(" or ") : ""),
    x.attempts !== undefined ? "(" + x.attempts + " attempts)" : "", x.redirected ? "(redirected)" : ""].filter((v) => v !== undefined && v !== null && v !== "").join(" ")));
  if (e.order) parts.push("results in input order");
  if (e.maxSeconds) parts.push("finishes within " + e.maxSeconds + " s");
  return parts.join("; ");
}
function evaluate(s, data) {
  const notes = [], e = s.expect || {};
  const fail = (m) => notes.push(m);
  if (e.sites !== undefined && data.results.length !== e.sites) fail("expected " + e.sites + " sites, got " + data.results.length);
  if (e.warnings !== undefined && data.warnings.length !== e.warnings) fail("expected " + e.warnings + " warnings, got " + data.warnings.length);
  (e.items || []).forEach((x, i) => {
    const r = data.results[i];
    if (!r) { fail("no result #" + (i + 1)); return; }
    if (x.status !== undefined && r.status !== x.status) fail("#" + (i + 1) + " status " + r.status + ", expected " + x.status);
    if (x.code !== undefined && r.status_code !== x.code) fail("#" + (i + 1) + " code " + r.status_code + ", expected " + x.code);
    if (x.error !== undefined && r.error_type !== x.error) fail("#" + (i + 1) + " error " + r.error_type + ", expected " + x.error);
    if (x.errorAny && !x.errorAny.includes(r.error_type)) fail("#" + (i + 1) + " error " + r.error_type + ", expected " + x.errorAny.join(" or "));
    if (x.attempts !== undefined && r.attempts !== x.attempts) fail("#" + (i + 1) + " attempts " + r.attempts + ", expected " + x.attempts);
    if (x.redirected !== undefined && r.redirected !== x.redirected) fail("#" + (i + 1) + " redirected " + r.redirected + ", expected " + x.redirected);
  });
  if (e.order && JSON.stringify(data.results.map((r) => r.url)) !== JSON.stringify(data.requested)) fail("results are not in input order");
  if (e.maxSeconds !== undefined && data.elapsed > e.maxSeconds) fail("took " + data.elapsed + " s, expected under " + e.maxSeconds + " s");
  return {ok: notes.length === 0, notes};
}
function loadScenario(s) {
  $("urls").value = s.urls; applyOptions(s.opts);
  if (s.interval) $("interval").value = s.interval;
  setMsg("Loaded: " + s.title);
}
function setCard(s, state, ev, data) {
  const b = s.badgeEl;
  b.className = "badge " + (state === "pass" ? "b-pass" : state === "fail" ? "b-fail" : state === "running" ? "b-run" : "b-idle");
  b.textContent = state === "pass" ? "PASS" : state === "fail" ? "FAIL" : state === "running" ? "running..." : "not run";
  s.resultEl.replaceChildren();
  if (data) s.resultEl.append(h("pre", {text: data.results.map((r) => r.cli).join("\n") || data.message}));
  if (ev && ev.notes.length) s.resultEl.append(h("div", {class: "msg err", text: ev.notes.join(" | ")}));
}
async function runScenario(s) {
  setCard(s, "running");
  try {
    const data = await check(s.urls, s.opts);
    lastData = data; renderResults(data); renderWarnings(data.warnings);
    const ev = evaluate(s, data); setCard(s, ev.ok ? "pass" : "fail", ev, data); return ev;
  } catch (e) { const ev = {ok: false, notes: [e.message]}; setCard(s, "fail", ev); return ev; }
}
async function runAll() {
  const list = scenarios.filter((s) => s.local && !s.manual);
  $("runAllBtn").disabled = true; let pass = 0, fail = 0;
  for (const s of list) {
    $("allSummary").textContent = "Running " + (pass + fail + 1) + " of " + list.length + "...";
    const ev = await runScenario(s); ev.ok ? pass++ : fail++;
  }
  $("allSummary").textContent = pass + " passed, " + fail + " failed (of " + list.length + ")";
  $("runAllBtn").disabled = false;
}
function renderCards() {
  const box = $("cards"); box.replaceChildren();
  scenarios.forEach((s) => {
    s.badgeEl = h("span", {class: "badge b-idle", text: s.manual ? "manual" : "not run"});
    s.resultEl = h("div", {});
    const needsServer = s.local && !info.test_server;
    const exp = describeExpect(s.expect || {});
    box.append(h("div", {class: "card"},
      h("div", {class: "top"}, h("span", {class: "title", text: s.title}), h("span", {class: "tag", text: s.reqs}),
        s.live ? h("span", {class: "tag", text: "live internet"}) : null, h("span", {class: "sp"}), s.badgeEl,
        h("button", {class: "small", text: "Load", onclick: () => loadScenario(s)}),
        s.manual ? null : h("button", {class: "small", text: "Run", disabled: needsServer ? "" : null, onclick: () => runScenario(s)})),
      h("p", {text: s.desc}), exp ? h("div", {class: "exp", text: "Expected: " + exp}) : null, s.resultEl));
    if (needsServer) s.resultEl.append(h("div", {class: "msg err", text: "Needs the built-in test server (not running)."}));
    if (s.manual && s.id === "watch") s.badgeEl.textContent = "manual";
  });
  $("runAllBtn").disabled = !info.test_server;
}

/* ---------- start-up ---------- */
async function init() {
  try { info = await api("/api/info"); } catch (e) { setMsg("Cannot reach the server: " + e.message, true); }
  const pill = $("serverPill");
  pill.textContent = info.test_server ? "built-in test server: port " + info.test_port : "built-in test server: off";
  pill.className = "pill " + (info.test_server ? "ok" : "bad");
  $("testNote").textContent = info.test_server
    ? "Local cases use the built-in test server, so they work offline and give the same result every time. \"Live\" cases need internet."
    : "The built-in test server could not start (port busy or test_server.py missing), so local cases are disabled.";
  scenarios = makeScenarios(info.test_port || 8765); renderCards();
  const B = "http://127.0.0.1:" + (info.test_port || 8765);
  $("urls").value = info.test_server
    ? ["# Local demo (served by the built-in test server)", B + "/ok", B + "/missing", B + "/redir", B + "/loop", B + "/close", "", "# invalid entries get warnings", "ftp://files.example.com/data", "not a url"].join("\n")
    : "https://example.com\nhttps://httpbin.org/status/404";
  updateRule(); $("histName").textContent = "file: " + info.history_file;
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => { showTab(t.dataset.tab); if (t.dataset.tab === "stats") loadStats(); }));
$("runBtn").addEventListener("click", runOnce);
$("watchBtn").addEventListener("click", () => (watch.running ? stopWatch() : startWatch()));
$("sampleBtn").addEventListener("click", () => { $("urls").value = SAMPLE; setMsg("Sample loaded (needs internet)."); });
$("clearBtn").addEventListener("click", () => { $("urls").value = ""; });
$("allow").addEventListener("change", updateRule); $("maxr").addEventListener("input", updateRule);
$("statsBtn").addEventListener("click", loadStats); $("clearHistBtn").addEventListener("click", clearHistory);
$("runAllBtn").addEventListener("click", () => { showTab("tests"); runAll(); });
init();
</script>
</body>
</html>
'''


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Web GUI for website_status_checker.py")
    parser.add_argument("--port", type=int, default=8000, help="port for the GUI (default 8000)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args()

    if test_server is not None:
        try:
            test_server.start_background(test_server.PORT, quiet=True)
            STATE["test_server"], STATE["test_port"] = True, test_server.PORT
        except OSError:
            print(f"Note: port {test_server.PORT} is busy, so the built-in test server is off "
                  f"(is test_server.py already running? Stop it first).")

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"Port {args.port} is already in use. Try: python status_checker_gui.py --port {args.port + 1}")
        sys.exit(1)

    url = f"http://127.0.0.1:{args.port}"
    print(f"Website Status Checker GUI running at {url}   (Ctrl+C to stop)")
    if STATE["test_server"]:
        print(f"Built-in test server running on port {STATE['test_port']}.")
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
