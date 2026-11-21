/* The demo's "server": a service worker that answers MangaVibe's requests in the visitor's browser
   from what demo/build.py recorded (rec/), so the real app's page runs with no server behind it.

   - GETs are answered from rec/manifest.json (the same key as build.py's key_of()).
   - GET /api/series (the library) is worked out from rec/list.json: the ids each filter value and
     each sort gave when the demo was built, combined here.
   - Plugin switches, settings, reading progress, favorites and reading status are kept in this
     worker's memory only: they last until the browser stops the worker (closing the tab, or after a
     while). Nothing is saved anywhere.
   - Everything else that would change something (deleting, editing, installing, uploading...) is
     refused with a message, the way the real server refuses what isn't allowed. */
"use strict";

const BASE = new URL(self.registration.scope).pathname;   // e.g. /manga-vibe/
const REC = `${BASE}rec/`;
const IGNORED = new Set(["v", "w", "_"]);                   // build.py IGNORED_PARAMS
const DEMO = "This is the demo: nothing can be changed or saved here. Install MangaVibe to do this.";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

let loading = null;
let S = null;  // { manifest, list, switches, plugins, settings, progress, series, flipped }

async function getJson(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

function load() {
  loading ??= (async () => {
    const [manifest, list, switches] = await Promise.all(
      ["manifest.json", "list.json", "switches.json"].map((f) => getJson(REC + f)));
    S = { manifest, list, switches, plugins: null, settings: null, progress: {}, series: {}, flipped: [] };
    S.plugins = await recordedJson("GET /api/plugins");
    S.settings = await recordedJson("GET /api/settings");
    return S;
  })().catch((e) => { loading = null; throw e; });
  return loading;
}

function keyOf(method, url) {
  const q = [...url.searchParams].filter(([k]) => !IGNORED.has(k))
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
  const qs = new URLSearchParams(q).toString();
  return `${method} ${url.pathname}${qs ? "?" + qs : ""}`;
}

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" } });
const refuse = (detail = DEMO, status = 403) => json({ detail }, status);

async function recorded(key) {
  const hit = S.manifest[key];
  if (!hit) return null;
  const r = await fetch(`${REC}r/${hit.f}`);
  return new Response(r.body, { status: hit.s, headers: { "Content-Type": hit.t } });
}

async function recordedJson(key) {
  const r = await recorded(key);
  return r ? r.json() : null;
}

/* ------------------------------------------------------------------ the library list */

function seriesList(url) {
  const L = S.list, p = url.searchParams;
  const sort = L.sorts[p.get("sort")] ? p.get("sort") : L.default_sort;
  const order = p.get("order") === "asc" || p.get("order") === "desc" ? p.get("order") : "default";
  let ids = (L.sorts[sort]?.[order] || L.all).slice();
  const keep = (set) => { const s = new Set(set); ids = ids.filter((id) => s.has(id)); };
  for (const [param, byValue] of Object.entries(L.values)) {
    for (const raw of p.getAll(param)) {
      const wanted = param === "genre" ? [raw] : raw.split(",").filter(Boolean);
      if (wanted.length) keep(wanted.flatMap((v) => byValue[v.toLowerCase()] || byValue[v] || []));
    }
  }
  for (const name of p.getAll("exclude")) {
    const out = new Set(L.values.genre?.[name] || []);
    ids = ids.filter((id) => !out.has(id));
  }
  for (const [scope, set] of Object.entries(L.scopes)) {
    const [k, v] = scope.split("=");
    if (p.get(k) === v) keep(set);
  }
  const q = (p.get("q") || "").trim().toLowerCase();
  if (q) ids = ids.filter((id) => L.facts[id]?.text.includes(q));
  const num = (k) => (p.get(k) ? Number(p.get(k)) : null);
  const unknown = ["1", "true", "yes", "on"].includes((p.get("length_unknown") || "").toLowerCase());
  for (const [k, field, cmp] of [["year_min", "year", 1], ["year_max", "year", -1], ["score_min", "score", 1]]) {
    const n = num(k);
    if (n) ids = ids.filter((id) => L.facts[id][field] != null && (cmp > 0 ? L.facts[id][field] >= n : L.facts[id][field] <= n));
  }
  for (const unit of ["chapters", "volumes"]) {
    const lo = num(`${unit}_min`), hi = num(`${unit}_max`);
    if (lo == null && hi == null) continue;
    ids = ids.filter((id) => {
      const v = L.facts[id][unit];
      if (v == null) return unknown;
      return (lo == null || v >= lo) && (hi == null || v <= hi);
    });
  }
  if (["1", "true"].includes(p.get("ids_only"))) return json({ ids });
  const cursor = Math.max(0, Number(p.get("cursor")) || 0);
  const limit = Math.max(1, Math.min(200, Number(p.get("limit")) || 60));
  const items = ids.slice(cursor, cursor + limit).map((id) => ({ ...L.cards[id], ...cardPatch(id) }));
  const next = cursor + items.length;
  return json({ items, next_cursor: next < ids.length ? next : null, total: ids.length });
}

function cardPatch(id) {
  const p = S.series[id];
  if (!p) return {};
  return Object.fromEntries(Object.entries(p).filter(([k]) => k in S.list.cards[id]));
}

/* ------------------------------------------------------------------ plugins */

function switchPlugin(id, enabled) {
  const rec = S.switches[id];
  const plugin = S.plugins.plugins.find((x) => x.id === id);
  if (!rec || !plugin) return refuse("Plugin not found", 404);
  if (rec.s !== 200) return json(rec.body, rec.s);
  if (plugin.enabled === enabled) return json({ ok: true, started: [], stopped: [], ui_started: [], ui_stopped: [] });
  // the recorded switch went from how the demo starts; going back is the same, the other way round
  const forward = !S.flipped.includes(id);
  const b = rec.body;
  const answer = forward ? b : { ok: true, started: b.stopped, stopped: b.started,
                                 ui_started: b.ui_stopped, ui_stopped: b.ui_started };
  plugin.enabled = enabled;
  // the plugins it replaced (an exclusive job): switched off with it, and on again when it goes back
  for (const other of S.plugins.plugins) {
    if (other.id !== id && b.stopped.includes(other.id) && other.enabled !== undefined) other.enabled = !forward;
  }
  S.flipped = forward ? [...S.flipped, id] : S.flipped.filter((x) => x !== id);
  return json(answer);
}

/* ------------------------------------------------------------------ requests */

async function handle(request, url) {
  await load();
  const method = request.method, path = url.pathname;
  let m;
  const body = async () => { try { return await request.json(); } catch { return {}; } };

  if (method === "GET") {
    if (path === "/") return page();
    if (path === "/api/series") return seriesList(url);
    if (path === "/api/plugins") return json(S.plugins);
    if (path === "/api/settings") return json(S.settings);
    if ((m = path.match(/^\/api\/plugins\/([^/]+)\/ui$/)) && S.switches[m[1]]?.ui) return json(S.switches[m[1]].ui);
    if ((m = path.match(/^\/api\/books\/(\d+)\/progress$/)) && S.progress[m[1]]) return json(S.progress[m[1]]);
    if ((m = path.match(/^\/api\/series\/(\d+)$/)) && S.series[m[1]]) {
      const r = await recordedJson(keyOf("GET", url));
      return json({ ...r, ...S.series[m[1]] });
    }
    const hit = await recorded(keyOf("GET", url));
    if (hit) return hit;
    // a link's icon comes from its website: the demo doesn't fetch those, so the plain icon shows
    if (/^\/api\/links\/\d+\/icon$/.test(path)) return refuse("No icon", 404);
    console.warn("[MangaVibe demo] not recorded:", keyOf("GET", url));
    return refuse("This isn't part of the demo.", 404);
  }

  if (method === "PUT" && (m = path.match(/^\/api\/plugins\/([^/]+)$/))) {
    return switchPlugin(decodeURIComponent(m[1]), !!(await body()).enabled);
  }
  if (method === "PUT" && path === "/api/settings") {
    const b = await body();
    for (const k of Object.keys(b)) if (k in S.settings && k !== "me") S.settings[k] = b[k];
    return json(S.settings);
  }
  if (method === "PUT" && (m = path.match(/^\/api\/books\/(\d+)\/progress$/))) {
    const b = await body();
    S.progress[m[1]] = { page: Math.max(0, b.page | 0), completed: !!b.completed };
    return json(S.progress[m[1]]);
  }
  if (method === "PATCH" && (m = path.match(/^\/api\/series\/(\d+)$/))) {
    const b = await body(), patch = (S.series[m[1]] ||= {});
    if (b.favorite != null) patch.favorite = !!b.favorite;
    if (b.reading_status) patch.manual_reading_status = b.reading_status;
    if (b.clear_reading_status) patch.manual_reading_status = null;
    if (b.reader_mode) patch.reader_mode = b.reader_mode === "default" ? null : b.reader_mode;
    return json({ ok: true });
  }
  // the libraries plugin locks every library when a tab opens: nothing to lock here
  if (method === "POST" && path.endsWith("/shelves/lock")) return json({ ok: true });
  return refuse();
}

/* The app's page, with the demo's badge added. As recorded, or, after switching plugins that change
   the page (a view, a Settings section, the Saved button...), as it was with the last of those switched:
   each one was recorded on its own, so two such switches at once show only the last one. */
async function page() {
  const changing = S.flipped.filter((id) => S.switches[id]?.page);
  const file = changing.length ? S.switches[changing[changing.length - 1]].page : null;
  const r = file ? await fetch(`${REC}r/${file}`) : await recorded("GET /");
  let html = await r.text();
  html = html.replace("</head>", `<link rel="stylesheet" href="${BASE}demo.css">\n</head>`)
             .replace("</body>", `<script src="${BASE}demo.js"></script>\n</body>`);
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

const APP_PATHS = /^\/(api\/|plugins\/|custom\.css$|manifest\.json$)/;

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;
  const path = url.pathname;
  // the demo's own page: the app (index.html is only the loader that installs this worker)
  if (e.request.mode === "navigate" && (path === BASE || path === `${BASE}index.html`)) {
    if (url.searchParams.has("loader")) return;
    e.respondWith(handle(new Request("/"), new URL("/", url)).catch(failed));
    return;
  }
  if (path.startsWith(BASE)) return;  // the demo's own files
  if (path.startsWith("/static/")) {
    e.respondWith(fetch(`${REC}${path.slice(1)}`));
    return;
  }
  if (APP_PATHS.test(path)) e.respondWith(handle(e.request, url).catch(failed));
});

function failed(err) {
  console.error("[MangaVibe demo]", err);
  return refuse(`The demo couldn't answer: ${err.message}`, 500);
}
