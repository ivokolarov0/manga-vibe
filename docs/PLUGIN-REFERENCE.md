# Plugin reference

[← Back to the README](../README.md) · [Using plugins](using-plugins.md) · [The full guide (PLUGINS.md)](PLUGINS.md)

Every part of a MangaVibe plugin, one at a time: what it is, then the smallest code that uses it, with a
comment above it saying what it does. Read the text, read the code, or both.

- **Where plugins go:** a `.py` file (or a folder with `__init__.py`) in the `plugins` folder of your data
  folder. Then **Settings → Plugins → Sources & setup → Reload plugins**.
- **What a plugin may use:** `app.plugins`, `app.plugins.sdk`, `app.plugins.web`, `app.plugins.query`,
  `app.users` on the server, and `window.MangaVibe` in the page. The rest of the app can change in any
  version (see [The SDK](#the-sdk)).
- **More detail:** [PLUGINS.md](PLUGINS.md) has the long explanations and every job's full contract. This
  page is the quick one. Every Python sample on it is run by the tests (`tests/test_plugin_reference.py`),
  so they work with this version of MangaVibe.

---

## The basics

### The smallest plugin

A plugin is a class that inherits from `Plugin` and has a unique `id`. Everything else is optional.

```python
# A plugin that does nothing yet: it shows up in Settings → Plugins
from app.plugins import Plugin

class Hello(Plugin):
    id = "hello"
    name = "Hello"
```

### Name, version and description

`name` and `description` are what Settings → Plugins shows. `version` is compared with your GitHub
releases when you publish updates.

```python
# How the plugin is shown in Settings → Plugins
from app.plugins import Plugin

class Hello(Plugin):
    id = "hello"
    name = "Hello"
    version = "1.2.0"
    description = "Says hello in the log."
```

### Category

Where Settings → Plugins lists the plugin: `metadata`, `reader`, `library`, `system`, `appearance` or
`other`. A list means several: it's listed under the first and tagged with the others.

```python
# Listed under Reader, tagged Library
from app.plugins import Plugin

class PageTools(Plugin):
    id = "page-tools"
    category = ["reader", "library"]
```

### Permissions

Tell the owner what the plugin uses. They see the list in Settings, and an update that adds an entry
asks them first. Write it as a plain list: Install reads it before running the plugin.

```python
# Shown on the plugin's page, and asked for again when an update adds one
from app.plugins import Plugin

class Tracker(Plugin):
    id = "tracker"
    permissions = ["Connects to api.example.com", "Reads your reading progress"]
```

### Starting switched off

A plugin starts switched on the first time it's seen. Set `enabled_by_default = False` for one the owner
should switch on themselves.

```python
# The owner switches it on in Settings → Plugins
from app.plugins import Plugin

class Experimental(Plugin):
    id = "experimental"
    enabled_by_default = False
```

### Updates from GitHub

Name the plugin's GitHub repo, and its page in Settings gets **Updates**: the newest published release,
whose tag (`v1.3.0`) is compared with `version`. MangaVibe never updates a plugin by itself.

```python
# Updates come from this repo's releases
from app.plugins import Plugin

class Tracker(Plugin):
    id = "tracker"
    version = "1.3.0"
    repo = "your-name/mangavibe-tracker"
```

### A folder plugin

A plugin can be a folder with `__init__.py`. Its other files are imported relatively, and a folder is
where templates (for [views](#replace-a-view)) and longer scripts go.

```text
plugins/
└── notes/
    ├── __init__.py      # the Plugin class
    ├── core.py          # from . import core
    ├── notes.js         # read into the class as js
    └── templates/views/ # only for plugins that draw a view
```

### Logging

`ctx.log` writes to the container log, under the plugin's name. Most hooks get `ctx` as their first
argument.

```python
# A line in the log every time a book is scanned
from app.plugins import Plugin

class Logger(Plugin):
    id = "logger"

    def on_book_scanned(self, ctx, book, parsed):
        ctx.log.info("Scanned %s", book.file_name)
```

---

## Settings and the plugin's page

Each plugin has a page in **Settings → Plugins** (tap it in the list). These put things on it.

### A settings box

`settings_fields` adds text boxes. The saved value is `ctx.setting(key)`.

```python
# A box for the user name, read back with ctx.setting()
from app.plugins import Plugin, SettingDef

class Tracker(Plugin):
    id = "tracker"
    settings_fields = [SettingDef("user", "User name", help="Your name on the site")]

    def status(self, ctx):
        return f"User: {ctx.setting('user') or 'not set'}"
```

### A secret setting

`secret=True` is never sent back to the browser: the page only shows that something is saved.

```python
# An API key that stays on the server
from app.plugins import Plugin, SettingDef

class Tracker(Plugin):
    id = "tracker"
    settings_fields = [SettingDef("api_key", "API key", secret=True)]
```

### A dropdown setting

With `options`, a setting is a dropdown. `default` is the value while nothing is saved.

```python
# A dropdown with two choices, "auto" until the owner picks one
from app.plugins import Plugin, SettingDef

class Sync(Plugin):
    id = "sync"
    settings_fields = [SettingDef("mode", "When to sync", default="auto",
                                  options=[("auto", "While reading"), ("manual", "Only with the button")])]
```

### Saving settings from code

`ctx.settings` is everything saved; `ctx.save_settings()` merges new values in. Use it for things the
plugin keeps itself, like a login token.

```python
# Remember a token after logging in
from app.plugins import Plugin

class Tracker(Plugin):
    id = "tracker"

    def remember(self, ctx, token):
        ctx.save_settings({"token": token})
```

### A button on the plugin's page

`global_actions` are buttons on the plugin's page, not tied to a series. `run_global_action` gets the
button's key; what it returns is shown as a message.

```python
# A "Sync all" button
from app.plugins import Action, Plugin

class Tracker(Plugin):
    id = "tracker"
    global_actions = [Action("sync", "Sync all")]

    def run_global_action(self, ctx, action_key):
        return "Synced"
```

### A button that asks first

`confirm` is a question shown before the button runs.

```python
# Asks "Forget everything?" before running
from app.plugins import Action, Plugin

class Tracker(Plugin):
    id = "tracker"
    global_actions = [Action("reset", "Reset", confirm="Forget everything?")]

    def run_global_action(self, ctx, action_key):
        ctx.save_settings({"token": None})
        return "Forgotten"
```

### Opening a web page from a button

Return `OpenUrl` from a button to open a page, e.g. a website's login.

```python
# The Connect button opens the site's login page
from app.plugins import Action, OpenUrl, Plugin

class Tracker(Plugin):
    id = "tracker"
    global_actions = [Action("connect", "Connect")]

    def run_global_action(self, ctx, action_key):
        return OpenUrl("https://example.com/login")
```

### A status line

`status()` is the short line under the plugin in Settings. Keep it quick: it's asked again every few
seconds while the page is open.

```python
# "Connected" or "Not connected" under the plugin's name
from app.plugins import Plugin

class Tracker(Plugin):
    id = "tracker"

    def status(self, ctx):
        return "Connected" if ctx.settings.get("token") else "Not connected"
```

### A web address of its own

`handle_web` answers `GET /api/plugins/<id>/web/<path>`, e.g. a login's callback. `ctx.web_url(path)` is
that address as the browser reaches the app. Return a message, an `OpenUrl` to redirect, or `None` for
404.

```python
# The login sends the browser back to /api/plugins/tracker/web/callback?code=...
from app.plugins import Plugin

class Tracker(Plugin):
    id = "tracker"

    def handle_web(self, ctx, path, params):
        if path == "callback":
            ctx.save_settings({"code": params.get("code")})
            return "Connected. You can close this page."
        return None
```

---

## Scanning the library

### Fixing a book's numbers

`on_book_scanned` runs for each new or changed file, after the file name was read. Change `parsed` to
fix the volume, chapter or extra.

```python
# Files named "... Special.cbz" are extras
from app.plugins import Plugin

class Specials(Plugin):
    id = "specials"

    def on_book_scanned(self, ctx, book, parsed):
        if "special" in book.file_name.lower():
            parsed.extra = True
```

### After a series was scanned

`on_series_scanned` gets the series and its books, once the scan of its folder is done.

```python
# Log how many books each series has
from app.plugins import Plugin

class Counter(Plugin):
    id = "counter"

    def on_series_scanned(self, ctx, series, books):
        ctx.log.info("%s: %d books", series.folder_name, len(books))
```

### Reading files inside a book

`ctx.list_entries(book)` lists what's inside an archive; `ctx.read_entry(book, name)` reads one entry as
bytes. `ctx.list_pages(book)` lists only the pages.

```python
# Read ComicInfo.xml when the book has one
from app.plugins import Plugin

class ComicInfo(Plugin):
    id = "comicinfo"

    def on_book_scanned(self, ctx, book, parsed):
        if "ComicInfo.xml" in ctx.list_entries(book):
            xml = ctx.read_entry(book, "ComicInfo.xml")
            ctx.log.info("%s has %d bytes of ComicInfo", book.file_name, len(xml))
```

### A file next to the books

`ctx.read_series_file(series, name)` reads a file in the series' folder (e.g. `series.json`), or gives
`None`.

```python
# Read series.json from the series' folder
import json

from app.plugins import Plugin

class SeriesJson(Plugin):
    id = "series-json"

    def on_series_scanned(self, ctx, series, books):
        raw = ctx.read_series_file(series, "series.json")
        if raw:
            ctx.log.info("series.json: %s", json.loads(raw))
```

### When the owner deletes a series

`on_series_deleted` runs just before a series and its folder are deleted, so it and your data can still
be read.

```python
# Clean up before the series is gone
from app.plugins import Plugin

class Notes(Plugin):
    id = "notes"

    def on_series_deleted(self, ctx, series):
        ctx.clear_series_data(series)
```

---

## The series page

### An extra field

`fields` declares a field shown in the series' details; `ctx.set_field()` sets its value (`None` removes
it), `ctx.get_field()` reads it.

```python
# A "Group" line on every series page
from app.plugins import FieldDef, Plugin

class Groups(Plugin):
    id = "groups"
    fields = [FieldDef("group", "Group")]

    def on_series_scanned(self, ctx, series, books):
        ctx.set_field(series, "group", "Demo Scans")
```

### A button on the series page

`actions` are buttons on every series page. `run_action` gets the button's key and the series; what it
returns is shown as a message.

```python
# A "Count books" button
from app.plugins import Action, Plugin

class Counter(Plugin):
    id = "counter"
    actions = [Action("count", "Count books")]

    def run_action(self, ctx, action_key, series):
        return f"{len(series.books)} books"
```

### A dropdown on the series page

`choices` puts a dropdown under the reading status. `get_choice` gives the selected value (`None`:
unknown, `False`: hide it for this series); `set_choice` runs when the owner picks one.

```python
# Where the series is on a tracker site
from app.plugins import Choice, Plugin

class Tracker(Plugin):
    id = "tracker"
    choices = [Choice("list", "On my list", options=[("reading", "Reading"), ("done", "Done")])]

    def get_choice(self, ctx, key, series):
        return ctx.get_series_data(series, key)

    def set_choice(self, ctx, key, series, value):
        ctx.set_series_data(series, key, value)
        return "Saved"
```

### Labels on the books

`book_badges` gives a short label per book in the series' list, as `{book_id: text}`.

```python
# "New" on books nobody has opened
from app.plugins import Plugin

class NewBadge(Plugin):
    id = "new-badge"

    def book_badges(self, ctx, books):
        return {b.id: "New" for b in books if not b.progress}
```

### Data of its own per series

`ctx.set_series_data(series, key, value)` keeps any JSON value for a series, never shown in the app.
It's removed with the series. `series` may be the row or its id.

```python
# Remember when the plugin last looked at a series
import time

from app.plugins import Plugin

class Checker(Plugin):
    id = "checker"

    def on_series_scanned(self, ctx, series, books):
        ctx.set_series_data(series, "checked_at", time.time())
```

### Rows in the series' details

`series_info` sends a dict with the series page (as `plugin_data[<id>]`); the page script turns it into
rows with the `series-info` event.

```python
# Send the series' shelf code to the page
from app.plugins import Plugin

class Shelf(Plugin):
    id = "shelf"
    js = """MangaVibe.on("shelf", "series-info", (s) => [["Shelf", s.plugin_data.shelf.code]]);"""

    def series_info(self, ctx, series):
        return {"code": f"A-{series.id}"}
```

---

## Reading events

These run in the background after the change is saved, one at a time, for the owner's own reading only.

### A book was opened

```python
# Log the first time a book is opened
from app.plugins import Plugin

class ReadingLog(Plugin):
    id = "reading-log"

    def on_book_started(self, ctx, book, series):
        ctx.log.info("Started %s", book.file_name)
```

### A book was finished

Runs when the last page is reached, or when the owner marks it read.

```python
# Log every finished book
from app.plugins import Plugin

class ReadingLog(Plugin):
    id = "reading-log"

    def on_book_completed(self, ctx, book, series):
        ctx.log.info("Finished %s", book.file_name)
```

### The reading status changed

`old` and `new` are `unread`, `reading`, `completed`, `plan_to_read`, `on_hold` or `dropped`.

```python
# Do something when a series is completed
from app.plugins import Plugin

class Tracker(Plugin):
    id = "tracker"

    def on_series_status_changed(self, ctx, series, old, new):
        if new == "completed":
            ctx.log.info("Completed %s", series.folder_name)
```

### Reading and setting the status

`ctx.reading_status(series)` is the status the app shows; `ctx.set_reading_status(series, status)` sets
it (`None`: work it out from progress again). Setting it doesn't fire `on_series_status_changed`, so a
sync can't loop.

```python
# Mark a series as dropped from a button
from app.plugins import Action, Plugin

class Dropper(Plugin):
    id = "dropper"
    actions = [Action("drop", "Drop it")]

    def run_action(self, ctx, action_key, series):
        ctx.set_reading_status(series, "dropped")
        return f"Was {ctx.reading_status(series)}"
```

## The activity log

Plugin API 29. Admins see who did what, and when (the built-in Activity log plugin keeps the entries).
`activity.log(kind, summary, target=, details=)` writes one: the time and who (the person asking, or
*System* for background work) are filled in. Name your own kinds after your plugin. Never put a password,
key or token in `details`.

```python
# Write an entry when a button does something an admin should know about
from app.plugins import Action, Plugin, activity

class Cleaner(Plugin):
    id = "cleaner"
    actions = [Action("clean", "Clean up")]

    def run_action(self, ctx, action_key, series):
        activity.log("cleaner.cleaned", series.folder_name, target=("series", series.id),
                     details={"files": 3})
        return "Cleaned"
```

`on_activity(ctx, entry)` hears every entry, in the background. `entry` has `at` (UTC), `user_id`
(`None`: System), `user_name`, `kind`, `group`, `summary`, `target` and `details`.

```python
# Warn in the log when someone fails to log in
from app.plugins import Plugin

class LoginWatch(Plugin):
    id = "login-watch"

    def on_activity(self, ctx, entry):
        if entry["kind"] == "account.login_failed":
            ctx.log.warning("Failed login as %s from %s", entry["summary"], entry["ip"])
```

---

## The reader

### Drawing over pages

`overlay_label` adds a switch to the plugin's panel in the reading options. While it's on, the reader asks
`page_overlay` for the page shown and draws the boxes it returns. Positions are fractions of the page
(0 to 1), top-left first.

```python
# A box with "Hello" at the top of every page
from app.plugins import Box, Overlay, Plugin

class Hello(Plugin):
    id = "hello-overlay"
    overlay_label = "Hello"

    def page_overlay(self, ctx, book, page):
        return Overlay(boxes=[Box(0.1, 0.05, 0.3, 0.06, text="Hello")])
```

### Styling the boxes

Every box has the classes `ov-box` and `ov-<plugin id>`, plus its `cls`. Set the colour with `--ov-bg`, so
the reader's see-through setting still works.

```python
# Yellow boxes
from app.plugins import Box, Overlay, Plugin

class Notes(Plugin):
    id = "notes"
    overlay_label = "Notes"
    css = ".ov-box.note { --ov-bg: #ffe680; }"

    def page_overlay(self, ctx, book, page):
        return Overlay(boxes=[Box(0.1, 0.1, 0.4, 0.08, text="A note", cls="note")])
```

### Not ready yet

Answer `Overlay(pending=True, message=...)` while the page is being worked on: the reader shows the message
and asks again every few seconds. Keep `page_overlay` fast; do slow work [in a thread](#a-worker-thread).

```python
# "Translating…" until the page has a result
from app.plugins import Box, Overlay, Plugin

class Translate(Plugin):
    id = "translate"
    overlay_label = "Translation"

    def page_overlay(self, ctx, book, page):
        text = ctx.get_page_data(book, page)
        if text is None:
            return Overlay(pending=True, message="Translating…")
        return Overlay(boxes=[Box(0.1, 0.1, 0.5, 0.1, text=text)])
```

### A note in the top bar

`note` is a quiet line under the chapter's name, shown only while the reader's controls are open.

```python
# Which page this is, in the top bar
from app.plugins import Overlay, Plugin

class PageNote(Plugin):
    id = "page-note"
    overlay_label = "Page note"

    def page_overlay(self, ctx, book, page):
        return Overlay(note=f"Page {page + 1} of {book.page_count}")
```

### Text to read aloud

`speech` is the page as plain text. The reader's **Read aloud** reads it with the device's voice, and screen
readers get it too.

```python
# A description for readers who can't see the page
from app.plugins import Overlay, Plugin

class Describe(Plugin):
    id = "describe"
    overlay_label = "Description"

    def page_overlay(self, ctx, book, page):
        return Overlay(speech=f"Page {page + 1}. Two people talk in the rain.")
```

### Data of its own per page

`ctx.set_page_data(book, page, value)` keeps any JSON value for one page; it's removed with the book and
when its file changes. `ctx.page_data(book)` gives all of a book's.

```python
# Save a result for page 0, read it back
from app.plugins import Action, Plugin

class Pages(Plugin):
    id = "pages"
    book_actions = [Action("mark", "Mark page 1")]

    def run_book_action(self, ctx, action_key, book):
        ctx.set_page_data(book, 0, {"marked": True})
        return f"{len(ctx.page_data(book))} pages marked"
```

### The page's picture

`ctx.read_page(book, n)` gives page `n` (from 0) as image bytes, for every format. `max_width` shrinks it.

```python
# The size of the first page, in bytes
from app.plugins import Action, Plugin

class PageSize(Plugin):
    id = "page-size"
    book_actions = [Action("size", "Size of page 1")]

    def run_book_action(self, ctx, action_key, book):
        data = ctx.read_page(book, 0, max_width=800)
        return f"{len(data)} bytes"
```

### Buttons for the open book

`book_actions` are buttons in the plugin's panel in the reading options. `run_book_action` gets the key and
the book.

```python
# A "Translate this chapter" button in the reader
from app.plugins import Action, Plugin

class Translate(Plugin):
    id = "translate"
    book_actions = [Action("all", "Translate this chapter")]

    def run_book_action(self, ctx, action_key, book):
        return f"Started {book.file_name}"
```

### Handling a reader button in the page

A `book_actions` button can be handled by the plugin's script instead: return `true` from the
`book-action` event and `run_book_action` isn't called. `at` is what's being read.

```js
// The "edit" button opens something in the page instead of going to the server
MangaVibe.on("notes", "book-action", (key, at) => {
  if (key !== "edit") return;
  MangaVibe.toast(`Editing page ${at.page + 1}`);
  return true;
});
```

### Redrawing the overlays

After your script changed what `page_overlay` gives, ask the reader to draw again.

```js
// Draw the boxes of the pages on screen again
MangaVibe.reader.redraw();
```

### A default reading mode

`reading_mode` gives a mode for series without their own: `("vertical", why)` or `None`.

```python
# Korean series read vertically unless set otherwise
from app.plugins import Plugin

class Webtoons(Plugin):
    id = "webtoons"

    def reading_mode(self, ctx, series):
        if series.country_of_origin == "KR":
            return ("vertical", "Korean series")
        return None
```

### How a series is read

`ctx.reading_direction(series)` says how the reader shows a series: `paged` or `vertical`.

```python
# Log the reading mode of each scanned series
from app.plugins import Plugin

class Modes(Plugin):
    id = "modes"

    def on_series_scanned(self, ctx, series, books):
        ctx.log.info("%s reads %s", series.folder_name, ctx.reading_direction(series))
```

---

## Long work

### Work started from a button

`ctx.run_in_background(fn, *args)` runs `fn(ctx, *args)` later with its own database session, so the button
answers at once. Pass ids, not rows.

```python
# The button answers at once, the work runs afterwards
from app.plugins import Action, Plugin

class Sync(Plugin):
    id = "sync"
    actions = [Action("sync", "Sync")]

    def run_action(self, ctx, action_key, series):
        ctx.run_in_background(self.sync, series.id)
        return "Sync started"

    def sync(self, ctx, series_id):
        ctx.log.info("Syncing series %s", series_id)
```

### A worker thread

`ctx.run_in_thread(fn)` starts `fn(ctx)` in a thread of the plugin's own, for work that runs for hours. It
must return once `ctx.should_stop()` is `True` (the plugin was reloaded, updated or switched off).

```python
# A worker that wakes up every 10 seconds until the plugin stops
import time

from app.plugins import Plugin

class Worker(Plugin):
    id = "worker"

    def on_start(self, ctx):
        ctx.run_in_thread(self.work)

    def work(self, ctx):
        while not ctx.should_stop():
            time.sleep(10)
```

### Calling a website

`ctx.http` is the app's HTTP client: throttled per site, with retries. `get_json`, `post_json` and
`request`; failures raise `ctx.http.ProviderError`.

```python
# Ask a website for JSON, and say so when it's down
from app.plugins import Action, Plugin

class Weather(Plugin):
    id = "weather"
    global_actions = [Action("check", "Check")]

    def run_global_action(self, ctx, action_key):
        try:
            data = ctx.http.get_json("https://api.example.com/status")
        except ctx.http.ProviderError as e:
            return f"The site didn't answer ({e.status})"
        return str(data)
```

### Files it can get again

`ctx.cache_dir` is a folder of the plugin's own (`cache/plugins/<id>` in the data folder) for files it can
download again, e.g. pictures. Data that matters goes in settings or `set_series_data` instead.

```python
# Download a picture once, then use the saved copy
from app.plugins import Action, Plugin

class Banner(Plugin):
    id = "banner"
    global_actions = [Action("get", "Get the banner")]

    def run_global_action(self, ctx, action_key):
        path = ctx.cache_dir / "banner.png"
        if not path.exists():
            path.write_bytes(ctx.http.get_bytes("https://example.com/banner.png"))
        return f"{path.stat().st_size} bytes"
```

The built-in **AniList recommendations** plugin (`app/plugins/builtin/anilist_recommendations/`) puts these
together: its own route asks AniList through `ctx.http`, keeps the answer with `set_series_data`, saves the
covers in `ctx.cache_dir`, and draws a panel in the `series-end` slot.

---

## Metadata providers

A provider finds titles, descriptions and covers, like the built-in AniList. `search` gives candidates,
`fetch` gives one's details.

```python
# A provider that always finds one made-up series
from app.plugins import Candidate, Plugin

class MyProvider(Plugin):
    id = "my-provider"
    is_provider = True
    url = "https://example.com"

    def search(self, ctx, query, year=None):
        return [Candidate(provider_id="1", title=query, year=year)]

    def fetch(self, ctx, provider_id):
        return {"title_english": "Example", "description": "Made up.", "genres": ["Comedy"]}
```

`fetch` gives the series' fields: `title_english`, `title_romaji`, `title_native`, `synonyms`,
`description`, `publication_status`, `start_year`, `format`, `country_of_origin`, `chapters_total`,
`volumes_total`, `average_score`, `authors`, plus `genres`, `tags` (`[{"name": ...}]`), `links` and
`cover_url`.

---

## Your own API

### Routes of its own

Give the plugin a FastAPI `router`: it answers under `/api/plugins/<id>/api/`, only while the plugin is on.
`plugin_ctx` gives a route the usual `ctx`.

```python
# GET /api/plugins/notes/api/hello
from fastapi import APIRouter, Depends

from app.plugins import Context, Plugin
from app.plugins.web import plugin_ctx

router = APIRouter()

@router.get("/hello")
def hello(ctx: Context = Depends(plugin_ctx)):
    return {"hello": ctx.setting("name") or "world"}

class Notes(Plugin):
    id = "notes"
    router = router
```

### Admins only

`users.manage` on a route refuses everyone but admins (with Accounts on). `users.current()` is who asks.

```python
# Only admins may call it; it says who asked
from fastapi import APIRouter

from app import users
from app.plugins import Plugin

router = APIRouter()

@router.post("/wipe", dependencies=[users.manage])
def wipe():
    return {"by": users.current().name}

class Admin(Plugin):
    id = "admin-tools"
    router = router
```

### A setting for each person

`users.personal_key(key)` is a settings key for the current person's own copy (the owner keeps `key`).
Read and write it with `get_setting` / `set_setting` from the SDK.

```python
# Each account keeps its own "last note"
from fastapi import APIRouter, Depends

from app import users
from app.plugins import Plugin
from app.plugins.sdk import get_db, get_setting

router = APIRouter()

@router.get("/mine")
def mine(db=Depends(get_db)):
    return {"note": get_setting(db, users.personal_key("notes:last"), "")}

class Notes(Plugin):
    id = "notes"
    router = router
```

---

## In the page

### Markup in a slot

`html` maps a slot of the page to markup put there. It's [Alpine.js](https://alpinejs.dev) markup, like the
page's own. The [slots](#slots) are listed below.

```python
# A button next to Filter and Sort on the library page
from app.plugins import Plugin

class Random(Plugin):
    id = "random"
    html = {"library-tools": '<button class="btn" @click="MangaVibe.toast(\'Hello\')">Hello</button>'}
```

### Where in a slot

When several plugins use one slot, give `(markup, order)`: lower goes first, and markup without an order
counts as 50.

```python
# Last in the top bar, next to Settings
from app.plugins import Plugin

class Menu(Plugin):
    id = "menu"
    html = {"topbar": ('<button class="btn small">Menu</button>', 90)}
```

### A script

`js` runs after the app's own scripts. Register stores and components in `MangaVibe.onStart`, which works
when the page loads and when the plugin is switched on later.

```python
# A script with an Alpine store of its own
from app.plugins import Plugin

class Notes(Plugin):
    id = "notes"
    js = """
MangaVibe.onStart(() => {
  Alpine.store("notes", { open: false });
});
"""
```

### Calling the server from the page

`MangaVibe.api(path, { method, body })` gives the JSON answer, or throws an `Error` with the server's
message. `MangaVibe.toast(text)` shows a short message.

```js
// Call the plugin's own route and show what it said
const r = await MangaVibe.api("/api/plugins/notes/api/hello");
MangaVibe.toast(`Hello, ${r.hello}`);
```

### Listening to the app

`MangaVibe.on(pluginId, event, fn)` listens to the app while the plugin is on. `start` runs when the page
loads or the plugin is switched on; `stop` when it's switched off.

```js
// Say hello when the plugin starts
MangaVibe.on("hello", "start", () => MangaVibe.toast("Hello plugin is on"));
```

The events: `start`, `stop`, `denied`, `series-info`, `empty-library`, `edit-open`, `edit-changed`,
`edit-save`, `settings-open`, `book-action`, `folders-changed`. [PLUGINS.md](PLUGINS.md#your-own-api-and-ui)
says what each one gets.

### A field in the Edit dialog

The `series-edit` slot puts fields in the series' Edit dialog; `edit-open` fills them in, `edit-changed`
says whether they changed, `edit-save` saves them.

```js
// Save the plugin's field when the owner presses Save in the Edit dialog
MangaVibe.on("notes", "edit-save", async (series) => {
  await MangaVibe.api(`/api/plugins/notes/api/note/${series.id}`, { method: "PUT", body: { text: "…" } });
  return { message: "Note saved" };
});
```

### A sheet or dialog

Put it in the `sheets` slot. `x-dialog` says how to close it, so a click outside, Escape and the Android
back button do.

```python
# A sheet opened with $store.app.sheet = 'notes'
from app.plugins import Plugin

SHEET = """
<div class="sheet" x-data x-show="$store.app.sheet === 'notes'" x-dialog="$store.app.sheet = ''">
  <p>My notes</p>
</div>"""

class Notes(Plugin):
    id = "notes"
    html = {"sheets": SHEET}
```

### Changing a piece of the UI kit

Buttons, dialogs, menus… are the UI kit ([UI-KIT.md](UI-KIT.md), Plugin API 31). Restyle one with its custom
properties in `css`; change what an interactive one does with `MangaVibe.kit`, in the first lines of `js`.

```js
// Every dialog: a click on the backdrop doesn't close it any more (Escape still does)
MangaVibe.kit.extend("notes", "dialog", (base) => ({ ...base, options: { ...base.options, backdrop: false } }));
```

### A Settings page of its own

`settings_pages` adds an entry to Settings' menu; the page's markup goes in the `settings` slot, shown
while `section` is its key.

```python
# Settings → Notes
from app.plugins import Plugin

class Notes(Plugin):
    id = "notes"
    settings_pages = [{"key": "notes", "label": "Notes", "icon": "pencil", "order": 70}]
    html = {"settings": '<section x-show="section === \'notes\'"><h2>Notes</h2></section>'}
```

`order` places it (Plugins is 50, Updates 60); `"personal": True` shows it to everyone, not only admins.

### Styles

`css` is loaded after the app's own styles, so it wins without `!important`. Changing the design tokens
(`--accent`, `--radius`…) is the easiest way to restyle.

```python
# A pink accent and rounder corners
from app.plugins import Plugin

class Pink(Plugin):
    id = "pink"
    category = "appearance"
    css = ":root { --accent: #ff6fa8; --radius: 14px; }"
```

### Icons

The app's icons are an SVG sprite: `<use href="#i-<name>"/>`. In a script, `MangaVibe.ui.icon(name)` gives
the same as a string. The names are listed in [PLUGINS.md](PLUGINS.md#icons).

```html
<!-- A button with the star icon -->
<button class="btn small"><svg class="ic" aria-hidden="true"><use href="#i-star"/></svg>Favorite</button>
```

### Slots

| Slot | Where |
|---|---|
| `home-top` | above Home, the library and Continue reading (give your markup `x-data`) |
| `home-end` | the Home page, after its rows (give your markup `x-data`) |
| `library-tools` | the library's buttons, after Filter / Sort / Letter |
| `library-filters` | the row of filters that are on |
| `select-menu` | the ⋯ menu of the library's multi-select |
| `library-end` | the end of the library page, for dialogs |
| `card-badges` | each series card, in the poster's corner (`s` is the series) |
| `continue-top` | above Continue reading |
| `series-actions` | the series page, after Continue (`s` is the series) |
| `series-menu` | the series page's ⋯ menu |
| `series-panels` | the series page's panels |
| `series-end` | the end of the series page |
| `book-row` | each book on the series page (`b` is the book) |
| `series-edit` | the Edit dialog's fields |
| `settings` | a Settings page of your own |
| `folder-row` | each folder in Settings → Library folders |
| `attention-note` | under a Needs attention list's explanation |
| `topbar` | the top bar, before Settings |
| `nav-items` | the Sidebar's column, after Library and Continue (give your markup `x-data`) |
| `sheets` | the end of the page, for sheets and dialogs |

### A row on the Home page

`home_sections` adds rows to Home; `home_section()` fills one with series (the app leaves out what the
person may not see and draws the cards). Everyone can move and hide it in Settings → Display.

```python
# A row of the newest manhwa on the Home page
from app.plugins import HomeSection, Plugin
from app.plugins.sdk import N_, Series

class ManhwaRow(Plugin):
    id = "manhwa-row"
    home_sections = [HomeSection("manhwa", N_("New manhwa"))]

    def home_section(self, ctx, request, key, limit):
        return (ctx.db.query(Series).filter(Series.country_of_origin == "KR")
                .order_by(Series.added_at.desc()).limit(limit).all())
```

### Cards of its own on the Home page

A `"tiles"` row gives dicts instead of series; a tile opens its `href`, or the plugin's script opens it.

```python
# A row of links on the Home page
from app.plugins import HomeSection, Plugin
from app.plugins.sdk import N_

class Shortcuts(Plugin):
    id = "shortcuts"
    home_sections = [HomeSection("links", N_("Shortcuts"), kind="tiles")]

    def home_section(self, ctx, request, key, limit):
        return [{"id": "fav", "title": "Favorites", "subtitle": "", "icon": "star", "href": "#/library?fav=1"}]
```

```js
// Open a tile from the plugin's script instead of following its href
MangaVibe.onStart(() => {
  MangaVibe.on("shortcuts", "home-tile", (item) => {
    if (item.id !== "fav") return false;
    MangaVibe.showLibrary({ fav: "1" });
    return true;
  });
});
```

---

## Shaping what everyone sees

### Hiding series

`series_filter` gives SQL conditions on `Series` for every list and count. Read your own query
parameters; raise `HTTPException` to refuse.

```python
# Never show series whose folder starts with "_"
from app.plugins import Plugin
from app.plugins.sdk import Series

class HideDrafts(Plugin):
    id = "hide-drafts"

    def series_filter(self, ctx, request):
        return [~Series.folder_name.startswith("_")]
```

### A parameter for every list

`MangaVibe.setListScope(pluginId, params)` adds query parameters to every list's request (the grid, tags,
Continue, Needs attention), for your `series_filter` to read.

```js
// Every list asks with ?only=favorites from now on
MangaVibe.setListScope("my-scope", { only: "favorites" });
```

### Middleware

`middleware` is an ASGI middleware put in front of every request while the plugin is on.

```python
# Adds a header to every answer
from app.plugins import Plugin

class Stamp:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def stamped(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message["headers"], (b"x-stamp", b"1")]
            await send(message)
        await self.app(scope, receive, stamped)

class Stamper(Plugin):
    id = "stamper"
    middleware = Stamp
```

### Starting and stopping

`on_start` runs at app start, after Reload plugins and when the plugin is switched on. `can_switch_off`
gives a reason it can't be switched off now, or `None`.

```python
# Can't be switched off while something is running
from app.plugins import Plugin

class Guard(Plugin):
    id = "guard"

    def on_start(self, ctx):
        ctx.log.info("Guard started")

    def can_switch_off(self, ctx):
        return "A sync is running" if ctx.settings.get("busy") else None
```

### Library folders added or removed

```python
# Hear about the owner's library folders changing
from app.plugins import Plugin

class Folders(Plugin):
    id = "folders"

    def on_folders_changed(self, ctx):
        ctx.log.info("Library folders changed")

    def on_folder_removed(self, ctx, folder):
        ctx.log.info("Removed %s", folder.root_path)
```

---

## Assistants and voice control

### A tool on the server

`mcp_tools` are tools an assistant can use through the Remote control plugin. `run_mcp_tool` runs as the
person whose key it is.

```python
# A tool "notes__add" that saves a note on a series
from app.plugins import McpTool, Plugin

class Notes(Plugin):
    id = "notes"
    mcp_tools = [McpTool("add", "Add a note to a series",
                         {"series_id": {"type": "integer"}, "text": {"type": "string"}},
                         required=["series_id", "text"])]

    def run_mcp_tool(self, ctx, name, args):
        ctx.set_series_data(args["series_id"], "note", args["text"])
        return "Saved"
```

### A command in the page

`MangaVibe.screenCommand()` is something the open screen can do for an assistant.

```js
// "notes__show": opens the plugin's sheet on the screen
MangaVibe.screenCommand("notes", "show", { description: "Open the notes", params: {} }, async () => {
  Alpine.store("app").sheet = "notes";
  return { open: true };
});
```

### The reader from a script

`MangaVibe.reader` does what the reader's own buttons do: `state()`, `step(forward)`, `goTo(page)`,
`openBook(which)`, `setMode(mode)`, `options(open)`, `overlay(plugin, on)`, `press(plugin, key)`, `leave()`.

```js
// Turn to the next page
MangaVibe.reader.step(true);
```

---

## A reader of its own

For books the page reader can't show, like light novels (`epub-text`). `reads` lists the formats;
`ctx.text_sections(book)` lists a book's sections on the server.

```python
# Serves a light novel's sections to its reader in the page
from fastapi import APIRouter, Depends

from app.plugins import Context, Plugin
from app.plugins.sdk import Book, get_or_404
from app.plugins.web import plugin_ctx

router = APIRouter()

@router.get("/books/{book_id}/sections")
def sections(book_id: int, ctx: Context = Depends(plugin_ctx)):
    return ctx.text_sections(get_or_404(ctx.db, Book, book_id))

class LightNovels(Plugin):
    id = "light-novels"
    reads = ["epub-text"]
    router = router
```

In the page, the reader registers itself for the format. `ctl.page` is where to start; `ctl.save()` keeps
the place.

```js
// The smallest reader: shows which section it's on
MangaVibe.registerReader("light-novels", "epub-text", {
  open(el, book, ctl) { el.textContent = `${book.label}, section ${ctl.page + 1}`; },
  close() {},
});
```

---

## Replacing a part of the app

Each replaceable part of MangaVibe is a **job**. A plugin says which jobs it does with `provides`, and which
it needs another plugin to do with `requires`. Switching it on switches off the plugin doing that job now.
The full contracts are in [PLUGINS.md](PLUGINS.md#jobs-replacing-a-part-of-the-app).

### Doing a job

```python
# This plugin does the "book labels" job, and needs the series cards to be drawn
from app.plugins import Plugin

class MyLabels(Plugin):
    id = "my-labels"
    provides = {"book:label": 1}
    requires = {"view:card": 1}

    def book_label(self, book):
        return book.file_name
```

The number is the version of the job's contract the plugin follows.

### Book labels: `book:label`

`book_label(book)` is what the lists, the reader and Continue call a book. Use the owner's numbers:
`eff_volume`, `eff_chapter`, `eff_chapter_end`, `eff_extra`.

```python
# "Volume 3" instead of "Vol 3"
from app.plugins import Plugin

class LongLabels(Plugin):
    id = "long-labels"
    provides = {"book:label": 1}

    def book_label(self, book):
        if book.eff_volume is not None:
            return f"Volume {book.eff_volume:g}"
        return book.file_name
```

### The file name parser: `scanner:parser`

`parse_name(filename, series_title)` reads the numbers from a file's name and gives a `Parsed`. After
switching parsers, the next scan reads every book's numbers again.

```python
# Every file's first number is its chapter
import re

from app.plugins import Plugin
from app.plugins.sdk import Parsed

class FirstNumber(Plugin):
    id = "first-number"
    provides = {"scanner:parser": 1}

    def parse_name(self, filename, series_title=""):
        m = re.search(r"\d+", filename)
        return Parsed(name=filename, chapter=float(m.group()) if m else None)
```

### The metadata matcher: `metadata:matcher`

Which search result is the series. `match_query` turns a folder name into a search, `match_score` rates a
result from 0 to 100, `match_pick` takes one or none.

```python
# Exact titles only
from app.plugins import Plugin

class Exact(Plugin):
    id = "exact-matcher"
    provides = {"metadata:matcher": 1}

    def match_query(self, folder_name):
        return folder_name, None

    def match_score(self, query, titles, year=None, cand_year=None, cand_format=None, novel=False):
        return 100 if query.lower() in (t.lower() for t in titles) else 0

    def match_pick(self, scored, threshold=90, lead=10):
        return scored[0][1] if scored and scored[0][0] == 100 else None
```

### The library's filters: `library-query`

`query_schema` says what the filter sheets show; `filter_query` narrows `lq.query`; `sort_order` gives the
ORDER BY. `values(params, key)` reads a comma-separated parameter.

```python
# Filters by publication status, sorts by title
from app.plugins import Plugin
from app.plugins.query import values
from app.plugins.sdk import Series

class MyFilters(Plugin):
    id = "my-filters"
    provides = {"library-query": 1}

    def query_schema(self, ctx):
        return {"filters": [{"key": "pub", "label": "Status", "kind": "choice", "params": ["pub"],
                             "options": [["RELEASING", "Releasing"], ["FINISHED", "Finished"]]}],
                "sorts": [{"key": "title", "label": "Title", "order": "asc"}], "default_sort": "title"}

    def filter_query(self, ctx, lq, params):
        if values(params, "pub"):
            lq.query = lq.query.filter(Series.publication_status.in_(values(params, "pub")))

    def sort_order(self, ctx, lq, params):
        return [lq.title.asc()]
```

### Needs attention lists: `attention:list`

A shared job: every plugin doing it adds lists. `attention_rows` gives one list's rows; a row's `key` lets
the owner ignore it.

```python
# A "No year" list
from app.plugins import Plugin
from app.plugins.sdk import Series, visible_series

class NoYear(Plugin):
    id = "no-year"
    provides = {"attention:list": 1}
    attention_lists = [{"key": "noyear", "label": "No year", "help": "Series without a year.", "alert": False}]

    def attention_rows(self, ctx, key, request):
        rows = ctx.db.query(Series).filter(Series.start_year.is_(None), *visible_series(ctx.db, request))
        return [{"key": f"noyear:{s.id}", "series_id": s.id, "series": s.folder_name, "text": "No year"}
                for s in rows]
```

### Saved searches: `saved-searches`

The searches are kept in the setting `jobs:saved-searches` (each account's under its personal key), so a
replacement finds everyone's.

```python
# Read the person's saved searches
from app import users
from app.plugins import Plugin
from app.plugins.sdk import get_setting

class MySearches(Plugin):
    id = "my-searches"
    provides = {"saved-searches": 1}

    def searches(self, ctx):
        return get_setting(ctx.db, users.personal_key("jobs:saved-searches"), [])
```

### Replace a view

A view (the cards, a page, a dialog, the top bar) is a folder plugin with its template in `templates/`.
Start from the built-in one with `{% extends "@<plugin id>/views/<name>.html" %}` and change a block.

```python
# plugins/compact_cards/__init__.py: draws the series cards
from app.plugins import Plugin

class CompactCards(Plugin):
    id = "compact-cards"
    provides = {"view:card": 1}
```

```html
{# plugins/compact_cards/templates/views/card.html: the built-in card with just the title #}
{% extends "@cards/views/card.html" %}
{% block text %}<div class="card-title" x-text="s.title"></div>{% endblock %}
```

The views: `view:card` (`card.html`), `view:library` (`library.html`), `view:continue` (`continue.html`),
`view:home` (`home.html`), `view:series` (`series.html`), `view:nav` (`nav.html`), `dialog:edit` (`edit.html`), `dialog:fix-match`
(`fix-match.html`). A view must keep the slots its contract lists. `examples/plugins/compact_cards/` is a
complete one.

### A Settings section: `settings:<section>`

Library folders, Display, Reader, Metadata and Updates are each a job. A replacement changes the same
setting keys, its menu entry is `settings_pages`, its markup `templates/views/settings-<section>.html`.

```python
# Does the Display section of Settings
from app.plugins import Plugin

class MyDisplay(Plugin):
    id = "my-display"
    provides = {"settings:display": 1}
    settings_pages = [{"key": "display", "label": "Display", "icon": "palette", "order": 20, "personal": True}]
```

### The image reader: `reader:images`

Draws the pages in the reader. The host keeps the bars, progress, overlays and read aloud; the plugin draws
into `el` and calls `ctl.shown(page)` whenever the page shown changes.

```js
// The smallest image reader: one page at a time, no zoom
MangaVibe.registerReader("simple-reader", "images", {
  modes: ["paged"],
  open(el, book, ctl) {
    this.el = el; this.book = book; this.ctl = ctl;
    this.goTo(ctl.page);
  },
  goTo(page) {
    this.page = page;
    this.el.innerHTML = `<img src="/api/books/${this.book.id}/pages/${page}">`;
    this.ctl.shown(page);
  },
  step(forward) {
    const next = this.page + (forward ? 1 : -1);
    if (next >= this.book.page_count) return this.ctl.end();
    if (next < 0) return this.ctl.start();
    this.goTo(next);
  },
  pageBox() { return this.el.querySelector("img"); },
  close() {},
});
```

---

## The SDK

What a plugin may import. The built-in plugins use only this (a test checks it), so a plugin of yours can do
anything they do.

```python
# The rows, the database and the helpers most plugins need
from app.plugins.sdk import Book, Series, SessionLocal, display_title, get_setting, set_setting, title_lang
```

| Import | Has |
|---|---|
| `app.plugins` | `Plugin`, `Context`, `FieldDef`, `Action`, `Choice`, `SettingDef`, `OpenUrl`, `Box`, `Overlay`, `McpTool`, `Candidate` |
| `app.plugins.sdk` | rows (`Series`, `Book`, `Library`, `Shelf`, `Tag`, `SeriesTag`, `Link`), the database (`SessionLocal`, `get_db`, `get_setting`, `set_setting`), `Parsed`, `natural_key`, `display_title`, `book_label`, `get_or_404`, `visible_series`, `http`, `clean_html` and more |
| `app.plugins.web` | `plugin_ctx` (a route's `ctx`) |
| `app.plugins.query` | `values`, `number`, `flag` (reading query parameters) |
| `app.users` | `current()`, `manage`, `personal_key()` |
| `window.MangaVibe` | everything in the page: `api`, `toast`, `on`, `onStart`, `reader`, `ui`, `series`, `library`… |

## The `ctx` object

| | |
|---|---|
| `ctx.log` | the plugin's logger |
| `ctx.db` | the database session |
| `ctx.settings`, `ctx.setting(key)`, `ctx.save_settings(dict)` | the plugin's settings |
| `ctx.set_field()`, `ctx.get_field()` | extra fields on the series page |
| `ctx.set_series_data()`, `ctx.get_series_data()`, `ctx.series_data()`, `ctx.clear_series_data()` | the plugin's own data per series |
| `ctx.set_page_data()`, `ctx.get_page_data()`, `ctx.page_data()`, `ctx.page_data_counts()`, `ctx.clear_page_data()` | the plugin's own data per page |
| `ctx.list_pages()`, `ctx.list_entries()`, `ctx.read_entry()`, `ctx.read_page()`, `ctx.text_sections()`, `ctx.read_series_file()` | reading books and series folders |
| `ctx.reading_status()`, `ctx.set_reading_status()`, `ctx.reading_direction()` | reading status and mode |
| `ctx.http` | the HTTP client |
| `ctx.cache_dir` | a folder for files the plugin can get again |
| `ctx.web_url(path)` | the address of the plugin's `handle_web` |
| `ctx.run_in_background()`, `ctx.run_in_thread()`, `ctx.should_stop()` | long work |
