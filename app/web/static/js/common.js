/* Shared helpers for the Alpine UI (app.js) and the plain-JS reader (reader.js). */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------- the page's language (issue #263, app/i18n.py) ----------------
   t(text), t(text, { name: value }) for a {name} in it, tn(singular, plural, n) (with {n}): the text in the
   page's language (MV_I18N, put in the page by the server), else the English one. The texts are found
   in the code by `python -m app.i18n`, so give t() and tn() the English text as a plain string ("…" or
   '…', not a variable or a `template`). See app/i18n.py. */
const I18N = window.MV_I18N || { lang: "en", nplurals: 2, plural: "n != 1", messages: {}, languages: ["en"] };
const LANG = I18N.lang || "en";
const pluralIndex = (() => {
  // the .po file's Plural-Forms (checked on the server: only n, numbers and operators)
  try {
    if (/^[n0-9\s?:%<>=!&|()+\-*/]+$/.test(I18N.plural)) {
      const f = new Function("n", `return Number(${I18N.plural});`);
      // gettext rules count whole things; a fraction (1.5 seconds) takes the form of 2, as in English
      // and German ("seconds") and in Slavic languages (1,5 секунди / секунды)
      return (n) => {
        n = Math.abs(Number(n) || 0);
        const i = f(Number.isInteger(n) ? n : 2);
        return i >= 0 && i < I18N.nplurals ? i : 0;
      };
    }
  } catch { /* the English rule below */ }
  return (n) => (Number(n) === 1 ? 0 : 1);
})();
function fillText(s, values) {
  return values ? s.replace(/\{(\w+)\}/g, (m, k) => (k in values ? String(values[k]) : m)) : s;
}
function t(msgid, values) {
  const v = I18N.messages[msgid];
  return fillText(typeof v === "string" ? v : msgid, values);
}
function tn(singular, plural, n, values) {
  const forms = I18N.messages[singular];
  const s = Array.isArray(forms) && forms[pluralIndex(n)] ? forms[pluralIndex(n)] : (Number(n) === 1 ? singular : plural);
  return fillText(s, { n, ...values });
}
// dates and numbers the way the page's language writes them
const fmtDate = (d, opts) => new Date(d).toLocaleDateString(LANG, opts);
const fmtDateTime = (d, opts) => new Date(d).toLocaleString(LANG, opts);
const fmtTime = (d, opts) => new Date(d).toLocaleTimeString(LANG, opts);
const fmtNum = (n, opts) => new Intl.NumberFormat(LANG, opts).format(n);
// a language's own name for itself ("Deutsch", "日本語"), for the language picker
function languageName(code) {
  try {
    const name = new Intl.DisplayNames([code], { type: "language" }).of(code);
    return name.charAt(0).toLocaleUpperCase(code) + name.slice(1);
  } catch { return code; }
}

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

/* An icon from the sprite at the top of the page (templates/shell/icons.html), as HTML: icon("star"). */
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
  // the settings aren't loaded yet (e.g. at the login): keep what the page put there
  if (!choice) return root.dataset.theme;
  const theme = themeOf(choice);
  if (root.dataset.theme !== theme) root.dataset.theme = theme;
  // the browser's own bars get the page colour, also a custom.css or plugin theme's
  const bg = getComputedStyle(root).getPropertyValue("--bg").trim();
  if (bg) $('meta[name="theme-color"]')?.setAttribute("content", bg);
  try { localStorage.setItem("theme-last", theme); } catch {}  // templates/page.html uses it before the first paint
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

/* "Hide controls after" (reader_autohide): seconds until the reader's bars hide; 0 is Never. Settings →
   Reader and the reading options both offer these. A saved time that isn't one of them (an older
   version's, say) is put in among them, so it still shows. Values are text, as a select keeps them. */
const AUTOHIDE_STEPS = [0.5, 1, 1.5, 2, 2.5, 3, 5, 8, 15];
// Settings → Reader → Top bar, also in the reader's options (reader.js): value -> words
const TOP_BAR = { hide: t("Hide with the controls"), always: t("Always visible"), room: t("When there's room") };

function autohideOptions(current) {
  const cur = +current, steps = [...AUTOHIDE_STEPS];
  if (cur > 0 && !steps.includes(cur)) steps.push(cur);
  steps.sort((a, b) => a - b);
  return [...steps.map((n) => [String(n), tn("{n} second", "{n} seconds", n, { n: fmtNum(n) })]), ["0", t("Never")]];
}

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

/* Android tablets (the app, and Chrome): a <select>'s list opens beside it, not as the phone's full-screen
   picker, and a tap on the select while its list is open closes the list and then opens it again at once,
   so it can't be closed that way (#246). That tap now only closes it. Taps only: a mouse works as on a
   computer. The list is closed again when an option is picked, or when another tap lands elsewhere; a phone's
   picker takes the window's focus, so the page getting it back means that picker is gone. */
if (/Android/i.test(navigator.userAgent)) {
  let openSelect = null;  // the select whose list a tap opened
  document.addEventListener("mousedown", (e) => {
    if (!e.sourceCapabilities?.firesTouchEvents) return;
    const sel = e.target.closest?.("select");
    if (sel && sel === openSelect) {
      e.preventDefault();  // the select doesn't open its list again
      sel.blur();
      openSelect = null;
    } else openSelect = sel && !sel.disabled ? sel : null;
  }, true);
  document.addEventListener("change", (e) => { if (e.target === openSelect) openSelect = null; }, true);
  window.addEventListener("focus", () => { openSelect = null; });
}
