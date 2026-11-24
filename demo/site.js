/* The website's top bar (home.html, docs.html): the light/dark switch. The pages follow the system's
   scheme; the switch picks the other one, and picking the system's again goes back to following it.
   The choice is kept in this browser (an inline script in each page's <head> applies it before the
   page draws, so it doesn't flash). */
"use strict";
(() => {
  const KEY = "mv-site-scheme";
  const meta = document.querySelector('meta[name="color-scheme"]');
  const system = matchMedia("(prefers-color-scheme: dark)");
  const stored = () => { try { return localStorage.getItem(KEY); } catch { return null; } };

  const MVSite = window.MVSite = {
    /* the scheme in use: the one picked, else the system's */
    dark() {
      const pinned = meta.content.trim();
      return pinned === "dark" || (pinned !== "light" && system.matches);
    },
  };

  function apply(value) {
    meta.content = value || "light dark";
    document.dispatchEvent(new CustomEvent("mv-scheme", { detail: { dark: MVSite.dark() } }));
  }

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".theme")) return;
    const target = MVSite.dark() ? "light" : "dark";
    const systemScheme = system.matches ? "dark" : "light";
    const value = target === systemScheme ? null : target;  // back to the system's: stop pinning
    try { value ? localStorage.setItem(KEY, value) : localStorage.removeItem(KEY); } catch {}
    apply(value);
  });
  addEventListener("storage", (e) => { if (e.key === KEY) apply(e.newValue); });
  system.addEventListener("change", () => apply(stored()));

  /* the phone menu closes after a link in it is picked, also one to a part of this page */
  const menu = document.getElementById("site-menu");
  menu?.addEventListener("click", (e) => {
    if (e.target.closest("a") && menu.matches(":popover-open")) menu.hidePopover();
  });
})();
