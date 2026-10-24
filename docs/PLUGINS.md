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
| `ctx.reading_status(series)` | the status the app shows: `unread`, `reading`, `completed`, `plan_to_read`, `on_hold`, `dropped` |
| `ctx.set_reading_status(series, status)` | set it by hand (`None` = work it out from progress again). Does **not** fire `on_series_status_changed`, so a sync can't loop |
| `ctx.http` | the app's HTTP client: `ctx.http.request(method, url, headers=..., data=...)`, `get_json`, `post_json`. Throttled per site, retries, raises `ctx.http.ProviderError` (its `status` is the HTTP code, e.g. `404`, or `None` if the site couldn't be reached) |
| `ctx.web_url(path)` | full address of this plugin's `handle_web` page, as the browser reaches the app (e.g. `http://192.168.1.5:8080/api/plugins/mal/web/callback`) |
| `ctx.run_in_background(fn, *args)` | run `fn(ctx, *args)` later with its own database session. Pass ids, not rows. Use it for long work started from a button |

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

`examples/plugins/translation_groups.py` uses all the other features except providers:
it shows scanlation groups from `[Group]` tags, lets a `series.json` file fix chapter numbers,
and adds a "Find missing chapters" button.
