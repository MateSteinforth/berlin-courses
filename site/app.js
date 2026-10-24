/* Berlin Courses – static frontend. Data comes from data/*.json (built by the crawler).
   Everything personal (addresses, free time, favourites, travel times) lives in localStorage. */
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
const TOD = [["morning", "Morning <12", 0, 720], ["midday", "Midday 12–15", 720, 900], ["afternoon", "Afternoon 15–18", 900, 1080], ["evening", "Evening 18+", 1080, 1440]];
const FORMATS = [["weekly", "Weekly"], ["block", "Block / intensive"], ["dates", "Single dates"]];
const GRID_START = 7 * 60, GRID_END = 22 * 60, STEP = 30; // free-time grid: 07:00–22:00, 30-min cells
const PAGE = 60;

// ---------- storage ----------
const store = {
  get(k, d) { try { const v = localStorage.getItem("bc." + k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("bc." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const S = {
  courses: [], byId: new Map(), locs: {}, meta: {}, details: null,
  places: store.get("places", {}),          // {home:{lat,lon,label}, work:{…}}
  free: new Set(store.get("free", [])),     // "d:minute" cells
  favs: new Set(store.get("favs", [])),
  travel: store.get("travel", {}),          // `${originKey}|${mode}` -> {bKey: minutes}
  travelApprox: store.get("travelApprox", {}),
  f: Object.assign({
    q: "", sems: [], unis: [], formats: [], days: [], langs: [], rel: null, tags: [],
    sg: false, tod: [], todFrom: "", todTo: "", free: false, dist: false, maxMin: 30, mode: "bike", origin: "best", past: true, arr: false, sort: "rel",
  }, store.get("filters", {})),
  view: "list", shown: PAGE, filtered: [],
};

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const toMin = (hhmm) => { if (!hhmm) return null; const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const fmtDate = (iso) => { const d = new Date(iso + "T12:00"); return `${d.getDate()}.${d.getMonth() + 1}.`; };
const todayIso = new Date().toISOString().slice(0, 10);
const saveFilters = () => store.set("filters", S.f);

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

function lastDate(c) {
  return (c.ss || []).reduce((m, s) => (s.l || s.f || "") > m ? (s.l || s.f) : m, "");
}

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

// ---------- travel ----------
const originKey = (p) => `${p.lat.toFixed(4)},${p.lon.toFixed(4)}`;
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

/** minutes from a place to a building for a mode (cached routing result or estimate). */
function minutesTo(placeName, bKey, mode = S.f.mode) {
  const p = S.places[placeName]; const loc = S.locs[bKey];
  if (!p || !loc) return null;
  const hit = S.travel[`${originKey(p)}|${mode}`]?.[bKey];
  if (hit != null) return hit;
  return Math.round(ESTIMATE[mode](haversineKm(p, loc)));
}
function travelFor(bKey, mode = S.f.mode) {
  const opts = S.f.origin === "best" ? ["home", "work"] : [S.f.origin];
  let best = null, from = null;
  for (const o of opts) {
    const m = minutesTo(o, bKey, mode);
    if (m != null && (best == null || m < best)) { best = m; from = o; }
  }
  return best == null ? null : { min: best, from };
}

async function fetchJson(url, ms = 12000) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
  try { const r = await fetch(url, { signal: ctl.signal }); if (!r.ok) throw new Error(r.status); return await r.json(); }
  finally { clearTimeout(t); }
}

let travelRunning = false;
async function computeTravel() {
  if (travelRunning) return; travelRunning = true;
  const keys = Object.keys(S.locs);
  const status = (t) => { $("#travelStatus").textContent = t; };
  try {
    for (const [name, p] of Object.entries(S.places)) {
      if (!p) continue;
      const ok = originKey(p);
      // walk + bike (+ scooter derived from bike distance) via OSRM tables
      for (const [mode, profile] of [["walk", "routed-foot"], ["bike", "routed-bike"]]) {
        const ck = `${ok}|${mode}`;
        if (S.travel[ck] && keys.every((k) => k in S.travel[ck])) continue;
        status(`Routing ${mode} times from ${name}…`);
        const res = S.travel[ck] || {};
        const scoot = S.travel[`${ok}|scooter`] || {};
        for (let i = 0; i < keys.length; i += 80) {
          const chunk = keys.slice(i, i + 80);
          const coords = [`${p.lon},${p.lat}`, ...chunk.map((k) => `${S.locs[k].lon},${S.locs[k].lat}`)].join(";");
          try {
            const j = await fetchJson(`https://routing.openstreetmap.de/${profile}/table/v1/driving/${coords}?sources=0&annotations=duration,distance`);
            chunk.forEach((k, idx) => {
              const dist = j.distances?.[0]?.[idx + 1];
              const dur = j.durations?.[0]?.[idx + 1];
              if (dist == null) return;
              // OSRM durations are optimistic; use distance with realistic Berlin speeds
              if (mode === "walk") res[k] = Math.round(dist / 1000 / 4.8 * 60);
              else {
                res[k] = Math.round(Math.max(dur / 60, dist / 1000 / 15 * 60) + 2);
                scoot[k] = Math.round(dist / 1000 / 17 * 60 + 4);
              }
            });
          } catch (e) { status(`Routing service unavailable – using estimates`); }
        }
        S.travel[ck] = res;
        if (mode === "bike") S.travel[`${ok}|scooter`] = scoot;
        store.set("travel", S.travel); render();
      }
      // transit via BVG REST API (community service, sometimes down → estimate stays)
      const tk = `${ok}|transit`;
      const tres = S.travel[tk] || {};
      const todo = keys.filter((k) => !(k in tres));
      if (todo.length) {
        status(`Asking BVG for ${todo.length} transit routes from ${name}…`);
        const dep = nextWeekday(2, 9); // a Tuesday 09:00 as representative departure
        let fails = 0;
        for (const k of todo) {
          const L = S.locs[k];
          try {
            const j = await fetchJson(`https://v6.bvg.transport.rest/journeys?from.latitude=${p.lat}&from.longitude=${p.lon}&from.address=start` +
              `&to.latitude=${L.lat}&to.longitude=${L.lon}&to.address=${encodeURIComponent(L.name)}&departure=${dep}&results=3&stopovers=false&remarks=false`, 9000);
            const mins = (j.journeys || []).map((jr) => {
              const a = new Date(jr.legs[0].departure || jr.legs[0].plannedDeparture);
              const b = new Date(jr.legs.at(-1).arrival || jr.legs.at(-1).plannedArrival);
              return (b - a) / 60000;
            }).filter((m) => m > 0);
            if (mins.length) { tres[k] = Math.round(Math.min(...mins)); S.travel[tk] = tres; }
            await new Promise((r) => setTimeout(r, 650)); // API limit: 100 req/min
          } catch {
            if (++fails >= 2) { S.travelApprox.transit = true; break; }
          }
        }
        store.set("travel", S.travel); store.set("travelApprox", S.travelApprox);
      }
    }
    status(S.travelApprox.transit && S.f.mode === "transit"
      ? "BVG API unreachable right now – transit times are estimates (≈). Use the route links to check."
      : Object.keys(S.places).length ? "" : "Set home/work under “My places” to see travel times.");
  } finally { travelRunning = false; render(); }
}
function nextWeekday(dow, hour) { // dow: 0=Mon
  const d = new Date(); d.setHours(hour, 0, 0, 0);
  const cur = (d.getDay() + 6) % 7; let add = (dow - cur + 7) % 7; if (add === 0) add = 7;
  d.setDate(d.getDate() + add); return d.toISOString();
}

// ---------- free-time fit ----------
function isFree(day, from, to) {
  if (from == null || to == null) return true;
  for (let m = Math.floor(from / STEP) * STEP; m < to; m += STEP) {
    if (m < GRID_START || m >= GRID_END) return false;
    if (!S.free.has(`${day}:${m}`)) return false;
  }
  return true;
}
function sessionFits(s) {
  const t = s.b ? travelFor(s.b) : null;
  const pad = t ? t.min : 0;
  const from = toMin(s.s), to = toMin(s.e);
  if (from == null) return true; // no time given → can't judge, don't exclude
  const days = s.d != null && (s.r === "weekly" || s.r === "biweekly") ? [s.d]
    : (occurrences(s) || []).map((iso) => (new Date(iso + "T12:00").getDay() + 6) % 7);
  if (!days.length && s.d != null) days.push(s.d);
  return days.every((d) => isFree(d, from - pad, to + pad));
}
const courseFits = (c) => groups(c).some((g) => g.every(sessionFits));

function courseTravel(c) {
  let best = null;
  for (const s of c.ss || []) {
    if (!s.b) continue;
    const t = travelFor(s.b);
    if (t && (best == null || t.min < best.min)) best = { ...t, b: s.b };
  }
  return best;
}

// ---------- time of day ----------
/** Session lies inside one selected time-of-day window (and the custom from/to range, if set). */
function inTimeOfDay(s) {
  const a = toMin(s.s), b = toMin(s.e);
  if (a == null || b == null) return true;
  const f = S.f;
  if (f.todFrom && a < toMin(f.todFrom)) return false;
  if (f.todTo && b > toMin(f.todTo)) return false;
  if (!f.tod.length) return true;
  // allow adjacent windows to combine: Morning+Midday = 0:00–15:00
  const wins = TOD.filter((w) => f.tod.includes(w[0])).map((w) => [w[2], w[3]]).sort((x, y) => x[0] - y[0]);
  const merged = [];
  for (const w of wins) { const l = merged.at(-1); if (l && w[0] <= l[1]) l[1] = Math.max(l[1], w[1]); else merged.push([...w]); }
  return merged.some(([lo, hi]) => a >= lo && b <= hi);
}

// ---------- filtering ----------
function matches(c) {
  const f = S.f;
  if (f.sems.length && !f.sems.includes(c.sem)) return false;
  if (f.unis.length && !f.unis.includes(c.u)) return false;
  const fm = c._fmt;
  if (fm === "none" && !f.arr) return false;
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
  if (f.langs.length) {
    const l = c._lang;
    if (!f.langs.some((x) => l.includes(x))) return false;
  }
  if (f.rel && (c.rel ?? -1) < Number(f.rel)) return false;
  if (f.sg && !c.sg) return false;
  if (f.tags.length && !(c.tags || []).some((t) => f.tags.includes(t))) return false;
  if (f.q) {
    const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.every((w) => c._text.includes(w))) return false;
  }
  if (f.dist && Object.keys(S.places).length) {
    const t = courseTravel(c);
    if (t && t.min > f.maxMin) return false;
  }
  if ((f.tod.length || f.todFrom || f.todTo) && fm !== "none" && !groups(c).some((g) => g.every(inTimeOfDay))) return false;
  if (f.free && S.free.size && fm !== "none" && !courseFits(c)) return false;
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
  if (!Object.keys(S.places).length) return "";
  const t = courseTravel(c);
  if (!t) return `<span class="travel">no location</span>`;
  const icon = MODES.find((m) => m.id === S.f.mode).label.split(" ")[0];
  const approx = S.f.mode === "transit" && !S.travel[`${originKey(S.places[t.from])}|transit`]?.[t.b] ? "≈" : "";
  const cls = t.min <= S.f.maxMin * 0.6 ? "good" : t.min > S.f.maxMin ? "far" : "";
  return `<span class="travel ${cls}" title="from ${t.from}">${icon} ${approx}${t.min} min · ${t.from}</span>`;
}

function card(c) {
  const fits = S.free.size ? new Set((c.ss || []).filter(sessionFits)) : new Set();
  const slots = (c.ss || []).slice(0, 6).map((s) =>
    `<span class="slot ${fits.has(s) ? "fit" : ""} ${s.r === "block" ? "block" : ""}">${esc(slotLabel(s))}</span>`).join("");
  const more = (c.ss || []).length > 6 ? `<span class="slot">+${c.ss.length - 6}</span>` : "";
  const where = (() => { const b = (c.ss || []).find((s) => s.b)?.b; return b ? S.locs[b].name : ((c.ss || []).find((s) => s.room)?.room || ""); })();
  return `<div class="card" data-id="${esc(c.id)}">
    <div>
      <div class="meta"><span class="uni ${c.u}">${c.u}</span>${c.ty ? `<span>${esc(c.ty)}</span>` : ""}${c.lec ? `<span>${esc(c.lec.slice(0, 3).join(", "))}</span>` : ""}${c.rel === 3 ? `<span class="rel3">★ D+C core</span>` : ""}${c.sg ? `<span class="rel3">Studium Generale</span>` : ""}</div>
      <h4>${esc(c.t)}</h4>
    </div>
    <button class="star ${S.favs.has(c.id) ? "on" : ""}" data-fav="${esc(c.id)}" aria-label="Favourite">★</button>
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
    map = L.map("map", { zoomControl: true }).setView([52.512, 13.33], 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors", maxZoom: 19,
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
  }
  setTimeout(() => map.invalidateSize(), 50);
  layer.clearLayers();
  const byB = new Map();
  for (const c of S.filtered) for (const b of new Set((c.ss || []).map((s) => s.b).filter(Boolean))) {
    if (!byB.has(b)) byB.set(b, []);
    byB.get(b).push(c);
  }
  for (const [b, cs] of byB) {
    const L0 = S.locs[b];
    const n = cs.length, size = Math.min(44, 20 + Math.sqrt(n) * 3);
    const icon = L.divIcon({ className: "", html: `<div class="pin ${cs[0].u}" style="width:${size}px;height:${size}px">${n}</div>`, iconSize: [size, size] });
    const t = travelFor(b);
    const html = `<b>${esc(L0.name)}</b>${L0.approx ? " (approx.)" : ""}<br>${esc(L0.address || "")}<br>${t ? `${t.min} min from ${t.from}<br>` : ""}` +
      cs.slice(0, 8).map((c) => `<a href="#" data-open="${esc(c.id)}">${esc(c.t)}</a>`).join("<br>") + (n > 8 ? `<br>…and ${n - 8} more` : "");
    L.marker([L0.lat, L0.lon], { icon }).bindPopup(html).addTo(layer);
  }
  for (const [name, p] of Object.entries(S.places)) {
    if (!p) continue;
    const icon = L.divIcon({ className: "", html: `<div class="pin me" style="width:30px;height:30px">${name === "home" ? "⌂" : "✱"}</div>`, iconSize: [30, 30] });
    L.marker([p.lat, p.lon], { icon }).bindPopup(name).addTo(layer);
  }
}

function renderWeek() {
  const favs = S.courses.filter((c) => S.favs.has(c.id));
  const showDays = favs.some((c) => (c.ss || []).some((s) => s.d >= 5)) ? 7 : 5;
  const H0 = 8, H1 = 21, PX = 44 / 60;
  let cols = "";
  for (let d = 0; d < showDays; d++) {
    let bg = "";
    for (let m = GRID_START; m < GRID_END; m += STEP) {
      if (S.free.has(`${d}:${m}`) && m >= H0 * 60 && m < H1 * 60) bg += `<div class="freebg" style="top:${(m - H0 * 60) * PX}px;height:${STEP * PX}px"></div>`;
    }
    const evs = [];
    for (const c of favs) for (const s of c.ss || []) {
      if (s.d !== d || !(s.r === "weekly" || s.r === "biweekly") || !s.s) continue;
      evs.push({ c, s, a: toMin(s.s), b: toMin(s.e) });
    }
    const html = evs.map((e) => {
      const clash = evs.some((o) => o !== e && o.c !== e.c && o.a < e.b && e.a < o.b);
      return `<div class="ev ${e.c.u} ${clash ? "clash" : ""}" data-id="${esc(e.c.id)}" style="top:${(e.a - H0 * 60) * PX}px;height:${Math.max(18, (e.b - e.a) * PX)}px" title="${esc(e.c.t)}">${e.s.s} ${esc(e.c.t)}</div>`;
    }).join("");
    cols += `<div class="col" style="grid-row:2;grid-column:${d + 2};height:${(H1 - H0) * 60 * PX}px">${bg}${html}</div>`;
  }
  let hours = "";
  for (let h = H0; h < H1; h++) hours += `<div class="hour">${h}:00</div>`;
  const dated = [];
  for (const c of favs) for (const s of c.ss || []) {
    if (s.r === "weekly" || s.r === "biweekly") continue;
    for (const o of occurrences(s) || []) dated.push({ o, c, s });
  }
  dated.sort((a, b) => a.o.localeCompare(b.o) || (a.s.s || "").localeCompare(b.s.s || ""));
  $("#week").innerHTML = favs.length ? `
    <div class="week-actions"><button class="btn" id="ics">Export to calendar (.ics)</button></div>
    <div class="week"><div class="week-grid" style="--days:${showDays}">
      <div class="hd"></div>${DAYS.slice(0, showDays).map((d) => `<div class="hd">${d}</div>`).join("")}
      <div style="grid-row:2;grid-column:1">${hours}</div>${cols}
    </div></div>
    <div class="dated"><h3>Blocks &amp; single dates</h3>${dated.length ? `<ul>${dated.map((x) =>
      `<li data-id="${esc(x.c.id)}"><span class="d">${new Date(x.o + "T12:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })} ${x.s.s ? x.s.s + "–" + x.s.e : ""}</span><span>${esc(x.c.t)}</span></li>`).join("")}</ul>` : `<p class="hint">None among your favourites.</p>`}</div>`
    : `<div class="empty">Star courses (★) to plan your week here. Free time you painted shows in green; clashes are outlined.</div>`;
}

function renderMeta() {
  const m = S.meta;
  const next = m.next_update ? ` · next update ${new Date(m.next_update).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}` : "";
  $("#metaLine").textContent = `${m.count?.toLocaleString() ?? "?"} courses · TU & UdK · updated ${m.built ? new Date(m.built).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "?"}${next}`;
  $("#favCount").textContent = S.favs.size || "";
}

function render() {
  applyFilters();
  $("#resultCount").textContent = `${S.filtered.length.toLocaleString()} courses`;
  if (S.view === "list") renderList();
  if (S.view === "map") renderMap();
  if (S.view === "week") renderWeek();
  renderMeta();
}

// ---------- filter UI ----------
function chipGroup(el, items, key, { single = false, counts = null } = {}) {
  el.innerHTML = items.map(([v, label]) =>
    `<span class="chip" data-v="${esc(v)}">${esc(label)}${counts ? `<span class="n">${counts[v] || 0}</span>` : ""}</span>`).join("");
  const sync = () => $$(".chip", el).forEach((ch) => {
    const v = ch.dataset.v; const cur = S.f[key];
    ch.classList.toggle("on", single ? String(cur) === v : cur.map(String).includes(v));
  });
  el.onclick = (e) => {
    const ch = e.target.closest(".chip"); if (!ch) return;
    let v = ch.dataset.v;
    if (single) S.f[key] = String(S.f[key]) === v ? null : v;
    else {
      const num = key === "days";
      const val = num ? Number(v) : v;
      S.f[key] = S.f[key].includes(val) ? S.f[key].filter((x) => x !== val) : [...S.f[key], val];
    }
    sync(); S.shown = PAGE; saveFilters(); render();
  };
  sync();
}

function buildFilters() {
  const cnt = (fn) => S.courses.reduce((m, c) => { for (const v of [].concat(fn(c))) m[v] = (m[v] || 0) + 1; return m; }, {});
  chipGroup($("#semesters"), (S.meta.semesters || []).filter((s) => s.count).map((s) => [s.key, s.label]), "sems", { counts: cnt((c) => c.sem) });
  chipGroup($("#unis"), [["TU", "TU Berlin"], ["UdK", "UdK Berlin"]], "unis", { counts: cnt((c) => c.u) });
  chipGroup($("#formats"), FORMATS, "formats", { counts: cnt((c) => c._fmt) });
  chipGroup($("#days"), DAYS.map((d, i) => [i, d]), "days");
  chipGroup($("#tod"), TOD.map((t) => [t[0], t[1]]), "tod");
  const opts = ['<option value="">any</option>'];
  for (let m = 7 * 60; m <= 22 * 60; m += 30) { const v = `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; opts.push(`<option>${v}</option>`); }
  $("#todFrom").innerHTML = opts.join(""); $("#todTo").innerHTML = opts.join("");
  chipGroup($("#langs"), Object.entries(LANGS), "langs");
  chipGroup($("#rel"), REL, "rel", { single: true });
  const tagCounts = cnt((c) => c.tags || []);
  chipGroup($("#tags"), Object.keys(tagCounts).sort((a, b) => tagCounts[b] - tagCounts[a]).map((t) => [t, t]), "tags", { counts: tagCounts });
  $("#modes").innerHTML = MODES.map((m) => `<button data-mode="${m.id}" class="${S.f.mode === m.id ? "on" : ""}">${m.label}</button>`).join("");
  $("#modes").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.f.mode = b.dataset.mode; $$("#modes button").forEach((x) => x.classList.toggle("on", x === b));
    saveFilters(); render(); computeTravel();
  };
  const bind = (id, key, prop = "checked", conv = (x) => x) => {
    const el = $(id); el[prop] = S.f[key];
    el.addEventListener("change", () => { S.f[key] = conv(el[prop]); S.shown = PAGE; saveFilters(); render(); });
  };
  bind("#fSg", "sg");
  bind("#todFrom", "todFrom", "value"); bind("#todTo", "todTo", "value");
  bind("#fFree", "free"); bind("#fDist", "dist"); bind("#fPast", "past"); bind("#fArr", "arr");
  bind("#maxMin", "maxMin", "value", Number); bind("#origin", "origin", "value"); bind("#sort", "sort", "value");
  const q = $("#q"); q.value = S.f.q;
  let tm; q.addEventListener("input", () => { clearTimeout(tm); tm = setTimeout(() => { S.f.q = q.value.trim(); S.shown = PAGE; saveFilters(); render(); }, 150); });
  $("#reset").onclick = () => {
    Object.assign(S.f, { sg: false, tod: [], todFrom: "", todTo: "", q: "", sems: [], unis: [], formats: [], days: [], langs: [], rel: null, tags: [], free: false, dist: false, arr: false, past: true });
    saveFilters(); location.reload();
  };
}

// ---------- detail drawer ----------
async function openCourse(id) {
  const c = S.byId.get(id); if (!c) return;
  const body = $("#drawerBody");
  const rows = groups(c).map((g) => {
    const name = g[0].g && g[0].g !== "_" ? `<tr><td colspan="3"><b>${esc(g[0].g)}</b></td></tr>` : "";
    return name + g.map((s) => {
      const t = s.b ? travelFor(s.b) : null;
      const L0 = s.b ? S.locs[s.b] : null;
      const route = L0 && t ? routeLinks(S.places[t.from], L0) : "";
      return `<tr><td>${esc(slotLabel(s))}${s.f && (s.r === "weekly" || s.r === "biweekly") ? `<br><small>${fmtDate(s.f)}–${s.l ? fmtDate(s.l) : ""}</small>` : ""}</td>
        <td>${esc(s.room || "")}${L0 ? `<br><small>${esc(L0.address || L0.name)}</small>` : ""}${s.n ? `<br><small>${esc(s.n)}</small>` : ""}</td>
        <td>${t ? `${t.min} min<br><small>from ${t.from}</small>${route}` : ""}</td></tr>`;
    }).join("");
  }).join("");
  body.innerHTML = `
    <div class="row"><button class="icon" data-close>✕</button><span class="uni ${c.u}">${c.u}</span>${esc(c.ty || "")}${c.nr ? ` · ${esc(c.nr)}` : ""}</div>
    <h2>${esc(c.t)}</h2>
    <div class="row">${c.lec ? esc(c.lec.join(", ")) : ""}</div>
    <div class="row">${esc(c.org || "")}${c.lang ? ` · ${esc(c.lang)}` : ""}</div>
    ${c.sum ? `<p>${esc(c.sum)}</p>` : ""}
    <div class="row">${(c.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join(" ")}${(c.kw || []).map((t) => `<span class="tag">${esc(t)}</span>`).join(" ")}</div>
    <div class="links">
      <a class="btn primary" href="${esc(c.url)}" target="_blank" rel="noopener">Open in ${c.u === "TU" ? "MOSES" : "HISinOne"} ↗</a>
      ${c.lms ? `<a class="btn" href="${esc(c.lms.url)}" target="_blank" rel="noopener">${c.lms.source === "tu_isis" ? "ISIS" : "Moodle"} course ↗</a>` : ""}
      <button class="btn" data-fav="${esc(c.id)}">${S.favs.has(c.id) ? "★ Saved" : "☆ Save"}</button>
    </div>
    <h3>Dates</h3>
    ${rows ? `<table>${rows}</table>` : `<p class="hint">${c.arr ? "By arrangement." : "No dates published."}</p>`}
    ${c.path ? `<h3>Catalog</h3><p class="hint">${esc(c.path.join(" › "))}</p>` : ""}
    <h3>Description</h3><div class="desc" id="desc">${c.hasd ? "Loading…" : "<span class='hint'>No description published.</span>"}</div>`;
  $("#drawer").hidden = false;
  if (c.hasd) {
    if (!S.details) S.details = await fetchJson("data/details.json", 60000).catch(() => ({}));
    const d = $("#desc"); if (d) d.textContent = S.details[id] || "—";
  }
}
function routeLinks(p, L0) {
  if (!p) return "";
  const gm = (m) => `https://www.google.com/maps/dir/?api=1&origin=${p.lat},${p.lon}&destination=${L0.lat},${L0.lon}&travelmode=${m}`;
  return `<br><small><a href="${gm("transit")}" target="_blank" rel="noopener">transit</a> · <a href="${gm("bicycling")}" target="_blank" rel="noopener">bike</a> · <a href="${gm("walking")}" target="_blank" rel="noopener">walk</a></small>`;
}

// ---------- favourites + ICS ----------
function toggleFav(id) {
  S.favs.has(id) ? S.favs.delete(id) : S.favs.add(id);
  store.set("favs", [...S.favs]);
  $$(`[data-fav="${CSS.escape(id)}"]`).forEach((b) => { b.classList.toggle("on", S.favs.has(id)); if (b.classList.contains("btn")) b.textContent = S.favs.has(id) ? "★ Saved" : "☆ Save"; });
  renderMeta(); if (S.view === "week") renderWeek();
}
function exportIcs() {
  const pad = (n) => String(n).padStart(2, "0");
  const dt = (iso, hhmm) => `${iso.replaceAll("-", "")}T${hhmm.replace(":", "")}00`;
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//berlin-courses//EN", "CALSCALE:GREGORIAN"];
  let n = 0;
  for (const c of S.courses.filter((x) => S.favs.has(x.id))) for (const s of c.ss || []) {
    if (!s.s || !s.f) continue;
    const loc = s.b ? `${S.locs[s.b].name}, ${s.room || ""}` : s.room || "";
    const base = [`SUMMARY:${c.t.replace(/[,;]/g, " ")}`, `LOCATION:${loc.replace(/[,;]/g, " ")}`, `URL:${c.url}`];
    const ev = (start, extra = []) => {
      lines.push("BEGIN:VEVENT", `UID:${c.id}-${n++}@berlin-courses`, `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}Z`,
        `DTSTART;TZID=Europe/Berlin:${dt(start, s.s)}`, `DTEND;TZID=Europe/Berlin:${dt(start, s.e)}`, ...base, ...extra, "END:VEVENT");
    };
    if ((s.r === "weekly" || s.r === "biweekly") && s.d != null) {
      // first occurrence on the right weekday
      const d0 = new Date(s.f + "T12:00"); while ((d0.getDay() + 6) % 7 !== s.d) d0.setDate(d0.getDate() + 1);
      const iso = `${d0.getFullYear()}-${pad(d0.getMonth() + 1)}-${pad(d0.getDate())}`;
      ev(iso, [`RRULE:FREQ=WEEKLY;INTERVAL=${s.r === "biweekly" ? 2 : 1}${s.l ? `;UNTIL=${s.l.replaceAll("-", "")}T235959Z` : ""}`]);
    } else for (const o of occurrences(s) || []) ev(o);
  }
  lines.push("END:VCALENDAR");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/calendar" }));
  a.download = "my-courses.ics"; a.click();
}

// ---------- setup dialog ----------
function buildFreeGrid() {
  const g = $("#freeGrid");
  let html = `<div></div>${DAYS.map((d) => `<div class="h">${d}</div>`).join("")}`;
  for (let m = GRID_START; m < GRID_END; m += STEP) {
    html += `<div class="t">${m % 60 === 0 ? `${m / 60}:00` : ""}</div>`;
    for (let d = 0; d < 7; d++) html += `<div class="c ${(m + STEP) % 60 === 0 ? "hr" : ""} ${S.free.has(`${d}:${m}`) ? "on" : ""}" data-k="${d}:${m}"></div>`;
  }
  g.innerHTML = html;
  let painting = null;
  const paint = (el) => {
    if (!el?.dataset?.k) return;
    painting ? S.free.add(el.dataset.k) : S.free.delete(el.dataset.k);
    el.classList.toggle("on", painting);
  };
  g.onpointerdown = (e) => { const el = e.target.closest(".c"); if (!el) return; painting = !S.free.has(el.dataset.k); paint(el); g.setPointerCapture(e.pointerId); };
  g.onpointermove = (e) => { if (painting === null) return; paint(document.elementFromPoint(e.clientX, e.clientY)?.closest?.(".c")); };
  g.onpointerup = g.onpointercancel = () => { if (painting !== null) { painting = null; store.set("free", [...S.free]); } };
  $$("[data-preset]").forEach((b) => b.onclick = () => {
    S.free.clear();
    for (let d = 0; d < 7; d++) for (let m = GRID_START; m < GRID_END; m += STEP) {
      if (b.dataset.preset === "all" || (b.dataset.preset === "school" && d < 5 && m >= 510 && m < 900)) S.free.add(`${d}:${m}`);
    }
    store.set("free", [...S.free]); buildFreeGrid();
  });
}
async function geocode(which) {
  const input = $(`#${which}Addr`); const out = $(`#${which}Found`);
  const q = input.value.trim(); if (!q) { delete S.places[which]; store.set("places", S.places); out.textContent = ""; return; }
  out.textContent = "searching…";
  try {
    const j = await fetchJson(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=de&viewbox=13.0,52.7,13.8,52.3&q=${encodeURIComponent(q + (/berlin/i.test(q) ? "" : ", Berlin"))}`);
    if (!j.length) { out.textContent = "not found"; return; }
    S.places[which] = { lat: +j[0].lat, lon: +j[0].lon, label: q };
    store.set("places", S.places);
    out.textContent = "✓ " + j[0].display_name.split(",").slice(0, 3).join(",");
    computeTravel();
  } catch { out.textContent = "lookup failed – try again"; }
}
function openSetup() {
  for (const w of ["home", "work"]) { $(`#${w}Addr`).value = S.places[w]?.label || ""; $(`#${w}Found`).textContent = S.places[w] ? "✓ set" : ""; }
  buildFreeGrid();
  $("#setup").showModal();
}

// ---------- events ----------
function bindEvents() {
  document.addEventListener("click", (e) => {
    const fav = e.target.closest("[data-fav]");
    if (fav) { e.stopPropagation(); toggleFav(fav.dataset.fav); return; }
    if (e.target.closest("[data-close]")) { $("#drawer").hidden = true; return; }
    const open = e.target.closest("[data-open]");
    if (open) { e.preventDefault(); openCourse(open.dataset.open); return; }
    const cardEl = e.target.closest(".card, .ev, .dated li");
    if (cardEl) openCourse(cardEl.dataset.id);
    if (e.target.id === "ics") exportIcs();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });
  $$(".tabs button").forEach((b) => b.onclick = () => {
    S.view = b.dataset.view;
    $$(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + S.view));
    render();
  });
  $("#more").onclick = () => { S.shown += PAGE; renderList(); };
  $("#openSetup").onclick = openSetup;
  $("#toggleFilters").onclick = () => $("#filters").classList.toggle("open");
  $$("[data-geo]").forEach((b) => b.onclick = () => geocode(b.dataset.geo));
  for (const w of ["home", "work"]) $(`#${w}Addr`).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); geocode(w); } });
  $("#setup").addEventListener("close", () => { store.set("free", [...S.free]); if (S.free.size && !S.f.free) { S.f.free = true; $("#fFree").checked = true; saveFilters(); } render(); });
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
  if (Object.keys(S.places).length) computeTravel();
  else $("#travelStatus").textContent = "Set home/work under “My places” to see travel times.";
}
boot();
})();
