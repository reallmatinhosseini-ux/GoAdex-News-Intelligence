/* GoAdex public site — motion, live chart, real counts, track record, calendar, forms.
   Shared by the home page and /investors (every feature checks its elements exist). CSP: script-src 'self'. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fine = matchMedia("(pointer: fine)").matches;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtInt = (n) => Number(n).toLocaleString("en-US");
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const hhmm = (d) => new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
  $$("#yr").forEach((el) => (el.textContent = new Date().getFullYear()));

  /* ── scroll: nav, progress, device tilt ───────────────── */
  const nav = $("#nav"), bar = $("#progress"), device = $("#device");
  let ticking = false;
  function onScroll() {
    const y = scrollY, h = document.documentElement.scrollHeight - innerHeight;
    nav && nav.classList.toggle("scrolled", y > 10);
    if (bar) bar.style.transform = `scaleX(${h > 0 ? y / h : 0})`;
    if (device && !reduce) {
      const p = Math.min(1, y / 520);
      device.style.transform = `rotateX(${18 * (1 - p)}deg) scale(${0.94 + 0.06 * p})`;
    }
    ticking = false;
  }
  addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(onScroll); } }, { passive: true });
  onScroll();
  if (device) { if (reduce) device.style.transform = "none"; setTimeout(() => device.classList.add("on"), 300); }

  /* ── reveal on scroll ─────────────────────────────────── */
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { threshold: 0.15, rootMargin: "0px 0px -6% 0px" });
  const observe = () => $$("[data-reveal]:not(.in), .x-sc, .x-b, .x-model:not(.in)").forEach((el) => io.observe(el));
  observe();

  /* ── spotlight + magnetic ─────────────────────────────── */
  if (fine && !reduce) {
    $$(".spot").forEach((el) => el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${e.clientX - r.left}px`); el.style.setProperty("--my", `${e.clientY - r.top}px`);
    }));
    $$(".magnetic").forEach((el) => {
      el.addEventListener("pointermove", (e) => {
        const r = el.getBoundingClientRect();
        el.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * 0.18}px, ${(e.clientY - r.top - r.height / 2) * 0.28}px)`;
      });
      el.addEventListener("pointerleave", () => { el.style.transform = ""; });
    });
  }

  /* ── FAQ: one open at a time ──────────────────────────── */
  $$(".x-faq details").forEach((d) => d.addEventListener("toggle", () => {
    if (d.open) $$(".x-faq details").forEach((o) => o !== d && (o.open = false));
  }));

  /* ── segmented controls with sliding ink ──────────────── */
  function segment(seg, onPick) {
    if (!seg) return;
    const ink = $(".x-seg-ink", seg);
    const move = () => { const b = $("button.on", seg); if (b && ink) { ink.style.width = b.offsetWidth + "px"; ink.style.transform = `translateX(${b.offsetLeft - 3}px)`; } };
    $$("button", seg).forEach((b) => b.addEventListener("click", () => {
      $$("button", seg).forEach((x) => x.classList.toggle("on", x === b)); move(); onPick(b);
    }));
    requestAnimationFrame(move); document.fonts?.ready.then(move); addEventListener("resize", move);
  }

  /* ── timeline: active step drives the clock ───────────── */
  const steps = $$("#tl-steps li"), arc = $("#tl-arc"), tlT = $("#tl-time"), tlL = $("#tl-label");
  if (steps.length) {
    const C = 2 * Math.PI * 52;
    const setStep = (li) => {
      if (li.classList.contains("on")) return;
      steps.forEach((s) => s.classList.toggle("on", s === li));
      arc.style.strokeDashoffset = C * (1 - Number(li.dataset.p));
      tlT.style.opacity = 0;
      setTimeout(() => { tlT.textContent = li.dataset.t; tlL.textContent = li.dataset.l; tlT.style.opacity = 1; }, 150);
    };
    const tio = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && setStep(e.target)), { rootMargin: "-45% 0px -45% 0px" });
    steps.forEach((s) => tio.observe(s));
    setStep(steps[0]);
  }

  /* ── animated hash (illustrative fingerprint) ─────────── */
  const hashEl = $("#hash"), hex = "0123456789abcdef";
  if (hashEl) setInterval(() => {
    if (document.hidden || reduce) return;
    const target = Array.from({ length: 8 }, () => Array.from({ length: 4 }, () => hex[Math.random() * 16 | 0]).join("")).join(" ");
    let f = 0;
    const t = setInterval(() => {
      f++;
      hashEl.textContent = target.split("").map((c, i) => c === " " ? " " : i < f * 2 ? c : hex[Math.random() * 16 | 0]).join("");
      if (f * 2 >= target.length) clearInterval(t);
    }, 30);
  }, 4000);

  /* ── count-up for real database figures ───────────────── */
  function countUp(el, to) {
    const from = Number(el.dataset.v || 0);
    el.dataset.v = to;
    if (reduce || from === to) { el.textContent = fmtInt(to); return; }
    const t0 = performance.now(), dur = from === 0 ? 1800 : 700;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 4);
      el.textContent = fmtInt(Math.round(from + (to - from) * e));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  const statEls = $$("[data-count]");
  let stats = null, statsShown = !statEls.length;
  const sio = new IntersectionObserver((es) => {
    if (es.some((e) => e.isIntersecting)) { statsShown = true; paintStats(); sio.disconnect(); }
  }, { threshold: 0.3 });
  statEls.forEach((el) => sio.observe(el));
  function paintStats() {
    if (!stats || !statsShown) return;
    statEls.forEach((el) => { const k = el.dataset.count; if (stats[k] != null) countUp(el, stats[k]); });
  }

  /* ── upcoming releases (bento) ────────────────────────── */
  let upcoming = [];
  function paintNext() {
    const ul = $("#next"); if (!ul) return;
    if (!upcoming.length) { ul.innerHTML = '<li class="x-next-empty">No high-impact releases scheduled in the current calendar window.</li>'; return; }
    ul.innerHTML = upcoming.slice(0, 4).map((e) => {
      const d = new Date(e.release_utc);
      const when = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" }).format(d);
      return `<li><div class="t"><b>${esc(e.title)}</b><small>${esc(e.country)} · ${when}</small></div><time data-ts="${d.getTime()}">—</time></li>`;
    }).join("");
    tickCountdowns();
  }
  function fmtLeft(ms) {
    let s = Math.max(0, Math.round(ms / 1000));
    if (s === 0) return "Live";
    const d = Math.floor(s / 86400); s %= 86400;
    const h = Math.floor(s / 3600); s %= 3600; const m = Math.floor(s / 60); s %= 60;
    return d ? `${d}d ${h}h ${m}m` : `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  function tickCountdowns() {
    const now = Date.now();
    $$("time[data-ts]").forEach((el) => (el.textContent = fmtLeft(Number(el.dataset.ts) - now)));
  }
  setInterval(tickCountdowns, 1000);

  /* ── live chart (12 h of recorded prices) ─────────────── */
  const cv = $("#chart");
  const series = { XAUUSD: [], EURUSD: [] };
  let inst = "XAUUSD", drawK = 0, hoverX = null;
  const lastPrice = {};
  const fmtP = (i, p) => (i === "XAUUSD" ? "$" + p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : p.toFixed(5));
  let ctx, draw = () => {}, animateIn = () => {}, paintPrice = () => {};
  if (cv) {
    ctx = cv.getContext("2d");
    const empty = $("#chart-empty"), pxEl = $("#px"), chgEl = $("#chg");
    const size = () => {
      const r = cv.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
      cv.width = r.width * dpr; cv.height = r.height * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    addEventListener("resize", () => { size(); draw(); });
    draw = () => {
      const pts = series[inst], W = cv.clientWidth, H = cv.clientHeight;
      ctx.clearRect(0, 0, W, H);
      empty.hidden = pts.length > 1;
      if (pts.length < 2) return;
      const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
      let lo = Infinity, hi = -Infinity;
      pts.forEach((p) => { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); });
      const pad = (hi - lo) * 0.18 || hi * 0.0005; lo -= pad; hi += pad;
      const X = (t) => 4 + ((t - t0) / Math.max(1, t1 - t0)) * (W - 64);
      const Y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (H - 30);
      const up = pts[pts.length - 1][1] >= pts[0][1];
      const col = up ? "14,150,112" : "212,70,62";
      ctx.font = "500 11px Inter, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      for (let g = 0; g <= 3; g++) {
        const v = lo + ((hi - lo) * g) / 3, y = Y(v);
        ctx.strokeStyle = "rgba(24,22,18,.06)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W - 58, y); ctx.stroke();
        ctx.fillStyle = "#8b8880"; ctx.fillText(v.toFixed(inst === "XAUUSD" ? 1 : 4), W - 52, y);
      }
      const n = Math.max(2, Math.floor(pts.length * drawK));
      const vis = pts.slice(0, n);
      // split at recording gaps (> 5 min) so missing data is never drawn as a fake line
      const segs = [[]];
      vis.forEach((p, i) => { if (i && p[0] - vis[i - 1][0] > 300) segs.push([]); segs[segs.length - 1].push(p); });
      const last = vis[vis.length - 1];
      const grad = ctx.createLinearGradient(0, 0, 0, H);
      grad.addColorStop(0, `rgba(${col},.18)`); grad.addColorStop(1, `rgba(${col},0)`);
      segs.forEach((sg) => {
        if (sg.length < 2) return;
        ctx.beginPath();
        sg.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
        ctx.lineTo(X(sg[sg.length - 1][0]), H); ctx.lineTo(X(sg[0][0]), H); ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
        ctx.beginPath();
        sg.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
        ctx.strokeStyle = `rgb(${col})`; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
      });
      if (drawK >= 1) {
        const lx = X(last[0]), ly = Y(last[1]), ph = (performance.now() % 2000) / 2000;
        ctx.beginPath(); ctx.arc(lx, ly, 4 + ph * 12, 0, Math.PI * 2); ctx.fillStyle = `rgba(${col},${0.35 * (1 - ph)})`; ctx.fill();
        ctx.beginPath(); ctx.arc(lx, ly, 4.5, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill();
        ctx.lineWidth = 2.5; ctx.strokeStyle = `rgb(${col})`; ctx.stroke();
      }
      if (hoverX != null && drawK >= 1) {
        let best = pts[0];
        for (const p of pts) if (Math.abs(X(p[0]) - hoverX) < Math.abs(X(best[0]) - hoverX)) best = p;
        const hx = X(best[0]), hy = Y(best[1]);
        ctx.setLineDash([3, 4]); ctx.strokeStyle = "rgba(24,22,18,.3)"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(hx, 0); ctx.lineTo(hx, H - 18); ctx.stroke(); ctx.setLineDash([]);
        ctx.beginPath(); ctx.arc(hx, hy, 5, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill(); ctx.strokeStyle = "#15140f"; ctx.lineWidth = 2; ctx.stroke();
        const label = `${best[1].toFixed(inst === "XAUUSD" ? 2 : 5)}  ·  ${new Date(best[0] * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
        ctx.font = "600 11.5px Inter, sans-serif";
        const tw = ctx.measureText(label).width + 16, tx = Math.min(Math.max(hx - tw / 2, 0), W - tw - 58);
        ctx.fillStyle = "#15140f"; ctx.beginPath(); ctx.roundRect(tx, H - 18, tw, 18, 6); ctx.fill();
        ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.fillText(label, tx + 8, H - 9);
      }
    };
    const loop = () => { if (!document.hidden) draw(); requestAnimationFrame(loop); };
    animateIn = () => {
      if (reduce) { drawK = 1; return; }
      drawK = 0; const t0 = performance.now();
      const s = (t) => { const k = Math.min(1, (t - t0) / 1500); drawK = 1 - Math.pow(1 - k, 3); if (k < 1) requestAnimationFrame(s); };
      requestAnimationFrame(s);
    };
    cv.addEventListener("pointermove", (e) => { hoverX = e.clientX - cv.getBoundingClientRect().left; });
    cv.addEventListener("pointerleave", () => { hoverX = null; });
    paintPrice = (flash) => {
      const pts = series[inst], p = lastPrice[inst] ?? (pts.length ? pts[pts.length - 1][1] : null);
      if (p == null) return;
      const prev = Number(pxEl.dataset.p || 0);
      pxEl.textContent = fmtP(inst, p); pxEl.dataset.p = p;
      if (flash && prev && p !== prev) {
        pxEl.classList.remove("up", "dn"); void pxEl.offsetWidth; pxEl.classList.add(p > prev ? "up" : "dn");
        setTimeout(() => pxEl.classList.remove("up", "dn"), 900);
      }
      if (pts.length > 1) {
        const c = ((p - pts[0][1]) / pts[0][1]) * 100;
        chgEl.textContent = `${c >= 0 ? "▲" : "▼"} ${Math.abs(c).toFixed(2)}% · 12h`;
        chgEl.className = "x-chg " + (c >= 0 ? "pos" : "neg");
      }
    };
    segment($("#seg"), (b) => { inst = b.dataset.i; pxEl.dataset.p = ""; paintPrice(false); animateIn(); });
    requestAnimationFrame(size);
    loop();
  }

  let firstOverview = true;
  async function loadOverview() {
    try {
      const d = await (await fetch("/api/public/overview")).json();
      series.XAUUSD = d.series.XAUUSD || []; series.EURUSD = d.series.EURUSD || [];
      stats = d.stats; paintStats();
      $$("[data-stat]").forEach((el) => { const v = d.stats[el.dataset.stat]; if (v != null) countUp(el, v); });
      upcoming = d.upcoming || []; paintNext();
      paintPrice(false);
      if (firstOverview) { firstOverview = false; animateIn(); }
    } catch {}
  }
  async function loadSpot() {
    if (document.hidden || !cv) return;
    try {
      const d = await (await fetch("/api/public/spot")).json();
      for (const k of ["XAUUSD", "EURUSD"]) {
        if (!d[k]) continue;
        lastPrice[k] = d[k].price;
        const ts = Math.floor(new Date(d[k].as_of).getTime() / 1000), s = series[k];
        if (!s.length || ts > s[s.length - 1][0]) s.push([ts, d[k].price]);
        while (s.length > 2 && s[0][0] < ts - 12 * 3600) s.shift();
      }
      paintPrice(true);
    } catch {}
  }
  loadOverview().then(loadSpot);
  setInterval(loadSpot, 5000);
  setInterval(loadOverview, 60000);

  /* ── track record + model status (real validation data) ─ */
  const wilson = (c, n, z = 1.96) => {
    if (!n) return null;
    const p = c / n, d = 1 + (z * z) / n, ctr = (p + (z * z) / (2 * n)) / d;
    const h = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / d;
    return [Math.max(0, ctr - h), Math.min(1, ctr + h), p];
  };
  const pct = (x) => Math.round(x * 100) + "%";
  if ($("#models") || $("#ir-status")) fetch("/api/public/metrics").then((r) => r.json()).then((m) => {
    const need = m.gate.min_calls, names = { XAUUSD: "Gold", EURUSD: "EUR/USD" }, stg = { t30: "T-30", t2: "T-2" };
    let cleared = 0, total = 0, replayed = 0, first = null, last = null;
    const cards = [];
    for (const a of ["XAUUSD", "EURUSD"]) for (const st of ["t30", "t2"]) {
      const r = m.assets[a][st]; total++; replayed = Math.max(replayed, r.replayed);
      if (r.published > 0) cleared++;
      if (r.first && (!first || r.first < first)) first = r.first;
      if (r.last && (!last || r.last > last)) last = r.last;
      const s = r.shadow.final || { n: 0, correct: 0 }, ci = wilson(s.correct, s.n);
      cards.push(`<div class="x-model" data-reveal style="--d:${cards.length * 0.07}s">
        <div class="x-model-h"><b>${names[a]}<small>${stg[st]}</small></b><span class="x-badge ${r.published > 0 ? "ok" : "wait"}">${r.published > 0 ? "Publishing" : "Validating"}</span></div>
        <div class="x-model-nums"><div><span>Replayed</span><b>${fmtInt(r.replayed)}</b></div><div><span>Published</span><b>${fmtInt(r.published)}</b></div><div><span>Shadow hit rate</span><b>${s.n ? pct(s.correct / s.n) : "—"}</b></div></div>
        <div><div class="x-ci-l"><span>95% interval${ci ? ` · ${pct(ci[0])}–${pct(ci[1])}` : ""}</span><span>50% line</span></div>
          <div class="x-ci"><i class="half"></i>${ci ? `<i class="rng" style="left:${ci[0] * 100}%;width:${(ci[1] - ci[0]) * 100}%"></i><i class="pt" style="left:${ci[2] * 100}%"></i>` : ""}</div></div>
        <div class="x-gp"><span>Gate</span><i><em style="--w:${Math.min(100, (s.n / need) * 100)}%"></em></i><span>${s.n}/${need} calls</span></div>
      </div>`);
    }
    if ($("#models")) {
      $("#models").innerHTML = cards.join(""); observe();
      $("#tr-cleared").textContent = cleared; $("#tr-total").textContent = total; $("#g-n").textContent = `≥ ${need}`;
      $("#tr-cleared-l").textContent = cleared === 0
        ? "No model has cleared the gate yet, so every forecast is currently withheld as “Insufficient Evidence”. That is the system working as designed."
        : `${cleared} model${cleared > 1 ? "s have" : " has"} cleared the publication gate.`;
      const f = (s) => s ? new Date(s).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—";
      $("#tr-note").textContent = `Shadow calls are produced and scored exactly like published ones but never shown to users. Hit rate = final direction after 60 minutes. Interval: Wilson score, 95%. Releases replayed ${f(first)} – ${f(last)} · engine ${m.engine_version} · as of ${new Date(m.as_of).toLocaleString([], { hour12: false })}.`;
    }
    if ($("#ir-status")) {
      $("#ir-status").textContent = cleared ? `${cleared} of ${total} models publishing` : `Validating · 0 of ${total} cleared`;
      $("#ir-replayed") && ($("#ir-replayed").textContent = `${fmtInt(replayed)} replayed`);
    }
  }).catch(() => { $("#models") && ($("#models").innerHTML = '<p class="x-tr-note">Validation data is temporarily unavailable.</p>'); });

  /* ── economic calendar (real) ─────────────────────────── */
  if ($("#cal-body")) {
    let calRows = [], week = 0, filter = "all";
    $("#cal-tz").textContent = `Times in ${tz}`;
    const num = (s) => { const m = String(s || "").replace(/,/g, "").match(/-?\d+(\.\d+)?/); return m ? parseFloat(m[0]) : null; };
    const paintCal = () => {
      const body = $("#cal-body"), now = Date.now();
      const rows = calRows.filter((r) => filter === "all" || (filter === "high" && r.impact === "high") || (filter === "cov" && r.tracked));
      if (!rows.length) { body.innerHTML = `<tr class="x-cal-empty"><td colspan="7">No releases match this view.</td></tr>`; return; }
      const nextT = week === 0 ? rows.map((r) => new Date(r.release_utc).getTime()).find((t) => t > now) : null;
      let day = "", html = "";
      for (const r of rows) {
        const d = new Date(r.release_utc), dk = d.toDateString();
        if (dk !== day) {
          day = dk;
          const n = rows.filter((x) => new Date(x.release_utc).toDateString() === dk).length;
          html += `<tr class="day"><td colspan="7">${d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}<span>${n} release${n > 1 ? "s" : ""}</span></td></tr>`;
        }
        const t = d.getTime(), past = t <= now, isNext = t === nextT;
        const a = num(r.actual), f = num(r.forecast);
        const dir = a != null && f != null && a !== f ? ` <span class="${a > f ? "pos" : "neg"}" style="font-size:10px">${a > f ? "▲" : "▼"}</span>` : "";
        html += `<tr class="row ${past ? "past" : ""} ${isNext ? "nx" : ""}">
          <td class="t">${hhmm(d)}</td><td class="c"><span>${esc(r.country)}</span></td>
          <td><span class="x-imp ${esc(r.impact)}"><i></i><i></i><i></i></span></td>
          <td class="ev">${esc(r.title)}${r.tracked ? '<span class="x-cov">Covered</span>' : ""}${isNext ? `<span class="nx-tag">Next · <time data-ts="${t}">—</time></span>` : ""}</td>
          <td class="n">${r.actual ? `<b>${esc(r.actual)}</b>${dir}` : "—"}</td>
          <td class="n">${esc(r.forecast) || "—"}</td><td class="n p">${esc(r.previous) || "—"}</td></tr>`;
      }
      body.innerHTML = html; tickCountdowns();
    };
    const loadCal = async () => {
      try {
        const d = await (await fetch(`/api/public/calendar?week=${week}`)).json();
        calRows = d.rows.map((r) => ({ ...r, impact: (r.impact || "").toLowerCase() }));
        paintCal();
      } catch { $("#cal-body").innerHTML = `<tr class="x-cal-empty"><td colspan="7">The calendar is temporarily unavailable.</td></tr>`; }
    };
    segment($("#cal-week"), (b) => { week = Number(b.dataset.w); loadCal(); });
    $$("#cal-filter button").forEach((b) => b.addEventListener("click", () => {
      $$("#cal-filter button").forEach((x) => x.classList.toggle("on", x === b)); filter = b.dataset.f; paintCal();
    }));
    loadCal(); setInterval(() => !document.hidden && loadCal(), 120000);
  }

  /* ── forms: waitlist + investor interest ──────────────── */
  function bindForm(form, url, okText) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = $(".x-wl-msg", form), btn = $("button[type=submit]", form), label = $("span", btn);
      const data = Object.fromEntries(new FormData(form));
      const fail = (t) => { msg.className = "x-wl-msg err"; msg.textContent = t; };
      if (!/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(data.email || "")) return fail("Please enter a valid email address.");
      if ("name" in data && !data.name.trim()) return fail("Please enter your full name.");
      if ("investor_type" in data && !data.investor_type) return fail("Please choose the type of organization you represent.");
      const consent = $("input[name=consent]", form);
      if (consent && !consent.checked) return fail("Please confirm the acknowledgement above.");
      const orig = label.textContent;
      btn.disabled = true; label.textContent = "Sending…";
      try {
        const r = await fetch(url, {
          method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
          body: JSON.stringify({ ...data, source: document.body.dataset.page || "landing" }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || "Something went wrong. Please try again.");
        form.classList.add("done");
        msg.className = "x-wl-msg ok"; msg.textContent = okText(j);
        const em = $("input[type=email]", form); if (url === "/api/waitlist") em.value = "";
        label.textContent = url === "/api/waitlist" ? "You’re in ✓" : "Received ✓";
      } catch (err) {
        fail(err.message); btn.disabled = false; label.textContent = orig;
      }
    });
  }
  $$("form[data-wl]").forEach((f) => bindForm(f, "/api/waitlist", (j) => j.status === "exists"
    ? "You’re already on the list — we’ll be in touch." : "You’re on the list. We’ll email you when your spot opens."));
  $$("form[data-inv]").forEach((f) => bindForm(f, "/api/investor-interest",
    () => "Thank you — your interest has been registered. GoAdex Holding will be in touch."));
})();
