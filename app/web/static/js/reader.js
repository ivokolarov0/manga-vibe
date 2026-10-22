/* The reader: plain JavaScript (touch, swipe, zoom, preloading and scroll tracking are
   direct browser work, so it stays outside Alpine). The router calls Reader.open/close. */
"use strict";

const Reader = (() => {

const R = { book: null, page: 0, mode: "rtl", ui: false, saveTimer: null, wake: null, firstTap: true,
  preloaded: new Map(), cameFromApp: false, hideTimer: null, sliding: false, hovering: false };
const WIDTHS = [720, 1080, 1440, 1800, 2160, 2880];
const TAP_SIDE = 0.25;  // width of each page-turn tap zone (left/right); the middle toggles the controls

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
  el.className = `fit-${settings.reader_fit || "screen"} ui-hidden ${R.mode}`;
  el.innerHTML = `
    ${R.mode === "vertical" ? `<div class="vertical"></div>` : `<div class="stage"><img alt=""></div>`}
    <div class="read-progress" aria-hidden="true"><i id="r-progress"></i></div>
    <div class="chrome top">
      <button id="r-back" aria-label="Back">←</button>
      <div class="title">${esc(book.series_title)} · ${esc(book.label)}</div>
      ${R.mode === "vertical" ? "" : `<button id="r-lock" aria-label="Lock zoom"></button>`}
      <button id="r-fs" aria-label="Fullscreen">⛶</button>
    </div>
    <div class="chrome bottom">
      <div class="pageinfo"><span id="r-num"></span><span>${esc(book.label)}</span></div>
      <input type="range" id="r-slider" min="1" max="${book.page_count}" value="${R.page + 1}"
        ${R.mode === "rtl" ? 'dir="rtl"' : ""}>
      <div class="opts">
        <select id="r-mode"><option value="rtl">Right to left</option><option value="ltr">Left to right</option>
          <option value="vertical">Vertical</option></select>
        <select id="r-fit"><option value="screen">Fit screen</option><option value="width">Fit width</option>
          <option value="height">Fit height</option><option value="original">Original</option></select>
        ${book.prev ? `<button id="r-prev">◀ ${esc(book.prev.label)}</button>` : ""}
        ${book.next ? `<button id="r-next">${esc(book.next.label)} ▶</button>` : ""}
      </div>
    </div>`;
  $("#r-mode").value = R.mode;
  $("#r-fit").value = settings.reader_fit || "screen";
  $("#r-back").onclick = () => leaveReader();
  $("#r-fs").onclick = toggleFullscreen;
  if ($("#r-lock")) $("#r-lock").onclick = () => setLocked(!locked());
  $("#r-slider").oninput = (e) => goTo(+e.target.value - 1);
  $("#r-mode").onchange = async (e) => {
    await api(`/api/series/${book.series_id}`, { method: "PATCH", body: { reader_mode: e.target.value } });
    saveProgress(true);
    openReader(book.id, R.page);
  };
  $("#r-fit").onchange = async (e) => {
    await api("/api/settings", { method: "PUT", body: { reader_fit: e.target.value } });
    await loadSettings();
    openReader(book.id, R.page);
  };
  if (book.prev) $("#r-prev").onclick = () => location.replace(`#/read/${book.prev.id}`);
  if (book.next) $("#r-next").onclick = () => location.replace(`#/read/${book.next.id}?page=0`);

  if (R.mode === "vertical") setupVertical(); else setupPaged();
  requestWakeLock();
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
  b.textContent = locked() ? "🔒" : "🔓";
  b.title = locked() ? "Zoom locked (tap to allow zoom and moving)" : "Zoom allowed (tap to lock)";
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
    const x = e.clientX / innerWidth;
    tapTimer = setTimeout(() => {
      if (Z.s > 1.01) return toggleUi();  // zoomed: taps only show/hide the controls
      if (x < TAP_SIDE) R.mode === "rtl" ? next() : prev();
      else if (x > 1 - TAP_SIDE) R.mode === "rtl" ? prev() : next();
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
}

function preload() {
  const n = +settings.preload || 4;
  for (let i = R.page + 1; i <= R.page + n && i < R.book.page_count; i++) {
    if (!R.preloaded.has(i)) {
      const im = new Image();
      im.src = pageUrl(i);
      R.preloaded.set(i, im);
    }
  }
}

function updatePageUi() {
  $("#r-num").textContent = `Page ${R.page + 1} / ${R.book.page_count}`;
  $("#r-slider").value = R.page + 1;
  // thin bar at the very bottom: how far into this book you are (right to left for RTL)
  $("#r-progress").style.width = `${((R.page + 1) / R.book.page_count) * 100}%`;
}

function goTo(i) {
  R.page = Math.max(0, Math.min(i, R.book.page_count - 1));
  if (R.mode === "vertical") {
    $("#reader .vertical").children[R.page]?.scrollIntoView();
    updatePageUi();
    saveProgress();
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
  box.innerHTML = Array.from({ length: R.book.page_count }, (_, i) =>
    `<img data-i="${i}" loading="lazy" src="${pageUrl(i)}" alt="">`).join("") +
    `<div style="padding:40px 20px;text-align:center">
      ${R.book.next ? `<button class="btn primary" id="v-next">Next: ${esc(R.book.next.label)}</button>`
        : `<p class="muted">End of the series.</p>`}</div>`;
  box.querySelectorAll("img").forEach((im) => (im.onload = () => im.classList.add("loaded")));
  $("#v-next")?.addEventListener("click", () => location.replace(`#/read/${R.book.next.id}?page=0`));
  // current page = the image in the middle of the screen (robust to images loading late)
  let ticking = false;
  box.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      const el = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
      if (el?.dataset?.i !== undefined && +el.dataset.i !== R.page) {
        R.page = +el.dataset.i;
        updatePageUi();
        saveProgress();
      }
    });
  }, { passive: true });
  box.children[R.page]?.scrollIntoView();
  updatePageUi();
  box.addEventListener("click", () => { maybeAutoFullscreen(); toggleUi(); });
}

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

function inChrome(el) { return !!el?.closest?.("#reader .chrome"); }

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
  return R.sliding || R.hovering || (inChrome(a) && a.tagName === "SELECT");
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

function saveProgress(now = false) {
  clearTimeout(R.saveTimer);
  const book = R.book, page = R.page;
  if (!book) return;
  const send = () => fetch(`/api/books/${book.id}/progress`, { method: "PUT", keepalive: true,
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ page }) });
  if (now) send(); else R.saveTimer = setTimeout(send, 700);
}

function maybeAutoFullscreen() {
  if (R.firstTap && settings.auto_fullscreen && !document.fullscreenElement) {
    document.documentElement.requestFullscreen?.().catch(() => {});
  }
  R.firstTap = false;
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.().catch(() => toast("Fullscreen not available"));
}

async function requestWakeLock() {
  try { R.wake = await navigator.wakeLock?.request("screen"); } catch {}  // needs HTTPS; ignore if unavailable
}

function leaveReader() {
  // back to where we came from inside the app; if opened directly, go to the series page
  if (R.cameFromApp) history.back();
  else location.replace(R.book ? `#/series/${R.book.series_id}` : "#/");
}

function closeReader() {
  if ($("#reader").hidden) return;
  saveProgress(true);
  R.wake?.release?.().catch(() => {});
  R.book = null;
  R.firstTap = true;
  clearTimeout(R.hideTimer);
  Object.assign(R, { sliding: false, hovering: false });
  $("#reader").hidden = true;
  $("#reader").innerHTML = "";
  document.body.style.overflow = "";
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

document.addEventListener("keydown", (e) => {
  if ($("#reader").hidden || !R.book || R.mode === "vertical") return;
  if (e.key === "ArrowRight") R.mode === "rtl" ? prev() : next();
  else if (e.key === "ArrowLeft") R.mode === "rtl" ? next() : prev();
  else if (e.key === " ") next();
  else if (e.key === "Escape") leaveReader();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") saveProgress(true);
  else if (!$("#reader").hidden) requestWakeLock();
});

return {
  open: openReader,
  close: closeReader,
  get isOpen() { return !$("#reader").hidden; },
};
})();
