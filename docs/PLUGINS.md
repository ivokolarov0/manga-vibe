# Writing MangaVibe plugins

A plugin is a Python file (or a folder with `__init__.py`) in the `plugins` folder inside
your data folder (`docker/mangavibe/plugins` on the NAS). After copying it there, press
**Settings → Reload plugins** (or restart the container). Each plugin can be switched off in Settings.

A plugin that fails to load, or crashes in a hook, is logged and skipped. It never stops the app.

**Plugins don't write to the manga folder.** Everything a plugin produces goes into MangaVibe's
database. Only two things in the app change the owner's files, and only when the owner asks:
**Delete series** (plugins hear about it through `on_series_deleted`) and the built-in **Optimize**
plugin, which replaces books with smaller ones. Optimize keeps the book's row, so a plugin's data
for the book and its pages stays (the pages are the same pages, in the same order).

## Minimal plugin

```python
from app.plugins import Plugin

class Hello(Plugin):
    id = "hello"            # unique
    name = "Hello"
    version = "1.0"
    description = "Logs every new book."
    category = "library"    # where Settings → Plugins lists it (see Categories)

    def on_book_scanned(self, ctx, book, parsed):
        ctx.log.info("New book: %s", book.file_name)
```

## What a plugin can do

| Feature | How |
|---|---|
| **Extra fields** shown on the series page | `fields = [FieldDef("key", "Label")]`, then `ctx.set_field(series, "key", value)` (any JSON value; `None` removes it) |
| **Fix ordering / parsing** | `on_book_scanned(ctx, book, parsed)`: change `parsed.volume`, `.chapter`, `.chapter_end`, `.extra`, `.extra_index`, `.version` |
| **Do something after a series scan** | `on_series_scanned(ctx, series, books)` |
| **Clean up when the owner deletes a series** | `on_series_deleted(ctx, series)`: called just before the series and its folder are deleted, so it and your data for it can still be read. Plugin API 6 |
| **Buttons on the series page** | `actions = [Action("key", "Label", confirm=None)]` and `run_action(ctx, key, series)` returning a message |
| **Dropdowns on the series page** | `choices = [Choice("key", "Label", options=[("value", "Label"), ...])]`, `get_choice(ctx, key, series)` and `set_choice(ctx, key, series, value)` (see [Dropdowns](#dropdowns)) |
| **Change how the app looks** | `css = """ :root { --accent: #ff6fa8; } """` (loaded after the built-in styles, so it wins) |
| **Metadata provider** | `is_provider = True`, implement `search(ctx, query, year)` → `list[Candidate]` and `fetch(ctx, provider_id)` → dict of series fields. Optional `url = "https://..."` links the site on the Metadata settings page |
| **React to reading** | `on_book_started(ctx, book, series)`, `on_book_completed(ctx, book, series)`, `on_series_status_changed(ctx, series, old, new)` (see [Reading events](#reading-events)) |
| **Settings boxes** on the plugin's page (Settings → Plugins → the plugin) | `settings_fields = [SettingDef("client_id", "Client ID", help="...", secret=False)]`; the value is in `ctx.settings["client_id"]`. `secret=True` values are never sent back to the browser. With `options=[("auto", "Automatic"), ("manual", "Manual")]` and `default="auto"` it's a dropdown instead; `ctx.setting("key")` gives the saved value or the default |
| **Buttons on the plugin's page** (Settings → Plugins → the plugin) | `global_actions = [Action("sync", "Sync all")]` and `run_global_action(ctx, key)` returning a message |
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
| **A tool of its own on the page being read** (e.g. an editor for its boxes) | a `book_actions` button that the plugin's `js` handles: `MangaVibe.on(id, "book-action", fn)`, `MangaVibe.reader.where()` and `.redraw()` (see [Drawing on pages](#drawing-on-pages)). Plugin API 12 |
| **Work for hours** without holding up other plugins | `ctx.run_in_thread(fn)`; the loop returns when `ctx.should_stop()` |
| **An API of its own** | `router = APIRouter()` (FastAPI), served at `/api/plugins/<id>/api/...` (see [Your own API and UI](#your-own-api-and-ui)). Plugin API 7 |
| **Its own buttons, sheets and scripts in the app** | `html = {"library-tools": "<button>…</button>"}` and `js = "…"` (see [Your own API and UI](#your-own-api-and-ui)). Plugin API 7 |
| **Decide what the lists show** (e.g. libraries, accounts) | `series_filter(ctx, request)`, `middleware`, `reading_mode()`, `series_info()`, `on_start()`, `can_switch_off()` (see [Your own API and UI](#your-own-api-and-ui)). Plugin API 8 |
| **Know who is asking** (accounts) | `users.current()`, `users.manage`, `users.personal_key()`, `enabled_by_default` (see [Your own API and UI](#your-own-api-and-ui)). Plugin API 9 |
| **Be listed under the right category** in Settings → Plugins | `category = "reader"`, or several: `category = ["reader", "library"]` (see [Categories](#categories)). Plugin API 10 |
| **Tools for assistants and voice control** (MCP) | `mcp_tools = [McpTool(...)]` and `run_mcp_tool(ctx, name, args)` on the server, `MangaVibe.screenCommand()` in the page (see [Assistants and voice control](#assistants-and-voice-control)). Plugin API 13 |
| **A reader of its own** for books the page reader can't show (light novels) | `reads = ["epub-text"]`, `ctx.text_sections(book)` on the server, `MangaVibe.registerReader()` in the page (see [A reader of its own](#a-reader-of-its-own)). Plugin API 14 |

## Categories

Settings → Plugins groups plugins by category, so the owner can find them and see what each one
changes. Set `category` to one of these:

| Category | For plugins that | Examples |
|---|---|---|
| `metadata` | find titles, descriptions, covers (`is_provider = True`) | AniList, MangaUpdates |
| `reader` | change the reader: page overlays, `book_actions`, page tools | `examples/plugins/page_info.py`, Manga Translate |
| `library` | change lists, series pages and books: fields, actions, libraries, searches | Libraries, Saved searches, Optimize, `translation_groups.py` |
| `system` | handle users, access, middleware, maintenance | Accounts |
| `appearance` | only change how the app looks (`css`) | `examples/plugins/sakura_theme.py` |
| `other` | don't fit any of the above | |

A plugin that spans areas lists several: `category = ["reader", "library"]`. It's listed under the
first one and tagged with the others. Unknown names are left out (and logged).

**Left empty**, the category is worked out from what the plugin uses: `is_provider` → `metadata`,
`overlay_label` or `book_actions` → `reader`, only `css` (nothing else, no hooks) → `appearance`,
anything else → `other`. So older plugins are grouped without changes, but setting it is clearer.

`GET /api/plugins` sends each plugin's `category` (the main one), `categories` (all of them, main
first) and `builtin` (true for the plugins in `app/plugins/builtin/`, false for the ones the owner
installed), plus `categories` at the top: every category key with its label, in the order shown.

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
| `ctx.reading_direction(series)` | `paged` or `vertical` (webtoons): the way the reader shows the series. Paged always turns forward to the right (there is no right to left). Set on the series, else a plugin's default (e.g. its library's, Settings → Libraries), else Settings → Reader. Plugin API 5 |
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
- **A button that opens something in the page** (an editor for the page's boxes, a dictionary…): list
  it in `book_actions` like any other, and handle it in the plugin's `js`. The reader asks the
  script first; when the handler returns `true`, `run_book_action` isn't called:

  ```js
  MangaVibe.on("notes", "book-action", (key, at) => {
    if (key !== "edit") return;          // other buttons go to run_book_action() as usual
    openMyEditor(at.book, at.page);      // at: { book, series, page, pages }; page counts from 0
    return true;
  });
  // later, once the editor saved what page_overlay() gives:
  MangaVibe.reader.redraw();
  ```

  `MangaVibe.reader.where()` gives the same `{ book, series, page, pages }` at any time (`null`
  while no book is open). The button is only there while the plugin is switched on, like all of a
  plugin's buttons; `run_book_action` still gets the key if the script didn't take it (return a
  message such as "Reload the page"). Plugin API 12: on older MangaVibe there is no
  `MangaVibe.reader`, and the button always goes to `run_book_action`.
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
  left out), at the release's tag. The tag is the version (`v1.2.0` or `1.2.0`) and is compared
  with `version`, so keep the two the same.
  - A single-file plugin: the file with the same name as the installed one (e.g. `tracker.py`).
  - A folder plugin (`plugins/tracker/__init__.py`): the folder with the same name in the
    release's source zip (e.g. `tracker/` in the repo), or the whole repo when its `__init__.py` is
    at the top. The folder is replaced as a whole; dot-files (`.github`, `.gitignore`) are left out.
    No release files need to be attached: GitHub's source zip is used.
- **Private repos:** the owner adds a GitHub token in **Settings → Plugins → GitHub token for
  private plugins** (or sets `GITHUB_TOKEN` on the container). A fine-grained token with read-only
  *Contents* access to the repo is enough. It is only sent to GitHub, and never shown again.
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
- **Safe swap:** the new version must load on its own and define a plugin with the same `id`, or
  nothing changes. The old file or folder is kept in `plugins/.backup/`.
- Only plugins in the `plugins` folder can be updated, not built-in ones, and not a folder that is
  a link to somewhere else (a checkout you update yourself). A plugin's settings and data are kept,
  since they're in the database, not in its files.
- **Reload plugins** loads a folder plugin's own modules (`core.py`, `api.py`, ...) fresh too, so
  copying a new version in and reloading is enough.

## Publish your plugins in a source

A source lists plugins so owners can install them from **Settings → Plugins → Get plugins**. It's a
JSON file anywhere on the web; on GitHub, put it at the top of a repo as `plugins.json` and owners add
it as `owner/name`. MangaVibe's own is [plugins.json](../plugins.json) in this repo.

```json
{
  "name": "My plugins",
  "plugins": [
    {"id": "tracker", "name": "Tracker", "category": "library",
     "description": "Sends what you read to Tracker.",
     "repo": "your-name/mangavibe-tracker", "file": "tracker.py"},
    {"id": "remote", "name": "Remote", "category": "system",
     "repo": "your-name/mangavibe-remote", "folder": "remote"}
  ]
}
```

- `id`: the plugin's `id`. The download has to define a plugin with it, or nothing is installed.
- `repo`: the GitHub repo it's installed from, at its newest **published release** (like
  [updates](#updates)). Give the plugin the same `repo`, so it's updated from there afterwards.
- `file` for a single-file plugin (that file at the release's tag), or `folder` for a folder plugin
  (that folder in the release's source zip, or the whole repo when its `__init__.py` is at the top).
  It's also the name it gets in `plugins/`.
- `name`, `description`, `category` (one of [the categories](#categories)) and `homepage` (the repo's
  page if left out) are what **Get plugins** shows.

An entry missing `id`, `repo` or one of `file` / `folder` is left out. Install asks for every
permission the plugin lists, and never replaces a file or folder that's already in `plugins/`.
Write `id` and `permissions` as plain values in the class (`permissions = ["…", "…"]`): Install
reads them from the file before running any of it. When they're worked out by code, the owner is
first asked whether the plugin may run just to say what it uses.

## Your own API and UI

Plugin API 7 (API and markup) and 8 (the hooks below). Some of the app's own features are plugins built
this way, so you can switch them off in Settings → Plugins or replace them: see
**`app/plugins/builtin/saved_searches/`** (the Saved button) for a small one and
**`app/plugins/builtin/libraries/`** (libraries with passwords) for one that uses everything here. A
plugin folder can use relative imports (`from . import core`).

**API.** Give the plugin a FastAPI router. It answers `/api/plugins/<id>/api/<its paths>`, only while
the plugin is on (otherwise 404). `plugin_ctx` gives your handlers the usual `ctx`; commit what you change.

```python
from fastapi import APIRouter, Depends
from app.plugins import Context, Plugin
from app.plugins.web import plugin_ctx

router = APIRouter()

@router.get("/notes")                      # GET /api/plugins/notes/api/notes
def list_notes(ctx: Context = Depends(plugin_ctx)):
    return ctx.settings.get("notes", [])

class Notes(Plugin):
    id = "notes"
    name = "Notes"
    router = router
```

**Markup.** `html` maps a slot of the page to markup put there. It's [Alpine.js](https://alpinejs.dev)
markup like the rest of the page (`x-show`, `@click`, `$store.app`…).

When several plugins use the same slot, their markup goes in the order the plugins are loaded. To choose
a place, give `(markup, order)` instead of the markup: lower goes first, and markup without an order
counts as 50. E.g. `html = {"topbar": (menu, 90)}` keeps the accounts plugin's menu last, next to
Settings. Plugin API 11.

| Slot | Where |
|---|---|
| `home-top` | above the library grid and Continue reading (the library tabs). Not inside a component: give your markup `x-data` |
| `library-tools` | the library page's buttons, after Filter / Sort / Letter |
| `library-filters` | the row of filters on, after Clear all (the row shows while filters are on) |
| `select-menu` | the ⋯ menu of the library's multi-select. `selCount`, `selMenu` and `bulk(action, msg, extra, url)` are there: `bulk` POSTs `{ ids, action, ...extra }` to your `url` and wants `{ done, skipped }` back |
| `library-end` | the end of the library page, for its dialogs (same names as `select-menu`) |
| `series-edit` | the series Edit dialog's fields, saved with its Save button (events `edit-open`, `edit-changed`, `edit-save`) |
| `settings` | a Settings page of your own: add it with `MangaVibe.settingsPage()` and show it with `x-show="section === '<key>'"` |
| `folder-row` | each folder in Settings → Library folders; `l` is the folder (`l.id`, `l.name`, `l.root_path`) |
| `attention-note` | under the explanation of a Needs attention list |
| `sheets` | the end of the page: sheets and dialogs of your own. Open one with `$store.app.sheet = "<name>"` and show it with `x-show="$store.app.sheet === '<name>'"`. Not inside a component: give it `x-data` |
| `series-actions` | the series page, after the Continue button and the status dropdowns. `s` is the series (`s.id`, `s.title`, `s.plugin_data[<id>]`…). Give a button or dropdown `class="ctl"` to fit the row. Plugin API 11 |
| `series-menu` | the series page's ⋯ menu (admins only), before Delete: `<button @click="menuOpen = false; …">`. `s` is the series. Plugin API 11 |
| `series-panels` | the series page's column of panels (Links, Metadata, Folder…), after Plugin tools: a `<section class="panel"><h2>…</h2>…</section>`. `s` is the series. Plugin API 11 |
| `series-end` | the end of the series page, after the Books panel: a `<section class="panel wide">`, or dialogs. `s` is the series, `books` its books. Plugin API 11 |
| `book-row` | each book in the series page's Books list, before its Read button. `b` is the book (`b.id`, `b.label`, `b.completed`, `b.page`), `s` the series. For a short label, `book_badges()` is simpler. Plugin API 11 |
| `card-badges` | each series card in the library and on Continue reading. `s` is the card's series (`s.id`, `s.title`, `s.reading_status`). A `<span class="badge">` is stacked in the poster's bottom-right corner. Plugin API 11 |
| `continue-top` | the Continue reading page, above its grid. Not inside a component of yours: give it `x-data`. Plugin API 11 |

More slots are added as more of the app becomes plugins.

**Dialogs and sheets.** Give a `.modal` or `.sheet` of your own `x-dialog="<how to close it>"`, e.g.
`<div class="modal" x-show="$store.notes.open" x-dialog="$store.notes.open = false">`. A click on the
backdrop, Escape and the Android app's back button then close it, like the app's own.

**Script.** `js` runs after the app's own scripts: before Alpine starts when the page loads, or later
when the owner switches the plugin on. Register your stores and components in `MangaVibe.onStart(() => {
Alpine.store(…); Alpine.data(…) })`, which works both ways, and use only `window.MangaVibe` from the app
(the rest can change in any version):

| `MangaVibe.` | |
|---|---|
| `onStart(fn)` | run `fn` when Alpine starts, or at once if it already has |
| `api(path, { method, body })` | call the server; the JSON answer, or throws an `Error` with the server's message |
| `toast(text, ms, kind)` | a short message at the bottom; `kind = "error"` for a red one |
| `libraryFilters()` | the library page's query (filters, sort, page) as `URLSearchParams`; reactive in Alpine |
| `filterQuery(params)` | just its filters and sort, in a fixed order (`""` when none are on), to compare two |
| `filterLabels(query)` | short labels for them: `["Isekai", "not Horror", "Reading"]` |
| `showLibrary(params)` | show the library page with these filters |
| `on(pluginId, event, fn)` | listen to the app (events below); only while your plugin is on |
| `setListScope(pluginId, params)` | query parameters for every list's API call (grid, tags, Continue, Needs attention), e.g. `{ shelf: 3 }`, for your `series_filter()` to read. The lists reload. Taken away when the plugin is switched off |
| `listsChanged()` | the same lists show something else now (e.g. a library was unlocked): they reload |
| `settingsPage(pluginId, key, label, icon, at)` | a page of your own in Settings at `#/settings/<key>`, at place `at` in the menu (0: first). `icon` is the name of a built-in icon (`"users"`, see Icons below) or any character, e.g. an emoji. Call it in `start`; it goes away when the plugin is switched off |
| `denied(error, retry)` | used by the app when the server answered 403: the `denied` event gets the answer's `detail` |
| `reader.where()` | what is being read: `{ book, series, page, pages }` (ids; `page` counts from 0), or `null` while no book is open. Plugin API 12 |
| `reader.redraw()` | the reader asks for the pages' overlays again and draws them, after your script changed what `page_overlay()` gives. Plugin API 12 |
| `reader.state()`, `.step(forward)`, `.goTo(page)`, `.openBook(which, page)`, `.setMode(mode)`, `.options(open)`, `.overlay(plugin, on)`, `.press(plugin, key)`, `.leave()`, `.refresh()` | the reader from a script, doing what its own buttons do (see [Assistants and voice control](#assistants-and-voice-control)). Plugin API 13 |
| `screenCommand(pluginId, name, spec, fn)` | something the page can do for an assistant or voice control (see [Assistants and voice control](#assistants-and-voice-control)). Plugin API 13 |
| `reloadSettings()` | load the settings again and show them, after they changed elsewhere. Plugin API 13 |

**Events** (`MangaVibe.on(pluginId, event, fn)`). `fn` may be async unless it says otherwise.

| Event | When / what to return |
|---|---|
| `start` | the page loads (the app waits for it before showing anything), or the plugin is switched on |
| `stop` | the plugin is switched off: forget what it showed |
| `denied (detail, retry)` | the server refused something (403) with this `detail`: return `true` if you deal with it (e.g. ask for a password, then call `retry()`) |
| `series-info (series)` | not async: rows for the series page's details, `[["Library", "Comics"]]`. `series.plugin_data[<id>]` is what your `series_info()` sent |
| `empty-library ()` | not async: the text for an empty library page, or nothing |
| `edit-open (series)` | not async: the Edit dialog opened; fill in your fields |
| `edit-changed (series)` | not async: `true` if your fields changed (else Save just closes) |
| `edit-save (series)` | Save, after the metadata: save your fields, return `{ message }`, or `{ message, leave: true }` when the series can't be shown any more (the app goes back to the library). Throw to show an error |
| `settings-open ()` | Settings opened: refresh what you show there |
| `book-action (key, at)` | one of your `book_actions` buttons was pressed in the reader; `at` is `MangaVibe.reader.where()`. Return `true` if your script does it (e.g. opens an editor): then `run_book_action` isn't called. Plugin API 12 |
| `folders-changed ()` | a library folder was added or removed |

Switching the plugin on or off in Settings adds or takes out its markup in place, without reloading the
page. Once your script has run it stays loaded until the next page load, so keep what it does to its
stores and components. **Reload plugins** reloads the page, so a changed plugin shows its new version.
Put styles in `css` as usual. A plugin folder can keep these in
files of their own and read them in (the saved searches plugin does).

**On the server** (Plugin API 8), a plugin can shape what everyone sees:

| Hook | |
|---|---|
| `series_filter(ctx, request)` | SQL conditions on `models.Series` for what this request may see, in every list and count. Read your own query parameters (`request.query_params`). Raise `HTTPException` to refuse. Not caught: if it fails, the request fails, so nothing shows by mistake. Keep it fast |
| `middleware = SomeAsgiMiddleware` | put in front of every request while the plugin is on, e.g. to refuse a locked library's pages and images |
| `reading_mode(ctx, series)` | a default reading mode for series without their own: `("vertical", "the mode of Webtoons")`, or `None` |
| `series_info(ctx, series)` | a dict sent with the series page as `plugin_data[<id>]` |
| `on_start(ctx)` | at app start, after Reload plugins and when switched on |
| `can_switch_off(ctx)` | a reason it can't be switched off now (shown to the owner), or `None` |
| `on_folders_changed(ctx)` / `on_folder_removed(ctx, folder)` | a library folder was added, or just removed |
| `enabled_by_default = False` | the plugin starts switched off the first time it's seen (e.g. accounts) |

**Who is asking** (`app/users.py`, Plugin API 9). With the accounts plugin each request runs as a
logged-in person; without it, as the owner (id 1), who may do everything.

| | |
|---|---|
| `users.current()` | the user of this request: `.id`, `.name`, `.can_manage` (an admin) |
| `dependencies=[users.manage]` | on a route of your own: admins only (403 for others) |
| `users.personal_key("my_key")` | a settings key for the current user's own copy (the owner keeps `my_key`) |
| `scope[users.SCOPE_KEY] = user` | in a `middleware` with `middleware_order < 0` (before accounts, 0): this request runs as `user`, no login asked. Plugin API 13 |

**`users.SCOPE_KEY` logs people in.** A request with a user in its scope skips the accounts login and
runs as that user, admins included. Only a middleware before accounts (`middleware_order < 0`) can set
it, so such a plugin decides who may do what in the app: Remote control sets it after checking a key.
Set it only after checking who is asking yourself, and as an owner, only install a plugin with a negative
`middleware_order` when you trust it as much as the app.

Reading progress, reading status, favorites and last read are each user's own. Reading events
(`on_book_started`…) only fire for **the owner** (the first admin): plugins like tracker syncs are set up
once for the app, so other accounts' reading isn't sent to it. Events and other background work
(`run_in_background`, threads, scans) run as the owner, so `ctx.reading_status()` there is the owner's;
inside a request (a button, a dropdown) it's the person's who asked.

Two more slots: `topbar` (the top bar, before Settings; inside a component) and `account-row` (each
account in Settings → Accounts, with the accounts plugin; `a` is the account: `a.id`, `a.name`, `a.admin`).
`$store.app.admin` says whether the person may manage the app; `MangaVibe.settingsPage(..., { personal: true })`
makes a page everyone sees.

## Assistants and voice control

Plugin API 13. The **Remote control** plugin is an MCP server (see
[manga-vibe-mcp](https://github.com/ivokolarov0/manga-vibe-mcp)): an assistant uses the app through it, as the person whose key it
has. Its `screen_describe` and `screen_press` tools already reach the buttons, fields and switches your
`html` adds, so a plugin works by voice without doing anything. Give it tools of its own when a step is
easier said than clicked ("translate this chapter", "describe this page").

**On the server**: a tool that answers from the server, even with no screen open.

```python
from app.plugins import McpTool, Plugin

class Notes(Plugin):
    id = "notes"
    mcp_tools = [McpTool("add", "Add a note to a series",
                         {"series_id": {"type": "integer"}, "text": {"type": "string"}},
                         required=["series_id", "text"]),
                 McpTool("wipe", "Remove every note", admin=True)]  # admins only

    def run_mcp_tool(self, ctx, name, args):
        if name == "add":
            ctx.set_series_data(args["series_id"], "note", args["text"])
            return "Saved"                        # text, or anything JSON can hold
        raise ValueError(f"No tool {name}")       # an error the assistant sees
```

The tools are called `<plugin id>__<name>` (here `notes__add`; a `-` in the id becomes `_`). `params`
is a JSON Schema `properties` object. `run_mcp_tool` runs as the person whose key it is
(`users.current()`), in a request of its own; what it changes in `ctx.db` is committed. Raise
`ValueError`, `HTTPException` or `PermissionError` to report what went wrong.

A MangaVibe from before Plugin API 13 has no `McpTool`, so importing it there stops the plugin loading.
To run on both: `try: from app.plugins import McpTool` / `except ImportError: McpTool = None`, and set
`mcp_tools` only when `McpTool` is there.

**In the page**: a command for the screen the person is looking at, from your `js`.

```js
MangaVibe.onStart(() => {
  MangaVibe.screenCommand("notes", "show", {
    description: "Open the notes of the series on the screen",
    params: { tab: { type: "string" } }, required: [],
  }, async ({ tab }) => {
    Alpine.store("notes").open = true;
    return { open: true };               // what the assistant gets; throw an Error to say it failed
  });
});
```

It's offered as the tool `notes__show` while a screen with your plugin on is open (the assistant can pick
which screen). The built-in plugins have examples: saved searches (`saved_searches__list` on the server,
`saved_searches__show` in the page) and libraries (`libraries__list`, `libraries__pick`).

**The reader from a script** (`MangaVibe.reader`), each doing what the reader's own button does and
giving `false` while no book is open:

| | |
|---|---|
| `state()` | `where()` and more: `label`, `series_title`, `mode`, `fit`, `options_open`, `at_end`, `previous` / `next` (`{ book, label }`), and `plugins`: each one's overlay (`{ label, on }`) and buttons (`[{ key, label }]`) |
| `step(forward)` | the next or previous page (vertical: the next page's top). Forward at the last page shows the end card |
| `goTo(page)` | a page of this book, from 0 |
| `openBook(which, page)` | `"next"`, `"previous"` or a book id, at `page` (from 0) or where it was left |
| `setMode(mode)` | `"paged"` or `"vertical"`, saved for the series (admins) |
| `options(open)` | the reading options (the drawer) open or closed |
| `overlay(plugin, on)` | a plugin's overlay on (`true`), off (`false`), or switched (`null`) |
| `press(plugin, key)` | one of a plugin's `book_actions` buttons (your `book-action` handler gets it as from a tap) |
| `leave()`, `refresh()` | back out of the reader; open the book again (e.g. after its settings changed) |

**Who a user is** (`find_user(ctx, user_id)`): a request with a key instead of a login asks the plugins
who that user is. The accounts plugin answers; a plugin of your own that keeps accounts should too
(return a `users.User`, or `None` once the account is gone).

## A reader of its own

Plugin API 14. Some books aren't pictures: an EPUB that is mostly text is a light novel. The scan
keeps it as a book of format **`epub-text`**. Its spine documents, its **sections**, count as its
pages: section *n* is page *n* of the book's progress, so Continue reading, Read/Unread, reading
events and tracker sync work as for any book. Its thumbnail is the EPUB's cover. The page reader can't
show it, and while no plugin reads it, it's listed under **Not supported**.

A plugin reads such books with two parts:

```python
class LightNovels(Plugin):
    id = "light-novel"
    reads = ["epub-text"]   # the book formats it has a reader for
    router = router         # serves the sections, e.g. GET /api/plugins/light-novel/api/books/<id>/sections/<n>
    js = READER_JS          # registers the reader
```

On the server, `ctx.text_sections(book)` lists the sections (entry names, in reading order) and
`ctx.read_entry(book, name)` reads one, or a picture they show. **Clean the XHTML before sending it to
the page**: an EPUB can hold scripts, styles and links to other sites.

In the page, register the reader for the format:

```js
MangaVibe.registerReader("light-novel", "epub-text", {
  open(el, book, ctl) {     // el: the whole reader screen, empty; book: GET /api/books/<id>
    el.innerHTML = "…";     // start at section ctl.page
  },
  close() {},               // leaving: stop timers, remove listeners added outside `el`
  step(forward) {},         // optional: turn a page (volume keys, voice control)
  goTo(section) {},         // optional
  options(open) {},         // optional: show or hide its settings
  back() { return false; }, // optional: Android back. true: it closed something of its own
});
```

`ctl` is what the app does for the reader:

| | |
|---|---|
| `ctl.page` | the section to start at (from 0): the saved place, or the `?page=` asked for |
| `ctl.save(section, completed)` | save where the reader is. Without `completed` it's sent a moment later (and when leaving). `true` marks the book read, `false` unread; reaching the last section doesn't mark it read, since that's a whole chapter, not a page |
| `ctl.leave()` | back to where the book was opened from |
| `ctl.openBook(which, section)` | another book of the series: `"next"`, `"previous"` or a book id (`book.next`, `book.prev` say if there is one) |
| `ctl.fullscreen()` | fullscreen on or off |

The app keeps the screen on and gives the plugin the Android volume keys (`step`) and back button
(`back`). Keys are the reader's own: the page reader's shortcuts are off while it shows a book. Where
inside a section the reader is (a scroll position) is the plugin's own to keep, e.g. with
`ctx.set_series_data()` from its API.

A MangaVibe from before Plugin API 14 lists these books under Not supported and never calls the reader.

## Styling: plugins and your own custom.css

All built-in CSS is in cascade layers (`app/web/static/css/`, one file per feature). Plugin CSS and
your own `docker/mangavibe/custom.css` are loaded **after** it and **outside** the layers, so they
always win, without `!important`. The order is: built-in → plugins → custom.css.

The easiest way to restyle is to change the design tokens from `css/tokens.css`
(`--accent`, `--card`, `--poster-width`, `--radius`, colours…). See `examples/custom.css` and
`examples/plugins/sakura_theme.py`. `custom.css` is created with instructions on first start;
edit it with File Station and reload the page.

There are two themes, dark and light: `<html data-theme="dark">` or `"light"`. A token set on
`:root` counts for both; for one theme only, use `:root[data-theme="light"] { --accent: … }`.
`--accent-text` is the text colour on the accent (dark in the dark theme, white in the light one):
set it too if your accent needs the other.

### Icons

The app's line icons are in one SVG sprite at the top of `index.html`. In your HTML:

```html
<button class="btn small"><svg class="ic" aria-hidden="true"><use href="#i-star"/></svg>Favorite</button>
```

In a script, `icon("star")` gives the same as a string (for `x-html`). They take the text colour and
scale with the font size; add the class `fill` for a filled one. Names: `search` `x` `plus` `check`
`check-up` `check-circle` `circle` `ban` `alert` `info` `chevron-left` `chevron-right` `chevron-down`
`arrow-up` `external` `dots` `library` `play` `book-open` `books` `star` `bookmark` `filter` `sort`
`settings` `sun` `moon` `palette` `tag` `blocks` `download` `upload` `refresh` `phone` `folder` `copy`
`pencil` `trash` `lock` `unlock` `key` `log-out` `user` `users` `shield` `image`.

## Example

`examples/plugins/sakura_theme.py` only changes the look (pink accent, rounder corners).

`examples/plugins/reading_log.py` uses the reading events, a settings box, Settings buttons, a status
line and its own web page.

`examples/plugins/translation_groups.py` uses fields, scan hooks, series buttons and dropdowns:
it shows scanlation groups from `[Group]` tags, lets a `series.json` file fix chapter numbers,
and adds a "Find missing chapters" button.

`examples/plugins/page_info.py` draws over reader pages: page overlays, per-page data, its own worker
thread, book badges and book buttons. A translator plugin is built the same way.
