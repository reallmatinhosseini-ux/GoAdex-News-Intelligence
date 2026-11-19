/* GoAdex News Intelligence — homepage + evidence page. Read-only: all analysis runs automatically on the server. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (p) => {
  const r = await fetch(p, { credentials: "same-origin" });
  if (r.status === 401) { location.href = "/login"; throw new Error("Login required"); }
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};
const postJSON = (p, body = {}) => fetch(p, { method: "POST", credentials: "same-origin",
  headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" }, body: JSON.stringify(body) });
let TZ = "Asia/Dubai";
let timers = [];

const I = {
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M7 14l5-5 5 5"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10l5 5 5-5"/></svg>',
  flat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 12h12"/></svg>',
  q: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.01"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/></svg>',
};

/* ---------- formatting ---------- */
const fmt = (iso, o = {}) => new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour12: false, ...o }).format(new Date(iso));
const when = (iso) => fmt(iso, { weekday: "short", day: "numeric", month: "short" });
const time = (iso) => fmt(iso, { hour: "2-digit", minute: "2-digit" });
const tzName = () => new Intl.DateTimeFormat("en-US", { timeZone: TZ, timeZoneName: "short" }).formatToParts(new Date()).find((p) => p.type === "timeZoneName")?.value || "";
function ago(iso) {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return "just now"; if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`; return `${Math.round(s / 86400)} d ago`;
}
function parts(iso) {
  let s = Math.max(0, Math.floor((new Date(iso) - Date.now()) / 1000));
  const d = Math.floor(s / 86400); s %= 86400; const h = Math.floor(s / 3600); s %= 3600;
  return { d, h, m: Math.floor(s / 60), s: s % 60 };
}
function shortCd(iso) {
  const ms = new Date(iso) - Date.now();
  if (ms <= 0) return "Released";
  const p = parts(iso);
  return p.d ? `in ${p.d}d ${p.h}h` : p.h ? `in ${p.h}h ${String(p.m).padStart(2, "0")}m` : `in ${p.m}m ${String(p.s).padStart(2, "0")}s`;
}

/* Direction: Ready events show the call; anything weak says so plainly. */
function direction(x, ready, lg = false) {
  const c = lg ? " lg" : "";
  if (!ready || !x || !x.bias) return `<span class="dir pending${c}">${I.flat}Awaiting analysis</span>`;
  const b = x.bias;
  if (b.startsWith("Bull")) return `<span class="dir bull${c}">${I.up}Bullish</span>`;
  if (b.startsWith("Bear")) return `<span class="dir bear${c}">${I.down}Bearish</span>`;
  if (b.startsWith("Insufficient")) return `<span class="dir insufficient${c}">${I.q}Insufficient Evidence</span>`;
  return `<span class="dir neutral${c}">${I.flat}Neutral</span>`;
}
const status = (e) => e.ready ? `<span class="chip ready"><span class="pip"></span>Analysis Ready</span>` : `<span class="chip notready"><span class="pip"></span>Not Ready</span>`;
const impact = (i) => `<span class="chip ${esc(i)}">${i === "high" ? "High impact" : "Medium impact"}</span>`;
const confText = (x) => x && x.confidence && !/^(Insufficient|Neutral)/.test(x.bias || "") ? `${x.confidence} confidence` : "";
const link = (id) => `#/e/${encodeURIComponent(id)}`;
const tick = (fn) => { fn(); timers.push(setInterval(fn, 1000)); };

/* ---------- money & prediction helpers ---------- */
const usd = (n, d = 2) => n == null ? "—" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const sgn = (n, d = 2) => n == null ? "—" : (n > 0 ? "+" : n < 0 ? "−" : "") + usd(Math.abs(n), d);
const pctS = (n) => n == null ? "—" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(2)}%`;
const ASSETS = [["XAUUSD", "Gold"], ["EURUSD", "EUR/USD"]];
const px = (a, n) => n == null ? "—" : a === "XAUUSD" ? usd(n) : Number(n).toFixed(5);
const mv = (a, n, signed = true) => n == null ? "—" : a === "XAUUSD"
  ? (signed ? sgn(n) : usd(Math.abs(n)))
  : `${signed ? (n > 0 ? "+" : n < 0 ? "−" : "") : ""}${Math.abs(n).toFixed(1)} pips`;
const secs = (iso) => iso ? fmt(iso, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
function predChip(p, ready = true, lg = false) {
  const c = lg ? " lg" : "";
  if (!ready || !p) return `<span class="dir pending${c}">${I.flat}Prediction Not Ready</span>`;
  if (p.status === "ready") return p.direction === "Bullish" ? `<span class="dir bull${c}">${I.up}Bullish</span>` : `<span class="dir bear${c}">${I.down}Bearish</span>`;
  if (p.status === "not_ready") return `<span class="dir pending${c}">${I.flat}Prediction Not Ready</span>`;
  if (p.status === "missed") return `<span class="dir pending${c}">${I.flat}Missed</span>`;
  return `<span class="dir insufficient${c}">${I.q}Insufficient Evidence</span>`;
}
const confChip = (p) => p && p.status === "ready" && p.confidence ? `<span class="chip conf-${esc(p.confidence.toLowerCase())}">${esc(p.confidence)} confidence</span>` : "";
function stepsHtml(p) {
  if (!p || !p.steps) return "";
  return `<ol class="steps">${p.steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>`;
}
/* One asset inside a stage panel: primary scenario, first/final move, explanation, evidence trail */
const ARROW = (d) => d === "UP" ? `<b class="pos">▲ UP</b>` : `<b class="neg">▼ DOWN</b>`;
function primaryChip(p, lg = false) {
  const c = lg ? " lg" : "";
  if (!p) return `<span class="dir pending${c}">${I.flat}Not Ready</span>`;
  if (p.status === "ready") {
    const bull = p.final === "UP";
    return `<span class="dir ${bull ? "bull" : "bear"}${c}">${bull ? I.up : I.down}${bull ? "BUY · Bullish" : "SELL · Bearish"}</span>${p.two_way ? `<span class="dir twoway${c}">⇅ Two-way</span>` : ""}`;
  }
  if (p.status === "missed") return `<span class="dir pending${c}">${I.flat}Missed</span>`;
  if (p.status === "not_ready") return `<span class="dir pending${c}">${I.flat}Not Ready</span>`;
  return `<span class="dir insufficient${c}">${I.q}Insufficient Evidence</span>`;
}
function leanChip(l) {
  if (!l || l.score == null) return "";
  const sc = l.score, a = Math.abs(sc);
  if (a < 0.05) return `<span class="lean-chip">◆ Evidence flat · ${sc >= 0 ? "+" : ""}${sc.toFixed(2)}</span>`;
  const word = (a < 0.15 ? "slightly " : "") + (sc > 0 ? "bullish" : "bearish");
  return `<span class="lean-chip ${sc > 0 ? "pos" : "neg"}">${sc > 0 ? "▲" : "▼"} Evidence ${word} · ${sc >= 0 ? "+" : ""}${sc.toFixed(2)}</span>`;
}
function probBars(p) {
  const pr = p.probabilities;
  if (!pr) return "";
  const row = (lbl, v, kind) => v == null ? "" : `<div class="pb"><span>${lbl}</span><div class="pbt"><i class="mid"></i><i class="pbf ${kind}" style="width:${v * 100}%"></i></div><b>${Math.round(v * 100)}%</b></div>`;
  return `<div class="pbars">${row("First move up", pr.initial, "up")}${row("Final move up", pr.final, "up")}${row("Reversal", pr.reversal, "rev")}</div>`;
}
function trailHtml(p) {
  const logx = (p.research_log || []).map((c) => `<li class="${c.ok ? "ok" : "bad"}"><b>${c.ok ? "✓" : "✗"} ${esc(c.source)}</b> ${esc(c.detail)}${c.as_of ? ` <em>· ${secs(c.as_of)}</em>` : ""}</li>`).join("");
  const alts = (p.alternatives || []).map((a) => `<li${a.chosen ? ' class="ok"' : ""}><b>${a.scenario} · ${esc(a.name)}</b> relative weight ${Math.round(a.relative_weight * 100)}%${a.chosen ? " — selected" : ""}</li>`).join("");
  return `${logx ? `<details class="calc"><summary>Research log — what was checked</summary><ul class="rlog">${logx}</ul></details>` : ""}
    ${alts ? `<details class="calc"><summary>Alternative scenarios</summary><ul class="rlog">${alts}</ul></details>` : ""}
    ${p.steps ? `<details class="calc"><summary>How it was calculated</summary>${stepsHtml(p)}</details>` : ""}`;
}
function stageAsset(stage, a, label, p, e) {
  if (!p) return `<div class="sa"><div class="sa-head"><b>${label}</b>${primaryChip(null)}</div></div>`;
  const ready = p.status === "ready";
  const pr = p.price_source || {};
  let body = "";
  if (ready) {
    body = `<div class="scn"><div><span>Primary scenario</span><b>${p.scenario} · ${esc(p.scenario_name)}</b></div>
      <div><span>Expected initial reaction</span>${ARROW(p.first)}<small>first 5 min</small></div>
      ${p.two_way ? `<div><span>Potential secondary reaction</span>${ARROW(p.final)}<small>reversal</small></div>` : ""}
      <div><span>Final bias</span>${ARROW(p.final)}<small>after 60 min</small></div></div>${probBars(p)}`;
  } else {
    body = `<p class="sa-reason"><b>${p.status === "missed" ? "What happened" : "Why"}:</b> ${esc(p.reason || "")}</p>${probBars(p)}`;
  }
  const change = p.changes_since_t30 ? `<p class="sa-change"><b>Since T-30:</b> ${esc(p.changes_since_t30.summary)}</p>` : "";
  const expl = p.explanation ? `<p class="sa-note">${esc(p.explanation)}</p>` : "";
  const stamp = p.status !== "missed" ? `<p class="sa-stamp">${p.price != null ? `Price ${px(a, p.price)}${pr.ask ? ` / ${px(a, pr.ask)}` : ""} at ${secs(pr.as_of)} · ` : ""}${p.data_until ? `1-min data until ${secs(p.data_until)} · ` : ""}published ${secs(p.published_at)}</p>` : `<p class="sa-stamp">Recorded ${secs(p.published_at)}</p>`;
  const out = p.outcome && Object.keys(p.outcome).length ? outcomeLine(a, p, p.outcome) : "";
  return `<div class="sa"><div class="sa-head"><b>${label}</b><div class="sa-chips">${primaryChip(p)}${confChip(p)}${!ready ? leanChip(p.lean) : ""}</div></div>
    ${body}${change}${expl}${stamp}${out}${trailHtml(p)}</div>`;
}
function outcomeLine(a, p, o) {
  const l = o.labels || {};
  const mvH = (h) => o.horizons?.[h] ? `${o.horizons[h].direction === "up" ? "▲" : o.horizons[h].direction === "down" ? "▼" : "◆"} ${mv(a, o.horizons[h].units)}` : "—";
  const chip = (v, ok, bad) => v == null ? `<span class="chip">Inconclusive</span>` : v ? `<span class="chip ready">✓ ${ok}</span>` : `<span class="chip miss">✗ ${bad}</span>`;
  return `<div class="sa-out"><span>Actual: first 5 min ${mvH("5")} · 60 min ${mvH("60")}${l.scenario ? ` · scenario ${l.scenario}` : ""}</span>
    ${p.status === "ready" ? chip(o.final_correct, "Final right", "Final wrong") + chip(o.initial_correct, "First move right", "First move wrong") + (p.two_way ? chip(o.order_correct, "Reversal order right", "Reversal wrong") : "") : ""}</div>`;
}

/* Two-stage panel for an event */
function stagesPanel(e, compact = false) {
  const now = Date.now();
  const block = (stage, title, at, sub) => {
    const due = new Date(at) > now;
    const s = e.stages?.[stage] || {};
    const any = Object.keys(s).length;
    const inner = any ? ASSETS.map(([a, l]) => stageAsset(stage, a, l, s[a], e)).join("")
      : due ? `<p class="sa-wait">Publishes automatically at <b>${time(at)}</b> · <span data-cd="${at}">${shortCd(at)}</span></p>`
      : `<p class="sa-reason">Not published (the stage was due while the system was offline).</p>`;
    return `<div class="stage ${any ? "done" : ""}"><div class="stage-head"><span class="stage-tag">${title}</span><small>${sub}</small></div>${inner}</div>`;
  };
  return `<div class="stages ${compact ? "compact" : ""}">
    ${block("t30", "T-30 · Forecast", e.t30_at, "published 30 min before the release")}
    ${block("t2", "T-2 · Final update", e.t2_at, "freshest data, 2 min before")}
  </div>`;
}

/* ---------- router ---------- */
async function route() {
  timers.forEach(clearInterval); timers = [];
  const h = location.hash.replace(/^#\/?/, "");
  const app = $("#app");
  window.scrollTo(0, 0);
  const sect = h.startsWith("waitlist") ? "waitlist" : h.startsWith("news") ? "news" : h.startsWith("calendar") ? "calendar" : (h.startsWith("backtest") || h.startsWith("bt/")) ? "bt" : h.startsWith("system") ? "system" : "home";
  document.querySelectorAll("#nav a, #menu-sheet a").forEach((b) => b.classList.toggle("on", b.dataset.r === sect));
  closeMenu();
  try {
    if (h.startsWith("e/")) await evidence(app, decodeURIComponent(h.slice(2)));
    else if (h.startsWith("bt/")) await btDetail(app, decodeURIComponent(h.slice(3)));
    else if (h.startsWith("backtest")) await backtestPage(app);
    else if (h.startsWith("calendar")) await calendarPage(app);
    else if (h.startsWith("news")) await newsPage(app);
    else if (h.startsWith("waitlist")) await waitlistPage(app);
    else if (h.startsWith("system")) await systemPage(app);
    else await home(app);
  } catch (e) {
    app.innerHTML = `<div class="page"><div class="empty" style="margin-top:24px"><strong>Couldn't load</strong>The analysis server isn't reachable right now (${esc(e.message)}). The page will retry automatically.</div></div>`;
    timers.push(setTimeout(route, 15000));
  }
}

/* ================= HOME ================= */
let calMode = "gold";
async function home(app, keepScroll = false) {
  const [d, sys, nws] = await Promise.all([api("/api/home"), api("/api/status").catch(() => null),
    api("/api/news?hours=24&limit=40").catch(() => null)]);
  timers.forEach(clearInterval); timers = [];
  TZ = d.timezone || TZ;
  const evs = d.events;
  const now = Date.now();
  // the nearest high-impact release stays featured until 65 min after it (so its result is visible)
  const next = evs.find((e) => e.impact === "high" && new Date(e.release_utc) > now - 65 * 60000) || evs.find((e) => new Date(e.release_utc) > now);
  const rest = evs.filter((e) => e !== next && new Date(e.release_utc) > now - 5 * 60000);
  const y = window.scrollY;
  const g = d.spot?.XAUUSD, eu = d.spot?.EURUSD;
  app.innerHTML = `<div class="page">
    ${next ? heroHtml(next, g, eu) : `<div class="panel hero" style="grid-template-columns:1fr"><div><div class="eyebrow">Next release</div><h1>Nothing major scheduled</h1><p class="muted">New events are discovered automatically every few minutes.</p></div></div>`}
    <div class="section-head"><div><h2>Upcoming news</h2><p>Forecast at T-30 · final update at T-2 · times in ${esc(tzName())}</p></div></div>
    <div class="events stagger">${rest.map(cardHtml).join("") || `<div class="empty"><strong>No other important events in the next three weeks</strong>The calendar updates itself.</div>`}</div>
    ${nws && nws.items.length ? `<div class="section-head"><div><h2>Market-moving news</h2><p>Latest high-impact headlines · <a class="link" href="#/news">All recent news →</a></p></div></div>
      <div class="panel newslist compact">${nws.items.filter((n) => n.impact !== "low").slice(0, 6).map(newsItem).join("")}</div>` : ""}
    <div class="section-head"><div><h2>Today's calendar</h2><p>Gold-moving releases today · <a class="link" href="#/calendar">Full calendar →</a></p></div></div>
    <div id="cal">${calendarHtml((d.calendar || []).filter((r) => dayKeyTZ(r.release_utc) === dayKeyTZ(new Date().toISOString())))}</div>
    ${d.backdrop.length ? `<div class="section-head"><div><h2>Market backdrop</h2><p>${esc(d.regime || "")}</p></div></div>
      <div class="metric-grid">${d.backdrop.map((m) => `<div class="metric"><span>${esc(m.label)}</span><b>${esc(m.value)}</b><small>${esc(m.note)}</small></div>`).join("")}</div>` : ""}
    ${readinessLine(sys)}
    <div class="foot">Everything runs in the background · live prices: Swissquote bid/ask, refreshed every 5 s</div>
  </div>`;
  if (keepScroll) window.scrollTo(0, y);
  document.querySelectorAll("#calseg button").forEach((b) => (b.onclick = () => { calMode = b.dataset.m; document.querySelectorAll("#calseg button").forEach((x) => x.classList.toggle("on", x === b)); $("#cal").innerHTML = calendarHtml(d.calendar); }));
  if (next) tick(() => {
    const p = parts(next.release_utc);
    const el = $("#cd"); if (!el) return;
    el.innerHTML = [["d", "Days"], ["h", "Hours"], ["m", "Min"], ["s", "Sec"]].map(([k, l]) => `<div><b>${String(p[k]).padStart(2, "0")}</b><small>${l}</small></div>`).join("");
  });
  tick(() => document.querySelectorAll("[data-cd]").forEach((el) => (el.textContent = shortCd(el.dataset.cd))));
  pollSpot();
  // refresh fast around a release so T-30 / T-2 / actual appear within seconds
  const soon = evs.some((e) => Math.abs(new Date(e.release_utc) - Date.now()) < 70 * 60000);
  timers.push(setInterval(() => !document.hidden && home(app, true).catch(() => {}), soon ? 5000 : 60000));
}

const dayKeyTZ = (iso) => fmt(iso, { year: "numeric", month: "2-digit", day: "2-digit" });
function readinessLine(st) {
  if (!st) return "";
  const h = st.history, valid = Object.values(st.models).some((m) => m.final.valid || m.initial.valid);
  const asleep = Object.values(st.recorder).some((r) => r.gap_minutes_24h > 30);
  return `<a class="panel readiness" href="#/system"><span class="eyebrow">System</span>
    <span>History ${h.loaded}/${h.total} releases · models ${valid ? '<b class="pos">validated</b>' : "not yet validated"}${asleep ? ' · <b class="neg">recorder gaps (Mac slept)</b>' : ""}</span><span class="link">Details →</span></a>`;
}

function statusHtml(st) {
  if (!st) return "";
  const h = st.history, pct = h.total ? Math.round(h.loaded / h.total * 100) : 0;
  const pill = (x, t) => `<span class="mpill ${x.valid ? "ok" : ""}" title="${x.n} out-of-sample calls">${t} ${x.valid ? "✓" : x.n ? `${x.n}/60` : "—"}</span>`;
  const models = Object.entries(st.models).map(([k, m]) => { const [a, s] = k.split(":");
    return `<div class="mrow"><span>${a === "XAUUSD" ? "Gold" : "EUR/USD"} · ${s === "t30" ? "T-30" : "T-2"}</span><div>${pill(m.final, "Final")}${pill(m.initial, "First move")}${pill(m.reversal, "Reversal")}</div></div>`; }).join("");
  const rec = Object.entries(st.recorder).map(([a, r]) => {
    const ok = r.last_tick_age_s != null && r.last_tick_age_s < 60;
    return `<div class="mrow"><span>${a === "XAUUSD" ? "Gold" : "EUR/USD"}</span><div class="small">${ok ? '<b class="pos">● live</b>' : '<b class="neg">● stopped</b>'} · ${r.minutes_3h} of 180 min recorded (3 h)${r.gap_minutes_24h > 30 ? ` · <b class="neg">${Math.round(r.gap_minutes_24h / 60 * 10) / 10} h of gaps in 24 h (Mac asleep/offline)</b>` : ""}</div></div>`; }).join("");
  const nm = { "Live quotes (Swissquote bid/ask)": "Live prices", "Dukascopy historical prices (1-minute)": "Price history", "FRED / ALFRED": "FRED macro data",
    "Economic calendar (Forex Factory weekly feed)": "Calendar & consensus", "BLS Public Data API": "BLS", "US Treasury yield curve": "Treasury yields",
    "ECB Data Portal": "ECB", "CFTC Commitments of Traders": "CFTC positioning", "Twelve Data (market prices)": "Twelve Data (fallback)" };
  const feeds = st.feeds.map((f) => { const ok = f.configured && f.last_ok && (!f.last_error_at || f.last_ok > f.last_error_at);
    const state = !f.configured ? "not set up" : ok ? "ok" : f.last_ok ? "intermittent" : "failing";
    return `<span class="fpill ${ok ? "ok" : f.configured ? "bad" : ""}" title="${esc((f.last_error || "").slice(0, 120))}">${esc(nm[f.provider] || f.provider)} · ${state}</span>`; }).join("");
  return `<div class="section-head"><div><h2>System &amp; model readiness</h2><p>Why a forecast is or isn't published — live status</p></div></div>
    <div class="grid2">
      <section class="panel card"><h4>Historical evidence <small>past releases with measured reactions</small></h4>
        <div class="prog"><i style="width:${pct}%"></i></div>
        <p class="small" style="margin:8px 0 0">${h.loaded} of ${h.total} loaded (${pct}%) · download pace 1 file / ${h.pace_s}s · ${Object.entries(h.by_type).map(([k, n]) => `${k.replace("US_", "").replace("_", " ")} ${n}`).join(" · ")}</p>
        <h4 style="margin-top:18px">Model validation <small>✓ = beat a coin flip out of sample</small></h4>${models}</section>
      <section class="panel card"><h4>Live price recorder</h4>${rec}
        <h4 style="margin-top:18px">Data sources</h4><div class="fpills">${feeds}</div>
        ${st.next_stage ? `<p class="small" style="margin-top:14px">Next scheduled stage: <b>${st.next_stage.kind === "stage_t2" ? "T-2" : "T-30"}</b> for ${esc(st.next_stage.event_id.split(":")[0].replace("US_", ""))} at <b>${when(st.next_stage.run_at)} ${time(st.next_stage.run_at)}</b></p>` : ""}</section>
    </div>`;
}

function actualLine(e) {
  const a = e.actual || {}, c = e.consensus || {};
  const keys = Object.keys(a).filter((k) => a[k] && typeof a[k] === "object" && "value" in a[k]);
  if (!keys.length) return "";
  return `<div class="actual"><span class="eyebrow">Released</span>${keys.slice(0, 3).map((k) => `<span><b>${esc(a[k].value)}</b> ${esc(k.replace(/_/g, " "))}${c[k] ? ` <em>vs forecast ${esc(c[k].value)}</em>` : ""}</span>`).join("")}</div>`;
}

function heroHtml(e, g, eu) {
  const late = e.late_discovery ? `<div class="note warn">${I.info}<span>Discovered less than an hour before release.</span></div>` : "";
  const fc = e.consensus && (e.consensus.headline || Object.values(e.consensus)[0]);
  return `<section class="panel hero hero2">
    <div>
      <div class="eyebrow">Next important release</div>
      <h1>${esc(e.title)}</h1>
      <div class="hero-meta"><span>${when(e.release_utc)} · ${time(e.release_utc)}</span>${impact(e.impact)}${status(e)}${fc ? `<span class="chip">Forecast ${esc(fc.value)}</span>` : ""}</div>
      <div class="countdown" id="cd"></div>
      <div class="livepair">
        <div><span class="pulse"></span>Gold <b data-live="XAUUSD">${g?.price ? usd(g.price) : "—"}</b><small data-live-t="XAUUSD"></small></div>
        <div><span class="pulse"></span>EUR/USD <b data-live="EURUSD">${eu?.price ? eu.price.toFixed(5) : "—"}</b><small data-live-t="EURUSD"></small></div>
      </div>
      ${actualLine(e)}${late}
      ${e.summary ? `<p class="hero-sum"><span class="eyebrow">Fundamental evidence lean</span><br>${esc(e.summary)}</p>` : ""}
      <a class="btn btn-primary" href="${link(e.id)}" style="margin-top:14px">Evidence ${I.arrow}</a>
    </div>
    <div>${stagesPanel(e)}</div>
  </section>`;
}

function cardHtml(e) {
  const mins = (new Date(e.release_utc) - Date.now()) / 60000;
  return `<article class="panel ev ${mins > 0 && mins < 35 ? "soon" : ""}">
    <div class="ev-top"><div class="ev-when">${when(e.release_utc)} <span>· ${time(e.release_utc)}</span></div><span class="ev-cd" data-cd="${e.release_utc}">${shortCd(e.release_utc)}</span></div>
    <h3>${esc(e.title)}</h3>
    ${stagesPanel(e, true)}
    ${actualLine(e)}
    <div class="ev-foot"><div style="display:flex;gap:6px;flex-wrap:wrap">${status(e)}${impact(e.impact)}</div><a class="btn btn-ghost btn-sm" href="${link(e.id)}">Evidence ${I.arrow}</a></div>
  </article>`;
}

function calendarHtml(rows) {
  const list = (rows || []).filter((r) => calMode === "all" || r.gold_relevant);
  if (!list.length) return `<div class="empty"><strong>No events</strong>The calendar feed refreshes hourly.</div>`;
  const days = {};
  list.forEach((r) => (days[fmt(r.release_utc, { weekday: "long", day: "numeric", month: "long" })] ||= []).push(r));
  const today = fmt(new Date().toISOString(), { weekday: "long", day: "numeric", month: "long" });
  return `<div class="cal stagger">${Object.entries(days).map(([d, rs]) => `<section class="panel calday"><div class="calhead">${esc(d)}${d === today ? ' <span class="chip ready">Today</span>' : ""}</div>
    <div class="calrows"><div class="calrow head"><span>Time</span><span>Event</span><span>Previous</span><span>Forecast</span><span>Actual</span></div>
    ${rs.map((r) => {
      const past = new Date(r.release_utc) < Date.now();
      const imp = (r.impact || "").toLowerCase();
      const tag = r.event_id && r.analysed ? `<a class="calgo" href="${link(r.event_id)}">Analysis ${I.arrow}</a>` : "";
      return `<div class="calrow ${r.gold_relevant ? "gold" : ""} ${past ? "past" : ""}">
        <span class="t">${time(r.release_utc)}</span>
        <span class="n"><i class="imp ${imp}" title="${esc(r.impact)} impact"></i><b>${esc(r.title)}</b><em>${esc(r.country)}</em>${r.gold_relevant ? '<span class="chip goldchip">Moves gold</span>' : ""}${tag}</span>
        <span class="v" data-l="Prev">${esc(r.previous || "—")}</span><span class="v" data-l="Fcst">${esc(r.forecast || "—")}</span>
        <span class="v a" data-l="Actual">${esc(r.actual || (past ? "—" : ""))}</span></div>`;
    }).join("")}</div></section>`).join("")}</div>`;
}

/* ================= CALENDAR PAGE ================= */
let calWeek = 0, calFilter = "gold", calCur = "all", calQ = "";
const CURRENCIES = [["all", "All"], ["USD", "USD"], ["EUR", "EUR"], ["other", "Others"]];
async function calendarPage(app) {
  const now = new Date();
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - ((now.getUTCDay() + 6) % 7) + calWeek * 7));
  const d = await api(`/api/calendar?start=${monday.toISOString().slice(0, 10)}&days=7`);
  timers.forEach(clearInterval); timers = [];
  TZ = d.timezone || TZ;
  const y = window.scrollY;
  const end = new Date(monday.getTime() + 6 * 86400000 + 12 * 3600000);  // Sunday of the shown week
  const label = calWeek === 0 ? "This week" : calWeek === 1 ? "Next week" : calWeek === -1 ? "Last week" : `${calWeek > 0 ? "+" : ""}${calWeek} weeks`;
  const keep = (r) => {
    if (calFilter === "gold" && !r.gold_relevant) return false;
    if (calFilter === "high" && r.impact !== "high") return false;
    if (calCur === "USD" && r.country !== "USD") return false;
    if (calCur === "EUR" && r.country !== "EUR") return false;
    if (calCur === "other" && ["USD", "EUR"].includes(r.country)) return false;
    if (calQ && !r.title.toLowerCase().includes(calQ.toLowerCase())) return false;
    return true;
  };
  const rows = d.rows.filter(keep);
  const nextId = d.rows.find((r) => new Date(r.release_utc) > Date.now() && (r.impact === "high" || r.gold_relevant))?.id;
  const days = {};
  rows.forEach((r) => (days[dayKeyTZ(r.release_utc)] ||= []).push(r));
  const todayK = dayKeyTZ(new Date().toISOString());
  const stageMini = (r) => {
    const st = r.stages && (r.stages.t2 || r.stages.t30);
    if (st) {
      const one = (a, l) => { const p = st[a]; if (!p) return "";
        if (p.status === "ready") return `<span class="mini-dir ${p.final === "UP" ? "pos" : "neg"}">${l} ${p.final === "UP" ? "BUY" : "SELL"}${p.two_way ? " ⇅" : ""}</span>`;
        return `<span class="mini-dir">${l} ${p.status === "missed" ? "missed" : p.status === "insufficient" ? "no edge" : "not ready"}</span>`; };
      return `<a class="calgo" href="${link(r.event_id)}">${one("XAUUSD", "Gold")}${one("EURUSD", "EUR")}</a>`;
    }
    return r.event_id && r.analysed ? `<a class="calgo" href="${link(r.event_id)}">Analysis ${I.arrow}</a>` : "";
  };
  app.innerHTML = `<div class="page">
    <div class="ev-hero"><div class="eyebrow">Economic calendar</div><h1>${label}</h1>
      <div class="hero-meta"><span>${when(d.start)} – ${when(end.toISOString())} · times in ${esc(tzName())} · forecast = market consensus</span></div></div>
    <div class="cal-controls panel">
      <div class="row-between" style="gap:8px">
        <div class="weeknav"><button class="btn btn-ghost btn-sm" id="wk-prev">‹</button><button class="btn btn-ghost btn-sm" id="wk-now" ${calWeek === 0 ? "disabled" : ""}>This week</button><button class="btn btn-ghost btn-sm" id="wk-next">›</button></div>
        ${seg("cal-filter", [["gold", "Gold-moving"], ["high", "High impact"], ["all", "All events"]], calFilter)}
      </div>
      <div class="row-between" style="gap:8px;margin-top:10px">
        ${seg("cal-cur", CURRENCIES, calCur)}
        <input class="input" id="cal-q" type="search" placeholder="Search events…" value="${esc(calQ)}">
      </div>
    </div>
    <p class="small muted" style="margin:10px 2px 0">${esc(d.coverage)} · ${rows.length} of ${d.rows.length} events shown</p>
    <div class="cal stagger" style="margin-top:12px">${Object.keys(days).length ? Object.entries(days).map(([k, rs]) => `
      <section class="panel calday ${k === todayK ? "today" : ""}"><div class="calhead">${fmt(rs[0].release_utc, { weekday: "long", day: "numeric", month: "long" })}${k === todayK ? ' <span class="chip ready">Today</span>' : ""}<small class="muted">${rs.length} event${rs.length > 1 ? "s" : ""}</small></div>
      <div class="calrows"><div class="calrow head"><span>Time</span><span>Event</span><span>Previous</span><span>Forecast</span><span>Actual</span></div>
      ${rs.map((r) => {
        const past = new Date(r.release_utc) < Date.now();
        const isNext = r.id === nextId;
        return `<div class="calrow ${r.gold_relevant ? "gold" : ""} ${past ? "past" : ""} ${isNext ? "nextup" : ""}">
          <span class="t">${time(r.release_utc)}${isNext ? `<em data-cd="${r.release_utc}">${shortCd(r.release_utc)}</em>` : ""}</span>
          <span class="n"><i class="imp ${esc(r.impact)}" title="${esc(r.impact)} impact"></i><span class="cur">${esc(r.country)}</span><b>${esc(r.title)}</b>${stageMini(r)}</span>
          <span class="v" data-l="Prev">${esc(r.previous || "—")}</span><span class="v" data-l="Fcst">${esc(r.forecast || "—")}</span>
          <span class="v a" data-l="Actual" title="${esc(r.actual_source ? JSON.parse(r.actual_source).source : (past && !r.actual ? "No free official source for this actual" : ""))}">${esc(r.actual || (past ? "—" : ""))}</span></div>`;
      }).join("")}</div></section>`).join("") : `<div class="empty"><strong>No events match</strong>Try "All events" or another week.</div>`}</div>
    <div class="foot">Impact: <i class="imp high"></i> high <i class="imp medium"></i> medium <i class="imp low"></i> low · gold-tinted rows can move gold · sources: Forex Factory weekly calendar, Federal Reserve, BEA, FRED</div>
  </div>`;
  window.scrollTo(0, y);
  $("#wk-prev").onclick = () => { calWeek--; route(); };
  $("#wk-next").onclick = () => { calWeek++; route(); };
  $("#wk-now").onclick = () => { calWeek = 0; route(); };
  document.querySelectorAll("#cal-filter button").forEach((b) => (b.onclick = () => { calFilter = b.dataset.k; calendarPage(app); }));
  document.querySelectorAll("#cal-cur button").forEach((b) => (b.onclick = () => { calCur = b.dataset.k; calendarPage(app); }));
  let qt; $("#cal-q").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { calQ = e.target.value; calendarPage(app).then(() => { const i = $("#cal-q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
  tick(() => document.querySelectorAll("[data-cd]").forEach((el) => (el.textContent = shortCd(el.dataset.cd))));
  timers.push(setInterval(() => !document.hidden && calendarPage(app).catch(() => {}), 60000));
}

/* ================= NEWS PAGE ================= */
let newsTag = "", newsImpact = "", newsQ = "", newsHours = 24, newsSeen = null;
const TAG_LABEL = { gold: "Gold", usd: "USD", eur: "EUR", central_bank: "Central banks", data: "Data", geopolitics: "Geopolitics", risk: "Risk sentiment", oil: "Oil" };
const OFFICIAL = new Set(["Federal Reserve", "European Central Bank", "BEA", "Official data (FRED)"]);
function newsItem(n) {
  const isNew = newsSeen && n.published_at > newsSeen;
  return `<article class="nitem ${isNew ? "fresh" : ""}">
    <div class="nmeta"><i class="imp ${esc(n.impact)}" title="${esc(n.impact)} impact"></i><span class="nsrc ${OFFICIAL.has(n.source) ? "official" : ""}">${esc(n.source)}</span>
      <span class="ntime" title="${esc(n.published_at)}">${time(n.published_at)} · ${ago(n.published_at)}</span></div>
    <a class="ntitle" href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>
    ${n.summary ? `<p class="nsum">${esc(n.summary)}</p>` : ""}
    <div class="ntags">${(n.tags || []).map((t) => `<span class="ntag t-${t}">${esc(TAG_LABEL[t] || t)}</span>`).join("")}
      ${n.related_event ? `<a class="ntag rel" href="${link(n.related_event)}">↳ ${esc((n.related_title || "related release").split(" — ")[0])}</a>` : ""}</div>
  </article>`;
}
async function newsPage(app) {
  const q = new URLSearchParams({ hours: newsHours, limit: 400, ...(newsTag && { tag: newsTag }), ...(newsImpact && { impact: newsImpact }), ...(newsQ && { q: newsQ }) });
  const d = await api(`/api/news?${q}`);
  timers.forEach(clearInterval); timers = [];
  const y = window.scrollY;
  const feeds = Object.entries(d.status.feeds || {});
  const groups = {};
  d.items.forEach((n) => (groups[dayKeyTZ(n.published_at)] ||= []).push(n));
  app.innerHTML = `<div class="page">
    <div class="ev-hero"><div class="eyebrow">Recent news</div><h1>Market-moving news</h1>
      <div class="hero-meta"><span>Headlines that can move Gold, the dollar or the euro · updated every 3 minutes · last check ${ago(d.status.at)}</span></div></div>
    <div class="cal-controls panel">
      <div class="row-between" style="gap:8px">
        ${seg("n-tag", [["", "All"], ["gold", "Gold"], ["usd", "USD"], ["eur", "EUR"], ["central_bank", "Central banks"], ["data", "Data"], ["geopolitics", "Geopolitics"]], newsTag)}
        ${seg("n-hours", [["6", "6 h"], ["24", "24 h"], ["72", "3 days"]], String(newsHours))}
      </div>
      <div class="row-between" style="gap:8px;margin-top:10px">
        ${seg("n-imp", [["", "Any impact"], ["high", "High"], ["medium", "Medium"]], newsImpact)}
        <input class="input" id="n-q" type="search" placeholder="Search headlines…" value="${esc(newsQ)}">
      </div>
    </div>
    <p class="small muted" style="margin:10px 2px 0">${d.items.length} headlines · impact is scored from what each story touches (gold, Fed/ECB, US data, dollar, euro, geopolitics, risk, oil); official sources are marked. Headlines link to the publisher.</p>
    ${Object.keys(groups).length ? Object.entries(groups).map(([k, items]) => `<div class="section-head" style="margin-top:22px"><div><h2 style="font-size:24px">${fmt(items[0].published_at, { weekday: "long", day: "numeric", month: "long" })}</h2></div></div>
      <div class="panel newslist stagger">${items.map(newsItem).join("")}</div>`).join("") : `<div class="empty" style="margin-top:16px"><strong>No headlines match</strong>Try a wider time range or another filter.</div>`}
    <div class="foot">Sources: ${feeds.map(([n, st]) => `${esc(n)} ${String(st).startsWith("ok") ? "✓" : "✗"}`).join(" · ")}</div>
  </div>`;
  window.scrollTo(0, y);
  newsSeen = d.items[0]?.published_at || newsSeen;
  const bind = (id, fn) => document.querySelectorAll(`#${id} button`).forEach((b) => (b.onclick = () => { fn(b.dataset.k); newsSeen = null; newsPage(app); }));
  bind("n-tag", (k) => (newsTag = k)); bind("n-hours", (k) => (newsHours = +k)); bind("n-imp", (k) => (newsImpact = k));
  let qt; $("#n-q").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { newsQ = e.target.value; newsPage(app).then(() => { const i = $("#n-q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
  timers.push(setInterval(() => !document.hidden && newsPage(app).catch(() => {}), 60000));
}

/* ================= SYSTEM PAGE ================= */
async function systemPage(app) {
  const st = await api("/api/status");
  timers.forEach(clearInterval); timers = [];
  app.innerHTML = `<div class="page">
    <div class="ev-hero"><div class="eyebrow">System</div><h1>Data &amp; model readiness</h1>
      <div class="hero-meta"><span>Why a forecast is or isn't published · last background job ${ago(st.last_job)}</span></div></div>
    <div style="margin-top:8px">${statusHtml(st).replace(/^\s*<div class="section-head">[\s\S]*?<\/div><\/div>/, "")}</div>
  </div>`;
  timers.push(setInterval(() => !document.hidden && systemPage(app).catch(() => {}), 30000));
}

/* ================= WAITLIST (admin) ================= */
async function waitlistPage(app) {
  const [d, inv] = await Promise.all([api("/api/admin/waitlist"), api("/api/admin/investors")]);
  timers.forEach(clearInterval); timers = [];
  const lab = { both: "Gold & EUR/USD", gold: "Gold", eurusd: "EUR/USD" };
  const tickets = { under_100k: "Under $100k", "100k_500k": "$100k – $500k", "500k_2m": "$500k – $2m", "2m_plus": "Over $2m", undisclosed: "To be discussed", "": "—" };
  app.innerHTML = `<div class="page">
    <div class="ev-hero"><div class="eyebrow">Admin</div><h1>Waitlist &amp; investors</h1>
      <div class="hero-meta"><span>Submissions from the public website · only visible to admins</span></div></div>
    <div class="section-head"><div><h2>Investor interest</h2><p>${inv.count} submission${inv.count === 1 ? "" : "s"} from /investors · <a class="link" href="/api/admin/investors.csv">Export CSV ↓</a> · Email alerts: ${inv.email_alerts ? "<b style=\"color:var(--pos)\">on</b>" : "<b style=\"color:var(--amber)\">off — add SMTP settings to .env</b>"}</p></div></div>
    <div class="panel bt-list">${inv.rows.length ? `<div class="wlrow head"><span>Contact</span><span>Type · allocation</span><span>Received</span></div>` + inv.rows.map((r) =>
      `<div class="wlrow"><span><b>${esc(r.name)}</b><em>${esc(r.email)}${r.organization ? " · " + esc(r.organization) : ""}${r.role ? " · " + esc(r.role) : ""}</em>${r.message ? `<em style="white-space:pre-wrap">${esc(r.message)}</em>` : ""}</span><span>${esc(inv.types[r.investor_type] || r.investor_type)}<br><span class="muted">${esc(tickets[r.ticket || ""] || r.ticket)}</span></span><span class="muted">${when(r.created_at)} ${time(r.created_at)}</span></div>`).join("")
      : `<div class="empty"><strong>No investor submissions yet</strong>Share goadex /investors with prospective partners.</div>`}</div>
    <div class="section-head"><div><h2>Early-access waitlist</h2></div></div>
    <div class="metric-grid" style="grid-template-columns:repeat(3,1fr)">
      <div class="metric"><span>Total sign-ups</span><b>${d.count}</b><small>all time</small></div>
      <div class="metric"><span>Last 7 days</span><b>${d.last_7d}</b><small>new sign-ups</small></div>
      <div class="metric"><span>Export</span><b><a class="link" href="/api/admin/waitlist.csv">CSV ↓</a></b><small>spreadsheet-safe</small></div>
    </div>
    <div class="panel bt-list" style="margin-top:16px">${d.rows.length ? `<div class="wlrow head"><span>Email</span><span>Interest</span><span>Joined</span></div>` + d.rows.map((r) =>
      `<div class="wlrow"><span><b>${esc(r.email)}</b>${r.name ? `<em>${esc(r.name)}</em>` : ""}</span><span>${esc(lab[r.interest] || r.interest || "—")}</span><span class="muted">${when(r.created_at)} ${time(r.created_at)}</span></div>`).join("")
      : `<div class="empty"><strong>No sign-ups yet</strong>Share the landing page to start collecting interest.</div>`}</div>
  </div>`;
  timers.push(setInterval(() => !document.hidden && waitlistPage(app).catch(() => {}), 30000));
}

/* ---------- log out ---------- */
async function logout() { await postJSON("/api/auth/logout"); location.href = "/login"; }
document.getElementById("logout-m")?.addEventListener("click", logout);
document.getElementById("logout-d")?.addEventListener("click", logout);

/* ---------- mobile menu ---------- */
function closeMenu() { const m = $("#menu-sheet"); if (m) { m.hidden = true; $("#menu-btn").setAttribute("aria-expanded", "false"); document.body.classList.remove("menu-open"); } }
$("#menu-btn").addEventListener("click", (e) => { e.stopPropagation(); const m = $("#menu-sheet"); const open = m.hidden; m.hidden = !open; $("#menu-btn").setAttribute("aria-expanded", String(open)); document.body.classList.toggle("menu-open", open); });
document.addEventListener("click", (e) => { if (!e.target.closest("#menu-sheet") && !e.target.closest("#menu-btn")) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

/* ================= EVIDENCE ================= */
const srcOf = (f) => {
  if (!f || !f.source) return "";
  const t = `${esc(f.source)}${f.as_of ? ` · ${esc(String(f.as_of).slice(0, 10))}` : ""}`;
  return f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">${t}</a>` : t;
};
const v = (f) => (f && f.value !== undefined && f.value !== null && f.value !== "") ? f.value : "—";

async function evidence(app, id) {
  const d = await api(`/api/events/${encodeURIComponent(id)}`);
  timers.forEach(clearInterval); timers = [];
  const keepY = window.scrollY;
  const e = d.event, r = d.report;
  TZ = TZ || "Asia/Dubai";
  const head = `<div class="ev-hero">
      <a class="btn-back" href="#/">${I.back}All news</a>
      <h1>${esc(e.title)}</h1>
      <div class="hero-meta"><span>${when(e.release_utc)} · ${time(e.release_utc)} ${esc(tzName())}</span>${impact(e.impact)}${status(e)}<span class="muted" data-cd="${e.release_utc}">${shortCd(e.release_utc)}</span></div>
    </div>`;
  if (!r) {
    app.innerHTML = `<div class="page">${head}<div class="empty" style="margin-top:24px"><strong>Not Ready</strong>Research for this release hasn't finished yet. It runs automatically in the background — this page will fill in on its own.</div></div>`;
    tick(() => document.querySelectorAll("[data-cd]").forEach((el) => (el.textContent = shortCd(el.dataset.cd))));
    timers.push(setInterval(() => route(), 60000));
    return;
  }
  const g = r.assets.XAUUSD, eu = r.assets.EURUSD;
  const leanBar = (x) => { const s = Math.max(-1, Math.min(1, x.score || 0)), w = Math.abs(s) * 50;
    const col = (x.bias || "").startsWith("Insufficient") ? "var(--ink-4)" : s > 0.15 ? "var(--pos)" : s < -0.15 ? "var(--neg)" : "var(--ink-4)";
    return `<div class="lean" title="Evidence balance (not a probability)"><span class="mid"></span><i style="left:${s >= 0 ? 50 : 50 - w}%;width:${w}%;background:${col};transform-origin:${s >= 0 ? "left" : "right"}"></i></div>`; };
  const callCell = (name, x) => `<div><span class="asset">${name}</span>${direction(x, true, true)}
      <span class="sub">${esc(confText(x) || (x.bias.startsWith("Insufficient") ? `Only ${Math.round(x.coverage * 100)}% of the needed evidence is available` : "Evidence is balanced"))}</span>${leanBar(x)}
      <span class="sub" style="font-size:12px">${esc(x.transmission || "")}</span></div>`;

  const factorHtml = (f) => {
    const s = f.signal, tag = s > 0.1 ? ["pos", "Supports gold ↑"] : s < -0.1 ? ["neg", "Supports gold ↓"] : ["flat", "Neutral"];
    return `<div class="factor"><div><b>${esc(f.name)}</b><p>${esc(f.evidence)}</p><div class="src">${(f.sources || []).filter(Boolean).map(srcOf).join(" · ")}</div></div><div class="tag ${tag[0]}">${tag[1]}<div class="muted" style="font-weight:450;margin-top:2px">weight ${Math.round(f.weight * 100)}%</div></div></div>`;
  };
  const factors = [...g.factors].sort((a, b) => Math.abs(b.signal * b.weight) - Math.abs(a.signal * a.weight));

  const sc = r.scenarios.map((s) => { const col = s.name.includes("Bearish") ? "var(--neg)" : s.name.includes("Bullish") ? "var(--pos)" : "var(--ink-4)";
    return `<div><div class="head"><i style="background:${col}"></i>${esc(s.name.replace("Mixed / fade", "Mixed or reversal"))}</div><p>${esc(s.condition)}</p><small>${esc(s.chain)}</small>${s.history ? `<small>${esc(s.history)}</small>` : ""}</div>`; }).join("");

  const reg = r.regime || {}, sg = reg.signals || {}, rt = sg.rates || {};
  const regRows = [
    sg.inflation && ["Inflation", sg.inflation.label, `Core CPI ${v(sg.inflation.core_cpi_yoy)}% y/y`, sg.inflation.core_cpi_yoy],
    sg.labor && ["Labour market", sg.labor.label, `Unemployment ${v(sg.labor.unemployment)}%`, sg.labor.unemployment],
    rt.policy_pricing && ["Rate expectations", rt.policy_pricing.label, `2y vs 3m: ${v(rt.policy_pricing.spread_2y_3m_bp)} bp`, rt.policy_pricing.spread_2y_3m_bp],
    rt.real10y && ["Real yields", rt.real_rate_label, `10-year TIPS ${v(rt.real10y)}%`, rt.real10y],
    sg.gold_positioning && ["Gold positioning", sg.gold_positioning.label, `Speculators net long ${v(sg.gold_positioning.net_pct_oi)}% of open interest`, sg.gold_positioning.net_pct_oi],
  ].filter(Boolean);

  const numbers = [];
  for (const [label, obj] of [["Previous", d.previous], ["Consensus", r.consensus], ["Actual", d.actual]]) {
    const rows = obj ? Object.entries(obj).filter(([, f]) => f && typeof f === "object" && "value" in f) : [];
    if (rows.length) {
      for (const [k, f] of rows) numbers.push(`<div><span>${label} · ${esc(k.replace(/_/g, " "))}</span><b>${esc(v(f))}<em>${srcOf(f)}</em></b></div>`);
    } else {
      numbers.push(`<div><span>${label}</span><b class="muted" style="font-weight:450">${label === "Actual" ? "Not released yet" : label === "Consensus" ? "No verified consensus feed" : "Captured 3 h before release"}</b></div>`);
    }
  }

  const urls = new Map();
  const addSrc = (f) => { if (f && f.url) urls.set(f.url, f.source || f.url); };
  factors.forEach((f) => (f.sources || []).forEach(addSrc));
  regRows.forEach((x) => addSrc(x[3]));
  [d.previous, r.consensus, d.actual].forEach((o) => o && Object.values(o).forEach(addSrc));
  if (d.source_url) urls.set(d.source_url, `Release schedule · ${d.source}`);
  const web = r.web_research;
  if (web && !web.error) ["views", "recent_developments", "consensus"].forEach((k) => (web[k] || []).forEach((x) => urls.set(x.source_url, x.source_name)));

  const tech = r.technicals?.XAUUSD;
  const nar = r.narrative;
  const gaps = r.data_quality?.gaps || [];
  app.innerHTML = `<div class="page">${head}
    ${e.late_discovery ? `<div class="note warn" style="margin-top:16px">${I.info}<span>Discovered less than an hour before release — the one-hour-early report could not be prepared.</span></div>` : ""}
    <section class="panel card" style="margin-top:22px"><h4>Predictions <small>T-30 direction · T-2 final targets · both assets</small></h4>${stagesPanel(e)}${stagesDetail(e)}</section>
    <div class="call stagger">${callCell("Gold · XAUUSD · evidence balance", g)}${callCell("EUR/USD", eu)}</div>
    <div class="twocol">
      <div class="stack stagger">
        <section class="panel card"><h4>Summary</h4><p class="prose">${esc(r.summary)}</p>
          ${nar && nar.text ? `<div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--line)"><div class="eyebrow" style="margin-bottom:8px">Strategist view · AI-written from the evidence below</div><p class="prose">${esc(nar.text)}</p></div>` : ""}
        </section>
        <section class="panel card"><h4>Why <small>each piece of evidence, strongest first</small></h4>${factors.map(factorHtml).join("") || `<p class="muted">No usable evidence yet.</p>`}</section>
        <section class="panel card"><h4>What could happen</h4><div class="scen">${sc}</div>
          <div class="note" style="margin-top:14px">${I.info}<span>The actual number is unknown until release. These are the outcomes that would move gold each way. ${esc(r.volatility?.basis || "")}</span></div></section>
        ${r.invalidation?.length ? `<section class="panel card"><h4>What would prove this wrong</h4>${r.invalidation.map((x) => `<p class="prose" style="margin-bottom:8px">• ${esc(x)}</p>`).join("")}</section>` : ""}
      </div>
      <div class="stack stagger">
        <section class="panel card"><h4>Key numbers</h4><div class="kv">${numbers.join("")}</div></section>
        <section class="panel card"><h4>Economic backdrop</h4><div class="kv">${regRows.map((x) => `<div><span>${x[0]}</span><b>${esc(x[1])}<em>${esc(x[2])} · ${srcOf(x[3])}</em></b></div>`).join("") || `<p class="muted">Not available.</p>`}</div></section>
        ${tech && tech.available ? `<section class="panel card"><h4>Gold price levels</h4><div class="kv">
          <div><span>Last</span><b>${tech.last.toFixed(2)}</b></div><div><span>Yesterday's high / low</span><b>${tech.prev_high.toFixed(2)} / ${tech.prev_low.toFixed(2)}</b></div>
          <div><span>20-day range</span><b>${tech.low_20d.toFixed(2)} – ${tech.high_20d.toFixed(2)}</b></div><div><span>Average daily range</span><b>${tech.atr14.toFixed(2)}</b></div></div><div class="src">${esc(tech.source)}</div></section>` : ""}
        <section class="panel card"><h4>Sources</h4><div class="sources">${[...urls].map(([u, n]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(n)}<span>${esc((() => { try { return new URL(u).hostname.replace("www.", ""); } catch { return ""; } })())} ↗</span></a>`).join("") || `<p class="muted">—</p>`}</div></section>
        ${gaps.length ? `<section class="panel card"><h4>Missing data</h4>${gaps.map((x) => `<p class="prose" style="font-size:13.5px;margin-bottom:6px">• ${esc(x)}</p>`).join("")}<div class="src">Missing inputs are never filled in with estimates. They lower the confidence instead.</div></section>` : ""}
        <section class="panel card"><h4>Updates</h4><div>${d.history.map((h) => `<div class="tl ${h.material_change ? "major" : ""}"><b>${esc({ early: "First analysis", refresh: "Refreshed", final: "Final report (pre-release)", monitor: "Final-hour update", post_release: "After release" }[h.stage] || h.stage)}</b><span>${when(h.created_at)} · ${time(h.created_at)}</span><p>${esc(h.revision_reason)}</p></div>`).join("")}</div></section>
      </div>
    </div>
    <div class="foot">The direction is a weighing of the evidence above, not a probability or trading advice.</div>
  </div>`;
  tick(() => document.querySelectorAll("[data-cd]").forEach((el) => (el.textContent = shortCd(el.dataset.cd))));
  window.scrollTo(0, keepY);
  const near = Math.abs(new Date(e.release_utc) - Date.now()) < 70 * 60000;
  if (near) timers.push(setInterval(() => !document.hidden && evidence(app, id).catch(() => {}), 10000));
}


function stagesDetail(e) { return ""; }

/* ================= BACKTEST ================= */
let btAsset = "XAUUSD", btStage = "t30", btSrc = "", btKey = "";
const pctTxt = (x) => x == null ? "—" : `${Math.round(x * 100)}%`;
const seg = (id, opts, cur) => `<div class="segmented" id="${id}">${opts.map(([k, l]) => `<button data-k="${k}" class="${cur === k ? "on" : ""}">${l}</button>`).join("")}</div>`;
const rateCell = (label, r, note) => `<div class="metric"><span>${label}</span><b>${pctTxt(r.accuracy)}</b><small>${r.n ? `${r.correct} right · ${r.incorrect} wrong${r.inconclusive ? ` · ${r.inconclusive} inconclusive` : ""} · lower bound ${pctTxt(r.lower_bound)}` : note}</small></div>`;
async function backtestPage(app) {
  const q = new URLSearchParams({ asset: btAsset, stage: btStage, ...(btSrc && { source: btSrc }), ...(btKey && { key: btKey }) });
  const d = await api(`/api/backtest?${q}`);
  const s = d.stats, o = s.overall, A = btAsset, m = s.model || {}, st = s.status;
  const tw = o.two_way;
  app.innerHTML = `<div class="page">
    <div class="ev-hero"><div class="eyebrow">News backtesting · engine ${esc(s.engine_version)}</div><h1>How accurate is it?</h1>
      <div class="hero-meta"><span>Every forecast scored against real 1-minute prices${s.first ? ` · ${when(s.first)} ${fmt(s.first, { year: "numeric" })} – ${when(s.last)} ${fmt(s.last, { year: "numeric" })}` : ""}</span></div></div>
    <div class="row-between" style="margin-top:22px">
      <div class="row-between" style="gap:8px">${seg("bt-asset", [["XAUUSD", "Gold"], ["EURUSD", "EUR/USD"]], btAsset)}${seg("bt-stage", [["t30", "T-30"], ["t2", "T-2"]], btStage)}</div>
      <div class="row-between" style="gap:8px">${seg("bt-src", [["", "All"], ["live", "Live"], ["retro", "Replay"]], btSrc)}
      <select class="select" id="key"><option value="">All news types</option>${Object.entries(d.types).map(([k, n]) => `<option value="${k}" ${btKey === k ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>
    </div>
    <div class="note" style="margin-top:14px">${I.info}<span><b>Live</b> = published before the release. <b>Replay</b> = reconstructed later by re-running the same engine with only data available at T-31 / T-3 minutes and models trained only on earlier releases. Scoring: first move = price at +5 min, final = +60 min, both vs 1 min before the release; moves under ${A === "XAUUSD" ? "2" : "1"} bp are inconclusive. Historical consensus forecasts aren't available from free sources, so replays use the fundamentals and price data available at the time.${st && st.pending ? ` History still loading: ${st.pending} releases left.` : ""}</span></div>
    <div class="metric-grid six stagger" style="margin-top:14px">
      <div class="metric"><span>Releases tested</span><b>${o.events}</b><small>${o.forecasts} forecasts · ${o.insufficient} insufficient evidence · ${o.missed} missed</small></div>
      ${rateCell("Final direction (60 min)", o.final, "no forecasts yet")}
      ${rateCell("First move (5 min)", o.initial, "no forecasts yet")}
      ${rateCell("Exact scenario (A–D)", o.scenario, "no forecasts yet")}
      <div class="metric"><span>Two-way forecasts</span><b>${tw.predicted}</b><small>${tw.predicted ? `reversal happened ${tw.occurred} · right order ${tw.order_correct}` : "none issued"}</small></div>
      <div class="metric"><span>Base rates (all releases)</span><b>${pctTxt(m.label_rates?.final_up)}</b><small>finished up · reversals ${pctTxt(m.label_rates?.reversal)} · flat ${pctTxt(m.label_rates?.inconclusive_final)}</small></div>
    </div>
    ${shadowHtml(o.shadow)}
    ${validationHtml(m)}
    <div class="twocol">
      <section class="panel card"><h4>Final-direction accuracy by time after release <small>50% = coin flip</small></h4>
        <div class="bars">${Object.entries(s.by_horizon).map(([h, v]) => `<div class="barrow"><span>${h.replace("m", " min")}</span><div class="track"><i class="half"></i><i class="fill ${v.accuracy != null && v.accuracy >= 0.5 ? "good" : "bad"}" style="width:${(v.accuracy || 0) * 100}%"></i></div><b>${pctTxt(v.accuracy)}</b><small>n=${v.n}</small></div>`).join("")}</div></section>
      <section class="panel card"><h4>By news type</h4><div class="kv">${Object.entries(s.by_event).map(([k, v]) => `<div><span>${esc(v.name)}<em>${v.forecasts} forecasts · ${v.insufficient} insufficient</em></span><b>${pctTxt(v.final.accuracy)}<em>final · first ${pctTxt(v.initial.accuracy)} · scenario ${pctTxt(v.scenario.accuracy)}</em></b></div>`).join("") || `<p class="muted">No scored releases yet.</p>`}</div></section>
    </div>
    ${factorHtml(m.factor_value)}
    <div class="section-head"><div><h2>Every tested release</h2><p>Including failures and abstentions · click for the full record</p></div></div>
    <div class="panel bt-list">${d.events.length ? `<div class="btrow head"><span>Release</span><span>Forecast</span><span>First · final</span><span>Actual 5 / 60 min</span><span>Result</span></div>` + d.events.map((r) => {
      const ready = r.status === "ready";
      const sh = r.shadow && r.shadow.final != null ? ` <span class="chip shadow">${r.shadow.final ? "model ✓" : "model ✗"}</span>` : "";
      const res = !ready ? `<span class="chip">${r.status === "missed" ? "Missed" : "No call"}</span>${sh}`
        : (r.final_correct == null ? `<span class="chip">Inconclusive</span>` : r.final_correct ? `<span class="chip ready">✓ Final</span>` : `<span class="chip miss">✗ Final</span>`)
          + (r.scenario_correct ? ` <span class="chip goldchip">Scenario ${r.scenario}</span>` : "");
      return `<a class="btrow" href="#/bt/${encodeURIComponent(r.event_id)}">
        <span><b>${esc((r.title || "").split(" — ")[0])}</b><em>${when(r.release_utc)} ${fmt(r.release_utc, { year: "numeric" })} · <i class="kind ${r.kind.split("_")[0]}">${r.kind.startsWith("live") ? "Live" : "Replay"}</i></em></span>
        <span>${ready ? `${r.final === "UP" ? '<b class="pos">BUY</b>' : '<b class="neg">SELL</b>'} · ${r.scenario}` : r.p_final != null ? `<em class="shadowtxt">model ${r.p_final > 0.5 ? "▲" : "▼"} ${Math.round(r.p_final * 100)}%</em>` : "—"}</span>
        <span>${ready ? `${r.first === "UP" ? "▲" : "▼"} then ${r.final === "UP" ? "▲" : "▼"}` : r.p_reversal != null ? `<em class="shadowtxt">reversal ${Math.round(r.p_reversal * 100)}%</em>` : "—"}</span>
        <span class="num">${mv(A, r.move5)} / <b class="${r.move60 > 0 ? "pos" : r.move60 < 0 ? "neg" : ""}" style="display:inline">${mv(A, r.move60)}</b>${r.actual_scenario ? ` · ${r.actual_scenario}` : ""}</span>
        <span>${res}</span></a>`;
    }).join("") : `<div class="empty"><strong>No scored releases yet</strong>History loads automatically in the background.</div>`}</div>
  </div>`;
  const bind = (id, fn) => document.querySelectorAll(`#${id} button`).forEach((b) => (b.onclick = () => { fn(b.dataset.k); route(); }));
  bind("bt-asset", (k) => (btAsset = k)); bind("bt-stage", (k) => (btStage = k)); bind("bt-src", (k) => (btSrc = k));
  $("#key").onchange = (e) => { btKey = e.target.value; route(); };
  if (st && st.pending) timers.push(setInterval(() => !document.hidden && route(), 30000));
}

function shadowHtml(sh) {
  if (!sh || !sh.final || !sh.final.n) return "";
  const c = (t, x) => `<div><span>${t}</span><b>${pctTxt(x.accuracy)}</b><small>${x.correct}/${x.n} right · lower bound ${pctTxt(x.lower_bound)}</small></div>`;
  const hz = [1, 5, 15, 30, 60].map((h) => sh[`h${h}`]).filter((x) => x && x.n);
  return `<section class="panel card" style="margin-top:14px"><h4>Unpublished model calls <small>what the probabilities implied on every replayed release — out of sample, never shown as forecasts</small></h4>
    <div class="vgrid">${c("Final direction (60 min)", sh.final)}${c("First move (5 min)", sh.initial)}${c("Reversal yes/no", sh.reversal)}</div>
    ${hz.length ? `<div class="src" style="margin-top:10px">Final-direction call checked at each horizon: ${[1, 5, 15, 30, 60].map((h) => sh[`h${h}`]?.n ? `${h} min ${sh[`h${h}`].correct}/${sh[`h${h}`].n}` : "").filter(Boolean).join(" · ")}</div>` : ""}
    <div class="src" style="margin-top:6px">This is how the system decides whether a model has earned the right to publish. A direction is published only when these results beat 50% by a statistically clear margin over at least 60 calls.</div></section>`;
}

function validationHtml(m) {
  const g = m && m.gates;
  if (!g || !m.n_events) return "";
  const cell = (title, x) => `<div><span>${title}</span><b class="${x.valid ? "pos" : ""}">${x.valid ? "Validated" : "Not validated"}</b><small>${x.n ? `${x.correct}/${x.n} out-of-sample calls right (${pctTxt(x.acc)}, lower bound ${pctTxt(x.lb)}) · Brier ${x.brier != null ? x.brier.toFixed(3) : "—"}` : "no out-of-sample calls yet"}</small></div>`;
  const r = g.reversal || {};
  return `<section class="panel card" style="margin-top:14px"><h4>Model validation <small>walk-forward, out-of-sample · ${m.n_events} releases with measured reactions</small></h4>
    <div class="vgrid">${cell("Final-direction model", g.final)}${cell("First-move model", g.initial)}
      <div><span>Reversal model</span><b class="${r.valid ? "pos" : ""}">${r.valid ? "Validated" : "Not validated"}</b><small>${r.n ? `Brier ${r.brier.toFixed(3)} vs base rate ${r.brier_base.toFixed(3)} · flagged reversals right ${r.flagged_correct}/${r.flagged}` : "no history yet"}</small></div></div>
    <div class="src" style="margin-top:10px">A component is used only while it is validated. Otherwise the forecast is "Insufficient Evidence". Validation needs ≥60 earlier out-of-sample calls with a lower bound above 50%.</div></section>`;
}

function factorHtml(fv) {
  if (!fv || !fv.length) return "";
  return `<section class="panel card" style="margin-top:14px"><h4>Which evidence has actually predicted the final move? <small>out-of-sample, each factor on its own</small></h4>
    <div class="kv">${fv.map((f) => `<div><span>${esc(f.factor)}<em>${f.n} releases with a clear signal</em></span><b class="${f.useful ? "pos" : ""}">${pctTxt(f.accuracy)}<em>${f.useful ? `useful (${f.direction})` : "no proven value yet"} · lower bound ${pctTxt(f.lower_bound)}</em></b></div>`).join("")}</div></section>`;
}

function priceChart(rx, pred, A) {
  const pts = (rx.path || []).filter((p) => p[0] >= -30 && p[0] <= 60).map((p) => [p[0], p[1]]);
  if (pts.length < 10) return `<div class="empty">No price path available.</div>`;
  const W = 760, H = 280, pl = 72, pr = 16, pt = 18, pb = 30;
  const tgt = pred && pred.status === "ready" && pred.target != null ? pred.target : null;
  const ys = pts.map((p) => p[1]).concat(tgt != null ? [tgt] : []).concat([rx.p_pre]);
  let lo = Math.min(...ys), hi = Math.max(...ys); const pad = (hi - lo) * 0.12 || hi * 1e-4; lo -= pad; hi += pad;
  const X = (m) => pl + ((m + 30) / 90) * (W - pl - pr), Y = (v) => pt + (1 - (v - lo) / (hi - lo)) * (H - pt - pb);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  const area = d + `L${X(pts[pts.length - 1][0]).toFixed(1)},${H - pb}L${X(pts[0][0]).toFixed(1)},${H - pb}Z`;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => lo + t * (hi - lo));
  const up = pred && pred.direction === "Bullish";
  const len = Math.round(pts.length * 9);
  const lab = (v) => A === "XAUUSD" ? usd(v, 0) : v.toFixed(4);
  return `<div class="chart-wrap" id="pc"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price from 30 minutes before to 60 minutes after the release">
    <defs><linearGradient id="ga" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#c9a227" stop-opacity=".18"/><stop offset="1" stop-color="#c9a227" stop-opacity="0"/></linearGradient></defs>
    ${ticks.map((t) => `<line x1="${pl}" x2="${W - pr}" y1="${Y(t)}" y2="${Y(t)}" stroke="rgba(24,22,18,.06)"/><text x="${pl - 10}" y="${Y(t) + 4}" text-anchor="end" font-size="11" fill="#8b8880">${lab(t)}</text>`).join("")}
    ${[-30, -15, 0, 15, 30, 45, 60].map((m) => `<text x="${X(m)}" y="${H - 8}" text-anchor="middle" font-size="11" fill="#8b8880">${m === 0 ? "Release" : (m > 0 ? "+" : "") + m + "m"}</text>`).join("")}
    <rect x="${X(0)}" y="${pt}" width="${X(30) - X(0)}" height="${H - pt - pb}" fill="rgba(24,22,18,.025)"/>
    <line x1="${X(0)}" x2="${X(0)}" y1="${pt}" y2="${H - pb}" stroke="#15140f" stroke-width="1.5" stroke-dasharray="3 4"/>
    <line x1="${X(-2)}" x2="${X(-2)}" y1="${pt}" y2="${H - pb}" stroke="#b9b6ad" stroke-dasharray="2 3"/>
    <line x1="${pl}" x2="${W - pr}" y1="${Y(rx.p_pre)}" y2="${Y(rx.p_pre)}" stroke="#b9b6ad" stroke-dasharray="2 4"/>
    ${tgt != null ? `<line x1="${X(-2)}" x2="${W - pr}" y1="${Y(tgt)}" y2="${Y(tgt)}" stroke="${up ? "#0e9670" : "#d4463e"}" stroke-width="1.5" stroke-dasharray="6 5"/><text x="${W - pr}" y="${Y(tgt) - 6}" text-anchor="end" font-size="11.5" font-weight="600" fill="${up ? "#0e9670" : "#d4463e"}">Target ${px(A, tgt)}</text>` : ""}
    <path d="${area}" fill="url(#ga)"/>
    <path d="${d}" fill="none" stroke="#15140f" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" class="draw" style="--len:${len};stroke-dasharray:${len}"/>
    <line class="xh" y1="${pt}" y2="${H - pb}" stroke="#8b8880" opacity="0"/><circle class="hd" r="4.5" fill="#15140f" stroke="#fff" stroke-width="2" opacity="0"/>
    <rect x="${pl}" y="0" width="${W - pl - pr}" height="${H}" fill="transparent" class="hit"/></svg><div class="chart-tip" style="opacity:0"></div></div>
    <div class="legend"><span><i style="background:#15140f"></i>${A === "XAUUSD" ? "Gold" : "EUR/USD"}</span><span><i class="dash" style="background:repeating-linear-gradient(90deg,#b9b6ad 0 3px,transparent 3px 6px)"></i>Price at release · T-2 marker</span>${tgt != null ? `<span><i class="dash" style="background:repeating-linear-gradient(90deg,${up ? "#0e9670" : "#d4463e"} 0 5px,transparent 5px 9px)"></i>Predicted target</span>` : ""}<span class="muted">Shaded: 30-min window</span></div>`;
}
function wireChart(rx, A) {
  const el = $("#pc"); if (!el) return;
  const pts = rx.path.filter((p) => p[0] >= -30 && p[0] <= 60);
  const svg = $("svg", el), tip = $(".chart-tip", el), xh = $(".xh", el), hd = $(".hd", el);
  const W = 760, pl = 72, pr = 16;
  $(".hit", el).addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) * (W / r.width);
    const m = ((sx - pl) / (W - pl - pr)) * 90 - 30;
    let best = pts[0]; for (const p of pts) if (Math.abs(p[0] - m) < Math.abs(best[0] - m)) best = p;
    const segs = svg.querySelector("path.draw").getAttribute("d").split(/[ML]/).filter(Boolean);
    const [x0, y0] = segs[pts.indexOf(best)].split(",").map(Number);
    xh.setAttribute("x1", x0); xh.setAttribute("x2", x0); xh.setAttribute("opacity", 1);
    hd.setAttribute("cx", x0); hd.setAttribute("cy", y0); hd.setAttribute("opacity", 1);
    const d = best[1] - rx.p_pre;
    tip.style.opacity = 1; tip.style.left = Math.min(r.width - 170, Math.max(0, (x0 / W) * r.width - 80)) + "px";
    tip.innerHTML = `<div class="row"><span>${best[0] === 0 ? "Release" : (best[0] > 0 ? "+" : "") + best[0] + " min"}</span><b>${px(A, best[1])}</b></div><div class="row"><span>vs release</span><b class="${d > 0 ? "pos" : d < 0 ? "neg" : ""}">${A === "XAUUSD" ? sgn(d) : mv(A, d / 0.0001)}</b></div>`;
  });
  $(".hit", el).addEventListener("mouseleave", () => { tip.style.opacity = 0; xh.setAttribute("opacity", 0); hd.setAttribute("opacity", 0); });
}

async function btDetail(app, id) {
  const d = await api(`/api/backtest/${encodeURIComponent(id)}?asset=${btAsset}&stage=${btStage}`);
  const p = d.pred, o = d.outcome, rx = d.reaction, rel = d.released || {}, A = d.asset, l = o.labels || {};
  const ready = p.status === "ready", live = d.kind.startsWith("live");
  const label = A === "XAUUSD" ? "Gold" : "EUR/USD";
  const rv = (f) => f ? `${esc(f.value)}${f.ref ? `<em>reference period ${esc(f.ref.slice(0, 7))}</em>` : ""}${f.source ? `<em>${f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.source)}</a>` : esc(f.source)}</em>` : ""}` : `<span class="muted" style="font-weight:450">Not available</span>`;
  const dirTxt = (v) => v == null ? "Flat (inconclusive)" : v ? "▲ UP" : "▼ DOWN";
  const chk = (v) => v == null ? `<span class="chip">Inconclusive</span>` : v ? `<b class="pos">✓ Right</b>` : `<b class="neg">✗ Wrong</b>`;
  app.innerHTML = `<div class="page">
    <div class="ev-hero"><a class="btn-back" href="#/backtest">${I.back}Backtest</a>
      <h1>${esc((d.title || d.name || "").split(" — ")[0])}</h1>
      <div class="hero-meta"><span>${when(d.release_utc)} ${fmt(d.release_utc, { year: "numeric" })} · ${time(d.release_utc)} ${esc(tzName())}</span>
        <span class="chip">${label} · ${d.stage === "t2" ? "T-2" : "T-30"}</span>
        <span class="chip ${live ? "ready" : ""}">${live ? "Live forecast, published before the release" : "Replay, reconstructed later without future data"}</span></div></div>
    <div class="call" style="margin-top:16px">
      <div><span class="asset">Forecast</span><div style="display:flex;gap:6px;flex-wrap:wrap">${primaryChip(p, true)}</div>
        <span class="sub">${ready ? `Scenario ${p.scenario} · ${esc(p.scenario_name)} · ${esc(p.confidence)} confidence` : esc(p.reason || "")}</span></div>
      <div><span class="asset">What happened</span><span class="big">${l.scenario ? `Scenario ${l.scenario}` : "Inconclusive"}</span>
        <span class="sub">first 5 min ${dirTxt(l.initial)} · 60 min ${dirTxt(l.final)}${l.reversal ? " · reversed" : ""}</span></div>
    </div>
    ${ready ? `<section class="panel card" style="margin-top:14px"><h4>Scorecard</h4><div class="kv">
      <div><span>Final direction (60 min)</span><b>${chk(o.final_correct)}</b></div>
      <div><span>First move (5 min)</span><b>${chk(o.initial_correct)}</b></div>
      <div><span>Two-way call</span><b>${chk(o.reversal_correct)}<em>predicted ${p.two_way ? "two-way" : "one-way"} · ${o.two_way_occurred == null ? "outcome inconclusive" : o.two_way_occurred ? "reversal occurred" : "no reversal"}</em></b></div>
      ${p.two_way ? `<div><span>Reversal order</span><b>${chk(o.order_correct)}</b></div>` : ""}
      <div><span>Exact scenario</span><b>${chk(o.scenario_correct)}</b></div></div></section>` : ""}
    <section class="panel card" style="margin-top:14px"><h4>${label} around the release <small>1-minute prices · ${esc(rx?.source || "")}</small></h4>${rx ? priceChart(rx, p, A) : `<div class="empty">No price data.</div>`}</section>
    <div class="twocol">
      <div class="stack">
        <section class="panel card"><h4>Direction after the news</h4>
          <div class="htable"><div class="hrow head"><span>After</span><span>Move</span><span>Direction</span><span></span>${ready ? "<span>Final call</span>" : ""}</div>
          ${Object.entries(o.horizons || {}).map(([h, v]) => `<div class="hrow"><span>${h} min</span><span class="num ${v.units > 0 ? "pos" : v.units < 0 ? "neg" : ""}">${mv(A, v.units)}</span><span>${v.direction === "up" ? "▲ Up" : v.direction === "down" ? "▼ Down" : "◆ Flat"}</span><span></span>${ready ? `<span>${v.correct == null ? "—" : v.correct ? '<b class="pos">✓</b>' : '<b class="neg">✗</b>'}</span>` : ""}</div>`).join("")}</div></section>
        ${p.explanation ? `<section class="panel card"><h4>Explanation at the time</h4><p class="prose">${esc(p.explanation)}</p></section>` : ""}
        ${p.steps ? `<section class="panel card"><h4>How the forecast was calculated</h4>${stepsHtml(p)}</section>` : ""}
        <section class="panel card"><h4>Evidence available at the time</h4>${(p.factors || []).sort((a, b) => Math.abs(b.signal * b.weight) - Math.abs(a.signal * a.weight)).map((f) => {
          const tag = f.signal > 0.1 ? ["pos", `Pointed ${label} ↑`] : f.signal < -0.1 ? ["neg", `Pointed ${label} ↓`] : ["flat", "Neutral"];
          return `<div class="factor"><div><b>${esc(f.name)}</b><p>${esc(f.evidence)}</p><div class="src">${(f.sources || []).filter(Boolean).map(srcOf).join(" · ")}</div></div><div class="tag ${tag[0]}">${tag[1]}</div></div>`; }).join("") || `<p class="muted">No factor data stored.</p>`}
          ${p.regime ? `<div class="src" style="margin-top:8px">Regime at the time: ${esc(p.regime)}</div>` : ""}</section>
      </div>
      <div class="stack">
        <section class="panel card"><h4>What was released</h4><div class="kv">
          <div><span>Previous</span><b>${rv(rel.previous)}</b></div>
          <div><span>Forecast (consensus)</span><b>${rel.forecast ? rv(rel.forecast) : `<span class="muted" style="font-weight:450">No free historical consensus archive</span>`}</b></div>
          <div><span>Actual</span><b>${rv(rel.actual)}</b></div></div>
          ${rel.statement ? `<p class="prose" style="font-size:13px;margin-top:10px">“${esc(rel.statement)}”</p>` : ""}
          <div class="src" style="margin-top:8px">${d.source_url ? `Schedule: <a href="${esc(d.source_url)}" target="_blank" rel="noopener">${esc(d.source)}</a>` : ""}</div></section>
        ${ready && p.alternatives ? `<section class="panel card"><h4>Alternatives considered</h4><div class="kv">${p.alternatives.map((x) => `<div><span>${x.scenario} · ${esc(x.name)}</span><b>${Math.round(x.relative_weight * 100)}%${x.chosen ? "<em>selected</em>" : ""}</b></div>`).join("")}</div></section>` : ""}
        <section class="panel card"><h4>Scoring rules</h4><p class="prose" style="font-size:13.5px">First move = price 5 minutes after the release versus 1 minute before it; final = 60 minutes after. Moves smaller than ${A === "XAUUSD" ? "2" : "1"} bp count as flat and are inconclusive. Scenario A/B = continuation; C/D = the first move reversed by 60 minutes.</p><div class="src">Engine ${esc(d.engine_version)}</div></section>
      </div>
    </div>
  </div>`;
  if (rx) wireChart(rx, A);
}

/* ---------- live prices (every 5 s) ---------- */
let lastSpot = null;
async function pollSpot() {
  try {
    const q = await api("/api/spot");
    const g = q.XAUUSD, eu = q.EURUSD;
    if (g && g.price) {
      const dir = lastSpot == null ? 0 : Math.sign(g.price - lastSpot);
      $("#tk-price").textContent = usd(g.price);
      const ba = $("#tk-ask"); if (ba) ba.textContent = g.ask ? `ask ${usd(g.ask)}` : "";
      const ch = $("#tk-chg"); ch.textContent = secs(g.as_of); ch.className = dir > 0 ? "pos" : dir < 0 ? "neg" : "";
      $("#ticker").title = `${g.source} · ${secs(g.as_of)}`;
      if (dir) { const t = $("#ticker"); t.classList.remove("up", "down"); void t.offsetWidth; t.classList.add(dir > 0 ? "up" : "down"); }
      lastSpot = g.price;
    }
    for (const [a, v] of [["XAUUSD", g], ["EURUSD", eu]]) {
      if (!v || !v.price) continue;
      document.querySelectorAll(`[data-live="${a}"]`).forEach((x) => (x.textContent = a === "XAUUSD" ? usd(v.price) : v.price.toFixed(5)));
      document.querySelectorAll(`[data-live-t="${a}"]`).forEach((x) => (x.textContent = `bid · ask ${a === "XAUUSD" ? usd(v.ask) : v.ask.toFixed(5)} · ${secs(v.as_of)}`));
    }
  } catch {}
}
pollSpot();
setInterval(() => !document.hidden && pollSpot(), 5000);
document.addEventListener("visibilitychange", () => !document.hidden && pollSpot());

/* ---------- boot ---------- */
window.addEventListener("hashchange", route);
window.addEventListener("scroll", () => $("#topbar").classList.toggle("scrolled", window.scrollY > 4), { passive: true });
if ($("#clock")) setInterval(() => { $("#clock").textContent = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date()) + " " + tzName(); }, 1000);
route();
