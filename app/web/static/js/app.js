/* MangaVibe UI with Alpine.js (https://alpinejs.dev). The markup lives in the Jinja templates in
   app/web/templates (page.html first);
   this file holds the data and actions. The reader is plain JS in reader.js.

   Routes use the URL hash:
     #/?genre=..&read=..  library (filters in the URL)   #/series/12?tab=files  series page
     #/continue           continue reading               #/attention?tab=..     needs attention
     #/settings           settings                       #/read/34?page=5       reader
     #/settings/plugin-mal_profile_sync  one plugin's settings page                                   */
"use strict";

const STATUS_LABELS = { unread: t("Unread"), reading: t("Reading"), completed: t("Completed"),
  plan_to_read: t("Plan to read"), on_hold: t("On hold"), dropped: t("Dropped") };
const PUB_LABELS = { RELEASING: t("Releasing"), FINISHED: t("Finished"), HIATUS: t("Hiatus"), CANCELLED: t("Cancelled"),
  NOT_YET_RELEASED: t("Not yet released"), UNKNOWN: t("Unknown status") };
const COUNTRY = { JP: t("Manga"), KR: t("Manhwa"), CN: t("Manhua"), TW: t("Manhua") };
/* The library's filters and sorts are the library-query plugin's (Plugin API 16, issue #204): what
   GET /api/library/schema says, worked out once into what the sheets, chips and saved searches use, and
   kept in $store.app.lib (so they redraw when another plugin takes over the job). By kind:
   - choice: a group of options in the filter sheet (comma-separated values: read=unread,reading)
   - tags: the Genres and Tags groups (include/exclude; one parameter per value: genre=A&genre=B)
   - names: free-text names, one parameter per value (author=A&author=B), each a chip "Author: A";
     a group in the filter sheet lists the names `source` gives ([{name, count}], e.g. /api/authors)
   - length: published totals within a range per unit (<unit>_min, <unit>_max, length_unknown)
   - toggle "fav" and the "read" choice also make the quick buttons; letter: the Letter sheet
   - text: the search box; anything "hidden" only comes through links and assistants
   The schema's labels are English (assistants read it too): they're shown with t() (issue #263). */
function deriveLib(s = {}) {
  const filters = (s.filters || []).map((f) => ({ ...f, label: t(f.label || ""),
    options: f.options?.map(([v, label]) => [v, t(label)]), units: f.units?.map(([u, label]) => [u, t(label)]) }));
  const sorts = (s.sorts || []).map((x) => ({ ...x, label: t(x.label || ""), order_labels: x.order_labels?.map((l) => t(l)) }));
  const choices = filters.filter((f) => f.kind === "choice" && !f.hidden && f.params?.length);
  const tags = filters.find((f) => f.kind === "tags");
  const length = filters.find((f) => f.kind === "length");
  const names = filters.filter((f) => f.kind === "names" && f.params?.length);
  const read = choices.find((f) => f.params[0] === "read");
  return {
    plugin: s.plugin || null,
    // [param, title, { value: label }] for each choice group of the filter sheet
    groups: choices.map((f) => [f.params[0], f.label, Object.fromEntries(f.options || [])]),
    sorts: sorts.map((x) => [x.key, x.label]),
    sortInfo: Object.fromEntries(sorts.map((x) => [x.key, x])),
    defaultSort: s.default_sort || sorts[0]?.key || "",
    repeated: [...(tags ? tags.params : []), ...names.map((f) => f.params[0])],  // one URL parameter per value
    tags: !!tags,
    names: names.map((f) => [f.params[0], f.label, f.source || ""]),  // [param, label, source] of each names filter
    listKeys: [...choices.map((f) => f.params[0]), ...(tags ? tags.params : []), ...names.map((f) => f.params[0])],
    lengthUnits: length ? length.units || [] : [],
    lengthKeys: length ? length.params || [] : [],
    letter: filters.some((f) => f.kind === "letter"),
    fav: filters.some((f) => f.kind === "toggle" && f.params?.[0] === "fav"),
    readValues: read ? read.options.map(([v]) => v) : [],
    params: [...(s.params || []), "sort", "order"],  // what a search is (saved searches keep these)
  };
}
// the library-query plugin's filters, as worked out above (before the schema is loaded: none)
const lib = () => window.Alpine?.store("app")?.lib || NO_LIB;
const NO_LIB = deriveLib();
async function loadLibrarySchema() {
  try { Alpine.store("app").lib = deriveLib(await api("/api/library/schema")); } catch {}
}
// "10–50 chapters", "100+ chapters", "up to 10 volumes"; "" when that unit has no range. The words of the
// built-in units; another plugin's unit is shown by its key
const LENGTH_WORDS = {
  chapters: { one: (n) => tn("{n} chapter", "{n} chapters", n), range: (from, to) => t("{from}–{to} chapters", { from, to }),
              min: (n) => t("{n}+ chapters", { n }), max: (n) => t("Up to {n} chapters", { n }) },
  volumes: { one: (n) => tn("{n} volume", "{n} volumes", n), range: (from, to) => t("{from}–{to} volumes", { from, to }),
             min: (n) => t("{n}+ volumes", { n }), max: (n) => t("Up to {n} volumes", { n }) },
};
function lengthLabel(p, unit) {
  const lo = p.get(`${unit}_min`), hi = p.get(`${unit}_max`);
  const w = LENGTH_WORDS[unit] || { one: (n) => `${n} ${unit}`, range: (a, b) => `${a}–${b} ${unit}`,
                                    min: (n) => `${n}+ ${unit}`, max: (n) => `≤ ${n} ${unit}` };
  if (lo && hi) return lo === hi ? w.one(+lo) : w.range(lo, hi);
  if (lo) return w.min(lo);
  return hi ? w.max(hi) : "";
}
// first-letter filter (letter=B); "#" is everything else: numbers, symbols, other scripts
const LETTERS = ["#", ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"];
// the quick buttons above the grid, each shown while the library-query plugin has its filter (lib())
const QUICK = [["", t("All")], ["unread", t("Unread")], ["reading", t("Reading")], ["completed", t("Completed")],
  ["plan_to_read", t("Plan to read")], ["fav", t("Favorites")]];  // Favorites gets a star icon (templates/views/library.html)
const quickButtons = () => QUICK.filter(([v]) => v === "" || (v === "fav" ? lib().fav : lib().readValues.includes(v)));
// the Needs attention lists: [key, label, help, alert], the plugins' (attention:list, Plugin API 21: the page
// gets them as MV_ATTENTION_LISTS). alert: its rows count towards the red numbers
const ATT_TABS = (window.MV_ATTENTION_LISTS || []).map((l) => [l.key, l.label, l.help, !!l.alert]);

const ID_NAMES = { anilist_id: "AniList", mal_id: "MyAnimeList", mangaupdates_id: "MangaUpdates", mangadex_id: "MangaDex" };

/* ---------------- small helpers ---------------- */

// genre/exclude (the tags filter's parameters) hold free-text tag names (may contain commas): one URL
// param per value. The other filters use fixed values, joined with commas.
const getList = (p, k) => lib().repeated.includes(k) ? p.getAll(k).filter(Boolean)
  : (p.get(k) || "").split(",").filter(Boolean);
function setList(p, k, values) {
  p.delete(k);
  if (lib().repeated.includes(k)) values.forEach((v) => p.append(k, v));
  else if (values.length) p.set(k, values.join(","));
}
function libParams() {
  const [path, query] = location.hash.slice(1).split("?");
  return (path || "/") === "/" ? new URLSearchParams(query || "") : new URLSearchParams();
}
// filter, sort and search changes go back to page 1
function navigateLibrary(p) {
  p.delete("page");
  const qs = p.toString();
  location.replace("#/" + (qs ? "?" + qs : ""));
}
const libraryUrl = (params) => "#/?" + new URLSearchParams(params).toString();
const scoreCls = (s) => (s >= 90 ? "hi" : s >= 70 ? "mid" : "lo");
const kindOf = (s) => s.format === "NOVEL" ? t("Novel") : s.format === "ONE_SHOT" ? t("One-shot") : COUNTRY[s.country_of_origin];

// "8 / 8 volumes · complete", "120 / 150 chapters · some missing"; empty when there's no total to compare with.
// Marked complete by hand (omnibus editions): "12 / 37 volumes · complete (set by you)".
function ownedText(c) {
  return c.unit === "chapter" ? tn("{owned} / {n} chapter", "{owned} / {n} chapters", c.total, { owned: c.owned })
    : tn("{owned} / {n} volume", "{owned} / {n} volumes", c.total, { owned: c.owned });
}
function haveText(c) {
  if (c?.manual) return (c.total ? ownedText(c) + " · " : "") + t("complete (set by you)");
  if (!c?.total) return "";
  return ownedText(c) + ({ complete: " · " + t("complete"), incomplete: " · " + t("some missing") }[c.state] || "");
}

// The edition the volumes count against, when it isn't the regular one: "Kanzenban · 18 volumes (regular: 27)"
function editionText(c) {
  const e = c?.edition;
  if (!e) return "";
  return [e.names.join(", "), tn("{n} volume", "{n} volumes", e.volumes)
            + (e.regular && e.regular !== e.volumes ? " " + t("(regular: {n})", { n: e.regular }) : ""),
          e.manual ? t("set by you") : t("from the pages per volume")].filter(Boolean).join(" · ");
}

// The Edit form's Edition choices: [value, label]; "0" is automatic
function editionOptions(s) {
  const out = [["0", t("Automatic")]], seen = new Set();
  const add = (n, names) => {
    if (!n || seen.has(n)) return;
    seen.add(n);
    out.push([String(n), tn("{n} volume", "{n} volumes", n)
      + (names?.length ? ` · ${names.join(", ")}` : n === s.volumes_total ? " · " + t("regular") : "")]);
  };
  add(s.volumes_total, []);
  for (const e of s.editions || []) add(e.volumes, e.names);
  add(s.edition_volumes, []);
  return out;
}

function cardSub(s) {
  const st = s.reading_status, read = s.book_count - s.unread_count;
  if (st === "completed") return STATUS_LABELS.completed;  // the poster has the ✓ or ✕ badge
  if (st === "dropped") return STATUS_LABELS.dropped;
  if (st === "reading") return `${STATUS_LABELS.reading} · ${read}/${s.book_count}`;
  if (st === "unread") return `${STATUS_LABELS.unread} · ` + tn("{n} book", "{n} books", s.book_count);
  return STATUS_LABELS[st] || st;
}

/* "today", "yesterday", "3 days ago", "2 months ago" for a time in ms */
const RELATIVE = new Intl.RelativeTimeFormat(LANG, { numeric: "auto" });
function ago(ms) {
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(ms).setHours(0, 0, 0, 0)) / 864e5);
  if (days < 7) return RELATIVE.format(-Math.max(days, 0), "day");
  if (days < 30) return RELATIVE.format(-Math.floor(days / 7), "week");
  if (days < 365) return RELATIVE.format(-Math.floor(days / 30), "month");
  return RELATIVE.format(-Math.floor(days / 365), "year");
}

/* ---------------- global store + router ---------------- */

/* x-dialog="<close>" on a .modal or .sheet: a click on its backdrop (the element itself, not what's in
   it) or Escape runs <close>, e.g. x-dialog="open = false". The Android back button sends Escape too
   (MangaVibeApp.back). Plugins use it as well (docs/PLUGINS.md). */
function dialogDirective(el, { expression }, { evaluateLater, cleanup }) {
  const close = evaluateLater(expression);
  const onClick = (e) => e.target === el && close();
  const onKey = (e) => e.key === "Escape" && close();
  el.addEventListener("click", onClick);
  window.addEventListener("keydown", onKey);
  cleanup(() => {
    el.removeEventListener("click", onClick);
    window.removeEventListener("keydown", onKey);
  });
}

document.addEventListener("alpine:init", () => {
  Alpine.directive("dialog", dialogDirective);
  alpineStarted = true;  // plugin scripts loaded from now on run MangaVibe.onStart() at once
  Alpine.store("app", {
    pluginSlots: filledSlots(),  // the page's slots plugins put something in (see window.MangaVibe)
    view: null,          // library | series | continue | attention | settings | read
    id: null,            // series id for the series page
    params: {},          // query params of the current route
    query: "",           // raw query string (library filters)
    sheet: null,         // which bottom sheet is open: "filter" | "sort" | "letter" | null
    folderPicker: false,
    fix: { open: false, seriesId: null, onDone: null },
    edit: { open: false, seriesId: null, onDone: null, tab: "info" },
    seriesBack: false,   // the series page was opened from inside the app, so its Back button can go back
    attentionCount: 0,
    att: null,           // the Needs attention lists (loaded by refreshAttentionCount)
    showIgnored: false,
    sub: null,           // second part of the route, e.g. "display" in #/settings/display
    providers: [],
    providerName(id) { return this.providers.find((p) => p.id === id)?.name || id; },
    meta: null,          // GET /api/metadata/status, polled while Settings is open
    // automatic matching in progress: { done, total, pct, current, left } for the progress bar, else null
    get metaProgress() {
      const m = this.meta;
      if (!m || !(m.current || m.queued)) return null;
      const done = m.batch_done || 0, total = done + m.queued + (m.current ? 1 : 0);
      let left = "";
      if (m.batch_started && done >= 5) {  // a few series in, the pace is known
        const min = Math.round((Date.now() / 1000 - m.batch_started) / done * (total - done) / 60);
        left = min < 1 ? t("less than a minute left")
          : min < 60 ? t("about {min} min left", { min })
            : t("about {h} h {min} min left", { h: Math.floor(min / 60), min: min % 60 });
      }
      return { done, total, pct: Math.floor((done / total) * 100), current: m.current, left };
    },
    libraryDirty: 0,     // bump to make the library reload (e.g. after a match)
    progressDirty: 0,    // bumped after the reader closes and its last progress save is done
    update: null,        // GET /api/update: running version, newest version on GitHub
    updating: null,      // an install in progress: { state, message, error }
    updatingBack: false, // ...that goes back to an earlier version
    theme: document.documentElement.dataset.theme,  // "dark" | "light", as shown now (applyTheme)
    inApp: !!androidApp, // running inside the Android app (android/)
    // /?safe=1: the page has no markup, scripts or styles of the owner's own plugins (app/main.py), only
    // the built-in ones, so a plugin that breaks the page can be switched off (Plugin API 15)
    safe: !["", "0", "false"].includes(new URLSearchParams(location.search).get("safe") ?? ""),
    appBanner: ANDROID && !androidApp && !appBannerDismissed(),  // "Read in the app" on the library
    openFix(seriesId, onDone) { this.fix = { open: true, seriesId, onDone }; },
    openEdit(seriesId, onDone, tab = "info") { this.edit = { open: true, seriesId, onDone, tab }; },
    // the library's filters and sorts: the library-query plugin's (deriveLib, GET /api/library/schema)
    lib: deriveLib(),
    // what plugins add to every list's API call, per plugin: { libraries: { shelf: 3 } } (MangaVibe.setListScope)
    listScope: {},
    listsDirty: 0,       // bumped when what the lists show changed for another reason (MangaVibe.listsChanged)
    // what the lists depend on besides their own filters: they reload when it changes
    get listKey() { return `${JSON.stringify(this.listScope)}:${this.listsDirty}`; },
    // Settings' pages: [key, label, icon, plugin id (a plugin's own page), personal] (MangaVibe.settingsPage)
    settingsNav: SETTINGS_NAV.map((x) => [...x]),
    // who is using the app (GET /api/settings "me"): without accounts, the owner, who may do everything
    me: { id: 1, name: "", can_manage: true },
    get admin() { return this.me.can_manage; },  // settings, folders, metadata, deleting...
  });

  // the series page's data and actions (MangaVibe.series): loads the series the route shows by itself
  Alpine.store("series", seriesController());
  // the library page's and Continue reading's (MangaVibe.library, MangaVibe.continue)
  Alpine.store("library", libraryController());
  Alpine.store("continue", continueController());

  window.addEventListener("mv-theme", (e) => (Alpine.store("app").theme = e.detail));

  let lastView = null, libraryScroll = 0;
  history.scrollRestoration = "manual";  // we restore the library position ourselves
  function route() {
    const app = Alpine.store("app");
    const [path, query] = location.hash.slice(1).split("?");
    const parts = (path || "/").split("/").filter(Boolean);
    const params = new URLSearchParams(query || "");
    if (parts[0] === "attention") {  // old address: Needs attention now lives in Settings
      return location.replace(`#/settings/att-${params.get("tab") || "unreadable"}`);
    }
    const view = parts[0] || "library";
    if (view === "read") Reader.open(+parts[1], params.get("page"),
      lastView === "read" ? undefined : lastView !== null);  // next book: keep where the reader was opened from
    else Reader.close()?.then(() => app.progressDirty++);  // pages refresh once the server has the progress
    if (view !== lastView) {  // filter changes stay on the library: keep the sheet open
      app.sheet = null;
      app.folderPicker = false;
      app.fix.open = false;
      app.edit.open = false;
    }
    // all pages share one document: start new pages at the top, give the library its old position back
    const newId = parts[1] && /^\d+$/.test(parts[1]) ? +parts[1] : null;
    const newSub = parts[1] || null;
    let scrollTarget = null;
    if (view !== lastView || newId !== app.id || (view === "settings" && newSub !== app.sub)) {
      if (lastView === "library") libraryScroll = scrollY;
      scrollTarget = view === "library" ? libraryScroll : 0;
    }
    // a series opened from another page can go back to it; one opened directly (link, fresh tab) has
    // nowhere to go back to. Coming back from the reader or switching tabs keeps what it was.
    if (view === "series" && lastView !== "read" && (lastView !== "series" || newId !== app.id)) {
      app.seriesBack = lastView !== null;
    }
    app.view = view;
    app.id = newId;
    app.sub = newSub;
    if (view === "settings" && lastView !== "settings") refreshAttentionCount();  // sidebar counts
    app.params = Object.fromEntries(params);
    app.query = view === "library" ? params.toString() : app.query;
    lastView = view;
    if (scrollTarget !== null) {
      // Alpine updates the page in a microtask queued by the changes above; this one runs right after
      // it, before the page is painted (Alpine.nextTick waits for a timer, so the new page showed at
      // the old position for a frame). Once more on the next frame in case the layout grew.
      const y = scrollTarget;
      queueMicrotask(() => { scrollTo(0, y); requestAnimationFrame(() => scrollTo(0, y)); });
    }
  }
  window.addEventListener("hashchange", route);

  /* the badge polls a cheap count; the full lists (all books, for gaps) only load inside Settings */
  async function refreshAttentionCount() {
    const app = Alpine.store("app");
    if (!app.admin) return (app.attentionCount = 0);  // accounts: only admins fix what needs attention
    try {
      if (app.view === "settings") {
        app.att = await api(`/api/attention?${scopeParam()}${app.showIgnored ? "ignored=true" : ""}`);
        app.attentionCount = app.att.count;
      } else {
        app.attentionCount = (await api(`/api/attention/count?${scopeParam()}`)).count;
      }
    } catch {}
  }
  queueMicrotask(async () => {
    await loadSettings().then((s) => s.me && (Alpine.store("app").me = s.me)).catch(() => {});
    await loadLibrarySchema();  // before plugins start: e.g. saved searches label their filters with it
    // plugins get ready before anything is shown, e.g. accounts: the login; libraries: lock them on a
    // new tab, pick one
    await emitPlugins("start");
    if (!Alpine.store("app").lib.plugin) await loadLibrarySchema();  // e.g. only now logged in (accounts)
    api("/api/providers").then((p) => (Alpine.store("app").providers = p)).catch(() => {});
    route();
    refreshAttentionCount();
    setInterval(refreshAttentionCount, 60000);
    refreshUpdate();
    setInterval(refreshUpdate, 3600000);  // the server asks GitHub once a day; this only reads its answer
  });
  window.refreshAttentionCount = refreshAttentionCount;
});

/* ---------------- Android app (android/) ----------------
   In the browser: a banner and Settings → Android app offer the app. In the app: the app sends the
   volume and back keys here (window.MangaVibeApp), and Settings → Android app changes the server. */

const ANDROID_PACKAGE = "io.github.ivokolarov0.mangavibe";

/* the APK attached to the newest release (built by .github/workflows/android.yml) */
function apkUrl() {
  const repo = Alpine.store("app").update?.repo || "ivokolarov0/manga-vibe";
  return `https://github.com/${repo}/releases/latest/download/MangaVibe.apk`;
}

/* Chrome opens the app at this page if it's installed (which also connects it to this server);
   if not, it downloads the APK instead */
function androidAppLink(url) {
  const enc = encodeURIComponent;
  return `intent://open#Intent;scheme=mangavibe;package=${ANDROID_PACKAGE};`
    + `S.url=${enc(url)};S.browser_fallback_url=${enc(apkUrl())};end`;
}

function openAndroidApp(url = location.href) { location.href = androidAppLink(url); }

function appBannerDismissed() {
  try { return localStorage.getItem("app-banner") === "dismissed"; } catch { return false; }
}

function dismissAppBanner() {
  try { localStorage.setItem("app-banner", "dismissed"); } catch {}
  Alpine.store("app").appBanner = false;
}

window.MangaVibeApp = {
  volumeKey: (dir) => Reader.volumeKey(dir),
  /* the app's text-to-speech finished reading what the reader gave it (read aloud) */
  spoke: (run) => Reader.spoke(run),
  /* …and started part i of it (so a change of speed can carry on from there) */
  speaking: (run, i) => Reader.speaking(run, i),
  /* the back button: true if the page handled it, false lets the app go back in history (or close) */
  back() {
    if (Reader.isOpen) return Reader.back();
    // every sheet and dialog closes on Escape
    const open = [...document.querySelectorAll(".modal, .sheet")].some((el) => el.getClientRects().length);
    if (open) dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    return open;
  },
};

/* ---------------- updates (toast + Settings → Updates) ---------------- */

async function refreshUpdate() {
  try { Alpine.store("app").update = await api("/api/update"); } catch {}
}

/* The version is older than the database (updater.NeedsDbChoice): put back the copy made before
   newer versions changed it, keep the newer database, or cancel. Returns "restore", "keep" or null. */
function askDatabase(d, label) {
  const older = t("{version} is older than your database: newer versions have changed it since.", { version: label });
  if (d.backup) {
    const when = d.backup.date ? fmtDateTime(d.backup.date) : t("an earlier update");
    if (confirm(older + "\n\n" + t("Put back the copy of the database made on {when}? Reading progress, settings and other changes made since then are lost.", { when })
                + "\n\n" + t("Cancel to see the other choice."))) return "restore";
  }
  const keep = t("Keep the newer database? {version} may not understand what newer versions added, and parts of it can break. Updating again fixes that.", { version: label });
  if (confirm(d.backup ? keep : older + "\n\n" + keep)) return "keep";
  return null;
}

/* Install a version (how.rollback: an earlier one), follow its progress, then reload the page when
   the new version answers. */
async function installUpdate(sha, how = {}) {
  const app = Alpine.store("app");
  const ask = (d) => {
    const db = askDatabase(d, how.label || t("This version"));
    if (db) return installUpdate(sha, { ...how, db });
    app.updating = null;
    toast(t("Nothing was changed"));
    refreshUpdate();
  };
  app.updating = { state: "installing", message: t("Starting…"), error: null };
  app.updatingBack = !!how.rollback;
  try {
    await api(how.rollback ? "/api/update/rollback" : "/api/update/install",
              { method: "POST", body: { sha, db: how.db || null } });
  } catch (e) {
    if (e.status === 409 && e.detail?.needs_db) return ask(e.detail.needs_db);
    app.updating = { state: "error", message: "", error: e.message };
    return;
  }
  let restarted = false;
  for (let i = 0; i < 900; i++) {  // pip can take a few minutes on a NAS
    await new Promise((r) => setTimeout(r, 1500));
    let u = null;
    try { u = await api("/api/update"); } catch { restarted = true; continue; }  // down while restarting
    if (u.current.sha === sha) { location.reload(); return; }
    if (u.status.state === "error") {
      if (u.status.needs_db && !how.db) return ask(u.status.needs_db);  // known once it was downloaded
      app.updating = { state: "error", message: "", error: u.status.error };
      app.update = u;
      return;
    }
    if (restarted && u.status.state === "idle") break;  // came back on the old version
    app.updating = { state: u.status.state, message: u.status.message, error: null };
  }
  app.updating = { state: "error", message: "",
    error: t("The new version did not start. MangaVibe went back to the previous one; see the container log.") };
  refreshUpdate();
}

const DISMISS_KEY = "update_dismissed";  // "sha|time": "Later" hides the toast for a day

function updateToast() {
  return {
    dismissed: (() => { try { return localStorage.getItem(DISMISS_KEY) || ""; } catch { return ""; } })(),
    get a() { return this.$store.app.update?.available; },
    get busy() { return this.$store.app.updating; },
    get show() {
      if (this.busy) return true;
      const u = this.$store.app.update, a = this.a;
      if (!u?.supported || !a || a.skipped || a.failed || settings.auto_update === false) return false;
      const app = this.$store.app;
      if (app.view === "read" || (app.view === "settings" && app.sub === "updates")) return false;  // same buttons there
      const [sha, at] = this.dismissed.split("|");
      return !(sha === a.sha && Date.now() - +at < 86400000);
    },
    get when() { return this.a?.date ? fmtDate(this.a.date) : ""; },
    later() {
      this.dismissed = `${this.a.sha}|${Date.now()}`;
      try { localStorage.setItem(DISMISS_KEY, this.dismissed); } catch {}
    },
    async skip() {
      this.$store.app.update = await api("/api/update/skip", { method: "POST", body: { sha: this.a.sha } });
      toast(t("This version is skipped. You'll be told about the next one."), 5000);
    },
    install() { installUpdate(this.a.sha); },
    close() { this.$store.app.updating = null; },
  };
}

/* ---------------- what plugins add to every list ----------------
   e.g. the libraries plugin: shelf=3 for the library picked with its tabs (MangaVibe.setListScope). */

// "shelf=3&" for the list API calls; "" when no plugin adds anything
function scopeParam() {
  const p = new URLSearchParams(scopeEntries());
  return p.toString() ? `${p}&` : "";
}

function scopeEntries() {
  return Object.values(Alpine.store("app").listScope).flatMap((o) => Object.entries(o))
    .filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [k, String(v)]);
}

/* ---------------- top bar ---------------- */

function topbar() {
  return {
    q: libParams().get("q") || "",
    timer: null,
    init() {
      this.$watch("$store.app.query", () => {
        if (document.activeElement !== this.$refs.search) this.q = libParams().get("q") || "";
      });
    },
    search() {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => {
        const q = this.q.trim();
        if (this.$store.app.view !== "library") return (location.hash = "#/" + (q ? "?q=" + encodeURIComponent(q) : ""));
        const p = libParams();
        q ? p.set("q", q) : p.delete("q");
        navigateLibrary(p);
      }, 300);
    },
  };
}

/* ---------------- library grid ---------------- */

// Visible page numbers with gaps collapsed, e.g. 1 … 4 5 6 … 20 ("…" entries are gaps).
function pageList(page, pages) {
  const out = [];
  for (let n = 1; n <= pages; n++) {
    if (n === 1 || n === pages || Math.abs(n - page) <= 1 || (page <= 3 && n <= 4) || (page >= pages - 2 && n >= pages - 3)) out.push(n);
    else if (out[out.length - 1] !== "…") out.push("…");
  }
  return out;
}

/* Watch a reactive value outside a component: fn(new, old) after it changes (not at first). */
function watchValue(get, fn) {
  let first = true, old;
  Alpine.effect(() => {
    const v = get();
    if (first) { first = false; old = v; return; }
    if (v === old) return;
    const was = old;
    old = v;
    queueMicrotask(() => fn(v, was));  // outside the effect: what fn reads isn't tracked
  });
}

/* MangaVibe.library (Plugin API 19, issue #207): the library page's cards, filters, paging and
   multi-select, for the view that draws it (the view:library plugin's; built in:
   app/plugins/builtin/library_page). An Alpine store ($store.library). It loads what the address and the
   plugins' list scope say by itself; a view draws `items` and calls these. */
function libraryController() {
  return {
    items: [], cursor: 0, done: false, loading: false, total: null, key: null, stale: false,
    paged: false, page: 1, size: 60, base: null,
    failed: "",  // why the last load didn't come (shown with "Try again"); "" when it did
    layout: "grid",  // grid (poster cards) | list (one series per row): the library_view setting
    end: null,       // the view's element at the end of the cards: near it, more are loaded (watchEnd)
    get quick() { return quickButtons(); },
    get lib() { return Alpine.store("app").lib; },
    init() {
      const app = () => Alpine.store("app");
      // reload when the filters (URL) change; keep cards + scroll position otherwise
      watchValue(() => app().query, () => { this.endSelect(); this.sync(); });
      watchValue(() => app().view, (v) => {
        if (v !== "library") this.endSelect();
        this.sync();
        this.refresh();
      });
      watchValue(() => app().libraryDirty, () => { this.key = null; this.sync(); });
      // a plugin changed what the lists show (e.g. another library, or one was unlocked)
      watchValue(() => app().listKey, () => { this.endSelect(); this.sync(); });
      // after reading: update the cards in place (now, or when the library is next shown)
      watchValue(() => app().progressDirty, () => { this.stale = true; this.refresh(); });
      queueMicrotask(() => this.sync());
    },
    // the view's element after the last card: more are loaded when it comes near the screen
    watchEnd(el) {
      this.end = el;
      new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && Alpine.store("app").view === "library" && !this.paged) this.loadMore();
      }, { rootMargin: "800px" }).observe(el);
    },
    // the filters without the page number, and what plugins add (e.g. the library picked): what the API
    // gets besides cursor/limit
    get filters() {
      const p = new URLSearchParams(Alpine.store("app").query);
      p.delete("page");
      for (const [k, v] of scopeEntries()) p.set(k, v);
      return p.toString();
    },
    sync() {
      if (Alpine.store("app").view !== "library") return;
      document.title = "MangaVibe";
      const paged = settings.library_listing === "paged";
      const page = paged ? Math.max(1, parseInt(new URLSearchParams(Alpine.store("app").query).get("page")) || 1) : 1;
      this.layout = settings.library_view === "list" ? "list" : "grid";
      // with a fixed column count, a page is whole rows (80 in 6 columns: 84), so the grid's last row is full
      // (the same in the list view, which switches without reloading)
      const pageSize = +settings.library_page_size || 80, cols = +settings.grid_columns || 0;
      const size = cols ? Math.ceil(pageSize / cols) * cols : pageSize;
      // listsDirty: a plugin said the same filters now show something else (e.g. a library was unlocked)
      const base = `${paged ? "paged" : "lazy"}:${size}:${this.filters}:${Alpine.store("app").listsDirty}`;
      const key = `${base}:${page}`;
      if (key === this.key) return;
      const pageChange = paged && base === this.base && this.key !== null;  // same list, other page
      Object.assign(this, { cursor: 0, done: false, loading: false, failed: "", key, base, paged, page, size, pageSize });
      if (!pageChange) Object.assign(this, { items: [], total: null });  // a new page keeps the old cards until it arrives
      paged ? this.loadPage(pageChange) : this.loadMore();
    },
    async loadPage(scroll) {
      const key = this.key;
      this.loading = true;
      this.failed = "";
      try {
        const data = await api(`/api/series?cursor=${(this.page - 1) * this.size}&limit=${this.size}&${this.filters}`,
          { timeout: LOAD_TIMEOUT });
        if (key !== this.key) return;
        if (!data.items.length && data.total && this.page > 1) return this.goPage(this.pages(data.total), true);  // past the end
        Object.assign(this, { items: data.items, total: data.total, done: true });
      } catch (e) {
        if (key === this.key) this.failed = e.message;
      } finally {
        if (key === this.key) this.loading = false;
      }
      const view = document.getElementById("library-view");  // the page's section around the view
      if (scroll && view) {
        await Alpine.nextTick();
        const top = view.getBoundingClientRect().top + scrollY - 70;  // clear the top bar
        if (scrollY > top) scrollTo({ top: Math.max(0, top) });
      }
    },
    /* reload the cards already shown without clearing them, so the scroll position stays */
    async refresh() {
      if (!this.stale || Alpine.store("app").view !== "library" || this.key === null) return;
      this.stale = false;
      if (this.paged) return this.loadPage(false);
      const key = this.key, want = Math.max(this.items.length, 60), items = [];
      this.loading = true;  // keeps loadMore from appending meanwhile
      try {
        let cursor = 0, data;
        do {
          data = await api(`/api/series?cursor=${cursor}&limit=${Math.min(200, want - items.length)}&${this.filters}`);
          if (key !== this.key) return;
          items.push(...data.items);
          cursor = data.next_cursor;
        } while (cursor !== null && items.length < want);
        Object.assign(this, { items, cursor: cursor ?? items.length, done: cursor === null, total: data.total });
      } catch {
        // keep the old cards; the next change reloads anyway
      } finally {
        if (key === this.key) this.loading = false;
      }
    },
    pages(total = this.total) { return Math.max(1, Math.ceil((total || 0) / this.size)); },
    get pageNums() { return pageList(this.page, this.pages()); },
    get pageInfo() {
      return t("Page {page} of {pages}", { page: this.page, pages: this.pages() }) + " · " + tn("{n} series", "{n} series", this.total);
    },
    pageSizes: PAGE_SIZES,
    pageSize: 80,  // the size picked; size is what a page holds (rounded to whole rows)
    async setPageSize(v) {  // the same setting as Settings → Display → Series per page
      await api("/api/settings", { method: "PUT", body: { library_page_size: +v } });
      await loadSettings();
      const p = libParams();
      if (p.has("page")) navigateLibrary(p);  // back to page 1; the query change reloads
      else { this.key = null; this.sync(); }
    },
    async setLayout(v) {  // the same cards, so nothing reloads
      if (v === this.layout) return;
      this.layout = v;
      try {
        await api("/api/settings", { method: "PUT", body: { library_view: v } });
        await loadSettings();
      } catch (e) {
        toast(t("Could not save the view: {error}", { error: e.message }), 3000, "error");
      }
    },
    goPage(n, replace = false) {
      n = Math.min(Math.max(1, n), this.pages());
      const p = libParams();
      n > 1 ? p.set("page", n) : p.delete("page");
      const url = "#/" + (p.toString() ? "?" + p : "");
      replace ? location.replace(url) : (location.hash = url);  // a new history entry, so Back returns to the last page
    },
    /* "Try again" after a load failed or timed out */
    retry() {
      this.failed = "";
      this.paged ? this.loadPage(false) : this.loadMore();
    },
    async loadMore() {
      // after a failure only "Try again" loads more (else scrolling would retry it over and over)
      if (this.paged || this.loading || this.done || this.failed || this.key === null) return;
      this.loading = true;
      const key = this.key;
      try {
        const data = await api(`/api/series?cursor=${this.cursor}&limit=60&${this.filters}`, { timeout: LOAD_TIMEOUT });
        if (key !== this.key) return;  // filters changed while loading
        this.items.push(...data.items);
        this.cursor = data.next_cursor ?? this.cursor;
        this.done = data.next_cursor === null;
        this.total = data.total;
      } catch (e) {
        if (key === this.key) this.failed = e.message;
      } finally {
        if (key === this.key) this.loading = false;
      }
      await Alpine.nextTick();
      if (!this.done && !this.failed && key === this.key && this.end?.getBoundingClientRect().top < innerHeight + 600) this.loadMore();
    },
    get params() { return new URLSearchParams(Alpine.store("app").query); },
    quickOn(v) {
      const p = this.params, read = getList(p, "read");
      if (v === "fav") return p.get("fav") === "1";
      if (v === "") return !read.length && p.get("fav") !== "1";
      return read.length === 1 && read[0] === v;
    },
    setQuick(v) {
      const p = libParams();
      if (v === "fav") p.get("fav") === "1" ? p.delete("fav") : p.set("fav", "1");
      else if (v === "") { p.delete("read"); p.delete("fav"); }
      else p.set("read", v);
      navigateLibrary(p);
    },
    // toolbar: Filter (with count), Sort, Letter
    get filterCount() {
      const p = this.params;
      return lib().listKeys.reduce((a, k) => a + getList(p, k).length, 0) + (lib().fav && p.get("fav") ? 1 : 0)
        + lib().lengthUnits.filter(([u]) => lengthLabel(p, u)).length;
    },
    get customSort() { return !!this.params.get("sort"); },
    get letter() { return this.params.get("letter") || ""; },
    openSheet(kind) { Alpine.store("app").sheet = kind; },  // the library-query plugin's sheets: filter, sort, letter
    get chips() {
      const p = this.params, out = [], read = getList(p, "read");
      for (const [k, , labels] of lib().groups) {
        if (k === "read" && read.length < 2) continue;  // a single reading status shows as a quick button
        for (const v of getList(p, k)) out.push({ k, v, label: labels[v] || v });
      }
      if (lib().tags) {
        for (const v of getList(p, "genre")) out.push({ k: "genre", v, label: v });
        for (const v of getList(p, "exclude")) out.push({ k: "exclude", v, label: t("not {tag}", { tag: v }), ex: true });
      }
      for (const [k, title] of lib().names) {
        for (const v of getList(p, k)) out.push({ k, v, label: `${title}: ${v}` });
      }
      for (const [u] of lib().lengthUnits) {
        const label = lengthLabel(p, u);
        if (label) out.push({ k: "length", v: u, label: p.get("length_unknown") ? t("{length} or unknown", { length: label }) : label });
      }
      if (p.get("letter")) out.push({ k: "letter", v: p.get("letter"), label: t("Starts with {letter}", { letter: p.get("letter") }) });
      const sort = p.get("sort");
      if (sort && sort !== lib().defaultSort) out.push({ k: "sort", v: sort,
        label: t("Sort: {sort}", { sort: lib().sortInfo[sort]?.label || sort }) + (p.get("order") === "asc" ? " ↑" : "") });
      return out;
    },
    removeChip(c) {
      const p = libParams();
      if (c.k === "sort") { p.delete("sort"); p.delete("order"); }
      else if (c.k === "letter") p.delete("letter");
      else if (c.k === "length") {
        p.delete(`${c.v}_min`); p.delete(`${c.v}_max`);
        if (!lib().lengthUnits.some(([u]) => lengthLabel(p, u))) p.delete("length_unknown");
      } else setList(p, c.k, getList(p, c.k).filter((x) => x !== c.v));
      navigateLibrary(p);
    },
    clearAll() {
      const q = this.params.get("q");
      navigateLibrary(new URLSearchParams(q ? { q } : {}));
    },

    // any filter or sort on (a plugin can add to their row, e.g. Save search)
    get filtersOn() { return !!searchQuery(this.params); },
    get emptyText() {
      if (this.params.toString()) return t("No series match these filters.");
      // a plugin may know why (e.g. libraries: this one is locked, or nothing was moved here yet)
      return collectPlugins("empty-library")[0] || t("No series yet. The first scan may still be running; see Settings.");
    },

    /* ---- multi-select: hold a card to start, then tap or shift-click to pick more ---- */
    selecting: false,
    sel: {},          // series id -> true
    anchor: null,     // the last card tapped: shift-click picks everything between it and the next
    pressTimer: null, pressAt: null, pressed: false,
    selMenu: false, delOpen: false, working: "",
    get selCount() { return Object.keys(this.sel).length; },
    isSel(s) { return !!this.sel[s.id]; },
    press(e, s) {
      if (e.button > 0 || this.selecting) return;
      this.pressAt = [e.clientX, e.clientY];
      clearTimeout(this.pressTimer);
      this.pressTimer = setTimeout(() => this.startSelect(s), 600);  // held without moving
    },
    startSelect(s) {
      this.pressCancel();
      this.pressed = true;  // the click that ends this hold must not open the series
      this.selecting = true;
      this.sel = { [s.id]: true };
      this.anchor = s.id;
      navigator.vibrate?.(30);
    },
    // a long touch on a phone opens the browser's link menu instead (and cancels the hold): use it
    pressMenu(e, s) {
      if (this.selecting) return e.preventDefault();
      if (!this.pressAt) return;  // a right-click: the normal menu
      e.preventDefault();
      if (e.pointerType !== "mouse") this.startSelect(s);
    },
    pressMove(e) {
      if (this.pressAt && Math.hypot(e.clientX - this.pressAt[0], e.clientY - this.pressAt[1]) > 10) this.pressCancel();
    },
    pressCancel() { clearTimeout(this.pressTimer); this.pressAt = null; },
    // after the hold: its click (if the browser sends one) comes right after pointerup
    pressEnd() {
      this.pressCancel();
      if (this.pressed) setTimeout(() => { this.pressed = false; }, 300);
    },
    cardClick(e, s) {
      this.pressCancel();
      if (this.pressed) { this.pressed = false; e.preventDefault(); return; }  // the hold that started it
      if (!this.selecting) return;  // a normal click opens the series
      e.preventDefault();
      if (e.shiftKey && this.anchor !== null) {
        const ids = this.items.map((x) => x.id);
        const [a, b] = [ids.indexOf(this.anchor), ids.indexOf(s.id)].sort((x, y) => x - y);
        if (a >= 0) {
          const sel = { ...this.sel };
          for (const id of ids.slice(a, b + 1)) sel[id] = true;
          this.sel = sel;
          this.anchor = s.id;
          return;
        }
      }
      const sel = { ...this.sel };
      if (sel[s.id]) delete sel[s.id]; else sel[s.id] = true;
      this.sel = sel;
      this.anchor = s.id;
      if (!this.selCount) this.endSelect();
    },
    endSelect() {
      if (this.working) return;
      Object.assign(this, { selecting: false, sel: {}, anchor: null, selMenu: false, delOpen: false });
    },
    async selectAll() {  // every series matching the filters, also the ones not loaded yet
      this.selMenu = false;
      try {
        const { ids } = await api(`/api/series?ids_only=true&${this.filters}`);
        this.sel = Object.fromEntries(ids.map((id) => [id, true]));
      } catch (e) { toast(e.message, 6000, "error"); }
    },
    // one action for the selected series. Plugin markup in the select-menu slot can use it with its own
    // address: it gets { ids, action, ...extra } and answers { done, skipped } (e.g. libraries' Move)
    async bulk(action, msg, extra = {}, url = "/api/series/bulk") {
      this.selMenu = false;
      this.working = t("Working…");
      try {
        const r = await api(url, { method: "POST", body: { ids: Object.keys(this.sel).map(Number), action, ...extra } });
        toast(msg(r.done) + (r.skipped && action === "refresh" ? " " + t("({n} without metadata skipped)", { n: r.skipped }) : ""));
        this.working = "";
        this.endSelect();
        Alpine.store("app").libraryDirty++;
      } catch (e) { toast(e.message, 6000, "error"); this.working = ""; }
    },
    // the titles the delete dialog names (only loaded cards are known here)
    get selTitles() { return this.items.filter((s) => this.sel[s.id]).map((s) => s.title); },
    async deleteSelected() {
      const ids = Object.keys(this.sel).map(Number);
      let done = 0;
      const failed = [];
      this.delOpen = false;
      for (const [i, id] of ids.entries()) {
        this.working = t("Deleting {done} of {total}…", { done: i + 1, total: ids.length });
        try {
          await api(`/api/series/${id}`, { method: "DELETE" });
          done++;
          const sel = { ...this.sel };
          delete sel[id];
          this.sel = sel;
        } catch (e) {
          failed.push(e.message);
          // a read-only folder or no permission: the rest would fail the same way
          if (/read-only|can't delete files/i.test(e.message)) break;
        }
      }
      this.working = "";
      if (failed.length) toast(t("Deleted {done} of {total}. {error}", { done, total: ids.length, error: failed[0] }), 10000, "error");
      else toast(tn("Deleted {n} series", "Deleted {n} series", done));
      if (!failed.length) this.endSelect();
      Alpine.store("app").libraryDirty++;
      refreshAttentionCount();
    },
  };
}

/* ---------------- filter sheet ---------------- */

function filterSheet() {
  return {
    tags: [], tagQuery: "",
    get lib() { return this.$store.app.lib; },
    get groups() { return this.lib.groups; },
    open: { read: true },  // which groups are unfolded
    init() {
      this.$watch("$store.app.sheet", async (sheet) => {
        if (sheet !== "filter") return;
        // unfold the groups that have something selected, so nothing chosen is hidden
        for (const [k] of this.groups) if (this.selected(k)) this.open[k] = true;
        if (this.selected("length")) this.open.length = true;
        for (const [k] of this.lib.names) if (this.selected(k)) this.open[k] = true;
        this.loadNames();
        this.tags = await api(`/api/tags?${scopeParam()}`).catch(() => this.tags);
        for (const k of ["genres", "tags"]) if (this.selected(k)) this.open[k] = true;
      });
    },
    // the names filters' groups (Author): the names their source lists, the chosen ones first
    nameLists: {}, nameQuery: {},
    async loadNames() {
      for (const [k, , source] of this.lib.names) {
        if (!source) continue;
        const sep = source.includes("?") ? "&" : "?";
        this.nameLists[k] = await api(`${source}${sep}${scopeParam()}`).catch(() => this.nameLists[k] || []);
      }
    },
    shownNames(k) {
      const q = (this.nameQuery[k] || "").toLowerCase(), on = getList(this.p, k);
      const all = this.nameLists[k] || [], known = new Set(all.map((n) => n.name));
      const chosen = [...on.filter((v) => !known.has(v)).map((name) => ({ name, count: 0 })),
                      ...all.filter((n) => on.includes(n.name))];
      const rest = all.filter((n) => !on.includes(n.name) && n.name.toLowerCase().includes(q))
        .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)).slice(0, 60);
      return [...chosen, ...rest];
    },
    toggleGroup(k) { this.open[k] = !this.open[k]; },
    // how many options of a group are on (include and exclude both count)
    selected(k) {
      if (k === "length") return this.lib.lengthUnits.filter(([u]) => lengthLabel(this.p, u)).length;
      if (k !== "genres" && k !== "tags") return getList(this.p, k).length;
      const names = new Set(this.tags.filter((t) => (t.kind === "genre") === (k === "genres")).map((t) => t.name));
      return [...getList(this.p, "genre"), ...getList(this.p, "exclude")].filter((n) => names.has(n)).length;
    },
    get p() { return new URLSearchParams(this.$store.app.query); },
    get genres() { return this.tags.filter((t) => t.kind === "genre"); },
    get otherTags() {
      const q = this.tagQuery.toLowerCase();
      return this.tags.filter((t) => t.kind !== "genre" && t.name.toLowerCase().includes(q))
        .sort((a, b) => b.count - a.count).slice(0, 60);
    },
    isOn(k, v) { return getList(this.p, k).includes(v); },
    tagState(name) { return this.isOn("genre", name) ? "on" : this.isOn("exclude", name) ? "ex" : ""; },
    apply(fn) { const p = libParams(); fn(p); navigateLibrary(p); },
    toggle(k, v) {
      this.apply((p) => { const cur = getList(p, k); setList(p, k, cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]); });
    },
    clearAll() {
      // every filter but the search box, and not the sort
      this.apply((p) => { for (const k of this.lib.params) if (k !== "q" && k !== "sort" && k !== "order") p.delete(k); });
    },
    // length range inputs: a whole number of at least 0, or empty for no bound
    get lengthUnits() { return this.lib.lengthUnits; },
    bound(k) { return this.p.get(k) || ""; },
    setBound(k, value) {
      const n = String(value).trim() === "" ? NaN : Math.floor(Number(value));
      this.apply((p) => {
        Number.isFinite(n) && n >= 0 ? p.set(k, n) : p.delete(k);
        if (!this.lib.lengthUnits.some(([u]) => lengthLabel(p, u))) p.delete("length_unknown");
      });
    },
    get lengthUnknown() { return this.p.get("length_unknown") === "true"; },
    setLengthUnknown(on) { this.apply((p) => (on ? p.set("length_unknown", "true") : p.delete("length_unknown"))); },
    cycleTag(t) {  // include -> exclude -> off
      this.apply((p) => {
        const inc = getList(p, "genre"), exc = getList(p, "exclude");
        if (inc.includes(t)) { setList(p, "genre", inc.filter((x) => x !== t)); setList(p, "exclude", [...exc, t]); }
        else if (exc.includes(t)) setList(p, "exclude", exc.filter((x) => x !== t));
        else setList(p, "genre", [...inc, t]);
      });
    },
  };
}

/* ---------------- first-letter sheet ---------------- */

function letterSheet() {
  return {
    letters: LETTERS,
    get letter() { return new URLSearchParams(this.$store.app.query).get("letter") || ""; },
    pick(l) {  // tap the selected letter again to clear it; like a menu: pick and close
      const p = libParams();
      !l || p.get("letter") === l ? p.delete("letter") : p.set("letter", l);
      navigateLibrary(p);
      this.$store.app.sheet = null;
    },
  };
}

/* ---------------- sort sheet ---------------- */

function sortSheet() {
  return {
    // the library-query plugin's sorts: each says the order it starts in and its order buttons' labels
    get sorts() { return this.$store.app.lib.sorts; },
    get info() { return this.$store.app.lib.sortInfo[this.sort] || {}; },
    get p() { return new URLSearchParams(this.$store.app.query); },
    get sort() { return this.p.get("sort") || this.$store.app.lib.defaultSort; },
    get asc() { return this.p.get("order") === "asc" || (!this.p.get("order") && this.info.order === "asc"); },
    get orderLabels() { return this.info.order_labels || [t("Ascending"), t("Descending")]; },
    setSort(v) {
      const p = libParams();
      v === this.$store.app.lib.defaultSort ? p.delete("sort") : p.set("sort", v);
      p.delete("order");
      navigateLibrary(p);
      this.$store.app.sheet = null;  // like a menu: pick and close
    },
    setOrder(asc) {
      const p = libParams();
      p.set("order", asc ? "asc" : "desc");
      navigateLibrary(p);
    },
  };
}

/* ---------------- the library page's filters, as plugins see them ----------------
   e.g. the built-in saved searches plugin (app/plugins/builtin/saved_searches/) keeps them under a name. */

// the filters and sort of a query (no page), always in the same order, so two can be compared. Its
// parameters are the library-query plugin's (lib().params)
function searchQuery(p) {
  return [...p].filter(([k, v]) => lib().params.includes(k) && v)
    .sort(([a, x], [b, y]) => a.localeCompare(b) || x.localeCompare(y))
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
}

// what a query's filters do, one short label each: ["Isekai", "not Horror", "Reading", "↓ Recently added"]
function searchParts(qs) {
  const p = new URLSearchParams(qs || ""), out = [];
  if (p.get("q")) out.push(`“${p.get("q")}”`);
  for (const v of getList(p, "genre")) out.push(v);
  for (const v of getList(p, "exclude")) out.push(t("not {tag}", { tag: v }));
  for (const [k] of lib().names) for (const v of getList(p, k)) out.push(v);
  for (const [k, , labels] of lib().groups) for (const v of getList(p, k)) out.push(labels[v] || v);
  for (const [u] of lib().lengthUnits) if (lengthLabel(p, u)) out.push(lengthLabel(p, u));
  if (p.get("fav")) out.push(t("Favorites"));
  if (p.get("letter")) out.push(t("Starts with {letter}", { letter: p.get("letter") }));
  const sort = lib().sortInfo[p.get("sort")]?.label;
  if (sort) out.push("↓ " + sort);
  return out;
}

/* What plugin scripts may use (Plugin API 7, docs/PLUGINS.md). Plugin scripts run after this file:
   before Alpine starts when the page loads, or later when the plugin is switched on. Either way they
   register their stores and components in MangaVibe.onStart(). Keep to these names: everything else
   in app.js can change in any version. */
let alpineStarted = false;
const pluginHandlers = [];         // [plugin id, event, fn] (MangaVibe.on)
const stoppedPlugins = new Set();  // switched off since the page loaded: their handlers don't run
const screenCommands = new Map();  // "<plugin id>\n<name>" -> { plugin, name, spec, fn } (MangaVibe.screenCommand)
const bookReaders = new Map();     // "<plugin id>\n<format>" -> reader (MangaVibe.registerReader)
// the reader.js side: the reader a switched-on plugin registered for this format ("images": the
// reader:images job), or undefined. evenOff: the built-in fallback, switched off or not
window.pluginReader = (pluginId, format, evenOff = false) =>
  stoppedPlugins.has(pluginId) && !evenOff ? undefined : bookReaders.get(`${pluginId}\n${format}`);

window.MangaVibe = {
  // run fn when Alpine starts (Alpine.store, Alpine.data), or now if it already has
  onStart(fn) { alpineStarted ? fn() : document.addEventListener("alpine:init", fn); },
  api,             // api(path, { method, body }) -> the JSON answer; throws Error(detail) on 4xx/5xx
  toast,           // toast(text, ms = 3000, kind = "" | "error")
  // the page's language (Plugin API 27, #263): t(text, values) and tn(singular, plural, n, values) as in
  // common.js, and dates and numbers written its way. In markup, t() and tn() work as they are
  t, tn, th, lang: LANG, fmtDate, fmtDateTime, fmtTime, fmtNum,
  // what views and plugin markup can use to show things as the app does (Plugin API 17), e.g. a card of
  // your own: x-text="MangaVibe.ui.cardSub(s)"
  ui: {
    cardSub,       // a card's line under the title: "Reading · 3/10", "Unread · 4 books", "Completed"
    updated: (s) => (s.updated_at ? t("Updated {when}", { when: ago(s.updated_at * 1000) }) : ""),  // a library card's last change
    ago,           // ago(ms): "today", "3 days ago", "2 months ago"
    status: (st) => STATUS_LABELS[st] || st,   // reading_status -> "Plan to read"
    publication: (p) => PUB_LABELS[p] || p,     // publication_status -> "Releasing"
    kind: kindOf,  // a series' kind: "Manga", "Manhwa", "Novel", "One-shot"
    scoreCls,      // a score's class: "hi" (90+), "mid" (70+), "lo"
    haveText,      // a collection: "8 / 8 volumes · complete"
    editionText,   // an edition: "Kanzenban · 18 volumes (regular: 27)"
    icon,          // icon("star") -> the icon's <svg> as HTML (for x-html)
    libraryUrl,    // libraryUrl({ genre: "Isekai" }) -> "#/?genre=Isekai"
    fileSize: (n) => {  // bytes -> "1.2 GB", "340.5 MB" (in the page's language: "1,2 GB")
      const one = (x) => fmtNum(x, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
      return n >= 1073741824 ? one(n / 1073741824) + " GB" : n >= 1048576 ? one(n / 1048576) + " MB" : fmtNum(Math.ceil(n / 1024)) + " KB";
    },
    number: (v) => (v === null || v === undefined ? "–" : v),  // a volume or chapter number, "–" when none
    text: formatText,  // a description as HTML (for x-html): paragraphs, line breaks, links; escaped
  },
  // the series page's data and actions (Plugin API 18): a view:series plugin draws the page from it
  get series() { return Alpine.store("series"); },
  // the library page's and Continue reading's (Plugin API 19): view:library and view:continue draw from them
  get library() { return Alpine.store("library"); },
  get continue() { return Alpine.store("continue"); },
  // a component whose names are a store's (read, write, call), e.g. Alpine.data("myLibrary", () =>
  // MangaVibe.bind(MangaVibe.library, { extra: 1 })): markup can say `items`, `bulk(...)`
  bind(store, own = {}) {
    const out = { ...own };
    const names = new Set();
    for (let o = Alpine.raw ? Alpine.raw(store) : store; o && o !== Object.prototype; o = Object.getPrototypeOf(o)) {
      Object.getOwnPropertyNames(o).forEach((n) => names.add(n));
    }
    for (const name of names) {
      if (name in out || name === "init" || name === "constructor") continue;
      Object.defineProperty(out, name, {
        get: () => (typeof store[name] === "function" ? store[name].bind(store) : store[name]),
        set: (v) => { store[name] = v; },
        enumerable: true, configurable: true,
      });
    }
    return out;
  },
  // the library page's query (filters, sort, page) as URLSearchParams; reactive inside Alpine
  libraryFilters: () => new URLSearchParams(Alpine.store("app").query),
  filterQuery: searchQuery,   // its filters and sort only, in a fixed order: "" when none are on
  filterLabels: searchParts,  // "genre=Isekai&read=reading" -> ["Isekai", "Reading"]
  showLibrary: navigateLibrary,  // show the library page with these URLSearchParams (back to page 1)
  // listen to the app (docs/PLUGINS.md, "Events"): only while the plugin is on
  on(pluginId, event, fn) { pluginHandlers.push([pluginId, event, fn]); },
  // what this plugin adds to every list's API call (grid, tags, Continue, Needs attention), e.g.
  // { shelf: 3 }; {} for nothing. The lists reload.
  setListScope(pluginId, params) {
    const app = Alpine.store("app");
    app.listScope = { ...app.listScope, [pluginId]: { ...params } };
    window.refreshAttentionCount?.();
  },
  // what the lists show changed on the server (e.g. a library was unlocked): they reload
  listsChanged() {
    Alpine.store("app").listsDirty++;
    window.refreshAttentionCount?.();
  },
  // a page of the plugin's own in Settings (#/settings/<key>), at this place in the menu; its markup
  // goes into the "settings" slot with x-show="section === '<key>'". Add it in the "start" event.
  // personal: everyone's (e.g. their own preferences); otherwise only admins see it
  settingsPage(pluginId, key, label, icon, at = Infinity, { personal = false } = {}) {
    const app = Alpine.store("app"), nav = app.settingsNav.filter(([k]) => k !== key);
    nav.splice(Math.min(at, nav.length), 0, [key, label, icon, pluginId, personal]);
    app.settingsNav = nav;
  },
  // the reader, for a plugin that works on the page being read (Plugin API 12): where() gives
  // { book, series, page, pages } (ids; page from 0), or null while no book is open; redraw() draws
  // the pages' overlays again after the plugin changed what its page_overlay() gives
  // Plugin API 13, for remote and voice control: the rest does what the reader's own buttons do and gives
  // false while no book is open. state() is where() and more (labels, mode, the plugins' buttons and
  // overlays); step(forward) turns a page; goTo(page); openBook("next" | "previous" | id, page);
  // setMode("paged" | "vertical"); options(open): the reading options; overlay(plugin, on);
  // press(plugin, key): a book_actions button; leave(): back out of the reader; refresh(): open it again
  reader: { where: () => Reader.where(), redraw: () => Reader.redraw(), state: () => Reader.state(),
    step: (forward = true) => Reader.step(forward), goTo: (page) => Reader.goTo(page),
    openBook: (which, page = null) => Reader.openBook(which, page), setMode: (mode) => Reader.setMode(mode),
    options: (open = true) => Reader.options(open), overlay: (plugin, on = null) => Reader.overlay(plugin, on),
    press: (plugin, key) => Reader.press(plugin, key), leave: () => Reader.leave(),
    refresh: () => Reader.refresh() },
  // something the page can do for an assistant or voice control (the MCP plugin offers it as a tool
  // named <plugin id>__<name>), Plugin API 13. spec: { description, params: { name: { type,
  // description } }, required: [names] }; fn(args) gives the answer (text, or anything JSON can hold)
  // and may be async; throw to report an error. Only while the plugin is on.
  screenCommand(pluginId, name, spec, fn) {
    screenCommands.set(`${pluginId}\n${name}`, { plugin: pluginId, name, spec: spec || {}, fn });
  },
  screenCommands: () => [...screenCommands.values()].filter((c) => !stoppedPlugins.has(c.plugin)),
  // the reader for books of a format the page reader can't show, e.g. "epub-text" (light novels), for a
  // plugin that lists the format in its `reads` (Plugin API 14). impl: { open(el, book, ctl), close(),
  // and optional step(forward), goTo(page), options(open), back() }. open() fills `el` (the whole screen);
  // `book` is GET /api/books/<id>; ctl: { page, save(page, completed), leave(), openBook(which, page),
  // fullscreen() }. See docs/PLUGINS.md, "A reader of its own".
  registerReader(pluginId, format, impl) { bookReaders.set(`${pluginId}\n${format}`, impl); },
  // the settings changed elsewhere (another device, an assistant): load them again and show them
  async reloadSettings() {
    const s = await loadSettings();
    if (s.me) Alpine.store("app").me = s.me;
    Alpine.store("app").libraryDirty++;
    await Reader.refresh();
    return s;
  },
  // the server refused something (an api() error): a plugin may know what to do, e.g. ask for a
  // library's password and then call retry(). Whether one did.
  async denied(err, retry) {
    if (err?.status !== 403) return false;
    return (await emitPlugins("denied", [err.detail, retry])).length > 0;
  },
};

function liveHandlers(event, only = null) {
  return pluginHandlers.filter(([id, e]) => e === event && !stoppedPlugins.has(id) && (!only || id === only))
    .map(([, , fn]) => fn);
}

// run the plugins' handlers for an event in turn and give their answers (null/undefined/false left
// out). A failing handler is logged and skipped, unless strict (the caller shows the error).
async function emitPlugins(event, args = [], { only = null, strict = false } = {}) {
  const out = [];
  for (const fn of liveHandlers(event, only)) {
    try {
      const r = await fn(...args);
      if (r !== undefined && r !== null && r !== false) out.push(r);
    } catch (e) {
      if (strict) throw e;
      console.error(`A plugin failed in "${event}"`, e);
    }
  }
  return out;
}

// the same for events whose answer is needed at once (getters): handlers mustn't be async
function collectPlugins(event, ...args) {
  const out = [];
  for (const fn of liveHandlers(event)) {
    try {
      const r = fn(...args);
      if (r !== undefined && r !== null && r !== false) out.push(r);
    } catch (e) { console.error(`A plugin failed in "${event}"`, e); }
  }
  return out;
}

/* A plugin switched on or off in Settings: add or take out its markup, script and style in place
   (the page loads with the switched-on ones already in, app/plugins/web.py), and tell it ("start",
   "stop"). Its script stays loaded once it ran; switched off, its handlers don't run. A slot can be in a
   repeated part of the page (e.g. each folder's row in Settings): it's filled in the shown copies and in
   the <template> they're made from. */
function eachRoot(fn, root = document) {
  fn(root);
  for (const t of root.querySelectorAll("template")) eachRoot(fn, t.content);
}

function filledSlots() {
  const out = new Set();
  eachRoot((root) => root.querySelectorAll(".plugin-slot").forEach((s) => s.children.length && out.add(s.dataset.slot)));
  return [...out];
}

// Alpine's own watch for new elements is paused while plugin markup goes in or out: each box is set up
// (or taken down) here, one at a time, so a mistake in one doesn't stop the others
function removeMarkup(id) {
  eachRoot((root) => root.querySelectorAll(".plugin-ui").forEach((box) => {
    if (box.dataset.plugin !== id) return;
    if (root === document) Alpine.destroyTree(box);
    Alpine.mutateDom(() => box.remove());
  }));
}

function addMarkup(id, slot, markup, order = 50) {
  eachRoot((root) => root.querySelectorAll(`.plugin-slot[data-slot="${slot}"]`).forEach((host) => {
    const box = Object.assign(document.createElement("div"), { className: "plugin-ui", innerHTML: markup });
    box.dataset.plugin = id;
    box.dataset.order = order;
    // in its place by order (app/plugins/web.py fill_slots): before the first box that goes after it
    const next = [...host.children].find((b) => b.classList.contains("plugin-ui") && +(b.dataset.order ?? 50) > order);
    Alpine.mutateDom(() => host.insertBefore(box, next || null));
    // in the page: set it up now, with what's around it (e.g. a folder row's l); in a <template>:
    // Alpine sets it up when it makes a copy
    if (root === document) {
      try { Alpine.initTree(box); } catch (e) { console.error(`Plugin ${id}: its ${slot} markup failed`, e); }
    }
  }));
}

async function addPluginUi(id) {
  const ui = await api(`/api/plugins/${encodeURIComponent(id)}/ui`);
  if (ui.css && !document.querySelector(`link[href="${ui.css}"]`)) {
    // before the app's stylesheet: still outside its layers, and the owner's custom.css comes after it
    const link = Object.assign(document.createElement("link"), { rel: "stylesheet", href: ui.css });
    document.querySelector('link[rel="stylesheet"]').before(link);
  }
  if (ui.js && !document.querySelector(`script[src="${ui.js}"]`)) {
    await new Promise((ok, fail) => document.body.append(
      Object.assign(document.createElement("script"), { src: ui.js, onload: ok, onerror: fail })));
  }
  removeMarkup(id);  // never twice
  for (const [slot, markup] of Object.entries(ui.html)) addMarkup(id, slot, markup, ui.order?.[slot] ?? 50);
  Alpine.store("app").pluginSlots = filledSlots();
  stoppedPlugins.delete(id);
  await emitPlugins("start", [], { only: id });
}

async function removePluginUi(id) {
  await emitPlugins("stop", [], { only: id });
  stoppedPlugins.add(id);
  removeMarkup(id);
  const app = Alpine.store("app");
  if (id in app.listScope) {
    const { [id]: _, ...rest } = app.listScope;
    app.listScope = rest;
  }
  app.settingsNav = app.settingsNav.filter((x) => x[3] !== id);
  app.pluginSlots = filledSlots();
  window.refreshAttentionCount?.();
}

/* ---------------- continue reading ---------------- */

/* MangaVibe.continue (Plugin API 19): what's in progress, for the Continue reading view (the view:continue
   plugin's; built in: app/plugins/builtin/continue_page). Loads by itself while that page is shown. */
function continueController() {
  return {
    items: null,  // GET /api/continue-reading: the series in progress, the last read first; null until loaded
    init() {
      const app = () => Alpine.store("app");
      watchValue(() => app().view, (v) => v === "continue" && this.load());
      watchValue(() => app().progressDirty, () => this.load());
      watchValue(() => app().listKey, () => this.load());  // e.g. another library, or one was unlocked
      queueMicrotask(() => this.load());
    },
    async load() {
      if (Alpine.store("app").view !== "continue") return;
      document.title = t("Continue reading");
      this.items = await api(`/api/continue-reading?${scopeParam()}`).catch(() => this.items);
    },
  };
}

/* ---------------- series page ---------------- */

/* MangaVibe.series (Plugin API 18, issue #206): the series page's data and everything it does, for the view
   that draws it (the view:series plugin's; built in: app/plugins/builtin/series_page). An Alpine store, so a
   view reads it reactively: $store.series.s, or MangaVibe.series in a script. It loads the series the route
   shows (#/series/<id>?tab=files) by itself; a view only draws it and calls these. */
function seriesController() {
  return {
    s: null,        // GET /api/series/<id>: the series (docs/PLUGINS.md, "The view:series job")
    books: [],      // GET /api/series/<id>/books (with ?all=1 on the Files tab): its books, in reading order
    tab: "",        // "" (books) or "files"
    error: "",      // shown instead of the page, e.g. when its library is locked
    loads: 0,       // +1 after each load: a view can watch it (e.g. to measure the description)
    loadedFor: null, badgeTimer: null, badgeSame: 0,
    init() {
      // load what the route shows: a series, its tab; again after the reader closed (progressDirty)
      let dirty = null;
      Alpine.effect(() => {
        const app = Alpine.store("app"), view = app.view, id = app.id, tab = app.params.tab || "";
        if (dirty !== app.progressDirty) { if (dirty !== null) this.loadedFor = null; dirty = app.progressDirty; }
        if (view === "series") queueMicrotask(() => this.show(id, tab));  // outside the effect: not tracked
      });
    },
    show(id, tab) {
      if (`${id}|${tab}` !== this.loadedFor) this.load(id, tab);
      else if (!this.badgeTimer) this.watchBadges(true);  // back on the same series: labels may have moved on
    },
    async load(id = Alpine.store("app").id, tab = Alpine.store("app").params.tab || "") {
      this.loadedFor = `${id}|${tab}`;
      this.error = "";
      // another series: drop the old one now, so the page shows "Loading…" instead of it until the new one is here
      if (this.s && String(this.s.id) !== String(id)) Object.assign(this, { s: null, books: [] });
      let s, books;
      try {
        [s, books] = await Promise.all([api(`/api/series/${id}`),
          api(`/api/series/${id}/books${tab === "files" ? "?all=1" : ""}`)]);
      } catch (e) {
        if (this.loadedFor !== `${id}|${tab}`) return;
        Object.assign(this, { s: null, error: e.message });
        MangaVibe.denied(e, () => this.reload());  // e.g. a locked library: its plugin asks for the password
        return;
      }
      if (this.loadedFor !== `${id}|${tab}`) return;
      Object.assign(this, { s, books, tab });
      this.watchBadges(true);
      document.title = s.title;
      this.loads++;
    },
    reload() { this.loadedFor = null; this.load(); },
    /* back to where the series was opened from (the library keeps its place, search and filters);
       opened directly, there's no such page, so go to the library */
    back() {
      const app = Alpine.store("app");
      if (app.seriesBack) history.back();
      else location.replace("#/" + (app.query ? "?" + app.query : ""));
    },
    get backLabel() { return Alpine.store("app").seriesBack ? t("Back") : t("Library"); },
    // the plugins' labels on the books (e.g. "Translated 12/40") stay live while the page is open:
    // asked every 3 s, every 30 s after a minute without a change; a load (e.g. after a plugin
    // button) makes it quick again. Nothing is asked while the tab is hidden or no plugin labels books.
    watchBadges(quick) {
      clearTimeout(this.badgeTimer);
      if (quick) this.badgeSame = 0;
      this.badgeTimer = setTimeout(() => this.pollBadges(), this.badgeSame >= 20 ? 30000 : 3000);
    },
    async pollBadges() {
      const app = Alpine.store("app"), key = this.loadedFor;
      this.badgeTimer = null;
      if (app.view !== "series" || !this.s) return;  // left the page: coming back starts it again
      if (document.hidden) return this.watchBadges();
      const r = await api(`/api/series/${this.s.id}/badges${this.tab === "files" ? "?all=1" : ""}`).catch(() => null);
      if (this.loadedFor !== key || app.view !== "series" || this.badgeTimer) return;  // another load took over
      if (r && !r.live) return;
      let changed = false;
      for (const b of r ? this.books : []) {
        const fresh = r.badges[b.id] || [];
        if (JSON.stringify(fresh) !== JSON.stringify(b.badges || [])) { b.badges = fresh; changed = true; }
      }
      this.badgeSame = changed ? 0 : this.badgeSame + 1;
      this.watchBadges();
    },

    get alt() {
      const t = this.s.titles;
      return [t.english, t.romaji, t.native].filter((x, i, all) => x && x !== this.s.title && all.indexOf(x) === i);
    },
    get facts() {
      const s = this.s;
      return [s.start_year, s.average_score && `★ ${pct(s.average_score)}`, PUB_LABELS[s.publication_status], kindOf(s)].filter(Boolean);
    },
    get details() {
      const s = this.s;
      return [
        [t("Author"), (s.creators || []).map((a) => a.name + (a.roles.length ? ` (${a.roles.join(", ")})` : "")).join("\n")],
        [t("Year"), s.start_year], [t("Type"), kindOf(s)], [t("Status"), PUB_LABELS[s.publication_status]],
        [t("Length"), [s.volumes_total && tn("{n} volume", "{n} volumes", s.volumes_total),
                       s.chapters_total && tn("{n} chapter", "{n} chapters", s.chapters_total)].filter(Boolean).join(" · ")],
        [t("You have"), haveText(s.collection)],
        [t("Edition"), editionText(s.collection)],
        [t("Score"), s.average_score && pct(s.average_score)],
        [t("In library"), tn("{n} book", "{n} books", s.book_count) + " · " + t("{n} unread", { n: s.unread_count })],
        // what plugins add, e.g. libraries: which library it shows in
        ...collectPlugins("series-info", s).flat(),
        ...s.plugin_fields.map((f) => [t(f.label), typeof f.value === "object" ? JSON.stringify(f.value) : f.value]),
        [t("Folder"), s.folder_path],
      ].filter(([, v]) => v);
    },
    get genres() { return this.s.tags.filter((tag) => tag.kind === "genre"); },
    get tagList() { return this.s.tags.filter((tag) => tag.kind !== "genre"); },  // the other tags (spoiler: tag.spoiler)
    get matchLabel() {
      const s = this.s;
      return { auto: t("Matched automatically · {score}", { score: s.match_confidence != null ? pct(s.match_confidence) : "?" }),
        manual: t("Matched by you"), failed: t("No confident match"), ignored: t("Not matched (your choice)"),
        unmatched: t("Looking for metadata…") }[s.match_status] || "";
    },
    tagUrl(name) { return libraryUrl({ genre: name }); },

    async patch(body, reloadAfter = false) {
      await api(`/api/series/${this.s.id}`, { method: "PATCH", body });
      Alpine.store("app").libraryDirty++;
      reloadAfter ? this.reload() : toast(t("Saved"));
    },
    // the reading status picked by hand (reading, completed, plan_to_read, on_hold, dropped); "" = automatic
    setStatus(v) { this.patch(v ? { reading_status: v } : { clear_reading_status: true }); },
    statusLabels: STATUS_LABELS,
    /* ---- the reading mode ---- */
    readerModes: [["", t("Automatic"), ""], ["paged", t("Pages"), t("One page at a time, the next one on the right")],
      ["vertical", t("Vertical"), t("Webtoons, long strips")]],
    get readerModeLabel() {
      const names = { paged: t("Pages"), vertical: t("Vertical") };
      return this.s?.reader_mode ? names[this.s.reader_mode]
        : t("Automatic: {mode}", { mode: names[this.s?.direction?.mode] || names.paged });
    },
    /* what "Automatic" comes to for this series, and why */
    get autoHint() {
      const d = this.s?.direction;
      if (!d) return "";
      const names = { paged: t("Pages"), vertical: t("Vertical") };
      // with a mode set by hand, "Automatic" would be what the library etc. say: not known here
      return d.source === "series" ? t("Its default (e.g. its library's), else Settings → Reader")
        : `${names[d.mode]} · ${t(d.why)}`;
    },
    async setReadingMode(v) {  // "paged", "vertical", or "" for automatic
      await api(`/api/series/${this.s.id}`, { method: "PATCH", body: { reader_mode: v || "default" } });
      this.s.reader_mode = v || null;
      // what it comes to now (Automatic is worked out on the server)
      const fresh = await api(`/api/series/${this.s.id}`).catch(() => null);
      if (fresh) this.s.direction = fresh.direction;
      toast(t("Reading mode: {mode}", { mode: this.readerModeLabel }));
    },
    async toggleFavorite() {  // flip at once, save in the background; nothing else on the page changes
      const on = !this.s.favorite;
      this.s.favorite = on;
      try {
        await api(`/api/series/${this.s.id}`, { method: "PATCH", body: { favorite: on } });
        Alpine.store("app").libraryDirty++;
      } catch (e) {
        this.s.favorite = !on;
        toast(e.message, 6000, "error");
      }
    },
    refreshing: false,
    async refreshMetadata() {
      this.refreshing = true;
      try { await api(`/api/series/${this.s.id}/refresh`, { method: "POST" }); toast(t("Metadata refreshed")); this.reload(); }
      catch (e) { toast(e.message, 6000, "error"); }
      finally { this.refreshing = false; }
    },
    fixMatch() { Alpine.store("app").openFix(this.s.id, () => this.reload()); },
    edit(tab = "info") { Alpine.store("app").openEdit(this.s.id, () => this.reload(), tab); },  // "info" or "poster"
    actionBusy: "",  // plugin + key of the plugin button that is running (it can take a while)
    async runAction(a) {  // one of s.actions: a plugin's button
      if (a.confirm && !confirm(a.confirm)) return;
      this.actionBusy = a.plugin + a.key;
      try { await showActionResult(await api(`/api/series/${this.s.id}/actions/${a.plugin}/${a.key}`, { method: "POST" })); }
      catch (e) { toast(e.message, 6000, "error"); }
      finally { this.actionBusy = ""; }
      this.reload();  // the plugin may have changed the status or its fields
    },
    choiceBusy: false,
    async setChoice(c, value) {  // one of s.choices (a plugin's dropdown) and one of its option values
      this.choiceBusy = true;
      try {
        await showActionResult(await api(`/api/series/${this.s.id}/choices/${c.plugin}/${c.key}`,
          { method: "PUT", body: { value } }));
        Alpine.store("app").libraryDirty++;
      } catch (e) { toast(e.message, 6000, "error"); }
      this.choiceBusy = false;
      this.reload();  // shows what was saved (or puts the old value back), and any status change
    },
    setTab(tab) { location.replace(`#/series/${this.s.id}${tab ? "?tab=" + tab : ""}`); },  // "" or "files"

    bookState(b) {
      if (b.error) return "";
      return (b.completed ? t("Read") : b.page ? t("Page {page} of {pages}", { page: b.page + 1, pages: b.page_count })
        : tn("{n} page", "{n} pages", b.page_count)) + (b.duplicate ? " · " + t("duplicate") : "");
    },
    async markAll(read) {  // every book read (true) or unread (false, asks first)
      if (!read && !confirm(t("Clear progress for every book in this series? It will also be removed from Continue reading."))) return;
      await api(`/api/series/${this.s.id}/${read ? "mark-read" : "mark-unread"}`, { method: "POST" });
      Alpine.store("app").libraryDirty++;
      this.reload();
    },
    async toggleBook(b) {  // one book read, or unread again
      await api(`/api/books/${b.id}/progress`, { method: "PUT",
        body: { page: b.completed ? 0 : b.page_count - 1, completed: !b.completed } });
      Alpine.store("app").libraryDirty++;
      this.reload();
    },
    // "Read up to here" only helps when some book before this one (its place in books) is still unread
    unreadBefore(i) {
      return this.books.slice(0, i).some((x) => !x.completed);
    },
    async readUpTo(b) {  // this book and every one before it read
      await api(`/api/series/${this.s.id}/mark-read?before_book=${b.id}`, { method: "POST" });
      await api(`/api/books/${b.id}/progress`, { method: "PUT", body: { page: 0, completed: true } });
      Alpine.store("app").libraryDirty++;
      this.reload();
    },
    bookProgress(b) { return b.page_count ? Math.round((b.page + 1) / b.page_count * 100) : 0; },  // % read
    /* ---- manual chapter numbers for one file (Files tab) ---- */
    numbersForm(b) {  // the form's starting values: the owner's numbers, else the file name's
      const o = b.override, pick = (manual, auto) => (manual ?? auto ?? "");
      return {
        volume: pick(o.volume, b.volume),
        chapter: pick(o.chapter, b.chapter),
        chapter_end: o.chapter !== null ? (o.chapter_end ?? "") : (b.chapter_end ?? ""),
        extra: o.extra ?? b.extra,
        hidden: b.hidden_by_owner,
      };
    },
    async saveNumbers(b, f) {  // true once saved (the list is re-ordered)
      const num = (v) => (v === "" || v === null ? null : +v);
      // only values that differ from what the file name says become manual overrides
      const chapterChanged = num(f.chapter) !== b.chapter || num(f.chapter_end) !== b.chapter_end;
      const body = {
        volume: num(f.volume) !== b.volume ? num(f.volume) : null,
        chapter: chapterChanged ? num(f.chapter) : null,
        chapter_end: chapterChanged ? num(f.chapter_end) : null,
        extra: f.extra !== b.extra ? f.extra : null,
        hidden: f.hidden,
      };
      if (body.chapter === null && chapterChanged && num(f.chapter_end) !== null) {
        toast(t("Enter the first chapter of the range"));
        return false;
      }
      try {
        await api(`/api/books/${b.id}`, { method: "PATCH", body });
        toast(t("Saved. The list is re-ordered."));
        Alpine.store("app").libraryDirty++;
        this.reload();
        return true;
      } catch (e) { toast(e.message, 6000, "error"); return false; }
    },
    async resetNumbers(b) {  // back to the numbers from the file name
      await api(`/api/books/${b.id}`, { method: "PATCH", body: { reset: true } });
      toast(t("Back to the numbers from the file name"));
      this.reload();
    },

    /* ---- the folder path: copy it (to paste into a file manager) ---- */
    get path() { return this.s.host_path || this.s.folder_path; },  // on the NAS when known, else in Docker
    async copyPath() {  // true when copied
      if (await copyText(this.path)) return true;
      toast(t("Couldn't copy. Select the path and copy it by hand."), 5000, "error");
      return false;
    },

    /* ---- re-read this series' folder ---- */
    rescanning: false,
    async rescan() {
      this.rescanning = true;
      try {
        const r = await api(`/api/series/${this.s.id}/rescan`, { method: "POST" });
        if (r.removed) {
          toast(t("The series folder is gone, so the series was removed from the library"));
          Alpine.store("app").libraryDirty++;
          refreshAttentionCount();
          location.hash = "#/";
          return;
        }
        const files = tn("{n} file", "{n} files", r.books);
        toast(r.errors ? t("Rescanned: {files}, {errors} with errors", { files, errors: r.errors }) : t("Rescanned: {files}", { files }));
        Alpine.store("app").libraryDirty++;
        refreshAttentionCount();
        this.reload();
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.rescanning = false; }
    },
    /* ---- delete the series and its folder: ask deleteInfo() first and show it, then delete() ---- */
    async deleteInfo() {  // { folder, files, size, can_delete, reason }, or null (an error was shown)
      try { return await api(`/api/series/${this.s.id}/delete-info`); }
      catch (e) { toast(e.message, 6000, "error"); return null; }
    },
    async delete() {  // true once deleted (the app goes back to the library)
      const title = this.s.title;
      try {
        await api(`/api/series/${this.s.id}`, { method: "DELETE" });
        toast(t("Deleted: {title}", { title }));
        Alpine.store("app").libraryDirty++;
        refreshAttentionCount();
        location.hash = "#/";
        return true;
      } catch (e) {
        toast(e.message, 8000, "error");
        this.reload();  // a partial delete: show what is left
        return false;
      }
    },
  };
}

/* ---------------- fix match dialog ---------------- */

function fixMatch() {
  return {
    s: null, provider: null, q: "", results: null, error: "", searching: false, url: "", busy: false,
    init() {
      this.$watch("$store.app.fix.open", (open) => open && this.start());
    },
    async start() {
      Object.assign(this, { s: null, results: null, error: "", url: "" });
      const [s, providers] = await Promise.all([api(`/api/series/${this.$store.app.fix.seriesId}`), api("/api/providers")]);
      this.$store.app.providers = providers;
      this.s = s;
      this.q = s.folder_name;
      this.provider = (providers.find((p) => p.available) || providers[0])?.id;
      this.search();
    },
    close() { this.$store.app.fix.open = false; },
    done(msg) {
      toast(msg);
      const cb = this.$store.app.fix.onDone;
      this.close();
      this.$store.app.libraryDirty++;
      refreshAttentionCount();
      cb?.();
    },
    setProvider(id) { this.provider = id; this.search(); },
    async search(fallback = true) {
      this.searching = true;
      this.error = "";
      try {
        this.results = await api(`/api/series/${this.s.id}/match/search`, { method: "POST", body: { query: this.q, provider: this.provider } });
      } catch (e) {
        this.results = null;
        this.error = e.message;
        // provider offline (e.g. AniList down): try the next one once
        const other = this.$store.app.providers.find((p) => p.id !== this.provider);
        if (fallback && e.status === 503 && other) {  // the message is in the page's language: not by its words
          this.$store.app.providers = await api("/api/providers");
          this.provider = other.id;
          this.searching = false;
          return this.search(false);
        }
      } finally { this.searching = false; }
    },
    async use(c) {
      this.busy = c.provider_id;  // which "Use" shows the spinner
      try {
        await api(`/api/series/${this.s.id}/match`, { method: "POST", body: { provider: this.provider, provider_id: c.provider_id } });
        this.done(t("Matched: {title}", { title: c.title }));
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.busy = false; }
    },
    async applyUrl() {
      if (this.busy) return;
      this.busy = "url";
      try {
        await api(`/api/series/${this.s.id}/match`, { method: "POST", body: { url: this.url.trim() } });
        this.done(t("Matched"));
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.busy = false; }
    },
    async post(path, msg) {
      try { await api(`/api/series/${this.s.id}/${path}`, { method: "POST" }); this.done(msg); }
      catch (e) { toast(e.message, 6000, "error"); }
    },
    scoreCls,
  };
}

/* ---------------- needs attention (a part of Settings) ---------------- */

function attentionView() {
  return {
    get a() { return this.$store.app.att; },
    get kind() {
      const s = this.$store.app.view === "settings" ? (this.$store.app.sub || "") : "";
      return s.startsWith("att-") ? s.slice(4) : null;
    },
    get title() { return ATT_TABS.find(([k]) => k === this.kind)?.[1] || ""; },
    get help() { return ATT_TABS.find(([k]) => k === this.kind)?.[2] || ""; },
    get rows() { return this.a?.[this.kind] || []; },
    get ignoredCount() { return this.a?.ignored_counts?.[this.kind] || 0; },
    groups(kind) {  // files of one series together, with one shared message if all are the same
      const map = new Map();
      for (const x of this.a[kind]) {
        if (!map.has(x.series_id)) map.set(x.series_id, { series_id: x.series_id, series: x.series, files: [] });
        map.get(x.series_id).files.push(x);
      }
      return [...map.values()].map((g) => ({
        ...g,
        sameError: new Set(g.files.map((f) => f.error)).size === 1,
        folder: g.files[0].path.slice(0, g.files[0].path.length - g.files[0].file.length),
      }));
    },
    async ignore(key, on = true) {
      await api("/api/attention/ignore", { method: "POST", body: { key, ignore: on, list: this.kind } });
      toast(on ? t("Hidden from this list. “Show ignored” brings it back.") : t("Restored"));
      refreshAttentionCount();
    },
    async ignoreAll(g) {
      for (const f of g.files) {
        if (!f.ignored) await api("/api/attention/ignore", { method: "POST", body: { key: f.key, ignore: true, list: this.kind } });
      }
      toast(tn("{n} file hidden from this list", "{n} files hidden from this list", g.files.length));
      refreshAttentionCount();
    },
    toggleIgnored() {
      this.$store.app.showIgnored = !this.$store.app.showIgnored;
      refreshAttentionCount();
    },
    async hideFile(f) {
      await api(`/api/books/${f.book_id}`, { method: "PATCH", body: { hidden: !f.hidden } });
      toast(f.hidden ? t("File shown again") : t("File hidden from reading (it stays on disk)"));
      this.afterChange();
    },
    matching: null,  // {series_id, provider_id} while a "Use" runs: a spinner on it, the row's buttons off
    async use(x, c) {
      if (this.matching) return;
      this.matching = { series_id: x.series_id, provider_id: c.provider_id };
      try {
        await api(`/api/series/${x.series_id}/match`, { method: "POST", body: { provider: c.provider, provider_id: c.provider_id } });
        toast(t("Matched: {title}", { title: c.title }));
        this.$store.app.libraryDirty++;
        await refreshAttentionCount();  // keep the spinner until the row is gone
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.matching = null; }
    },
    isMatching(x, c) { return this.matching?.series_id === x.series_id && (!c || this.matching.provider_id === c.provider_id); },
    fix(x) { this.$store.app.openFix(x.series_id, () => refreshAttentionCount()); },
    sharedLabel(g) { return t("Same {ids}", { ids: g.shared.map((x) => `${ID_NAMES[x.field] || x.field} #${x.id}`).join(", ") }); },
    async dontMatch(x) {
      await api(`/api/series/${x.series_id}/ignore-match`, { method: "POST" });
      this.afterChange();
    },
    rescanning: {},  // series id -> true while its rescan runs
    async rescan(seriesId) {
      if (this.rescanning[seriesId]) return;
      this.rescanning = { ...this.rescanning, [seriesId]: true };
      try {
        const r = await api(`/api/series/${seriesId}/rescan`, { method: "POST" });
        toast(r.removed ? t("The series folder is gone, so the series was removed from the library")
          : r.errors ? t("Rescanned: {files}, {errors} with errors", { files: tn("{n} file", "{n} files", r.books), errors: r.errors })
            : t("Rescanned: {files}", { files: tn("{n} file", "{n} files", r.books) }));
        this.afterChange();
      } catch (e) { toast(e.message, 6000, "error"); } finally {
        const { [seriesId]: _, ...rest } = this.rescanning;
        this.rescanning = rest;
      }
    },
    afterChange() { refreshAttentionCount(); this.$store.app.libraryDirty++; },
    scoreCls,
  };
}

/* ---------------- settings ---------------- */

// Settings' menu: [key, label, icon, plugin id, personal]. The pages are the running plugins' (their
// settings_pages, put in the page as MV_SETTINGS_PAGES, Plugin API 20) and the core's (Plugins); plugins can
// add more from their scripts with MangaVibe.settingsPage (e.g. libraries: Libraries, first). The icon is the
// name of one in the sprite (templates/shell/icons.html); a plugin may also give any character.
const ANDROID = /Android/i.test(navigator.userAgent);
const SETTINGS_NAV = (window.MV_SETTINGS_PAGES || [{ key: "plugins", label: t("Plugins"), icon: "blocks" }])
  .filter((p) => !p.android || ANDROID)  // e.g. the Android app's page: only on Android phones
  .map((p) => [p.key, p.label, p.icon, p.plugin || "", !!p.personal]);
/* The plain settings of a page: a label, a hint and a dropdown, saved when changed. Each is
   { key, label, hint, options: [[value, text], ...], show(st) }; show (when there) decides whether
   it's there at all. choices(value) (when there) gives the options for the saved value instead.
   Values are strings, like st's; save() turns them into numbers or booleans. The words in the page's
   language (t(), issue #263). */
const ON_OFF = [["true", t("On")], ["false", t("Off")]];
const SETTING_ROWS = {
  display: [
    // the app's language: the browser's, or one picked (the page is drawn again in it)
    { key: "language", label: t("Language"), hint: t("Just for you. Missing words are shown in English"), personal: true,
      options: [["auto", t("Browser's language")], ...(I18N.languages || ["en"]).map((c) => [c, languageName(c)])] },
    { key: "theme", label: t("Theme"), hint: t("Just for you"), personal: true,
      options: [["dark", t("Dark")], ["light", t("Light")], ["auto", t("Match device")]] },
    { key: "card_size", label: t("Card size"), options: [["small", t("Small")], ["medium", t("Medium")], ["large", t("Large")]] },
    { key: "grid_columns", label: t("Grid columns"), personal: true,
      hint: t("Just for you. Auto fits as many cards as the screen has room for; narrow screens show fewer than picked"),
      options: [["auto", t("Auto")], ...GRID_COLUMNS.map((n) => [String(n), fmtNum(n)])] },
    // with Auto columns: the cards are at least this wide and stretch to fill the row (CSS minmax)
    { key: "card_width", label: t("Card width"), personal: true, sub: true,
      hint: t("Just for you, on wide screens. Cards are at least this wide and grow a little to fill the row"),
      options: [["auto", t("From card size")], ...CARD_WIDTHS.map((n) => [String(n), t("{n} px", { n: fmtNum(n) })])],
      show: (st) => st.grid_columns === "auto" },
    { key: "page_width", label: t("Page width"), personal: true,
      hint: t("Just for you. Full width lets the Library and Continue grids use the whole screen, however wide"),
      options: [["contained", t("Contained")], ["full", t("Full width")]] },
    { key: "page_width_series", label: t("Series page full width as well"), personal: true, sub: true, check: true,
      hint: t("The page of a series also uses the whole screen"), show: (st) => st.page_width === "full" },
    { key: "nav_position", label: t("Navigation bar"), hint: t("Where Library and Continue sit (with the Sidebar plugin: on phones only)"),
      options: [["bottom", t("Bottom")], ["top", t("Top")]] },
    { key: "library_listing", label: t("Library listing"), hint: t("Load more while scrolling, or show numbered pages"),
      options: [["lazy", t("Lazy load")], ["paged", t("Paginated")]] },
    { key: "library_page_size", label: t("Series per page"), options: PAGE_SIZES.map((n) => [String(n), fmtNum(n)]),
      show: (st) => st.library_listing === "paged" },
    // also the grid/list switch above the library (the library picks it up when shown again)
    { key: "library_view", label: t("Library view"), hint: t("Just for you"), personal: true,
      options: [["grid", t("Grid")], ["list", t("List")]] },
    { key: "title_language", label: t("Title language"), hint: t("Falls back to the next one if missing"),
      options: [["english", t("English")], ["romaji", t("Romaji")], ["native", t("Native")]] },
  ],
  reader: [
    { key: "reader_mode", label: t("Default reading mode"),
      hint: t("Pages: one at a time, the next one on the right (→, a tap on the right, a swipe to the left). For series whose library has no mode of its own (Settings → Libraries); each series can be set by hand"),
      options: [["paged", t("Pages")], ["vertical", t("Vertical")]] },
    { key: "reader_fit", label: t("Fit"),
      options: [["screen", t("Fit screen")], ["width", t("Fit width")], ["height", t("Fit height")], ["original", t("Original")]] },
    // no fullscreen in the Android app: it already fills the screen between the phone's bars
    { key: "auto_fullscreen", label: t("Fullscreen on first tap"), options: ON_OFF, show: () => !Alpine.store("app").inApp,
      hint: t("Phones and tablets; after the phone was locked, the next tap goes back to fullscreen. With a mouse, use the fullscreen button or F") },
    { key: "preload", label: t("Pages to preload"), hint: t("More = smoother, uses more data"),
      options: ["2", "3", "4", "5", "8"].map((n) => [n, fmtNum(+n)]) },
    { key: "reader_autohide", label: t("Hide controls after"),
      hint: t("When nothing is touched. Turning the page or a tap in the middle hides them at once"),
      // also in the reading options; choices(v): the options, with a saved time that isn't one of them
      options: autohideOptions(), choices: autohideOptions },
    // the same choice (and words) in the reading options: TOP_BAR in common.js
    { key: "reader_top_bar", label: t("Top bar"),
      hint: t("The series and chapter at the top. When there's room: it stays while the page leaves space above it, so it covers none of the picture"),
      options: Object.entries(TOP_BAR) },
    { key: "reader_drawer_side", label: t("Reading hand"),
      hint: t("Back, fullscreen and the options button sit on this side, within thumb reach"),
      options: [["right", t("Right")], ["left", t("Left")]] },
    { key: "reader_tap_layout", label: t("Tap zones"),
      hint: t("One-handed: most of the page turns forward, a narrow edge goes back, the middle opens the menu"),
      options: [["default", t("Left back, right forward")], ["one-handed", t("One-handed")], ["off", t("Off (swipe only)")]] },
    { key: "overlay_opacity", label: t("Overlay box background"),
      hint: t("Plugin boxes over the page, e.g. translations. Lower shows the art underneath"),
      options: [["100", t("Solid")], ["75", pct(75)], ["50", pct(50)], ["25", pct(25)], ["0", t("See-through")]] },
    { key: "overlay_outline", label: t("Overlay text outline"),
      hint: t("A white outline around the letters, so they stay readable over the art"),
      options: [["off", t("Off")], ["thin", t("Thin")], ["thick", t("Thick")]] },
    { key: "reader_brightness", label: t("Page brightness"), hint: t("Darker pages are easier on the eyes, e.g. at night"),
      options: [["100", t("Normal")], ["85", pct(85)], ["70", pct(70)], ["55", pct(55)], ["40", pct(40)]] },
    { key: "reader_contrast", label: t("Page contrast"), hint: t("More makes faint lines and small text stand out"),
      options: [["75", t("Less")], ["100", t("Normal")], ["125", t("More")], ["150", t("Most")]] },
    { key: "reader_colors", label: t("Page colours"),
      hint: t("Inverted turns white pages black with white lines. Warm takes out the blue light"),
      options: [["normal", t("Normal")], ["dark", t("Inverted")], ["warm", t("Warm")], ["dark-warm", t("Inverted, warm")]] },
  ],
  app: [
    { key: "reader_volume_keys", label: t("Volume buttons turn pages"),
      hint: t("In the reader: volume down goes to the next page, volume up goes back"), options: ON_OFF },
  ],
};
// Settings pages that are each person's own (with accounts, everyone sees them; the rest is for admins) are
// the ones registered `personal`. Display is the app's, but its personal rows (the theme) are everyone's.
const PERSONAL_PAGES = new Set();

// Settings → Plugins: what each viewer picked (category, source, status, built-ins unfolded)
const PLUGIN_VIEW_KEY = "plugins-view";
// names of icons in the sprite (templates/shell/icons.html), shown with navIcon()
const PLUGIN_CAT_ICONS = { metadata: "tag", reader: "book-open", library: "books", system: "settings",
  appearance: "palette", other: "blocks" };
function loadPluginView() {
  try {
    const v = JSON.parse(localStorage.getItem(PLUGIN_VIEW_KEY) || "{}");
    const ok = (x, allowed) => (typeof x === "string" && (!allowed || allowed.includes(x)) ? x : undefined);
    return Object.fromEntries(Object.entries({
      tab: ok(v.tab, ["installed", "get", "setup"]),
      cat: ok(v.cat), src: ok(v.src, ["all", "installed", "builtin"]),
      status: ok(v.status, ["all", "on", "off", "update", "errors"]),
      builtinOpen: typeof v.builtinOpen === "boolean" ? v.builtinOpen : undefined,
    }).filter(([, x]) => x !== undefined));
  } catch { return {}; }
}

function settingsView() {
  return {
    // everything for an admin; for others (accounts) only their own pages (Reader...), not the app's
    get nav() {
      const app = this.$store.app;
      return app.admin ? app.settingsNav : app.settingsNav.filter((x) => x[4] || PERSONAL_PAGES.has(x[0]));
    },
    get attNav() { return this.$store.app.admin ? ATT_TABS : []; },
    rows: SETTING_ROWS,  // the plain dropdown settings of each page
    // Display: all of it for an admin, only the personal rows for others
    get displayRows() { return this.$store.app.admin ? SETTING_ROWS.display : SETTING_ROWS.display.filter((r) => r.personal); },
    get section() {
      const s = this.$store.app.sub || this.nav[0][0];
      return s.startsWith("plugin-") ? "plugin" : s;
    },
    // the plugin whose page is open (#/settings/plugin-<id>)
    get plugin() {
      const s = this.$store.app.sub || "";
      return s.startsWith("plugin-") ? this.plugins.find((p) => p.id === s.slice(7)) || null : null;
    },
    // ---- Settings → Plugins: grouped by category, with search and filters ----
    // the category, source (built in or installed) and status picked, kept per browser; the search isn't
    // tab: Installed, Get plugins (from the sources) or Sources & setup; getCat is Get plugins' category
    pf: { tab: "installed", cat: "all", src: "all", status: "all", builtinOpen: false, q: "", getCat: "all",
          ...loadPluginView() },
    pluginCats: {},  // category key -> label, in order (GET /api/plugins)
    // the GitHub token for private plugin repos: where it comes from ("settings", "env", ""), never
    // the token itself; ghToken is only what's being typed
    ghTokenFrom: "", ghToken: "",
    savePluginView() {
      const { tab, cat, src, status, builtinOpen } = this.pf;
      try { localStorage.setItem(PLUGIN_VIEW_KEY, JSON.stringify({ tab, cat, src, status, builtinOpen })); } catch {}
    },
    // a filter of the installed list (an alert, a tag on a plugin's page) shows that list
    setPluginFilter(key, value) {
      this.pf[key] = value;
      if (["cat", "src", "status"].includes(key)) this.pf.tab = "installed";
      this.savePluginView();
    },
    // Get plugins: the categories its plugins are in, and how many of them aren't installed yet
    get availCats() {
      return Object.keys(this.pluginCats).filter((c) => this.avail.plugins.some((a) => a.category === c));
    },
    get availNew() { return this.avail.plugins.filter((a) => !a.installed).length; },
    catLabel(c) { return t(this.pluginCats[c] || c); },  // the labels are marked N_() on the server
    catIcon(c) { return PLUGIN_CAT_ICONS[c] || "blocks"; },
    hasUpdate(p) { return !!(p.update.available && !p.update.skipped); },
    get pluginUpdates() { return this.plugins.filter((p) => this.hasUpdate(p)).length; },
    get pluginErrorCount() { return Object.keys(this.pluginErrors).length; },
    // what the Plugins tab shows, worked out in one pass by an x-effect whenever the plugins or the
    // filters change (not again for every binding that reads it)
    pview: { installed: [], builtin: [], cats: [], all: 0, src: { all: 0, installed: 0, builtin: 0 },
             forced: false, open: false },
    computePluginView() {
      const { cat, src, status: st, builtinOpen } = this.pf;
      const q = this.pf.q.trim().toLowerCase();
      // the search and status apply everywhere, counts included; then category and source
      const base = this.plugins.filter((p) =>
        (st === "all" || (st === "on" && p.enabled) || (st === "off" && !p.enabled) ||
         (st === "update" && this.hasUpdate(p))) &&
        (!q || `${p.name} ${p.description} ${p.id}`.toLowerCase().includes(q)))
        .sort((a, b) => a.name.localeCompare(b.name));
      // a plugin belongs to each of its categories: listed under the main one, found under its tags too
      const inCat = (p, c = cat) => c === "all" || p.categories.includes(c);
      const inSrc = (p, s = src) => s === "all" || (s === "builtin") === p.builtin;
      const count = (fn) => base.reduce((n, p) => n + (fn(p) ? 1 : 0), 0);
      // the sidebar: every category that has a plugin (and the picked one), counted with the source;
      // the sources counted with the category
      const cats = Object.keys(this.pluginCats)
        .filter((c) => c === cat || this.plugins.some((p) => p.categories.includes(c)))
        .map((c) => [c, count((p) => inSrc(p) && inCat(p, c))]);
      const srcCounts = Object.fromEntries(["all", "installed", "builtin"].map((s) => [s, count((p) => inCat(p) && inSrc(p, s))]));
      const list = st === "errors" ? [] : base.filter((p) => inCat(p) && inSrc(p));
      // plugins that do the same exclusive job (Navigation: the top bar or the sidebar) are one row of the
      // list instead, named after the job, that opens to pick one of them. It's listed when one of them
      // matches the filters, always with all of them (so the others can be picked from any view); under
      // Installed if one of them is. A plugin doing two such jobs is in both rows
      const rows = [], grouped = new Set();
      for (const job of this.pluginJobs) {
        if (!job.exclusive || job.plugins.length < 2) continue;
        job.plugins.forEach((id) => grouped.add(id));
        if (!list.some((p) => job.plugins.includes(p.id))) continue;
        const members = this.plugins.filter((p) => job.plugins.includes(p.id)).sort((a, b) => a.name.localeCompare(b.name));
        rows.push({ choice: true, id: `job:${job.key}`, name: t(job.label), job, members,
                    current: members.find((p) => p.enabled && !p.blocked) || null,
                    builtin: members.every((p) => p.builtin), update: members.some((p) => this.hasUpdate(p)) });
      }
      rows.push(...list.filter((p) => !grouped.has(p.id)));
      rows.sort((a, b) => a.name.localeCompare(b.name));
      const installed = rows.filter((p) => !p.builtin), builtin = rows.filter((p) => p.builtin);
      // the built-in ones are folded away, unless that would hide the whole answer: a search, a picked
      // category or source, or nothing installed to show
      const forced = !!q || src === "builtin" || cat !== "all" || !installed.length;
      return { installed, builtin, cats, all: count((p) => inSrc(p)), src: srcCounts, forced,
               open: builtinOpen || forced };
    },
    // what a row shows next to the name: every category while all are listed, else the other ones
    rowTags(p) { return p.categories.filter((c) => c !== this.pf.cat); },
    openPlugin(p) { location.hash = `#/settings/plugin-${p.id}`; },
    go(sec) { this.navOpen = false; location.replace(`#/settings/${sec}`); },
    // phones: the nav is one "current page ▾" button that opens a menu
    navOpen: false,
    get current() {
      if (this.section === "plugin") return ["blocks", this.plugin?.name || t("Plugin")];
      const n = this.nav.find(([k]) => k === this.section);
      if (n) return [n[2], n[1]];
      const a = ATT_TABS.find(([k]) => "att-" + k === this.section);
      return a ? ["alert", a[1]] : ["settings", t("Settings")];
    },
    // problems worth a look (the red counts), shown on the closed menu button
    get attAlerts() {
      return ATT_TABS.filter((t) => t[3]).reduce((n, [k]) => n + this.attCount(k), 0);
    },
    attAlert(k) { return !!ATT_TABS.find((t) => t[0] === k)?.[3]; },
    // Settings → Android app
    appServer: androidApp?.server() || "",
    appVersion: androidApp?.version() || "",
    apkUrl,
    openAndroidApp,
    changeServer() { androidApp?.changeServer(); },
    attCount(k) { return this.$store.app.att?.[k]?.filter((x) => !x.ignored).length || 0; },
    st: {}, libs: [], plugins: [], providers: [], pluginErrors: {}, pluginsMoved: [], pluginJobs: [], pageJobs: [],
    get safe() { return Alpine.store("app").safe; },
    scan: {}, scanText: "", metaText: "", timers: [],
    init() {
      this.$watch("$store.app.view", (v) => (v === "settings" ? this.load() : this.stop()));
      // phone: the navigation is a scrolling strip; keep the open section's button in view
      this.$watch("$store.app.sub", () => this.$nextTick(() =>
        this.$root.querySelector(".settings-nav .active")?.scrollIntoView({ block: "nearest", inline: "center" })));
      window.addEventListener("libraries-changed", () => this.load());
      this.load();
    },
    async load() {
      if (this.$store.app.view !== "settings") return;
      document.title = t("Settings");
      // selects compare strings, so keep every value as text here; save() converts back
      const strings = () => Object.fromEntries(Object.entries(settings).map(([k, v]) => [k, String(v)]));
      if (!this.$store.app.admin) {  // accounts: only their own pages (Reader)
        this.st = strings();
        return emitPlugins("settings-open");
      }
      const [libs, plug, providers] = await Promise.all([api("/api/libraries"), api("/api/plugins"),
        api("/api/metadata/providers"), emitPlugins("settings-open")]);
      this.st = strings();
      Object.assign(this, { libs, providers });
      this.setPlugins(plug);
      if (!this.avail.loaded) this.loadAvailable();  // asks the sources: not waited for
      this.stop();
      this.pollScan();
      this.pollMeta();
      this.pollPluginStatus();
    },
    stop() { this.timers.forEach(clearTimeout); this.timers = []; clearTimeout(this.statusTimer); },
    // the plugins' status lines (e.g. "Queue: 12 pages") stay live while a plugins page is open: asked
    // every 3 s, every 15 s after a minute without a change. Only the status changes, so a setting
    // being typed isn't touched.
    statusTimer: null, statusSame: 0,
    async pollPluginStatus() {
      const app = this.$store.app;
      if (app.view !== "settings") return;
      if (!document.hidden && (this.section === "plugins" || this.section === "plugin")) {
        const r = await api("/api/plugins").catch(() => null);
        if (app.view !== "settings") return;
        let changed = false;
        for (const fresh of r?.plugins || []) {
          const p = this.plugins.find((x) => x.id === fresh.id);
          if (p && p.status !== fresh.status) { p.status = fresh.status; changed = true; }
        }
        this.statusSame = changed ? 0 : this.statusSame + 1;
      }
      clearTimeout(this.statusTimer);
      this.statusTimer = setTimeout(() => this.pollPluginStatus(), this.statusSame >= 20 ? 15000 : 3000);
    },
    async save(key) {
      let v = this.st[key];
      if (v === "true" || v === "false") v = v === "true";
      else if (typeof v === "string" && /^\d+(\.\d+)?$/.test(v)) v = +v;  // e.g. 0.5 seconds
      await api("/api/settings", { method: "PUT", body: { [key]: v } });
      // the page is drawn in its language on the server: draw it again, staying where we are
      if (key === "language") { location.reload(); return; }
      if (key === "theme") setTheme(v);
      await loadSettings();
      if (["title_language", "library_listing", "library_page_size", "grid_columns"].includes(key)) this.$store.app.libraryDirty++;
      toast(t("Saved"));
    },
    async rescan(lib) {
      const r = await api(lib ? `/api/libraries/${lib.id}/scan` : "/api/scan", { method: "POST" })
        .catch((e) => { toast(e.message, 6000); return null; });
      if (!r) return;
      if (!r.started) toast(t("A scan is already running. Try again when it has finished."), 5000);
      this.stop(); this.pollScan();
    },
    // progress for one folder's row while that folder is being scanned
    libScanText(l) {
      const st = this.scan;
      if (!st.running || (st.library_id != null ? st.library_id !== l.id : st.library !== l.name)) return "";
      return st.library_id != null ? t("Scanning {done}/{total}…", { done: st.done, total: st.total }) : t("Scanning…");
    },
    async setLibraryHostPath(l, v) {
      v = v.trim();
      if (v === (l.host_path || "")) return;
      try {
        await api(`/api/libraries/${l.id}`, { method: "PATCH", body: { host_path: v } });
        l.host_path = v || null;
        toast(v ? t("Saved") : t("Cleared: the series page shows the path inside Docker"));
      } catch (e) { toast(e.message, 6000, "error"); }
    },
    async removeLibrary(l) {
      if (!confirm(t("Remove “{name}” ({path}) from MangaVibe?", { name: l.name, path: l.root_path }) + "\n\n"
        + tn("Your files are not touched. Its {n} series and their reading progress are removed from the app.",
             "Your files are not touched. Its {n} series and their reading progress are removed from the app.", l.series))) return;
      try { await api(`/api/libraries/${l.id}`, { method: "DELETE" }); }
      catch (e) { return toast(e.message, 6000, "error"); }  // e.g. its library is locked
      toast(t("Folder removed"));
      await emitPlugins("folders-changed");  // e.g. its library goes too when nothing else is in it
      this.load();
    },
    checking: false,
    get upd() { return this.$store.app.update; },
    versionText(v) {
      if (!v?.sha) return t("Built-in version (from the Docker image)");
      return v.short + (v.date ? ` · ${fmtDate(v.date)}` : "")
        + (v.message ? ` · ${v.message}` : "") + (v.builtin ? " " + t("(built into the image)") : "");
    },
    get checkedText() {
      const at = this.upd?.status?.checked_at;
      return at ? t("Last checked {when}", { when: fmtDateTime(at * 1000) }) : t("Not checked yet");
    },
    async checkUpdate() {
      this.checking = true;
      try {
        this.$store.app.update = await api("/api/update/check", { method: "POST" });
        const u = this.$store.app.update;
        if (u.status.error) toast(u.status.error, 6000, "error");
        else toast(u.available ? t("A new version is available") : t("You have the newest version"));
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.checking = false; }
    },
    async skipUpdate() {
      this.$store.app.update = await api("/api/update/skip", { method: "POST", body: { sha: this.upd.available.sha } });
    },
    async unskipUpdate() { this.$store.app.update = await api("/api/update/skip", { method: "DELETE" }); },
    installUpdate() { installUpdate(this.upd.available.sha); },
    // ---- going back to an earlier version (GET /api/update/versions, only when asked: it asks GitHub)
    older: null, olderError: "", loadingOlder: false,
    async loadOlder() {
      this.loadingOlder = true;
      try {
        const r = await api("/api/update/versions");
        this.older = r.versions;
        this.olderError = r.error || "";
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.loadingOlder = false; }
    },
    olderText(v) {
      const where = v.builtin ? t("Built into the Docker image, no download")
        : v.on_disk ? t("Still on the server, no download") : t("Downloaded from GitHub");
      return [v.date && fmtDate(v.date), v.message, where].filter(Boolean).join(" · ");
    },
    goBack(v) {
      const cur = this.upd?.current?.short || t("the running version");
      if (!confirm(t("Go back to {version}? MangaVibe restarts with it. {current} won't be offered again until a newer version is out.",
                     { version: v.short, current: cur }))) return;
      installUpdate(v.sha, { rollback: true, label: v.short });
    },
    // ---- Settings → Backup (app/backup.py): the export is a plain download link
    backupTokens: "false", importing: false,
    async importBackup(e) {
      const file = e.target.files[0];
      e.target.value = "";  // the same file can be picked again
      if (!file || this.importing) return;
      if (!confirm(t("Import {file}? It replaces this server's library data, reading progress, accounts and settings with the file's. The current database is copied to /data/backups first.",
                     { file: file.name }))) return;
      this.importing = true;
      try {
        // the file itself as the body (not a form), so the server writes it straight to the data volume
        const res = await fetch("/api/backup/import", { method: "POST", body: file,
                                                         headers: { "Content-Type": "application/zip" } });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
        const r = await res.json();
        toast(t("Imported {series} and {books}. Reloading…", { series: tn("{n} series", "{n} series", r.series),
                                                               books: tn("{n} book", "{n} books", r.books) }), 4000);
        // everything changed (and with accounts, the login may have too): start again from the top
        setTimeout(() => { location.hash = "#/"; location.reload(); }, 1500);
      } catch (err) {
        toast(err.message, 10000, "error");
        this.importing = false;
      }
    },
    async loadProviders() { this.providers = await api("/api/metadata/providers").catch(() => this.providers); },
    // the plugins doing one of p's exclusive jobs now, which switching p on replaces (Plugin API 15)
    replacedBy(p) {
      const out = [];
      for (const job of this.pluginJobs) {
        if (!job.exclusive || !(job.key in (p.provides || {}))) continue;
        for (const id of job.doing) {
          const q = this.plugins.find((x) => x.id === id);
          if (q && q.id !== p.id) out.push([q, job]);
        }
      }
      return out;
    },
    jobLabel(key) { return t(this.pluginJobs.find((j) => j.key === key)?.label || key); },
    jobsText(p) { return Object.keys(p.provides || {}).map((k) => this.jobLabel(k)).join(", "); },
    // the only plugin doing a job MangaVibe needs: it can't be switched off until another one does it
    requiredText(p) {
      const jobs = (p.required_for || []).map((k) => this.jobLabel(k)).join(", ");
      return jobs && t("MangaVibe needs {jobs}. To switch {plugin} off, switch on another plugin that does it.",
                       { jobs, plugin: p.name });
    },
    // the "pick one" rows (computePluginView) that are open, by job key
    choiceOpen: {},
    toggleChoice(key) { this.choiceOpen = { ...this.choiceOpen, [key]: !this.choiceOpen[key] }; },
    // a "pick one" row's Use button: p switched on, which switches the row's other one off; or, for a job
    // the app can do without, null for none of them
    pickPlugin(job, p) {
      const current = this.plugins.find((x) => x.enabled && job.plugins.includes(x.id));
      const target = p || current;
      if (!target || (p && target === current)) return;
      target.enabled = !!p;
      this.togglePlugin(target, job.key);
    },
    // picked: the job key when p was picked in that job's "pick one" row
    async togglePlugin(p, picked = null) {
      // picked from a row, it's clear what it replaces there: only ask about the other jobs it takes over
      const replaced = p.enabled ? this.replacedBy(p).filter(([, job]) => job.key !== picked) : [];
      if (replaced.length && !confirm(replaced.map(([q, job]) => t("{plugin} replaces {other} ({job}).",
          { plugin: p.name, other: q.name, job: t(job.label) })).join("\n") + " " + t("Switch it on?"))) return (p.enabled = false);
      let r;
      try { r = await api(`/api/plugins/${p.id}`, { method: "PUT", body: { enabled: p.enabled } }); }
      catch (e) {  // e.g. Libraries while one has a password, or the only plugin doing a job the app needs
        p.enabled = !p.enabled;
        if (picked) this.refreshPlugins();  // the radio buttons show what's on again
        return toast(e.message, 8000, "error");
      }
      // a view (the cards, a page, a dialog, a Settings section...) is drawn when the page is put together:
      // load the page again to show the other one
      const ids = [...(r.started || []), ...(r.stopped || [])];
      if (ids.some((id) => Object.keys(this.plugins.find((x) => x.id === id)?.provides || {})
        .some((j) => this.pageJobs.includes(j)))) {
        return location.reload();
      }
      // what it adds to the page (e.g. Saved searches), and the plugins it replaced or that start or stop
      // with it: show that, or take it away. Safe mode leaves out the owner's own plugins
      const shown = (id) => !this.safe || this.plugins.find((x) => x.id === id)?.builtin;
      try {
        for (const id of r.ui_stopped || []) await removePluginUi(id);
        for (const id of r.ui_started || []) if (shown(id)) await addPluginUi(id);
      } catch (e) {
        if (e.status === 401) return location.reload();  // e.g. Accounts: from now on, logging in first
        toast(t("Reload the page to see the change ({error})", { error: e.message }), 6000, "error");
      }
      const changed = [...(r.started || []), ...(r.stopped || [])].map((id) => this.plugins.find((x) => x.id === id));
      if (changed.some((x) => x?.provider)) this.loadProviders();
      // another plugin filters and sorts the library now: its sheets, and the grid as it lists it
      if (changed.some((x) => x?.provides?.["library-query"])) {
        await loadLibrarySchema();
        this.$store.app.libraryDirty++;
      }
      this.refreshPlugins();  // a switched-on plugin shows its settings and status
    },
    async refreshPlugins() {
      const r = await api("/api/plugins").catch(() => null);
      if (r) this.setPlugins(r);
    },
    // a GET /api/plugins answer (also what reload and update install return)
    setPlugins(r) {
      this.plugins = r.plugins;
      this.pluginErrors = r.errors;
      this.pluginsMoved = r.moved || [];
      if (r.jobs) this.pluginJobs = r.jobs;
      if (r.page_jobs) this.pageJobs = r.page_jobs;
      if ("github_token" in r) this.ghTokenFrom = r.github_token;
      if (r.categories) {
        this.pluginCats = r.categories;
        // a category saved by an older version, or one no longer there: show them all
        if (this.pf.cat !== "all" && !(this.pf.cat in r.categories)) this.setPluginFilter("cat", "all");
      }
    },
    async savePluginSetting(p, f) {
      try {
        const fresh = await api(`/api/plugins/${p.id}/settings`, { method: "PUT", body: { [f.key]: f.value } });
        Object.assign(p, fresh);
        toast(t("Saved"));
      } catch (e) { toast(e.message, 6000, "error"); }
    },
    pluginUpdateText(p) {
      const u = p.update;
      if (u.error) return u.error;
      const when = " · " + (u.checked_at ? t("last checked {when}", { when: fmtDateTime(u.checked_at * 1000) }) : t("not checked yet"));
      if (u.skipped) return t("You skipped version {version}. A newer one will show here", { version: u.latest }) + when;
      if (u.available) return t("Version {version} is available (you have {current})", { version: u.latest, current: p.version }) + when;
      return t("Up to date. New versions come from {url}", { url: `github.com/${u.repo}` }) + when;
    },
    async skipPluginUpdate(p, on) {
      try {
        Object.assign(p, await api(`/api/plugins/${p.id}/updates/skip`, { method: on ? "POST" : "DELETE" }));
      } catch (e) { toast(e.message, 6000, "error"); }
    },
    async checkPluginUpdate(p) {
      this.busyAction = p.id + ":update";
      try {
        Object.assign(p, await api(`/api/plugins/${p.id}/updates/check`, { method: "POST" }));
        if (p.update.error) toast(p.update.error, 6000, "error");
        else toast(p.update.available ? t("Version {version} is available", { version: p.update.latest })
          : t("{plugin} is up to date", { plugin: p.name }));
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.busyAction = ""; }
    },
    async saveGitHubToken(clear) {
      const token = clear ? "" : this.ghToken.trim();
      if (!clear && !token) return;
      try {
        const r = await api("/api/plugins/github-token", { method: "PUT", body: { token } });
        this.ghTokenFrom = r.github_token; this.ghToken = "";
        toast(clear ? t("GitHub token removed") : t("GitHub token saved"));
      } catch (e) { toast(e.message, 6000, "error"); }
    },
    installPluginUpdate(p) {
      if (!confirm(t("Install {plugin} {version} from {url}?", { plugin: p.name, version: p.update.latest,
                                                                url: `github.com/${p.update.repo}` }))) return;
      this.putInPluginVersion(p, "install", null, p.update.latest);
    },
    // going back: a copy in plugins/.backup (no download), or any release picked from the list
    restorePluginVersion(p, version) {
      if (!confirm(t("Go back to {plugin} {version}? It's the copy kept when you updated it.", { plugin: p.name, version }))) return;
      this.putInPluginVersion(p, "restore", version, version);
    },
    pluginReleases: {},  // plugin id -> its releases on GitHub, once "Other versions" was pressed
    pluginPick: {},      // plugin id -> the release picked in that list
    async loadPluginReleases(p) {
      this.busyAction = p.id + ":update";
      try {
        const r = await api(`/api/plugins/${p.id}/updates/releases`);
        this.pluginReleases[p.id] = r.releases.filter((x) => x.version.replace(/^v/, "") !== p.version.replace(/^v/, ""));
        this.pluginPick[p.id] = this.pluginReleases[p.id][0]?.version || "";
        if (!this.pluginReleases[p.id].length) toast(t("No other release found"));
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.busyAction = ""; }
    },
    installPluginRelease(p) {
      const version = this.pluginPick[p.id];
      if (!version || !confirm(t("Install {plugin} {version} from {url}?", { plugin: p.name, version,
                                                                          url: `github.com/${p.update.repo}` }))) return;
      this.putInPluginVersion(p, "install", version, version);
    },
    async putInPluginVersion(p, what, version, label) {
      this.busyAction = p.id + ":update";
      const send = (accept) => api(`/api/plugins/${p.id}/updates/${what}`,
                                   { method: "POST", body: { accept_permissions: accept, version } });
      try {
        let r;
        try { r = await send([]); } catch (e) {
          if (e.status !== 409 || !e.detail.permissions) throw e;
          // that version asks for permissions the installed one doesn't have
          const perms = e.detail.permissions;
          if (!confirm(t("{plugin} {version} asks for new permissions:", { plugin: p.name, version: label }) + "\n\n" +
                       perms.map((x) => `• ${x}`).join("\n") +
                       "\n\n" + t("Allow them and install it?"))) { toast(t("Nothing was installed")); return; }
          r = await send(perms);
        }
        this.setPlugins(r);
        delete this.pluginReleases[p.id];
        toast(r.message);
        this.loadProviders();
      } catch (e) { toast(e.message, 8000, "error"); } finally { this.busyAction = ""; }
    },
    // ---- Settings → Plugins → Get plugins: what the plugin sources list ----
    avail: { loaded: false, sources: [], plugins: [] },
    availBusy: false, availInstalling: "", newSource: "",
    // the sources' plugins that match the search and category; not installed ones first
    get availList() {
      const q = this.pf.q.trim().toLowerCase(), cat = this.pf.getCat;
      const names = Object.fromEntries(this.avail.sources.map((s) => [s.url, s]));
      return this.avail.plugins
        .filter((a) => (cat === "all" || a.category === cat) &&
                       (!q || `${a.name} ${a.description} ${a.id} ${a.repo}`.toLowerCase().includes(q)))
        .map((a) => ({ ...a, official: !!names[a.source]?.official, sourceName: names[a.source]?.name || "" }))
        .sort((a, b) => (a.installed - b.installed) || a.name.localeCompare(b.name));
    },
    async loadAvailable(refresh = false) {
      this.availBusy = true;
      try {
        this.avail = { loaded: true, ...await api(`/api/plugins/available${refresh ? "?refresh=1" : ""}`) };
        if (refresh) {
          const bad = this.avail.sources.filter((s) => s.error);
          toast(bad.length ? `${bad[0].name}: ${bad[0].error}` : t("Plugin sources refreshed"), bad.length ? 8000 : 3000,
                bad.length ? "error" : "");
        }
      } catch (e) {
        this.avail.loaded = true;
        if (refresh) toast(e.message, 6000, "error");
      } finally { this.availBusy = false; }
    },
    async addSource() {
      const url = this.newSource.trim();
      if (!url) return;
      if (!confirm(t("Plugins from this source run inside MangaVibe with full access to its data. Only add sources you trust. Add it?"))) return;
      this.availBusy = true;
      try {
        this.avail = { loaded: true, ...await api("/api/plugins/sources", { method: "POST", body: { url } }) };
        this.newSource = "";
        toast(t("Source added"));
      } catch (e) { toast(e.message, 8000, "error"); } finally { this.availBusy = false; }
    },
    async removeSource(s) {
      if (!confirm(t("Remove the source {name}? Plugins you installed from it stay.", { name: s.name }))) return;
      try {
        this.avail = { loaded: true, ...await api("/api/plugins/sources/remove", { method: "POST", body: { url: s.url } }) };
      } catch (e) { toast(e.message, 6000, "error"); }
    },
    async installAvailable(a) {
      if (!confirm(t("Install {plugin} from {url}?", { plugin: a.name, url: `github.com/${a.repo}` }) +
                   (a.official ? "" : "\n\n" + t("It's listed by {source}, not by MangaVibe.", { source: a.sourceName })))) return;
      this.availInstalling = a.source + " " + a.id;
      const send = (accept) => api("/api/plugins/available/install",
                                   { method: "POST", body: { source: a.source, id: a.id, accept_permissions: accept } });
      try {
        let r;
        try { r = await send([]); } catch (e) {
          if (e.status !== 409 || !e.detail.permissions) throw e;
          const perms = e.detail.permissions;
          if (!confirm(t("{plugin} asks for these permissions:", { plugin: a.name }) + "\n\n" + perms.map((x) => `• ${x}`).join("\n") +
                       "\n\n" + t("Allow them and install it?"))) { toast(t("Nothing was installed")); return; }
          r = await send(perms);
        }
        const p = r.plugins.find((x) => x.id === a.id);
        // a plugin that adds to the page shows once the page loads again
        if (p?.ui && p.enabled) { toast(r.message); return setTimeout(() => location.reload(), 800); }
        this.setPlugins(r);
        this.avail = { loaded: true, ...r.available };
        toast(r.message);
        this.loadProviders();
      } catch (e) { toast(e.message, 8000, "error"); } finally { this.availInstalling = ""; }
    },
    busyAction: "",
    async runGlobalAction(p, a) {
      if (a.confirm && !confirm(a.confirm)) return;
      this.busyAction = p.id + a.key;
      try { await showActionResult(await api(`/api/plugins/${p.id}/actions/${a.key}`, { method: "POST" })); }
      catch (e) { toast(e.message, 6000, "error"); }
      finally { this.busyAction = ""; }
      this.refreshPlugins();
      this.statusSame = 0;  // a button was pressed: watch its status closely again
    },
    async reloadPlugins() {
      const before = this.plugins.some((p) => p.ui && p.enabled);
      const r = await api("/api/plugins/reload", { method: "POST" });
      // a plugin that adds to the page: load it again so its new version shows
      if (before || r.plugins.some((p) => p.ui && p.enabled)) return location.reload();
      this.setPlugins(r);
      this.loadProviders();
      this.loadAvailable();  // which of the sources' plugins are installed now
    },
    async pollScan() {
      const st = await api("/api/scan/status").catch(() => null);
      if (!st || this.$store.app.view !== "settings") return;
      const finished = this.scan.running && !st.running;
      this.scan = st;
      if (finished) this.libs = await api("/api/libraries").catch(() => this.libs);  // new series counts
      this.scanText = st.running ? t("Scanning {done}/{total}: {current}", { done: st.done, total: st.total, current: st.current || "" })
        : st.finished_at ? [t("Last scan finished {when}", { when: fmtDateTime(st.finished_at) }),
                            st.new_books && tn("{n} new book", "{n} new books", st.new_books),
                            st.errors && tn("{n} error", "{n} errors", st.errors)].filter(Boolean).join(" · ") : "";
      if (st.running) { this.$store.app.libraryDirty++; this.timers.push(setTimeout(() => this.pollScan(), 1000)); }
    },
    async pollMeta() {
      const st = await api("/api/metadata/status").catch(() => null);
      if (!st || this.$store.app.view !== "settings") return;
      this.$store.app.meta = st;  // the progress bar (Metadata, Missing metadata)
      const parts = [];
      if (st.matched || st.failed) parts.push(t("{matched} matched, {failed} need attention", { matched: st.matched, failed: st.failed }));
      if (st.providers_down_until) parts.push(t("AniList/MangaUpdates not reachable; retrying at {time}", { time: fmtTime(st.providers_down_until) }));
      this.metaText = parts.join(" · ") || (st.current || st.queued ? ""
        : t("Idle. New series are matched automatically after each scan."));
      if (st.current || st.queued) { this.$store.app.libraryDirty++; this.timers.push(setTimeout(() => this.pollMeta(), 2000)); }
    },
  };
}

/* ---------------- folder picker (folders inside the container) ---------------- */

function folderPicker() {
  return {
    path: "", parent: null, dirs: [], error: "", busy: false,
    init() {
      this.$watch("$store.app.folderPicker", (open) => open && this.open(""));
    },
    async open(path) {
      this.error = "";
      try {
        Object.assign(this, await api(`/api/folders?path=${encodeURIComponent(path)}`));
      } catch (e) { this.error = e.message; }
    },
    get crumbs() {  // "/manga/Shoujo" -> ["/", "/manga", "/manga/Shoujo"]
      if (!this.path) return [];
      const sep = this.path.includes("\\") ? "\\" : "/";
      const parts = this.path.split(sep).filter(Boolean);
      const root = this.path.startsWith("/") ? "/" : parts.shift() + sep;
      const out = [{ name: root, path: root }];
      let cur = root;
      for (const p of parts) {
        cur = cur.endsWith(sep) ? cur + p : cur + sep + p;
        out.push({ name: p, path: cur });
      }
      return out;
    },
    close() { this.$store.app.folderPicker = false; },
    async choose() {
      this.busy = true;
      try {
        await api("/api/libraries", { method: "POST", body: { root_path: this.path } });
        toast(t("Folder added. Scanning…"), 5000);
        this.close();
        await emitPlugins("folders-changed");  // e.g. libraries: the one named like the folder
        window.dispatchEvent(new Event("libraries-changed"));
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.busy = false; }
    },
  };
}

/* ---------------- edit series dialog (info + poster) ---------------- */

const TYPES = { manga: ["MANGA", "JP"], manhwa: ["MANGA", "KR"], manhua: ["MANGA", "CN"],
  one_shot: ["ONE_SHOT", null], novel: ["NOVEL", null] };
const typeOf = (s) => s.format === "NOVEL" ? "novel" : s.format === "ONE_SHOT" ? "one_shot"
  : s.country_of_origin === "KR" ? "manhwa" : ["CN", "TW"].includes(s.country_of_origin) ? "manhua"
  : s.format || s.country_of_origin ? "manga" : "";

function editSeries() {
  return {
    s: null, tab: "info", f: {}, orig: {}, saving: false, books: [],
    file: null, preview: "", uploading: false, pageBook: null, pageNo: 1,
    pubLabels: PUB_LABELS,
    init() {
      this.$watch("$store.app.edit.open", (open) => open && this.start());
    },
    async start() {
      const id = this.$store.app.edit.seriesId;
      const [s, books] = await Promise.all([api(`/api/series/${id}`), api(`/api/series/${id}/books`)]);
      this.s = s;
      this.books = books.filter((b) => !b.error);
      this.pageBook = this.books[0]?.id ?? null;
      this.pageNo = 1;
      this.tab = this.$store.app.edit.tab || "info";
      this.clearFile();
      const lines = (a) => a.join("\n");
      this.f = {
        title_english: s.titles.english || "", title_romaji: s.titles.romaji || "", title_native: s.titles.native || "",
        synonyms: lines(s.synonyms), description: s.description || "",
        publication_status: s.publication_status || "UNKNOWN", start_year: s.start_year ?? "",
        type: typeOf(s), volumes_total: s.volumes_total ?? "", chapters_total: s.chapters_total ?? "",
        authors: lines(s.authors.map((a) => a.name + (a.role ? ` (${a.role})` : ""))),
        genres: s.tags.filter((tag) => tag.kind === "genre").map((tag) => tag.name).join(", "),
        tags: s.tags.filter((tag) => tag.kind !== "genre").map((tag) => tag.name).join(", "),
        links: lines(s.links.map((l) => `${l.site} | ${l.url}`)),
        collection_complete: s.collection?.manual ? "complete" : "auto",
        edition_volumes: String(s.edition_volumes || 0),
      };
      this.orig = { ...this.f };
      collectPlugins("edit-open", s);  // plugin fields in the series-edit slot fill themselves in
    },
    close() { this.clearFile(); this.$store.app.edit.open = false; },
    locked(field) { return this.s?.locked_fields.includes(field); },
    get editions() { return this.s ? editionOptions(this.s) : []; },
    get lockedCount() { return this.s?.locked_fields.length || 0; },

    /* turn the form back into API values; only changed fields are sent (and so locked) */
    body() {
      const f = this.f, o = this.orig, b = {};
      const changed = (k) => String(f[k]) !== String(o[k]);
      const num = (v) => (v === "" || v === null ? null : +v);
      const list = (v, sep) => v.split(sep).map((x) => x.trim()).filter(Boolean);
      for (const k of ["title_english", "title_romaji", "title_native", "description", "publication_status"]) {
        if (changed(k)) b[k] = f[k];
      }
      for (const k of ["start_year", "volumes_total", "chapters_total"]) if (changed(k)) b[k] = num(f[k]);
      if (changed("synonyms")) b.synonyms = list(f.synonyms, "\n");
      if (changed("type")) [b.format, b.country_of_origin] = TYPES[f.type] || [null, null];
      if (changed("authors")) b.authors = list(f.authors, "\n").map((line) => {
        const m = line.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
        return m ? { name: m[1], role: m[2] } : { name: line, role: "" };
      });
      if (changed("collection_complete")) b.collection_complete = f.collection_complete === "complete";
      if (changed("edition_volumes")) b.edition_volumes = +f.edition_volumes;
      if (changed("genres")) b.genres = list(f.genres, ",");
      if (changed("tags")) b.tags = list(f.tags, ",");
      if (changed("links")) b.links = list(f.links, "\n").map((line) => {
        const [site, ...rest] = line.split("|");
        return rest.length ? { site: site.trim(), url: rest.join("|").trim() } : { site: "Link", url: line.trim() };
      });
      return b;
    },
    async save() {
      const body = this.body(), metadata = Object.keys(body).length > 0;
      const pluginChanged = collectPlugins("edit-changed", this.s).some(Boolean);  // e.g. its library
      if (!metadata && !pluginChanged) return this.close();
      this.saving = true;
      try {
        // the metadata first: moved to a locked library, the series can't be changed any more
        if (metadata) await api(`/api/series/${this.s.id}/metadata`, { method: "PUT", body });
        // then the plugins' fields: each answers { message, leave } (leave: the series can't be shown now)
        const done = await emitPlugins("edit-save", [this.s], { strict: true });
        const leave = done.find((r) => r.leave);
        if (leave) {  // e.g. moved to a locked library: back to the library instead of a password prompt
          toast(leave.message, 6000);
          this.close();
          this.$store.app.libraryDirty++;
          location.hash = "#/";
          return;
        }
        toast(metadata ? t("Saved. Your changes are kept when metadata is refreshed.")
          : done.map((r) => r.message).filter(Boolean).join(" ") || t("Saved"));
        this.done();
      } catch (e) { toast(e.message, 7000, "error"); } finally { this.saving = false; }
    },
    async unlockAll() {
      if (!confirm(t("Let metadata refresh change all fields again? Your edits stay until the next refresh."))) return;
      await api(`/api/series/${this.s.id}/metadata`, { method: "PUT", body: { unlock: this.s.locked_fields } });
      toast(t("Unlocked"));
      this.start();
    },
    done() {
      const cb = this.$store.app.edit.onDone;
      this.close();
      this.$store.app.libraryDirty++;
      cb?.();
    },

    /* ---- poster ---- */
    pickFile(ev) {
      const file = ev.target.files[0];
      this.clearFile();
      if (!file) return;
      if (!file.type.startsWith("image/")) return toast(t("Please choose an image file"));
      if (file.size > 10 * 1024 * 1024) return toast(t("The image is larger than 10 MB"));
      this.file = file;
      this.preview = URL.createObjectURL(file);  // local preview only; the server checks the file again
    },
    clearFile() {
      if (this.preview) URL.revokeObjectURL(this.preview);
      this.file = null;
      this.preview = "";
      if (this.$refs.file) this.$refs.file.value = "";
    },
    async upload() {
      if (!this.file) return;
      this.uploading = true;
      const form = new FormData();
      form.append("file", this.file);
      try {
        const res = await fetch(`/api/series/${this.s.id}/poster/upload`, { method: "POST", body: form });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
        toast(t("Poster changed"));
        this.done();
      } catch (e) { toast(e.message, 7000, "error"); } finally { this.uploading = false; }
    },
    get pageBookObj() { return this.books.find((b) => b.id === +this.pageBook); },
    get pagePreview() {
      const b = this.pageBookObj;
      if (!b) return "";
      const n = Math.max(1, Math.min(+this.pageNo || 1, b.page_count)) - 1;
      return `/api/books/${b.id}/pages/${n}?v=${b.version}&w=360`;
    },
    async choose(source) {
      const b = this.pageBookObj;
      const body = source === "page" ? { source, book_id: b.id, page: Math.max(1, +this.pageNo || 1) - 1 } : { source };
      try {
        await api(`/api/series/${this.s.id}/poster`, { method: "POST", body });
        toast(t("Poster changed"));
        this.done();
      } catch (e) { toast(e.message, 7000, "error"); }
    },
  };
}
