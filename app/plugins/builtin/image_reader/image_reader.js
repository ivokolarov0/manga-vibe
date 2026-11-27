/* The image reader (built-in plugin, see __init__.py): draws a book's pages for the reader host
   (reader.js), paged or vertical, with zoom and pan, taps and swipes, the wheel and keys, the fit and the
   page look. It does the reader:images job (Plugin API 24): the host keeps the bars, the reading options,
   progress, plugin overlays, read aloud and the Android keys, and asks this for the pages. Uses
   window.MangaVibe only. */
(() => {
"use strict";

const WIDTHS = [720, 1080, 1440, 1800, 2160, 2880];
const TAP_SIDE = 0.25;  // width of each page-turn tap zone (left: back, right: forward); the middle toggles the controls
const CLICK_SIDE = 0.4;  // the same with a mouse: a wide screen leaves only a narrow middle strip for the controls
const TAP_EDGE = 0.2;   // one-handed layout: the narrow "previous" strip; the rest turns forward
// the words in the page's language (MangaVibe.t, issue #263)
const MV = window.MangaVibe;
const FITS = { screen: MV.t("Fit screen"), width: MV.t("Fit width"), height: MV.t("Fit height"), original: MV.t("Original size") };
// how the page images look (reader settings): easier on the eyes, e.g. white pages at night
const pctText = (n) => MV.fmtNum(n / 100, { style: "percent" });
const BRIGHTNESS = { 100: MV.t("Normal"), 85: pctText(85), 70: pctText(70), 55: pctText(55), 40: pctText(40) };
const CONTRAST = { 100: MV.t("Normal"), 125: MV.t("More"), 150: MV.t("Most"), 75: MV.t("Less") };
const COLORS = { normal: MV.t("Normal"), dark: MV.t("Inverted"), warm: MV.t("Warm"), "dark-warm": MV.t("Inverted, warm") };
// two pages side by side (#276): off, on wide screens (landscape and at least SPREAD_MIN_WIDTH), always
const SPREADS = { off: MV.t("Off"), auto: MV.t("On wide screens"), on: MV.t("Always") };
const SPREAD_MIN_WIDTH = 700;
const MAX_ZOOM = 5;

const svg = (body) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ICONS = {
  fit: svg(`<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>`),
  // locked: closed shackle; unlocked: shackle swung open, so the two are easy to tell apart
  lock: svg(`<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>`),
  unlock: svg(`<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>`),
  sun: svg(`<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>`),
  contrast: svg(`<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>`),
  colors: svg(`<path d="M12 3a9 9 0 1 0 9 9c0-1.5-1.5-2-3-2h-2a2 2 0 0 1-2-2c0-1.5 1-2 1-3.5S13.5 3 12 3z"/><circle cx="8" cy="11" r="1"/><circle cx="10" cy="7.5" r="1"/>`),
  // an open book
  book: svg(`<path d="M12 6v14M12 6C10 4.5 7 4 4 4v14c3 0 6 .5 8 2M12 6c2-1.5 5-2 8-2v14c-3 0-6 .5-8 2"/>`),
  shift: svg(`<path d="M4 12h14M14 8l4 4-4 4"/><path d="M4 6v12"/>`),
  order: svg(`<path d="M20 8H6l3-3M4 16h14l-3 3"/>`),
};

const escHtml = (s) => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

/* the value after `cur` in a {value: label} list, wrapping around */
function cycle(list, cur) {
  const keys = Object.keys(list);
  return keys[(keys.indexOf(cur) + 1) % keys.length];
}

// what is open: the book, the host's ctl, the element to draw in, the mode and the page shown (with two
// pages side by side: the first of them). two: two pages are shown side by side now. aspect: page ->
// its width / height, learned as the pictures load (for the book in aspectOf).
const R = { book: null, ctl: null, el: null, mode: "paged", page: 0, preloaded: new Map(), off: [],
  two: false, aspect: new Map(), aspectOf: null };
const opts = () => R.ctl.settings;  // the reader settings (Settings → Reader), from the host

/* a listener outside R.el, taken away again on close */
function listen(target, type, fn, opts) {
  target.addEventListener(type, fn, opts);
  R.off.push(() => target.removeEventListener(type, fn, opts));
}

function fit() { return opts().reader_fit in FITS ? opts().reader_fit : "screen"; }

/* the width to ask the server for page i: the screen's, or half of it for a page that shares it */
function pageWidth(i) {
  if (fit() === "original") return 0;
  const room = R.two && !wide(i) ? innerWidth / 2 : innerWidth;
  const want = Math.max(room, fit() === "height" ? innerHeight * 0.75 : 0) * (devicePixelRatio || 1);
  return WIDTHS.find((w) => w >= want) || WIDTHS[WIDTHS.length - 1];
}

function pageUrl(i, full = false) {
  const w = full ? 0 : pageWidth(i);
  return `/api/books/${R.book.id}/pages/${i}?v=${R.book.version}${w ? "&w=" + w : ""}`;
}

/* the page shown changed: the host updates its numbers, saves progress and draws the overlays. With
   two pages, it hears the last one (read up to there) and the first. */
function shown(i) {
  R.page = i;
  const g = spreadOf(i);
  R.ctl.shown(g[g.length - 1], g[0]);
}

/* ---------------- two pages side by side (#276) ----------------
   Settings → Reader → Two pages (reader_spread), for paged mode. The cover shows alone, then the pages
   pair up as printed; a picture wider than tall (a double page scanned as one) shows alone, and the
   pairing goes on after it. Per series on this device: the pairs shifted by one page (for books whose
   pairs come out wrong) and the order of the two (right to left for manga, from the book). */

function spread() { return opts().reader_spread in SPREADS ? opts().reader_spread : "off"; }

/* should two pages show now? "On wide screens": not while the screen is upright or narrow */
function twoPages() {
  const s = spread();
  return R.mode === "paged" && (s === "on" || (s === "auto" && innerWidth > innerHeight && innerWidth >= SPREAD_MIN_WIDTH));
}

function seriesPref(name) {
  try { return localStorage.getItem(`reader-${name}:${R.book.series_id}`); } catch { return null; }
}
function setSeriesPref(name, value) {
  try { localStorage.setItem(`reader-${name}:${R.book.series_id}`, value); } catch {}
}
function shifted() { return seriesPref("spread-shift") === "1"; }
function rtl() {
  const v = seriesPref("spread-order");
  return v ? v === "rtl" : R.book.right_to_left !== false;
}

/* a double page: known once its picture has loaded (until then it counts as an ordinary page) */
function wide(i) { return (R.aspect.get(i) || 0) > 1; }
function learn(i, im) {
  if (im.naturalWidth && im.naturalHeight) R.aspect.set(i, im.naturalWidth / im.naturalHeight);
}

/* the pages shown together with page p, in book order: [p] or a pair */
function spreadOf(p) {
  if (!R.two) return [p];
  const n = R.book.page_count, alone = shifted() ? 2 : 1;  // the cover, and with the shift the page after it
  for (let i = 0; i < n;) {
    const g = i < alone || wide(i) || i + 1 >= n || wide(i + 1) ? [i] : [i, i + 1];
    if (p < i + g.length) return g;
    i += g.length;
  }
  return [p];
}

/* the last page on screen */
function lastShown() {
  const g = spreadOf(R.page);
  return g[g.length - 1];
}

/* the paged stage's content: one picture, or a box for two that zoom, pan and slide together */
function buildStage() {
  const st = stage();
  st.innerHTML = R.two ? `<div class="spread"></div>` : `<img alt="">`;
  if (!R.two) {
    img().onerror = () => MangaVibe.toast(MV.t("Could not load page {n}", { n: R.page + 1 }), 3000, "error");
    // the browser's own "drag the picture" would cancel our pointer events (Firefox ignores the CSS rule)
    img().draggable = false;
  }
}

/* paged mode drawn again, one or two pages (the setting or the screen changed) */
function relayout() {
  if (R.mode !== "paged" || !R.book) return;
  R.two = twoPages();
  buildStage();
  showPage();
  spreadButtons();
}

/* ---------------- paged mode: zoom, pan, swipe, page animations ----------------
   One <img> inside .stage (two pages: a .spread box with their pictures). Zoom and pan are a CSS
   transform on it (view()): translate(x, y) scale(s), origin at the centre. Pointer Events cover touch
   and mouse. The lock (🔒) turns zoom/pan off and keeps the page fitted. */

const Z = { s: 1, x: 0, y: 0, hiRes: false };
const pointers = new Map();  // pointerId -> {x, y}
let gesture = null;          // what the current touch is doing: pan | pinch | swipe | tap

function locked() {
  try { return localStorage.getItem("reader-lock") === "1"; } catch { return false; }
}

function setLocked(on) {
  try { localStorage.setItem("reader-lock", on ? "1" : "0"); } catch {}
  updateLockButton();
  if (on && view()) resetZoom(true);
  R.el?.querySelector(".vertical")?.classList.toggle("locked", on);
}

function updateLockButton() {
  const b = document.getElementById("r-lock");
  if (!b) return;
  b.innerHTML = locked() ? ICONS.lock : ICONS.unlock;
  b.classList.toggle("on", locked());
  b.title = locked() ? MV.t("Zoom locked (tap to allow zoom and moving)") : MV.t("Zoom allowed (tap to lock)");
  b.setAttribute("aria-label", b.title);
}

function img() { return R.el?.querySelector(".stage > img") || null; }
function stage() { return R.el.querySelector(".stage"); }
/* what zooms, pans and slides: the picture, or the box of two */
function view() { return R.el?.querySelector(".stage > img, .stage > .spread") || null; }
/* the pictures on screen, each with its page in data-p */
function pics() {
  const v = view();
  return !v ? [] : v.tagName === "IMG" ? [v] : [...v.querySelectorAll("img")];
}

/* `animate`: true for the usual .2s, or a length in seconds (a wheel or key step is quicker) */
function applyZoom(animate = false) {
  const el = view();
  if (!el) return;
  el.style.transition = animate ? `transform ${animate === true ? 0.2 : animate}s ease-out` : "none";
  el.style.transform = `translate(${Z.x}px, ${Z.y}px) scale(${Z.s})`;
  stage().classList.toggle("zoomed", Z.s > 1.01);
  stage().classList.toggle("can-pan", canPan());
}

/* keep the zoomed page covering the screen: no empty space at the edges */
function clampPan() {
  const el = view();
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
  clampPan();  // back at 1×, a page that fits is centred again; a taller one keeps its place
  maybeHiRes();
}

/* how far a page taller than the window can move up or down from the middle (0: it fits) */
function roomY() {
  const el = view();
  return el ? Math.max(0, (el.offsetHeight * Z.s - innerHeight) / 2) : 0;
}

/* is the page bigger than the screen (zoomed in, or "fit width"/"original" pages)? */
function canPan() {
  const el = view();
  return !locked() && (el.offsetWidth * Z.s > innerWidth + 1 || el.offsetHeight * Z.s > innerHeight + 1);
}

/* back to the fitted page. A page taller than the window ("fit width") stays about where it was
   instead of jumping to its middle. */
function resetZoom(animate = false) {
  Object.assign(Z, { x: Z.x / Z.s, y: Z.y / Z.s, s: 1 });
  if (view()) clampPan();
  applyZoom(animate);
}

/* zoomed in a lot: swap to the full-resolution pages so they stay sharp */
function maybeHiRes() {
  if (Z.s < 1.6 || Z.hiRes || fit() === "original") return;
  Z.hiRes = true;
  const book = R.book.id, page = R.page;
  for (const el of pics()) {
    const full = pageUrl(+el.dataset.p, true), pre = new Image();
    pre.onload = () => { if (Z.hiRes && R.book?.id === book && R.page === page && el.isConnected) el.src = full; };
    pre.src = full;
  }
}

function setupPaged() {
  R.el.innerHTML = `<div class="stage"></div>`;
  const st = stage();
  R.two = twoPages();
  buildStage();
  updateLockButton();
  showPage(0);

  let start = null, lastTap = { t: 0, x: 0, y: 0 }, tapTimer = null;

  st.addEventListener("dragstart", (e) => e.preventDefault());

  st.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse") {
      if (e.button !== 0) return;  // only the left button; middle/right do nothing here
      e.preventDefault();          // no text selection / native image drag
    }
    try { st.setPointerCapture(e.pointerId); } catch {}  // keeps getting moves outside the image
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2 && !locked()) {
      const [a, b] = [...pointers.values()];
      gesture = { type: "pinch", d0: Math.hypot(a.x - b.x, a.y - b.y), from: { ...Z } };
    } else if (pointers.size === 1) {
      start = { x: e.clientX, y: e.clientY, t: Date.now(), from: { ...Z }, mouse: e.pointerType === "mouse" };
      gesture = { type: "tap" };
    }
  });

  st.addEventListener("pointermove", (e) => {
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
      const broad = view().offsetWidth * Z.s > innerWidth + 1;
      const sideways = Math.abs(dx) > Math.abs(dy);
      if (start.mouse) {
        // mouse: holding the left button always moves the page (if there is room); it never turns
        // pages, and a drag is never counted as a click
        gesture.type = canPan() ? "pan" : "none";
      } else {
        // touch: sideways on a page that fits the width turns pages; otherwise move the page around
        gesture.type = sideways && !broad && Z.s <= 1.01 ? "swipe" : canPan() ? "pan" : sideways ? "swipe" : "none";
      }
      st.classList.toggle("dragging", gesture.type === "pan");
    }
    if (gesture.type === "pan") {
      Z.x = start.from.x + dx;
      Z.y = start.from.y + dy;
      clampPan();
      applyZoom();
    } else if (gesture.type === "swipe") {
      view().style.transition = "none";
      view().style.transform = `translate(${dx}px, ${Z.y}px)`;  // the page follows the finger
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
    st.classList.remove("dragging");
    const dx = e.clientX - (start?.x ?? e.clientX), dt = Date.now() - (start?.t ?? 0);
    const type = gesture?.type;
    gesture = null;
    if (type === "swipe") {
      const fast = Math.abs(dx) > 30 && dt < 250;
      if (Math.abs(dx) > innerWidth * 0.18 || fast) {
        turn(dx < 0, Math.sign(dx));  // swipe to the left: the next page comes in from the right
      } else {
        resetZoom(true);  // not far enough: snap back
      }
      return;
    }
    if (type !== "tap") return;
    if (R.ctl.tap(e)) return;  // the host took it (a tap beside the open drawer closes it)
    // tap or double tap
    const now = Date.now();
    if (now - lastTap.t < 300 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 40 && !locked()) {
      clearTimeout(tapTimer);
      lastTap = { t: 0, x: 0, y: 0 };
      if (Z.s > 1.01) resetZoom(true);
      else { zoomAt(2.5, e.clientX, e.clientY); applyZoom(true); }
      return;
    }
    lastTap = { t: now, x: e.clientX, y: e.clientY };
    const x = e.clientX / innerWidth, y = e.clientY / innerHeight, mouse = e.pointerType === "mouse";
    tapTimer = setTimeout(() => {
      if (!R.book) return;
      if (Z.s > 1.01) return R.ctl.menu();  // zoomed: taps don't turn pages, they only show or hide the menu
      const act = tapAction(x, y, mouse);
      if (act === "next") next();
      else if (act === "prev") prev();
      else R.ctl.menu();
    }, 230);
  };
  st.addEventListener("pointerup", end);
  st.addEventListener("pointercancel", end);

  // desktop: the wheel scrolls a page taller than the window, and turns pages at its ends;
  // Ctrl + wheel (and a touchpad pinch, which browsers send as one) zooms around the cursor
  st.addEventListener("wheel", (e) => {
    e.preventDefault();
    if (e.ctrlKey) {
      if (locked()) return;
      zoomAt(Z.s * Math.exp(-wheelDelta(e).y / 300), e.clientX, e.clientY);
      return applyZoom();
    }
    wheelScroll(e);
  }, { passive: false });
}

/* a wheel event's movement in pixels (Firefox counts a mouse wheel in lines) */
function wheelDelta(e) {
  const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? innerHeight : 1;
  let x = e.deltaX * unit, y = e.deltaY * unit;
  if (e.shiftKey && !x) [x, y] = [y, 0];  // Shift + wheel: sideways
  return { x, y };
}

/* One spin of the wheel, or one touchpad swipe with its momentum, is a run of events close
   together. A run either scrolls the page or turns it, never both: the page stops at its end,
   and the next run turns it. So a fast spin can't fly through several pages. */
const W = { t: 0, acc: 0, done: false };
const WHEEL_GAP = 250;   // ms between events that still belong to the same run
const WHEEL_TURN = 50;   // px of wheel past the end of the page that turn it

function wheelScroll(e) {
  const d = wheelDelta(e);
  if (e.timeStamp - W.t > WHEEL_GAP) Object.assign(W, { acc: 0, done: false });
  W.t = e.timeStamp;
  const el = view();
  if (!el) return;
  const sideways = Math.abs(d.x) > Math.abs(d.y), delta = sideways ? d.x : d.y;
  if (!delta) return;
  const room = sideways ? Math.max(0, (el.offsetWidth * Z.s - innerWidth) / 2) : roomY();
  const pos = sideways ? Z.x : Z.y;
  // down (or right) moves the page up (or left): towards -room
  const to = Math.max(-room, Math.min(room, pos - delta));
  if (to !== pos) {
    Z[sideways ? "x" : "y"] = to;
    applyZoom(Math.abs(delta) >= 30 ? 0.12 : false);  // a mouse notch glides, a touchpad follows the fingers
    W.done = true;
    return;
  }
  if (W.done || Z.s > 1.01) return;  // zoomed in: the wheel only moves the page, like taps don't turn it
  W.acc += Math.abs(delta);
  if (W.acc < WHEEL_TURN) return;
  W.done = true;
  if (delta > 0) next(); else prev(!sideways);  // scrolling back up shows the end of the page before
}

/* ↓, Page Down and Space: down the page by a step, then on to the next page (↑, Page Up, Shift +
   Space the other way). A key held down stops at the end of a page instead of racing through the book. */
function keyScroll(down, step, repeat) {
  const room = roomY(), to = Math.max(-room, Math.min(room, Z.y + (down ? -step : step)));
  if (to !== Z.y) {
    Z.y = to;
    return applyZoom(0.15);
  }
  if (repeat || Z.s > 1.01) return;
  if (down) next(); else prev(true);
}

/* what a tap at (x, y) (fractions of the screen) does: "next", "prev" or "ui" (open the menu).
   Forward is always on the right, like every digital reader (Settings → Reader → Tap zones). */
function tapAction(x, y, mouse = false) {
  const layout = opts().reader_tap_layout || "default";
  if (layout === "off") return "ui";
  const d = 1 - x;  // 0 at the "next" (right) edge, 1 at the "previous" (left) edge
  if (layout === "one-handed") {
    if (d > 1 - TAP_EDGE) return "prev";
    if (Math.abs(x - 0.5) < 0.2 && Math.abs(y - 0.5) < 0.2) return "ui";  // a box in the middle
    return "next";
  }
  const side = mouse ? CLICK_SIDE : TAP_SIDE;
  return d < side ? "next" : d > 1 - side ? "prev" : "ui";
}

/* swipe/tap/key page turn with a slide: out towards `outDir` (-1 left, 1 right), new page in from
   the other side. `atEnd`: a page taller than the window comes in showing its bottom (scrolling back). */
function turn(forward, outDir, atEnd = false) {
  R.ctl.reading();  // turning the page is reading on: the host puts its controls away
  const canGo = forward ? lastShown() < R.book.page_count - 1 : R.page > 0;
  if (!canGo) {
    resetZoom(true);
    return forward ? R.ctl.end() : R.ctl.start();
  }
  const el = view();
  el.style.transition = "transform .16s ease-in, opacity .16s";
  el.style.transform = `translate(${Z.x + outDir * innerWidth}px, ${Z.y}px) scale(${Z.s})`;
  el.style.opacity = "0";
  const book = R.book, to = forward ? lastShown() + 1 : spreadOf(R.page - 1)[0];
  setTimeout(() => {
    if (R.book !== book) return;
    R.page = to;
    showPage(-outDir, atEnd);
  }, 160);
}

/* show R.page (two pages: the pair it is in); `fromDir` is the side the new page slides in from (-1
   left, 1 right, 0 none). The browser keeps painting the old picture in the <img> until the new one is
   decoded, so a sliding page stays hidden until then; only then does it slide in. A page taller than
   the window starts at its top, like a page of a book (with `atEnd`, at its bottom). */
let showToken = 0;
function showPage(fromDir = 0, atEnd = false) {
  const el = view();
  if (!el) return;
  const st = stage(), group = spreadOf(R.page);
  R.page = group[0];
  Object.assign(Z, { s: 1, x: 0, y: 0, hiRes: false });
  st.classList.remove("zoomed");
  el.style.transition = "none";
  el.style.transform = fromDir ? `translateX(${fromDir * 30}%)` : "none";
  el.style.opacity = fromDir ? "0" : "1";
  const token = ++showToken;  // a newer turn makes this one stale
  clearTimeout(st._spin);
  st._spin = setTimeout(() => st.classList.add("waiting"), 150);  // slow page: show a spinner
  if (R.two) {
    // new pictures each time (their overlay layers go with them); the box stays hidden while it slides
    el.classList.toggle("pair", group.length > 1);
    el.classList.toggle("rtl", rtl());
    el.innerHTML = group.map(() => `<img alt="" draggable="false">`).join("");
  }
  const pictures = pics();
  pictures.forEach((im, k) => {
    const i = group[k];
    im.dataset.p = i;
    im.src = pageUrl(i);
    im.alt = MV.t("Page {page} of {pages}", { page: i + 1, pages: R.book.page_count });
    if (R.two) im.onerror = () => MangaVibe.toast(MV.t("Could not load page {n}", { n: i + 1 }), 3000, "error");
  });
  // Firefox resolves decode() at once for the old picture while the new one is still loading
  // (Chrome waits), so wait for the load first: else the old page slides in, then blinks to the new one.
  // decode() fails on a broken page (onerror shows a toast) or when src changes again; show the page anyway
  const ready = (im) => (im.complete ? Promise.resolve() : new Promise((done) => {
    im.addEventListener("load", done, { once: true });
    im.addEventListener("error", done, { once: true });
  })).then(() => im.decode()).catch(() => {});
  Promise.all(pictures.map(ready)).then(() => {
    if (token !== showToken || view() !== el) return;
    pictures.forEach((im, k) => learn(group[k], im));
    // one of them turned out to be a double page: pair them again (it shows alone)
    if (spreadOf(R.page).join() !== group.join()) return showPage(fromDir, atEnd);
    clearTimeout(st._spin);
    st.classList.remove("waiting");
    st.classList.toggle("can-pan", canPan());  // e.g. "fit width" pages taller than the screen
    if (Z.s > 1.01) { el.style.opacity = "1"; return; }  // zoomed in meanwhile: keep the zoom, don't slide
    if (Z.x === 0 && Z.y === 0) Z.y = atEnd ? -roomY() : roomY();  // not moved meanwhile: top (or bottom)
    if (!fromDir) return applyZoom();
    // the start position, at the page's height, so it only slides sideways
    el.style.transform = `translate(${fromDir * 30}%, ${Z.y}px)`;
    void el.offsetWidth;  // apply the start position first, so the slide below animates from it
    el.style.transition = "transform .2s ease-out, opacity .2s";
    el.style.transform = `translate(${Z.x}px, ${Z.y}px) scale(1)`;
    el.style.opacity = "1";
  });
  preload();
  shown(R.page);
}

/* the pages after the ones shown, ahead of time (they also tell which ones are double pages) */
function preload() {
  const n = +opts().preload || 4, last = lastShown();
  // let go of pages far behind or ahead, so a long book doesn't pile up in the phone's memory
  // (going back is still quick: the browser's HTTP cache has them)
  for (const i of R.preloaded.keys()) if (i < R.page - 2 || i > last + n) R.preloaded.delete(i);
  for (let i = last + 1; i <= last + n && i < R.book.page_count; i++) {
    if (!R.preloaded.has(i)) {
      const im = new Image();
      const sizes = R.aspect;
      im.onload = () => { if (R.aspect === sizes) learn(i, im); };  // not after another book opened
      im.src = pageUrl(i);
      R.preloaded.set(i, im);
    }
  }
}

/* the old page slides out to the left going forward, to the right going back */
function next() { turn(true, -1); }
function prev(atEnd = false) { turn(false, 1, atEnd); }

/* ---------------- vertical mode (webtoons) ---------------- */

function vbox() { return R.el?.querySelector(".vertical") || null; }

function setupVertical() {
  R.el.innerHTML = `<div class="vertical${locked() ? " locked" : ""}"></div>`;
  const box = vbox();
  // each page in a box of its own, so a plugin overlay can sit on top of it (the host puts it there)
  box.innerHTML = Array.from({ length: R.book.page_count }, (_, i) =>
    `<div class="vpage" data-p="${i}"><img data-i="${i}" loading="lazy" src="${pageUrl(i)}" alt="${escHtml(MV.t("Page {n}", { n: i + 1 }))}"></div>`).join("") +
    `<div style="padding:40px 20px;text-align:center">
      ${R.book.next ? `<button class="btn primary" id="v-next">${escHtml(MV.t("Next: {book}", { book: R.book.next.label }))}</button>`
        : `<p class="muted">${escHtml(MV.t("End of the series."))}</p>`}</div>`;
  box.querySelectorAll("img").forEach((im) => {
    im.onload = () => im.classList.add("loaded");
    im.addEventListener("error", () => im.classList.add("failed"));  // no spinner forever on a broken page
  });
  box.querySelector("#v-next")?.addEventListener("click", () => R.ctl.openBook("next"));
  // current page = the image in the middle of the screen (robust to images loading late)
  let ticking = false;
  box.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      if (!R.book) return;
      // the middle of the screen may be over an overlay box: its page box says which page it is
      const p = document.elementFromPoint(innerWidth / 2, innerHeight / 2)?.closest?.(".vpage")?.dataset.p;
      if (p !== undefined && +p !== R.page) shown(+p);
    });
  }, { passive: true });
  box.children[R.page]?.scrollIntoView();
  shown(R.page);
  box.addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    if (R.ctl.tap(e)) return;
    R.ctl.menu();
  });
}

/* ---------------- page look: brightness, contrast, colours ----------------
   A CSS filter on the page images only (overlay boxes and the controls stay as they are). Reader
   settings like the fit: changed from the reading options or Settings → Reader. */

function brightness() { const v = +opts().reader_brightness; return v in BRIGHTNESS ? v : 100; }
function contrast() { const v = +opts().reader_contrast; return v in CONTRAST ? v : 100; }
function colors() { return opts().reader_colors in COLORS ? opts().reader_colors : "normal"; }

/* "Inverted" turns white pages black; the hue turn undoes what inverting does to colours, so a red
   stays red. It goes first, so lowering the brightness dims the (now white) letters, not a black page. */
function pageFilter() {
  const c = colors(), f = [];
  if (c.startsWith("dark")) f.push("invert(1) hue-rotate(180deg)");
  if (c.endsWith("warm")) f.push("sepia(0.45)");
  if (contrast() !== 100) f.push(`contrast(${contrast() / 100})`);
  if (brightness() !== 100) f.push(`brightness(${brightness() / 100})`);
  return f.join(" ") || "none";
}

function applyLook() {
  R.el?.style.setProperty("--page-filter", pageFilter());
  for (const [id, text, changed] of [["r-bright", BRIGHTNESS[brightness()], brightness() !== 100],
    ["r-contrast", CONTRAST[contrast()], contrast() !== 100], ["r-colors", COLORS[colors()], colors() !== "normal"]]) {
    const b = document.getElementById(id);
    if (!b) continue;
    b.querySelector("b").textContent = text;
    b.classList.toggle("on", changed);
  }
  // the drawer's row: what isn't normal, e.g. "Inverted · 70%"
  const state = document.getElementById("r-look-state");
  if (state) {
    const parts = [colors() !== "normal" && COLORS[colors()], brightness() !== 100 && BRIGHTNESS[brightness()],
      contrast() !== 100 && MV.t("{level} contrast", { level: CONTRAST[contrast()] })].filter(Boolean);
    state.textContent = parts.join(" · ") || MV.t("Normal");
    state.parentElement.classList.toggle("on", parts.length > 0);
  }
}

/* the drawer changed the look: applied at once, saved as a reader setting */
function setLook(change) {
  R.ctl.keepControls();
  R.ctl.saveSettings(change);
  applyLook();
}

/* ---------------- what the host asks for ---------------- */

function open(el, book, ctl) {
  close();
  Object.assign(R, { book, ctl, el, mode: ctl.mode === "vertical" ? "vertical" : "paged", page: ctl.page, two: false });
  R.preloaded.clear();
  if (R.aspectOf !== book.id) Object.assign(R, { aspect: new Map(), aspectOf: book.id });  // kept when drawn again
  el.className = `img-reader fit-${fit()}`;  // reader.css: the fit of the pages
  applyLook();
  listen(window, "resize", () => {
    if (!R.book || !view()) return;
    if (twoPages() !== R.two) return relayout();  // turned upright or sideways ("On wide screens")
    clampPan();
    applyZoom();
  });
  if (R.mode === "vertical") setupVertical(); else setupPaged();
}

function close() {
  R.off.forEach((off) => off());
  R.off = [];
  pointers.clear();
  gesture = null;
  showToken++;
  R.preloaded.clear();
  if (R.el) R.el.innerHTML = "";
  Object.assign(R, { book: null, el: null });
}

function goTo(i) {
  if (!R.book) return false;
  const page = Math.max(0, Math.min(+i, R.book.page_count - 1));
  if (R.mode === "vertical") {
    vbox().children[page]?.scrollIntoView();
    shown(page);
  } else {
    R.page = page;
    showPage();
  }
  return true;
}

/* forward or back a page. how: "volume" (the Android keys) scrolls most of a screen in vertical mode */
function step(forward, how = "page") {
  if (!R.book) return false;
  if (R.mode === "vertical") {
    if (how === "volume") vbox()?.scrollBy({ top: (forward ? 1 : -1) * innerHeight * 0.85, behavior: "smooth" });
    else goTo(R.page + (forward ? 1 : -1));
  } else if (forward) next();
  else prev();
  return true;
}

/* zoom to scale s around the screen point (px, py), for a script (MangaVibe.reader.zoom, Plugin API 41,
   e.g. eye control). Paged mode only, and not while the zoom is locked; s 1 is the fitted page. */
function zoomTo(s, px = innerWidth / 2, py = innerHeight / 2, animate = true) {
  if (!R.book || R.mode === "vertical" || locked() || !view()) return false;
  if (s <= 1.01) resetZoom(animate);
  else { zoomAt(s, px, py); applyZoom(animate); }
  return true;
}

/* move the page by (dx, dy) pixels, for a script (MangaVibe.reader.pan): paged, a zoomed page or one
   bigger than the screen moves (dx/dy > 0 shows more of its left/top); vertical, the strip scrolls by dy.
   Gives { x, y }: how far it really moved, 0 at an edge (so the script can turn the page there). */
function panBy(dx, dy, animate = false) {
  if (!R.book) return false;
  if (R.mode === "vertical") {
    const box = vbox();
    if (!box) return false;
    const before = box.scrollTop;
    box.scrollTop = before - dy;
    return { x: 0, y: before - box.scrollTop };
  }
  if (locked() || !view()) return false;
  const from = { x: Z.x, y: Z.y };
  Z.x += dx;
  Z.y += dy;
  clampPan();
  applyZoom(animate);
  return { x: Z.x - from.x, y: Z.y - from.y };
}

/* the keys the host leaves to the reader; true: it used the key */
function key(e) {
  if (!R.book) return false;
  if (R.mode === "vertical") {
    const s = { ArrowDown: 0.2, ArrowUp: -0.2, PageDown: 0.85, PageUp: -0.85, " ": e.shiftKey ? -0.85 : 0.85 }[e.key];
    if (!s) return false;
    vbox()?.scrollBy({ top: s * innerHeight, behavior: "smooth" });
    return true;
  }
  const keys = {
    ArrowRight: () => next(), ArrowLeft: () => prev(),
    ArrowDown: () => keyScroll(true, innerHeight * 0.2, e.repeat),
    ArrowUp: () => keyScroll(false, innerHeight * 0.2, e.repeat),
    PageDown: () => keyScroll(true, innerHeight * 0.85, e.repeat),
    PageUp: () => keyScroll(false, innerHeight * 0.85, e.repeat),
    " ": () => keyScroll(!e.shiftKey, innerHeight * 0.85, e.repeat),
    Home: () => goTo(0), End: () => goTo(R.book.page_count - 1),
  };
  if (!keys[e.key]) return false;
  keys[e.key]();
  return true;
}

/* the wheel over the host's own bars (the progress strip at the bottom) still scrolls the pages */
function wheel(e) {
  if (!R.book) return;
  if (R.mode === "vertical") vbox()?.scrollBy(0, wheelDelta(e).y);
  else if (!e.ctrlKey) wheelScroll(e);
}

/* the element that shows page i now, for the host's overlays (null: it isn't on screen) */
function pageBox(i) {
  if (!R.book) return null;
  if (R.mode === "vertical") return R.el.querySelector(`.vpage[data-p="${i}"] img`);
  return pics().find((im) => +im.dataset.p === +i) || null;
}

/* the "Two pages" row and its panel say how it is now */
function spreadButtons() {
  const set = (id, text, on) => {
    const b = document.getElementById(id);
    if (!b) return;
    b.querySelector("b").textContent = text;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on);
  };
  set("r-spread", SPREADS[spread()], spread() !== "off");
  set("r-spread-shift", shifted() ? MV.t("On") : MV.t("Off"), shifted());
  set("r-spread-order", rtl() ? MV.t("Right to left") : MV.t("Left to right"), false);
  for (const id of ["r-spread-shift", "r-spread-order"]) {
    const b = document.getElementById(id);
    if (b) b.hidden = spread() === "off";
  }
  const hint = document.getElementById("r-spread-hint");
  if (hint) hint.hidden = spread() !== "auto" || R.two;
  const state = document.getElementById("r-spread-state");
  if (state) {
    state.textContent = SPREADS[spread()];
    state.parentElement.classList.toggle("on", spread() !== "off");
  }
}

/* its options in the reading options: the fit (and the zoom lock), and Page look and Two pages (paged
   mode) with a panel of their own */
function drawer() {
  const paged = R.mode !== "vertical";
  return {
    rows: `<div class="d-row d-fit">
        <button class="d-opt" id="r-fit">${ICONS.fit}<span>${escHtml(FITS[fit()])}</span></button>
        ${paged ? `<button id="r-lock" class="icon"></button>` : ""}
      </div>
      <button class="d-opt d-toggle d-more" data-panel="look">${ICONS.sun}<span>${escHtml(MV.t("Page look"))}</span><b id="r-look-state"></b></button>
      ${paged ? `<button class="d-opt d-toggle d-more" data-panel="spread">${ICONS.book}<span>${escHtml(MV.t("Two pages"))}</span><b id="r-spread-state"></b></button>` : ""}`,
    // the host escapes a panel's title
    panels: [{ key: "look", title: MV.t("Page look"), body: `
      <button class="d-opt d-toggle" id="r-bright">${ICONS.sun}<span>${escHtml(MV.t("Brightness"))}</span><b></b></button>
      <button class="d-opt d-toggle" id="r-contrast">${ICONS.contrast}<span>${escHtml(MV.t("Contrast"))}</span><b></b></button>
      <button class="d-opt d-toggle" id="r-colors">${ICONS.colors}<span>${escHtml(MV.t("Page colours"))}</span><b></b></button>` },
    ...(paged ? [{ key: "spread", title: MV.t("Two pages"), body: `
      <button class="d-opt d-toggle" id="r-spread">${ICONS.book}<span>${escHtml(MV.t("Two pages"))}</span><b></b></button>
      <p class="d-hint" id="r-spread-hint" hidden>${escHtml(MV.t("The screen is upright or narrow now, so one page shows at a time."))}</p>
      <button class="d-opt d-toggle" id="r-spread-shift">${ICONS.shift}<span>${escHtml(MV.t("Shift the pairs by one page"))}</span><b></b></button>
      <button class="d-opt d-toggle" id="r-spread-order">${ICONS.order}<span>${escHtml(MV.t("Page order"))}</span><b></b></button>` }] : [])],
  };
}

function bindDrawer(root) {
  const $id = (id) => root.querySelector(`#${id}`);
  // number keys of an object come in number order, so the steps are spelled out
  const next = (steps, cur) => steps[(steps.indexOf(cur) + 1) % steps.length];
  $id("r-fit").onclick = async () => {
    await R.ctl.saveSettings({ reader_fit: cycle(FITS, fit()) });
    R.ctl.reopen();
  };
  if ($id("r-lock")) $id("r-lock").onclick = () => setLocked(!locked());
  $id("r-bright").onclick = () => setLook({ reader_brightness: next([100, 85, 70, 55, 40], brightness()) });
  $id("r-contrast").onclick = () => setLook({ reader_contrast: next([100, 125, 150, 75], contrast()) });
  $id("r-colors").onclick = () => setLook({ reader_colors: cycle(COLORS, colors()) });
  // two pages: the setting for all devices; the shift and the order for this series on this device
  if ($id("r-spread")) {
    $id("r-spread").onclick = () => {
      R.ctl.keepControls();
      R.ctl.saveSettings({ reader_spread: cycle(SPREADS, spread()) });
      if (twoPages() !== R.two) relayout(); else spreadButtons();
    };
    $id("r-spread-shift").onclick = () => {
      R.ctl.keepControls();
      setSeriesPref("spread-shift", shifted() ? "0" : "1");
      if (R.two) showPage();
      spreadButtons();
    };
    $id("r-spread-order").onclick = () => {
      R.ctl.keepControls();
      setSeriesPref("spread-order", rtl() ? "ltr" : "rtl");
      view()?.classList.toggle("rtl", rtl());
      spreadButtons();
    };
  }
  updateLockButton();
  applyLook();
  spreadButtons();
}

MangaVibe.registerReader("image-reader", "images", {
  modes: ["paged", "vertical"],
  open, close, goTo, step, key, wheel, pageBox, drawer, bindDrawer, zoomTo, panBy,
  where: () => (R.book ? { page: R.page } : null),
  state: () => ({ fit: fit(), zoom: Z.s, locked: locked(), two_pages: R.two }),
});
})();
