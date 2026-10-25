/* The reader: plain JavaScript (touch, swipe, zoom, preloading and scroll tracking are
   direct browser work, so it stays outside Alpine). The router calls Reader.open/close. */
"use strict";

const Reader = (() => {

const R = { book: null, page: 0, mode: "rtl", ui: false, saveTimer: null, wake: null, firstTap: true,
  preloaded: new Map(), cameFromApp: false, hideTimer: null, sliding: false, hovering: false, drawer: false,
  ov: null };  // plugin overlays (see "plugin overlays" below)
const WIDTHS = [720, 1080, 1440, 1800, 2160, 2880];
const TAP_SIDE = 0.25;  // width of each page-turn tap zone (left/right); the middle toggles the controls
const TAP_EDGE = 0.2;   // one-handed layout: the narrow "previous" strip; the rest turns forward
const MODES = { rtl: "Right to left", ltr: "Left to right", vertical: "Vertical" };
const FITS = { screen: "Fit screen", width: "Fit width", height: "Fit height", original: "Original size" };
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
  menu: svg(`<path d="M4 6h16M4 12h16M4 18h16"/>`),
  back: svg(`<path d="M19 12H5M11 6l-6 6 6 6"/>`),
  chPrev: svg(`<path d="M18 6l-7 6 7 6V6zM6 6v12"/>`),
  chNext: svg(`<path d="M6 6l7 6-7 6V6zM18 6v12"/>`),
  close: svg(`<path d="M6 6l12 12M18 6L6 18"/>`),
  mode: svg(`<path d="M4 7h13l-3-3M20 17H7l3 3"/>`),
  fit: svg(`<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>`),
  fullscreen: svg(`<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>`),
  fullscreenExit: svg(`<path d="M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5"/>`),
  settings: svg(`<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>`),
  // locked: closed shackle; unlocked: shackle swung open, so the two are easy to tell apart
  lock: svg(`<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>`),
  unlock: svg(`<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>`),
  overlay: svg(`<path d="M4 5h16v10H9l-5 4V5z"/><path d="M8 9h8M8 12h5"/>`),
  plugin: svg(`<path d="M9 3v4M15 3v4M7 7h10v5a5 5 0 0 1-10 0V7zM12 17v4"/>`),
};

/* the hand the reader is held in (Settings → Reader): back, fullscreen and the options button sit on
   that side, and the options drawer slides in from it */
function drawerSide() { return settings.reader_drawer_side === "left" ? "left" : "right"; }

function setDrawer(open) {
  R.drawer = open;
  $("#reader").classList.toggle("drawer-open", open);
  const d = $("#r-drawer");
  d.inert = !open;
  if (open) {
    R.hovering = false;
    clearTimeout(R.hideTimer);
  } else {
    if (d.contains(document.activeElement)) document.activeElement.blur();
    restartHideTimer();
  }
}

function pageWidth() {
  if (settings.reader_fit === "original") return 0;
  const want = Math.max(innerWidth, settings.reader_fit === "height" ? innerHeight * 0.75 : 0) * (devicePixelRatio || 1);
  return WIDTHS.find((w) => w >= want) || WIDTHS[WIDTHS.length - 1];
}

function pageUrl(i) {
  const w = pageWidth();
  return `/api/books/${R.book.id}/pages/${i}?v=${R.book.version}${w ? "&w=" + w : ""}`;
}

async function openReader(bookId, startPage, cameFromApp = R.cameFromApp) {
  R.cameFromApp = cameFromApp;
  const el = $("#reader");
  el.hidden = false;
  el.innerHTML = `<div class="loading">Loading…</div>`;
  document.body.style.overflow = "hidden";
  const book = await api(`/api/books/${bookId}`);
  if (book.error) { el.innerHTML = `<div class="endcard"><p>${esc(book.error)}</p>
    <a class="btn" href="#/series/${book.series_id}">Back</a></div>`; return; }
  Object.assign(R, { book, mode: book.reader_mode || settings.reader_mode || "rtl",
    page: startPage !== null ? +startPage : (book.completed ? 0 : book.page) });
  R.page = Math.max(0, Math.min(R.page, book.page_count - 1));
  R.preloaded.clear();
  // drop ?page= so a reload resumes from saved progress (replaceState fires no hashchange)
  history.replaceState(history.state, "", `#/read/${book.id}`);
  document.title = `${book.label} · ${book.series_title}`;
  clearTimeout(R.hideTimer);
  el.className = `fit-${settings.reader_fit || "screen"} ui-hidden ${R.mode} drawer-${drawerSide()}`
    + (R.drawer ? " drawer-open" : "");
  const paged = R.mode !== "vertical";
  ovReset(book);
  el.innerHTML = `
    ${paged ? `<div class="stage"><img alt=""><div class="ov-layer" id="r-ov"></div></div>` : `<div class="vertical"></div>`}
    <div class="ov-status" id="r-ov-status" hidden></div>
    <div class="read-progress" id="r-progress">
      <span id="rp-cur"></span>
      <div class="rp-track">${Array.from({ length: book.page_count },
        (_, i) => `<i data-p="${i}" title="Page ${i + 1}"></i>`).join("")}</div>
      <span>${book.page_count}</span>
    </div>
    <div class="chrome top">
      <div class="title"><b>${esc(book.series_title)}</b><span>${esc(book.label)}</span></div>
    </div>
    <!-- the most used buttons, stacked just above the bottom card on the thumb side (one-handed use) -->
    <div class="chrome rail">
      <button id="r-back" class="icon" aria-label="Back" title="Back">${ICONS.back}</button>
      <button id="r-fs2" class="icon" aria-label="Fullscreen" title="Fullscreen">${ICONS.fullscreen}</button>
    </div>
    <!-- a floating card; the options button is at the thumb end -->
    <div class="chrome bottom">
      <!-- chapter buttons, page numbers and slider follow the reading direction (dir="rtl" mirrors them) -->
      <div class="b-pages" ${R.mode === "rtl" ? 'dir="rtl"' : ""}>
        <button id="r-prev" class="icon" aria-label="Previous chapter"
          ${book.prev ? `title="${esc(book.prev.label)}"` : "disabled"}>${ICONS.chPrev}</button>
        <span id="r-num"></span>
        <input type="range" id="r-slider" min="1" max="${book.page_count}" value="${R.page + 1}" aria-label="Page">
        <span>${book.page_count}</span>
        <button id="r-next" class="icon" aria-label="Next chapter"
          ${book.next ? `title="${esc(book.next.label)}"` : "disabled"}>${ICONS.chNext}</button>
      </div>
      <button id="r-menu" class="icon b-menu" aria-label="Reading options" title="Reading options">${ICONS.menu}</button>
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
      <button class="d-opt" id="r-mode">${ICONS.mode}<span>${MODES[R.mode]}</span></button>
      <div class="d-row d-fit">
        <button class="d-opt" id="r-fit">${ICONS.fit}<span>${FITS[settings.reader_fit || "screen"]}</span></button>
        ${paged ? `<button id="r-lock" class="icon"></button>` : ""}
      </div>
      ${book.overlays.map((o) => `<button class="d-opt d-toggle" data-ov="${esc(o.plugin)}">${ICONS.overlay}
        <span>${esc(o.label)}</span><b></b></button>`).join("")}
      ${book.overlays.length ? `<button class="d-opt" id="r-ov-unhide" hidden>${ICONS.overlay}<span></span></button>
        <button class="d-opt d-toggle" id="r-ov-opacity" hidden>${ICONS.overlay}<span>Box background</span><b></b></button>
        <button class="d-opt d-toggle" id="r-ov-outline" hidden>${ICONS.overlay}<span>Text outline</span><b></b></button>` : ""}
      ${book.actions.map((a, i) => `<button class="d-opt" data-act="${i}">${ICONS.plugin}<span>${esc(a.label)}</span></button>`).join("")}
      <button class="d-opt" id="r-fs">${ICONS.fullscreen}<span>Fullscreen</span></button>
      <a class="d-opt" id="r-settings" href="#/settings/reader">${ICONS.settings}<span>Reader settings</span></a>
    </aside>`;
  $("#r-back").onclick = () => leaveReader();
  $("#r-menu").onclick = () => setDrawer(true);
  $("#r-close").onclick = () => setDrawer(false);
  $("#r-exit").onclick = () => leaveReader();
  $("#r-fs").onclick = $("#r-fs2").onclick = toggleFullscreen;
  updateFullscreenButtons();
  if ($("#r-lock")) $("#r-lock").onclick = () => setLocked(!locked());
  $("#r-slider").oninput = (e) => goTo(+e.target.value - 1);
  $("#r-progress").onclick = (e) => { if (e.target.dataset.p) goTo(+e.target.dataset.p); };
  // vertical mode: the wheel over the bottom strip still scrolls the pages
  $("#r-progress").onwheel = (e) => $("#reader .vertical")?.scrollBy(0, e.deltaY);
  $("#r-page").onchange = (e) => goTo(+e.target.value);
  $("#r-pprev").onclick = () => (paged ? prev() : goTo(R.page - 1));
  $("#r-pnext").onclick = () => (paged ? next() : goTo(R.page + 1));
  $("#r-mode").onclick = async () => {
    await api(`/api/series/${book.series_id}`, { method: "PATCH", body: { reader_mode: cycle(MODES, R.mode) } });
    saveProgress(true);
    openReader(book.id, R.page);
  };
  $("#r-fit").onclick = async () => {
    await api("/api/settings", { method: "PUT", body: { reader_fit: cycle(FITS, settings.reader_fit || "screen") } });
    await loadSettings();
    openReader(book.id, R.page);
  };
  const toPrev = () => location.replace(`#/read/${book.prev.id}`);
  const toNext = () => location.replace(`#/read/${book.next.id}?page=0`);
  if (book.prev) $("#r-prev").onclick = $("#r-cprev").onclick = toPrev;
  if (book.next) $("#r-next").onclick = $("#r-cnext").onclick = toNext;
  el.querySelectorAll("[data-ov]").forEach((b) => (b.onclick = () => ovToggle(b.dataset.ov)));
  el.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => ovAction(book.actions[+b.dataset.act])));
  if ($("#r-ov-unhide")) $("#r-ov-unhide").onclick = ovShowHidden;
  if ($("#r-ov-opacity")) {
    // from solid down to see-through (the keys of OV_OPACITY are in number order, the other way)
    const steps = [100, 75, 50, 25, 0];
    $("#r-ov-opacity").onclick = () =>
      ovSetStyle({ overlay_opacity: steps[(steps.indexOf(ovOpacity()) + 1) % steps.length] });
    $("#r-ov-outline").onclick = () => ovSetStyle({ overlay_outline: cycle(OV_OUTLINE, ovOutline()) });
  }
  ovApplyStyle();
  ovUpdateButtons();

  if (R.mode === "vertical") setupVertical(); else setupPaged();
  requestWakeLock();
  androidApp?.readerState(true, settings.reader_volume_keys !== false);  // keeps the screen on, takes the volume keys
}

/* ---------------- paged mode: zoom, pan, swipe, page animations ----------------
   One <img> inside .stage. Zoom and pan are a CSS transform on the image:
   translate(x, y) scale(s), origin at the centre. Pointer Events cover touch and mouse.
   The lock (🔒) turns zoom/pan off and keeps the page fitted. */

const Z = { s: 1, x: 0, y: 0, hiRes: false };
const MAX_ZOOM = 5;
const pointers = new Map();  // pointerId -> {x, y}
let gesture = null;          // what the current touch is doing: pan | pinch | swipe | tap

function locked() {
  try { return localStorage.getItem("reader-lock") === "1"; } catch { return false; }
}

function setLocked(on) {
  try { localStorage.setItem("reader-lock", on ? "1" : "0"); } catch {}
  updateLockButton();
  if (on) resetZoom(true);
  $("#reader .vertical")?.classList.toggle("locked", on);
}

function updateLockButton() {
  const b = $("#r-lock");
  if (!b) return;
  b.innerHTML = locked() ? ICONS.lock : ICONS.unlock;
  b.classList.toggle("on", locked());
  b.title = locked() ? "Zoom locked (tap to allow zoom and moving)" : "Zoom allowed (tap to lock)";
  b.setAttribute("aria-label", b.title);
}

function img() { return $("#reader .stage img"); }

function applyZoom(animate = false) {
  const el = img();
  if (!el) return;
  el.style.transition = animate ? "transform .2s ease-out" : "none";
  el.style.transform = `translate(${Z.x}px, ${Z.y}px) scale(${Z.s})`;
  $("#reader .stage").classList.toggle("zoomed", Z.s > 1.01);
  $("#reader .stage").classList.toggle("can-pan", canPan());
}

/* keep the zoomed page covering the screen: no empty space at the edges */
function clampPan() {
  const el = img();
  const w = el.offsetWidth * Z.s, h = el.offsetHeight * Z.s;
  const maxX = Math.max(0, (w - innerWidth) / 2), maxY = Math.max(0, (h - innerHeight) / 2);
  Z.x = Math.max(-maxX, Math.min(maxX, Z.x));
  Z.y = Math.max(-maxY, Math.min(maxY, Z.y));
}

/* zoom to scale s keeping the screen point (px, py) under the finger/cursor still */
function zoomAt(s, px, py, from = { s: Z.s, x: Z.x, y: Z.y }) {
  s = Math.max(1, Math.min(MAX_ZOOM, s));
  const cx = px - innerWidth / 2, cy = py - innerHeight / 2;
  Z.x = cx - (cx - from.x) * (s / from.s);
  Z.y = cy - (cy - from.y) * (s / from.s);
  Z.s = s;
  if (s === 1) { Z.x = 0; Z.y = 0; }
  clampPan();
  maybeHiRes();
}

/* is the page bigger than the screen (zoomed in, or "fit width"/"original" pages)? */
function canPan() {
  const el = img();
  return !locked() && (el.offsetWidth * Z.s > innerWidth + 1 || el.offsetHeight * Z.s > innerHeight + 1);
}

function resetZoom(animate = false) {
  Object.assign(Z, { s: 1, x: 0, y: 0 });
  applyZoom(animate);
}

/* zoomed in a lot: swap to the full-resolution page so it stays sharp */
function maybeHiRes() {
  if (Z.s < 1.6 || Z.hiRes || settings.reader_fit === "original") return;
  Z.hiRes = true;
  const book = R.book.id, page = R.page;
  const full = `/api/books/${book}/pages/${page}?v=${R.book.version}`;
  const pre = new Image();
  pre.onload = () => { if (Z.hiRes && R.book?.id === book && R.page === page && img()) img().src = full; };
  pre.src = full;
}

function setupPaged() {
  const stage = $("#reader .stage");
  img().onerror = () => toast("Could not load page " + (R.page + 1), 3000, "error");
  updateLockButton();
  ovFollowImage();
  showPage(0);

  let start = null, lastTap = { t: 0, x: 0, y: 0 }, tapTimer = null;

  // the browser's own "drag the picture" would cancel our pointer events (Firefox ignores the CSS rule)
  img().draggable = false;
  stage.addEventListener("dragstart", (e) => e.preventDefault());

  stage.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse") {
      if (e.button !== 0) return;  // only the left button; middle/right do nothing here
      e.preventDefault();          // no text selection / native image drag
    }
    try { stage.setPointerCapture(e.pointerId); } catch {}  // keeps getting moves outside the image
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2 && !locked()) {
      const [a, b] = [...pointers.values()];
      gesture = { type: "pinch", d0: Math.hypot(a.x - b.x, a.y - b.y), from: { ...Z } };
    } else if (pointers.size === 1) {
      start = { x: e.clientX, y: e.clientY, t: Date.now(), from: { ...Z }, mouse: e.pointerType === "mouse" };
      gesture = { type: "tap" };
    }
  });

  stage.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (gesture?.type === "pinch" && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      zoomAt(gesture.from.s * d / gesture.d0, (a.x + b.x) / 2, (a.y + b.y) / 2, gesture.from);
      applyZoom();
      return;
    }
    if (!start || pointers.size !== 1) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (gesture.type === "tap" && Math.hypot(dx, dy) > 10) {
      const wide = img().offsetWidth * Z.s > innerWidth + 1;
      const sideways = Math.abs(dx) > Math.abs(dy);
      if (start.mouse) {
        // mouse: holding the left button always moves the page (if there is room); it never turns
        // pages, and a drag is never counted as a click
        gesture.type = canPan() ? "pan" : "none";
      } else {
        // touch: sideways on a page that fits the width turns pages; otherwise move the page around
        gesture.type = sideways && !wide && Z.s <= 1.01 ? "swipe" : canPan() ? "pan" : sideways ? "swipe" : "none";
      }
      stage.classList.toggle("dragging", gesture.type === "pan");
    }
    if (gesture.type === "pan") {
      Z.x = start.from.x + dx;
      Z.y = start.from.y + dy;
      clampPan();
      applyZoom();
    } else if (gesture.type === "swipe") {
      img().style.transition = "none";
      img().style.transform = `translateX(${dx}px)`;  // the page follows the finger
    }
  });

  const end = (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.delete(e.pointerId);
    if (gesture?.type === "pinch") {
      if (pointers.size === 0) {
        if (Z.s < 1.05) resetZoom(true);
        gesture = null;
      } else {  // one finger left after a pinch: continue as a pan from here
        const [p] = [...pointers.values()];
        start = { x: p.x, y: p.y, t: Date.now(), from: { ...Z } };
        gesture = { type: "pan" };
      }
      return;
    }
    if (pointers.size) return;
    stage.classList.remove("dragging");
    const dx = e.clientX - (start?.x ?? e.clientX), dt = Date.now() - (start?.t ?? 0);
    const type = gesture?.type;
    gesture = null;
    if (type === "swipe") {
      const fast = Math.abs(dx) > 30 && dt < 250;
      if (Math.abs(dx) > innerWidth * 0.18 || fast) {
        const forward = R.mode === "rtl" ? dx > 0 : dx < 0;
        turn(forward, Math.sign(dx));
      } else {
        resetZoom(true);  // not far enough: snap back
      }
      return;
    }
    if (type !== "tap") return;
    if (R.drawer) return setDrawer(false);  // a tap beside the open drawer just closes it
    // tap or double tap
    maybeAutoFullscreen();
    const now = Date.now();
    if (now - lastTap.t < 300 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 40 && !locked()) {
      clearTimeout(tapTimer);
      lastTap = { t: 0, x: 0, y: 0 };
      if (Z.s > 1.01) resetZoom(true);
      else { zoomAt(2.5, e.clientX, e.clientY); applyZoom(true); }
      return;
    }
    lastTap = { t: now, x: e.clientX, y: e.clientY };
    const x = e.clientX / innerWidth, y = e.clientY / innerHeight;
    tapTimer = setTimeout(() => {
      if (Z.s > 1.01) return toggleUi();  // zoomed: taps only show/hide the controls
      const act = tapAction(x, y);
      if (act === "next") next();
      else if (act === "prev") prev();
      else toggleUi();
    }, 230);
  };
  stage.addEventListener("pointerup", end);
  stage.addEventListener("pointercancel", end);

  // desktop: mouse wheel zooms around the cursor
  stage.addEventListener("wheel", (e) => {
    if (locked()) return;
    e.preventDefault();
    zoomAt(Z.s * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY);
    applyZoom();
  }, { passive: false });
}

/* what a tap at (x, y) (fractions of the screen) does: "next", "prev" or "ui" (show/hide controls).
   Tap zones are set apart from the reading direction (Settings → Reader): swipe, page order and the
   slide stay with the direction, only where you tap changes. */
function tapAction(x, y) {
  const layout = settings.reader_tap_layout || "default";
  if (layout === "off") return "ui";
  // forward is on the right, except in RTL with "Invert tap zones" off (left, like a paper book)
  const nextOnLeft = R.mode === "rtl" && !settings.reader_tap_invert;
  const d = nextOnLeft ? x : 1 - x;  // 0 at the "next" edge, 1 at the "previous" edge
  if (layout === "one-handed") {
    if (d > 1 - TAP_EDGE) return "prev";
    if (Math.abs(x - 0.5) < 0.2 && Math.abs(y - 0.5) < 0.2) return "ui";  // a box in the middle
    return "next";
  }
  return d < TAP_SIDE ? "next" : d > 1 - TAP_SIDE ? "prev" : "ui";
}

addEventListener("resize", () => { if (R.book && img()) { clampPan(); applyZoom(); } });

/* swipe/tap/key page turn with a slide: out towards `outDir` (-1 left, 1 right), new page in from the other side */
function turn(forward, outDir) {
  const canGo = forward ? R.page < R.book.page_count - 1 : R.page > 0;
  if (!canGo) {
    resetZoom(true);
    return forward ? showEndCard() : (R.book.prev && toast(`Start of book. Previous: ${R.book.prev.label}`));
  }
  const el = img();
  el.style.transition = "transform .16s ease-in, opacity .16s";
  el.style.transform = `translateX(${outDir * innerWidth}px)`;
  el.style.opacity = "0";
  setTimeout(() => {
    R.page += forward ? 1 : -1;
    showPage(-outDir);
  }, 160);
}

/* show R.page; `fromDir` is the side the new page slides in from (-1 left, 1 right, 0 none) */
function showPage(fromDir = 0) {
  const el = img();
  if (!el) return;
  $("#reader .endcard")?.remove();
  Object.assign(Z, { s: 1, x: 0, y: 0, hiRes: false });
  $("#reader .stage").classList.remove("zoomed");
  el.style.transition = "none";
  el.style.opacity = "1";
  el.style.transform = fromDir ? `translateX(${fromDir * 30}%)` : "none";
  if (fromDir) el.style.opacity = "0.3";
  const enter = () => {
    if (!fromDir) return;
    requestAnimationFrame(() => {
      el.style.opacity = "1";
      if (Z.s > 1.01) return;  // zoomed in meanwhile: keep the zoom, don't slide
      el.style.transition = "transform .2s ease-out, opacity .2s";
      el.style.transform = "translate(0, 0) scale(1)";
      el.style.opacity = "1";
    });
  };
  el.onload = () => {
    el.onload = null;  // once per page: the hi-res swap later must not re-run the slide-in (it would reset zoom)
    $("#reader .stage")?.classList.toggle("can-pan", canPan());  // e.g. "fit width" pages taller than the screen
    enter();
  };
  el.src = pageUrl(R.page);
  if (el.complete) enter();
  updatePageUi();
  preload();
  saveProgress();
  ovShow();
}

function preload() {
  const n = +settings.preload || 4;
  // let go of pages far behind or ahead, so a long book doesn't pile up in the phone's memory
  // (going back is still quick: the browser's HTTP cache has them)
  for (const i of R.preloaded.keys()) if (i < R.page - 2 || i > R.page + n) R.preloaded.delete(i);
  for (let i = R.page + 1; i <= R.page + n && i < R.book.page_count; i++) {
    if (!R.preloaded.has(i)) {
      const im = new Image();
      im.src = pageUrl(i);
      R.preloaded.set(i, im);
    }
  }
}

function updatePageUi() {
  $("#r-num").textContent = R.page + 1;
  // filled part of the slider track (the browser only colours it natively in some engines)
  $("#r-slider").style.setProperty("--fill", `${R.book.page_count > 1 ? (R.page / (R.book.page_count - 1)) * 100 : 100}%`);
  $("#r-slider").value = R.page + 1;
  $("#r-page").value = R.page;
  $("#r-page-label").textContent = `${R.page + 1} / ${R.book.page_count}`;
  // thin bar at the very bottom: how far into this book you are (right to left for RTL)
  for (const seg of $("#r-progress .rp-track").children) {
    const i = +seg.dataset.p;
    seg.className = i < R.page ? "read" : i === R.page ? "read cur" : "";
  }
  $("#rp-cur").textContent = R.page + 1;
}

function goTo(i) {
  R.page = Math.max(0, Math.min(i, R.book.page_count - 1));
  if (R.mode === "vertical") {
    $("#reader .vertical").children[R.page]?.scrollIntoView();
    updatePageUi();
    saveProgress();
    ovShow();
  } else showPage();
}

/* the old page slides out the way you read: left in LTR, right in RTL */
const forwardOut = () => (R.mode === "rtl" ? 1 : -1);
function next() { turn(true, forwardOut()); }
function prev() { turn(false, -forwardOut()); }

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

function setupVertical() {
  const box = $("#reader .vertical");
  // each page in a box of its own, so a plugin overlay can sit on top of it
  box.innerHTML = Array.from({ length: R.book.page_count }, (_, i) =>
    `<div class="vpage" data-p="${i}"><img data-i="${i}" loading="lazy" src="${pageUrl(i)}" alt="">`
    + `<div class="ov-layer"></div></div>`).join("") +
    `<div style="padding:40px 20px;text-align:center">
      ${R.book.next ? `<button class="btn primary" id="v-next">Next: ${esc(R.book.next.label)}</button>`
        : `<p class="muted">End of the series.</p>`}</div>`;
  box.querySelectorAll("img").forEach((im) => (im.onload = () => {
    im.classList.add("loaded");
    ovFit(im.nextElementSibling);  // overlay boxes drawn before the image had a size
  }));
  $("#v-next")?.addEventListener("click", () => location.replace(`#/read/${R.book.next.id}?page=0`));
  // current page = the image in the middle of the screen (robust to images loading late)
  let ticking = false;
  box.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      // the middle of the screen may be over an overlay box: its page box says which page it is
      const p = document.elementFromPoint(innerWidth / 2, innerHeight / 2)?.closest?.(".vpage")?.dataset.p;
      if (p !== undefined && +p !== R.page) {
        R.page = +p;
        updatePageUi();
        saveProgress();
        ovShow();
      }
    });
  }, { passive: true });
  box.children[R.page]?.scrollIntoView();
  updatePageUi();
  ovShow();
  box.addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    if (R.drawer) return setDrawer(false);
    maybeAutoFullscreen();
    toggleUi();
  });
}

/* ---------------- plugin overlays ----------------
   A plugin with an overlay (e.g. a translator) draws boxes over pages. Each one is a toggle in the
   drawer, remembered per series on this device. The server answers per page with layers of boxes
   whose positions are fractions of the page; a layer still `pending` is asked for again a little
   later, while that page is still being read. In paged mode the layer copies the image's size and
   transform (zoom, pan, swipe, slide), so it moves with the page without touching that code. */

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
  $("#reader").querySelectorAll("[data-ov]").forEach((b) => {
    const on = R.ov.on.includes(b.dataset.ov);
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on);
    b.querySelector("b").textContent = on ? "On" : "Off";
  });
  const unhide = $("#r-ov-unhide");
  if (unhide) {
    const n = R.book.hidden_boxes || 0;
    unhide.hidden = !n || !R.ov.on.length;
    unhide.querySelector("span").textContent = `Show ${n} hidden ${n === 1 ? "box" : "boxes"}`;
  }
  // how the boxes look: only while an overlay is on
  const opacity = $("#r-ov-opacity"), outline = $("#r-ov-outline");
  if (opacity) {
    opacity.hidden = outline.hidden = !R.ov.on.length;
    opacity.querySelector("b").textContent = OV_OPACITY[ovOpacity()];
    outline.querySelector("b").textContent = OV_OUTLINE[ovOutline()];
  }
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
async function ovSetStyle(change) {
  Object.assign(settings, change);
  ovApplyStyle();
  ovUpdateButtons();
  $("#reader").querySelectorAll(".ov-layer").forEach(ovFit);
  restartHideTimer();
  try { await api("/api/settings", { method: "PUT", body: change }); }
  catch (e) { toast(e.message, 6000, "error"); }
}

async function ovAction(a) {
  if (a.confirm && !confirm(a.confirm)) return;
  try {
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
  if (R.mode !== "vertical") ovDraw(R.page);  // the layer may still show the previous page
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

function ovLayer(i) {
  if (R.mode === "vertical") return $(`#reader .vpage[data-p="${i}"] .ov-layer`);
  return i === R.page ? $("#r-ov") : null;
}

function ovDraw(i) {
  const layer = ovLayer(i);
  if (!layer) return;
  const layers = R.ov?.pages.get(i)?.layers || [];
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
    for (const type of ["pointerdown", "pointerup"]) btn.addEventListener(type, (e) => e.stopPropagation());
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

/* paged mode: keep the layer exactly over the image: same box, same transform, same fade */
function ovFollowImage() {
  const el = img(), layer = $("#r-ov");
  if (!el || !layer) return;
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
}

addEventListener("resize", () => {
  if (R.ov && R.mode === "vertical") $("#reader").querySelectorAll(".ov-layer").forEach(ovFit);
});

/* ---------------- controls: show/hide, auto-hide after a few seconds ----------------
   Tap/click toggles the bars. Once shown, they hide on their own after `reader_autohide`
   seconds (0 = never). Any use of the bars restarts the countdown, and they wait while
   the slider is held, the mouse is over them, or a dropdown has focus. */

function uiShown() { return !$("#reader").classList.contains("ui-hidden"); }

function toggleUi() { uiShown() ? hideUi() : showUi(); }

function showUi() {
  $("#reader").classList.remove("ui-hidden");
  restartHideTimer();
}

function hideUi() {
  clearTimeout(R.hideTimer);
  $("#reader").classList.add("ui-hidden");
  // hidden bars must not keep focus, or arrow keys would still change a dropdown/slider
  if (inChrome(document.activeElement)) document.activeElement.blur();
}

function inChrome(el) { return !!el?.closest?.("#reader .chrome, #reader .drawer"); }

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

function maybeAutoFullscreen() {
  if (R.firstTap && settings.auto_fullscreen && !document.fullscreenElement && !androidApp) {
    document.documentElement.requestFullscreen?.().catch(() => {});
  }
  R.firstTap = false;
}

/* both fullscreen buttons show what a tap will do; hidden where the browser has no fullscreen (iPhone)
   and in the Android app, which is always fullscreen */
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
  ovReset(null);
  R.book = null;
  R.firstTap = true;
  clearTimeout(R.hideTimer);
  Object.assign(R, { sliding: false, hovering: false, drawer: false });
  $("#reader").hidden = true;
  $("#reader").innerHTML = "";
  document.body.style.overflow = "";
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  return saved;
}

document.addEventListener("keydown", (e) => {
  if ($("#reader").hidden || !R.book) return;
  if (e.key === "Escape") return R.drawer ? setDrawer(false) : leaveReader();
  if (R.mode === "vertical" || e.target.tagName === "SELECT") return;
  if (e.key === "ArrowRight") R.mode === "rtl" ? prev() : next();
  else if (e.key === "ArrowLeft") R.mode === "rtl" ? next() : prev();
  else if (e.key === " ") next();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") saveProgress(true);
  else if (!$("#reader").hidden) requestWakeLock();
});

/* the Android app's volume keys (Settings → Android app): down = next page, up = previous.
   Vertical mode scrolls most of a screen instead. */
function volumeKey(dir) {
  if ($("#reader").hidden || !R.book) return;
  const forward = dir === "next";
  if (R.mode === "vertical") {
    $("#reader .vertical")?.scrollBy({ top: (forward ? 1 : -1) * innerHeight * 0.85, behavior: "smooth" });
  } else if (forward) next();
  else prev();
}

/* the Android back button: closes the options drawer, then leaves the reader */
function back() {
  if ($("#reader").hidden) return false;
  if (R.drawer) setDrawer(false);
  else leaveReader();
  return true;
}

return {
  open: openReader,
  close: closeReader,
  volumeKey,
  back,
  get isOpen() { return !$("#reader").hidden; },
};
})();
