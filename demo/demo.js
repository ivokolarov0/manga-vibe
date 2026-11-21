/* Added to the app's page by the demo (sw.js): a small badge that says this is the demo, and links
   to MangaVibe. Hidden while reading, so it never covers a page. */
(() => {
  try { sessionStorage.removeItem("mv-demo-tries"); } catch {}  // index.html: the demo started

  /* Firefox sends keepalive requests (the reader saves progress with one, so it survives leaving the
     page) straight to the network, past the service worker: on GitHub Pages that's a 405 page instead of
     the demo's answer. The app's own requests don't need keepalive here, so they go without it. */
  const appFetch = window.fetch;
  window.fetch = (input, init) => {
    if (init?.keepalive) {
      const url = new URL(typeof input === "string" ? input : input.url, location.href);
      if (url.origin === location.origin && url.pathname.startsWith("/api/")) init = { ...init, keepalive: false };
    }
    return appFetch(input, init);
  };

  const badge = document.createElement("aside");
  badge.className = "mv-demo";
  badge.setAttribute("aria-label", "About this demo");
  badge.innerHTML = `
    <button type="button" class="mv-demo-pill" aria-expanded="false">Demo</button>
    <div class="mv-demo-card" hidden>
      <b>This is the MangaVibe demo</b>
      <p>A made-up library: every series, cover and page was drawn for it. Look around, read, switch
        plugins and settings. Nothing is saved: it starts again when you come back.</p>
      <p>Deleting, editing, installing and uploading need a MangaVibe of your own.</p>
      <a href="https://github.com/ivokolarov0/manga-vibe#-install" target="_blank" rel="noopener">Get MangaVibe →</a>
    </div>`;
  const pill = badge.querySelector(".mv-demo-pill"), card = badge.querySelector(".mv-demo-card");
  pill.addEventListener("click", () => {
    card.hidden = !card.hidden;
    pill.setAttribute("aria-expanded", String(!card.hidden));
  });
  document.addEventListener("click", (e) => {
    if (!badge.contains(e.target)) { card.hidden = true; pill.setAttribute("aria-expanded", "false"); }
  });
  const place = () => { badge.hidden = location.hash.startsWith("#/read"); };
  addEventListener("hashchange", place);
  place();
  document.body.append(badge);
})();
