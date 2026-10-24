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
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
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

/* App settings (theme, reader defaults, ...). One shared object; reload with loadSettings(). */
const settings = {};
async function loadSettings() {
  Object.assign(settings, await api("/api/settings"));
  document.documentElement.dataset.theme = settings.theme;
  document.documentElement.dataset.card = settings.card_size;
  return settings;
}
