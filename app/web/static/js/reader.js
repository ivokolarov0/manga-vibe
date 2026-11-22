/* The reader host: plain JavaScript (touch, preloading and scroll tracking are direct browser work, so
   it stays outside Alpine). The router calls Reader.open/close.

   It keeps what every reader shares: opening and leaving a book, the bars and the reading options,
   saving progress, the next and previous book, the end card, plugin overlays, plugin buttons, read
   aloud, fullscreen, the wake lock and the Android keys. The pages are drawn by the plugin doing the
   reader:images job (built in: app/plugins/builtin/image_reader/, Plugin API 24), and books the page
   reader can't show (light novels) by a plugin's reader of their own (Plugin API 14). */
"use strict";

const Reader = (() => {

const R = { book: null, page: 0, mode: "paged", saveTimer: null, wake: null, firstTap: true,
  cameFromApp: false, hideTimer: null, sliding: false, hovering: false, drawer: false,
  ov: null,    // plugin overlays (see "plugin overlays" below)
  img: null,   // the reader:images plugin showing the book (its registered reader)
  own: null,   // a plugin's reader of its own (a light novel)
  opening: 0 };  // counts openReader() calls, so a slow answer for an earlier one is dropped
const MODES = { paged: "Pages", vertical: "Vertical" };
// plugin overlay boxes: background opacity in % and the outline around the text (reader settings)
const OV_OPACITY = { 100: "Solid", 75: "75%", 50: "50%", 25: "25%", 0: "See-through" };
const OV_OUTLINE = { off: "Off", thin: "Thin", thick: "Thick" };
const OV_STROKE = { off: "0px", thin: "0.12em", thick: "0.22em" };

/* the value after `cur` in a {value: label} list, wrapping around */
function cycle(list, cur) {
  const keys = Object.keys(list);
  return keys[(keys.indexOf(cur) + 1) % keys.length];
}

const svg = (body) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ICONS = {
  // three lines that fold into an × (reader.css animates them when the drawer is open)
  burger: svg(`<path class="bl bl-1" d="M4 6h16"/><path class="bl bl-2" d="M4 12h16"/><path class="bl bl-3" d="M4 18h16"/>`),
  back: svg(`<path d="M19 12H5M11 6l-6 6 6 6"/>`),
  chPrev: svg(`<path d="M18 6l-7 6 7 6V6zM6 6v12"/>`),
  chNext: svg(`<path d="M6 6l7 6-7 6V6zM18 6v12"/>`),
  close: svg(`<path d="M6 6l12 12M18 6L6 18"/>`),
  mode: svg(`<path d="M4 7h13l-3-3M20 17H7l3 3"/>`),
  fullscreen: svg(`<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>`),
  fullscreenExit: svg(`<path d="M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5"/>`),
  settings: svg(`<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>`),
  overlay: svg(`<path d="M4 5h16v10H9l-5 4V5z"/><path d="M8 9h8M8 12h5"/>`),
  plugin: svg(`<path d="M9 3v4M15 3v4M7 7h10v5a5 5 0 0 1-10 0V7zM12 17v4"/>`),
  speak: svg(`<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>`),
  speed: svg(`<path d="M5 18a8 8 0 1 1 14 0"/><path d="M12 14l4-5"/>`),
  autoTurn: svg(`<path d="M5 6l6 6-6 6M13 6l6 6-6 6"/>`),
  topBar: svg(`<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>`),
};

/* change reader settings: applied at once (the caller redraws what they change), saved for all devices
   (a promise: true when saved) */
async function saveSettings(change) {
  Object.assign(settings, change);
  try {
    await api("/api/settings", { method: "PUT", body: change });
    return true;
  } catch (e) {
    toast(e.message, 6000, "error");
    return false;
  }
}

/* ---------------- the top bar while reading (Settings → Reader → Top bar) ----------------
   hide: it goes with the other controls. always: it stays up. room: it stays up while no page picture
   reaches under the title, e.g. a fitted page on a tall phone screen; in vertical mode that is the page at
   the top of the screen. While the controls are away it lets every tap through to the page (reader.css). */
// the same words as Settings → Reader (SETTING_ROWS.reader in app.js)
const TOP_BAR = { hide: "Hide with the controls", always: "Always visible", room: "When there's room" };
function topBarMode() { return settings.reader_top_bar in TOP_BAR ? settings.reader_top_bar : "hide"; }

let topBarFrame = 0;
function topBarUpdate() {
  cancelAnimationFrame(topBarFrame);
  topBarFrame = requestAnimationFrame(() => {
    const bar = $("#reader .chrome.top");
    if (!bar || !R.book) return;
    const mode = topBarMode();
    $("#reader").classList.toggle("top-stay", mode === "always" || (mode === "room" && pageLeavesRoom(bar)));
  });
}

/* no page on screen reaches under the bar, its fade included (opacity doesn't move it, so this works
   while it's hidden). A page still loading counts as reaching it, so the bar doesn't blink up first. */
function pageLeavesRoom(bar) {
  const limit = bar.getBoundingClientRect().bottom;
  const covers = (r) => r.height > 0 && r.top < limit && r.bottom > 0 && r.right > 0 && r.left < innerWidth;
  const cur = imgCall("pageBox", R.page);
  if (cur instanceof HTMLImageElement && !(cur.complete && cur.naturalHeight)) return false;
  if (cur && covers(cur.getBoundingClientRect())) return false;
  // vertical: the pages above the middle one, back to the first that ends above the screen (short
  // pages: many can be on screen). Paged mode has no box for them.
  for (let i = R.page - 1; i >= 0; i--) {
    const box = imgCall("pageBox", i);
    if (!box) break;
    const r = box.getBoundingClientRect();
    if (covers(r)) return false;
    if (r.height > 0 && r.bottom <= 0) break;
  }
  return true;
}

function topBarButton() {
  const b = $("#r-topbar");
  if (b) b.querySelector("b").textContent = TOP_BAR[topBarMode()];
}

function setTopBar(mode) {
  saveSettings({ reader_top_bar: mode });
  topBarButton();
  topBarUpdate();
  restartHideTimer();
}

// what moves or resizes the page: scrolling (vertical), a page loading, zoom and pan (their
// transitions end, or the finger lets go), the wheel, keys and the window
for (const type of ["scroll", "load", "transitionend", "pointerup", "pointermove", "wheel", "keydown"]) {
  document.addEventListener(type, (e) => {
    if (topBarMode() !== "room" || !R.book || R.own) return;
    if (type === "pointermove" && !e.buttons) return;
    // scrolls, loads and transitions elsewhere (the drawer, the bars) don't move the page
    if (["scroll", "load", "transitionend"].includes(type) && !e.target?.closest?.("#r-pages")) return;
    topBarUpdate();
  }, { passive: true, capture: true });
}
addEventListener("resize", () => { if (R.book && !R.own) topBarUpdate(); });

/* the hand the reader is held in (Settings → Reader): back, fullscreen and the options button sit on
   that side, and the options drawer slides in from it */
function drawerSide() { return settings.reader_drawer_side === "left" ? "left" : "right"; }

/* the ☰ button by the slider opens the drawer and folds into × to close it again */
function menuButton() {
  const b = $("#r-menu");
  if (!b) return;
  const label = R.drawer ? "Close options" : "Reading options";
  b.classList.toggle("open", R.drawer);
  b.title = label;
  b.setAttribute("aria-label", label);
  b.setAttribute("aria-expanded", R.drawer);
}

function setDrawer(open) {
  R.drawer = open;
  $("#reader").classList.toggle("drawer-open", open);
  menuButton();
  const d = $("#r-drawer");
  d.inert = !open;
  if (open) {
    R.hovering = false;
    clearTimeout(R.hideTimer);
    autohidePicker();  // the settings may have been loaded again since (another account)
  } else {
    if (R.panel) openPanel(null);
    if (d.contains(document.activeElement)) document.activeElement.blur();
    restartHideTimer();
  }
}

/* ---------------- drawer panels ----------------
   The drawer keeps one row per thing (the reader's Page look, each plugin); a row opens a panel of its
   own that slides down over the drawer, with that thing's switches and buttons. Its × (or Escape, the
   Android back button, a tap on the drawer below) returns to the drawer. */

/* the plugins with something in the reader: [{id, name, overlay: {label} | null, actions: [index in book.actions]}] */
function drawerPlugins(book) {
  const out = new Map();
  const get = (id, name) => out.get(id) || out.set(id, { id, name: name || id, overlay: null, actions: [] }).get(id);
  for (const o of book.overlays) get(o.plugin, o.name).overlay = o;
  book.actions.forEach((a, i) => get(a.plugin, a.name).actions.push(i));
  return [...out.values()];
}

function drawerPanel(key, title, body) {
  return `<aside class="d-panel" data-panel-of="${esc(key)}" aria-label="${esc(title)}" inert>
    <div class="d-row d-panel-head">
      <div class="d-title">${esc(title)}</div>
      <button class="icon" data-panel-close aria-label="Back to the reading options" title="Close">${ICONS.close}</button>
    </div>${body}</aside>`;
}

/* open the panel `key` (null: back to the drawer) */
function openPanel(key) {
  const was = R.panel;
  R.panel = key;
  for (const p of $("#reader").querySelectorAll(".d-panel")) {
    const on = p.dataset.panelOf === key;
    p.classList.toggle("open", on);
    p.inert = !on;
  }
  $("#r-drawer")?.classList.toggle("under-panel", !!key);
  if (key) {
    sayButtons();  // the voice list may have come since
    $(`#reader .d-panel.open [data-panel-close]`)?.focus({ preventScroll: true });
  } else if (was) {
    $(`#reader [data-panel="${CSS.escape(was)}"]`)?.focus({ preventScroll: true });  // back where you were
  }
  restartHideTimer();
}

/* ---------------- a plugin's reader (Plugin API 14) ----------------
   Books the page reader can't show (light novels: format "epub-text") open in the reader a plugin
   registered with MangaVibe.registerReader(). It gets the reader's element to fill and `ctl`; this file
   still owns opening, leaving, saving progress, the Android back and volume keys and the wake lock. */
const PLUGIN_FORMATS = ["epub-text"];  // formats only a plugin's reader shows

function openPluginReader(el, book, startPage) {
  const own = book.reader && window.pluginReader?.(book.reader, book.format);
  if (!own) {
    el.innerHTML = `<div class="endcard"><p>${book.reader
      ? "The reader for this book didn't load. Reload the page, or check its plugin in Settings → Plugins."
      : "This is a text book (a light novel). Add a reader for text books in Settings → Plugins to read it."}</p>
      <a class="btn" href="#/series/${book.series_id}">Back</a></div>`;
    return;
  }
  const page = startPage !== null ? +startPage : (book.completed ? 0 : book.page);
  Object.assign(R, { book, own, mode: "plugin", page: Math.max(0, Math.min(page, book.page_count - 1)) });
  ovReset(null);  // pictures' overlays don't apply to text
  history.replaceState(history.state, "", `#/read/${book.id}`);
  document.title = `${book.label} · ${book.series_title}`;
  el.className = "plugin-reader";
  el.innerHTML = "";
  const ctl = {
    page: R.page,  // where to start: the section (page) from 0
    // the place being read: section `page`; completed true or false marks the book read or unread
    // (reaching the last section doesn't, as the page reader does). Saved a moment later, or now.
    save(page, completed = null) {
      if (R.book !== book) return Promise.resolve();
      R.page = Math.max(0, Math.min(+page || 0, book.page_count - 1));
      if (completed === null) return saveProgress();
      clearTimeout(R.saveTimer);
      return api(`/api/books/${book.id}/progress`, { method: "PUT", body: { page: R.page, completed } })
        .then((p) => { book.completed = p.completed; return p; });
    },
    leave: () => leaveReader(),
    openBook: (which, page = null) => openBook(which, page),
    fullscreen: () => toggleFullscreen(),
  };
  try {
    own.open(el, book, ctl);
  } catch (e) {
    console.error("The plugin's reader failed to open", e);
    el.innerHTML = `<div class="endcard"><p>The reader for this book failed: ${esc(e.message)}</p>
      <a class="btn" href="#/series/${book.series_id}">Back</a></div>`;
  }
  requestWakeLock();
  androidApp?.readerState(true, settings.reader_volume_keys !== false);
}

/* the plugin reader's own handler for something (step, goTo, volumeKey, back...), or undefined */
function ownCall(name, ...args) {
  const fn = R.own?.[name];
  if (typeof fn !== "function") return undefined;
  try { return fn.apply(R.own, args); } catch (e) { console.error(`The plugin's reader failed in ${name}`, e); }
  return undefined;
}

/* ---------------- the image reader (reader:images, Plugin API 24) ----------------
   The plugin doing the job draws the pages into #r-pages; the server says who that is (book.image_readers:
   the plugin doing it, then the built-in one as the fallback, whose script is always in the page). */

/* the image reader's handler for something (step, goTo, key...), or undefined */
function imgCall(name, ...args) {
  const fn = R.img?.[name];
  if (typeof fn !== "function") return undefined;
  try { return fn.apply(R.img, args); } catch (e) { console.error(`The image reader failed in ${name}`, e); }
  return undefined;
}

/* the modes the image reader draws (the mode button only switches between these) */
function imgModes() {
  const m = (R.img?.modes || ["paged", "vertical"]).filter((x) => x in MODES);
  return m.length ? m : ["paged"];
}

/* what the image reader gets from the host */
function imageCtl(book) {
  const mine = () => R.book === book;
  return {
    page: R.page,          // where to start, from 0
    mode: R.mode,          // "paged" or "vertical"
    settings,              // the reader settings (Settings → Reader): reader_fit, preload, reader_tap_layout...
    // the reader shows page i now: the numbers, progress (saved a moment later) and the overlays follow
    shown(i) { if (mine()) pageShown(i); },
    end() { if (mine()) showEndCard(); },  // forward from the last page: the end card (again: the next book)
    // back from the first page: a toast naming the previous book; back again while it shows opens that
    // book on its last page (the page number is clamped to the book's last)
    start() {
      if (!mine() || !book.prev) return;
      if (R.startToast && R.startToast === toast.timer && $("#toast").classList.contains("show")) {
        $("#toast").classList.remove("show");
        openBook("previous", 1e9);
        return;
      }
      toast(`Start of book. Previous: ${book.prev.label}`);
      R.startToast = toast.timer;  // another toast (or the toast going away) ends it
    },
    // a tap on the page: true when the host took it (it closed the reading options); call it first
    tap(e) {
      if (!mine()) return true;
      if (R.drawer) { setDrawer(false); return true; }
      maybeAutoFullscreen(e);
      return false;
    },
    menu() { if (mine()) toggleMenu(); },                          // the bars and the reading options
    reading() { if (mine() && uiShown() && !controlsBusy()) hideUi(); },  // reading on: the bars go away
    keepControls() { if (mine()) restartHideTimer(); },             // its option was used: the bars stay
    // change reader settings: applied at once, saved for all devices (a promise)
    saveSettings,
    reopen() { if (mine()) openReader(book.id, R.page); },  // draw the book again (e.g. another fit)
    openBook: (which, page = null) => openBook(which, page),
  };
}

/* open the book in the first image reader that opens; false if none did */
function openImageReader(book) {
  const ids = book.image_readers || ["image-reader"];
  for (const [i, id] of ids.entries()) {
    // the fallback (after the first) is the built-in reader, even switched off
    const impl = window.pluginReader?.(id, "images", i > 0);
    if (!impl) continue;
    R.img = impl;
    if (!imgModes().includes(R.mode)) R.mode = imgModes()[0];  // a mode it doesn't draw: its first
    $("#reader").classList.remove("paged", "vertical");
    $("#reader").classList.add(R.mode);
    try {
      impl.open($("#r-pages"), book, imageCtl(book));
      return true;
    } catch (e) {
      console.error(`The image reader ${id} failed to open`, e);
      try { impl.close?.(); } catch {}
    }
  }
  R.img = null;
  return false;
}

async function openReader(bookId, startPage, cameFromApp = R.cameFromApp) {
  R.cameFromApp = cameFromApp;
  if (R.own) { ownCall("close"); R.own = null; }  // another book after one a plugin's reader showed
  if (R.img) { imgCall("close"); R.img = null; }
  const el = $("#reader");
  el.hidden = false;
  // a spinner, and a way out while it waits (the reader's own Back button comes with the book)
  el.innerHTML = `<div class="loading">Loading…</div>
    <button class="btn loading-back" id="r-back">${ICONS.back} Back</button>`;
  $("#r-back").onclick = () => leaveReader();
  document.body.style.overflow = "hidden";
  const open = ++R.opening;  // a newer open (or leaving) makes this one stale
  let book;
  try { book = await api(`/api/books/${bookId}`, { timeout: LOAD_TIMEOUT }); } catch (e) {
    if (open !== R.opening || el.hidden) return;  // left (or another book) meanwhile
    el.innerHTML = `<div class="endcard"><p>${esc(e.message)}</p>
      <div class="btn-row"><button class="btn primary" id="r-retry">Try again</button><a class="btn" href="#/">Back</a></div></div>`;
    $("#r-retry").addEventListener("click", () => openReader(bookId, startPage, cameFromApp));
    // e.g. a locked library: its plugin asks for the password, then the book opens
    MangaVibe.denied(e, () => openReader(bookId, startPage, cameFromApp));
    return;
  }
  if (open !== R.opening || el.hidden) return;
  if (book.error) { el.innerHTML = `<div class="endcard"><p>${esc(book.error)}</p>
    <a class="btn" href="#/series/${book.series_id}">Back</a></div>`; return; }
  if (book.reader || PLUGIN_FORMATS.includes(book.format)) return openPluginReader(el, book, startPage);
  Object.assign(R, { book, mode: book.reader_mode === "vertical" ? "vertical" : "paged",
    page: startPage !== null ? +startPage : (book.completed ? 0 : book.page) });
  R.page = Math.max(0, Math.min(R.page, book.page_count - 1));
  // drop ?page= so a reload resumes from saved progress (replaceState fires no hashchange)
  history.replaceState(history.state, "", `#/read/${book.id}`);
  document.title = `${book.label} · ${book.series_title}`;
  clearTimeout(R.hideTimer);
  el.className = `ui-hidden drawer-${drawerSide()}` + (R.drawer ? " drawer-open" : "") +
    (topBarMode() === "always" ? " top-stay" : "");  // "room" is measured once the page is there
  ovReset(book);
  R.panel = null;
  const plugins = drawerPlugins(book), canSay = book.overlays.length > 0 && canSpeak();
  el.innerHTML = `
    <div id="r-pages"></div>
    <div class="ov-status" id="r-ov-status" hidden></div>
    <!-- for screen readers (TalkBack, NVDA, VoiceOver): the plugin's text of the page being read -->
    <div class="sr-only" id="r-live" aria-live="polite"></div>
    <div class="read-progress" id="r-progress">
      <span id="rp-cur"></span>
      <div class="rp-track">${Array.from({ length: book.page_count },
        (_, i) => `<i data-p="${i}" title="Page ${i + 1}"></i>`).join("")}</div>
      <span>${book.page_count}</span>
    </div>
    <div class="chrome top">
      <div class="title"><b>${esc(book.series_title)}</b><span>${esc(book.label)}</span>
        <span class="ov-note" id="r-ov-note" hidden></span></div>
    </div>
    <!-- the most used buttons, stacked just above the bottom card on the thumb side (one-handed use) -->
    <div class="chrome rail">
      <button id="r-back" class="icon" aria-label="Back" title="Back">${ICONS.back}</button>
      <button id="r-fs2" class="icon" aria-label="Fullscreen" title="Fullscreen">${ICONS.fullscreen}</button>
    </div>
    <!-- a floating card; the options button is at the thumb end -->
    <div class="chrome bottom">
      <!-- chapter buttons (only when there is one to go to), page numbers and slider: back on the left,
           forward on the right -->
      <div class="b-pages">
        ${book.prev ? `<button id="r-prev" class="icon" aria-label="Previous chapter"
          title="${esc(book.prev.label)}">${ICONS.chPrev}</button>` : ""}
        <span id="r-num"></span>
        <input type="range" id="r-slider" min="1" max="${book.page_count}" value="${R.page + 1}" aria-label="Page">
        <span>${book.page_count}</span>
        ${book.next ? `<button id="r-next" class="icon" aria-label="Next chapter"
          title="${esc(book.next.label)}">${ICONS.chNext}</button>` : ""}
      </div>
      <button id="r-menu" class="icon b-menu" aria-controls="r-drawer">${ICONS.burger}</button>
    </div>
    <!-- written in desktop order (top to bottom, like MangaDex). On touch screens reader.css moves
         the rarely used options to the top and keeps page/chapter/close at the bottom, by the thumb. -->
    <aside class="drawer" id="r-drawer" aria-label="Reading options" ${R.drawer ? "" : "inert"}>
      <div class="d-row d-foot">
        <button class="d-opt" id="r-exit">${ICONS.back}<span>Back to series</span></button>
        <button id="r-close" class="icon" aria-label="Close options" title="Close">${ICONS.close}</button>
      </div>
      <div class="d-title">${esc(book.series_title)}</div>
      <div class="d-sub">${esc(book.label)}</div>
      <div class="d-row d-page">
        <button id="r-pprev" class="icon" aria-label="Previous page">‹</button>
        <!-- the whole box opens the list: an invisible native select over the visible text -->
        <label class="d-field pick"><small>Page</small><span id="r-page-label"></span>
          <select id="r-page" aria-label="Page">${Array.from({ length: book.page_count },
            (_, i) => `<option value="${i}">${i + 1} / ${book.page_count}</option>`).join("")}</select></label>
        <button id="r-pnext" class="icon" aria-label="Next page">›</button>
      </div>
      <div class="d-row d-chapter">
        <button id="r-cprev" class="icon" aria-label="Previous chapter" ${book.prev ? `title="${esc(book.prev.label)}"` : "disabled"}>‹</button>
        <div class="d-field"><small>Chapter</small><span>${esc(book.label)}</span></div>
        <button id="r-cnext" class="icon" aria-label="Next chapter" ${book.next ? `title="${esc(book.next.label)}"` : "disabled"}>›</button>
      </div>
      <hr>
      <!-- the mode only when the image reader draws more than one -->
      <button class="d-opt" id="r-mode" hidden>${ICONS.mode}<span></span></button>
      <!-- the image reader's own options (built in: the fit, the zoom lock and Page look), once it's open -->
      <div id="r-own" style="display: contents"></div>
      <button class="d-opt d-toggle" id="r-topbar">${ICONS.topBar}<span>Top bar</span><b></b></button>
      <button class="d-opt" id="r-fs">${ICONS.fullscreen}<span>Fullscreen</span></button>
      <div class="d-row"><label class="d-field pick"><small>Hide controls after</small><span id="r-autohide-label"></span>
        <select id="r-autohide" aria-label="Hide controls after"></select></label></div>
      <a class="d-opt" id="r-settings" href="#/settings/reader">${ICONS.settings}<span>Reader settings</span></a>
      <!-- last, after a line: everything a plugin adds, one row per plugin -->
      ${plugins.length ? `<div class="d-head d-plugins">Plugins</div>` : ""}
      ${plugins.map((p) => `<button class="d-opt d-toggle d-more" data-panel="plugin:${esc(p.id)}">${ICONS.plugin}
        <span>${esc(p.name)}</span><b data-ov-state="${p.overlay ? esc(p.id) : ""}"></b></button>`).join("")}
    </aside>
    ${plugins.map((p) => drawerPanel(`plugin:${p.id}`, p.name, `
      ${p.overlay ? `<button class="d-opt d-toggle" data-ov="${esc(p.id)}">${ICONS.overlay}<span>${esc(p.overlay.label)}</span><b></b></button>` : ""}
      ${p.actions.map((i) => `<button class="d-opt" data-act="${i}">${ICONS.plugin}<span>${esc(book.actions[i].label)}</span></button>`).join("")}
      ${p.overlay && canSay ? `<div class="d-head">Read aloud</div>
        <button class="d-opt d-toggle" data-say="on">${ICONS.speak}<span>Read aloud</span><b></b></button>
        <p class="d-hint" data-say="hint" hidden>Switch ${esc(p.overlay.label)} on above to hear its text.</p>
        <button class="d-opt d-toggle" data-say="rate">${ICONS.speed}<span>Reading speed</span><b></b></button>
        <button class="d-opt d-toggle" data-say="turn">${ICONS.autoTurn}<span>Turn pages by itself</span><b></b></button>
        <div class="d-row" data-say="voice"><label class="d-field pick"><small>Voice</small><span></span>
          <select aria-label="Voice"></select></label></div>` : ""}
      ${p.overlay ? `<div class="d-head" data-ov-look-head hidden>Boxes on the page</div>
        <button class="d-opt d-toggle" data-ov-look="opacity" hidden>${ICONS.overlay}<span>Box background</span><b></b></button>
        <button class="d-opt d-toggle" data-ov-look="outline" hidden>${ICONS.overlay}<span>Text outline</span><b></b></button>
        <button class="d-opt" data-ov-unhide hidden>${ICONS.overlay}<span></span></button>` : ""}`)).join("")}`;
  $("#r-back").onclick = () => leaveReader();
  $("#r-menu").onclick = () => setDrawer(!R.drawer);
  $("#r-close").onclick = () => setDrawer(false);
  $("#r-exit").onclick = () => leaveReader();
  $("#r-fs").onclick = $("#r-fs2").onclick = toggleFullscreen;
  updateFullscreenButtons();
  $("#r-topbar").onclick = () => setTopBar(cycle(TOP_BAR, topBarMode()));
  topBarButton();
  $("#r-slider").oninput = (e) => imgCall("goTo", +e.target.value - 1);
  $("#r-progress").onclick = (e) => { if (e.target.dataset.p) imgCall("goTo", +e.target.dataset.p); };
  // desktop: the mouse on the bottom edge brings up the full controls
  $("#r-progress").onpointerenter = (e) => { if (e.pointerType === "mouse" && !uiShown()) showUi(); };
  // the wheel over the bottom strip still scrolls the pages (and turns them, in paged mode)
  $("#r-progress").addEventListener("wheel", (e) => {
    e.preventDefault();
    imgCall("wheel", e);
  }, { passive: false });
  $("#r-page").onchange = (e) => imgCall("goTo", +e.target.value);
  $("#r-pprev").onclick = () => imgCall("step", false);
  $("#r-pnext").onclick = () => imgCall("step", true);
  autohidePicker();
  $("#r-autohide").onchange = (e) => pickAutohide(+e.target.value);
  $("#r-mode").onclick = () => setMode(cycle(Object.fromEntries(imgModes().map((m) => [m, 1])), R.mode));
  const toPrev = () => location.replace(`#/read/${book.prev.id}`);
  const toNext = () => location.replace(`#/read/${book.next.id}?page=0`);
  if (book.prev) $("#r-prev").onclick = $("#r-cprev").onclick = toPrev;
  if (book.next) $("#r-next").onclick = $("#r-cnext").onclick = toNext;
  // the pages, then the image reader's options in the drawer (they may show its settings)
  const opened = openImageReader(book);
  if (opened) {
    const own = imgCall("drawer") || {};
    $("#r-own").innerHTML = own.rows || "";
    $("#r-drawer").insertAdjacentHTML("afterend",
      (own.panels || []).map((p) => drawerPanel(p.key, p.title, p.body)).join(""));
  } else {
    $("#r-pages").innerHTML = `<div class="endcard"><p>No reader for pictures loaded. Reload the page, or check
      Settings → Plugins.</p><a class="btn" href="#/series/${book.series_id}">Back</a></div>`;
  }
  $("#r-mode").hidden = !opened || imgModes().length < 2;
  $("#r-mode span").textContent = MODES[R.mode];
  menuButton();
  el.querySelectorAll("[data-panel]").forEach((b) => (b.onclick = () => openPanel(b.dataset.panel)));
  el.querySelectorAll("[data-panel-close]").forEach((b) => (b.onclick = () => openPanel(null)));
  // a tap on the drawer under an open panel closes the panel (reader.css lets the taps through to it)
  $("#r-drawer").addEventListener("click", (e) => { if (R.panel) { e.preventDefault(); openPanel(null); } }, true);
  el.querySelectorAll("[data-ov]").forEach((b) => (b.onclick = () => ovToggle(b.dataset.ov)));
  el.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => ovAction(book.actions[+b.dataset.act])));
  el.querySelectorAll("[data-ov-unhide]").forEach((b) => (b.onclick = ovShowHidden));
  // number keys of an object come in number order, so the steps are spelled out
  const step = (steps, cur) => steps[(steps.indexOf(cur) + 1) % steps.length];
  el.querySelectorAll("[data-ov-look]").forEach((b) => (b.onclick = () => (b.dataset.ovLook === "opacity"
    ? ovSetStyle({ overlay_opacity: step([100, 75, 50, 25, 0], ovOpacity()) })  // from solid to see-through
    : ovSetStyle({ overlay_outline: cycle(OV_OUTLINE, ovOutline()) }))));
  ovApplyStyle();
  ovUpdateButtons();
  saySetup();
  if (opened) imgCall("bindDrawer", el);
  topBarUpdate();
  watchPointer();
  requestWakeLock();
  androidApp?.readerState(true, settings.reader_volume_keys !== false);  // keeps the screen on, takes the volume keys
}

/* the image reader shows page i: the numbers, progress and overlays follow */
function pageShown(i) {
  R.page = Math.max(0, Math.min(+i, R.book.page_count - 1));
  $("#reader .endcard")?.remove();
  updatePageUi();
  saveProgress();
  ovShow();
  topBarUpdate();
}

/* Moving the mouse over the pages brings the controls up (without the drawer) and keeps them up while
   it moves (each movement starts the "Hide controls after" countdown again), and a mouse that
   rests there hides its pointer while they're away. Only a real movement counts: the pointer has to
   get MOUSE_MOVE_PX away from where it last rested or the controls last hid, so a mouse that jitters
   by itself on its surface doesn't keep bringing them (or the pointer) back while you read. It's the
   distance from that point, so jitter that wanders back and forth never adds up. Dragging doesn't
   count, and neither does the moment just after the bars hid (a click that turned the page). */
const MOUSE_MOVE_PX = 8;
const MOUSE_AFTER_HIDE_MS = 600;
const mouse = { x: null, y: null, ax: null, ay: null, idle: null };

function mouseAnchor() { mouse.ax = mouse.x; mouse.ay = mouse.y; }

function watchPointer() {
  mouse.ax = mouse.ay = null;
  $("#r-pages").addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse") return;
    mouse.x = e.clientX;
    mouse.y = e.clientY;
    if (mouse.ax === null || e.buttons) return mouseAnchor();  // first sight, or dragging
    if (Math.hypot(mouse.x - mouse.ax, mouse.y - mouse.ay) < MOUSE_MOVE_PX) return;
    mouseAnchor();
    // up: the countdown starts again, so a mouse that keeps moving keeps them up (#257)
    if (uiShown()) restartHideTimer();
    else if (performance.now() - (R.hiddenAt || 0) > MOUSE_AFTER_HIDE_MS) showUi();
    $("#reader").classList.remove("cursor-idle");
    clearTimeout(mouse.idle);
    mouse.idle = setTimeout(() => {
      $("#reader")?.classList.add("cursor-idle");
      mouseAnchor();  // it rests here now
    }, 2000);
  });
}

function updatePageUi() {
  $("#r-num").textContent = R.page + 1;
  // filled part of the slider track (the browser only colours it natively in some engines)
  $("#r-slider").style.setProperty("--fill", `${R.book.page_count > 1 ? (R.page / (R.book.page_count - 1)) * 100 : 100}%`);
  $("#r-slider").value = R.page + 1;
  $("#r-page").value = R.page;
  $("#r-page-label").textContent = `${R.page + 1} / ${R.book.page_count}`;
  // thin bar at the very bottom: how far into this book you are
  for (const seg of $("#r-progress .rp-track").children) {
    const i = +seg.dataset.p;
    seg.className = i < R.page ? "read" : i === R.page ? "read cur" : "";
  }
  $("#rp-cur").textContent = R.page + 1;
}

function showEndCard() {
  saveProgress(true);
  const el = $("#reader");
  if ($("#reader .endcard")) {  // second tap at the end: continue to next book
    if (R.book.next) location.replace(`#/read/${R.book.next.id}?page=0`);
    return;
  }
  el.insertAdjacentHTML("beforeend", `<div class="endcard">
    <p>End of ${esc(R.book.label)}</p>
    ${R.book.next ? `<button class="btn primary" id="end-next">Next: ${esc(R.book.next.label)}</button>`
      : `<p class="muted">This was the last book in the series.</p>`}
    <a class="btn" href="#/series/${R.book.series_id}">Back to series</a></div>`);
  $("#end-next")?.addEventListener("click", (e) => {
    e.stopPropagation();
    location.replace(`#/read/${R.book.next.id}?page=0`);
  });
  $("#reader .endcard").addEventListener("click", (e) => {
    if (e.target.classList.contains("endcard")) e.currentTarget.remove();
  });
}

/* ---------------- plugin overlays ----------------
   A plugin with an overlay (e.g. a translator) draws boxes over pages. Each one is a toggle in the
   drawer, remembered per series on this device. The server answers per page with layers of boxes
   whose positions are fractions of the page; a layer still `pending` is asked for again a little
   later, while that page is still being read. The image reader says which element shows a page
   (pageBox); a layer next to it copies its box and transform (zoom, pan, swipe, slide), so it moves
   with the page without the reader knowing about overlays. */

const OV_RETRY_MS = 2500;

function ovReset(book) {
  if (R.ov) {
    R.ov.observers.forEach((o) => o.disconnect());
    R.ov.timers.forEach((t) => clearTimeout(t));
  }
  R.ov = !book ? null : {
    on: book.overlays.map((o) => o.plugin).filter((p) => ovSaved(book, p)),
    pages: new Map(),     // page -> {layers, done}
    loading: new Set(), timers: new Map(), observers: [],
    layers: new WeakMap(),  // the reader's page element -> the layer that follows it
  };
}

function ovKey(book, plugin) { return `overlay:${plugin}:${book.series_id}`; }
function ovSaved(book, plugin) {
  try { return localStorage.getItem(ovKey(book, plugin)) === "1"; } catch { return false; }
}

function ovToggle(plugin) {
  const on = !R.ov.on.includes(plugin);
  try { localStorage.setItem(ovKey(R.book, plugin), on ? "1" : "0"); } catch {}
  R.ov.on = on ? [...R.ov.on, plugin] : R.ov.on.filter((p) => p !== plugin);
  ovForget();
  ovUpdateButtons();
}

/* drop what was loaded (the toggles changed, or a plugin button may have changed the pages) */
function ovForget() {
  R.ov.timers.forEach((t) => clearTimeout(t));
  R.ov.timers.clear();
  R.ov.pages.clear();
  $("#reader").querySelectorAll(".ov-layer").forEach((l) => (l.innerHTML = ""));
  ovShow();
}

function ovUpdateButtons() {
  const el = $("#reader");
  el.querySelectorAll("[data-ov]").forEach((b) => {
    const on = R.ov.on.includes(b.dataset.ov);
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on);
    b.querySelector("b").textContent = on ? "On" : "Off";
  });
  // the plugin's row in the drawer says whether its overlay is on
  el.querySelectorAll("[data-ov-state]").forEach((b) => {
    if (!b.dataset.ovState) return;
    const on = R.ov.on.includes(b.dataset.ovState);
    b.textContent = on ? "On" : "Off";
    b.parentElement.classList.toggle("on", on);
  });
  // how the boxes look, and hidden boxes: only while an overlay is on (the settings are for all of them)
  const any = R.ov.on.length > 0, n = R.book.hidden_boxes || 0;
  el.querySelectorAll("[data-ov-look-head]").forEach((h) => (h.hidden = !any));
  el.querySelectorAll("[data-ov-look]").forEach((b) => {
    b.hidden = !any;
    b.querySelector("b").textContent = b.dataset.ovLook === "opacity" ? OV_OPACITY[ovOpacity()] : OV_OUTLINE[ovOutline()];
  });
  el.querySelectorAll("[data-ov-unhide]").forEach((b) => {
    b.hidden = !n || !any;
    b.querySelector("span").textContent = `Show ${n} hidden ${n === 1 ? "box" : "boxes"}`;
  });
  sayButtons();
}

/* the box look from the reader settings, kept to the values the reader knows */
function ovOpacity() {
  const v = +settings.overlay_opacity;
  return v in OV_OPACITY ? v : 100;
}
function ovOutline() { return settings.overlay_outline in OV_OUTLINE ? settings.overlay_outline : "off"; }

/* CSS variables on the reader, which reader.css uses for every overlay box */
function ovApplyStyle() {
  const el = $("#reader");
  el.style.setProperty("--ov-bg-opacity", ovOpacity() / 100);
  el.style.setProperty("--ov-text-stroke", OV_STROKE[ovOutline()]);
}

/* the drawer changed the look: saved as a reader setting, applied at once (the outline changes
   how much text fits, so the boxes are fitted again) */
function ovSetStyle(change) {
  saveSettings(change);
  ovApplyStyle();
  ovUpdateButtons();
  $("#reader").querySelectorAll(".ov-layer").forEach(ovFit);
  restartHideTimer();
}

/* what is being read, for plugin scripts (MangaVibe.reader.where()); null while the reader is closed */
function where() {
  return R.book ? { book: R.book.id, series: R.book.series_id, page: R.page, pages: R.book.page_count } : null;
}

/* draw the pages' overlays again (MangaVibe.reader.redraw()): a plugin's script changed what its
   page_overlay() gives, e.g. in an editor of its own */
function redraw() {
  if (R.ov) ovForget();
}

/* ---------------- the reader from a script (MangaVibe.reader, Plugin API 13) ----------------
   What a person does with taps and keys, for remote control (the MCP plugin) and voice control: each
   does what the matching button does. */

/* all there is to know about the open book, or null */
function state() {
  if (!R.book) return null;
  const b = R.book, on = R.ov?.on || [];
  return { ...where(), label: b.label, series_title: b.series_title, mode: R.mode,
    fit: (R.img && imgCall("state")?.fit) || settings.reader_fit || "screen",
    modes: R.own ? [] : imgModes(), options_open: R.drawer, panel: R.panel,
    at_end: !!$("#reader .endcard"), fullscreen: !!document.fullscreenElement,
    previous: b.prev ? { book: b.prev.id, label: b.prev.label } : null,
    next: b.next ? { book: b.next.id, label: b.next.label } : null,
    // what plugins put in the reading options: an overlay to switch on and off, buttons
    plugins: drawerPlugins(b).map((p) => ({ plugin: p.id, name: p.name,
      overlay: p.overlay ? { label: p.overlay.label, on: on.includes(p.id) } : null,
      buttons: p.actions.map((i) => ({ key: b.actions[i].key, label: b.actions[i].label })) })) };
}

/* the next or previous page, like → and ← (vertical mode: the next page's top). At the last page,
   forward shows the end card; forward again there goes on to the next book */
function step(forward) {
  if (!R.book) return false;
  if (R.own) return ownCall("step", forward) !== false;
  return imgCall("step", forward) !== false;
}

/* another book of the series: "next", "previous" or a book id; page from 0 (default: the start, or the
   saved place for an id) */
function openBook(which, page = null) {
  if (!R.book) return false;
  const id = which === "next" ? R.book.next?.id : which === "previous" ? R.book.prev?.id : +which;
  if (!id) return false;
  saveProgress(true);
  location.replace(`#/read/${id}` + (page !== null ? `?page=${page}` : which === "next" ? "?page=0" : ""));
  return true;
}

/* paged or vertical: the series' own mode, as the drawer's mode button sets it (admins). The image
   reader's setMode() switches without opening the book again, if it has one. */
async function setMode(mode) {
  if (!R.book || R.own || !imgModes().includes(mode)) return false;
  if (mode !== R.mode) {
    await api(`/api/series/${R.book.series_id}`, { method: "PATCH", body: { reader_mode: mode } });
    saveProgress(true);
    await openReader(R.book.id, R.page);
  }
  return true;
}

/* open or close the reading options (the drawer), with the bars */
function options(open) {
  if (!R.book) return false;
  if (R.own) return ownCall("options", open) !== false;
  if (open) openMenu(); else setDrawer(false);
  return true;
}

/* a plugin's overlay on or off (on: true/false, or null to switch it) */
function overlay(plugin, on = null) {
  if (!R.book || R.own || !R.book.overlays.some((o) => o.plugin === plugin)) return false;
  if (on === null || on !== R.ov.on.includes(plugin)) ovToggle(plugin);
  return true;
}

/* press one of a plugin's buttons (book_actions); its script may handle it in the page */
async function press(plugin, key) {
  const a = R.book?.actions.find((x) => x.plugin === plugin && x.key === key);
  if (!a) return false;
  await ovAction(a);
  return true;
}

/* open the reader again, e.g. after its settings changed elsewhere */
async function refresh() {
  if (!R.book) return false;
  saveProgress(true);
  await openReader(R.book.id, R.page);
  return true;
}

async function ovAction(a) {
  if (a.confirm && !confirm(a.confirm)) return;
  try {
    // the plugin's own script may do the button's work in the page (its "book-action" handler says
    // so by returning true), e.g. open an editor: then the server isn't asked
    if ((await emitPlugins("book-action", [a.key, where()], { only: a.plugin })).length) return;
    await showActionResult(await api(`/api/books/${R.book.id}/actions/${a.plugin}/${a.key}`, { method: "POST" }));
  } catch (e) { toast(e.message, 6000, "error"); }
  if (R.ov) ovForget();
}

/* pages whose overlay is wanted now: the page being read and the next one (vertical: also the previous) */
function ovWanted(i) {
  return R.mode === "vertical" ? Math.abs(i - R.page) <= 1 : i === R.page || i === R.page + 1;
}

function ovShow() {
  if (!R.ov) return;
  ovStatus();
  if (!R.ov.on.length) return;
  for (let i = R.page - 1; i <= R.page + 1; i++) {
    if (i >= 0 && i < R.book.page_count && ovWanted(i)) ovLoad(i);
  }
  ovDraw(R.page);  // the layer may still show the previous page (one picture that changes its page)
}

async function ovLoad(i) {
  const ov = R.ov, book = R.book;
  if (ov.pages.get(i)?.done) return ovDraw(i);
  if (ov.loading.has(i)) return;
  ov.loading.add(i);
  let data = null;
  try { data = await api(`/api/books/${book.id}/overlays/${i}?plugins=${encodeURIComponent(ov.on.join(","))}`); }
  catch {}
  ov.loading.delete(i);
  if (R.ov !== ov) return;  // another book, or the toggles changed meanwhile
  const layers = data ? data.layers : [{ pending: true, message: "Can't reach MangaVibe", boxes: [] }];
  const done = layers.every((l) => !l.pending);
  ov.pages.set(i, { layers, done });
  ovDraw(i);
  if (i === R.page) ovStatus();
  if (!done) {
    clearTimeout(ov.timers.get(i));
    ov.timers.set(i, setTimeout(() => {
      ov.timers.delete(i);
      if (R.ov === ov && ovWanted(i)) ovLoad(i);
    }, OV_RETRY_MS));
  }
}

/* the layer over the element that shows page i (made the first time), or null: not on screen */
function ovLayer(i) {
  const el = imgCall("pageBox", i);
  if (!el || !el.isConnected) return null;
  let layer = R.ov.layers.get(el);
  if (!layer || !layer.isConnected) {
    layer = document.createElement("div");
    layer.className = "ov-layer";
    el.after(layer);
    R.ov.layers.set(el, layer);
    ovFollow(el, layer);
  }
  return layer;
}

function ovDraw(i) {
  if (!R.ov) return;
  const layer = ovLayer(i);
  if (!layer) return;
  const layers = R.ov.pages.get(i)?.layers || [];
  const pct = (v) => `${(v * 100).toFixed(3)}%`;
  // only plain class names from plugins: letters, digits, - and _
  const cls = (s) => s.split(/\s+/).filter((c) => /^[\w-]+$/.test(c)).join(" ");
  layer.innerHTML = layers.flatMap((l) => l.boxes.filter((b) => !b.hidden).map((b) =>
    `<div class="ov-box ov-${esc(l.plugin)} ${cls(b.cls)}" style="left:${pct(b.x)};top:${pct(b.y)};`
    + `width:${pct(b.w)};height:${pct(b.h)}"${b.title ? ` title="${esc(b.title)}"` : ""}>`
    + `<span>${esc(b.text)}</span>`
    // the × goes to the corner away from the nearer screen edge: the title bar is at the top, the
    // controls card at the bottom, and either would cover it
    + `<button class="ov-hide${b.y + b.h / 2 < 0.5 ? " low" : ""}" data-plugin="${esc(l.plugin)}" data-box="${esc(b.id)}" `
    + `aria-label="Hide this box" title="Hide this box">×</button></div>`)).join("");
  // the × must not count as a tap on the page (turning it or toggling the controls)
  layer.querySelectorAll(".ov-hide").forEach((btn) => {
    for (const type of ["pointerdown", "pointerup", "click"]) btn.addEventListener(type, (e) => e.stopPropagation());
    btn.onclick = (e) => { e.stopPropagation(); ovHide(i, btn.dataset.plugin, btn.dataset.box); };
  });
  ovFit(layer);
}

/* the owner hid one box: gone at once, remembered on the server (all devices) */
async function ovHide(i, plugin, box) {
  const entry = R.ov.pages.get(i);
  const b = entry?.layers.find((l) => l.plugin === plugin)?.boxes.find((x) => x.id === box);
  if (b) b.hidden = true;
  ovDraw(i);
  restartHideTimer();
  try {
    const r = await api(`/api/books/${R.book.id}/overlays/${i}/hidden`, { method: "PUT", body: { plugin, box, hidden: true } });
    R.book.hidden_boxes = r.hidden_boxes;
    ovUpdateButtons();
    toast("Box hidden. Reader options → Show hidden boxes brings it back.");
  } catch (e) {
    if (b) b.hidden = false;
    ovDraw(i);
    toast(e.message, 6000, "error");
  }
}

async function ovShowHidden() {
  try {
    await api(`/api/books/${R.book.id}/overlays/hidden`, { method: "DELETE" });
    R.book.hidden_boxes = 0;
    ovUpdateButtons();
    ovForget();
  } catch (e) { toast(e.message, 6000, "error"); }
}

/* the largest font size at which each box's text fits inside it */
function ovFit(layer) {
  for (const box of layer.children) {
    const span = box.firstChild;
    const h = box.clientHeight, w = box.clientWidth;
    if (!h || !w) continue;  // not laid out yet (image still loading): the resize watcher calls again
    let lo = 5, hi = Math.max(6, Math.min(h * 0.6, 64));
    for (let n = 0; n < 7; n++) {
      const mid = (lo + hi) / 2;
      box.style.fontSize = `${mid}px`;
      if (span.offsetHeight <= h && span.scrollWidth <= w) lo = mid; else hi = mid;
    }
    box.style.fontSize = `${lo}px`;
  }
}

/* keep the layer exactly over the page's element: same box, same transform, same fade */
function ovFollow(el, layer) {
  const follow = () => {
    Object.assign(layer.style, { left: `${el.offsetLeft}px`, top: `${el.offsetTop}px`,
      width: `${el.offsetWidth}px`, height: `${el.offsetHeight}px`,
      transform: el.style.transform, transition: el.style.transition, opacity: el.style.opacity });
  };
  const mo = new MutationObserver(follow);
  mo.observe(el, { attributes: true, attributeFilter: ["style", "src"] });
  const ro = new ResizeObserver(() => { follow(); ovFit(layer); });
  ro.observe(el);
  R.ov.observers.push(mo, ro);
  follow();
}

/* the small line at the top: what the overlay of the current page is doing */
function ovStatus() {
  const el = $("#r-ov-status");
  if (!el) return;
  const entry = R.ov?.on.length ? R.ov.pages.get(R.page) : null;
  const msg = entry ? entry.layers.map((l) => l.message || (l.pending ? "Working…" : "")).filter(Boolean)[0] : "";
  el.hidden = !msg;
  el.textContent = msg || "";
  el.classList.toggle("busy", !!entry && !entry.done);
  // the plugins' quiet line about the page: in the top bar, so it only shows with the controls
  const note = $("#r-ov-note");
  if (note) {
    const text = entry ? entry.layers.map((l) => l.note).filter(Boolean).join(" · ") : "";
    note.hidden = !text;
    note.textContent = text;
  }
  // screen readers announce the page's text once it's there (not while read aloud speaks it already)
  const live = $("#r-live"), said = entry?.done && !S.on ? pageText(entry) : "";
  if (live && live.textContent !== said) live.textContent = said;
  sayUpdate();
}

addEventListener("resize", () => {
  if (R.ov) $("#reader").querySelectorAll(".ov-layer").forEach(ovFit);
});

/* ---------------- read aloud ----------------
   Reads the text a plugin gives for the page (its `speech`, or else the text of its boxes) with the
   device's voice: the browser's speech synthesis, or Android's own text-to-speech in the app (its web
   view has none). Remembered on this device, and needs an overlay switched on. With "Turn pages by
   itself", the next page comes once a page is read, and after the last page the next chapter: a book
   to listen to. Screen reader users can leave it off: #r-live hands them the same text. */

const RATE_STEPS = [0.75, 1, 1.25, 1.5, 2];
const S = (() => {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("reader-speech") || "{}") || {}; } catch {}
  return { on: !!saved.on, turn: !!saved.turn, voice: String(saved.voice || ""),
    rate: RATE_STEPS.includes(saved.rate) ? saved.rate : 1,
    key: null,        // "book:page" being read (or read): each page is read once
    run: 0,           // goes up with every stop, so callbacks of an older reading are ignored
    blocked: false,   // the browser wanted a tap first
    speech: null,     // what is being read: {parts, done, part} (part: the one being read now)
    turnTimer: null, androidDone: null, androidFrom: 0 };
})();

function saySave() {
  try { localStorage.setItem("reader-speech", JSON.stringify({ on: S.on, turn: S.turn, voice: S.voice, rate: S.rate })); } catch {}
}

/* the app (from the version with a speak() bridge) or a browser with speech synthesis */
function canSpeak() { return androidApp ? !!androidApp.speak : "speechSynthesis" in window; }

/* the read aloud controls: in the panel of every plugin with an overlay (they're one setting for all) */
function sayAll(kind) { return [...$("#reader").querySelectorAll(`[data-say="${kind}"]`)]; }

function saySetup() {
  sayAll("on").forEach((b) => (b.onclick = () => {
    S.on = !S.on;
    saySave();
    sayStop();
    S.key = null;
    sayButtons();
    ovStatus();  // starts reading the page (this tap lets the browser speak)
    restartHideTimer();
  }));
  sayAll("rate").forEach((b) => (b.onclick = () => {
    S.rate = RATE_STEPS[(RATE_STEPS.indexOf(S.rate) + 1) % RATE_STEPS.length];
    saySave();
    sayButtons();
    restartHideTimer();
    sayAgain();  // at once, from the line being read
  }));
  sayAll("turn").forEach((b) => (b.onclick = () => {
    S.turn = !S.turn;
    saySave();
    sayButtons();
    restartHideTimer();
    if (S.turn && S.finished === S.key) sayDone(S.key);  // the page was already read: go on now
  }));
  sayAll("voice").forEach((row) => (row.querySelector("select").onchange = (e) => {
    S.voice = e.target.value;
    saySave();
    sayButtons();
    sayAgain();  // at once, from the line being read
  }));
  sayButtons();
}

function sayButtons() {
  const toggle = (el, on) => {
    el.classList.toggle("on", on);
    el.setAttribute("aria-pressed", on);
    el.querySelector("b").textContent = on ? "On" : "Off";
  };
  sayAll("on").forEach((b) => toggle(b, S.on));
  // nothing to read while no overlay is on: say how to get some
  sayAll("hint").forEach((h) => (h.hidden = !S.on || !!R.ov?.on.length));
  sayAll("rate").forEach((b) => {
    b.hidden = !S.on;
    b.querySelector("b").textContent = `${S.rate}×`;
  });
  sayAll("turn").forEach((b) => {
    b.hidden = !S.on;
    toggle(b, S.turn);
  });
  // the app uses the phone's text-to-speech voice (Android settings → Text-to-speech)
  sayAll("voice").forEach((row) => {
    row.hidden = !S.on || !!androidApp;
    if (!row.hidden) sayVoices(row);
  });
}

function sayVoices(row) {
  const sel = row.querySelector("select");
  const voices = speechSynthesis.getVoices();
  sel.innerHTML = `<option value="">Device default</option>` + voices.map((v) =>
    `<option value="${esc(v.voiceURI)}">${esc(v.name)} (${esc(v.lang)})</option>`).join("");
  sel.value = voices.some((v) => v.voiceURI === S.voice) ? S.voice : "";
  row.querySelector("span").textContent = sel.selectedOptions[0]?.textContent || "Device default";
}
if (!androidApp && "speechSynthesis" in window) {
  speechSynthesis.addEventListener?.("voiceschanged", () => { if (R.book) sayButtons(); });  // the list comes late
}

/* the page as text: each overlay's `speech`, or the text of its boxes in their order */
function pageText(entry) {
  return (entry?.layers || []).map((l) => l.speech
    || l.boxes.filter((b) => !b.hidden).map((b) => b.text).filter(Boolean).join("\n")).filter(Boolean).join("\n\n");
}

/* read the current page if it's there and wasn't read yet (called whenever the page or its overlay changes) */
function sayUpdate() {
  if (!S.on || !R.book || !R.ov?.on.length) {
    if (S.key !== null) { sayStop(); S.key = null; }
    return;
  }
  if (S.blocked) return;
  const key = `${R.book.id}:${R.page}`;
  if (S.key === key) return;
  sayStop();                 // another page: the old one stops at once
  const entry = R.ov.pages.get(R.page);
  if (!entry?.done) return;  // still being worked on: called again when it's there
  S.key = key;
  const text = pageText(entry);
  const problem = entry.layers.map((l) => l.message).filter(Boolean).join(". ");
  if (text) speak(text, () => sayDone(key));
  else if (problem) speak(problem, () => {});  // e.g. "set the server": said, and the page stays
  else sayDone(key);         // nothing to read on this page
}

/* read `text`, then call `done`. It's read in parts (lines), and the part being read is remembered,
   so a change of speed or voice can carry on from there (sayAgain) */
function speak(text, done) {
  S.speech = { parts: sayParts(text), done, part: 0 };
  speakFrom(0);
}

function speakFrom(start) {
  const sp = S.speech, run = S.run;
  const finish = () => {
    if (S.run !== run) return;
    S.speech = null;
    sp.done();
  };
  const parts = sp.parts.slice(start);
  if (androidApp) {
    // one line per part: the app reads them in turn, says which one starts (speaking) and when all are done (spoke)
    S.androidDone = finish;
    S.androidFrom = start;
    androidApp.speak(run, parts.join("\n"), S.rate);
    return;
  }
  const voice = speechSynthesis.getVoices().find((v) => v.voiceURI === S.voice);
  // in short parts: some browsers (Chrome) stop a long utterance after about 15 seconds
  parts.forEach((part, j) => {
    const u = new SpeechSynthesisUtterance(part);
    u.rate = S.rate;
    if (voice) { u.voice = voice; u.lang = voice.lang; }
    u.onstart = () => { if (S.run === run) sp.part = start + j; };
    if (j === parts.length - 1) u.onend = finish;
    u.onerror = (e) => {
      if (S.run !== run) return;
      if (e.error === "not-allowed") {  // no tap on this page yet: wait for one
        S.blocked = true;
        S.key = null;
        toast("Tap the page to start reading aloud");
      } else if (e.error !== "interrupted" && e.error !== "canceled") {
        speechSynthesis.cancel();  // the rest of this page too
        finish();                  // and go on as if it was read (the next page, with Turn pages by itself)
      }
    };
    speechSynthesis.speak(u);
  });
}

/* the speed or voice changed: what is being read goes on from the part it was at, the new way */
function sayAgain() {
  const sp = S.speech;
  if (!sp) return;  // nothing being read
  sayStop();
  S.speech = sp;
  speakFrom(sp.part);
}

/* lines, and long lines cut after a sentence (or a comma) */
function sayParts(text) {
  const out = [];
  for (const line of text.split(/\n+/)) {
    let rest = line.trim();
    while (rest.length > 200) {
      const cut = Math.max(...[". ", "! ", "? ", "; ", ", "].map((s) => rest.lastIndexOf(s, 200)));
      const at = cut > 40 ? cut + 1 : 200;
      out.push(rest.slice(0, at));
      rest = rest.slice(at).trim();
    }
    if (rest) out.push(rest);
  }
  return out;
}

/* a page was read to the end: with "Turn pages by itself", on to the next page (or chapter) */
function sayDone(key) {
  S.finished = key;
  if (!S.turn || S.key !== key) return;
  clearTimeout(S.turnTimer);
  S.turnTimer = setTimeout(() => {
    if (!S.on || !S.turn || S.key !== key || !R.book) return;
    if (R.page < R.book.page_count - 1) imgCall("step", true);
    else if (R.book.next) location.replace(`#/read/${R.book.next.id}?page=0`);
  }, 900);
}

function sayStop() {
  S.run++;
  S.speech = null;
  clearTimeout(S.turnTimer);
  if (androidApp) androidApp.stopSpeaking?.();
  else if ("speechSynthesis" in window) speechSynthesis.cancel();
}

/* the Android app finished reading (its text-to-speech calls MangaVibeApp.spoke) */
function spoke(run) {
  if (run === S.run) S.androidDone?.();
}

/* the Android app started part `i` of what it was given (MangaVibeApp.speaking) */
function speaking(run, i) {
  if (run === S.run && S.speech) S.speech.part = S.androidFrom + i;
}

// the browser refused to speak before a tap: the next tap or key starts it
for (const type of ["pointerup", "keydown"]) {
  document.addEventListener(type, () => {
    if (S.blocked && R.book) { S.blocked = false; sayUpdate(); }
  }, true);
}

/* ---------------- controls: show/hide, auto-hide after a few seconds ----------------
   A tap/click in the middle shows them with the options drawer (openMenu); the next one hides them
   again, and so does turning the page. Otherwise they hide on their own after `reader_autohide`
   seconds (0 = never). Any use of the bars restarts the countdown, and they wait while
   the slider is held, the mouse is over them, or a dropdown has focus. */

function uiShown() { return !$("#reader").classList.contains("ui-hidden"); }

/* a tap on the page that doesn't turn it: the bars and the reading options (the ☰ drawer) together.
   A tap beside the open drawer closes it again (and the bars hide on their own). */
function openMenu() {
  showUi();
  setDrawer(true);
}

/* a tap in the middle: the menu, or away with the bars if they're up (the drawer was closed already) */
function toggleMenu() {
  if (uiShown()) hideUi(); else openMenu();
}

function showUi() {
  $("#reader").classList.remove("ui-hidden");
  restartHideTimer();
}

function hideUi() {
  clearTimeout(R.hideTimer);
  $("#reader").classList.add("ui-hidden");
  R.hiddenAt = performance.now();
  mouseAnchor();  // the mouse has to move away from here to bring them back (watchPointer)
  // hidden bars must not keep focus, or arrow keys would still change a dropdown/slider
  if (inChrome(document.activeElement)) document.activeElement.blur();
}

function inChrome(el) { return !!el?.closest?.("#reader .chrome, #reader .drawer, #reader .d-panel"); }

/* the reading options' "Hide controls after": the same choices as Settings → Reader. The options are
   only written again when they change (a saved time not in the list comes or goes), so a select being
   stepped through with the keyboard keeps its place. */
function autohidePicker() {
  const sel = $("#r-autohide");
  if (!sel) return;
  const secs = +settings.reader_autohide, cur = secs > 0 ? String(secs) : "0", options = autohideOptions(cur);
  const values = options.map(([v]) => v).join(" ");
  if (sel.dataset.values !== values) {
    sel.innerHTML = options.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
    sel.dataset.values = values;
  }
  sel.value = cur;
  $("#r-autohide-label").textContent = options.find(([v]) => v === cur)[1];
}

/* a time picked there: used at once, saved a moment later (only the last one, when it's stepped through
   with the arrow keys). A save that fails puts back the time saved before. */
const autohideSave = { timer: null, saved: null };
function pickAutohide(secs) {
  if (autohideSave.timer === null) autohideSave.saved = settings.reader_autohide;
  settings.reader_autohide = secs;
  autohidePicker();
  clearTimeout(autohideSave.timer);
  autohideSave.timer = setTimeout(async () => {
    autohideSave.timer = null;
    const before = autohideSave.saved;
    if (await saveSettings({ reader_autohide: secs })) return;
    if (settings.reader_autohide === secs && autohideSave.timer === null) {  // nothing picked since
      settings.reader_autohide = before;
      autohidePicker();
      restartHideTimer();
    }
  }, 400);
}

function restartHideTimer() {
  clearTimeout(R.hideTimer);
  const secs = +settings.reader_autohide;
  if (!R.book || !uiShown() || !(secs > 0)) return;
  R.hideTimer = setTimeout(() => {
    if (!R.book || !uiShown()) return;
    if (controlsBusy()) return restartHideTimer();  // check again later
    hideUi();
  }, secs * 1000);
}

/* someone is using the bars right now: don't pull them away */
function controlsBusy() {
  const a = document.activeElement;
  return R.drawer || R.sliding || R.hovering || (inChrome(a) && a.tagName === "SELECT");
}

// any interaction with the bars restarts the countdown (listeners on document: #reader is re-rendered)
for (const type of ["pointerdown", "pointermove", "wheel", "keydown", "input", "change", "focusin"]) {
  document.addEventListener(type, (e) => { if (inChrome(e.target)) restartHideTimer(); },
    { passive: true, capture: true });
}
document.addEventListener("pointerdown", (e) => {
  if (e.target.id === "r-slider") R.sliding = true;
  // pressing anywhere else lets go of a focused dropdown, so it no longer holds the bars open
  else if (!inChrome(e.target) && inChrome(document.activeElement)) document.activeElement.blur();
}, true);
const stopSliding = () => { if (R.sliding) { R.sliding = false; restartHideTimer(); } };
document.addEventListener("pointerup", stopSliding, true);
document.addEventListener("pointercancel", stopSliding, true);
// mouse over the bars keeps them up (touch has no hover, and a tap must not count as one)
document.addEventListener("pointerover", (e) => {
  if (e.pointerType === "mouse") R.hovering = inChrome(e.target);
}, true);
document.addEventListener("pointerout", (e) => {
  if (e.pointerType !== "mouse" || inChrome(e.relatedTarget)) return;
  R.hovering = false;
  restartHideTimer();
}, true);

/* with `now`, returns a promise that settles once the server has the page (never rejects) */
function saveProgress(now = false) {
  clearTimeout(R.saveTimer);
  const book = R.book, page = R.page;
  if (!book) return Promise.resolve();
  const send = () => fetch(`/api/books/${book.id}/progress`, { method: "PUT", keepalive: true,
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ page }) }).catch(() => {});
  if (now) return send();
  R.saveTimer = setTimeout(send, 700);
}

/* phones and tablets only: a mouse click never goes fullscreen by itself (the buttons still do).
   On the first tap with Auto fullscreen, and again on the next tap after the phone took fullscreen
   away (locked, or another app): browsers only go fullscreen on a tap, never by themselves. */
function maybeAutoFullscreen(e) {
  if (e?.pointerType === "mouse") return;
  const want = (R.firstTap && settings.auto_fullscreen) || R.wantFs;
  if (want && !document.fullscreenElement && !androidApp) {
    document.documentElement.requestFullscreen?.().catch(() => {});
  }
  R.firstTap = false;
}

/* R.wantFs: the reader was fullscreen and nobody chose to leave it. Fullscreen that goes away while
   the page is hidden, or just as it comes back (browsers send the event late), was taken by the phone;
   leaving it on screen (Esc, the back gesture, the button) is the reader's choice and stays. */
R.wantFs = false;
R.shownAt = 0;
document.addEventListener("fullscreenchange", () => {
  if (document.fullscreenElement) R.wantFs = true;
  else if (document.visibilityState === "visible" && performance.now() - R.shownAt > 1500) R.wantFs = false;
});

/* both fullscreen buttons show what a tap will do; hidden where the browser has no fullscreen (iPhone)
   and in the Android app, which already fills the screen between the phone's bars */
function updateFullscreenButtons() {
  const on = !!document.fullscreenElement, label = on ? "Exit fullscreen" : "Fullscreen";
  const rail = $("#r-fs2"), opt = $("#r-fs");
  if (!rail || !opt) return;
  rail.hidden = opt.hidden = document.fullscreenEnabled === false || !!androidApp;
  rail.innerHTML = on ? ICONS.fullscreenExit : ICONS.fullscreen;
  rail.title = label;
  rail.setAttribute("aria-label", label);
  opt.innerHTML = `${on ? ICONS.fullscreenExit : ICONS.fullscreen}<span>${label}</span>`;
}
document.addEventListener("fullscreenchange", updateFullscreenButtons);

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.().catch(() => toast("Fullscreen not available"));
}

async function requestWakeLock() {
  if (androidApp) return;  // the app keeps the screen on itself (readerState)
  try { R.wake = await navigator.wakeLock?.request("screen"); } catch {}  // needs HTTPS; ignore if unavailable
}

function leaveReader() {
  // back to where we came from inside the app; if opened directly, go to the series page
  if (R.cameFromApp) history.back();
  else location.replace(R.book ? `#/series/${R.book.series_id}` : "#/");
}

/* returns the final progress save (a promise), or nothing if the reader wasn't open */
function closeReader() {
  if ($("#reader").hidden) return;
  const saved = saveProgress(true);
  R.wake?.release?.().catch(() => {});
  androidApp?.readerState(false, false);
  sayStop();
  S.key = null;
  if (R.own) { ownCall("close"); R.own = null; }
  if (R.img) { imgCall("close"); R.img = null; }
  ovReset(null);
  R.book = null;
  R.firstTap = true;
  R.wantFs = false;
  clearTimeout(R.hideTimer);
  Object.assign(R, { sliding: false, hovering: false, drawer: false });
  $("#reader").hidden = true;
  $("#reader").innerHTML = "";
  document.body.style.overflow = "";
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  return saved;
}

document.addEventListener("keydown", (e) => {
  if ($("#reader").hidden || !R.book || R.own) return;  // a plugin's reader takes its own keys
  if (e.key === "Escape") return R.panel ? openPanel(null) : R.drawer ? setDrawer(false) : leaveReader();
  if (e.ctrlKey || e.metaKey || e.altKey) return;  // the browser's own shortcuts
  // the slider and dropdowns take their own keys; Space and Enter press a focused button
  const t = e.target, own = t.tagName === "SELECT" || t.tagName === "INPUT";
  if (own || (e.key === " " && t.closest?.("button, a"))) return;
  if (e.key === "f" || e.key === "F") {
    if (!$("#r-fs").hidden) toggleFullscreen();
    return;
  }
  // the rest (arrows, Page Up/Down, Space, Home, End) is the image reader's
  if (imgCall("key", e) === true) e.preventDefault();  // else a focused scroll box would scroll a second time
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") saveProgress(true);
  else {
    R.shownAt = performance.now();
    if (!$("#reader").hidden) requestWakeLock();
  }
});

/* the Android app's volume keys (Settings → Android app): down = next page, up = previous.
   Vertical mode scrolls most of a screen instead. */
function volumeKey(dir) {
  if ($("#reader").hidden || !R.book) return;
  const forward = dir === "next";
  if (R.own) return void ownCall("step", forward);
  imgCall("step", forward, "volume");
}

/* the Android back button: closes a drawer panel, then the options drawer, then leaves the reader */
function back() {
  if ($("#reader").hidden) return false;
  if (R.own && ownCall("back") === true) return true;  // e.g. it closed a menu of its own
  if (R.panel) openPanel(null);
  else if (R.drawer) setDrawer(false);
  else leaveReader();
  return true;
}

return {
  open: openReader,
  close: closeReader,
  volumeKey,
  back,
  spoke,
  speaking,
  where,
  redraw,
  state, step, openBook, setMode, options, overlay, press,
  goTo: (i) => (!R.book ? false : R.own ? ownCall("goTo", +i) !== false : imgCall("goTo", +i) !== false),
  refresh, leave: () => (R.book ? (leaveReader(), true) : false),
  get isOpen() { return !$("#reader").hidden; },
};
})();
