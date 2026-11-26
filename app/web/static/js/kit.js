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

const shown = (el) => el.getClientRects().length > 0;  // not display: none (x-show)

/* ---------------- the app's pieces ---------------- */

/* x-dialog="<close>" on a .modal or .sheet: a click on its backdrop (the element itself, not what's in
   it) or Escape runs <close>, e.g. x-dialog="open = false". The Android back button sends Escape too
   (MangaVibeApp.back). Options: backdrop (a click on it closes), escape. Event kit-close { reason:
   "backdrop" | "escape" }: preventDefault() keeps it open. */
own.set("dialog", {
  options: { backdrop: true, escape: true },
  setup(ctx) {
    const { el, options } = ctx;
    const close = (reason) => shown(el) && ctx.emit("close", { reason }) && ctx.run();
    const onClick = (e) => options.backdrop && e.target === el && close("backdrop");
    const onKey = (e) => options.escape && e.key === "Escape" && close("escape");
    el.addEventListener("click", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
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
