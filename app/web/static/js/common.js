/* Shared helpers for the Alpine UI (app.js) and the plain-JS reader (reader.js). */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// how long the library and the reader wait for the server before saying so (with "Try again")
const LOAD_TIMEOUT = 30000;

/* opts.timeout (ms): give up when the server hasn't answered by then (a request that hangs would
   otherwise leave the page loading forever), with an error that says so */
async function api(path, opts = {}) {
  const { timeout, ...rest } = opts;
  let res;
  try {
    res = await fetch(path, {
      ...rest,
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: rest.signal || (timeout ? AbortSignal.timeout(timeout) : undefined),
    });
  } catch (e) {
    if (e.name === "TimeoutError") {
      const err = new Error("The server is taking too long to answer");
      err.timeout = true;
      throw err;
    }
    throw e;
  }
  if (!res.ok) {
    // not logged in (an accounts plugin): it shows the login
    if (res.status === 401) window.dispatchEvent(new CustomEvent("mv-login-needed"));
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    // detail is usually text; some answers send an object with a message and more (e.err.detail)
    const err = new Error(typeof detail === "string" ? detail : detail.message || res.statusText);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  try { return await res.json(); } catch (e) {
    // the answer stopped coming partway (the timeout also covers reading it)
    if (e.name === "TimeoutError") throw new Error("The server is taking too long to answer");
    throw e;
  }
}

/* Short message at the bottom. kind "error" marks it as an error (red stripe). */
function toast(msg, ms = 3000, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", kind === "error");
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), ms);
}

/* Copy text. The Clipboard API only works on https or localhost; MangaVibe on a NAS is
   usually plain http, so fall back to a hidden textarea there. Returns true when it worked. */
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through to the old way */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

/* What a plugin button returned: a message, and maybe a page to open (e.g. a website's login). */
async function showActionResult(r) {
  if (r.message) toast(r.message, 6000);
  if (r.open_url) location.href = r.open_url;
}

/* Descriptions from MangaUpdates use markdown-style **bold** and *italic*.
   Escape everything first (so provider text can never inject HTML), then add only <strong>/<em>. */
function formatText(text) {
  return esc(text)
    .replace(/\*\*(?=\S)([\s\S]+?)(?<=\S)\*\*/g, "<strong>$1</strong>")
    .replace(/__(?=\S)([\s\S]+?)(?<=\S)__/g, "<strong>$1</strong>")
    .replace(/(^|[^\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])/g, "$1<em>$2</em>")
    .replace(/(^|[^\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])/g, "$1<em>$2</em>");
}

/* An icon from the sprite at the top of index.html, as HTML: icon("star"). */
const icon = (name, cls = "") =>
  `<svg class="ic${cls ? " " + cls : ""}" aria-hidden="true"><use href="#i-${esc(name)}"/></svg>`;
/* A Settings menu icon: the name of a sprite icon ("folder"), or any character (a plugin's emoji). */
const navIcon = (x) => (document.getElementById("i-" + x) ? icon(x) : esc(x));

/* Theme. Settings → Display has it, each person's own: "dark", "light" or "auto" (follows the device).
   <html data-theme> is always "dark" or "light": that is what the CSS and custom.css look at. */
const lightDevice = window.matchMedia("(prefers-color-scheme: light)");
// a theme setting as "dark" or "light"
const themeOf = (choice) => (choice === "auto" ? (lightDevice.matches ? "light" : "dark") : choice === "light" ? "light" : "dark");
// the top bar's old sun/moon button kept a choice per device: it would win over the setting forever
try { localStorage.removeItem("theme"); } catch {}
function applyTheme() {
  const root = document.documentElement;
  const choice = settings.theme;
  // the settings aren't loaded yet (e.g. at the login): keep what index.html put there
  if (!choice) return root.dataset.theme;
  const theme = themeOf(choice);
  if (root.dataset.theme !== theme) root.dataset.theme = theme;
  // the browser's own bars get the page colour, also a custom.css or plugin theme's
  const bg = getComputedStyle(root).getPropertyValue("--bg").trim();
  if (bg) $('meta[name="theme-color"]')?.setAttribute("content", bg);
  try { localStorage.setItem("theme-last", theme); } catch {}  // index.html uses it before the first paint
  window.dispatchEvent(new CustomEvent("mv-theme", { detail: theme }));
  return theme;
}
lightDevice.addEventListener("change", applyTheme);
/* Change the theme with the colours fading over, not snapping. choice: a theme setting. */
function setTheme(choice) {
  settings.theme = choice;
  const root = document.documentElement;
  root.classList.add("theme-anim");
  clearTimeout(setTheme.timer);
  setTheme.timer = setTimeout(() => root.classList.remove("theme-anim"), 400);
  return applyTheme();
}

/* The library's "per page" choices (as PAGE_SIZES in app/api/system.py) and the fixed column counts of
   Settings → Display → Grid columns: the sizes fill whole rows in most of these. */
const PAGE_SIZES = [40, 80, 120];
const GRID_COLUMNS = [4, 5, 6, 8, 10, 12];
// Settings → Display → Card width (with Auto columns): a card's minimum width in px on wide screens
const CARD_WIDTHS = [120, 140, 160, 180, 200, 240, 280, 320];

/* App settings (theme, reader defaults, ...). One shared object; reload with loadSettings(). */
const settings = {};
async function loadSettings() {
  Object.assign(settings, await api("/api/settings"));
  applyTheme();
  const root = document.documentElement;
  root.dataset.card = settings.card_size;
  // a fixed column count for the library grid (library.css); none = as many as fit
  const cols = +settings.grid_columns || 0;
  if (cols) root.style.setProperty("--cols", cols);
  else root.style.removeProperty("--cols");
  root.toggleAttribute("data-cols", !!cols);
  // with Auto columns: the cards' minimum width on wide screens, instead of the card size's (tokens.css)
  const cardWidth = +settings.card_width || 0;
  if (cardWidth) root.style.setProperty("--card-width", cardWidth + "px");
  else root.style.removeProperty("--card-width");
  root.toggleAttribute("data-card-width", !!cardWidth);
  root.dataset.width = settings.page_width;  // full: the Library and Continue grids fill the screen (tokens.css)
  root.toggleAttribute("data-width-series", settings.page_width_series === true);  // ...and the series page
  document.documentElement.dataset.nav = settings.nav_position;
  return settings;
}

/* The MangaVibe Android app (android/) puts the page in a WebView and adds window.MangaVibeAndroid:
   readerState(open, volumeKeys), changeServer(), server(), version(). Null in a browser. */
const androidApp = window.MangaVibeAndroid || null;
