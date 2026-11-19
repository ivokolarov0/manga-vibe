/* Remote control (built-in plugin, see __init__.py): this page as a "screen" an assistant can control
   through MangaVibe's MCP server, and Settings → Remote control (keys, open screens).

   The page asks the server for commands and waits (long polling, screens.py), does each one and sends
   the answer back. What it can do:
   - describe: what is on the screen, for someone who can't see it, with a ref for every control shown
   - press / fill / key / scroll: use those controls as a person would
   - go / back: another page of the app; reader: the reader's own actions (MangaVibe.reader)
   - plugin: a plugin's screen command (MangaVibe.screenCommand)
   Uses window.MangaVibe (app.js) and the page's markup only for describe, which reads whatever it shows. */
MangaVibe.onStart(() => {
  const MV = MangaVibe, ID = "mcp", API = "/api/plugins/mcp/api";
  const MAX_CONTROLS = 150;

  /* ---------------- which page this is ---------------- */
  let screenId = "";
  try { screenId = sessionStorage.getItem("mcp-screen") || ""; } catch {}
  if (!screenId) {
    screenId = Math.random().toString(36).slice(2, 10);
    try { sessionStorage.setItem("mcp-screen", screenId); } catch {}
  }
  function deviceName() {
    const ua = navigator.userAgent;
    if (window.MangaVibeAndroid) return "Android app";
    const browser = /Firefox\//.test(ua) ? "Firefox" : /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera"
      : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
    const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Windows/.test(ua) ? "Windows"
      : /Mac OS X/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "";
    return os ? `${browser} on ${os}` : browser;
  }
  let inputAt = Date.now();
  function where() {
    const app = Alpine.store("app");
    return { view: app.view, address: location.hash || "#/", title: document.title };
  }
  function info() {
    return { screen: screenId, name: deviceName(), visible: document.visibilityState === "visible",
      focused: document.hasFocus(), input_ago: (Date.now() - inputAt) / 1000, where: where(),
      commands: MV.screenCommands().map((c) => ({ plugin: c.plugin, name: c.name, spec: c.spec })) };
  }

  /* ---------------- asking for commands ---------------- */
  let running = false, on = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function loop() {
    if (running) return;
    running = true;
    let wait = 2000;
    while (on) {
      try {
        const r = await MV.api(`${API}/screens/poll`, { method: "POST", body: info() });
        wait = 2000;
        for (const cmd of r.commands || []) await run(cmd);
      } catch (e) {
        await sleep(wait);  // offline, logged out, switched off: try again, less and less often
        wait = Math.min(wait * 2, 60000);
      }
    }
    running = false;
  }
  // tell the server at once when the page is shown, touched after a while, or moves elsewhere,
  // so commands go to the screen the person is using (the poll only says so every 25 seconds)
  let reportTimer = null;
  function report() {
    if (!on) return;
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => MV.api(`${API}/screens/state`, { method: "POST", body: info() }).catch(() => {}), 300);
  }
  const touched = () => {
    const idle = Date.now() - inputAt > 20000;
    inputAt = Date.now();
    if (idle) report();
  };
  for (const ev of ["pointerdown", "keydown", "wheel"]) addEventListener(ev, touched, { capture: true, passive: true });
  document.addEventListener("visibilitychange", report);
  addEventListener("hashchange", report);

  async function run(cmd) {
    let ok = true, result = null;
    try {
      const fn = COMMANDS[cmd.type];
      if (!fn) throw new Error(`This app doesn't know "${cmd.type}" (reload it for the newest version)`);
      result = await fn(cmd.args || {});
    } catch (e) {
      ok = false;
      result = e.message || String(e);
    }
    if (!cmd.id) return;
    await MV.api(`${API}/screens/result`, { method: "POST",
      body: ok ? { screen: screenId, id: cmd.id, ok, result: result ?? null } : { screen: screenId, id: cmd.id, ok, error: result } })
      .catch(() => {});
  }

  /* ---------------- what is on the screen ---------------- */
  const clean = (s, n = 100) => String(s || "").replace(/\s+/g, " ").trim().slice(0, n);
  function shown(el) {
    if (!el.isConnected || el.closest("[inert], [aria-hidden='true'], [hidden]")) return false;
    if (el.checkVisibility) return el.checkVisibility({ visibilityProperty: true, opacityProperty: false });
    return el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  }
  // what is open on top: the reader (its open panel, its drawer), else the last open dialog or sheet
  function layers() {
    const reader = $("#reader");
    if (reader && !reader.hidden) {
      const panel = reader.querySelector(".d-panel.open");
      if (panel) return { root: panel, open: [`Reader options: ${clean(panel.getAttribute("aria-label"))}`] };
      const drawer = reader.querySelector(".drawer:not([inert])");
      if (drawer && reader.classList.contains("drawer-open")) return { root: drawer, open: ["Reader options"] };
      return { root: reader, open: ["Reader"] };
    }
    const dialogs = [...document.querySelectorAll(".modal, .sheet, [role=dialog], dialog[open]")].filter(shown);
    const top = dialogs[dialogs.length - 1];
    const name = (d) => clean(d.getAttribute("aria-label")
      || d.querySelector(".sheet-head b, .sheet-head strong, h1, h2, h3, header")?.textContent, 60);
    return { root: top || document.body, open: dialogs.map((d) => name(d) || "A dialog") };
  }
  const CONTROLS = "a[href], button, select, textarea, input:not([type=hidden]), summary, [role=button], [role=tab], "
    + "[role=menuitem], [role=checkbox], [role=switch], [role=option], [role=link]";
  function roleOf(el) {
    const r = el.getAttribute("role");
    if (r) return r;
    const t = el.tagName;
    if (t === "A") return "link";
    if (t === "SELECT") return "dropdown";
    if (t === "TEXTAREA") return "text field";
    if (t === "INPUT") {
      const type = (el.type || "text").toLowerCase();
      return { checkbox: el.closest(".switch, .lib-option, .set-row") ? "switch" : "checkbox", radio: "radio",
        range: "slider", button: "button", submit: "button", file: "file picker" }[type] || "text field";
    }
    return "button";
  }
  function nameOf(el) {
    const by = el.getAttribute("aria-labelledby");
    const label = el.getAttribute("aria-label") || (by && clean(by.split(" ").map((i) => document.getElementById(i)?.textContent).join(" ")))
      || (el.labels?.[0] && clean(el.labels[0].innerText || el.labels[0].textContent))
      || (el.tagName !== "SELECT" && clean(el.innerText || el.textContent))
      || el.title || el.placeholder || el.querySelector?.("img[alt]")?.alt || el.closest("label")?.innerText;
    return clean(label || el.name || el.id, 100);
  }
  let nextRef = 1;
  function controls(root, all) {
    document.querySelectorAll("[data-mcp-ref]").forEach((el) => el.removeAttribute("data-mcp-ref"));
    const out = [];
    let more = 0;
    for (const el of root.querySelectorAll(CONTROLS)) {
      if (!shown(el)) continue;
      if (!all && out.length >= MAX_CONTROLS) { more++; continue; }
      const ref = nextRef++;
      el.dataset.mcpRef = ref;
      const c = { ref, role: roleOf(el), name: nameOf(el) };
      if (el.disabled || el.getAttribute("aria-disabled") === "true") c.disabled = true;
      if (el.type === "checkbox" || el.type === "radio") c.checked = el.checked;
      for (const a of ["pressed", "expanded", "selected", "checked", "current"]) {
        const v = el.getAttribute(`aria-${a}`);
        if (v !== null && v !== "false") c[a] = v === "true" ? true : v;
      }
      if (el.classList.contains("on") || el.classList.contains("active")) c.on = true;
      if (el.tagName === "SELECT") {
        c.value = el.selectedOptions[0]?.textContent.trim() || el.value;
        c.options = [...el.options].slice(0, 40).map((o) => clean(o.textContent, 60));
      } else if (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && !["checkbox", "radio", "button", "submit"].includes(el.type))) {
        c.value = el.type === "password" ? (el.value ? "(typed)" : "") : el.value;
        if (el.type === "range") Object.assign(c, { min: el.min, max: el.max });
      }
      if (el.tagName === "A" && el.getAttribute("href")?.startsWith("#")) c.goes_to = el.getAttribute("href");
      out.push(c);
    }
    return { controls: out, ...(more ? { more_controls: `${more} more: describe with all=true, or scroll` } : {}) };
  }
  function heading() {
    for (const h of document.querySelectorAll("main h1, h1, .page h2, main h2, h2")) if (shown(h)) return clean(h.textContent);
    return "";
  }
  function summary() {
    const app = Alpine.store("app"), out = {};
    if (app.view === "library") {
      const filters = MV.filterLabels(MV.filterQuery(MV.libraryFilters()));
      out.filters = filters.length ? filters : "none (everything)";
      out.series_shown = [...document.querySelectorAll("a.card")].filter(shown).length;
    }
    if (app.view === "series") out.series_id = app.id;
    if (app.view === "settings") out.section = app.sub || "";
    if (app.view === "read") out.reader = MV.reader.state();
    const t = $("#toast");
    if (t && shown(t) && clean(t.textContent)) out.message = clean(t.textContent, 200);
    return out;
  }
  function describe({ all = false } = {}) {
    const { root, open } = layers();
    return { ...where(), heading: heading(), ...summary(), open_on_top: open.length ? open : undefined,
      ...controls(root, all) };
  }
  // after something was done: where things are now (short; describe for the controls)
  async function settled(extra = {}) {
    await sleep(400);
    const { open } = layers();
    return { ...extra, ...where(), heading: heading(), ...summary(), open_on_top: open.length ? open : undefined };
  }

  /* ---------------- doing things ---------------- */
  function byRef(ref) {
    const el = document.querySelector(`[data-mcp-ref="${+ref}"]`);
    if (!el || !shown(el)) throw new Error(`Nothing with ref ${ref} on the screen any more: describe it again`);
    return el;
  }
  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype
      : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function pressKey(key, target = document.activeElement || document.body) {
    const init = { key, bubbles: true, cancelable: true, code: key.length === 1 ? `Key${key.toUpperCase()}` : key };
    target.dispatchEvent(new KeyboardEvent("keydown", init));
    target.dispatchEvent(new KeyboardEvent("keyup", init));
  }
  const reader = () => {
    if (!MV.reader.where()) throw new Error("No book is open on this screen (screen_navigate to read one)");
    return MV.reader;
  };
  // the reader needs a moment after a turn (the slide) or opening a book before its state is right
  async function readerNow(ms = 450) {
    await sleep(ms);
    return MV.reader.state() || await settled({ reader: null });
  }

  const COMMANDS = {
    describe,
    async press({ ref }) {
      const el = byRef(ref), name = nameOf(el);
      el.focus?.({ preventScroll: true });
      el.click();
      return settled({ pressed: name });
    },
    async fill({ ref, value, submit }) {
      const el = byRef(ref);
      if (el.type === "checkbox" || el.type === "radio" || el.getAttribute("role") === "switch") {
        const want = value === true || String(value).toLowerCase() === "true" || value === 1;
        const now = el.type ? el.checked : el.getAttribute("aria-checked") === "true";
        if (want !== now) el.click();
      } else if (el.tagName === "SELECT") {
        const v = String(value).toLowerCase();
        const opt = [...el.options].find((o) => o.value.toLowerCase() === v)
          || [...el.options].find((o) => o.textContent.trim().toLowerCase() === v)
          || [...el.options].find((o) => o.textContent.trim().toLowerCase().includes(v));
        if (!opt) throw new Error(`No option "${value}": ${[...el.options].map((o) => o.textContent.trim()).join(", ")}`);
        setValue(el, opt.value);
      } else {
        el.focus?.({ preventScroll: true });
        setValue(el, String(value));
      }
      if (submit) {
        pressKey("Enter", el);
        el.form?.requestSubmit?.();
      }
      return settled({ filled: nameOf(el) });
    },
    async key({ key }) {
      pressKey(key);
      return settled({ key });
    },
    async scroll({ direction }) {
      const box = MV.reader.where() ? $("#reader .vertical") : null, target = box || document.scrollingElement;
      const h = innerHeight * 0.85;
      const top = { down: target.scrollTop + h, up: target.scrollTop - h, top: 0, bottom: target.scrollHeight }[direction];
      target.scrollTo({ top, behavior: "instant" });
      await sleep(300);
      return { scrolled: direction, at_end: target.scrollTop + target.clientHeight >= target.scrollHeight - 4,
        at_top: target.scrollTop <= 0 };
    },
    async go({ address }) {
      if (!/^#\//.test(address)) throw new Error("Not an address of the app");
      if (location.hash === address) dispatchEvent(new HashChangeEvent("hashchange"));
      else location.hash = address;
      if (address.startsWith("#/read/")) return readerNow(700);
      return settled();
    },
    async back() {
      if (MV.reader.where()) MV.reader.leave(); else history.back();
      return settled();
    },
    async reader({ action, ...a }) {
      if (action === "state") return reader().state();
      const r = reader();
      if (action === "turn") {
        for (let i = 0; i < a.pages; i++) {
          if (!r.step(a.forward)) break;
          if (a.pages > 1) await sleep(MV.reader.state().mode === "vertical" ? 50 : 220);
        }
        return readerNow();
      }
      if (action === "page") {
        const pages = r.where().pages;
        if (a.page >= pages) throw new Error(`This book has ${pages} pages`);
        r.goTo(a.page);
        return readerNow(300);
      }
      if (action === "book") {
        if (!r.openBook(a.which, a.page)) throw new Error(a.which === "next" ? "This is the last book"
          : a.which === "previous" ? "This is the first book" : "No such book");
        return readerNow(900);
      }
      if (action === "mode") { await r.setMode(a.mode); return readerNow(300); }
      if (action === "options") { r.options(a.open); return settled(); }
      if (action === "overlay") {
        if (!r.overlay(a.plugin, a.on)) throw new Error(`No overlay of ${a.plugin} in this book`);
        return readerNow(200);
      }
      if (action === "press") {
        if (!(await r.press(a.plugin, a.key))) throw new Error(`No button ${a.key} of ${a.plugin} here`);
        return settled();
      }
      if (action === "leave") { r.leave(); return settled(); }
      throw new Error(`Unknown reader action ${action}`);
    },
    async plugin({ plugin, name, params }) {
      const c = MV.screenCommands().find((x) => x.plugin === plugin && x.name === name);
      if (!c) throw new Error(`${plugin} has no "${name}" on this screen`);
      return await c.fn(params || {});
    },
    // no answer wanted: something changed elsewhere (an assistant changed settings or a series)
    async settings() { await MV.reloadSettings(); },
    async refresh() {
      const app = Alpine.store("app");
      app.libraryDirty++;
      app.progressDirty++;
    },
  };

  /* ---------------- Settings → Remote control ---------------- */
  Alpine.store("mcp", { url: "", keys: [], screens: [], created: null, name: "", screenId,
    async load() {
      const [i, k, s] = await Promise.all([MV.api(`${API}/info`), MV.api(`${API}/keys`), MV.api(`${API}/screens`)]);
      Object.assign(this, { url: i.url, keys: k, screens: s });
    },
    async create() {
      try {
        this.created = await MV.api(`${API}/keys`, { method: "POST", body: { name: this.name } });
        this.name = "";
        await this.load();
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
    async remove(k) {
      if (!confirm(`Delete the key "${k.name}"? Assistants using it can't connect any more.`)) return;
      try {
        await MV.api(`${API}/keys/${encodeURIComponent(k.id)}`, { method: "DELETE" });
        await this.load();
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
    copy(text) {
      navigator.clipboard?.writeText(text).then(() => MV.toast("Copied"), () => MV.toast("Select it and copy it"));
    },
    config() {
      return JSON.stringify({ mcpServers: { mangavibe: { type: "http", url: this.url,
        headers: { Authorization: `Bearer ${this.created?.key || "<your key>"}` } } } }, null, 2);
    },
    when(t) { return t ? new Date(t).toLocaleString() : "never"; },
  });

  MV.on(ID, "start", () => {
    MV.settingsPage(ID, "remote", "Remote control", "phone", Infinity, { personal: true });
    on = true;
    loop();
    setTimeout(report, 1000);  // the first ask goes before the app shows its first page: say where it is now
  });
  MV.on(ID, "stop", () => { on = false; });
  MV.on(ID, "settings-open", () => Alpine.store("mcp").load().catch(() => {}));
});
