/* Berlin Courses – static frontend. Data comes from data/*.json (built by the crawler).
   Nothing personal is stored: the address and filters live only in memory for this visit.
   Starred courses are remembered in this browser (localStorage) and can be exported as PDF/ICS. */
(() => {
"use strict";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MODES = [
  { id: "walk", label: "🚶 Walk" },
  { id: "bike", label: "🚲 Bike" },
  { id: "scooter", label: "🛴 Scooter" },
  { id: "transit", label: "🚇 BVG" },
];
const LANGS = { de: "German", en: "English" };
const REL = [["3", "Core"], ["2", "Relevant+"], ["1", "Any related"]];
const FORMATS = [["weekly", "Weekly"], ["block", "Block / intensive"], ["dates", "Single dates"]];
const SYSTEM = { TU: "MOSES", UdK: "HISinOne", FU: "FU Vorlesungsverzeichnis", HU: "AGNES", HTW: "HTW LSF", ASH: "ASH LSF", KHB: "Incom", FHP: "Incom" };
const SG = "SG"; // pseudo university chip: UdK Studium Generale
const TOD_MIN = 420, TOD_MAX = 1320, KM_MAX = 25, MIN_MAX = 90;
const PAGE = 60;

const store = {
  get(k, d) { try { const v = localStorage.getItem("bc." + k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("bc." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const DEFAULT_F = () => ({
  q: "", sems: [], unis: [], formats: [], days: [], langs: [], rel: null, tags: [],
  todA: TOD_MIN, todB: TOD_MAX, km: KM_MAX, maxMin: MIN_MAX, mode: "bike", past: true, arr: false, sort: "rel",
});
const S = {
  courses: [], byId: new Map(), locs: {}, meta: {}, details: null,
  home: null,                               // {lat, lon, label} – memory only
  travel: {},                               // mode -> {bKey: minutes} from home
  transitApprox: false,
  favs: new Set(store.get("favs", [])),
  f: DEFAULT_F(),
  view: "list", shown: PAGE, filtered: [],
};

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const toMin = (hhmm) => { if (!hhmm) return null; const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const fmtDate = (iso) => { const d = new Date(iso + "T12:00"); return `${d.getDate()}.${d.getMonth() + 1}.`; };
const todayIso = new Date().toISOString().slice(0, 10);
const uniClass = (u) => "u-" + String(u).replace(/[^A-Za-z]/g, "");

// ---------- course helpers ----------
function format(c) {
  const ss = c.ss || [];
  if (!ss.length) return "none";
  if (ss.some((s) => s.r === "weekly" || s.r === "biweekly")) return "weekly";
  if (ss.some((s) => s.r === "block")) return "block";
  const dates = ss.filter((s) => s.f).map((s) => s.f).sort();
  if (dates.length >= 2 && (new Date(dates.at(-1)) - new Date(dates[0])) / 864e5 <= 21) return "block";
  return "dates";
}

/** Concrete occurrences for date-based sessions (blocks, single dates). */
function occurrences(s) {
  if (s.r === "weekly" || s.r === "biweekly") return null;
  if (!s.f) return [];
  const out = [];
  if (s.r === "block" || (s.r === "irregular" && s.d == null)) {
    const end = new Date((s.l || s.f) + "T12:00");
    for (let d = new Date(s.f + "T12:00"), i = 0; d <= end && i < 62; d.setDate(d.getDate() + 1), i++) {
      out.push(d.toISOString().slice(0, 10));
    }
    return out;
  }
  out.push(s.f);
  if (s.l && s.l !== s.f) out.push(s.l);
  return out;
}

const lastDate = (c) => (c.ss || []).reduce((m, s) => (s.l || s.f || "") > m ? (s.l || s.f) : m, "");

function groups(c) {
  const g = new Map();
  for (const s of c.ss || []) {
    const k = s.g || "_";
    if (!g.has(k)) g.set(k, []);
    g.get(k).push(s);
  }
  return [...g.values()];
}

function slotLabel(s) {
  const t = s.s ? `${s.s}–${s.e}` : "";
  if (s.r === "weekly" || s.r === "biweekly") {
    return `${s.d != null ? DAYS[s.d] : "?"} ${t}${s.r === "biweekly" ? " · biweekly" : ""}`;
  }
  if (s.r === "block") return `Block ${fmtDate(s.f)}${s.l && s.l !== s.f ? "–" + fmtDate(s.l) : ""} ${t}`;
  return `${s.d != null ? DAYS[s.d] + " " : ""}${s.f ? fmtDate(s.f) : ""}${s.l && s.l !== s.f ? "–" + fmtDate(s.l) : ""} ${t}`;
}

function systemName(c) {
  if (/studium-generale/.test(c.url)) return "Studium Generale";
  return SYSTEM[c.u] || "catalog";
}

// ---------- distance + travel ----------
function haversineKm(a, b) {
  const R = 6371, r = Math.PI / 180;
  const dLat = (b.lat - a.lat) * r, dLon = (b.lon - a.lon) * r;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
const ESTIMATE = {   // fallback minutes from straight-line km
  walk: (km) => km * 1.3 / 4.8 * 60,
  bike: (km) => km * 1.25 / 15 * 60 + 2,
  scooter: (km) => km * 1.25 / 17 * 60 + 4,
  transit: (km) => 9 + km * 1.35 / 20 * 60,
};

function minutesTo(bKey, mode = S.f.mode) {
  const loc = S.locs[bKey];
  if (!S.home || !loc) return null;
  const hit = S.travel[mode]?.[bKey];
  if (hit != null) return { min: hit, approx: false };
  return { min: Math.round(ESTIMATE[mode](haversineKm(S.home, loc))), approx: true };
}
const kmTo = (bKey) => (S.home && S.locs[bKey] ? haversineKm(S.home, S.locs[bKey]) : null);

/** Closest building of a course: {b, km, min, approx} or null. */
function courseTravel(c) {
  if (!S.home) return null;
  let best = null;
  for (const b of new Set((c.ss || []).map((s) => s.b).filter(Boolean))) {
    if (!S.locs[b]) continue;
    const t = minutesTo(b), km = kmTo(b);
    if (!best || t.min < best.min) best = { b, km, ...t };
  }
  return best;
}

async function fetchJson(url, ms = 12000) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
  try { const r = await fetch(url, { signal: ctl.signal }); if (!r.ok) throw new Error(r.status); return await r.json(); }
  finally { clearTimeout(t); }
}

let travelRun = 0;
async function computeTravel() {
  const run = ++travelRun, home = S.home;
  if (!home) return;
  const keys = Object.keys(S.locs);
  const status = (t) => { if (run === travelRun) $("#travelStatus").textContent = t; };
  // walk + bike (+ scooter derived from bike distance) via OSRM tables
  for (const [mode, profile] of [["walk", "routed-foot"], ["bike", "routed-bike"]]) {
    if (S.travel[mode]) continue;
    status(`Routing ${mode} times…`);
    const res = {}, scoot = {};
    for (let i = 0; i < keys.length; i += 80) {
      const chunk = keys.slice(i, i + 80);
      const coords = [`${home.lon},${home.lat}`, ...chunk.map((k) => `${S.locs[k].lon},${S.locs[k].lat}`)].join(";");
      try {
        let j;
        for (let attempt = 0; ; attempt++) { // public OSRM server rate-limits bursts (429): back off and retry
          try { j = await fetchJson(`https://routing.openstreetmap.de/${profile}/table/v1/driving/${coords}?sources=0&annotations=duration,distance`, 20000); break; }
          catch (e) { if (attempt >= 3) throw e; await new Promise((r) => setTimeout(r, 1500 * (attempt + 1))); }
        }
        await new Promise((r) => setTimeout(r, 400));
        chunk.forEach((k, idx) => {
          const dist = j.distances?.[0]?.[idx + 1], dur = j.durations?.[0]?.[idx + 1];
          if (dist == null) return;
          // OSRM durations are optimistic; use distance with realistic Berlin speeds
          if (mode === "walk") res[k] = Math.round(dist / 1000 / 4.8 * 60);
          else { res[k] = Math.round(Math.max(dur / 60, dist / 1000 / 15 * 60) + 2); scoot[k] = Math.round(dist / 1000 / 17 * 60 + 4); }
        });
      } catch { status("Routing service unavailable – using estimates"); }
    }
    if (run !== travelRun) return;
    S.travel[mode] = res;
    if (mode === "bike") S.travel.scooter = scoot;
    render();
  }
  if (S.f.mode === "transit") await computeTransit(run, keys);
  status(S.f.mode === "transit" && S.transitApprox ? "BVG API unreachable right now – transit times are estimates (≈)." : "");
}

/** Transit via the community BVG REST API (only when BVG is selected; sometimes down → estimates). */
async function computeTransit(run, keys) {
  const home = S.home, res = S.travel.transit || (S.travel.transit = {});
  const todo = keys.filter((k) => !(k in res));
  if (!todo.length || S.transitApprox) return;
  const dep = nextWeekday(1, 9); // a Tuesday 09:00 as representative departure
  let fails = 0, n = 0;
  for (const k of todo) {
    if (run !== travelRun) return;
    $("#travelStatus").textContent = `Asking BVG for transit times… ${++n}/${todo.length}`;
    const L0 = S.locs[k];
    try {
      const j = await fetchJson(`https://v6.bvg.transport.rest/journeys?from.latitude=${home.lat}&from.longitude=${home.lon}&from.address=start` +
        `&to.latitude=${L0.lat}&to.longitude=${L0.lon}&to.address=${encodeURIComponent(L0.name)}&departure=${dep}&results=3&stopovers=false&remarks=false`, 9000);
      const mins = (j.journeys || []).map((jr) => {
        const a = new Date(jr.legs[0].departure || jr.legs[0].plannedDeparture);
        const b = new Date(jr.legs.at(-1).arrival || jr.legs.at(-1).plannedArrival);
        return (b - a) / 60000;
      }).filter((m) => m > 0);
      if (mins.length) res[k] = Math.round(Math.min(...mins));
      if (n % 10 === 0) render();
      await new Promise((r) => setTimeout(r, 650)); // API limit: 100 req/min
    } catch {
      if (++fails >= 2) { S.transitApprox = true; break; }
    }
  }
  render();
}
function nextWeekday(dow, hour) { // dow: 0=Mon
  const d = new Date(); d.setHours(hour, 0, 0, 0);
  const cur = (d.getDay() + 6) % 7; let add = (dow - cur + 7) % 7; if (add === 0) add = 7;
  d.setDate(d.getDate() + add); return d.toISOString();
}

async function geocode(q) {
  const out = $("#addrFound");
  if (!q) { S.home = null; S.travel = {}; out.textContent = "Not stored anywhere. Looked up via OpenStreetMap."; syncDist(); render(); return; }
  out.textContent = "Searching…";
  try {
    const j = await fetchJson(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=de&viewbox=12.9,52.7,13.8,52.3&q=${encodeURIComponent(q + (/berlin|potsdam/i.test(q) ? "" : ", Berlin"))}`);
    if (!j.length) { out.textContent = "Address not found – try street and number."; return; }
    S.home = { lat: +j[0].lat, lon: +j[0].lon, label: q };
    S.travel = {}; S.transitApprox = false;
    out.textContent = "✓ " + j[0].display_name.split(",").slice(0, 3).join(",");
    syncDist(); render(); computeTravel();
  } catch { out.textContent = "Lookup failed – try again."; }
}

// ---------- filtering ----------
const distActive = () => S.home && (S.f.km < KM_MAX || S.f.maxMin < MIN_MAX);
const todActive = () => S.f.todA > TOD_MIN || S.f.todB < TOD_MAX;

function inTimeOfDay(s) {
  const a = toMin(s.s), b = toMin(s.e);
  if (a == null || b == null) return true;
  return a >= S.f.todA && b <= S.f.todB;
}

function matches(c) {
  const f = S.f;
  if (f.sems.length && !f.sems.includes(c.sem)) return false;
  if (f.unis.length && !f.unis.includes(c.u) && !(c.sg && f.unis.includes(SG))) return false;
  const fm = c._fmt;
  if (fm === "none" && !f.arr && !(c.sg && f.unis.includes(SG))) return false;
  if (f.formats.length && fm !== "none" && !f.formats.includes(fm)) return false;
  if (f.past && fm !== "weekly" && fm !== "none" && c._last && c._last < todayIso) return false;
  if (f.days.length) {
    const ds = new Set();
    for (const s of c.ss || []) {
      if (s.d != null) ds.add(s.d);
      for (const o of occurrences(s) || []) ds.add((new Date(o + "T12:00").getDay() + 6) % 7);
    }
    if (ds.size && ![...ds].some((d) => f.days.includes(d))) return false;
  }
  if (f.langs.length && !f.langs.some((x) => c._lang.includes(x))) return false;
  if (f.rel && (c.rel ?? -1) < Number(f.rel)) return false;
  if (f.tags.length && !(c.tags || []).some((t) => f.tags.includes(t))) return false;
  if (f.q) {
    const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.every((w) => c._text.includes(w))) return false;
  }
  if (distActive()) {
    const t = courseTravel(c);
    if (!t) return false; // unknown location can't be judged
    if (f.km < KM_MAX && t.km > f.km) return false;
    if (f.maxMin < MIN_MAX && t.min > f.maxMin) return false;
  }
  if (todActive() && fm !== "none" && !groups(c).some((g) => g.every(inTimeOfDay))) return false;
  return true;
}

function firstSlotKey(c) {
  const s = (c.ss || []).find((x) => x.s);
  if (!s) return 1e9;
  const d = s.d ?? (s.f ? (new Date(s.f + "T12:00").getDay() + 6) % 7 : 9);
  return d * 1440 + toMin(s.s);
}

function applyFilters() {
  const list = S.courses.filter(matches);
  const sort = S.f.sort;
  const cmpTitle = (a, b) => a.t.localeCompare(b.t);
  if (sort === "title") list.sort(cmpTitle);
  else if (sort === "time") list.sort((a, b) => firstSlotKey(a) - firstSlotKey(b) || cmpTitle(a, b));
  else if (sort === "travel") list.sort((a, b) => (courseTravel(a)?.min ?? 999) - (courseTravel(b)?.min ?? 999) || cmpTitle(a, b));
  else list.sort((a, b) => (b.rel ?? -1) - (a.rel ?? -1) || (S.favs.has(b.id) - S.favs.has(a.id)) || cmpTitle(a, b));
  S.filtered = list;
}

// ---------- rendering ----------
function travelBadge(c) {
  if (!S.home) return "";
  const t = courseTravel(c);
  if (!t) return `<span class="travel">no location</span>`;
  const icon = MODES.find((m) => m.id === S.f.mode).label.split(" ")[0];
  const lim = S.f.maxMin < MIN_MAX ? S.f.maxMin : 30;
  const cls = t.min <= lim * 0.6 ? "good" : t.min > lim ? "far" : "";
  return `<span class="travel ${cls}">${icon} ${t.approx && S.f.mode === "transit" ? "≈" : ""}${t.min} min · ${t.km.toFixed(1)} km</span>`;
}

function whereOf(c) {
  const b = (c.ss || []).find((s) => s.b && S.locs[s.b])?.b;
  return b ? S.locs[b].name : ((c.ss || []).find((s) => s.room)?.room || "");
}

function card(c) {
  const slots = (c.ss || []).slice(0, 6).map((s) =>
    `<span class="slot ${s.r === "block" ? "block" : ""}">${esc(slotLabel(s))}</span>`).join("");
  const more = (c.ss || []).length > 6 ? `<span class="slot">+${c.ss.length - 6}</span>` : "";
  const where = whereOf(c);
  return `<div class="card" data-id="${esc(c.id)}">
    <div>
      <div class="meta"><span class="uni ${uniClass(c.u)}">${esc(c.u)}</span>${c.ty ? `<span>${esc(c.ty)}</span>` : ""}${c.lec ? `<span>${esc(c.lec.slice(0, 3).join(", "))}</span>` : ""}${c.rel === 3 ? `<span class="rel3">★ D+C core</span>` : ""}${c.sg ? `<span class="rel3">Studium Generale</span>` : ""}</div>
      <h4>${esc(c.t)}</h4>
    </div>
    <button class="star ${S.favs.has(c.id) ? "on" : ""}" data-fav="${esc(c.id)}" aria-label="Star" title="Add to my list">★</button>
    <div class="when">${slots}${more}${c.arr ? `<span class="slot">by arrangement</span>` : ""}</div>
    ${c.sum ? `<p class="sum">${esc(c.sum)}</p>` : ""}
    <div class="foot">${travelBadge(c)}${where ? `<span class="tag">📍 ${esc(where)}</span>` : ""}${(c.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}${c.lms ? `<span class="tag">${c.lms.source === "tu_isis" ? "ISIS" : "Moodle"}</span>` : ""}</div>
  </div>`;
}

function renderList() {
  const list = S.filtered.slice(0, S.shown);
  $("#list").innerHTML = list.length ? list.map(card).join("") : `<div class="empty">No courses match. Try loosening the filters.</div>`;
  $("#more").hidden = S.filtered.length <= S.shown;
}

let map, layer;
function renderMap() {
  if (!map) {
    map = L.map("map", { zoomControl: true }).setView([52.512, 13.36], 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap contributors", maxZoom: 19 }).addTo(map);
    layer = L.layerGroup().addTo(map);
  }
  setTimeout(() => map.invalidateSize(), 50);
  layer.clearLayers();
  const byB = new Map();
  for (const c of S.filtered) for (const b of new Set((c.ss || []).map((s) => s.b).filter((b) => b && S.locs[b]))) {
    if (!byB.has(b)) byB.set(b, []);
    byB.get(b).push(c);
  }
  for (const [b, cs] of byB) {
    const L0 = S.locs[b];
    const n = cs.length, size = Math.min(44, 20 + Math.sqrt(n) * 3);
    const icon = L.divIcon({ className: "", html: `<div class="pin ${uniClass(cs[0].u)}" style="width:${size}px;height:${size}px">${n}</div>`, iconSize: [size, size] });
    const t = S.home ? minutesTo(b) : null;
    const html = `<b>${esc(L0.name)}</b>${L0.approx ? " (approx.)" : ""}<br>${esc(L0.address || "")}<br>${t ? `${t.min} min · ${kmTo(b).toFixed(1)} km<br>` : ""}` +
      cs.slice(0, 8).map((c) => `<a href="#" data-open="${esc(c.id)}">${esc(c.t)}</a>`).join("<br>") + (n > 8 ? `<br>…and ${n - 8} more` : "");
    L.marker([L0.lat, L0.lon], { icon }).bindPopup(html).addTo(layer);
  }
  if (S.home) {
    const icon = L.divIcon({ className: "", html: `<div class="pin me" style="width:30px;height:30px">⌂</div>`, iconSize: [30, 30] });
    L.marker([S.home.lat, S.home.lon], { icon }).bindPopup("You").addTo(layer);
    if (S.f.km < KM_MAX) L.circle([S.home.lat, S.home.lon], { radius: S.f.km * 1000, color: "#e4572e", weight: 1.5, fillOpacity: 0.06 }).addTo(layer);
  }
}

function favCourses() {
  return S.courses.filter((c) => S.favs.has(c.id)).sort((a, b) => firstSlotKey(a) - firstSlotKey(b) || a.t.localeCompare(b.t));
}

function renderMine() {
  const favs = favCourses();
  if (!favs.length) {
    $("#mine").innerHTML = `<div class="empty">Star courses (★) to collect them here, then download your list as PDF.</div>`;
    return;
  }
  const showDays = favs.some((c) => (c.ss || []).some((s) => s.d >= 5)) ? 7 : 5;
  const H0 = 8, H1 = 21, PX = 44 / 60;
  let cols = "";
  for (let d = 0; d < showDays; d++) {
    const evs = [];
    for (const c of favs) for (const s of c.ss || []) {
      if (s.d !== d || !(s.r === "weekly" || s.r === "biweekly") || !s.s) continue;
      evs.push({ c, s, a: toMin(s.s), b: toMin(s.e) });
    }
    const html = evs.map((e) => {
      const clash = evs.some((o) => o !== e && o.c !== e.c && o.a < e.b && e.a < o.b);
      return `<div class="ev ${uniClass(e.c.u)} ${clash ? "clash" : ""}" data-id="${esc(e.c.id)}" style="top:${(e.a - H0 * 60) * PX}px;height:${Math.max(18, (e.b - e.a) * PX)}px" title="${esc(e.c.t)}">${e.s.s} ${esc(e.c.t)}</div>`;
    }).join("");
    cols += `<div class="col" style="grid-row:2;grid-column:${d + 2};height:${(H1 - H0) * 60 * PX}px">${html}</div>`;
  }
  let hours = "";
  for (let h = H0; h < H1; h++) hours += `<div class="hour">${h}:00</div>`;
  const dated = [];
  for (const c of favs) for (const s of c.ss || []) {
    if (s.r === "weekly" || s.r === "biweekly") continue;
    for (const o of occurrences(s) || []) dated.push({ o, c, s });
  }
  dated.sort((a, b) => a.o.localeCompare(b.o) || (a.s.s || "").localeCompare(b.s.s || ""));
  $("#mine").innerHTML = `
    <div class="week-actions">
      <span class="hint">${favs.length} course${favs.length > 1 ? "s" : ""} starred</span>
      <button class="btn primary" id="pdf">Download PDF</button>
      <button class="btn" id="ics">Add to calendar (.ics)</button>
    </div>
    <div class="week"><div class="week-grid" style="--days:${showDays}">
      <div class="hd"></div>${DAYS.slice(0, showDays).map((d) => `<div class="hd">${d}</div>`).join("")}
      <div style="grid-row:2;grid-column:1">${hours}</div>${cols}
    </div></div>
    ${dated.length ? `<div class="dated"><h3>Blocks &amp; single dates</h3><ul>${dated.map((x) =>
      `<li data-id="${esc(x.c.id)}"><span class="d">${new Date(x.o + "T12:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })} ${x.s.s ? x.s.s + "–" + x.s.e : ""}</span><span>${esc(x.c.t)}</span></li>`).join("")}</ul></div>` : ""}
    <h3 class="mine-h">Courses</h3>
    <div class="mine-list">${favs.map((c) => `
      <div class="mine-row" data-id="${esc(c.id)}">
        <span class="uni ${uniClass(c.u)}">${esc(c.u)}</span>
        <div class="mine-main"><b>${esc(c.t)}</b><small>${esc([(c.ss || []).slice(0, 3).map(slotLabel).join(" · "), whereOf(c)].filter(Boolean).join(" · "))}</small></div>
        <a class="btn" href="${esc(c.url)}" target="_blank" rel="noopener">Sign up in ${esc(systemName(c))} ↗</a>
        <button class="star on" data-fav="${esc(c.id)}" title="Remove from my list">★</button>
      </div>`).join("")}</div>`;
}

function renderMeta() {
  const m = S.meta;
  const next = m.next_update ? ` · next update ${new Date(m.next_update).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}` : "";
  $("#metaLine").textContent = `${m.count?.toLocaleString() ?? "?"} courses · ${[...new Set(S.courses.map((c) => c.u))].join(", ")} · updated ${m.built ? new Date(m.built).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "?"}${next}`;
  $("#favCount").textContent = S.favs.size || "";
  $("#favCountTop").textContent = S.favs.size || "";
}

function render() {
  applyFilters();
  $("#resultCount").textContent = `${S.filtered.length.toLocaleString()} courses`;
  if (S.view === "list") renderList();
  if (S.view === "map") renderMap();
  if (S.view === "mine") renderMine();
  renderMeta();
}
const rerender = () => { S.shown = PAGE; render(); };

// ---------- filter UI ----------
function chipGroup(el, items, key, { single = false, counts = null, cls = null } = {}) {
  el.innerHTML = items.map(([v, label]) =>
    `<span class="chip ${cls ? cls(v) : ""}" data-v="${esc(v)}">${esc(label)}${counts ? `<span class="n">${(counts[v] || 0).toLocaleString()}</span>` : ""}</span>`).join("");
  const sync = () => $$(".chip", el).forEach((ch) => {
    const v = ch.dataset.v; const cur = S.f[key];
    ch.classList.toggle("on", single ? String(cur) === v : cur.map(String).includes(v));
  });
  el.onclick = (e) => {
    const ch = e.target.closest(".chip"); if (!ch) return;
    const v = key === "days" ? Number(ch.dataset.v) : ch.dataset.v;
    if (single) S.f[key] = String(S.f[key]) === String(v) ? null : v;
    else S.f[key] = S.f[key].includes(v) ? S.f[key].filter((x) => x !== v) : [...S.f[key], v];
    sync(); rerender();
  };
  el._sync = sync;
  sync();
}

function syncDist() {
  const f = S.f;
  $("#distControls").classList.toggle("off", !S.home);
  $("#kmVal").textContent = f.km >= KM_MAX ? "any" : `${f.km} km`;
  $("#minVal").textContent = f.maxMin >= MIN_MAX ? "any" : `${f.maxMin} min`;
  $$("#modes button").forEach((x) => x.classList.toggle("on", x.dataset.mode === f.mode));
  if (!S.home) $("#travelStatus").textContent = "Enter your address to filter by distance and travel time.";
}

function syncTod() {
  const a = S.f.todA, b = S.f.todB, span = TOD_MAX - TOD_MIN;
  $("#todA").value = a; $("#todB").value = b;
  $("#todFill").style.left = `${(a - TOD_MIN) / span * 100}%`;
  $("#todFill").style.right = `${(TOD_MAX - b) / span * 100}%`;
  $("#todLabel").textContent = todActive() ? `${hhmm(a)} – ${hhmm(b)}` : "any time";
}

function buildFilters() {
  const cnt = (fn) => S.courses.reduce((m, c) => { for (const v of [].concat(fn(c))) m[v] = (m[v] || 0) + 1; return m; }, {});
  chipGroup($("#semesters"), (S.meta.semesters || []).filter((s) => s.count).map((s) => [s.key, s.label]), "sems", { counts: cnt((c) => c.sem) });
  const uniCounts = cnt((c) => c.u);
  const unis = Object.keys(uniCounts).sort((a, b) => uniCounts[b] - uniCounts[a]).map((u) => [u, u]);
  const sgN = S.courses.filter((c) => c.sg).length;
  if (sgN) { uniCounts[SG] = sgN; unis.splice(unis.findIndex(([u]) => u === "UdK") + 1, 0, [SG, "UdK Studium Generale"]); }
  chipGroup($("#unis"), unis, "unis", { counts: uniCounts, cls: (v) => "dot " + uniClass(v === SG ? "UdK" : v) });
  chipGroup($("#formats"), FORMATS, "formats", { counts: cnt((c) => c._fmt) });
  chipGroup($("#days"), DAYS.map((d, i) => [i, d]), "days");
  chipGroup($("#langs"), Object.entries(LANGS), "langs");
  chipGroup($("#rel"), REL, "rel", { single: true });
  $("#relSection").hidden = !S.courses.some((c) => c.rel != null);
  const tagCounts = cnt((c) => c.tags || []);
  chipGroup($("#tags"), Object.keys(tagCounts).sort((a, b) => tagCounts[b] - tagCounts[a]).map((t) => [t, t]), "tags", { counts: tagCounts });
  $("#tagSection").hidden = !Object.keys(tagCounts).length;

  // distance
  $("#modes").innerHTML = MODES.map((m) => `<button type="button" data-mode="${m.id}">${m.label}</button>`).join("");
  $("#modes").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.f.mode = b.dataset.mode; syncDist(); rerender(); computeTravel();
  };
  $("#addrForm").onsubmit = (e) => { e.preventDefault(); geocode($("#addr").value.trim()); };
  $("#km").oninput = () => { S.f.km = +$("#km").value; syncDist(); rerender(); };
  $("#maxMin").oninput = () => { S.f.maxMin = +$("#maxMin").value; syncDist(); rerender(); };
  syncDist();

  // time of day: two-handle slider
  const onTod = (which) => () => {
    let a = +$("#todA").value, b = +$("#todB").value;
    if (a > b - 30) { if (which === "a") a = b - 30; else b = a + 30; }
    S.f.todA = a; S.f.todB = b; syncTod(); rerender();
  };
  $("#todA").oninput = onTod("a"); $("#todB").oninput = onTod("b");
  syncTod();

  const bind = (id, key, prop = "checked") => {
    const el = $(id); el[prop] = S.f[key];
    el.addEventListener("change", () => { S.f[key] = el[prop]; rerender(); });
  };
  bind("#fPast", "past"); bind("#fArr", "arr"); bind("#sort", "sort", "value");
  const q = $("#q");
  let tm; q.addEventListener("input", () => { clearTimeout(tm); tm = setTimeout(() => { S.f.q = q.value.trim(); rerender(); }, 150); });
  $("#reset").onclick = () => {
    const keep = { mode: S.f.mode, sort: S.f.sort };
    S.f = Object.assign(DEFAULT_F(), keep);
    q.value = ""; $("#km").value = KM_MAX; $("#maxMin").value = MIN_MAX;
    $("#fPast").checked = true; $("#fArr").checked = false;
    $$(".chips").forEach((el) => el._sync?.());
    syncDist(); syncTod(); rerender();
  };
}

// ---------- detail drawer ----------
async function openCourse(id) {
  const c = S.byId.get(id); if (!c) return;
  const body = $("#drawerBody");
  const rows = groups(c).map((g) => {
    const name = g[0].g && g[0].g !== "_" ? `<tr><td colspan="3"><b>${esc(g[0].g)}</b></td></tr>` : "";
    return name + g.map((s) => {
      const L0 = s.b ? S.locs[s.b] : null;
      const t = L0 && S.home ? minutesTo(s.b) : null;
      return `<tr><td>${esc(slotLabel(s))}${s.f && (s.r === "weekly" || s.r === "biweekly") ? `<br><small>${fmtDate(s.f)}–${s.l ? fmtDate(s.l) : ""}</small>` : ""}</td>
        <td>${esc(s.room || "")}${L0 ? `<br><small>${esc(L0.address || L0.name)}</small>` : ""}${s.n ? `<br><small>${esc(s.n)}</small>` : ""}</td>
        <td>${t ? `${t.approx && S.f.mode === "transit" ? "≈" : ""}${t.min} min<br><small>${kmTo(s.b).toFixed(1)} km</small>${routeLinks(L0)}` : ""}</td></tr>`;
    }).join("");
  }).join("");
  body.innerHTML = `
    <div class="row"><button class="icon" data-close>✕</button><span class="uni ${uniClass(c.u)}">${esc(c.u)}</span>${esc(c.ty || "")}${c.nr ? ` · ${esc(c.nr)}` : ""}</div>
    <h2>${esc(c.t)}</h2>
    <div class="row">${c.lec ? esc(c.lec.join(", ")) : ""}</div>
    <div class="row">${esc(c.org || "")}${c.lang ? ` · ${esc(c.lang)}` : ""}</div>
    ${c.sum ? `<p>${esc(c.sum)}</p>` : ""}
    <div class="row">${(c.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join(" ")}${(c.kw || []).map((t) => `<span class="tag">${esc(t)}</span>`).join(" ")}</div>
    <div class="links">
      <a class="btn primary" href="${esc(c.url)}" target="_blank" rel="noopener">Open in ${esc(systemName(c))} ↗</a>
      ${c.lms ? `<a class="btn" href="${esc(c.lms.url)}" target="_blank" rel="noopener">${c.lms.source === "tu_isis" ? "ISIS" : "Moodle"} course ↗</a>` : ""}
      <button class="btn" data-fav="${esc(c.id)}">${S.favs.has(c.id) ? "★ On my list" : "☆ Add to my list"}</button>
    </div>
    <h3>Dates</h3>
    ${rows ? `<table>${rows}</table>` : `<p class="hint">${c.arr ? "By arrangement." : "No dates published."}</p>`}
    ${c.path ? `<h3>Catalog</h3><p class="hint">${esc(c.path.join(" › "))}</p>` : ""}
    <h3>Description</h3><div class="desc" id="desc">${c.hasd ? "Loading…" : c.sum
      ? `${esc(c.sum)}<p class="hint">AI-generated from the title and department – the catalog has no description for this course.</p>`
      : "<span class='hint'>No description published.</span>"}</div>`;
  $("#drawer").hidden = false;
  if (c.hasd) {
    if (!S.details) S.details = await fetchJson("data/details.json", 60000).catch(() => ({}));
    const d = $("#desc"); if (d) d.textContent = S.details[id] || "—";
  }
}
function routeLinks(L0) {
  const p = S.home; if (!p) return "";
  const gm = (m) => `https://www.google.com/maps/dir/?api=1&origin=${p.lat},${p.lon}&destination=${L0.lat},${L0.lon}&travelmode=${m}`;
  return `<br><small><a href="${gm("transit")}" target="_blank" rel="noopener">transit</a> · <a href="${gm("bicycling")}" target="_blank" rel="noopener">bike</a> · <a href="${gm("walking")}" target="_blank" rel="noopener">walk</a></small>`;
}

// ---------- my list: favourites, ICS, PDF ----------
function toggleFav(id) {
  S.favs.has(id) ? S.favs.delete(id) : S.favs.add(id);
  store.set("favs", [...S.favs]);
  $$(`[data-fav="${CSS.escape(id)}"]`).forEach((b) => { b.classList.toggle("on", S.favs.has(id)); if (b.classList.contains("btn")) b.textContent = S.favs.has(id) ? "★ On my list" : "☆ Add to my list"; });
  renderMeta(); if (S.view === "mine") renderMine();
}

function exportIcs() {
  const pad = (n) => String(n).padStart(2, "0");
  const dt = (iso, t) => `${iso.replaceAll("-", "")}T${t.replace(":", "")}00`;
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//berlin-courses//EN", "CALSCALE:GREGORIAN"];
  let n = 0;
  for (const c of favCourses()) for (const s of c.ss || []) {
    if (!s.s || !s.f) continue;
    const loc = s.b && S.locs[s.b] ? `${S.locs[s.b].name}, ${s.room || ""}` : s.room || "";
    const base = [`SUMMARY:${c.t.replace(/[,;]/g, " ")}`, `LOCATION:${loc.replace(/[,;]/g, " ")}`, `URL:${c.url}`];
    const ev = (start, extra = []) => {
      lines.push("BEGIN:VEVENT", `UID:${c.id}-${n++}@berlin-courses`, `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}Z`,
        `DTSTART;TZID=Europe/Berlin:${dt(start, s.s)}`, `DTEND;TZID=Europe/Berlin:${dt(start, s.e)}`, ...base, ...extra, "END:VEVENT");
    };
    if ((s.r === "weekly" || s.r === "biweekly") && s.d != null) {
      const d0 = new Date(s.f + "T12:00"); while ((d0.getDay() + 6) % 7 !== s.d) d0.setDate(d0.getDate() + 1);
      const iso = `${d0.getFullYear()}-${pad(d0.getMonth() + 1)}-${pad(d0.getDate())}`;
      ev(iso, [`RRULE:FREQ=WEEKLY;INTERVAL=${s.r === "biweekly" ? 2 : 1}${s.l ? `;UNTIL=${s.l.replaceAll("-", "")}T235959Z` : ""}`]);
    } else for (const o of occurrences(s) || []) ev(o);
  }
  lines.push("END:VCALENDAR");
  download(new Blob([lines.join("\r\n")], { type: "text/calendar" }), "my-courses.ics");
}

function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

let jsPdfLoading;
function loadJsPdf() {
  if (window.jspdf) return Promise.resolve(window.jspdf);
  return jsPdfLoading ||= new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js";
    s.onload = () => ok(window.jspdf); s.onerror = () => { jsPdfLoading = null; fail(new Error("PDF library failed to load")); };
    document.head.appendChild(s);
  });
}

/** PDF of the starred courses, with clickable links to each course's sign-up page. */
async function exportPdf() {
  const btn = $("#pdf"); if (btn) { btn.disabled = true; btn.textContent = "Preparing…"; }
  try {
    const { jsPDF } = await loadJsPdf();
    // built-in PDF fonts are Latin-1 only
    const t = (s) => String(s ?? "").replace(/[–—]/g, "-").replace(/[‘’]/g, "'").replace(/[“”„]/g, '"').replace(/…/g, "...").replace(/·/g, "|").replace(/[^\x00-\xFF]/g, "");
    const doc = new jsPDF({ unit: "mm", format: "a4" });
    const W = 210, M = 16, CW = W - 2 * M;
    let y = M;
    const ensure = (h) => { if (y + h > 297 - M) { doc.addPage(); y = M; } };
    const text = (s, size, style = "normal", color = [28, 27, 25]) => {
      doc.setFont("helvetica", style); doc.setFontSize(size); doc.setTextColor(...color);
      const lines = doc.splitTextToSize(t(s), CW);
      const lh = size * 0.42;
      ensure(lines.length * lh);
      doc.text(lines, M, y + lh * 0.8); y += lines.length * lh;
    };
    const link = (label, url) => {
      doc.setFont("helvetica", "bold"); doc.setFontSize(10); doc.setTextColor(200, 70, 35);
      ensure(5);
      doc.textWithLink(t(label), M, y + 3.6, { url }); y += 5;
      doc.setFont("helvetica", "normal"); doc.setFontSize(7.5); doc.setTextColor(120, 118, 112);
      const lines = doc.splitTextToSize(url, CW);
      ensure(lines.length * 3.2);
      lines.forEach((ln) => { doc.textWithLink(ln, M, y + 2.6, { url }); y += 3.2; });
    };
    const favs = favCourses();
    const sems = [...new Set(favs.map((c) => (S.meta.semesters || []).find((s) => s.key === c.sem)?.label || c.sem))].join(", ");
    text("My courses", 20, "bold");
    y += 1;
    text(`${sems} | ${favs.length} course${favs.length > 1 ? "s" : ""} | made ${new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })} with Berlin Courses`, 9, "normal", [107, 104, 96]);
    y += 6;
    for (const c of favs) {
      ensure(30);
      doc.setDrawColor(228, 225, 218); doc.line(M, y, W - M, y); y += 4;
      text(`${c.u}${c.sg ? " | Studium Generale" : ""}${c.ty ? " | " + c.ty : ""}${c.nr ? " | " + c.nr : ""}`, 8.5, "bold", [107, 104, 96]);
      y += 0.5;
      text(c.t, 13, "bold");
      if (c.lec?.length) text(c.lec.join(", "), 9.5, "normal", [80, 78, 72]);
      y += 1.5;
      const ss = c.ss || [];
      if (!ss.length) text(c.arr ? "Dates by arrangement" : "No dates published", 9.5);
      for (const s of ss.slice(0, 12)) {
        const L0 = s.b ? S.locs[s.b] : null;
        const range = s.f && (s.r === "weekly" || s.r === "biweekly") ? ` (${fmtDate(s.f)}-${s.l ? fmtDate(s.l) : ""})` : "";
        const where = [s.room, L0?.address || L0?.name].filter(Boolean).join(", ");
        text(`${slotLabel(s)}${range}${where ? "  |  " + where : ""}${s.g && s.g !== "_" ? "  |  " + s.g : ""}`, 9.5);
      }
      if (ss.length > 12) text(`+ ${ss.length - 12} more dates`, 9.5, "italic");
      y += 2;
      link(`Sign up / details in ${systemName(c)}`, c.url);
      if (c.lms) { y += 1; link(`${c.lms.source === "tu_isis" ? "ISIS" : "Moodle"} course`, c.lms.url); }
      y += 5;
    }
    ensure(14);
    y += 2;
    text("Studying at another university? Sign up on the linked page; for courses outside your own university you may need to register as a guest student (Nebenhörer) at the host university. Check dates in the linked catalog before the semester starts.", 8, "italic", [107, 104, 96]);
    doc.save("my-courses.pdf");
  } catch (e) {
    alert("Could not create the PDF: " + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Download PDF"; }
  }
}

// ---------- events ----------
function setView(v) {
  S.view = v;
  $$(".tabs button").forEach((x) => x.classList.toggle("active", x.dataset.view === v));
  $$(".view").forEach((el) => el.classList.toggle("active", el.id === "view-" + v));
  render();
}
function bindEvents() {
  document.addEventListener("click", (e) => {
    const fav = e.target.closest("[data-fav]");
    if (fav) { e.stopPropagation(); toggleFav(fav.dataset.fav); return; }
    if (e.target.closest("[data-close]")) { $("#drawer").hidden = true; return; }
    if (e.target.id === "ics") { exportIcs(); return; }
    if (e.target.id === "pdf") { exportPdf(); return; }
    if (e.target.closest("a[href]:not([data-open])")) return;
    const open = e.target.closest("[data-open]");
    if (open) { e.preventDefault(); openCourse(open.dataset.open); return; }
    const cardEl = e.target.closest(".card, .ev, .dated li, .mine-row");
    if (cardEl) openCourse(cardEl.dataset.id);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });
  $$(".tabs button").forEach((b) => b.onclick = () => setView(b.dataset.view));
  $("#openList").onclick = () => setView("mine");
  $("#more").onclick = () => { S.shown += PAGE; renderList(); };
  $("#toggleFilters").onclick = () => $("#filters").classList.toggle("open");
  // theme: follows the system until toggled, then remembered
  const isDark = () => (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const syncTheme = () => { $("#theme").textContent = isDark() ? "☀️" : "🌙"; };
  $("#theme").onclick = () => {
    const t = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = t; store.set("theme", t); syncTheme();
  };
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncTheme);
  syncTheme();
}

// ---------- boot ----------
async function boot() {
  bindEvents();
  try {
    const [courses, locs, meta] = await Promise.all([
      fetchJson("data/courses.json", 60000), fetchJson("data/locations.json"), fetchJson("data/meta.json"),
    ]);
    S.locs = locs; S.meta = meta;
    for (const c of courses) {
      c._fmt = format(c);
      c._last = lastDate(c);
      const l = `${c.ail || ""} ${c.lang || ""}`.toLowerCase();
      c._lang = [l.includes("en") || l.includes("engl") ? "en" : "", l.includes("de") || l.includes("deutsch") ? "de" : ""].filter(Boolean);
      c._text = [c.t, c.ty, c.org, c.nr, (c.lec || []).join(" "), (c.tags || []).join(" "), (c.kw || []).join(" "), c.sum, (c.path || []).join(" "), (c.ss || []).map((s) => s.room).join(" ")].join(" ").toLowerCase();
      S.byId.set(c.id, c);
    }
    S.courses = courses;
  } catch (e) {
    $("#list").innerHTML = `<div class="empty">Could not load course data (${esc(e.message)}).</div>`;
    return;
  }
  buildFilters();
  render();
}
boot();
})();
