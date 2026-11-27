# The UI kit

The pieces MangaVibe and its plugins share: buttons, fields, chips, tabs, menus, dialogs, toasts… Use them in
a plugin's markup and it looks and works like the rest of the app, and it follows the owner's theme and
`custom.css`. Plugin API 31 (issue #294); dialogs for the keyboard and screen readers, API 32; menus (`x-menu`), API 33; tabs and choices (`x-tabs`, `x-choice`), API 34; fields and setting rows (`x-field`), API 35; toasts with an action, tooltips (`x-tooltip`), API 36; the new look (warm darks, the accent's gradient,
Bricolage Grotesque and Plus Jakarta Sans, choices right in a setting row), API 38.

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
once (see [Styling](PLUGINS.md#styling-plugins-and-your-own-customcss)). The ones the pieces use most:

| | |
|---|---|
| `--bg`, `--surface`, `--surface2`, `--surface3` | the page, cards, things on a card, things on those (warm darks; light in the light theme) |
| `--text`, `--muted`, `--faint` | text, quieter text, counts and labels over lists |
| `--border` | hairlines (a see-through warm white in the dark theme) |
| `--accent`, `--accent-2`, `--accent-gradient`, `--accent-soft`, `--accent-text` | the orange, the other end of its gradient (made from `--accent`), the gradient (primary buttons, progress, switches), a soft tint of it, text on it |
| `--radius`, `--radius-panel`, `--radius-cover`, `--radius-pill` | buttons and fields, cards, covers, pills |
| `--shadow-sm`, `--shadow`, `--shadow-lg`, `--shadow-cover` | shadows, and the one under a cover |
| `--font-ui`, `--font-display` | Plus Jakarta Sans for everything, Bricolage Grotesque for titles (`h1`, `h2`, a dialog's title); both are served by MangaVibe (`static/fonts/`), so they work offline |

To change one piece, set its custom properties, on `:root` for the whole app or on a box for the pieces
inside it:

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

An interactive piece (`dialog`, `menu`, `tabs`, `choice`, `field`, `tooltip`) is an object
`{ options, setup(ctx) }`. Its directive (`x-dialog`, `x-menu`, `x-tabs`, `x-choice`, `x-field`,
`x-tooltip`) asks `MangaVibe.kit` for it each time an element starts, so the changes below reach every use.
(`toast` is a piece without a directive: `MangaVibe.toast` calls its `show()`.)

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

- `.btn`: `.small`, `.primary` (the accent's gradient, `--accent-gradient`, with a soft light under it),
  `.danger`, `.on` (switched on), `.busy` (a spinner before the label; disable the button too), `.disabled`
  or `disabled`.
- `.iconbtn`: a square button with an icon, the height of a `.btn`; a `<span class="count">` on it is a
  number in its corner. `.icon-btn`: a small round one. `.link-btn`: looks like a link.
- `.btn-row`: a row of buttons that wraps.
- Give a button that only has an icon an `aria-label` (and a `title`).
- Custom properties: `--btn-bg`, `--btn-border`, `--btn-radius`, `--btn-height` (all buttons), `--btn-primary-bg`
  (a colour or a gradient), `--btn-primary-text`, `--btn-primary-glow` (the light under it: set it with a
  `--btn-primary-bg` of another colour), `--btn-danger`.

### Fields

```html
<div class="field" x-field>
  <label>Year</label> <small class="hint">four digits</small>
  <input x-model="year">
  <p class="error" x-show="bad" x-text="t('A year has four digits')"></p>
</div>

<input type="text" placeholder="Name" aria-label="Name">
<label class="row"><input type="checkbox" class="switch" role="switch"> Show spoilers</label>
```

- Text fields, `select` and `textarea` are styled as they are, no class needed. A checkbox is a rounded box;
  with `.switch` (or right in a `.setting-row`) it's a switch.
- **`.field` with `x-field`**: a field with its name. A `<label>` (the name), an optional `.hint`, the
  control, an optional `.error`. The name and the hint sit on one line, the control under them, so
  fields side by side line up (the Edit dialog's form). `x-field`:
  - ties the name to the control (`for`/`id`), so a click on it focuses the control and a screen reader
    reads it as the control's name;
  - describes the control with the hint and, while it shows, the error (`aria-describedby`), so the hint
    isn't read as part of the name;
  - gives the error `role="alert"`, so it's read out when it appears, and the control
    `aria-invalid="true"` (a red border) while it shows;
  - makes a switch checkbox `role="switch"`;
  - looks again when something inside changes (an error appears, an `x-if` adds the control).
- A field without a `.field` still needs a name: a `<label>` around it or `aria-label`. A form-wide error
  (not one field's): `<p class="err" role="alert">`.
- A plugin can change one part only: the methods are `control(el)`, `name(el)`, `hint(el)`, `error(el)`
  and `wire(el)`.
- Custom properties: `--field-bg`, `--field-border`, `--field-radius`, `--field-height`, `--field-focus`,
  `--check-on`, `--switch-off`, `--switch-on`.

### Setting rows

```html
<section class="settings-card">
  <label x-field class="setting-row">
    <span class="label"><span>Find metadata automatically</span><small>For new series after each scan</small></span>
    <input type="checkbox">
  </label>
  <div x-field class="setting-row">
    <div class="label"><span>Scan for new files</span><small>Runs by itself every 6 hours.</small></div>
    <div class="row-buttons"><button class="btn small">Rescan all</button></div>
  </div>
</section>
```

- A setting per row: a `.label` with its name (**in an element of its own**, a `<span>`) and an optional
  `<small>` hint, then its control: a checkbox (drawn as a switch), a select, a `.segmented` with
  `x-choice` (two or three short choices right in the row, beside the name when there's room and under it
  on a phone), a field, or buttons in a `.row-buttons`. `.sub`: a follow-up to the row above, indented.
- A row with a `.segmented` is a `<div>`, not a `<label>`: a click on a label presses the first button in it.
  On/off is a switch, two or three choices a `.segmented`, a longer list a select.
- Settings' own rows come from `SETTING_ROWS` (app.js) through the `setting_rows` macro: `check: true` for a
  switch, `inline: true` for a `.segmented`, else a select. `group: "Title"` starts a titled card, drawn by
  the `setting_groups` macro in a `<section class="settings-groups">` (a page of cards, not one card).
- `x-field` as for Fields: the control is named by the name alone and described by the hint, and the
  checkbox is a switch for screen readers.
- `.settings-card`: the card they sit in. Settings' pages (`section` in the settings content, a plugin's
  page included) are cards by themselves.
- The dividers go **between** rows: a line on top of each row that has a shown row before it. Never a
  `border-bottom` taken off the last row with `:last-child` (it came back whenever something followed the
  last row, #126, #245); `tests/test_web_parts.py` checks.
- Plugins' settings (`settings` on a plugin) are drawn as setting rows on the plugin's page.
- A folder card's options (`.lib-option` with a `.lib-option-text`, the `folder-row` slot) are rows of the
  same shape: give them `x-field` too.
- Custom properties: `--card-bg`, `--card-border`, `--card-radius`, `--card-padding`, `--row-height`,
  `--row-divider`.

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

- Two or three choices side by side; the chosen one has `.on` (a raised tile), and your `@click` picks.
  `.segmented.accent`: the chosen one in the accent's gradient. In a setting row: see Setting rows.
- `x-choice` makes it a radio group for screen readers (`role="radiogroup"`, `role="radio"` with
  `aria-checked`; give the group an `aria-label`), with one stop for Tab and the arrow keys (all four, round,
  plus Home and End) moving and picking. Option `manual`, `data-kit-skip`, the methods: as for tabs.
- Custom properties: `--choice-on`, `--choice-on-text`, `--segmented-bg`, `--segmented-min` (a button's
  least width).

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

```js
MangaVibe.toast(t("Saved"));
MangaVibe.toast(e.message, 6000, "error");
MangaVibe.toast(t("Marked 3 series as read"), 3000, "", { label: t("Undo"), run: () => undo() });
```

- `MangaVibe.toast(text, ms, kind, action)`: a short message at the bottom; `kind = "error"` for a red one.
- **Read out**: the text also goes to a hidden live region, `role="status"` (read when the screen reader is
  free) or, for an error, `role="alert"` (read at once). The same message twice is read twice.
- **An action** (`{ label, run }`, Plugin API 36) adds a button, e.g. Undo. The toast then stays at least 6
  seconds, and it waits while the mouse or the focus is on it.
- The piece is `toast` in `MangaVibe.kit`: `show()`, `say()`, `hide()`, `pause()` and `resume()`, and
  `options.ms` (3000) and `options.actionMs` (6000), so a plugin can change how long toasts stay.
- Custom properties: `--toast-bg`, `--toast-text` (the tooltip uses them too).

### Progress

```html
<b id="matching">Matching metadata: 4 of 10 series</b>
<div class="progress" role="progressbar" aria-labelledby="matching" aria-valuenow="4" aria-valuemin="0"
     aria-valuemax="10" aria-valuetext="40%"><i style="width: 40%"></i></div>
```

- A bar: its `<i>` is the filled part. Give it a name (`aria-labelledby` or `aria-label`) and its value
  (`aria-valuenow`, and `aria-valuetext` to say it the way it shows).
- Without `aria-valuenow` it's busy with no known end: a piece of the bar slides across (slowly with
  "reduce motion").
- A bar that only repeats what the text next to it says (a card's reading progress) is `aria-hidden`.
- Custom properties: `--progress-color`, `--progress-track`.

### Badges

```html
<span class="count">12</span>
<span class="badge done" role="img" aria-label="Completed" title="Completed"><svg class="ic" aria-hidden="true">…</svg></span>
<span class="dot" role="img" aria-label="needs attention"></span>
```

- `.count`: a number in a tab or on an icon button; a series card's `.badge`s sit in the poster's corner.
- A badge that's only an icon, a number or a dot needs words for screen readers: `role="img"` with an
  `aria-label` (an `aria-label` on a plain `<span>` isn't read). A number that's already in the button's
  name (the filter count) is `aria-hidden`.

### Empty

```html
<div class="empty">
  <svg class="ic" aria-hidden="true"><use href="#i-books"/></svg>
  <span>Nothing in progress. Start a series and it shows up here.</span>
  <a class="btn small primary" href="#/library">Open the Library</a>
</div>
```

- Nothing to show: a quiet icon, a sentence that says why, and what to do about it (a button), the same on
  every page (Home, the library, Continue reading, Needs attention, the saved searches).
- Custom properties: `--empty-gap`, `--empty-padding` (tighter in a sheet).

### Loading

```html
<div class="loading-line">Loading…</div>

<div class="grid">
  <template x-for="i in (items === null ? 12 : 0)" :key="i">
    <div class="skeleton-card" aria-hidden="true"><div class="poster-shape"></div><div class="line"></div><div class="line"></div></div>
  </template>
</div>
<p class="sr-only" role="status" x-show="items === null">Loading…</p>
```

- `.loading-line`: “Loading…” with a spinner before it (more cards at the end of a list).
- `.skeleton-card`: a grey card (a poster and two lines) where cards are on their way, in the grid they'll
  fill; `.skeleton` for any other shape. The library, Continue reading and Home show them on their first
  load. They're `aria-hidden`: say “Loading…” in a `.sr-only` `role="status"` instead.
- `.sr-only`: there for screen readers, not on the screen.
- Custom property: `--skeleton`.

### Tooltip

```html
<button class="iconbtn" x-tooltip="t('Add to favorites')"><svg class="ic" aria-hidden="true">…</svg></button>
<button class="icon-btn" aria-label="More" title="More" x-tooltip>…</button>
```

- `x-tooltip="text"`: a small bubble above the button while the mouse is on it or it has the keyboard's
  focus (not after a tap), placed below it when there's no room above. Escape hides it.
- It's the button's name when it has none (an icon-only button: `aria-label`), so you write the words once.
- Plain `x-tooltip` takes the button's `title` (and removes it, so the browser's own tooltip doesn't show
  too).
- The piece is `tooltip` in `MangaVibe.kit`: `bubble()`, `place()`, and `options.delay` (400 ms).

## Working on the kit

- The CSS is in `app/web/static/css/kit/`, one file per piece, imported by `main.css` in the `kit` layer
  (after `base`, before `layout` and the pages). The behaviour is in `app/web/static/js/kit.js`.
- A new piece gets: its CSS file (custom properties that fall back to the tokens), a `### Name` section
  here, and a `data-piece="name"` section on the kit page (`app/web/kit/kit.html`).
  `tests/test_ui_kit.py` checks the last two match.
- Settings cards keep no divider under their last row: dividers go *between* rows (see `CLAUDE.md`).
- The built-in plugins use the kit as any plugin would: their CSS changes a kit piece only through its
  custom properties (a rule on `.btn`, `.chip`, `.modal`… may set `--btn-bg` but not `background`), and
  their icons come from the sprite. `tests/test_ui_kit.py` checks both. The reader's own controls (the bar,
  the options drawer and its panels over the page) are a design of their own, not copies of kit pieces.
