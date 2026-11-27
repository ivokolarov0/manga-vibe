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

/* MangaVibe.confirm(…), MangaVibe.prompt(…) and MangaVibe.ask(…) (Plugin API 39, issue #323): our own
   dialog in place of the browser's confirm() and prompt(). Each returns a Promise:
     await confirm({ title, text, list, note, confirm: t("Delete"), cancel, danger })  -> true / false
     await prompt({ title, text, label, value, placeholder, type, confirm, required, check }) -> the text / null
     await ask({ title, text, list, note, buttons: [{ label, value, danger, primary }] }) -> a button's value / null
   (confirm and prompt also take a plain text: confirm("Delete it?")). text: blank lines start a new
   paragraph; list: a list of points under it (a plugin's permissions); note: a paragraph after the list.
   check(value): a message when what's typed won't do ("At least 4 characters"), shown under the field.
   A native <dialog> opened with showModal(): the page behind can't be reached, Tab stays inside; Escape, a
   click on the backdrop and the Android app's back button cancel; the focus goes back to what opened it.
   It's a sheet from the bottom on phones (css/kit/ask.css). For a danger one, the safe button (Cancel) has
   the focus first. One at a time: a second one waits for the first to close.
   build(), focusIn() and open() are methods, so a plugin can change one of them alone. */
const t = (msgid, values) => (typeof window.t === "function" ? window.t(msgid, values)
  : String(msgid).replace(/\{(\w+)\}/g, (m, k) => (values && k in values ? values[k] : m)));  // the kit page has no common.js
const make = (tag, props = {}, ...kids) => {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids.filter((k) => k !== null && k !== undefined && k !== ""));
  return e;
};
const paragraphs = (text) => String(text ?? "").split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean)
  .map((s) => make("p", { textContent: s }));
let askQueue = Promise.resolve();
own.set("ask", {
  options: { backdrop: true },
  // the dialog's parts: { dialog, form, field, input, error, buttons }
  build(o) {
    const id = ++uid;
    const title = o.title ? make("h2", { className: "ask-title", id: `kit-ask-title-${id}`, textContent: o.title }) : null;
    const list = o.list?.length ? make("ul", {}, ...o.list.map((x) => make("li", { textContent: String(x) }))) : null;
    const body = [...paragraphs(o.text), list, ...paragraphs(o.note)];
    const text = body.length ? make("div", { className: "ask-text", id: `kit-ask-text-${id}` }, ...body) : null;
    let field = null, input = null, error = null;
    if (o.prompt) {
      input = make("input", { type: o.type || "text", value: o.value ?? "", placeholder: o.placeholder || "",
                            id: `kit-ask-input-${id}`, autocomplete: o.type === "password" ? "new-password" : "off" });
      error = make("p", { className: "error", id: `kit-ask-error-${id}`, hidden: true });
      error.setAttribute("role", "alert");
      if (!o.label && !title) input.setAttribute("aria-label", t("Your answer"));
      field = make("div", { className: "field" },
                 o.label ? make("label", { htmlFor: input.id, textContent: o.label }) : null, input, error);
      if (!o.label && title) input.setAttribute("aria-labelledby", title.id);
    }
    const buttons = o.buttons.map((b) => {
      const button = make("button", { type: b.submit ? "submit" : "button", textContent: b.label,
                                    className: ["btn", b.primary && "primary", b.danger && "danger"].filter(Boolean).join(" ") });
      button.kitValue = b.value;
      return button;
    });
    const form = make("form", { method: "dialog", noValidate: true }, title, text, field,
                    make("div", { className: "ask-buttons" }, ...buttons));
    const dialog = make("dialog", { className: ["ask", o.danger && "danger"].filter(Boolean).join(" ") }, form);
    if (title) dialog.setAttribute("aria-labelledby", title.id);
    else dialog.setAttribute("aria-label", o.prompt ? o.label || t("Your answer") : t("Confirm"));
    if (text) dialog.setAttribute("aria-describedby", text.id);
    if (o.danger || !o.prompt) dialog.setAttribute("role", "alertdialog");
    return { dialog, form, field, input, error, buttons };
  },
  // a prompt's field (its text picked), else the safe button for a danger one, else the main button
  focusIn(parts, o) {
    if (parts.input) {
      parts.input.focus();
      return parts.input.select();
    }
    const i = o.danger ? o.buttons.findIndex((b) => b.value === null) : o.buttons.findIndex((b) => b.submit);
    (parts.buttons[i] || parts.buttons.at(-1))?.focus();
  },
  open(o) {
    const run = () => new Promise((resolve) => {
      const piece = this;
      const parts = piece.build(o);
      const { dialog, form, input, error } = parts;
      const was = document.activeElement;
      let done = false;
      const keys = (e) => {
        if (e.key !== "Escape" || e.isComposing) return;
        e.preventDefault();
        e.stopImmediatePropagation();  // only this closes: not a dialog under it, nor the reader
        finish(null);
      };
      const submit = parts.buttons.find((b) => b.type === "submit");
      // nothing typed yet (and it's needed): the button waits
      const empty = () => {
        if (input && o.required !== false) submit.disabled = !input.value.trim();
      };
      // what's typed won't do: say why under the field, and don't close
      const bad = () => {
        const why = o.check?.(input.value) || "";
        error.textContent = why;
        error.hidden = !why;
        for (const [k, v] of [["aria-invalid", "true"], ["aria-describedby", error.id]]) {
          if (why) input.setAttribute(k, v);
          else input.removeAttribute(k);
        }
        return !!why;
      };
      function finish(value) {
        if (done) return;
        done = true;
        removeEventListener("keydown", keys, true);
        openDialogs.splice(openDialogs.indexOf(dialog), 1);
        if (dialog.open) dialog.close();
        dialog.remove();
        lockScroll(false);
        const focus = document.activeElement;
        if (!focus || focus === document.body) get("dialog").opener(was)?.focus({ preventScroll: true });
        resolve(value);
      }
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        if (input && (submit.disabled || bad())) return input.focus();
        finish(input ? input.value : submit.kitValue);
      });
      for (const b of parts.buttons) if (b.type === "button") b.addEventListener("click", () => finish(b.kitValue));
      input?.addEventListener("input", () => {
        empty();
        if (!error.hidden) bad();  // again, once it said something
      });
      empty();
      dialog.addEventListener("cancel", (e) => {
        e.preventDefault();
        finish(null);
      });
      // the backdrop is the dialog itself (the form fills the box): a click that started and ended there
      let downOnBackdrop = false;
      dialog.addEventListener("pointerdown", (e) => (downOnBackdrop = e.target === dialog));
      dialog.addEventListener("click", (e) => {
        if (e.target === dialog && downOnBackdrop && o.backdrop !== false) finish(null);
      });
      // the page's own keys (the reader's arrows, a selection's Escape) don't see what's pressed in it
      dialog.addEventListener("keydown", (e) => e.stopPropagation());
      addEventListener("keydown", keys, true);  // also the Android back button's (sent to the window)
      document.body.append(dialog);
      dialog.inert = false;
      openDialogs.push(dialog);  // the x-dialog under it leaves Escape and Tab to it
      lockScroll(true);
      dialog.showModal();
      piece.focusIn(parts, o);
    });
    const p = askQueue.then(run);
    askQueue = p.catch(() => {});
    return p;
  },
  confirm(o) {
    if (typeof o !== "object" || o === null) o = { text: o };
    const opts = { ...this.options, ...o };
    return this.open({
      ...opts,
      buttons: [{ label: opts.cancel || t("Cancel"), value: null },
                { label: opts.confirm || t("OK"), value: true, submit: true, primary: true, danger: !!opts.danger }],
    }).then((v) => v === true);
  },
  prompt(o) {
    if (typeof o !== "object" || o === null) o = { label: o };
    const opts = { ...this.options, ...o };
    return this.open({
      ...opts, prompt: true,
      buttons: [{ label: opts.cancel || t("Cancel"), value: null },
                { label: opts.confirm || t("OK"), submit: true, primary: true, danger: !!opts.danger }],
    });
  },
  ask(o) {
    const opts = { ...this.options, ...o };
    const given = (opts.buttons || []).map((b) => ({ ...b, value: b.value ?? b.label }));
    const last = given.length - 1;
    return this.open({
      ...opts,
      buttons: [{ label: opts.cancel || t("Cancel"), value: null },
                ...given.map((b, i) => ({ ...b, primary: b.primary ?? (i === last), submit: i === last }))],
    });
  },
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
  // our own confirm and prompt dialogs (the "ask" piece): Promises, see above
  confirm: (o) => get("ask").confirm(o),
  prompt: (o) => get("ask").prompt(o),
  ask: (o) => get("ask").ask(o),
  // one of them is showing (the Android app's back button cancels it first)
  asking: () => !!document.querySelector("dialog.ask[open]"),
};
})();

window.MangaVibe ??= {};
window.MangaVibe.kit = kit;
Object.assign(window.MangaVibe, { confirm: kit.confirm, prompt: kit.prompt, ask: kit.ask });
