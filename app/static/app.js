"use strict";
// Montierth kitchen panel. One page, three jobs: glanceable house status,
// the grandkid slideshow (tap = slideshow mode, stays until dismissed), and a
// fullscreen front-door camera that pops itself when Frigate sees a person.

const $ = (id) => document.getElementById(id);
const el = {
  clock: $("clock"), meridiem: $("meridiem"), date: $("date"),
  wxIcon: $("wx-icon"), outTemp: $("out-temp"), wxCond: $("wx-cond"), wxHilo: $("wx-hilo"),
  inTemp: $("in-temp"), haBanner: $("ha-banner"),
  family: $("family-tile"), slideshow: $("slideshow"), slideshowTap: $("slideshow-tap"),
  statusList: $("status-list"), camRow: $("cam-row"), forecast: $("forecast"), metrics: $("metrics"),
  stockK: $("stock-k"), stockV: $("stock-v"),
  camOverlay: $("cam-overlay"), camPoster: $("cam-poster"), camVideo: $("cam-video"),
  camTitle: $("cam-title"), camNote: $("cam-note"),
  histOverlay: $("hist-overlay"), histTitle: $("hist-title"), histNow: $("hist-now"),
  histChart: $("hist-chart"), histStats: $("hist-stats"),
};

let boot = null;
const states = {};
let forecast = [];
let stock = null;

// ---- weather icons (inline SVG: the Pi has no emoji font) -------------------

const CLOUD = '<path d="M7 18h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 11.1 3.5 3.5 0 0 0 7 18z"/>';
const SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>';
const MOON = '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>';
const SMALL_SUN = '<circle cx="17.5" cy="7" r="2.6"/><path d="M17.5 1.6v1.4M22.9 7h-1.4M21.3 3.2l-1 1M13.7 3.2l1 1"/>';
const LOW_CLOUD = '<path d="M6 20h9.5a3.6 3.6 0 0 0 .5-7.17A5.4 5.4 0 0 0 5.2 13.8 3.15 3.15 0 0 0 6 20z"/>';
const ICONS = {
  sunny: SUN,
  "clear-night": MOON,
  partlycloudy: SMALL_SUN + LOW_CLOUD,
  cloudy: CLOUD,
  fog: '<path d="M7 13h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 6.1 3.5 3.5 0 0 0 7 13zM4 17h16M6 21h12"/>',
  rainy: '<path d="M7 14h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 7.1 3.5 3.5 0 0 0 7 14zM8 17l-1 3M12 17l-1 3M16 17l-1 3"/>',
  pouring: '<path d="M7 14h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 7.1 3.5 3.5 0 0 0 7 14zM7 17l-1.5 4M11 17l-1.5 4M15 17l-1.5 4M19 17l-1.5 4"/>',
  snowy: '<path d="M7 14h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 7.1 3.5 3.5 0 0 0 7 14z"/><path d="M8 18h.01M12 18h.01M16 18h.01M10 21h.01M14 21h.01" stroke-width="2.6"/>',
  lightning: '<path d="M7 14h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 7.1 3.5 3.5 0 0 0 7 14zM13 15l-3 4h3l-2 4"/>',
  windy: '<path d="M3 9h11a3 3 0 1 0-3-3M3 14h15a3 3 0 1 1-3 3M3 19h7"/>',
};
ICONS["snowy-rainy"] = ICONS.snowy; ICONS.hail = ICONS.snowy; ICONS["lightning-rainy"] = ICONS.lightning;
ICONS["windy-variant"] = ICONS.windy; ICONS.exceptional = ICONS.cloudy;
const COND_TEXT = {
  sunny: "Sunny", "clear-night": "Clear", partlycloudy: "Partly cloudy", cloudy: "Cloudy", fog: "Fog",
  rainy: "Rain", pouring: "Heavy rain", snowy: "Snow", "snowy-rainy": "Sleet", hail: "Hail",
  lightning: "Thunderstorms", "lightning-rainy": "Thunderstorms", windy: "Windy", "windy-variant": "Windy",
  exceptional: "Unusual weather",
};
function icon(cond) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">${ICONS[cond] || ICONS.cloudy}</svg>`;
}

// ---- rendering ---------------------------------------------------------------

function num(entityId, decimals = 0) {
  const v = parseFloat(states[entityId]?.state);
  return Number.isFinite(v) ? v.toFixed(decimals) : "--";
}

const STATUS_WORDS = {
  lock: { locked: ["Locked", "ok"], unlocked: ["Unlocked", "warn"], locking: ["Locking", "warn"],
          unlocking: ["Unlocking", "warn"], jammed: ["Jammed", "alert"] },
  garage: { closed: ["Closed", "ok"], open: ["Open", "warn"], opening: ["Opening", "warn"], closing: ["Closing", "warn"] },
  door: { off: ["Closed", "ok"], on: ["Open", "warn"] },
};

function renderStatus() {
  el.statusList.innerHTML = "";
  for (const item of boot.status_items) {
    const s = states[item.entity]?.state;
    const [word, level] = (STATUS_WORDS[item.kind] || {})[s] || ["--", ""];
    const row = document.createElement("div");
    row.className = `status-row ${level}`;
    row.innerHTML = `<span class="status-label"></span><span class="status-value"></span>`;
    row.children[0].textContent = item.label;
    row.children[1].textContent = word;
    el.statusList.appendChild(row);
  }
}

function renderMetrics() {
  el.metrics.innerHTML = "";
  for (const m of boot.metrics) {
    const raw = parseFloat(states[m.entity]?.state);
    let level = "";
    if (Number.isFinite(raw)) {
      if (m.alert != null && raw >= m.alert) level = "alert";
      else if (m.warn != null && raw >= m.warn) level = "warn";
    }
    const box = document.createElement("div");
    box.className = `metric tappable ${level}`;
    box.addEventListener("click", () => openHistory(m));
    const dec = m.decimals || 0;
    const shown = Number.isFinite(raw)
      ? raw.toLocaleString([], { minimumFractionDigits: dec, maximumFractionDigits: dec }) : "--";
    box.innerHTML = `<div class="metric-k"></div><div class="metric-v"></div>`;
    box.children[0].textContent = m.label;
    box.children[1].innerHTML = `${shown}${m.unit ? `<small>${m.unit}</small>` : ""}`;
    el.metrics.appendChild(box);
  }
}

function renderWeather() {
  const e = boot.entities;
  el.outTemp.textContent = num(e.outdoor_temp);
  el.inTemp.textContent = num(e.indoor_temp);
  const cond = states[e.weather]?.state;
  el.wxIcon.innerHTML = icon(cond);
  el.wxCond.textContent = COND_TEXT[cond] || "";
}

function renderForecast() {
  el.forecast.innerHTML = "";
  const now = Date.now();
  const upcoming = forecast.filter((f) => new Date(f.datetime).getTime() > now - 30 * 60000);
  const day = upcoming.slice(0, 24).map((f) => f.temperature).filter(Number.isFinite);
  el.wxHilo.textContent = day.length ? `High ${Math.round(Math.max(...day))}°  ·  Low ${Math.round(Math.min(...day))}°` : "";
  for (const f of upcoming.filter((_, i) => i % 2 === 0).slice(0, 6)) {
    const d = new Date(f.datetime);
    let h = d.getHours();
    const mer = h < 12 ? "am" : "pm";
    h = h % 12 || 12;
    const pop = f.precipitation_probability;
    const box = document.createElement("div");
    box.className = "fc";
    box.innerHTML = `<div class="fc-time">${h}${mer}</div><div class="fc-icon">${icon(f.condition)}</div>` +
      `<div class="fc-temp">${Math.round(f.temperature)}°</div><div class="fc-pop">${pop >= 20 ? `${Math.round(pop)}%` : ""}</div>`;
    el.forecast.appendChild(box);
  }
}

function renderStock() {
  const cfg = boot?.stock;
  if (!cfg) return;
  el.stockK.textContent = cfg.label || "";
  const price = stock?.price;
  el.stockV.textContent = Number.isFinite(price) ? price.toFixed(cfg.decimals ?? 2) : "--";
  // Green above yesterday's close, red below. Plain ink when a source gave us
  // no close to compare against, or the price has not moved off it.
  const prev = stock?.previous_close;
  const dir = Number.isFinite(price) && Number.isFinite(prev) && price !== prev
    ? (price > prev ? "up" : "down") : "";
  el.stockV.className = dir ? `stock-v ${dir}` : "stock-v";
}

function renderAll() { renderWeather(); renderStatus(); renderMetrics(); }

function tickClock() {
  const now = new Date();
  const h = now.getHours();
  el.clock.textContent = `${h % 12 || 12}:${String(now.getMinutes()).padStart(2, "0")}`;
  el.meridiem.textContent = h < 12 ? "AM" : "PM";
  el.date.textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}

// ---- slideshow: tap = slideshow mode, stays until tapped / swiped away -------
// The overlay owns all input over the iframe (the viewer can't see kiosk
// touches itself): tap -> the viewer decides (photo = toggle fullscreen, video =
// expand + play) and answers with slideshow-fullscreen; horizontal swipe = nav;
// swipe down = leave slideshow mode. Unlike the Beelink kitchen there is NO
// auto-return: slideshow mode is a mode, and survives a page reload.

const FULL_KEY = "panel.slideshowFull";
function setFull(on) {
  el.family.classList.toggle("fullscreen", on);
  try { localStorage.setItem(FULL_KEY, on ? "1" : "0"); } catch (_) { /* private mode */ }
}
function isFull() { return el.family.classList.contains("fullscreen"); }

let down = null;
el.slideshowTap.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
el.slideshowTap.addEventListener("pointercancel", () => { down = null; });
el.slideshowTap.addEventListener("pointerup", (e) => {
  if (!down) return;
  const dx = e.clientX - down.x, dy = e.clientY - down.y;
  down = null;
  const post = (msg) => el.slideshow.contentWindow?.postMessage(msg, "*");
  if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.5) post({ type: "slideshow-nav", dir: dx < 0 ? 1 : -1 });
  else if (dy > 120 && Math.abs(dy) > Math.abs(dx) * 1.5 && isFull()) setFull(false);
  else if (Math.abs(dx) < 12 && Math.abs(dy) < 12) post({ type: "slideshow-tap" });
});
window.addEventListener("message", (e) => {
  if (e.source !== el.slideshow.contentWindow) return;
  const d = e.data;
  if (!d || d.type !== "slideshow-fullscreen") return;
  setFull(d.action === "on" ? true : d.action === "off" ? false : !isFull());
});

// ---- cameras -------------------------------------------------------------------

function snapshotUrl(name, h) { return `/api/camera/${name}/snapshot.jpg?h=${h}&t=${Date.now()}`; }

function buildCameraTiles() {
  el.camRow.innerHTML = "";
  for (const cam of boot.cameras) {
    const tile = document.createElement("div");
    tile.className = "cam-tile";
    tile.innerHTML = `<img alt=""><div class="cam-name"></div>`;
    tile.children[1].textContent = cam.label;
    tile.addEventListener("click", () => openCamera(cam.name, boot.manual_camera_seconds));
    el.camRow.appendChild(tile);
    const img = tile.children[0];
    // Reload only after the previous frame landed, so a slow link never stacks requests.
    const next = () => setTimeout(() => { img.src = snapshotUrl(cam.name, 360); }, cam_overlay_open() ? 5000 : 2000);
    img.addEventListener("load", next);
    img.addEventListener("error", () => setTimeout(next, 5000));
    img.src = snapshotUrl(cam.name, 360);
  }
}
function cam_overlay_open() { return !el.camOverlay.hidden; }

const live = { pc: null, closeTimer: null, posterTimer: null, watchdog: null, camera: null, token: 0 };

function iceGathered(pc, ms) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener("icegatheringstatechange", check); resolve(); };
    const check = () => { if (pc.iceGatheringState === "complete") done(); };
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(done, ms);
  });
}

async function startStream(cam, token) {
  stopStream();
  const pc = new RTCPeerConnection({ iceServers: [] });
  live.pc = pc;
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (e) => { el.camVideo.srcObject = e.streams[0] || new MediaStream([e.track]); };
  pc.onconnectionstatechange = () => {
    if (live.pc === pc && ["failed", "disconnected"].includes(pc.connectionState)) retryStream(cam, token);
  };
  await pc.setLocalDescription(await pc.createOffer());
  await iceGathered(pc, 1500);
  const r = await fetch(`/api/webrtc/${cam.stream}`, {
    method: "POST", headers: { "Content-Type": "application/sdp" }, body: pc.localDescription.sdp,
  });
  if (!r.ok) throw new Error(`webrtc ${r.status}`);
  const sdp = await r.text();
  if (live.pc !== pc) return;
  await pc.setRemoteDescription({ type: "answer", sdp });
}

function stopStream() {
  if (live.pc) { try { live.pc.close(); } catch (_) { /* already closed */ } }
  live.pc = null;
  el.camVideo.srcObject = null;
  el.camVideo.classList.remove("live");
  el.camOverlay.classList.remove("is-live");
}

function retryStream(cam, token) {
  if (token !== live.token || !cam_overlay_open()) return;
  el.camNote.textContent = "Reconnecting…";
  stopStream();
  setTimeout(() => {
    if (token !== live.token || !cam_overlay_open()) return;
    startStream(cam, token).catch(() => retryStream(cam, token));
    armWatchdog(cam, token);
  }, 3000);
}

function armWatchdog(cam, token) {
  clearTimeout(live.watchdog);
  // A slow first frame is normal: the transcode waits for the camera's next
  // keyframe (driveway was 10 s apart on 2026-09-19). Tearing down restarts that
  // whole chain, so only give up after 30 s; real connection failures are caught
  // by onconnectionstatechange. The refreshing poster covers the wait.
  live.watchdog = setTimeout(() => {
    if (token === live.token && !el.camVideo.classList.contains("live")) retryStream(cam, token);
  }, 30000);
}

el.camVideo.addEventListener("playing", () => {
  el.camVideo.classList.add("live");
  el.camOverlay.classList.add("is-live");
  el.camNote.textContent = "";
});

function openCamera(name, seconds) {
  const cam = boot.cameras.find((c) => c.name === name);
  if (!cam) return;
  clearTimeout(live.closeTimer);
  live.closeTimer = setTimeout(closeCamera, Math.max(10, seconds) * 1000);
  if (cam_overlay_open() && live.camera === name) return; // already up: just extend
  closeHistory();
  const token = ++live.token;
  live.camera = name;
  el.camTitle.textContent = cam.label;
  el.camNote.textContent = "";
  el.camOverlay.hidden = false;
  // Poster: Frigate's latest frame, instantly, then chained reloads (next request
  // only after the last one landed, ~4/s = the 5 fps detect stream) so the wait
  // for live video reads as low-res motion rather than a slideshow. The wait is
  // real: the on-demand transcode cannot start before the camera's next keyframe.
  startPoster(name, token);
  startStream(cam, token).catch(() => retryStream(cam, token));
  armWatchdog(cam, token);
}

function startPoster(name, token) {
  const img = el.camPoster;
  const next = (delay) => {
    clearTimeout(live.posterTimer);
    live.posterTimer = setTimeout(() => {
      if (token !== live.token || el.camVideo.classList.contains("live")) return;
      img.src = snapshotUrl(name, 720);
    }, delay);
  };
  img.onload = () => next(200);
  img.onerror = () => next(1500);
  img.src = snapshotUrl(name, 720);
}

function closeCamera() {
  live.token++;
  live.camera = null;
  clearTimeout(live.closeTimer);
  clearTimeout(live.watchdog);
  clearTimeout(live.posterTimer);
  el.camPoster.onload = el.camPoster.onerror = null;
  stopStream();
  el.camOverlay.hidden = true;
  el.camPoster.removeAttribute("src");
}
el.camOverlay.addEventListener("click", closeCamera);

// ---- tap a number = its last 24 hours ---------------------------------------------
// spec = a METRICS row, or the same shape built for the header temperatures.
// The low/high come from the raw samples on the backend, so "did it freeze last
// night" is answered even when the line is downsampled.

const hist = { token: 0, closeTimer: null };
const HIST_SECONDS = 90;

function fmtVal(v, spec) {
  const dec = spec.decimals || 0;
  return v.toLocaleString([], { minimumFractionDigits: dec, maximumFractionDigits: dec });
}
function fmtTime(ms) {
  const d = new Date(ms);
  const h = d.getHours();
  return `${h % 12 || 12}:${String(d.getMinutes()).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`;
}
function niceStep(span) {
  const raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => st >= raw);
}

function chartSvg(data, spec) {
  const W = 1640, H = 560, L = 110, R = 40, T = 46, B = 56;
  const vals = data.points.map((p) => p[1]).concat(data.min.v, data.max.v);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (spec.freeze && lo < 40) lo = Math.min(lo, 30); // keep the 32° line in view on a cold night
  const step = niceStep(Math.max(hi - lo, spec.minSpan || 1) * 1.15);
  lo = Math.floor(lo / step) * step;
  hi = Math.max(Math.ceil(hi / step) * step, lo + step);
  if (spec.unit !== "°" && lo < 0 && Math.min(...vals) >= 0) lo = 0;
  const x = (t) => L + ((t - data.start) / (data.end - data.start)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const tickDec = step < 1 ? (step < 0.1 ? 2 : 1) : 0;
  let g = "";
  for (let v = lo; v <= hi + step / 1000; v += step) {
    g += `<line class="h-grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>` +
      `<text class="h-axis" x="${L - 16}" y="${y(v) + 9}" text-anchor="end">${v.toFixed(tickDec)}</text>`;
  }
  const first = new Date(data.start);
  first.setMinutes(0, 0, 0);
  for (let t = first.getTime() + 3600000; t < data.end; t += 3600000) {
    const hr = new Date(t).getHours();
    if (hr % 3) continue;
    const label = hr === 0 ? "12am" : hr === 12 ? "noon" : `${hr % 12}${hr < 12 ? "am" : "pm"}`;
    g += `<line class="${hr === 0 ? "h-midnight" : "h-grid"}" x1="${x(t)}" x2="${x(t)}" y1="${T}" y2="${H - B}"/>` +
      `<text class="h-axis" x="${x(t)}" y="${H - 14}" text-anchor="middle">${label}</text>`;
  }
  const marks = [];
  if (spec.freeze && lo <= 32 && hi >= 32) marks.push([32, "h-freeze", "32° freezing"]);
  if (spec.warn != null && spec.warn >= lo && spec.warn <= hi) marks.push([spec.warn, "h-warn", ""]);
  if (spec.alert != null && spec.alert >= lo && spec.alert <= hi) marks.push([spec.alert, "h-alert", ""]);
  for (const [v, cls, text] of marks) {
    g += `<line class="h-mark ${cls}" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`;
    if (text) g += `<text class="h-marktext ${cls}" x="${W - R - 8}" y="${y(v) - 10}" text-anchor="end">${text}</text>`;
  }
  const line = data.points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join("");
  const lastX = x(data.points[data.points.length - 1][0]).toFixed(1);
  g += `<path class="h-area" d="${line}L${lastX} ${H - B}L${x(data.points[0][0]).toFixed(1)} ${H - B}Z"/>` +
    `<path class="h-line" d="${line}"/>`;
  for (const [pt, cls] of [[data.min, "h-low"], [data.max, "h-high"]]) {
    const px = Math.min(Math.max(x(pt.t), L + 8), W - R - 8), py = y(pt.v);
    const anchor = px > W - 220 ? "end" : px < L + 120 ? "start" : "middle";
    g += `<circle class="h-dot ${cls}" cx="${px}" cy="${py}" r="9"/>` +
      `<text class="h-dottext ${cls}" x="${px}" y="${cls === "h-low" && py < H - B - 50 ? py + 38 : py - 18}" text-anchor="${anchor}">${fmtVal(pt.v, spec)}</text>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg>`;
}

function statBox(cls, key, value, sub) {
  return `<div class="hist-stat ${cls}"><div class="hist-k">${key}</div><div class="hist-v">${value}</div>` +
    `<div class="hist-t">${sub || "&nbsp;"}</div></div>`;
}

async function openHistory(spec) {
  const token = ++hist.token;
  clearTimeout(hist.closeTimer);
  hist.closeTimer = setTimeout(closeHistory, HIST_SECONDS * 1000);
  const unit = spec.unit === "°" ? "°" : spec.unit ? `<small>${spec.unit}</small>` : "";
  el.histTitle.textContent = spec.title || spec.label;
  const now = parseFloat(states[spec.entity]?.state);
  el.histNow.innerHTML = Number.isFinite(now) ? `${fmtVal(now, spec)}${unit}` : "";
  el.histChart.innerHTML = `<div class="hist-msg">Loading…</div>`;
  el.histStats.innerHTML = "";
  el.histOverlay.hidden = false;
  let data;
  try {
    const r = await fetch(`/api/history/${spec.entity}?hours=24`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    data = await r.json();
  } catch (_) { data = null; }
  if (token !== hist.token) return;
  if (!data || !data.min || data.points.length < 2) {
    el.histChart.innerHTML = `<div class="hist-msg">No history available</div>`;
    return;
  }
  el.histChart.innerHTML = chartSvg(data, spec);
  const froze = spec.freeze && data.min.v <= 32;
  let stats = statBox(froze ? "low froze" : "low", froze ? "Low · froze" : "Low", `${fmtVal(data.min.v, spec)}${unit}`, fmtTime(data.min.t)) +
    statBox("high", "High", `${fmtVal(data.max.v, spec)}${unit}`, fmtTime(data.max.t));
  if (spec.energy_today) {
    const today = parseFloat(states[spec.energy_today]?.state);
    stats += statBox("", "Last 24 hours", `${data.integral_hours.toFixed(1)}<small>kWh</small>`, "");
    if (Number.isFinite(today)) stats += statBox("", "Today", `${today.toFixed(1)}<small>kWh</small>`, "since midnight");
  }
  el.histStats.innerHTML = stats;
}

function closeHistory() {
  hist.token++;
  clearTimeout(hist.closeTimer);
  el.histOverlay.hidden = true;
  el.histChart.innerHTML = "";
}
el.histOverlay.addEventListener("click", closeHistory);

// ---- live channel --------------------------------------------------------------

function setHaStatus(connected) { el.haBanner.hidden = !!connected; }

function handle(msg) {
  switch (msg.type) {
    case "states": Object.assign(states, msg.states); renderAll(); break;
    case "state_changed": states[msg.entity_id] = msg.state; renderAll(); break;
    case "forecast": forecast = msg.forecast || []; renderForecast(); break;
    case "stock": stock = msg.quote || stock; renderStock(); break;
    case "ha_status": setHaStatus(msg.connected); break;
    case "camera_popup": openCamera(msg.camera, msg.seconds || 60); break;
    case "camera_close": closeCamera(); break;
  }
}

let loadedBuild = null;
async function loadBootstrap() {
  const r = await fetch("/api/bootstrap", { cache: "no-store" });
  const data = await r.json();
  // A rebuilt backend serves new assets: pick them up without anyone touching the Pi.
  if (loadedBuild !== null && data.build !== loadedBuild) { location.reload(); return null; }
  loadedBuild = data.build;
  return data;
}

function connectLive() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/api/live`);
  ws.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  ws.onclose = () => {
    setTimeout(async () => {
      try {
        const data = await loadBootstrap();
        if (data) {
          forecast = data.forecast || forecast;
          stock = data.stock?.quote || stock;
          setHaStatus(data.ha.connected); renderForecast(); renderStock();
        }
      } catch (_) { /* backend still down; keep trying */ }
      connectLive();
    }, 3000);
  };
}

async function init() {
  tickClock();
  setInterval(tickClock, 1000);
  for (;;) {
    try { boot = await loadBootstrap(); break; } catch (_) { await new Promise((r) => setTimeout(r, 3000)); }
  }
  Object.assign(states, boot.states);
  forecast = boot.forecast || [];
  stock = boot.stock?.quote || null;
  setHaStatus(boot.ha.connected);
  const base = boot.slideshow_url || `${location.protocol}//${location.hostname}:${boot.slideshow_port}`;
  // widgetW/H: keep the viewer chrome-free (no second clock) until it is fullscreen.
  el.slideshow.src = `${base}/?widgetW=1700&widgetH=950`;
  let wasFull = false;
  try { wasFull = localStorage.getItem(FULL_KEY) === "1"; } catch (_) { /* ignore */ }
  if (wasFull) setFull(true);
  renderAll();
  renderForecast();
  renderStock();
  buildCameraTiles();
  const e = boot.entities;
  $("out-block").addEventListener("click", () => openHistory(
    { entity: e.outdoor_temp, title: "Outside temperature", unit: "°", decimals: 0, freeze: true, minSpan: 8 }));
  $("in-block").addEventListener("click", () => openHistory(
    { entity: e.indoor_temp, title: "Inside temperature", unit: "°", decimals: 0, minSpan: 4 }));
  setInterval(renderForecast, 10 * 60000); // roll the hourly strip forward
  connectLive();
}
init();
