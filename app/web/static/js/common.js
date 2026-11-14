/* Shared helpers for the Alpine UI (app.js) and the plain-JS reader (reader.js). */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
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
  return res.json();
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

/* Theme. Settings → Display has the app's theme: "dark", "light" or "auto" (follows the device).
   The sun/moon button in the top bar picks the other one for this device only (localStorage
   "theme"), which wins over the app's theme. Tapping the button back to what the app's theme gives
   ends the device's own choice, so the device follows the app (and "auto") again.
   <html data-theme> is always "dark" or "light": that is what the CSS and custom.css look at. */
const lightDevice = window.matchMedia("(prefers-color-scheme: light)");
// a theme setting as "dark" or "light"
const themeOf = (choice) => (choice === "auto" ? (lightDevice.matches ? "light" : "dark") : choice === "light" ? "light" : "dark");
function deviceTheme() {
  try {
    const t = localStorage.getItem("theme");
    return t === "dark" || t === "light" ? t : "";
  } catch { return ""; }
}
function applyTheme() {
  const root = document.documentElement;
  const choice = deviceTheme() || settings.theme;
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
/* Change the theme with the colours fading over, not snapping. choice: "dark" | "light" for this
   device, or "" to follow Settings → Display again. */
function setDeviceTheme(choice) {
  try { choice ? localStorage.setItem("theme", choice) : localStorage.removeItem("theme"); } catch {}
  const root = document.documentElement;
  root.classList.add("theme-anim");
  clearTimeout(setDeviceTheme.timer);
  setDeviceTheme.timer = setTimeout(() => root.classList.remove("theme-anim"), 400);
  return applyTheme();
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  // back to what the app's theme gives: no choice of its own any more
  return setDeviceTheme(settings.theme && themeOf(settings.theme) === next ? "" : next);
}

/* App settings (theme, reader defaults, ...). One shared object; reload with loadSettings(). */
const settings = {};
async function loadSettings() {
  Object.assign(settings, await api("/api/settings"));
  applyTheme();
  document.documentElement.dataset.card = settings.card_size;
  document.documentElement.dataset.nav = settings.nav_position;
  return settings;
}

/* The MangaVibe Android app (android/) puts the page in a WebView and adds window.MangaVibeAndroid:
   readerState(open, volumeKeys), changeServer(), server(), version(). Null in a browser. */
const androidApp = window.MangaVibeAndroid || null;
