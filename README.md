# MangaVibe

> [!WARNING]
> **This app is fully vibe coded.** Every line was written by an AI assistant (Claude), guided by
> conversation rather than written or reviewed line by line by a human developer. It has automated
> tests and has been checked in a browser, but it has not had a professional security audit.
> Use it at your own risk, keep it on your home network (see [docs/SYNOLOGY.md](docs/SYNOLOGY.md)),
> and keep backups of anything you care about. Your manga folder is mounted read-only, so the app
> can't change your files.

A personal manga server for a Synology NAS: scans your manga folder, orders messy file names
correctly, and gives you a fast reader in the phone browser. Metadata from AniList and
MangaUpdates is fetched automatically. See [SPEC.md](SPEC.md) for the full plan and [DECISIONS.md](DECISIONS.md) for choices made.

- **Install on the NAS:** [docs/SYNOLOGY.md](docs/SYNOLOGY.md)
- **Write plugins:** [docs/PLUGINS.md](docs/PLUGINS.md)

## Status

### Phase 1: core library and reader ✅
- Scans every library root; each first-level folder is a series. Reads `.cbz/.zip`, `.cbr/.rar`, `.cb7/.7z`,
  image `.epub` (in the book's reading order), `.pdf`, archives inside archives and folders of
  images, without extracting (type detected from content, Deflate64 ZIPs via 7-Zip). Text EPUBs
  (light novels) are listed with a "coming later" note.
- Incremental rescans (only new/changed files are opened), every 6 hours and on demand.
- Filename parser with all spec test cases: volumes, chapters, ranges, `15a`, decimals, Japanese,
  extras, `v2` revisions (older revision hidden), duplicates flagged, never hidden.
- Library grid with infinite scroll, search, sort. Series page with book list, mark read/unread,
  "mark previous as read", reading status, favorite, Files tab with parsed values and flags.
- Reader: right-to-left, left-to-right, vertical/webtoon; tap zones, swipe with slide animation,
  pinch zoom and pan (with a 🔒 lock), double-tap zoom, fit screen/width/height/original, page slider,
  preload, fullscreen, progress saved on every page, "Next: Ch 11" at the end of a book.
- Files tab: set a file's volume/chapter or a chapter range (e.g. 10–18 in one zip) by hand, mark it
  extra or hidden; the series re-orders. Files are never renamed. "Rescan this series" button.
- Continue Reading page.
- Plugin system: extra fields, scan hooks, series-page buttons, provider interface (example plugin included).
- Docker image + Synology guide.

### Phase 2: metadata ✅
- Automatic matching with AniList (primary) and MangaUpdates (link + fallback), confidence score,
  posters, titles in English/Romaji/Native, description, status, genres, tags, authors, links.
- Fix match dialog: search per provider, paste an AniList/MangaUpdates link, unmatch, "don't match".
- Needs Attention page: failed matches (pick a candidate with one tap), unreadable files with the
  exact error, duplicates, chapter gaps, unsupported formats.
- Keeps working when a provider is down (AniList was offline when this was built).

### Phase 3 (partly): filters ✅
- Search over all titles, synonyms, folder name and authors.
- Filters: genres/tags include + exclude, reading status, publication status, metadata status,
  format (manga/manhwa/manhua/one-shot/novel), favorites. Sorts: title, added, updated, read, score,
  year, unread count. Kept in the URL.
- Library cards show read state: ✓ and faded when read, progress bar and "3/12" when reading.

## Run on your PC

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/python dev.py
```

Then open http://localhost:8090. `dev.py` creates a small fake library in `dev-library/`.
To use real manga: set `LIBRARY_PATHS=D:\Manga` before running it.

Working in a git worktree? Run `python preview.py` from it instead: it borrows the main
checkout's `.venv` and `dev-library`, gives each worktree its own port and database, opens the
browser and prints a LAN address for testing on a phone. Several worktrees can run at once.

## Tests

```bash
.venv/Scripts/python -m pytest
```

## Layout

```
app/main.py             startup, routes
app/config.py           environment variables
app/models.py, db.py    SQLite tables, first start, migrations
app/scanner/            parser.py (file names), archives.py (reading), walker.py (scanning)
app/api/                series.py, books.py, system.py
app/plugins/            plugin system
app/images.py           thumbnails, resized page cache
app/web/index.html      the page: Alpine templates for every view
app/web/static/css/     styles: main.css imports one file per feature (CSS layers)
app/web/static/js/      app.js (Alpine data), reader.js (plain-JS reader), common.js,
                        vendor/alpine.min.js
app/web/static/icons/   app icons; manifest.json next to them
examples/plugins/       example plugin
docker/entrypoint.sh    container start script
```
