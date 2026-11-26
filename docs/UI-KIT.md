# The UI kit

The pieces MangaVibe and its plugins share: buttons, fields, chips, tabs, menus, dialogs, toasts… Use them in
a plugin's markup and it looks and works like the rest of the app, and it follows the owner's theme and
`custom.css`. Plugin API 31 (issue #294); dialogs for the keyboard and screen readers, API 32; menus (`x-menu`), API 33; tabs and choices (`x-tabs`, `x-choice`), API 34.

**See them all at `/kit`** (e.g. `http://localhost:8000/kit`): every piece in its variants, dark and light,
with your plugins' CSS and `custom.css` applied.

Three promises:

- **Plain markup.** Each piece is HTML with a class (`<button class="btn primary">`) and, for the ones that
  do something, an Alpine directive (`x-dialog`). No shadow DOM, no build step, nothing to import.
- **You can restyle anything.** The kit's CSS is in the `kit` cascade layer; plugin CSS and `custom.css`
  come after every layer, so any rule of yours wins, without `!important`. Each piece also reads custom
  properties of its own (below), so a box of yours can change only the pieces inside it.
- **You can change what they do.** The behaviour of each interactive piece is a plain object in
  `MangaVibe.kit`: change an option on one element, change it for the whole app, or put a piece of your own
  in its place.

## Restyling

The tokens in `app/web/static/css/tokens.css` (`--accent`, `--surface`, `--radius`…) change everything at
once (see [Styling](PLUGINS.md#styling-plugins-and-your-own-customcss)). To change one piece, set its
custom properties, on `:root` for the whole app or on a box for the pieces inside it:

```css
/* every button in my plugin's panel: green and round */
.my-panel {
  --btn-bg: color-mix(in srgb, var(--ok) 18%, var(--surface2));
  --btn-border: var(--ok);
  --btn-radius: 999px;
}

/* wider dialogs everywhere */
:root { --dialog-width: 900px; }
```

A custom property that isn't set falls back to the token, so the piece keeps following the theme. Any other
change is a normal CSS rule: `.my-panel .btn { font-weight: 700 }` wins over the kit's own.

## Changing the behaviour: `MangaVibe.kit`

An interactive piece (the dialog, the menu, tabs and choices: `dialog`, `menu`, `tabs`, `choice`) is an
object `{ options, setup(ctx) }`. Its directive (`x-dialog`, `x-menu`, `x-tabs`, `x-choice`) asks
`MangaVibe.kit` for it each time an element starts, so:

| | |
|---|---|
| `data-kit-options="{ backdrop: false }"` | on one element: its options (an Alpine expression, so it can use the component's data) |
| `MangaVibe.kit.extend(pluginId, name, fn)` | for the whole app: `fn(piece)` gets the piece so far and returns the new one |
| `MangaVibe.kit.define(pluginId, name, piece)` | for the whole app: a piece of your own in its place |
| `MangaVibe.kit.get(name)` | the piece as it is now (with every switched-on plugin's changes) |
| `MangaVibe.kit.names()` | the names of the pieces |

`extend` and `define` count only while your plugin is switched on, and reach the elements that start after
the call: call them in your script's first lines (before Alpine starts, when the page loads), not in a
`start` handler. Several plugins' changes apply in the order their scripts ran.

```js
// every dialog: a click on the backdrop no longer closes it (Escape still does)
MangaVibe.kit.extend("my-plugin", "dialog", (base) => ({ ...base, options: { ...base.options, backdrop: false } }));

// and say something when one closes
MangaVibe.kit.extend("my-plugin", "dialog", (base) => ({
  ...base,
  setup(ctx) {
    const undo = base.setup(ctx);
    ctx.el.addEventListener("kit-close", (e) => console.log("closed by", e.detail.reason));
    return undo;
  },
}));
```

`setup(ctx)` runs when an element with the directive starts and may return a function that undoes what it
did (it runs when the element goes). `ctx` has:

| | |
|---|---|
| `el` | the element |
| `options` | the piece's `options`, then the element's `data-kit-options` |
| `run()` | runs the directive's expression (`x-dialog="open = false"`) |
| `expression`, `get()`, `set(value)` | the directive's expression as text, and as a value to read and set (`x-menu="open"`) |
| `effect(fn)` | runs `fn` again whenever what it read changes (Alpine's `effect`, gone with the element) |
| `emit(event, detail)` | sends `kit-<event>` from the element (it bubbles); `false` when a listener called `preventDefault()` |
| `modifiers`, `value`, `name` | the directive's modifiers (`x-dialog.foo`), value (`x-dialog:foo`) and the piece's name |

**Events.** The pieces send `kit-…` events that bubble, so you can react without changing anything:
`@kit-close="…"` in markup, or `addEventListener("kit-close", …)`. A listener that calls `preventDefault()`
stops what the piece was about to do (a dialog stays open).

## The pieces

### Buttons

```html
<button class="btn">Button</button>
<button class="btn primary">Save</button>
<button class="btn small danger">Delete</button>
<button class="iconbtn" aria-label="Filter"><svg class="ic" aria-hidden="true"><use href="#i-filter"/></svg></button>
<div class="btn-row">…</div>
```

- `.btn`: `.small`, `.primary` (the accent colour), `.danger`, `.on` (switched on), `.busy` (a spinner before
  the label; disable the button too), `.disabled` or `disabled`.
- `.iconbtn`: a square button with an icon, the height of a `.btn`; a `<span class="count">` on it is a
  number in its corner. `.icon-btn`: a small round one. `.link-btn`: looks like a link.
- `.btn-row`: a row of buttons that wraps.
- Give a button that only has an icon an `aria-label` (and a `title`).
- Custom properties: `--btn-bg`, `--btn-border`, `--btn-radius`, `--btn-height` (all buttons), `--btn-primary-bg`,
  `--btn-primary-text`, `--btn-danger`.

### Fields

```html
<input type="text" placeholder="Name" aria-label="Name">
<select aria-label="Order">…</select>
<label class="row"><input type="checkbox" class="switch"> Show spoilers</label>
```

- Text fields, `select` and `textarea` are styled as they are, no class needed. A checkbox is a rounded box;
  with `.switch` (or in a `.setting-row`) it's a switch.
- Every field needs a name a screen reader can read: a `<label>` around it or `aria-label`.
- Custom properties: `--field-bg`, `--field-border`, `--field-radius`, `--field-height`, `--field-focus`,
  `--check-on`, `--switch-off`, `--switch-on`.

### Chips

```html
<div class="chips"><span class="chip">Isekai</span><span class="chip tag" @click="…">Action</span></div>
```

- `.chip`: `.tag` (one to click), `.warn`, `.required`, `.spoiler`. `.chips`: a row of them that wraps.
- Custom property: `--chip-bg`.

### Tabs

```html
<div class="tabs" x-tabs aria-label="Series">
  <button data-tab="books" :class="{ active: tab === 'books' }" @click="tab = 'books'">Books <span class="count">12</span></button>
  <button data-tab="files" :class="{ active: tab === 'files' }" @click="tab = 'files'">Files</button>
</div>
<div data-tab-panel="books" x-show="tab === 'books'">…</div>
<div data-tab-panel="files" x-show="tab === 'files'">…</div>
```

- The active tab has `.active`, and your `@click` picks one, as always. `x-tabs` adds the rest:
  `role="tablist"` and `role="tab"` with `aria-selected`; one stop for Tab (the active tab); Left and
  Right move between the tabs (round), Home and End to the ends, and pick the one they land on by clicking
  it, so your `@click` runs.
- `data-tab` on a tab and `data-tab-panel` with the same value on what it shows (inside the tabs'
  parent) tie them together for screen readers: `aria-controls`, `role="tabpanel"`, `aria-labelledby`.
- Option `manual` (`data-kit-options="{ manual: true }"`): the arrows only move, Enter or Space picks.
  For tabs whose picking does more than show something (Fix match's sources search, a locked library
  asks for its password).
- A button in the row that isn't a tab (the libraries' +) gets `data-kit-skip`.
- `.tabs.small`: tighter; `.tabs.scroll`: scrolls sideways when it doesn't fit.
- A plugin can change one part only: the methods are `items(el)`, `isPicked(item)` and `link(el, tab)`.
- Custom properties: `--tabs-accent`, `--tabs-border`.

### Segmented

```html
<div class="segmented" x-choice aria-label="Order">
  <button :class="{ on: asc }" @click="asc = true">Ascending</button>
  <button :class="{ on: !asc }" @click="asc = false">Descending</button>
</div>
```

- Two or three choices side by side; the chosen one has `.on`, and your `@click` picks.
- `x-choice` makes it a radio group for screen readers (`role="radiogroup"`, `role="radio"` with
  `aria-checked`; give the group an `aria-label`), with one stop for Tab and the arrow keys (all four, round,
  plus Home and End) moving and picking. Option `manual`, `data-kit-skip`, the methods: as for tabs.
- Custom properties: `--choice-on`, `--choice-on-text`, `--segmented-bg`.

### Options

```html
<div class="options" x-choice aria-label="Reading direction">
  <button :class="{ on: dir === 'rtl' }" @click="dir = 'rtl'">
    <span class="radio" aria-hidden="true"></span><span class="text">Right to left<small>Manga</small></span></button>
  …
</div>
```

- One choice per row, like radio buttons (the sort sheet, reading direction); the chosen one has `.on`.
- `x-choice` as for Segmented. When picking one also closes something (the sort sheet, the reading mode
  dialog), use `data-kit-options="{ manual: true }"`, so the arrows don't pick on the way.
- A list of buttons that each *do* something (Move to library) is `.options` without `x-choice`.
- Custom property: `--choice-on`.

### Panel

```html
<section class="panel"><h2>Notes</h2>…</section>
```

- A titled card: its `h2` is a small uppercase title, `h3` a subtitle.
- Custom properties: `--panel-bg`, `--panel-border`, `--panel-radius`, `--panel-padding`, `--panel-shadow`.

### Menu

```html
<div class="menu" x-menu="open">
  <button class="icon-btn" aria-label="More">…</button>
  <div class="menu-pop" x-show="open" x-transition.opacity x-cloak>
    <button @click="rescan()"><svg class="ic" aria-hidden="true"><use href="#i-refresh"/></svg>Rescan</button>
    <button class="danger" @click="openDelete()">…Delete</button>
  </div>
</div>
```

- A button that opens a list of actions, from its top right corner. A menu item can have a `<small>` second
  line and `.danger`.
- `x-menu` opens and closes it: **don't put an `@click` on its button**, nor `@click.outside` or
  `@keydown.escape` on the menu. `x-menu="open"` keeps open or closed in your own data (markup can still
  say `open = false`); a plain `x-menu` keeps it itself, through the pop-up's `hidden`, so the pop-up
  needs no `x-show`.
- For screen readers: the button gets `aria-haspopup="menu"`, `aria-expanded` and `aria-controls`; the
  pop-up `role="menu"`, named after the button; its buttons and links `role="menuitem"`. Give a button
  that only has an icon an `aria-label`.
- Keys: Enter, Space or Down on the button opens the menu on its first item, Up on its last; Up, Down,
  Home and End move (round); a letter goes to the next item that starts with it; Escape closes it and puts
  the focus back on the button (a dialog around the menu stays open, and so does the library's
  selection); Tab closes it. A click on an item or outside closes it; with the mouse the focus stays where
  it was.
- It stays on the screen: from the button's left edge when its right one is too close to the screen's
  left side, above the button when there's no room below.
- Options: `keepOpen` (a click on an item leaves it open, e.g. for switches), `popover` (not a list of
  actions but anything, e.g. a small form: no menu roles or arrow keys, Tab moves through it, and leaving
  it closes it; `aria-haspopup="dialog"`).
- Events: `kit-open`; `kit-select` with `detail.item` before a picked item closes it (`preventDefault()`
  keeps it open); `kit-closed`.
- A plugin can change one part only: the methods are `button(el)`, `pop(el)`, `items(pop)` and
  `place(pop, el)`.
- Items in a plugin slot (`series-menu`, `select-menu`) are menu items too, nothing to change.
- Custom properties: `--menu-bg`, `--menu-width`, `--menu-radius`.

### Dialog

```html
<div class="modal" x-show="$store.notes.open" x-cloak x-dialog="$store.notes.open = false">
  <div>
    <div class="sheet-head"><b>Notes</b><div class="btn-row"><button class="btn small primary">Save</button></div></div>
    …
  </div>
</div>
```

- `.modal` or `.sheet` is the backdrop, its `div` the box: a sheet from the bottom on phones, a centred
  dialog on wider screens. `.sheet-head`: a title and buttons that stay at the top while it scrolls.
- `x-dialog="<how to close it>"`: a click on the backdrop, Escape and the Android app's back button run it.
  When several dialogs show (one opened from another), only the top one closes.
- Show and hide it as you like (`x-show`, `x-if`): the kit sees it. While it shows:
  - the box is a dialog for screen readers (`role="dialog"`, `aria-modal="true"`), named after its title:
    an element with `data-kit-label`, else the `.sheet-head`'s `<b>`, else its first heading. A title that
    comes a moment later (an `x-if` waiting for data) still names it. Set `aria-label` on the box yourself
    if it has no title;
  - the focus moves into it: to an element with `autofocus`, else to the box itself, so a phone doesn't
    open its keyboard (option `focus: "field"`: the first field). If your script already put the focus
    in it (a password field), it stays there;
  - Tab and Shift+Tab go round inside it; the rest of the page is `inert` (no clicks, no Tab, not read
    out; the toast still is) and doesn't scroll.
- When it hides, the focus goes back to what opened it (or, if that was an item of a menu that closed,
  to the menu's button).
- Options: `backdrop` (a click on it closes; `true`), `escape` (`true`), `modal` (`false`: the page
  behind stays usable and the focus isn't kept in; `true`), `focus` (`"box"` or `"field"`), `restoreFocus`
  (`true`).
- Events: `kit-open` once it shows; `kit-close` with `detail.reason` `"backdrop"` or `"escape"`, before
  it closes, where `preventDefault()` keeps it open (e.g. to ask about unsaved changes); `kit-closed` once
  it's hidden.
- A plugin can change one part only, e.g. what the dialog is named after: its methods are `box(el)`,
  `label(box)` (returns `true` once named), `focusIn(box, options)` and `opener(element)`.

  ```js
  MangaVibe.kit.extend("my-plugin", "dialog", (base) => ({
    ...base,
    label: (box) => base.label(box) || (box.setAttribute("aria-label", "Dialog"), true),
  }));
  ```
- Custom properties: `--dialog-width`, `--dialog-bg`, `--dialog-radius`, `--dialog-backdrop`.

### Toast

- `MangaVibe.toast(text, ms, kind)`: a short message at the bottom; `kind = "error"` for a red one.
- Custom properties: `--toast-bg`, `--toast-text`.

### Empty

```html
<div class="empty"><svg class="ic" aria-hidden="true"><use href="#i-books"/></svg><span>Nothing here yet.</span></div>
```

- Nothing to show: a quiet icon above a line of text.

### Loading

```html
<div class="loading-line">Loading…</div>
```

- “Loading…” with a spinner before it.

## Working on the kit

- The CSS is in `app/web/static/css/kit/`, one file per piece, imported by `main.css` in the `kit` layer
  (after `base`, before `layout` and the pages). The behaviour is in `app/web/static/js/kit.js`.
- A new piece gets: its CSS file (custom properties that fall back to the tokens), a `### Name` section
  here, and a `data-piece="name"` section on the kit page (`app/web/kit/kit.html`).
  `tests/test_ui_kit.py` checks the last two match.
- Settings cards keep no divider under their last row: dividers go *between* rows (see `CLAUDE.md`).
