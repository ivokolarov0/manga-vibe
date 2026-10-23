/* MangaVibe UI with Alpine.js (https://alpinejs.dev). The markup lives in index.html;
   this file holds the data and actions. The reader is plain JS in reader.js.

   Routes use the URL hash:
     #/?genre=..&read=..  library (filters in the URL)   #/series/12?tab=files  series page
     #/continue           continue reading               #/attention?tab=..     needs attention
     #/settings           settings                       #/read/34?page=5       reader          */
"use strict";

const STATUS_LABELS = { unread: "Unread", reading: "Reading", completed: "Completed",
  plan_to_read: "Plan to read", on_hold: "On hold", dropped: "Dropped" };
const PUB_LABELS = { RELEASING: "Releasing", FINISHED: "Finished", HIATUS: "Hiatus", CANCELLED: "Cancelled",
  NOT_YET_RELEASED: "Not yet released", UNKNOWN: "Unknown status" };
const COUNTRY = { JP: "Manga", KR: "Manhwa", CN: "Manhua", TW: "Manhua" };
const SORTS = [["title", "Title"], ["added", "Recently added"], ["updated", "Recently updated"],
  ["read", "Recently read"], ["score", "Score"], ["year", "Year"], ["unread", "Unread count"]];
const FILTER_GROUPS = [
  ["read", "Reading status", { unread: "Unread", reading: "Reading", completed: "Completed", on_hold: "On hold",
    dropped: "Dropped", plan_to_read: "Plan to read" }],
  ["pub", "Publication", { RELEASING: "Releasing", FINISHED: "Finished", HIATUS: "Hiatus", CANCELLED: "Cancelled",
    NOT_YET_RELEASED: "Not yet released", UNKNOWN: "Unknown status" }],
  ["match", "Metadata", { matched: "Has metadata", attention: "Needs attention", unmatched: "No metadata" }],
  ["format", "Format", { manga: "Manga", manhwa: "Manhwa", manhua: "Manhua", one_shot: "One-shot", novel: "Novel" }],
];
const LIST_KEYS = ["read", "pub", "match", "format", "genre", "exclude"];
const QUICK = [["", "All"], ["unread", "Unread"], ["reading", "Reading"], ["completed", "Read"],
  ["plan_to_read", "Plan to read"], ["fav", "★ Favorites"]];
const ATT_TABS = [
  ["unreadable", "Errors", "These files could not be opened. The message says why."],
  ["matches", "Missing metadata", "No confident match was found online. Pick the right one, or open Fix match."],
  ["duplicates", "Duplicates", "Two or more files with the same chapter number. Both are kept."],
  ["gaps", "Missing chapters", "Gaps in the chapter numbers. Often fine (volumes may cover them)."],
  ["unsupported", "Not supported", "Text books (light novel EPUBs) are listed; a novel reader comes later."],
];

/* ---------------- small helpers ---------------- */

// genre/exclude hold free-text tag names (may contain commas): one URL param per value.
// The other filters use fixed values, joined with commas.
const REPEATED = ["genre", "exclude"];
const getList = (p, k) => REPEATED.includes(k) ? p.getAll(k).filter(Boolean)
  : (p.get(k) || "").split(",").filter(Boolean);
function setList(p, k, values) {
  p.delete(k);
  if (REPEATED.includes(k)) values.forEach((v) => p.append(k, v));
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
const kindOf = (s) => s.format === "NOVEL" ? "Novel" : s.format === "ONE_SHOT" ? "One-shot" : COUNTRY[s.country_of_origin];

function cardSub(s) {
  const st = s.reading_status, read = s.book_count - s.unread_count;
  if (st === "completed") return "✓ Read";
  if (st === "reading") return `Reading · ${read}/${s.book_count}`;
  if (st === "unread") return `Unread · ${s.book_count} ${s.book_count === 1 ? "book" : "books"}`;
  return STATUS_LABELS[st] || st;
}

/* ---------------- global store + router ---------------- */

document.addEventListener("alpine:init", () => {
  Alpine.store("app", {
    view: null,          // library | series | continue | attention | settings | read
    id: null,            // series id for the series page
    params: {},          // query params of the current route
    query: "",           // raw query string (library filters)
    sheet: null,         // which bottom sheet is open: "filter" | "sort" | null
    folderPicker: false,
    fix: { open: false, seriesId: null, onDone: null },
    edit: { open: false, seriesId: null, onDone: null, tab: "info" },
    attentionCount: 0,
    att: null,           // the Needs attention lists (loaded by refreshAttentionCount)
    showIgnored: false,
    sub: null,           // second part of the route, e.g. "display" in #/settings/display
    providers: [],
    providerName(id) { return this.providers.find((p) => p.id === id)?.name || id; },
    libraryDirty: 0,     // bump to make the library reload (e.g. after a match)
    progressDirty: 0,    // bumped after the reader closes and its last progress save is done
    update: null,        // GET /api/update: running version, newest version on GitHub
    updating: null,      // an install in progress: { state, message, error }
    openFix(seriesId, onDone) { this.fix = { open: true, seriesId, onDone }; },
    openEdit(seriesId, onDone, tab = "info") { this.edit = { open: true, seriesId, onDone, tab }; },
  });

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
    if (view !== lastView || newId !== app.id || (view === "settings" && newSub !== app.sub)) {
      if (lastView === "library") libraryScroll = scrollY;
      const y = view === "library" ? libraryScroll : 0;
      Alpine.nextTick(() => requestAnimationFrame(() => scrollTo(0, y)));  // after Alpine shows the page
    }
    app.view = view;
    app.id = newId;
    app.sub = newSub;
    if (view === "settings" && lastView !== "settings") refreshAttentionCount();  // sidebar counts
    app.params = Object.fromEntries(params);
    app.query = view === "library" ? params.toString() : app.query;
    lastView = view;
  }
  window.addEventListener("hashchange", route);

  /* the badge polls a cheap count; the full lists (all books, for gaps) only load inside Settings */
  async function refreshAttentionCount() {
    const app = Alpine.store("app");
    try {
      if (app.view === "settings") {
        app.att = await api(`/api/attention${app.showIgnored ? "?ignored=true" : ""}`);
        app.attentionCount = app.att.count;
      } else {
        app.attentionCount = (await api("/api/attention/count")).count;
      }
    } catch {}
  }
  queueMicrotask(async () => {
    await loadSettings().catch(() => {});
    api("/api/providers").then((p) => (Alpine.store("app").providers = p)).catch(() => {});
    route();
    refreshAttentionCount();
    setInterval(refreshAttentionCount, 60000);
    refreshUpdate();
    setInterval(refreshUpdate, 3600000);  // the server asks GitHub once a day; this only reads its answer
  });
  window.refreshAttentionCount = refreshAttentionCount;
});

/* ---------------- updates (toast + Settings → Updates) ---------------- */

async function refreshUpdate() {
  try { Alpine.store("app").update = await api("/api/update"); } catch {}
}

/* Install a version, follow its progress, then reload the page when the new version answers. */
async function installUpdate(sha) {
  const app = Alpine.store("app");
  app.updating = { state: "installing", message: "Starting…", error: null };
  try {
    await api("/api/update/install", { method: "POST", body: { sha } });
  } catch (e) {
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
      app.updating = { state: "error", message: "", error: u.status.error };
      app.update = u;
      return;
    }
    if (restarted && u.status.state === "idle") break;  // came back on the old version
    app.updating = { state: u.status.state, message: u.status.message, error: null };
  }
  app.updating = { state: "error", message: "",
    error: "The new version did not start. MangaVibe went back to the previous one; see the container log." };
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
    get when() { return this.a?.date ? new Date(this.a.date).toLocaleDateString() : ""; },
    later() {
      this.dismissed = `${this.a.sha}|${Date.now()}`;
      try { localStorage.setItem(DISMISS_KEY, this.dismissed); } catch {}
    },
    async skip() {
      this.$store.app.update = await api("/api/update/skip", { method: "POST", body: { sha: this.a.sha } });
      toast("This version is skipped. You'll be told about the next one.", 5000);
    },
    install() { installUpdate(this.a.sha); },
    close() { this.$store.app.updating = null; },
  };
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
    get filterCount() {
      const p = new URLSearchParams(this.$store.app.query);
      return LIST_KEYS.reduce((a, k) => a + getList(p, k).length, 0) + (p.get("fav") ? 1 : 0);
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
    get customSort() { return !!new URLSearchParams(this.$store.app.query).get("sort"); },
    openSheet(kind) {
      if (this.$store.app.view !== "library") location.hash = "#/";
      setTimeout(() => (this.$store.app.sheet = kind));  // after the route change closed other sheets
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

function library() {
  return {
    items: [], cursor: 0, done: false, loading: false, total: null, key: null, stale: false,
    paged: false, page: 1, size: 60, base: null,
    quick: QUICK,
    init() {
      // reload when the filters (URL) change; keep cards + scroll position otherwise
      this.$watch("$store.app.query", () => this.sync());
      this.$watch("$store.app.view", () => this.sync());
      this.$watch("$store.app.libraryDirty", () => { this.key = null; this.sync(); });
      // after reading: update the cards in place (now, or when the library is next shown)
      this.$watch("$store.app.progressDirty", () => { this.stale = true; this.refresh(); });
      this.$watch("$store.app.view", () => this.refresh());
      new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && this.$store.app.view === "library" && !this.paged) this.loadMore();
      }, { rootMargin: "800px" }).observe(this.$refs.sentinel);
    },
    // the filters without the page number: what the API gets besides cursor/limit
    get filters() { const p = new URLSearchParams(this.$store.app.query); p.delete("page"); return p.toString(); },
    sync() {
      if (this.$store.app.view !== "library") return;
      document.title = "MangaVibe";
      const paged = settings.library_listing === "paged";
      const page = paged ? Math.max(1, parseInt(new URLSearchParams(this.$store.app.query).get("page")) || 1) : 1;
      const size = +settings.library_page_size || 60;
      const base = `${paged ? "paged" : "lazy"}:${size}:${this.filters}`, key = `${base}:${page}`;
      if (key === this.key) return;
      const pageChange = paged && base === this.base && this.key !== null;  // same list, other page
      Object.assign(this, { cursor: 0, done: false, loading: false, key, base, paged, page, size });
      if (!pageChange) Object.assign(this, { items: [], total: null });  // a new page keeps the old cards until it arrives
      paged ? this.loadPage(pageChange) : this.loadMore();
    },
    async loadPage(scroll) {
      const key = this.key;
      this.loading = true;
      try {
        const data = await api(`/api/series?cursor=${(this.page - 1) * this.size}&limit=${this.size}&${this.filters}`);
        if (key !== this.key) return;
        if (!data.items.length && data.total && this.page > 1) return this.goPage(this.pages(data.total), true);  // past the end
        Object.assign(this, { items: data.items, total: data.total, done: true });
      } catch (e) {
        toast("Could not load library: " + e.message, 3000, "error");
      } finally {
        if (key === this.key) this.loading = false;
      }
      if (scroll) {
        await this.$nextTick();
        const top = this.$root.getBoundingClientRect().top + scrollY - 70;  // clear the top bar
        if (scrollY > top) scrollTo({ top: Math.max(0, top) });
      }
    },
    /* reload the cards already shown without clearing them, so the scroll position stays */
    async refresh() {
      if (!this.stale || this.$store.app.view !== "library" || this.key === null) return;
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
      return `Page ${this.page} of ${this.pages()} · ${this.total} series`;
    },
    pageSizes: [30, 60, 120],
    async setPageSize(v) {  // the same setting as Settings → Display → Series per page
      await api("/api/settings", { method: "PUT", body: { library_page_size: +v } });
      await loadSettings();
      const p = libParams();
      if (p.has("page")) navigateLibrary(p);  // back to page 1; the query change reloads
      else { this.key = null; this.sync(); }
    },
    goPage(n, replace = false) {
      n = Math.min(Math.max(1, n), this.pages());
      const p = libParams();
      n > 1 ? p.set("page", n) : p.delete("page");
      const url = "#/" + (p.toString() ? "?" + p : "");
      replace ? location.replace(url) : (location.hash = url);  // a new history entry, so Back returns to the last page
    },
    async loadMore() {
      if (this.paged || this.loading || this.done || this.key === null) return;
      this.loading = true;
      const key = this.key;
      try {
        const data = await api(`/api/series?cursor=${this.cursor}&limit=60&${this.filters}`);
        if (key !== this.key) return;  // filters changed while loading
        this.items.push(...data.items);
        this.cursor = data.next_cursor ?? this.cursor;
        this.done = data.next_cursor === null;
        this.total = data.total;
      } catch (e) {
        toast("Could not load library: " + e.message, 3000, "error");
      } finally {
        if (key === this.key) this.loading = false;
      }
      await this.$nextTick();
      if (!this.done && key === this.key && this.$refs.sentinel.getBoundingClientRect().top < innerHeight + 600) this.loadMore();
    },
    get params() { return new URLSearchParams(this.$store.app.query); },
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
    get chips() {
      const p = this.params, out = [], read = getList(p, "read");
      for (const [k, , labels] of FILTER_GROUPS) {
        if (k === "read" && read.length < 2) continue;  // a single reading status shows as a quick button
        for (const v of getList(p, k)) out.push({ k, v, label: labels[v] || v });
      }
      for (const v of getList(p, "genre")) out.push({ k: "genre", v, label: v });
      for (const v of getList(p, "exclude")) out.push({ k: "exclude", v, label: "not " + v, ex: true });
      const sort = p.get("sort");
      if (sort && sort !== "title") out.push({ k: "sort", v: sort,
        label: "Sort: " + (SORTS.find(([k]) => k === sort)?.[1] || sort) + (p.get("order") === "asc" ? " ↑" : "") });
      return out;
    },
    removeChip(c) {
      const p = libParams();
      if (c.k === "sort") { p.delete("sort"); p.delete("order"); }
      else setList(p, c.k, getList(p, c.k).filter((x) => x !== c.v));
      navigateLibrary(p);
    },
    clearAll() {
      const q = this.params.get("q");
      navigateLibrary(new URLSearchParams(q ? { q } : {}));
    },
    get emptyText() {
      return this.filters ? "No series match these filters." : "No series yet. The first scan may still be running; see Settings.";
    },
    cardSub,
  };
}

/* ---------------- filter sheet ---------------- */

function filterSheet() {
  return {
    tags: [], tagQuery: "", groups: FILTER_GROUPS,
    init() {
      this.$watch("$store.app.sheet", async (sheet) => {
        if (sheet === "filter") this.tags = await api("/api/tags").catch(() => this.tags);
      });
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
      this.apply((p) => { for (const k of [...LIST_KEYS, "fav"]) p.delete(k); });
    },
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

/* ---------------- sort sheet ---------------- */

function sortSheet() {
  return {
    sorts: SORTS,
    get p() { return new URLSearchParams(this.$store.app.query); },
    get sort() { return this.p.get("sort") || "title"; },
    get asc() { return this.p.get("order") === "asc" || (!this.p.get("order") && this.sort === "title"); },
    get orderLabels() {
      return {
        title: ["A → Z", "Z → A"], score: ["Lowest first", "Highest first"],
        unread: ["Fewest first", "Most first"],
      }[this.sort] || ["Oldest first", "Newest first"];
    },
    setSort(v) {
      const p = libParams();
      v === "title" ? p.delete("sort") : p.set("sort", v);
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

/* ---------------- continue reading ---------------- */

function continueView() {
  return {
    items: null,
    init() {
      this.$watch("$store.app.view", (v) => v === "continue" && this.load());
      this.$watch("$store.app.progressDirty", () => this.load());
      this.load();
    },
    async load() {
      if (this.$store.app.view !== "continue") return;
      document.title = "Continue reading";
      this.items = await api("/api/continue-reading");
    },
  };
}

/* ---------------- series page ---------------- */

function seriesView() {
  return {
    s: null, books: [], tab: "", loadedFor: null,
    showAllTags: false, showSpoilers: false, descLong: false, descOpen: false,
    statusLabels: STATUS_LABELS, pubLabels: PUB_LABELS,
    init() {
      const check = () => {
        const app = this.$store.app;
        if (app.view !== "series") return;
        const key = `${app.id}|${app.params.tab || ""}`;
        if (key !== this.loadedFor) this.load();
      };
      this.$watch("$store.app.view", check);
      this.$watch("$store.app.id", check);
      this.$watch("$store.app.params", check);
      this.$watch("$store.app.progressDirty", () => { this.loadedFor = null; check(); });  // back from the reader
      check();
    },
    async load() {
      const app = this.$store.app, id = app.id, tab = app.params.tab || "";
      this.loadedFor = `${id}|${tab}`;
      const [s, books] = await Promise.all([api(`/api/series/${id}`),
        api(`/api/series/${id}/books${tab === "files" ? "?all=1" : ""}`)]);
      if (this.loadedFor !== `${id}|${tab}`) return;
      const sameSeries = this.s?.id === s.id;
      Object.assign(this, { s, books, tab });
      if (!sameSeries) {  // reloading the same series keeps "show more", tags, … as they were: no jumping
        Object.assign(this, { showAllTags: false, showSpoilers: false, descOpen: false });
      }
      document.title = s.title;
      await this.$nextTick();
      const d = this.$refs.desc;  // scrollHeight is the full text height, even while it's shortened
      this.descLong = !!d && d.scrollHeight > parseFloat(getComputedStyle(d).lineHeight) * 6.5;
    },
    reload() { this.loadedFor = null; this.load(); },

    get alt() {
      const t = this.s.titles;
      return [t.english, t.romaji, t.native].filter((x, i, all) => x && x !== this.s.title && all.indexOf(x) === i);
    },
    get facts() {
      const s = this.s;
      return [s.start_year, s.average_score && `★ ${s.average_score}%`, PUB_LABELS[s.publication_status], kindOf(s)].filter(Boolean);
    },
    get details() {
      const s = this.s;
      return [
        ["Author", s.authors.map((a) => a.name + (a.role ? ` (${a.role})` : "")).join("\n")],
        ["Year", s.start_year], ["Type", kindOf(s)], ["Status", PUB_LABELS[s.publication_status]],
        ["Length", [s.volumes_total && `${s.volumes_total} volumes`, s.chapters_total && `${s.chapters_total} chapters`].filter(Boolean).join(" · ")],
        ["Score", s.average_score && `${s.average_score}%`],
        ["In library", `${s.book_count} books · ${s.unread_count} unread`],
        ...s.plugin_fields.map((f) => [f.label, typeof f.value === "object" ? JSON.stringify(f.value) : f.value]),
        ["Folder", s.folder_path],
      ].filter(([, v]) => v);
    },
    get genres() { return this.s.tags.filter((t) => t.kind === "genre"); },
    get tagList() { return this.s.tags.filter((t) => t.kind !== "genre"); },
    get visibleTags() {
      return this.tagList.filter((t, i) => (this.showAllTags || i < 12) && (!t.spoiler || this.showSpoilers));
    },
    get spoilerCount() { return this.tagList.filter((t) => t.spoiler).length; },
    get matchLabel() {
      const s = this.s;
      return { auto: `Matched automatically · ${s.match_confidence ?? "?"}%`, manual: "Matched by you",
        failed: "No confident match", ignored: "Not matched (your choice)", unmatched: "Looking for metadata…" }[s.match_status] || "";
    },
    tagUrl(name) { return libraryUrl({ genre: name }); },

    async patch(body, reloadAfter = false) {
      await api(`/api/series/${this.s.id}`, { method: "PATCH", body });
      this.$store.app.libraryDirty++;
      reloadAfter ? this.reload() : toast("Saved");
    },
    setStatus(v) { this.patch(v ? { reading_status: v } : { clear_reading_status: true }); },
    /* ---- ⋯ menu and the reading direction dialog ---- */
    menuOpen: false,
    readerOpen: false,
    readerModes: [["", "Default", "Follow Settings → Reader"], ["rtl", "Right to left", "Manga"],
      ["ltr", "Left to right", "Comics, some manhua"], ["vertical", "Vertical", "Webtoons, long strips"]],
    get readerModeLabel() {
      return { rtl: "Right to left", ltr: "Left to right", vertical: "Vertical" }[this.s?.reader_mode] || "Default";
    },
    async setReader(v) {
      await api(`/api/series/${this.s.id}`, { method: "PATCH", body: { reader_mode: v || "default" } });
      this.s.reader_mode = v || null;
      this.readerOpen = false;
      toast(`Reading direction: ${this.readerModeLabel}`);
    },
    async toggleFav() {  // flip at once, save in the background; nothing else on the page changes
      const on = !this.s.favorite;
      this.s.favorite = on;
      try {
        await api(`/api/series/${this.s.id}`, { method: "PATCH", body: { favorite: on } });
        this.$store.app.libraryDirty++;
      } catch (e) {
        this.s.favorite = !on;
        toast(e.message, 6000, "error");
      }
    },
    async refreshMeta(btn) {
      btn.disabled = true;
      btn.textContent = "Refreshing…";
      try { await api(`/api/series/${this.s.id}/refresh`, { method: "POST" }); toast("Metadata refreshed"); this.reload(); }
      catch (e) { toast(e.message, 6000, "error"); btn.disabled = false; btn.textContent = "Refresh metadata"; }
    },
    fixMatch() { this.$store.app.openFix(this.s.id, () => this.reload()); },
    editInfo(tab = "info") { this.$store.app.openEdit(this.s.id, () => this.reload(), tab); },
    async runAction(a) {
      if (a.confirm && !confirm(a.confirm)) return;
      try { toast((await api(`/api/series/${this.s.id}/actions/${a.plugin}/${a.key}`, { method: "POST" })).message, 6000); }
      catch (e) { toast(e.message, 6000, "error"); }
    },
    setTab(t) { location.replace(`#/series/${this.s.id}${t ? "?tab=" + t : ""}`); },

    bookState(b) {
      if (b.error) return "";
      return (b.completed ? "Read" : b.page ? `Page ${b.page + 1} of ${b.page_count}` : `${b.page_count} pages`)
        + (b.duplicate ? " · duplicate" : "");
    },
    async markAll(read) {
      if (!read && !confirm("Clear progress for every book in this series? It will also be removed from Continue reading.")) return;
      await api(`/api/series/${this.s.id}/${read ? "mark-read" : "mark-unread"}`, { method: "POST" });
      this.$store.app.libraryDirty++;
      this.reload();
    },
    async toggleBook(b) {
      await api(`/api/books/${b.id}/progress`, { method: "PUT",
        body: { page: b.completed ? 0 : b.page_count - 1, completed: !b.completed } });
      this.$store.app.libraryDirty++;
      this.reload();
    },
    async readUpTo(b) {
      await api(`/api/series/${this.s.id}/mark-read?before_book=${b.id}`, { method: "POST" });
      await api(`/api/books/${b.id}/progress`, { method: "PUT", body: { page: 0, completed: true } });
      this.$store.app.libraryDirty++;
      this.reload();
    },
    /* ---- manual chapter numbers for one file (Files tab) ---- */
    editing: null,
    form: {},
    startEdit(b) {
      const o = b.override, pick = (manual, auto) => (manual ?? auto ?? "");
      this.form = {
        volume: pick(o.volume, b.volume),
        chapter: pick(o.chapter, b.chapter),
        chapter_end: o.chapter !== null ? (o.chapter_end ?? "") : (b.chapter_end ?? ""),
        extra: o.extra ?? b.extra,
        hidden: b.hidden_by_owner,
      };
      this.editing = b.id;
    },
    async saveEdit(b) {
      const num = (v) => (v === "" || v === null ? null : +v);
      const f = this.form;
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
        return toast("Enter the first chapter of the range");
      }
      try {
        await api(`/api/books/${b.id}`, { method: "PATCH", body });
        this.editing = null;
        toast("Saved. The list is re-ordered.");
        this.$store.app.libraryDirty++;
        this.reload();
      } catch (e) { toast(e.message, 6000, "error"); }
    },
    async resetEdit(b) {
      await api(`/api/books/${b.id}`, { method: "PATCH", body: { reset: true } });
      this.editing = null;
      toast("Back to the numbers from the file name");
      this.reload();
    },

    /* ---- re-read this series' folder ---- */
    rescanning: false,
    async rescan() {
      this.rescanning = true;
      try {
        const r = await api(`/api/series/${this.s.id}/rescan`, { method: "POST" });
        if (r.removed) {
          toast("The series folder is gone, so the series was removed from the library");
          this.$store.app.libraryDirty++;
          refreshAttentionCount();
          location.hash = "#/";
          return;
        }
        toast(`Rescanned: ${r.books} files` + (r.errors ? `, ${r.errors} with errors` : ""));
        this.$store.app.libraryDirty++;
        refreshAttentionCount();
        this.reload();
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.rescanning = false; }
    },
    progressPct(b) { return b.page_count ? Math.round((b.page + 1) / b.page_count * 100) : 0; },
    mb(n) { return (n / 1048576).toFixed(1) + " MB"; },
    n(v) { return v === null || v === undefined ? "–" : v; },
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
        if (fallback && e.message.includes("unavailable") && other) {
          this.$store.app.providers = await api("/api/providers");
          this.provider = other.id;
          this.searching = false;
          return this.search(false);
        }
      } finally { this.searching = false; }
    },
    async use(c) {
      this.busy = true;
      try {
        await api(`/api/series/${this.s.id}/match`, { method: "POST", body: { provider: this.provider, provider_id: c.provider_id } });
        this.done("Matched: " + c.title);
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.busy = false; }
    },
    async applyUrl() {
      try {
        await api(`/api/series/${this.s.id}/match`, { method: "POST", body: { url: this.url.trim() } });
        this.done("Matched");
      } catch (e) { toast(e.message, 6000, "error"); }
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
      await api("/api/attention/ignore", { method: "POST", body: { key, ignore: on } });
      toast(on ? "Hidden from this list. “Show ignored” brings it back." : "Restored");
      refreshAttentionCount();
    },
    async ignoreAll(g) {
      for (const f of g.files) {
        if (!f.ignored) await api("/api/attention/ignore", { method: "POST", body: { key: f.key, ignore: true } });
      }
      toast(`${g.files.length} files hidden from this list`);
      refreshAttentionCount();
    },
    toggleIgnored() {
      this.$store.app.showIgnored = !this.$store.app.showIgnored;
      refreshAttentionCount();
    },
    async hideFile(f) {
      await api(`/api/books/${f.book_id}`, { method: "PATCH", body: { hidden: !f.hidden } });
      toast(f.hidden ? "File shown again" : "File hidden from reading (it stays on disk)");
      this.afterChange();
    },
    async use(x, c) {
      try {
        await api(`/api/series/${x.series_id}/match`, { method: "POST", body: { provider: c.provider, provider_id: c.provider_id } });
        toast("Matched");
        this.afterChange();
      } catch (e) { toast(e.message, 6000, "error"); }
    },
    fix(x) { this.$store.app.openFix(x.series_id, () => refreshAttentionCount()); },
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
        toast(r.removed ? "The series folder is gone, so the series was removed from the library"
          : `Rescanned: ${r.books} files` + (r.errors ? `, ${r.errors} with errors` : ""));
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

const SETTINGS_NAV = [["library", "Library folders", "📁"], ["display", "Display", "🎨"],
  ["reader", "Reader", "📖"], ["metadata", "Metadata", "🏷"], ["plugins", "Plugins", "🧩"], ["updates", "Updates", "⟳"]];

function settingsView() {
  return {
    nav: SETTINGS_NAV,
    attNav: ATT_TABS,
    get section() { return this.$store.app.sub || "library"; },
    go(sec) { location.replace(`#/settings/${sec}`); },
    attCount(k) { return this.$store.app.att?.[k]?.filter((x) => !x.ignored).length || 0; },
    st: {}, libs: [], plugins: [], providers: [], pluginErrors: {}, scan: {}, scanText: "", metaText: "", timers: [],
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
      document.title = "Settings";
      const [libs, plug, providers] = await Promise.all([api("/api/libraries"), api("/api/plugins"),
        api("/api/metadata/providers")]);
      // selects compare strings, so keep every value as text here; save() converts back
      this.st = Object.fromEntries(Object.entries(settings).map(([k, v]) => [k, String(v)]));
      Object.assign(this, { libs, providers, plugins: plug.plugins, pluginErrors: plug.errors });
      this.stop();
      this.pollScan();
      this.pollMeta();
    },
    stop() { this.timers.forEach(clearTimeout); this.timers = []; },
    async save(key) {
      let v = this.st[key];
      if (v === "true" || v === "false") v = v === "true";
      else if (typeof v === "string" && /^\d+$/.test(v)) v = +v;
      await api("/api/settings", { method: "PUT", body: { [key]: v } });
      await loadSettings();
      if (["title_language", "library_listing", "library_page_size"].includes(key)) this.$store.app.libraryDirty++;
      toast("Saved");
    },
    async rescan(lib) {
      const r = await api(lib ? `/api/libraries/${lib.id}/scan` : "/api/scan", { method: "POST" })
        .catch((e) => { toast(e.message, 6000); return null; });
      if (!r) return;
      if (!r.started) toast("A scan is already running. Try again when it has finished.", 5000);
      this.stop(); this.pollScan();
    },
    // progress for one folder's row while that folder is being scanned
    libScanText(l) {
      const st = this.scan;
      if (!st.running || (st.library_id != null ? st.library_id !== l.id : st.library !== l.name)) return "";
      return st.library_id != null ? `Scanning ${st.done}/${st.total}…` : "Scanning…";
    },
    async removeLibrary(l) {
      if (!confirm(`Remove "${l.name}" (${l.root_path}) from MangaVibe?

Your files are not touched. `
        + `Its ${l.series} series and their reading progress are removed from the app.`)) return;
      await api(`/api/libraries/${l.id}`, { method: "DELETE" });
      toast("Folder removed");
      this.$store.app.libraryDirty++;
      refreshAttentionCount();
      this.load();
    },
    checking: false,
    get upd() { return this.$store.app.update; },
    versionText(v) {
      if (!v?.sha) return "Built-in version (from the Docker image)";
      return v.short + (v.date ? ` · ${new Date(v.date).toLocaleDateString()}` : "")
        + (v.message ? ` · ${v.message}` : "") + (v.builtin ? " (built into the image)" : "");
    },
    get checkedText() {
      const at = this.upd?.status?.checked_at;
      return at ? `Last checked ${new Date(at * 1000).toLocaleString()}` : "Not checked yet";
    },
    async checkUpdate() {
      this.checking = true;
      try {
        this.$store.app.update = await api("/api/update/check", { method: "POST" });
        const u = this.$store.app.update;
        if (u.status.error) toast(u.status.error, 6000, "error");
        else toast(u.available ? "A new version is available" : "You have the newest version");
      } catch (e) { toast(e.message, 6000, "error"); } finally { this.checking = false; }
    },
    async skipUpdate() {
      this.$store.app.update = await api("/api/update/skip", { method: "POST", body: { sha: this.upd.available.sha } });
    },
    async unskipUpdate() { this.$store.app.update = await api("/api/update/skip", { method: "DELETE" }); },
    installUpdate() { installUpdate(this.upd.available.sha); },
    async loadProviders() { this.providers = await api("/api/metadata/providers").catch(() => this.providers); },
    async togglePlugin(p) {
      await api(`/api/plugins/${p.id}`, { method: "PUT", body: { enabled: p.enabled } });
      if (p.provider) this.loadProviders();
    },
    async reloadPlugins() {
      const r = await api("/api/plugins/reload", { method: "POST" });
      this.plugins = r.plugins; this.pluginErrors = r.errors;
      this.loadProviders();
    },
    async pollScan() {
      const st = await api("/api/scan/status").catch(() => null);
      if (!st || this.$store.app.view !== "settings") return;
      const finished = this.scan.running && !st.running;
      this.scan = st;
      if (finished) this.libs = await api("/api/libraries").catch(() => this.libs);  // new series counts
      this.scanText = st.running ? `Scanning ${st.done}/${st.total}: ${st.current || ""}`
        : st.finished_at ? `Last scan finished ${new Date(st.finished_at).toLocaleString()}`
          + (st.new_books ? ` · ${st.new_books} new books` : "") + (st.errors ? ` · ${st.errors} errors` : "") : "";
      if (st.running) { this.$store.app.libraryDirty++; this.timers.push(setTimeout(() => this.pollScan(), 1000)); }
    },
    async pollMeta() {
      const st = await api("/api/metadata/status").catch(() => null);
      if (!st || this.$store.app.view !== "settings") return;
      const parts = [];
      if (st.current) parts.push(`Matching: ${st.current}`);
      if (st.queued) parts.push(`${st.queued} series waiting`);
      if (st.matched || st.failed) parts.push(`${st.matched} matched, ${st.failed} need attention`);
      if (st.providers_down_until) parts.push(`AniList/MangaUpdates not reachable; retrying at ${new Date(st.providers_down_until).toLocaleTimeString()}`);
      this.metaText = parts.join(" · ") || "Idle. New series are matched automatically after each scan.";
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
        toast("Folder added. Scanning…");
        this.close();
        this.$store.app.libraryDirty++;
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
        genres: s.tags.filter((t) => t.kind === "genre").map((t) => t.name).join(", "),
        tags: s.tags.filter((t) => t.kind !== "genre").map((t) => t.name).join(", "),
        links: lines(s.links.map((l) => `${l.site} | ${l.url}`)),
      };
      this.orig = { ...this.f };
    },
    close() { this.clearFile(); this.$store.app.edit.open = false; },
    locked(field) { return this.s?.locked_fields.includes(field); },
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
      if (changed("genres")) b.genres = list(f.genres, ",");
      if (changed("tags")) b.tags = list(f.tags, ",");
      if (changed("links")) b.links = list(f.links, "\n").map((line) => {
        const [site, ...rest] = line.split("|");
        return rest.length ? { site: site.trim(), url: rest.join("|").trim() } : { site: "Link", url: line.trim() };
      });
      return b;
    },
    async save() {
      const body = this.body();
      if (!Object.keys(body).length) return this.close();
      this.saving = true;
      try {
        await api(`/api/series/${this.s.id}/metadata`, { method: "PUT", body });
        toast("Saved. Your changes are kept when metadata is refreshed.");
        this.done();
      } catch (e) { toast(e.message, 7000, "error"); } finally { this.saving = false; }
    },
    async unlockAll() {
      if (!confirm("Let metadata refresh change all fields again? Your edits stay until the next refresh.")) return;
      await api(`/api/series/${this.s.id}/metadata`, { method: "PUT", body: { unlock: this.s.locked_fields } });
      toast("Unlocked");
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
      if (!file.type.startsWith("image/")) return toast("Please choose an image file");
      if (file.size > 10 * 1024 * 1024) return toast("The image is larger than 10 MB");
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
        toast("Poster changed");
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
        toast("Poster changed");
        this.done();
      } catch (e) { toast(e.message, 7000, "error"); }
    },
  };
}
