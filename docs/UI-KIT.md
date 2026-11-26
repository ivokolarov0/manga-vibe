# The UI kit

The pieces MangaVibe and its plugins share: buttons, fields, chips, tabs, menus, dialogs, toasts… Use them in
a plugin's markup and it looks and works like the rest of the app, and it follows the owner's theme and
`custom.css`. Plugin API 31 (issue #294).

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

An interactive piece (today: the dialog; menus, tabs and others come in the next steps of #294) is an
object `{ options, setup(ctx) }`. Its directive (`x-dialog`) asks `MangaVibe.kit` for it each time an
element starts, so:

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
<div class="tabs" role="tablist">
  <button role="tab" :class="{ active: tab === 'books' }" :aria-selected="tab === 'books'" @click="tab = 'books'">
    Books <span class="count">12</span></button>
  …
</div>
```

- The active tab has `.active`. `.tabs.small`: tighter; `.tabs.scroll`: scrolls sideways when it doesn't fit.
- Custom properties: `--tabs-accent`, `--tabs-border`.

### Segmented

```html
<div class="segmented" role="radiogroup" aria-label="Order">
  <button role="radio" :class="{ on: asc }" :aria-checked="asc" @click="asc = true">Ascending</button>
  <button role="radio" :class="{ on: !asc }" :aria-checked="!asc" @click="asc = false">Descending</button>
</div>
```

- Two or three choices side by side; the chosen one has `.on`.
- Custom properties: `--choice-on`, `--choice-on-text`, `--segmented-bg`.

### Options

```html
<div class="options" role="radiogroup" aria-label="Reading direction">
  <button role="radio" class="on" aria-checked="true">
    <span class="radio"></span><span class="text">Right to left<small>Manga</small></span></button>
  …
</div>
```

- One choice per row, like radio buttons (the sort sheet, reading direction); the chosen one has `.on`.
- Custom property: `--choice-on`.

### Panel

```html
<section class="panel"><h2>Notes</h2>…</section>
```

- A titled card: its `h2` is a small uppercase title, `h3` a subtitle.
- Custom properties: `--panel-bg`, `--panel-border`, `--panel-radius`, `--panel-padding`, `--panel-shadow`.

### Menu

```html
<div class="menu" @click.outside="open = false" @keydown.escape="open = false">
  <button class="icon-btn" aria-label="More" @click="open = !open">…</button>
  <div class="menu-pop" x-show="open" x-transition.opacity x-cloak>
    <button @click="open = false; rescan()"><svg class="ic" aria-hidden="true"><use href="#i-refresh"/></svg>Rescan</button>
    <button class="danger">…Delete</button>
  </div>
</div>
```

- A button that opens a list of actions, from its top right corner. A menu item can have a `<small>` second
  line and `.danger`.
- Its keyboard and screen-reader support (an `x-menu` directive) comes in step 3 of #294 (#297).
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
- Options: `backdrop` (a click on it closes; `true`), `escape` (`true`).
- Event: `kit-close` with `detail.reason` `"backdrop"` or `"escape"`, before it closes; `preventDefault()`
  keeps it open (e.g. to ask about unsaved changes).
- Focus and screen readers (focus moves in and back, the page behind can't be reached) come in step 2 of
  #294 (#296).
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
