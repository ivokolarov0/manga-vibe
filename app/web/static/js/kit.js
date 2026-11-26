/* The UI kit's behaviour (docs/UI-KIT.md): MangaVibe.kit.

   Each interactive piece (a dialog, later menus and tabs…) is a plain object in a registry, used by an
   Alpine directive of the same name (x-dialog). The directive asks the registry for the piece every time
   an element starts, so a plugin can change a piece for the whole app without touching its markup:

     MangaVibe.kit.extend("my-plugin", "dialog", (base) => ({ ...base, options: { ...base.options, backdrop: false } }));
     MangaVibe.kit.define("my-plugin", "dialog", { options: {}, setup(ctx) { … } });  // a piece of its own

   Changes count only while the plugin is switched on; the elements that start after that use them.
   A piece is { options, setup(ctx) }. setup runs when an element with the directive starts, gets
   ctx = { el, options, expression, run, get, set, effect, emit, modifiers, value, name } and may return a
   function that undoes it:
     options: the piece's options, then the element's data-kit-options="{ … }" (an Alpine expression);
     run(): runs the directive's expression (x-dialog="open = false");
     get() / set(value): the expression as a value (x-menu="menuOpen"): its value, and setting it;
     effect(fn): runs fn again whenever what it read changes (Alpine's effect, gone with the element);
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
  return (el, { expression, modifiers, value }, { evaluateLater, cleanup, effect }) => {
    const piece = get(name);
    if (!piece?.setup) return;
    const run = expression ? evaluateLater(expression) : () => {};
    let setter = null;  // made the first time: "close()" can be run but not set
    const extra = el.dataset.kitOptions ? Alpine.evaluate(el, el.dataset.kitOptions) : null;
    const ctx = {
      el, modifiers, value, name, expression, effect,
      options: { ...piece.options, ...extra },
      run: () => run(),
      get: () => {
        let v;
        run((x) => (v = x));
        return v;
      },
      set: (v) => {
        setter ||= evaluateLater(`${expression} = __kitValue`);
        setter(() => {}, { scope: { __kitValue: v } });
      },
      emit: (event, detail = {}) => el.dispatchEvent(
        new CustomEvent(`kit-${event}`, { detail, bubbles: true, cancelable: true })),
    };
    const undo = piece.setup(ctx);
    if (typeof undo === "function") cleanup(undo);
  };
}

const shown = (el) => el.isConnected && el.getClientRects().length > 0;  // not display: none (x-show)
// not hidden by its own x-show or `hidden`, whether or not what's around it is on screen (a closed sheet's
// content is read while hidden, and nothing in it changes when the sheet opens)
const there = (el) => !el.hidden && el.style.display !== "none";

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
      if (e.key === "Escape" && options.escape) {
        e.preventDefault();  // used: the page's own Escape (e.g. ending a selection) leaves it
        return close("escape");
      }
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

/* x-menu on a .menu: its button opens the .menu-pop next to it, a list of actions. x-menu="menuOpen"
   keeps open or closed in your own data (so markup can say menuOpen = false); plain x-menu keeps it
   itself (the pop-up's `hidden`). Don't put an @click on the button: x-menu opens and closes it.
   The button gets aria-haspopup, aria-expanded and aria-controls; the pop-up role="menu" and its buttons
   and links role="menuitem". Keys: Enter, Space or Down opens on the first item, Up on the last; Up, Down,
   Home and End move (round); a letter goes to the next item that starts with it; Escape closes and puts
   the focus back on the button (a dialog around it stays open); Tab closes. A click on an item or outside
   closes it. The pop-up opens from the button's right edge, and flips to its left or above it when it
   would leave the screen.
   Options: keepOpen (a click on an item leaves it open, for switches), popover (not a list of actions
   but any content, e.g. a small form: no menu roles or arrow keys, Tab moves through it, and leaving it
   closes it). Events: kit-open, kit-select { item } before an item closes it (preventDefault() keeps it
   open), kit-closed.
   button(), pop(), items() and place() are methods, so a plugin can change one of them alone. */
const ITEMS = "button, a[href], [role='menuitem']";
own.set("menu", {
  options: { keepOpen: false, popover: false },
  button: (el) => el.querySelector(":scope > button, :scope > [data-kit-trigger]"),
  pop: (el) => el.querySelector(":scope > .menu-pop"),
  items: (pop) => [...pop.querySelectorAll(ITEMS)].filter((i) => shown(i) && !i.disabled),
  // stay on the screen: from the button's left edge when it would go past the left one, above the
  // button when there's no room below
  place(pop, el) {
    delete pop.dataset.kitAlign;
    delete pop.dataset.kitSide;
    const r = pop.getBoundingClientRect();
    if (r.left < 8) pop.dataset.kitAlign = "start";
    if (r.bottom > innerHeight - 8 && el.getBoundingClientRect().top > r.height + 16) pop.dataset.kitSide = "top";
  },
  setup(ctx) {
    const piece = this, { el, options } = ctx;
    const button = piece.button(el), pop = piece.pop(el);
    if (!button || !pop) return;
    pop.id ||= `kit-menu-${++uid}`;
    button.id ||= `kit-menu-button-${++uid}`;
    button.setAttribute("aria-haspopup", options.popover ? "dialog" : "menu");
    button.setAttribute("aria-controls", pop.id);
    if (!options.popover) {
      pop.setAttribute("role", "menu");
      if (!pop.hasAttribute("aria-label")) pop.setAttribute("aria-labelledby", button.id);
    }

    let mine = false;  // open or closed, without x-menu="…"
    const isOpen = () => (ctx.expression ? !!ctx.get() : mine);
    const setOpen = (on) => {
      if (ctx.expression) ctx.set(on);
      else changed((mine = on));
    };
    const list = () => (options.popover ? focusables(pop) : piece.items(pop));
    let focusTo = null, was = false;  // where the focus goes when it opens: "first" | "last" | null
    const focusItem = (which) => {
      const all = list();
      (which === "last" ? all.at(-1) : all[0])?.focus();
    };

    function changed(on) {
      button.setAttribute("aria-expanded", String(on));
      if (!ctx.expression) pop.hidden = !on;
      if (on === was) return;
      was = on;
      if (on) {
        if (!options.popover) {
          for (const i of pop.querySelectorAll(ITEMS)) {
            if (!i.hasAttribute("role")) i.setAttribute("role", "menuitem");
            i.tabIndex = -1;  // the arrow keys move between them, Tab leaves the menu
          }
        }
        Alpine.nextTick(() => {  // once x-show has shown it
          piece.place(pop, el);
          if (focusTo) focusItem(focusTo);
          focusTo = null;
        });
        ctx.emit("open");
      } else {
        if (pop.contains(document.activeElement)) button.focus();
        ctx.emit("closed");
      }
    }
    if (ctx.expression) ctx.effect(() => changed(isOpen()));
    else changed(false);

    const onButton = (e) => {
      if (!button.contains(e.target)) return;
      focusTo = e.detail === 0 ? "first" : null;  // a click from the keyboard (Enter, Space)
      setOpen(!isOpen());
    };
    const onKey = (e) => {
      const onButtonKey = button.contains(e.target);
      if (onButtonKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        focusTo = e.key === "ArrowDown" ? "first" : "last";
        return isOpen() ? focusItem(focusTo) : setOpen(true);
      }
      if (!isOpen()) return;
      if (e.key === "Escape") {
        e.preventDefault();  // a dialog around the menu stays open
        setOpen(false);
        return button.focus();
      }
      if (e.key === "Tab" && !options.popover) return setOpen(false);
      if (options.popover || onButtonKey) return;
      const all = list(), at = all.indexOf(document.activeElement);
      const go = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: all.length - 1 }[e.key];
      if (go !== undefined) {
        e.preventDefault();
        all.at(go % all.length)?.focus();
      } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== " ") {
        const k = e.key.toLowerCase();
        const next = [...all.slice(at + 1), ...all.slice(0, at + 1)]
          .find((i) => i.textContent.trim().toLowerCase().startsWith(k));
        next?.focus();
      }
    };
    const onPick = (e) => {
      const item = options.popover ? null : e.target.closest(ITEMS);
      if (!item || !pop.contains(item) || options.keepOpen) return;
      if (ctx.emit("select", { item })) setOpen(false);
    };
    const onOutside = (e) => isOpen() && !el.contains(e.target) && setOpen(false);
    const onLeave = (e) => isOpen() && e.relatedTarget && !el.contains(e.relatedTarget) && setOpen(false);

    button.addEventListener("click", onButton);
    el.addEventListener("keydown", onKey);
    pop.addEventListener("click", onPick);
    el.addEventListener("focusout", onLeave);
    document.addEventListener("click", onOutside, true);  // a click, as @click.outside did (a script's too)
    return () => {
      button.removeEventListener("click", onButton);
      el.removeEventListener("keydown", onKey);
      pop.removeEventListener("click", onPick);
      el.removeEventListener("focusout", onLeave);
      document.removeEventListener("click", onOutside, true);
    };
  },
});

/* A row of buttons where one is the picked one: x-choice (.segmented, .options) and x-tabs (.tabs).
   The markup keeps choosing as it always did: its @click picks, and the picked button has a class (.on,
   .active). The kit reads that class (and sees it change) and adds the rest:
   - for screen readers: the group's role (radiogroup, tablist) and each button's (radio, tab) with
     aria-checked or aria-selected;
   - one stop for Tab: the picked button (tabindex 0, the others -1);
   - the arrow keys move between the buttons (round), Home and End to the ends, and pick the one they
     land on by clicking it, so your @click runs. Option manual: the arrows only move, Enter or Space
     picks (e.g. when picking starts a search).
   A button with data-kit-skip isn't one of them (the libraries' + button in their tab row).
   items(), isPicked() and link() are methods, so a plugin can change one of them alone. */
const group = {
  options: { manual: false },
  role: "radiogroup", itemRole: "radio", pickedClass: "on", state: "aria-checked",
  keys: { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 },
  items: (el) => [...el.querySelectorAll(":scope > button:not([data-kit-skip])")],
  isPicked(item) { return item.classList.contains(this.pickedClass); },
  link() {},  // tabs: the panel a tab shows
  setup(ctx) {
    const piece = this, { el, options } = ctx;
    if (!el.hasAttribute("role")) el.setAttribute("role", piece.role);
    const update = () => {
      const all = piece.items(el);
      const picked = all.find((i) => piece.isPicked(i) && there(i));
      const stop = picked || all.find((i) => there(i) && !i.disabled);
      for (const i of all) {
        i.setAttribute("role", piece.itemRole);
        i.setAttribute(piece.state, String(i === picked));
        i.tabIndex = i === stop ? 0 : -1;
        piece.link(el, i);
      }
    };
    // a click changes the class, x-for adds buttons, x-show shows one: look again
    const watch = new MutationObserver(update);
    watch.observe(el, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "style"] });
    queueMicrotask(update);

    const onKey = (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const all = piece.items(el).filter((i) => shown(i) && !i.disabled);
      const at = all.indexOf(document.activeElement);
      if (at < 0) return;
      const step = piece.keys[e.key];
      const to = step !== undefined ? (at + step + all.length) % all.length
        : e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : -1;
      if (to < 0) return;
      e.preventDefault();
      all[to].focus();
      if (!options.manual && !piece.isPicked(all[to])) all[to].click();
    };
    el.addEventListener("keydown", onKey);
    return () => {
      watch.disconnect();
      el.removeEventListener("keydown", onKey);
    };
  },
};

/* x-choice on a .segmented or .options: one of a few choices, like radio buttons (the picked one .on). */
own.set("choice", { ...group });

/* x-tabs on a .tabs (the picked tab .active). Left and Right move (a row of tabs). A tab with
   data-tab="files" shows the panel with data-tab-panel="files" next to the tabs (inside the tabs' parent):
   they're tied together for screen readers (aria-controls, role="tabpanel", aria-labelledby). */
own.set("tabs", {
  ...group,
  role: "tablist", itemRole: "tab", pickedClass: "active", state: "aria-selected",
  keys: { ArrowRight: 1, ArrowLeft: -1 },
  link(el, tab) {
    const key = tab.dataset.tab;
    if (key === undefined) return;
    const panel = [...(el.parentElement?.querySelectorAll("[data-tab-panel]") || [])].find((p) => p.dataset.tabPanel === key);
    if (!panel) return;
    tab.id ||= `kit-tab-${++uid}`;
    panel.id ||= `kit-tab-panel-${++uid}`;
    tab.setAttribute("aria-controls", panel.id);
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tab.id);
  },
});

/* x-field on a .field or a .setting-row: ties a control to its name, its hint and its error, for screen
   readers. A .field is <label> (the name), an optional .hint, the control, an optional .error; a
   .setting-row is a .label holding the name (its first element) and an optional <small> hint, then the
   control (a folder card's .lib-option, with its .lib-option-text, the same). The kit:
   - names the control by the name alone (for/id, or aria-labelledby in a setting row, so a hint inside the
     row's <label> isn't read as part of the name);
   - describes it with the hint and, while it shows, the error (aria-describedby); the error gets
     role="alert" so it's read when it appears, and the control aria-invalid="true" while it shows;
   - makes a checkbox that looks like a switch (.switch, or right in a setting row) role="switch".
   A setting row's name is the .label's first element: wrap a bare text in a <span>.
   It looks again when something in it changes (an error appears, x-if adds the control).
   control(), name(), hint(), error() and wire() are methods, so a plugin can change one of them alone. */
own.set("field", {
  options: {},
  control: (el) => el.querySelector("input:not([type='hidden']), select, textarea"),
  name: (el) => el.querySelector(":scope > label, :scope > :is(.label, .lib-option-text) > :first-child:not(small)"),
  hint: (el) => el.querySelector(":scope > .hint, :scope > :is(.label, .lib-option-text) > small"),
  error: (el) => el.querySelector(":scope > .error"),
  wire(el) {
    const control = this.control(el);
    if (!control) return;
    const id = (e, what) => (e.id ||= `kit-${what}-${++uid}`);
    id(control, "field");
    // the checkboxes css/kit/fields.css draws as switches
    if (control.type === "checkbox" && !control.hasAttribute("role")
        && control.matches(".switch, :is(.setting-row, .row-buttons, .lib-option) > *")) {
      control.setAttribute("role", "switch");
    }
    const name = this.name(el);
    if (name?.tagName === "LABEL") {
      if (!name.contains(control)) name.htmlFor = control.id;
    } else if (name && !control.hasAttribute("aria-label")) {
      control.setAttribute("aria-labelledby", id(name, "name"));
    }
    const said = [];
    const hint = this.hint(el);
    if (hint && there(hint) && hint.textContent.trim()) said.push(id(hint, "hint"));
    const error = this.error(el);
    if (error && !error.hasAttribute("role")) error.setAttribute("role", "alert");
    const bad = error && there(error) && error.textContent.trim();
    if (bad) said.push(id(error, "error"));
    if (said.length) control.setAttribute("aria-describedby", said.join(" "));
    else control.removeAttribute("aria-describedby");
    if (bad) control.setAttribute("aria-invalid", "true");
    else control.removeAttribute("aria-invalid");
  },
  setup(ctx) {
    const piece = this, { el } = ctx;
    const update = () => piece.wire(el);
    const watch = new MutationObserver(update);
    watch.observe(el, { subtree: true, childList: true, characterData: true, attributes: true,
                        attributeFilter: ["style", "hidden"] });
    queueMicrotask(update);
    return () => watch.disconnect();
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
