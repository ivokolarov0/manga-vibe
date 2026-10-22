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
| **Change how the app looks** | `css = """ :root { --accent: #ff6fa8; } """` (loaded after the built-in styles, so it wins) |
| **Metadata provider** | `is_provider = True`, implement `search(ctx, query, year)` → `list[Candidate]` and `fetch(ctx, provider_id)` → dict of series fields. Optional `url = "https://..."` links the site on the Metadata settings page |

## The `ctx` object

| | |
|---|---|
| `ctx.log` | logger (messages appear in the container log) |
| `ctx.settings` / `ctx.save_settings(dict)` | the plugin's own saved settings |
| `ctx.set_field(series, key, value)` / `ctx.get_field(series, key)` | extra fields |
| `ctx.list_pages(book)` | page names inside a book |
| `ctx.list_entries(book)` | all names inside a zip/cbz (not only images) |
| `ctx.read_entry(book, name)` | bytes of one entry, e.g. `"ComicInfo.xml"` |
| `ctx.read_series_file(series, name)` | bytes of a sidecar file in the series folder (e.g. `series.json`), or `None` |
| `ctx.db` | the database session (advanced; MangaVibe commits after hooks) |

`book` and `series` are database rows; see `app/models.py` for their fields.

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

`examples/plugins/translation_groups.py` uses all the other features except providers:
it shows scanlation groups from `[Group]` tags, lets a `series.json` file fix chapter numbers,
and adds a "Find missing chapters" button.
