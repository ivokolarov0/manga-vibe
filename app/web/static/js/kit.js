/* The UI kit's behaviour (docs/UI-KIT.md): MangaVibe.kit.

   Each interactive piece (a dialog, later menus and tabs…) is a plain object in a registry, used by an
   Alpine directive of the same name (x-dialog). The directive asks the registry for the piece every time
   an element starts, so a plugin can change a piece for the whole app without touching its markup:

     MangaVibe.kit.extend("my-plugin", "dialog", (base) => ({ ...base, options: { ...base.options, backdrop: false } }));
     MangaVibe.kit.define("my-plugin", "dialog", { options: {}, setup(ctx) { … } });  // a piece of its own

   Changes count only while the plugin is switched on; the elements that start after that use them.
   A piece is { options, setup(ctx) }. setup runs when an element with the directive starts, gets
   ctx = { el, options, run, emit, modifiers, value, name } and may return a function that undoes it:
     options: the piece's options, then the element's data-kit-options="{ … }" (an Alpine expression);
     run(): runs the directive's expression (x-dialog="open = false");
     emit(event, detail): sends "kit-<event>" from the element (bubbles); false when a listener called
       preventDefault(), to say "don't".
   Loaded before app.js, which puts `kit` in window.MangaVibe; also used alone by the kit page (/kit). */
const kit = (() => {
const own = new Map();  // name -> the app's own piece
const changes = [];     // [pluginId, name, "define" | "extend", piece or fn], in the order they came

// a plugin switched off since the page loaded doesn't count (app.js keeps them; the kit page has none)
const counts = (pluginId) => !pluginId || typeof stoppedPlugins === "undefined" || !stoppedPlugins.has(pluginId);

function get(name) {
  let piece = own.get(name);
  for (const [pluginId, n, how, value] of changes) {
    if (n !== name || !counts(pluginId)) continue;
    piece = how === "define" ? value : value(piece);
  }
  return piece;
}

// x-<name> on an element: the piece's setup, undone when the element goes
function directive(name) {
  return (el, { expression, modifiers, value }, { evaluateLater, cleanup }) => {
    const piece = get(name);
    if (!piece?.setup) return;
    const run = expression ? evaluateLater(expression) : () => {};
    const extra = el.dataset.kitOptions ? Alpine.evaluate(el, el.dataset.kitOptions) : null;
    const ctx = {
      el, modifiers, value, name,
      options: { ...piece.options, ...extra },
      run: () => run(),
      emit: (event, detail = {}) => el.dispatchEvent(
        new CustomEvent(`kit-${event}`, { detail, bubbles: true, cancelable: true })),
    };
    const undo = piece.setup(ctx);
    if (typeof undo === "function") cleanup(undo);
  };
}

const shown = (el) => el.isConnected && el.getClientRects().length > 0;  // not display: none (x-show)

/* ---------------- the app's pieces ---------------- */

let uid = 0;
const FOCUSABLE = "a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex='-1']), [contenteditable='true']";
const focusables = (root) => [...root.querySelectorAll(FOCUSABLE)].filter((e) => !e.disabled && shown(e));

const openDialogs = [];  // the dialogs showing now, the top one last

// while a modal dialog shows, the page behind it can't be scrolled (the scrollbar's room is kept, so
// nothing moves sideways)
let scrollLocks = 0;
function lockScroll(on) {
  const root = document.documentElement;
  if (on && scrollLocks++ === 0) {
    root.classList.toggle("kit-gutter", innerWidth > root.clientWidth);  // it had a scrollbar
    root.classList.add("kit-modal-open");
  } else if (!on && --scrollLocks === 0) {
    root.classList.remove("kit-modal-open", "kit-gutter");
  }
}

// the rest of the page can't be reached (Tab, a click, a screen reader): `inert` on everything beside the
// dialog and beside each of its parents. Live regions (the toast) stay, so they're still read out.
// Returns what it made inert, to give back.
function inertAround(el) {
  const made = [];
  for (let node = el; node.parentElement && node !== document.body; node = node.parentElement) {
    for (const sib of node.parentElement.children) {
      if (sib === node || sib.inert || ["SCRIPT", "STYLE", "TEMPLATE"].includes(sib.tagName)) continue;
      if (sib.matches("#toast, [aria-live], [role='status'], [role='alert']")) continue;
      sib.inert = true;
      made.push(sib);
    }
  }
  return made;
}

/* x-dialog="<close>" on a .modal or .sheet (the backdrop) around a <div> (the box): a click on the
   backdrop or Escape runs <close>, e.g. x-dialog="open = false". The Android back button sends Escape
   (MangaVibeApp.back). Only the top dialog closes when several show.
   It sees the dialog show and hide (x-show, x-if) by itself. While it shows: the box is a dialog for
   screen readers (role, aria-modal, named after its title: [data-kit-label], .sheet-head > b or the first
   heading), the focus moves into it ([autofocus], else the box itself, so a phone doesn't open its
   keyboard; focus: "field" takes the first field), Tab stays in it, and the page behind can't be reached
   or scrolled. When it hides, the focus goes back to what opened it.
   Options: backdrop, escape (they close it), modal (false: the page behind stays usable), focus ("box" |
   "field"), restoreFocus. Events: kit-open, kit-close { reason: "backdrop" | "escape" } before it
   closes (preventDefault() keeps it open), kit-closed after it hid.
   box(), label(), focusIn() and opener() are methods, so a plugin can change one of them alone. */
own.set("dialog", {
  options: { backdrop: true, escape: true, modal: true, focus: "box", restoreFocus: true },
  box: (el) => el.querySelector(":scope > div") || el,
  // true once the box has a name
  label(box) {
    if (box.hasAttribute("aria-label") || box.hasAttribute("aria-labelledby")) return true;
    const title = box.querySelector("[data-kit-label], .sheet-head > b, h1, h2, h3");
    if (!title) return false;
    title.id ||= `kit-label-${++uid}`;
    box.setAttribute("aria-labelledby", title.id);
    return true;
  },
  focusIn(box, options) {
    const target = [...box.querySelectorAll("[autofocus]")].find(shown)
      || (options.focus === "field" && focusables(box).find((e) => e.matches("input, select, textarea")));
    if (target) return target.focus({ preventScroll: true });
    if (!box.hasAttribute("tabindex")) box.tabIndex = -1;
    box.focus({ preventScroll: true });
  },
  // where the focus goes back to: what had it, or, when that's hidden now (an item of a closed menu),
  // the menu's button
  opener(was) {
    if (!was?.isConnected || was === document.body) return null;
    return shown(was) ? was : was.closest(".menu")?.querySelector("button") || null;
  },
  setup(ctx) {
    const piece = this, { el, options } = ctx;
    const box = piece.box(el);
    let open = null;  // while it shows: { opener, inerted }

    const show = () => {
      open = { opener: document.activeElement, inerted: [], freed: [] };
      openDialogs.push(el);
      // a dialog opened from another one may be in what that one made inert
      for (let n = el; n; n = n.parentElement) {
        if (n.inert) {
          n.inert = false;
          open.freed.push(n);
        }
      }
      if (!box.hasAttribute("role")) box.setAttribute("role", "dialog");
      if (options.modal && !box.hasAttribute("aria-modal")) box.setAttribute("aria-modal", "true");
      // its title may come a moment later (an x-if that waits for its data): name it then
      if (!piece.label(box)) {
        open.naming = new MutationObserver(() => piece.label(box) && open?.naming.disconnect());
        open.naming.observe(box, { childList: true, subtree: true });
      }
      if (options.modal) {
        open.inerted = inertAround(el);
        lockScroll(true);
      }
      // a component may have put the focus in already (a password field): leave it there
      if (!box.contains(document.activeElement)) piece.focusIn(box, options);
      ctx.emit("open");
    };
    const hide = () => {
      const was = open;
      open = null;
      was.naming?.disconnect();
      openDialogs.splice(openDialogs.indexOf(el), 1);
      was.inerted.forEach((e) => (e.inert = false));
      if (openDialogs.length) was.freed.forEach((e) => (e.inert = true));  // the other one still shows
      if (options.modal) lockScroll(false);
      const focus = document.activeElement;
      if (options.restoreFocus && (!focus || focus === document.body || el.contains(focus))) {
        piece.opener(was.opener)?.focus({ preventScroll: true });
      }
      ctx.emit("closed");
    };
    const check = () => {
      if (shown(el) && !open) show();
      else if (!shown(el) && open) hide();
    };

    const close = (reason) => ctx.emit("close", { reason }) && ctx.run();
    const isTop = () => open && openDialogs.at(-1) === el;
    const onClick = (e) => options.backdrop && e.target === el && isTop() && close("backdrop");
    const onKey = (e) => {
      if (!isTop() || e.defaultPrevented) return;  // a menu in the dialog took its Escape
      if (e.key === "Escape" && options.escape) return close("escape");
      // Tab goes round inside the box (inert keeps it out of the page, not out of the browser's bar)
      if (e.key === "Tab" && options.modal) {
        const all = focusables(box);
        if (!all.length) return e.preventDefault();
        const first = all[0], last = all.at(-1), at = document.activeElement;
        if (e.shiftKey && (at === first || at === box)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
      }
    };

    const watch = new MutationObserver(check);
    watch.observe(el, { attributes: true, attributeFilter: ["style", "hidden", "class", "x-cloak"] });
    queueMicrotask(check);  // after x-show's first run
    el.addEventListener("click", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      watch.disconnect();
      if (open) hide();  // taken out of the page while showing (x-if)
      el.removeEventListener("click", onClick);
      window.removeEventListener("keydown", onKey);
    };
  },
});

document.addEventListener("alpine:init", () => {
  for (const name of own.keys()) Alpine.directive(name, directive(name));
});

return {
  get,
  names: () => [...new Set([...own.keys(), ...changes.map((c) => c[1])])],
  // a piece of the plugin's own in place of this one (also a new name: it gets no directive, use get())
  define(pluginId, name, piece) { changes.push([pluginId, name, "define", piece]); },
  // change a piece: fn(the piece so far) -> the new one
  extend(pluginId, name, fn) { changes.push([pluginId, name, "extend", fn]); },
};
})();

window.MangaVibe ??= {};
window.MangaVibe.kit = kit;
