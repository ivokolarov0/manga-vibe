/* Draws the repo's Markdown on the website, for the homepage (home.html) and the developer pages
   (docs.html): GitHub's alerts, highlighted code with a Copy button, Mermaid diagrams, and links that
   stay on the site for docs that are on it. Needs marked (and highlight.js for colours).

   MV_SITE (filled in by demo/build.py) says where the site's root is from this page (base) and where
   each doc is on the site (links: file in the repo → path, see demo/pages.py). */
"use strict";
(() => {
  const SITE = window.MV_SITE;
  const REPO = "https://github.com/ivokolarov0/manga-vibe/blob/main/";
  const RAW = "https://raw.githubusercontent.com/ivokolarov0/manga-vibe/main/";

  /* GitHub's anchor for a heading, so the docs' own #links work here as on GitHub */
  const slug = (text) => text.trim().toLowerCase().replace(/[^\w\- ]/g, "").replace(/ /g, "-");

  /* Where a link of the Markdown file `src` goes. Its own #parts get the page's prefix (`prefix`, on the
     homepage, where several files share one page); a doc on the site stays on it; the README's pictures
     are on the site; any other file of the repo opens on GitHub. */
  function where(href, { src, prefix, image }) {
    if (!href) return null;
    if (href.startsWith("#")) return prefix ? `#${prefix}-${href.slice(1)}` : null;
    if (/^[a-z][a-z0-9+.-]*:|^\/\//i.test(href)) return null;
    const u = new URL(href, `https://repo.invalid/${src}`);
    const path = decodeURIComponent(u.pathname.slice(1));
    const part = u.hash.slice(1);
    const to = SITE.links[path];
    if (to !== undefined) {
      const [page, id] = to.split("#");
      if (id) return `${SITE.base}${page}#${id}${part ? `-${part}` : ""}`;
      return SITE.base + page + (part ? `#${part}` : "");
    }
    if (path.startsWith("docs/media/")) return `${SITE.base}media/${path.slice("docs/media/".length)}`;
    return (image ? RAW : REPO) + path + u.hash;
  }

  function fixLinks(root, opts) {
    for (const a of root.querySelectorAll("a[href]")) {
      const to = where(a.getAttribute("href"), { ...opts, image: false });
      if (to) a.href = to;
    }
    for (const img of root.querySelectorAll("img[src]")) {
      const to = where(img.getAttribute("src"), { ...opts, image: true });
      if (to) img.src = to;
    }
  }

  /* > [!TIP] and friends, as on GitHub */
  function alerts(root) {
    for (const bq of root.querySelectorAll("blockquote")) {
      const p = bq.firstElementChild;
      const m = p?.tagName === "P" && p.innerHTML.match(/^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(<br>)?/i);
      if (!m) continue;
      const kind = m[1].toLowerCase();
      p.innerHTML = p.innerHTML.slice(m[0].length);
      if (!p.textContent.trim()) p.remove();
      bq.classList.add("alert", `alert-${kind}`);
      bq.insertAdjacentHTML("afterbegin", `<p class="alert-title">${kind[0].toUpperCase() + kind.slice(1)}</p>`);
    }
  }

  /* What the Markdown has for GitHub that the site has its own way for: the "← Back to the README"
     lines and the folded Contents box at the top; on the homepage also the title and the warning that
     the page already shows once (`intro: false`). */
  function dropGithubNav(root, intro) {
    for (const el of [...root.children]) {
      if (el.tagName === "H2") break;
      if (el.tagName === "P" && el.textContent.includes("←")) el.remove();
      else if (el.tagName === "DETAILS" && /contents/i.test(el.querySelector("summary")?.textContent || "")) el.remove();
      else if (!intro && (el.tagName === "H1" || el.classList.contains("alert-caution"))) el.remove();
    }
  }

  let codeCount = 0;
  /* every code block, also one inside a list's step: a diagram, or highlighted code with a Copy button */
  function codeBlocks(root) {
    for (const pre of [...root.querySelectorAll("pre")]) {
      const block = pre.querySelector("code");
      if (!block) continue;
      if (block.classList.contains("language-mermaid")) {
        const fig = document.createElement("div");
        fig.className = "mermaid";
        fig.dataset.source = block.textContent;
        fig.textContent = block.textContent;
        pre.replaceWith(fig);
        continue;
      }
      block.id = `code-${++codeCount}`;
      const lang = (block.className.match(/language-(\w+)/) || [])[1];
      if (lang && window.hljs?.getLanguage(lang)) hljs.highlightElement(block);
      const box = document.createElement("div");
      box.className = "copyable";
      box.innerHTML = `<span class="copy"><button type="button" class="copy-button" command="--copy"
        commandfor="${block.id}" aria-label="Copy this code">Copy</button><span class="tip" role="status"></span></span>`;
      pre.replaceWith(box);
      box.prepend(pre);
    }
  }

  /* The Markdown as HTML, in a <div>. Options: src (the file in the repo, for its links), prefix (ids of
     its headings and #links on a page shared with other files), demote (headings this many levels
     lower), intro (keep the title and the first warning). Returns { root }. */
  function render(md, { src, prefix = "", demote = 0, intro = true }) {
    const root = document.createElement("div");
    root.innerHTML = marked.parse(md);
    alerts(root);
    dropGithubNav(root, intro);
    fixLinks(root, { src, prefix });
    codeBlocks(root);
    for (const h of [...root.querySelectorAll("h1, h2, h3, h4, h5")]) {
      let el = h;
      if (demote) {
        el = document.createElement(`h${Math.min(6, +h.tagName[1] + demote)}`);
        el.append(...h.childNodes);
        h.replaceWith(el);
      }
      if (prefix) el.id = `${prefix}-${slug(el.textContent)}`;
    }
    for (const table of [...root.querySelectorAll("table")]) {
      const wrap = document.createElement("div");
      wrap.className = "table";
      table.replaceWith(wrap);
      wrap.append(table);
    }
    return { root };
  }

  /* Mermaid diagrams, loaded only on pages that have them, drawn again when the scheme changes */
  let mermaidLoaded = null;
  function drawDiagrams(root = document) {
    const nodes = [...root.querySelectorAll(".mermaid")];
    if (!nodes.length) return;
    mermaidLoaded ||= new Promise((done, fail) => {
      const script = document.createElement("script");
      script.src = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.min.js";
      script.onload = done;
      script.onerror = fail;
      document.head.append(script);
    });
    mermaidLoaded.then(() => {
      const dark = window.MVSite ? MVSite.dark() : matchMedia("(prefers-color-scheme: dark)").matches;
      mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "default" });
      for (const node of nodes) {
        node.removeAttribute("data-processed");
        node.textContent = node.dataset.source;
      }
      // at their own size: a wide diagram scrolls sideways instead of shrinking until it can't be read
      return mermaid.run({ nodes }).then(() => {
        for (const svg of root.querySelectorAll(".mermaid svg")) {
          const w = svg.viewBox?.baseVal?.width;
          if (w) Object.assign(svg.style, { maxWidth: "none", width: `${Math.ceil(w)}px` });
        }
      });
    }).catch(() => {});
  }
  document.addEventListener("mv-scheme", () => { if (mermaidLoaded) drawDiagrams(); });

  /* ---- Copy: Invoker Commands where the browser has them, a click listener elsewhere ---- */
  const timers = new WeakMap();
  function report(button, ok) {
    const tip = button.nextElementSibling;
    clearTimeout(timers.get(button));
    button.removeAttribute("data-copied");
    button.removeAttribute("data-failed");
    tip.textContent = "";
    button.setAttribute(ok ? "data-copied" : "data-failed", "");
    requestAnimationFrame(() => { tip.textContent = ok ? "Copied" : "Copy failed"; });
    timers.set(button, setTimeout(() => {
      button.removeAttribute("data-copied");
      button.removeAttribute("data-failed");
      tip.textContent = "";
    }, 2000));
  }
  async function copy(button, target) {
    const text = target.innerText.trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      report(button, true);
    } catch {
      report(button, false);
    }
  }
  if ("CommandEvent" in window) {
    document.addEventListener("command", (e) => { if (e.command === "--copy") copy(e.source, e.target); }, true);
  } else {
    document.addEventListener("click", (e) => {
      const button = e.target.closest('button[command="--copy"]');
      const target = button && document.getElementById(button.getAttribute("commandfor"));
      if (target) copy(button, target);
    });
  }

  window.MVDoc = { REPO, slug, render, drawDiagrams };
})();
