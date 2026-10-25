# Writing MangaVibe plugins

A plugin is a Python file (or a folder with `__init__.py`) in the `plugins` folder inside
your data folder (`docker/mangavibe/plugins` on the NAS). After copying it there, press
**Settings → Reload plugins** (or restart the container). Each plugin can be switched off in Settings.

A plugin that fails to load, or crashes in a hook, is logged and skipped. It never stops the app.

**Plugins never write to the manga folder.** It is mounted read-only. Everything a plugin
produces goes into MangaVibe's database.

## Minimal plugin

```python
from app.plugins import Plugin

class Hello(Plugin):
    id = "hello"            # unique
    name = "Hello"
    version = "1.0"
    description = "Logs every new book."

    def on_book_scanned(self, ctx, book, parsed):
        ctx.log.info("New book: %s", book.file_name)
```

## What a plugin can do

| Feature | How |
|---|---|
| **Extra fields** shown on the series page | `fields = [FieldDef("key", "Label")]`, then `ctx.set_field(series, "key", value)` (any JSON value; `None` removes it) |
| **Fix ordering / parsing** | `on_book_scanned(ctx, book, parsed)`: change `parsed.volume`, `.chapter`, `.chapter_end`, `.extra`, `.extra_index`, `.version` |
| **Do something after a series scan** | `on_series_scanned(ctx, series, books)` |
| **Buttons on the series page** | `actions = [Action("key", "Label", confirm=None)]` and `run_action(ctx, key, series)` returning a message |
| **Dropdowns on the series page** | `choices = [Choice("key", "Label", options=[("value", "Label"), ...])]`, `get_choice(ctx, key, series)` and `set_choice(ctx, key, series, value)` (see [Dropdowns](#dropdowns)) |
| **Change how the app looks** | `css = """ :root { --accent: #ff6fa8; } """` (loaded after the built-in styles, so it wins) |
| **Metadata provider** | `is_provider = True`, implement `search(ctx, query, year)` → `list[Candidate]` and `fetch(ctx, provider_id)` → dict of series fields. Optional `url = "https://..."` links the site on the Metadata settings page |
| **React to reading** | `on_book_started(ctx, book, series)`, `on_book_completed(ctx, book, series)`, `on_series_status_changed(ctx, series, old, new)` (see [Reading events](#reading-events)) |
| **Settings boxes** on the plugin's page (Settings → Plugins → Settings) | `settings_fields = [SettingDef("client_id", "Client ID", help="...", secret=False)]`; the value is in `ctx.settings["client_id"]`. `secret=True` values are never sent back to the browser. With `options=[("auto", "Automatic"), ("manual", "Manual")]` and `default="auto"` it's a dropdown instead; `ctx.setting("key")` gives the saved value or the default |
| **Buttons on the plugin's page** (Settings → Plugins → Settings) | `global_actions = [Action("sync", "Sync all")]` and `run_global_action(ctx, key)` returning a message |
| **Status line** under the plugin in Settings | `status(ctx)` returning a short text, e.g. `"Connected as Ivo"` |
| **Open a page** (e.g. a login) from a button | return `OpenUrl(url, message="")` from `run_action` or `run_global_action` |
| **A web address of its own** (e.g. an OAuth callback) | `handle_web(ctx, path, params)` answers `GET /api/plugins/<id>/web/<path>`. Return a message (shown on a small page with a link back) or `OpenUrl(url)` to redirect; `None` = 404 |
| **Updates** from its GitHub releases | `repo = "owner/name"` (see [Updates](#updates)) |
| **Say what it uses** | `permissions = ["Connects to api.myanimelist.net"]`: shown in Settings; an update that adds one asks first (see [Updates](#updates)) |
| **Draw over reader pages** (e.g. a translation) | `overlay_label = "Translation"` and `page_overlay(ctx, book, page)` returning `Overlay(boxes=[Box(...)])` (see [Drawing on pages](#drawing-on-pages)) |
| **Have a page read aloud** (e.g. a description for blind readers) | `Overlay(speech="...")` from `page_overlay` (see [Drawing on pages](#drawing-on-pages)) |
| **Save something per page** | `ctx.set_page_data(book, page, value)`, `ctx.get_page_data(book, page)`, `ctx.page_data(book)` |
| **Keep something per series** for the plugin itself (e.g. notes on the characters) | `ctx.set_series_data(series, key, value)`, `ctx.get_series_data(series, key)`; never shown in the app |
| **Labels on books** in the series list | `book_badges(ctx, books)` returning `{book_id: "Translated"}` |
| **Buttons for the open book** (in the plugin's panel in the reader's options) | `book_actions = [Action("all", "Translate this chapter")]` and `run_book_action(ctx, key, book)` |
| **Work for hours** without holding up other plugins | `ctx.run_in_thread(fn)`; the loop returns when `ctx.should_stop()` |

## The `ctx` object

| | |
|---|---|
| `ctx.log` | logger (messages appear in the container log) |
| `ctx.settings` / `ctx.save_settings(dict)` | the plugin's own saved settings |
| `ctx.setting(key)` | one setting: the saved value, or the `SettingDef`'s `default` while nothing is saved |
| `ctx.set_field(series, key, value)` / `ctx.get_field(series, key)` | extra fields |
| `ctx.list_pages(book)` | page names inside a book |
| `ctx.list_entries(book)` | all names inside a zip/cbz (not only images) |
| `ctx.read_entry(book, name)` | bytes of one entry, e.g. `"ComicInfo.xml"` |
| `ctx.read_series_file(series, name)` | bytes of a sidecar file in the series folder (e.g. `series.json`), or `None` |
| `ctx.db` | the database session (advanced; MangaVibe commits after hooks) |
| `ctx.reading_direction(series)` | `paged` or `vertical` (webtoons): the way the reader shows the series. Paged always turns forward to the right (there is no right to left). Set on the series, else its library folder, else Settings → Reader. Plugin API 5 |
| `ctx.reading_status(series)` | the status the app shows: `unread`, `reading`, `completed`, `plan_to_read`, `on_hold`, `dropped` |
| `ctx.set_reading_status(series, status)` | set it by hand (`None` = work it out from progress again). Does **not** fire `on_series_status_changed`, so a sync can't loop |
| `ctx.http` | the app's HTTP client: `ctx.http.request(method, url, headers=..., data=...)`, `get_json`, `post_json`. Throttled per site, retries, raises `ctx.http.ProviderError` (its `status` is the HTTP code, e.g. `404`, or `None` if the site couldn't be reached) |
| `ctx.web_url(path)` | full address of this plugin's `handle_web` page, as the browser reaches the app (e.g. `http://192.168.1.5:8080/api/plugins/mal/web/callback`) |
| `ctx.run_in_background(fn, *args)` | run `fn(ctx, *args)` later with its own database session. Pass ids, not rows. Use it for long work started from a button |
| `ctx.run_in_thread(fn, *args)` | run `fn(ctx, *args)` now, in a thread of the plugin's own (returns it). For a worker that runs for a long time; it must return once `ctx.should_stop()` is `True` |
| `ctx.should_stop()` | `True` once the plugin was reloaded, updated or switched off |
| `ctx.read_page(book, n, max_width=None)` | page `n` (0-based) as image bytes, for every format (PDF and EPUB pages come rendered). `max_width` shrinks larger pages to WebP |
| `ctx.set_page_data(book, n, value)` / `ctx.get_page_data(book, n)` | the plugin's own value for one page (any JSON value; `None` removes it). Removed with the book, and when its file changes |
| `ctx.page_data(book)` / `ctx.page_data_counts(books)` / `ctx.clear_page_data(book=None)` | all saved pages of a book `{page: value}`; how many pages have a value per book `{book_id: n}`; forget one book or all |
| `ctx.set_series_data(series, key, value)` / `ctx.get_series_data(series, key)` | the plugin's own value under `key` for a series (a `Series` or its id, e.g. `book.series_id`; any JSON value; `None` removes it). Unlike `set_field()`, nothing is shown. Removed with the series |
| `ctx.series_data(series)` / `ctx.clear_series_data(series, key=None)` | all saved values of a series `{key: value}`; forget one key or all |

`book` and `series` are database rows; see `app/models.py` for their fields. A series' online ids
are on it too, e.g. `series.mal_id`, `series.anilist_id`, and `series.publication_status`
(`FINISHED`, `RELEASING`, ...).

## Reading events

| Hook | When |
|---|---|
| `on_book_started(ctx, book, series)` | a book is opened for the first time (or again after marking it unread) |
| `on_book_completed(ctx, book, series)` | a book becomes read: last page reached, or marked read |
| `on_series_status_changed(ctx, series, old, new)` | the series' reading status changes, e.g. `unread` → `reading`, `reading` → `completed`, or the owner picks one by hand |

They run **in the background, after the change is saved**, one at a time and in order, so a slow
website never slows the reader. A crash is logged and the next event still runs. Only the owner's
own changes fire them (reading, the Read/Unread buttons, picking a status), not scans and not
changes made by plugins.

A tracker sync looks like this:

```python
from app.plugins import Action, OpenUrl, Plugin, SettingDef

class Tracker(Plugin):
    id = "tracker"
    name = "My tracker"
    settings_fields = [SettingDef("client_id", "Client ID")]
    global_actions = [Action("connect", "Connect"), Action("sync", "Sync all")]
    actions = [Action("sync", "Sync this series")]

    def on_series_status_changed(self, ctx, series, old, new):
        if new == "completed" and series.publication_status == "FINISHED":
            ...  # tell the website

    def run_global_action(self, ctx, key):
        if key == "connect":
            return OpenUrl("https://tracker.example/login?redirect=" + ctx.web_url("callback"))
        ctx.run_in_background(self.pull_list)  # may take a while
        return "Sync started"

    def handle_web(self, ctx, path, params):
        if path == "callback":
            ...  # swap params["code"] for a token, ctx.save_settings(...)
            return "Connected. You can close this page."

    def status(self, ctx):
        return "Connected" if ctx.settings.get("token") else "Not connected"
```

## Drawing on pages

In the reader's options each plugin is one row under **Plugins**; it opens a panel with everything the
plugin brings (its switch, its book buttons, Read aloud and the look of its boxes), so the options stay
short however many plugins there are.
A plugin with `overlay_label` gets a switch in that panel (**Translation: On/Off**),
remembered per series on each device. While it's on, the reader asks `page_overlay` for the page
you're on and the next one, and draws the boxes it returns over the page. They move with the page
when you zoom, pan or swipe, in paged and vertical mode.

```python
from app.plugins import Box, Overlay, Plugin

class Notes(Plugin):
    id = "notes"
    name = "Notes"
    overlay_label = "Notes"
    css = ".ov-box.note { --ov-bg: #ffe680; }"   # boxes are white with black text unless styled

    def page_overlay(self, ctx, book, page):
        text = ctx.get_page_data(book, page)
        if text is None:
            return None                                       # nothing on this page
        return Overlay(boxes=[Box(0.1, 0.05, 0.4, 0.08, text=text, title="My note", cls="note")])
```

- **Positions are fractions of the page**: `Box(x, y, w, h)` from 0 to 1, top-left first. They fit
  any page size and zoom. The text is sized to fill its box. `title` shows when the mouse is over the
  box (e.g. the original text), `cls` adds CSS classes for the plugin's `css`. Every box also has the
  classes `ov-box` and `ov-<plugin id>`.
- **The reader chooses how see-through boxes are, and a white outline around the text** (Settings →
  Reader, and in the reader options while an overlay is on). To keep these working, give a box its
  colour with `--ov-bg` rather than `background`, and change the outline colour with `--ov-outline`
  if your text is light (e.g. `--ov-bg: #000; --ov-outline: #000; color: #fff`). A plugin that sets
  `--ov-bg-opacity` or `--ov-text-stroke` on its boxes overrides the reader's choice.
- **The owner can hide single boxes**: with the reader's controls showing, each box has a ×. MangaVibe
  remembers it by the box's position (`x,y` to 3 decimals) and leaves it out, so the plugin needs to
  do nothing. A box that comes back at the same place after the plugin redid the page stays hidden.
  *Show hidden boxes* in the reader options brings a book's boxes back.
- **Keep `page_overlay` fast.** It runs as pages are shown. Read what you saved; do slow work in a
  thread and answer `Overlay(pending=True, message="Translating…")`. The reader shows the message at
  the top and asks again every few seconds while that page is still being read.
  `Overlay(message="PC offline")` (without `pending`) shows a status line without asking again.
- **A quiet line about the page:** `Overlay(boxes=..., note="Full context · Haruka, Taro")` shows the
  note in the reader's top bar, under the chapter's name, only while the controls are open. Unlike
  `message`, it never covers the page. Added in plugin API 4 (with series data): a plugin that should
  also run on older MangaVibe can check `"note" in Overlay.__dataclass_fields__` and
  `hasattr(ctx, "set_series_data")` first.
- **Text to listen to:** `Overlay(boxes=..., speech="Page 5.
Panel 1. Two boys in the rain.
Haruka says: Hey!")`
  is the page as plain text in reading order. The reader's **Read aloud** (in the plugin's panel in the
  reader options) reads it with the device's voice, and screen readers (TalkBack, NVDA,
  VoiceOver) get it too. One line per sentence or speaker reads best. Without `speech`, read aloud reads
  the text of the boxes in their order. Added in plugin API 5: check `"speech" in
  Overlay.__dataclass_fields__` to also run on older MangaVibe.
- **A worker of your own:** start it with `ctx.run_in_thread(self.work)` the first time there's
  something to do. It gets its own database session; call `ctx.db.commit()` after each page so the
  reader can show it. Loop until `ctx.should_stop()`, which turns `True` when the plugin is reloaded,
  updated or switched off (then a new plugin object starts its own thread when needed).
- **Pages:** `ctx.read_page(book, n, max_width=1600)` gives the image; `ctx.set_page_data()` keeps the
  result. Saved pages are deleted when the book's file changes on disk, so they never belong to an
  older version of the file.
- **Show progress** with `book_badges` (a label per book in the series list, e.g. `"12/40"`; use
  `ctx.page_data_counts(books)`), and offer `book_actions` such as "Translate this chapter" in the
  reader. Series-wide buttons (`actions`) stay on the series page.
- **Progress shows live:** while the series page is open it asks for `book_badges` again every few
  seconds, and Settings does the same for `status()` while a plugins page is open (both slow down
  after a minute without a change). So a label like "Translated 12/40" or "Queue: 30 pages" counts
  up on its own; keep both quick (no website calls).

`examples/plugins/page_info.py` uses all of it: a queue, its own thread, the next pages prepared
ahead, badges and book buttons.

## Dropdowns

A dropdown sits under the reading status on the series page, e.g. the series' status on a tracker
site, which the owner can change from there:

```python
from app.plugins import Choice, FieldDef, Plugin

class Tracker(Plugin):
    id = "tracker"
    fields = [FieldDef("site_status", "Tracker")]
    choices = [Choice("site_status", "Tracker", options=[
        ("reading", "Reading"), ("completed", "Completed"), ("", "Not on my list")])]

    def get_choice(self, ctx, key, series):
        if not series.mal_id:
            return False                      # no dropdown for this series
        return ctx.get_field(series, key)     # None = not known yet

    def set_choice(self, ctx, key, series, value):
        ...  # tell the website
        ctx.set_field(series, key, value)
        return "Saved on the tracker"
```

- `get_choice` returns the selected option value, `None` when it isn't known yet (the dropdown says
  "unknown"), or `False` to hide the dropdown for that series. It runs every time the series page
  opens, so read what you saved instead of asking a website. Without it, the value is the plugin
  field with the same key.
- `set_choice` runs when the owner picks an option; `value` is always one of the option values.
  Return a message (shown as a toast) or `OpenUrl`. The page reloads afterwards, so a changed
  reading status shows at once.
- While the dropdown shows, the plugin field with the same key is left out of the series details,
  so the value isn't shown twice.

## Updates

A plugin that names its GitHub repo gets an **Updates** part on its page in Settings → Plugins:

```python
class Tracker(Plugin):
    id = "tracker"
    version = "1.1.0"
    repo = "your-name/mangavibe-tracker"
```

- **Where updates come from:** the repo's newest **published release** (drafts and pre-releases are
  left out). MangaVibe downloads the file with the same name as the installed one (e.g.
  `tracker.py`) at the release's tag. The tag is the version (`v1.2.0` or `1.2.0`) and is compared
  with `version`, so keep the two the same.
- **To publish an update:** raise `version` in the file, merge it, then create a release with the
  tag `v<version>`. Nothing reaches users before the release.
- **Always the owner's choice:** MangaVibe checks once a day (or on **Check for updates**) and shows
  the new version with **Update to vX** and **Skip this version**. It never installs by itself. A
  skipped version isn't offered again; a newer one is. The daily check follows Settings → Updates →
  *Check for updates every day*, and `AUTO_UPDATE=0` turns it off.
- **New permissions are asked for:** list what the plugin uses in `permissions`, e.g.
  `permissions = ["Connects to api.myanimelist.net", "Reads book files"]`. They're shown in
  Settings → Plugins. When a new version has an entry the installed one doesn't, **Update** lists
  those entries and installs only after the owner agrees. Entries are compared as text, so rewording
  one counts as new. They aren't enforced (a plugin is Python and can do anything the app can); they
  tell the owner what changed. Built-in plugins aren't updated this way (they come with the app),
  but they list their permissions too, and a test checks that each one does.
- **Safe swap:** the new file must load on its own and define a plugin with the same `id`, or
  nothing changes. The old file is kept in `plugins/.backup/`.
- Only single-file plugins in the `plugins` folder can be updated. A plugin's settings and data are
  kept, since they're in the database, not in the file.

## Styling: plugins and your own custom.css

All built-in CSS is in cascade layers (`app/web/static/css/`, one file per feature). Plugin CSS and
your own `docker/mangavibe/custom.css` are loaded **after** it and **outside** the layers, so they
always win, without `!important`. The order is: built-in → plugins → custom.css.

The easiest way to restyle is to change the design tokens from `css/tokens.css`
(`--accent`, `--card`, `--poster-width`, `--radius`, colours…). See `examples/custom.css` and
`examples/plugins/sakura_theme.py`. `custom.css` is created with instructions on first start;
edit it with File Station and reload the page.

## Example

`examples/plugins/sakura_theme.py` only changes the look (pink accent, rounder corners).

`examples/plugins/reading_log.py` uses the reading events, a settings box, Settings buttons, a status
line and its own web page.

`examples/plugins/translation_groups.py` uses fields, scan hooks, series buttons and dropdowns:
it shows scanlation groups from `[Group]` tags, lets a `series.json` file fix chapter numbers,
and adds a "Find missing chapters" button.

`examples/plugins/page_info.py` draws over reader pages: page overlays, per-page data, its own worker
thread, book badges and book buttons. A translator plugin is built the same way.
