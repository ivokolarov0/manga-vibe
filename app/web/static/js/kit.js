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
   control: a field, or a .segmented of choices (a folder card's .lib-option, with its .lib-option-text,
   the same). The kit:
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
  // a .segmented (choices right in a setting row) is named and described like a field
  control: (el) => el.querySelector("input:not([type='hidden']), select, textarea, .segmented"),
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

/* The toast: a short message at the bottom (MangaVibe.toast(text, ms, kind, action)). The visible
   #toast isn't read out itself: the text also goes to a hidden live region, #toast-say (role="status")
   or, for an error, #toast-alert (role="alert"), emptied first so the same message twice is read twice.
   An action ({ label, run }) adds a button (an "Undo"): the toast then stays at least 6 seconds, and it
   waits while the mouse or the focus is on it. show() returns a number that's new for every toast.
   show(), say(), hide(), pause() and resume() are methods, so a plugin can change one of them alone. */
const toastState = { seq: 0, timer: 0, left: 0, since: 0 };
own.set("toast", {
  options: { ms: 3000, actionMs: 6000 },
  show(text, ms = this.options.ms, kind = "", action = null) {
    const box = document.getElementById("toast");
    if (!box) return 0;
    (box.querySelector(".toast-text") || box).textContent = text;
    box.classList.toggle("error", kind === "error");
    const button = box.querySelector(".toast-action");
    if (button) {
      button.hidden = !action;
      button.textContent = action?.label || "";
      button.onclick = action ? () => { this.hide(); action.run(); } : null;
    }
    box.classList.toggle("has-action", !!action);
    box.classList.add("show");
    this.say(text, kind);
    toastState.left = action ? Math.max(ms, this.options.actionMs) : ms;
    this.resume();
    return ++toastState.seq;
  },
  say(text, kind) {
    const region = document.getElementById(kind === "error" ? "toast-alert" : "toast-say");
    if (!region) return;
    region.textContent = "";
    setTimeout(() => (region.textContent = text), 50);  // a change, even for the same text
  },
  hide() {
    clearTimeout(toastState.timer);
    document.getElementById("toast")?.classList.remove("show", "has-action");
  },
  pause() {
    clearTimeout(toastState.timer);
    toastState.left -= Date.now() - toastState.since;
  },
  resume() {
    clearTimeout(toastState.timer);
    toastState.since = Date.now();
    toastState.timer = setTimeout(() => this.hide(), Math.max(toastState.left, 1000));
  },
});
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("toast");
  if (!box) return;
  const shownNow = () => box.classList.contains("show");
  const pause = () => shownNow() && get("toast").pause();
  const resume = (e) => shownNow() && !box.contains(e.relatedTarget) && !box.matches(":hover") && get("toast").resume();
  box.addEventListener("mouseenter", pause);
  box.addEventListener("focusin", pause);
  box.addEventListener("mouseleave", (e) => shownNow() && !box.contains(document.activeElement) && get("toast").resume(e));
  box.addEventListener("focusout", resume);
});

/* x-tooltip="text" on a button (an icon button above all): the text shows in a small bubble above it
   while the mouse is on it or it has the keyboard's focus (not on a tap or click), and is the button's
   name when it has none (aria-label). Without a value it takes the button's title, which it removes,
   so the browser's own tooltip doesn't show as well. Escape hides it. One bubble for the page (.tooltip),
   placed above the button, or below it when there's no room.
   bubble() and place() are methods, so a plugin can change one of them alone. */
let tipFor = null;
own.set("tooltip", {
  options: { delay: 400 },
  bubble() {
    let tip = document.querySelector("body > .tooltip");
    if (!tip) {
      tip = Object.assign(document.createElement("div"), { className: "tooltip", hidden: true });
      tip.setAttribute("aria-hidden", "true");  // the button's name already says it
      document.body.append(tip);
    }
    return tip;
  },
  place(tip, el) {
    const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
    const left = Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), innerWidth - t.width - 8);
    const above = r.top - t.height - 8;
    tip.style.left = `${left}px`;
    tip.style.top = `${above >= 8 ? above : r.bottom + 8}px`;
  },
  setup(ctx) {
    const piece = this, { el, options } = ctx;
    const text = () => String((ctx.expression ? ctx.get() : null) ?? el.dataset.kitTip ?? "");
    if (!ctx.expression && el.title) {
      el.dataset.kitTip = el.title;
      el.removeAttribute("title");
    }
    const name = () => {
      if (!el.hasAttribute("aria-label") && !el.textContent.trim() || el.dataset.kitNamed) {
        el.setAttribute("aria-label", text());
        el.dataset.kitNamed = "1";
      }
    };
    if (ctx.expression) ctx.effect(name);
    else name();
    let wait = 0;
    const show = () => {
      const tip = piece.bubble(), words = text();
      if (!words) return;
      tip.textContent = words;
      tip.hidden = false;
      tipFor = el;
      piece.place(tip, el);
    };
    const hide = () => {
      clearTimeout(wait);
      if (tipFor !== el) return;
      piece.bubble().hidden = true;
      tipFor = null;
    };
    const enter = () => {
      clearTimeout(wait);
      wait = setTimeout(show, options.delay);
    };
    const focus = () => el.matches(":focus-visible") && show();
    const key = (e) => e.key === "Escape" && tipFor === el && (e.preventDefault(), hide());
    const click = (e) => e.detail > 0 && hide();  // a tap or a click; not one the keyboard made (x-choice's)
    const on = [["mouseenter", enter], ["mouseleave", hide], ["focus", focus], ["blur", hide],
                ["click", click], ["keydown", key]];
    on.forEach(([t, f]) => el.addEventListener(t, f));
    return () => {
      hide();
      on.forEach(([t, f]) => el.removeEventListener(t, f));
    };
  },
});

/* ---------------- the date picker ---------------- */

// the page's texts (common.js's t(), issue #263); the kit page has none, so there they stay English
const fill = (s, values) => (values ? s.replace(/\{(\w+)\}/g, (m, k) => (k in values ? String(values[k]) : m)) : s);
const t = (text, values) => (typeof window.t === "function" ? window.t(text, values) : fill(text, values));

// days are "2026-11-27" strings; worked out on UTC dates, so no time zone or summer time gets in the way
const pad = (n) => String(n).padStart(2, "0");
const isoOf = (d) => `${String(d.getUTCFullYear()).padStart(4, "0")}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
function dayOf(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m || +m[1] < 1000) return null;
  const d = new Date(Date.UTC(+m[1], m[2] - 1, +m[3]));
  return isoOf(d) === iso ? d : null;  // not 2026-02-30
}
const addDays = (iso, n) => {
  const d = dayOf(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return isoOf(d);
};
function addMonths(iso, n) {  // the same day of the month, or its last day (31 Jan + 1 month: 28 Feb)
  const d = dayOf(iso), day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + n);
  d.setUTCDate(Math.min(day, new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate()));
  return isoOf(d);
}
const monthOf = (iso) => `${iso.slice(0, 7)}-01`;

// the regions whose weeks start on Sunday (where the browser can't say: Firefox has no weekInfo)
const SUNDAY_FIRST = new Set(("AG AS BD BR BS BT BW BZ CA CN CO DM DO ET GT GU HK HN ID IL IN JM JP KE KH KR LA MH MM MO "
  + "MT MX MZ NI NP PA PE PH PK PR PT PY SA SG SV TH TT TW UM US VE VI WS YE ZA ZW").split(" "));

/* x-date="<a day>" on an <input>: a text field for a day, with a calendar of our own under it (not the
   browser's <input type="date">, which looks different in every browser and doesn't follow the theme).
   The expression holds the day as "2026-11-27", or "" for none; the field shows it in the page's language
   ("27 Nov 2026") and takes what's typed in it (27.11.2026, 2026-11-27, 27 Nov, Nov 27 2026…).
   Two x-date fields in a .date-range are a from/to pair: the calendar shows the days between them, a pick
   that would put them the wrong way round swaps them, and it has quick picks (Last 7 days, Last 30 days,
   This year). A click on the field, or Down, opens the calendar; in it the arrow keys move by day and
   week, Page Up / Page Down by month (with Shift by year), Home / End to the week's ends, Enter or Space
   picks, Escape closes. A grid for screen readers, each day named in full; today is marked. On a phone
   (narrower than options.sheetBelow) it's a bottom sheet with big days.
   Options: min, max (days outside can't be picked), quick (the range's quick picks; true), sheetBelow
   (700). Events: kit-change { value } after a pick, kit-open, kit-closed.
   locale(), firstDay(), today(), format(), short(), parse(), move() and quick() are methods, so a plugin
   can change one of them alone. */
own.set("date", {
  options: { min: "", max: "", quick: true, sheetBelow: 700 },
  // the page's language; the browser's own when it's the same language with a country (en-GB: 27 Nov)
  locale() {
    const page = document.documentElement?.lang || "en", nav = globalThis.navigator?.language || "";
    return nav.includes("-") && nav.split("-")[0].toLowerCase() === page.split("-")[0].toLowerCase() ? nav : page;
  },
  // 0 = Sunday, 1 = Monday…
  firstDay(locale = this.locale()) {
    try {
      const l = new Intl.Locale(locale);
      const info = l.getWeekInfo?.() || l.weekInfo;
      if (info?.firstDay) return info.firstDay % 7;
      return SUNDAY_FIRST.has(l.maximize().region) ? 0 : 1;
    } catch {
      return 1;
    }
  },
  today() {
    const n = new Date();
    return `${n.getFullYear()}-${pad(n.getMonth() + 1)}-${pad(n.getDate())}`;
  },
  // "27 Nov 2026" (in the page's language); "" for no day
  format(iso, opts = { day: "numeric", month: "short", year: "numeric" }) {
    const d = dayOf(iso);
    return d ? new Intl.DateTimeFormat(this.locale(), { ...opts, timeZone: "UTC" }).format(d) : "";
  },
  // "27 Nov" this year, "27 Nov 2025" another year: a filter's pill
  short(iso) {
    return this.format(iso, iso?.slice(0, 4) === this.today().slice(0, 4) ? { day: "numeric", month: "short" } : undefined);
  },
  // what's typed in the field as a day: "2026-11-27", "" when it's empty, null when it isn't a day. The
  // numbers go in the order the language writes them (27.11.2026, 11/27/2026, 2026/11/27); a month may be
  // a name ("27 Nov", "November 27, 2026"); no year: this year
  parse(text) {
    const s = String(text ?? "").trim();
    if (!s) return "";
    const locale = this.locale(), year = +this.today().slice(0, 4);
    const make = (y, m, d) => {
      if (!y || !m || !d) return null;
      const iso = `${String(y < 100 ? 2000 + y : y).padStart(4, "0")}-${pad(m)}-${pad(d)}`;
      return dayOf(iso) ? iso : null;
    };
    const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
    if (iso) return make(+iso[1], +iso[2], +iso[3]);
    const low = s.toLocaleLowerCase(locale);
    const nums = low.match(/\d+/g) || [];
    let month = 0;
    for (const word of low.match(/\p{L}+/gu) || []) {
      for (let m = 0; m < 12 && !month; m++) {
        const names = ["long", "short"].map((style) => new Intl.DateTimeFormat(locale, { month: style, timeZone: "UTC" })
          .format(new Date(Date.UTC(2001, m, 15))).toLocaleLowerCase(locale).replace(/\.$/, ""));
        if (names.includes(word) || (word.length >= 3 && names[0].startsWith(word))) month = m + 1;
      }
    }
    if (month) {
      const y = nums.find((n) => n.length >= 3);
      const d = nums.find((n) => n !== y);
      return make(y ? +y : year, month, d ? +d : 0);
    }
    const order = new Intl.DateTimeFormat(locale, { year: "numeric", month: "numeric", day: "numeric", timeZone: "UTC" })
      .formatToParts(new Date(Date.UTC(2001, 10, 22))).map((p) => p.type).filter((p) => ["year", "month", "day"].includes(p));
    const v = {};
    if (nums.length === 3) {
      (nums[0].length >= 3 ? ["year", "month", "day"] : order).forEach((k, i) => (v[k] = +nums[i]));
    } else if (nums.length === 2) {
      order.filter((k) => k !== "year").forEach((k, i) => (v[k] = +nums[i]));
      v.year = year;
    } else {
      return null;
    }
    return make(v.year, v.month, v.day);
  },
  // where a key in the calendar goes from a day: the day, or null for a key it doesn't use
  move(iso, key, { shift = false, first = this.firstDay() } = {}) {
    const back = (dayOf(iso).getUTCDay() - first + 7) % 7;  // days since the week's first
    switch (key) {
      case "ArrowLeft": return addDays(iso, -1);
      case "ArrowRight": return addDays(iso, 1);
      case "ArrowUp": return addDays(iso, -7);
      case "ArrowDown": return addDays(iso, 7);
      case "Home": return addDays(iso, -back);
      case "End": return addDays(iso, 6 - back);
      case "PageUp": return addMonths(iso, shift ? -12 : -1);
      case "PageDown": return addMonths(iso, shift ? 12 : 1);
      default: return null;
    }
  },
  // a range's quick picks: [label, from, to]; to "" is up to now
  quick(today = this.today()) {
    return [[t("Last 7 days"), addDays(today, -6), ""], [t("Last 30 days"), addDays(today, -29), ""],
            [t("This year"), `${today.slice(0, 4)}-01-01`, ""]];
  },
  setup(ctx) {
    const piece = this, { el: input, options } = ctx;
    if (input.tagName !== "INPUT") return;
    const state = {
      get: () => String(ctx.get() ?? ""),
      set: (v) => {
        ctx.set(v);
        ctx.emit("change", { value: v });
      },
    };
    input._kitDate = state;
    input.classList.add("date-input");
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-haspopup", "dialog");
    input.setAttribute("aria-expanded", "false");

    // the other field of a from/to pair
    const range = () => {
      const all = [...(input.closest(".date-range")?.querySelectorAll("input") || [])].filter((i) => i._kitDate);
      if (all.length !== 2) return null;
      const from = all.indexOf(input) === 0;
      return { from, other: all[from ? 1 : 0]._kitDate };
    };
    const allowed = (iso) => (!options.min || iso >= options.min) && (!options.max || iso <= options.max);

    let typing = false;  // the field holds what's being typed, not the day
    // the calendar (made the first time it opens), the day with the focus in it and the month showing
    let pop = null, backdrop = null, grid = null, monthSel = null, yearSel = null, quickRow = null, said = null;
    let at = "", month = "", sheet = false, first = 1;
    const keyboard = input.inputMode;  // the field's own, back after a phone's calendar closes
    ctx.effect(() => {
      const v = state.get();
      if (!typing) input.value = piece.format(v);
      if (pop && !pop.hidden) draw();
    });

    // a day picked (or typed): in a pair, the wrong way round swaps them
    const pick = (v) => {
      const r = range(), o = r?.other.get();
      if (v && o && (r.from ? v > o : v < o)) {
        state.set(o);
        r.other.set(v);
      } else if (v !== state.get()) {
        state.set(v);
      }
      input.value = piece.format(state.get());
    };
    const commit = () => {
      if (!typing) return true;
      const v = piece.parse(input.value);
      if (v === null || (v && !allowed(v))) {
        input.setAttribute("aria-invalid", "true");
        return false;
      }
      typing = false;
      input.removeAttribute("aria-invalid");
      pick(v);
      return true;
    };

    /* the calendar goes in the dialog the field is in (so that dialog keeps the focus and the clicks),
       else in the page */
    const h =(tag, props = {}, ...kids) => {
      const e = Object.assign(document.createElement(tag), props);
      e.append(...kids);
      return e;
    };
    const icon = (name) => {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "ic");
      svg.setAttribute("aria-hidden", "true");
      const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      use.setAttribute("href", `#i-${name}`);
      svg.append(use);
      return svg;
    };
    const build = () => {
      const where = input.closest(".modal > div, .sheet > div") || document.body;
      backdrop = h("div", { className: "date-backdrop", hidden: true });
      const name = input.getAttribute("aria-label") || t("Choose a date");
      const close = h("button", { type: "button", className: "btn small", textContent: t("Done") });
      close.dataset.act = "close";
      const prev = h("button", { type: "button", className: "icon-btn" }, icon("chevron-left"));
      const next = h("button", { type: "button", className: "icon-btn" }, icon("chevron-right"));
      prev.dataset.go = "-1";
      next.dataset.go = "1";
      prev.setAttribute("aria-label", t("Previous month"));
      next.setAttribute("aria-label", t("Next month"));
      monthSel = h("select", { className: "date-month" });
      yearSel = h("select", { className: "date-year" });
      monthSel.setAttribute("aria-label", t("Month"));
      yearSel.setAttribute("aria-label", t("Year"));
      for (let m = 0; m < 12; m++) {
        monthSel.append(h("option", { value: pad(m + 1), textContent: piece.format(`2001-${pad(m + 1)}-15`, { month: "long" }) }));
      }
      grid = h("table", { className: "date-grid" });
      grid.setAttribute("role", "grid");
      quickRow = h("div", { className: "date-quick" });
      const today = h("button", { type: "button", className: "btn small", textContent: t("Today") });
      const clear = h("button", { type: "button", className: "btn small", textContent: t("Clear") });
      today.dataset.act = "today";
      clear.dataset.act = "clear";
      said = h("div", { className: "sr-only" });
      said.setAttribute("aria-live", "polite");
      pop = h("div", { className: "date-pop", hidden: true, id: `kit-date-${++uid}` },
        h("div", { className: "date-title" }, h("b", { textContent: name }), close),
        h("div", { className: "date-head" }, prev, monthSel, yearSel, next),
        grid, quickRow, h("div", { className: "date-foot" }, today, clear), said);
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-label", name);
      where.append(backdrop, pop);
      input.setAttribute("aria-controls", pop.id);
      pop.addEventListener("click", onPopClick);
      pop.addEventListener("keydown", onPopKey);
      pop.addEventListener("focusout", onLeave);
      monthSel.addEventListener("change", () => showMonth(`${yearSel.value}-${monthSel.value}-01`, true));
      yearSel.addEventListener("change", () => showMonth(`${yearSel.value}-${monthSel.value}-01`, true));
      backdrop.addEventListener("click", () => close_(false));
    };

    // the month showing, the day with the focus kept in it
    const showMonth = (m, keepFocus = false) => {
      const day = Math.min(+at.slice(8), new Date(Date.UTC(+m.slice(0, 4), +m.slice(5, 7), 0)).getUTCDate());
      at = `${m.slice(0, 8)}${pad(day)}`;
      month = m;
      draw();
      said.textContent = piece.format(m, { month: "long", year: "numeric" });
      if (!keepFocus) focusDay();
    };
    const focusDay = () => pop.querySelector(`[data-day="${at}"]`)?.focus({ preventScroll: true });
    const goTo = (iso) => {
      at = iso;
      if (monthOf(iso) !== month) showMonth(monthOf(iso));
      else {
        draw();
        focusDay();
      }
    };

    function draw() {
      const value = state.get(), r = range(), other = r?.other.get() || "";
      const [lo, hi] = r ? (r.from ? [value, other] : [other, value]) : [value, value];
      const today = piece.today();
      // the years to pick from: 30 back, 5 ahead, and any the days shown need
      const y = +month.slice(0, 4), now = +today.slice(0, 4);
      const years = [];
      for (let n = Math.min(now - 30, y, ...(options.min ? [+options.min.slice(0, 4)] : [])),
           top = Math.max(now + 5, y); n <= top; n++) years.push(n);
      if (yearSel.options.length !== years.length || yearSel.options[0]?.value !== String(years[0])) {
        yearSel.replaceChildren(...years.map((n) => h("option", { value: String(n), textContent: String(n) })));
      }
      yearSel.value = String(y);
      monthSel.value = month.slice(5, 7);
      const head = h("tr");
      for (let i = 0; i < 7; i++) {
        const wd = `2023-01-${pad(1 + ((first + i) % 7))}`;  // 1 Jan 2023 was a Sunday
        const th = h("th", { textContent: piece.format(wd, { weekday: "short" }) });
        th.setAttribute("role", "columnheader");
        th.setAttribute("abbr", piece.format(wd, { weekday: "long" }));
        head.append(th);
      }
      const body = [];
      let day = addDays(month, -((dayOf(month).getUTCDay() - first + 7) % 7));
      for (let w = 0; w < 6; w++) {
        const row = h("tr");
        for (let i = 0; i < 7; i++, day = addDays(day, 1)) {
          const b = h("button", { type: "button", textContent: String(+day.slice(8)), tabIndex: day === at ? 0 : -1 });
          b.dataset.day = day;
          b.setAttribute("aria-label", piece.format(day, { weekday: "long", day: "numeric", month: "long", year: "numeric" }));
          const picked = day === value || (r && day === other);
          const cls = [monthOf(day) !== month && "other", picked && "picked", day === today && "today",
                       r && lo && hi && day > lo && day < hi && "between", r && lo && day === lo && hi && "start",
                       r && hi && day === hi && lo && "end"].filter(Boolean);
          b.className = cls.join(" ");
          if (day === today) b.setAttribute("aria-current", "date");
          if (!allowed(day)) b.setAttribute("aria-disabled", "true");
          const cell = h("td", {}, b);
          cell.setAttribute("role", "gridcell");
          cell.setAttribute("aria-selected", String(!!picked));
          row.append(cell);
        }
        body.push(row);
      }
      grid.replaceChildren(h("thead", {}, head), h("tbody", {}, ...body));
      const quick = r && options.quick ? piece.quick(today) : [];
      quickRow.hidden = !quick.length;
      quickRow.replaceChildren(...quick.map(([label, from, to]) => {
        const b = h("button", { type: "button", className: "chip-btn", textContent: label });
        b.dataset.from = from;
        b.dataset.to = to;
        const on = (r.from ? [value, other] : [other, value]).join() === [from, to].join();
        b.classList.toggle("on", on);
        b.setAttribute("aria-pressed", String(on));
        return b;
      }));
    }

    const place = () => {
      if (!pop || pop.hidden) return;
      if (sheet) {  // css/kit/date.css puts it at the bottom
        pop.style.removeProperty("left");
        pop.style.removeProperty("top");
        return;
      }
      const r = input.getBoundingClientRect(), w = pop.offsetWidth, ht = pop.offsetHeight;
      const top = r.bottom + 6 + ht > innerHeight - 8 && r.top - ht - 6 >= 8 ? r.top - ht - 6 : r.bottom + 6;
      pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - w - 8))}px`;
      pop.style.top = `${top}px`;
    };
    const isOpen = () => pop && !pop.hidden;
    const open = (focusGrid) => {
      if (!pop) build();
      if (isOpen()) return focusGrid && focusDay();
      const r = range();
      const typed = typing ? piece.parse(input.value) : null;
      at = typed || state.get() || r?.other.get() || piece.today();
      month = monthOf(at);
      first = piece.firstDay();
      sheet = innerWidth < options.sheetBelow;
      pop.classList.toggle("as-sheet", sheet);
      backdrop.hidden = !sheet;
      draw();
      pop.hidden = false;
      input.setAttribute("aria-expanded", "true");
      place();
      if (focusGrid || sheet) focusDay();
      document.addEventListener("pointerdown", onOutside, true);
      addEventListener("resize", place);
      addEventListener("scroll", place, true);
      ctx.emit("open");
    };
    // back in the field after a key (Escape, Enter); not after a tap on a phone, where that would bring up
    // its keyboard
    function close_(focusField) {
      if (!isOpen()) return;
      const inside = pop.contains(document.activeElement);
      pop.hidden = true;
      backdrop.hidden = true;
      input.setAttribute("aria-expanded", "false");
      document.removeEventListener("pointerdown", onOutside, true);
      removeEventListener("resize", place);
      removeEventListener("scroll", place, true);
      input.inputMode = keyboard;
      if (focusField || (inside && !sheet)) input.focus({ preventScroll: true });
      else if (inside) document.activeElement.blur();
      ctx.emit("closed");
    }
    const choose = (iso, byKey) => {
      if (!allowed(iso)) return;
      typing = false;
      input.removeAttribute("aria-invalid");
      pick(iso);
      close_(byKey);
    };

    function onPopClick(e) {
      const b = e.target.closest("button");
      if (!b) return;
      const byKey = e.detail === 0;
      if (b.dataset.day) return choose(b.dataset.day, byKey);
      if (b.dataset.go) return showMonth(addMonths(month, +b.dataset.go), true);
      if (b.dataset.from !== undefined) {
        const r = range();
        const [mine, other] = r.from ? [b.dataset.from, b.dataset.to] : [b.dataset.to, b.dataset.from];
        typing = false;
        state.set(mine);
        r.other.set(other);
        return close_(byKey);
      }
      if (b.dataset.act === "today") return goTo(piece.today());
      if (b.dataset.act === "clear") return choose("", byKey);
      if (b.dataset.act === "close") return close_(byKey);
    }
    function onPopKey(e) {
      if (e.key === "Escape") {
        e.preventDefault();  // a dialog around it stays open
        return close_(true);
      }
      if (e.key === "Tab") {  // round inside the calendar
        const all = focusables(pop);
        const i = all.indexOf(document.activeElement);
        e.preventDefault();
        return all.at((i + (e.shiftKey ? -1 : 1)) % all.length)?.focus();
      }
      const day = e.target.dataset?.day;
      if (!day || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        return choose(day, true);
      }
      const to = piece.move(day, e.key, { shift: e.shiftKey, first });
      if (!to) return;
      e.preventDefault();
      goTo(to);
    }
    // the focus or a click went somewhere else: close (what was typed stays, for the field's change)
    function onLeave(e) {
      if (e.relatedTarget && !pop.contains(e.relatedTarget) && e.relatedTarget !== input) close_(false);
    }
    function onOutside(e) {
      if (!pop.contains(e.target) && e.target !== input && e.target !== backdrop) close_(false);
    }

    const onClick = () => open(false);
    // a tap on a phone opens the sheet (the click), without the phone's keyboard coming up for the field
    const onPointer = (e) => {
      if (e.pointerType === "touch" && innerWidth < options.sheetBelow) input.inputMode = "none";
    };
    const onKey = (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        return open(true);
      }
      if (e.key === "Escape" && isOpen()) {
        e.preventDefault();
        return close_(true);
      }
      if (e.key === "Enter") {
        e.preventDefault();
        if (commit()) close_(true);
      }
    };
    const onInput = () => {
      typing = true;
      const v = piece.parse(input.value);  // the calendar follows what's typed
      if (v && isOpen() && v !== at) {
        at = v;
        month = monthOf(v);
        draw();
      }
    };
    const onChange = () => commit();
    const onBlur = (e) => {
      if (!pop?.contains(e.relatedTarget)) {
        commit();
        if (e.relatedTarget) close_(false);
      }
    };
    const on = [["click", onClick], ["pointerdown", onPointer], ["keydown", onKey], ["input", onInput],
                ["change", onChange], ["blur", onBlur]];
    on.forEach(([type, f]) => input.addEventListener(type, f));
    return () => {
      close_(false);
      on.forEach(([type, f]) => input.removeEventListener(type, f));
      pop?.remove();
      backdrop?.remove();
      delete input._kitDate;
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
