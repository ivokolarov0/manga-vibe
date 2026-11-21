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
const FITS = { screen: "Fit screen", width: "Fit width", height: "Fit height", original: "Original size" };
// how the page images look (reader settings): easier on the eyes, e.g. white pages at night
const BRIGHTNESS = { 100: "Normal", 85: "85%", 70: "70%", 55: "55%", 40: "40%" };
const CONTRAST = { 100: "Normal", 125: "More", 150: "Most", 75: "Less" };
const COLORS = { normal: "Normal", dark: "Inverted", warm: "Warm", "dark-warm": "Inverted, warm" };
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
};

const escHtml = (s) => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

/* the value after `cur` in a {value: label} list, wrapping around */
function cycle(list, cur) {
  const keys = Object.keys(list);
  return keys[(keys.indexOf(cur) + 1) % keys.length];
}

// what is open: the book, the host's ctl, the element to draw in, the mode and the page shown
const R = { book: null, ctl: null, el: null, mode: "paged", page: 0, preloaded: new Map(), off: [] };
const opts = () => R.ctl.settings;  // the reader settings (Settings → Reader), from the host

/* a listener outside R.el, taken away again on close */
function listen(target, type, fn, opts) {
  target.addEventListener(type, fn, opts);
  R.off.push(() => target.removeEventListener(type, fn, opts));
}

function fit() { return opts().reader_fit in FITS ? opts().reader_fit : "screen"; }

function pageWidth() {
  if (fit() === "original") return 0;
  const want = Math.max(innerWidth, fit() === "height" ? innerHeight * 0.75 : 0) * (devicePixelRatio || 1);
  return WIDTHS.find((w) => w >= want) || WIDTHS[WIDTHS.length - 1];
}

function pageUrl(i, full = false) {
  const w = full ? 0 : pageWidth();
  return `/api/books/${R.book.id}/pages/${i}?v=${R.book.version}${w ? "&w=" + w : ""}`;
}

/* the page shown changed: the host updates its numbers, saves progress and draws the overlays */
function shown(i) {
  R.page = i;
  R.ctl.shown(i);
}

/* ---------------- paged mode: zoom, pan, swipe, page animations ----------------
   One <img> inside .stage. Zoom and pan are a CSS transform on the image:
   translate(x, y) scale(s), origin at the centre. Pointer Events cover touch and mouse.
   The lock (🔒) turns zoom/pan off and keeps the page fitted. */

const Z = { s: 1, x: 0, y: 0, hiRes: false };
const pointers = new Map();  // pointerId -> {x, y}
let gesture = null;          // what the current touch is doing: pan | pinch | swipe | tap

function locked() {
  try { return localStorage.getItem("reader-lock") === "1"; } catch { return false; }
}

function setLocked(on) {
  try { localStorage.setItem("reader-lock", on ? "1" : "0"); } catch {}
  updateLockButton();
  if (on && img()) resetZoom(true);
  R.el?.querySelector(".vertical")?.classList.toggle("locked", on);
}

function updateLockButton() {
  const b = document.getElementById("r-lock");
  if (!b) return;
  b.innerHTML = locked() ? ICONS.lock : ICONS.unlock;
  b.classList.toggle("on", locked());
  b.title = locked() ? "Zoom locked (tap to allow zoom and moving)" : "Zoom allowed (tap to lock)";
  b.setAttribute("aria-label", b.title);
}

function img() { return R.el?.querySelector(".stage img") || null; }
function stage() { return R.el.querySelector(".stage"); }

/* `animate`: true for the usual .2s, or a length in seconds (a wheel or key step is quicker) */
function applyZoom(animate = false) {
  const el = img();
  if (!el) return;
  el.style.transition = animate ? `transform ${animate === true ? 0.2 : animate}s ease-out` : "none";
  el.style.transform = `translate(${Z.x}px, ${Z.y}px) scale(${Z.s})`;
  stage().classList.toggle("zoomed", Z.s > 1.01);
  stage().classList.toggle("can-pan", canPan());
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
  clampPan();  // back at 1×, a page that fits is centred again; a taller one keeps its place
  maybeHiRes();
}

/* how far a page taller than the window can move up or down from the middle (0: it fits) */
function roomY() {
  const el = img();
  return el ? Math.max(0, (el.offsetHeight * Z.s - innerHeight) / 2) : 0;
}

/* is the page bigger than the screen (zoomed in, or "fit width"/"original" pages)? */
function canPan() {
  const el = img();
  return !locked() && (el.offsetWidth * Z.s > innerWidth + 1 || el.offsetHeight * Z.s > innerHeight + 1);
}

/* back to the fitted page. A page taller than the window ("fit width") stays about where it was
   instead of jumping to its middle. */
function resetZoom(animate = false) {
  Object.assign(Z, { x: Z.x / Z.s, y: Z.y / Z.s, s: 1 });
  if (img()) clampPan();
  applyZoom(animate);
}

/* zoomed in a lot: swap to the full-resolution page so it stays sharp */
function maybeHiRes() {
  if (Z.s < 1.6 || Z.hiRes || fit() === "original") return;
  Z.hiRes = true;
  const book = R.book.id, page = R.page;
  const full = pageUrl(page, true);
  const pre = new Image();
  pre.onload = () => { if (Z.hiRes && R.book?.id === book && R.page === page && img()) img().src = full; };
  pre.src = full;
}

function setupPaged() {
  R.el.innerHTML = `<div class="stage"><img alt=""></div>`;
  const st = stage();
  img().onerror = () => MangaVibe.toast("Could not load page " + (R.page + 1), 3000, "error");
  updateLockButton();
  showPage(0);

  let start = null, lastTap = { t: 0, x: 0, y: 0 }, tapTimer = null;

  // the browser's own "drag the picture" would cancel our pointer events (Firefox ignores the CSS rule)
  img().draggable = false;
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
      st.classList.toggle("dragging", gesture.type === "pan");
    }
    if (gesture.type === "pan") {
      Z.x = start.from.x + dx;
      Z.y = start.from.y + dy;
      clampPan();
      applyZoom();
    } else if (gesture.type === "swipe") {
      img().style.transition = "none";
      img().style.transform = `translate(${dx}px, ${Z.y}px)`;  // the page follows the finger
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
  const el = img();
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
  const canGo = forward ? R.page < R.book.page_count - 1 : R.page > 0;
  if (!canGo) {
    resetZoom(true);
    return forward ? R.ctl.end() : R.ctl.start();
  }
  const el = img();
  el.style.transition = "transform .16s ease-in, opacity .16s";
  el.style.transform = `translate(${Z.x + outDir * innerWidth}px, ${Z.y}px) scale(${Z.s})`;
  el.style.opacity = "0";
  const book = R.book;
  setTimeout(() => {
    if (R.book !== book) return;
    R.page += forward ? 1 : -1;
    showPage(-outDir, atEnd);
  }, 160);
}

/* show R.page; `fromDir` is the side the new page slides in from (-1 left, 1 right, 0 none).
   The browser keeps painting the old picture in the <img> until the new one is decoded, so a
   sliding page stays hidden until then; only then does it slide in. A page taller than the window
   starts at its top, like a page of a book (with `atEnd`, at its bottom). */
let showToken = 0;
function showPage(fromDir = 0, atEnd = false) {
  const el = img();
  if (!el) return;
  const st = stage();
  Object.assign(Z, { s: 1, x: 0, y: 0, hiRes: false });
  st.classList.remove("zoomed");
  el.style.transition = "none";
  el.style.transform = fromDir ? `translateX(${fromDir * 30}%)` : "none";
  el.style.opacity = fromDir ? "0" : "1";
  const token = ++showToken;  // a newer turn makes this one stale
  clearTimeout(st._spin);
  st._spin = setTimeout(() => st.classList.add("waiting"), 150);  // slow page: show a spinner
  el.src = pageUrl(R.page);
  el.alt = `Page ${R.page + 1} of ${R.book.page_count}`;
  // Firefox resolves decode() at once for the old picture while the new one is still loading
  // (Chrome waits), so wait for the load first: else the old page slides in, then blinks to the new one
  const loaded = el.complete ? Promise.resolve() : new Promise((done) => {
    el.addEventListener("load", done, { once: true });
    el.addEventListener("error", done, { once: true });
  });
  // decode() fails on a broken page (onerror shows a toast) or when src changes again; show the page anyway
  loaded.then(() => el.decode()).catch(() => {}).then(() => {
    if (token !== showToken || img() !== el) return;
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

function preload() {
  const n = +opts().preload || 4;
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
    `<div class="vpage" data-p="${i}"><img data-i="${i}" loading="lazy" src="${pageUrl(i)}" alt="Page ${i + 1}"></div>`).join("") +
    `<div style="padding:40px 20px;text-align:center">
      ${R.book.next ? `<button class="btn primary" id="v-next">Next: ${escHtml(R.book.next.label)}</button>`
        : `<p class="muted">End of the series.</p>`}</div>`;
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
      contrast() !== 100 && `${CONTRAST[contrast()]} contrast`].filter(Boolean);
    state.textContent = parts.join(" · ") || "Normal";
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
  Object.assign(R, { book, ctl, el, mode: ctl.mode === "vertical" ? "vertical" : "paged", page: ctl.page });
  R.preloaded.clear();
  el.className = `img-reader fit-${fit()}`;  // reader.css: the fit of the pages
  applyLook();
  listen(window, "resize", () => { if (R.book && img()) { clampPan(); applyZoom(); } });
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
  return i === R.page ? img() : null;
}

/* its options in the reading options: the fit (and the zoom lock), and Page look with a panel of its own */
function drawer() {
  return {
    rows: `<div class="d-row d-fit">
        <button class="d-opt" id="r-fit">${ICONS.fit}<span>${FITS[fit()]}</span></button>
        ${R.mode !== "vertical" ? `<button id="r-lock" class="icon"></button>` : ""}
      </div>
      <button class="d-opt d-toggle d-more" data-panel="look">${ICONS.sun}<span>Page look</span><b id="r-look-state"></b></button>`,
    panels: [{ key: "look", title: "Page look", body: `
      <button class="d-opt d-toggle" id="r-bright">${ICONS.sun}<span>Brightness</span><b></b></button>
      <button class="d-opt d-toggle" id="r-contrast">${ICONS.contrast}<span>Contrast</span><b></b></button>
      <button class="d-opt d-toggle" id="r-colors">${ICONS.colors}<span>Page colours</span><b></b></button>` }],
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
  updateLockButton();
  applyLook();
}

MangaVibe.registerReader("image-reader", "images", {
  modes: ["paged", "vertical"],
  open, close, goTo, step, key, wheel, pageBox, drawer, bindDrawer,
  where: () => (R.book ? { page: R.page } : null),
  state: () => ({ fit: fit(), zoom: Z.s, locked: locked() }),
});
})();
