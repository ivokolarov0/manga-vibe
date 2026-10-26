# MangaVibe — Project Specification

A personal, self-hosted manga server with built-in online metadata, written for one user and run in Docker on a Synology NAS. Reading happens in a mobile-friendly web app in the browser (Android Chrome, fullscreen). No native app.

> **How to use this document (for the AI assistant in the new session):**
> Read this whole file before writing code. Build in the phases listed in section 14, in order. Finish and test each phase before starting the next. When something in this spec is ambiguous, pick the simplest option that satisfies it and note the decision in `DECISIONS.md`. Keep the codebase small and readable: the owner maintains it with AI help, not as a professional developer.

---

## 1. Background and goals

The owner tried Komga + Komf and Kavita. The problems were:
- Online metadata needed extra tools (Komf, a browser extension) that were buggy.
- Fixing a wrong match was not possible from the UI.
- Kavita's online metadata (Kavita+) is a paid subscription.

**Goal:** one app that scans a manga folder, fetches metadata from AniList/MangaUpdates automatically, lets the owner fix wrong matches with one click, and provides a good reader on an Android phone.

### Non-goals
- Multiple users, roles, permissions.
- Public internet exposure (LAN only; remote access via VPN/Tailscale).
- Native Android/iOS apps.
- Downloading manga from the internet.
- Modifying, renaming or moving the owner's files. **The manga folder is mounted read-only. The app never writes to it.**

---

## 2. Environment

| Item | Value |
|---|---|
| NAS | Synology, x86_64, DSM with the **older "Docker" package** (no Compose/Projects UI). Containers are created with the Docker GUI wizard. |
| NAS IP | `192.168.1.215` |
| Ports already used | 49153 (Komga), 8085 (Komf) |
| MangaVibe port | **8090** (host) → 8090 (container) |
| Client | Android phone, Chrome. Also desktop browser for management. |
| Timezone | Europe/Sofia |

Deliverables must therefore include:
- A `Dockerfile` that produces a single image.
- A `docker-compose.yml` (for reference / local development).
- **Step-by-step instructions for the Synology Docker GUI wizard** (image import or registry, volumes, port, environment), because Compose is not available on the NAS. Simplest path: build the image on a PC, push to Docker Hub (or `docker save` → import in the Synology Docker "Image → Add → Add from file"), then launch it with the wizard.
- The app must start correctly with only the volumes and env vars set in the wizard. **It must create any config/database files it needs on first start** (lesson from Komf, which crashed because `application.yml` did not exist).

---

## 3. Library layout and file formats

### Layout
```
/manga                      (mounted read-only)
  Series Name A/
    Series_Name_A_v01_ch01_[OT].zip
    Series_Name_A10.zip
    ...
  Series Name B/
    Vol 01.cbz
    Chapter 5/              (folder of loose images = one chapter)
      001.jpg
      002.png
  ...
```
- Each **first-level folder** under a library root is one **series**.
- Each file (or image subfolder) inside a series folder is one **book** (a volume, a chapter, a range of chapters, or an extra).
- Nested deeper folders inside a series folder are scanned too; a folder that contains only images is one book.
- Support more than one library root (e.g. `/manga`, `/comics`), configured in the UI or env.

### Formats

| Format | Handling |
|---|---|
| `.cbz`, `.zip` | Python `zipfile`. Read pages directly from the archive; do not extract to disk. |
| `.cbr`, `.rar` | `rarfile` Python package + `unrar` binary installed in the Docker image. |
| `.cb7`, `.7z` | `py7zr` (nice to have). |
| Folder of `.jpg/.jpeg/.png/.webp/.gif/.avif` | Treat as one book. |
| `.epub` (image-based manga) | Read the spine; if pages are images, show them in the normal manga reader. |
| `.epub` (text, light novels) | **Phase 6.** Until then, list it in the library and show a "text EPUB, reader coming later" message. |
| `.pdf` | Nice to have (render pages with PyMuPDF). |

Inside archives: only image files count as pages. Ignore `__MACOSX/`, `Thumbs.db`, `.DS_Store`, text files, nested `ComicInfo.xml` (but read it for metadata, see 6.4). Page order = natural sort of the path inside the archive.

---

## 4. Ordering books inside a series (filename parsing)

File names are messy and inconsistent, even within one series. This is a core feature; it must be well tested. A reference implementation (`parser.py`) from an earlier session is provided alongside this spec; reuse or rewrite it, but **all test cases below must pass**.

### 4.1 Cleaning before parsing
1. Remove the file extension.
2. Remove bracketed group/tag text: `[KnO]`, `[OT]`, `{tags}`.
3. Remove parenthesised noise: `(Digital)`, `(2019)`, anything with `scan` or `rip`.
4. Replace `_` with spaces, collapse whitespace.
5. Remove the series title (folder name) so digits inside the title (e.g. *20th Century Boys*) are not read as numbers.

### 4.2 Detection rules (in this order)
1. **Revision marker:** a `v` + number **glued to a preceding digit** (`ch03v2`) is a revision (version 2), **not** a volume. Remove it before the next steps.
2. **Extras:** words `extra`, `bonus`, `omake`, `special`, `side story`, `oneshot`/`one shot` — matched even when glued to other text (`ExtraChapter`, `Extra2`). A trailing number becomes the extra index (`Extra2` → extra #2). Extras sort after all numbered books.
3. **Chapter range:** `ch01-05`, `c001-c010`, `Chapter 1~3` → chapters 1–5.
4. **Chapter:** `chapter`, `ch`, `c`, `#`, Japanese `第N話`. Decimals (`10.5`) and letter suffixes (`15a` → 15.1, `15b` → 15.2) are supported.
5. **Volume:** `volume`, `vol`, `v` (word-start), Japanese `第N巻`.
6. **Bare trailing number** (`Hana_to_Akuma52`, `Title 015a`) when no chapter or volume was found → **chapter**.
7. Nothing found → no number; sort by natural filename order after numbered books.

### 4.3 Sorting
- Books with a chapter number: sort by chapter (start of range).
- Books with only a volume number: sort by volume, placed before loose chapters if their chapter range is unknown.
- Then extras (by extra index, then name).
- Then unnumbered files (natural sort).
- Natural sort means `file2` before `file10`.

### 4.4 Duplicates and revisions
- Same chapter, different revision (`ch03` and `ch03v2`): keep the **highest revision** as the active book; keep the others as hidden "older revisions" (visible in the series' file list, not in reading order).
- Same chapter, same revision (e.g. two groups): **keep both** and flag the series as having duplicates. **Never silently hide a file.**

### 4.5 Required test cases

| File name | Series title | Expected |
|---|---|---|
| `Hana_to_Akuma_v01_ch01_[OT].zip` | Hana to Akuma | Vol 1, Ch 1 |
| `Hana_to_Akuma_v01_ch03v2_[OT].zip` | Hana to Akuma | Vol 1, Ch 3, revision 2 |
| `[KnO]Hana_to_Akuma52.zip` | Hana to Akuma | Ch 52 |
| `Hana_to_Akuma10.zip` | Hana to Akuma | Ch 10 |
| `Hana_to_Akuma_Bonus_Chapter_[FBD].zip` | Hana to Akuma | Extra |
| `Hana_to_Akuma_ExtraChapter.zip` | Hana to Akuma | Extra |
| `Hana_to_Akuma_Extra2.zip` | Hana to Akuma | Extra #2 (**not** chapter 2) |
| `Hana_to_Akuma_v2.zip` | Hana to Akuma | Vol 2 (not a revision) |
| `Berserk Vol.03 (Digital).cbz` | Berserk | Vol 3 |
| `[Group] 20th Century Boys c010-012.rar` | 20th Century Boys | Ch 10–12 |
| `Some Title - Chapter 10.5.zip` | Some Title | Ch 10.5 |
| `Some Title 015a.cbz` | Some Title | Ch 15.1 |
| `Kingdom v05 c040.cbz` | Kingdom | Vol 5, Ch 40 |
| `Title Omake.zip` | Title | Extra |
| `ワンピース 第3巻.zip` | ワンピース | Vol 3 |
| `ワンピース 第12話.zip` | ワンピース | Ch 12 |

**Full-folder test** (shuffled input, real folder from the owner's library). Given the Hana to Akuma files `v01_ch01..v01_ch05` (ch03/ch04 as `v2`), `v02_ch06..v02_ch09` (ch07/ch08 as `v2`), `Hana_to_Akuma10..14.zip`, `[KnO]Hana_to_Akuma52..58.zip`, the three extras, plus an old `v01_ch03_[OT].zip`, the expected order is: Ch 1–9, Ch 10–14, Ch 52–58, then the three extras. `v01_ch03_[OT].zip` is an older revision (hidden from reading order). No duplicates flagged.

### 4.6 Manual override
In the series "Files" view the owner can:
- Drag books into a different order, or edit a book's volume/chapter/extra values.
- Mark a book as hidden.
- Overrides are stored in the database keyed by file path and **survive rescans**.

---

## 5. Data model (SQLite)

Use SQLite (single file in `/data`). Use SQLAlchemy or SQLModel with simple migrations (Alembic, or a small hand-written version table). Suggested tables:

**library**: id, name, root_path, created_at

**series**: id, library_id, folder_path (unique), folder_name, sort_title,
- metadata: title_english, title_romaji, title_native, synonyms (JSON list), description, publication_status (`RELEASING`, `FINISHED`, `HIATUS`, `CANCELLED`, `NOT_YET_RELEASED`, `UNKNOWN`), start_year, format (`MANGA`, `ONE_SHOT`, `NOVEL`, `MANHWA`/`MANHUA` via country), country_of_origin, chapters_total, volumes_total, average_score, authors (JSON list of {name, role}),
- ids: anilist_id, mal_id, mangaupdates_id, mangadex_id,
- poster_path (local cached file), poster_source (`provider`, `upload`, `first_page`),
- match_status (`unmatched`, `auto`, `manual`, `failed`, `ignored`), match_confidence (0–100), matched_at,
- locked_fields (JSON list: fields the owner edited by hand; metadata refresh must not overwrite them),
- reading_status (`plan_to_read`, `reading`, `completed`, `on_hold`, `dropped`, or null = derived),
- favorite (bool), added_at, updated_at, last_read_at

**book**: id, series_id, path (unique), file_size, mtime, format, page_count,
- parsed: volume, chapter, chapter_end, extra (bool), extra_index, revision,
- override_* fields (nullable) and sort_override (nullable),
- hidden (bool: older revision or hidden by owner), duplicate_flag (bool),
- sort_index (computed after parsing/overrides),
- thumbnail_path

**tag**: id, name (unique), kind (`genre`, `tag`, `custom`)
**series_tag**: series_id, tag_id, rank (AniList tag rank, nullable), is_spoiler (bool)

**link**: id, series_id, site (`AniList`, `MyAnimeList`, `MangaUpdates`, `MangaDex`, `Official`, other), url

**progress**: book_id (unique), page (0-based), completed (bool), updated_at

**setting**: key, value (JSON) — reader defaults, library roots, etc.

---

## 6. Metadata

### 6.1 Providers
1. **AniList** (primary). GraphQL API at `https://graphql.anilist.co`, no API key needed. Gives English/romaji/native titles, synonyms, description, status, genres, tags (with rank and spoiler flag), cover image (extraLarge), banner, start date, chapters/volumes, average score, country, staff, `idMal`, and `externalLinks` (official sites, publishers). Build the MyAnimeList link from `idMal`: `https://myanimelist.net/manga/{idMal}`.
2. **MangaUpdates** (secondary). Public API `https://api.mangaupdates.com/v1/` (`POST /series/search`, `GET /series/{id}`). Used to add the MangaUpdates link and ID, and to fill fields AniList lacks (e.g. many manhwa/doujin-like entries, alternative titles, genres).
3. **MangaDex** (optional, phase 5): fallback for titles not on AniList and alternative covers.

Rate limits: respect them. AniList allows roughly 90 requests/minute but may be lower at times; throttle to ~30/min, honour `Retry-After` on HTTP 429, retry with backoff. Queue all metadata work in a background worker so the UI never waits on it.

### 6.2 Automatic matching
1. Build a search query from the folder name: remove `[group]` tags, `(year)`, `(Digital)`, underscores; keep the year as a hint if present.
2. Search AniList (type `MANGA`, include adult = configurable, default off).
3. Score each result 0–100: best normalised-title similarity against all of romaji/english/native/synonyms (use `rapidfuzz`), plus small bonuses for matching year and format.
4. **Auto-accept** if the best score ≥ 90 and it is at least 10 points ahead of the second result. Otherwise mark `failed` and put the series in the **"Needs attention"** list with the top candidates saved.
5. After an AniList match, query MangaUpdates by title to add its link/ID (accept only high-confidence matches there too).
6. Download the poster into `/data/covers/{series_id}.jpg` (resize to a reasonable max width, e.g. 600 px; keep a smaller grid version, e.g. 300 px, as WebP or JPEG).
7. Unmatched series still work: title = folder name, poster = first page of the first book.

### 6.3 Manual "Fix match" (must be one click away)
On every series page: **Fix match** button opens a dialog:
- Search box pre-filled with the folder name; results from AniList (and MangaUpdates as a second tab) shown with cover, all three titles, year, format, status, and a link to open the provider page in a new tab.
- Paste-a-URL option: paste an AniList or MangaUpdates URL directly and apply it.
- Select → apply immediately, show the new metadata, `match_status = manual`.
- Also: **Unmatch** (revert to folder name) and **Don't match** (`ignored`, never auto-matched again).

### 6.4 Local metadata
If a book contains `ComicInfo.xml`, read Series, Title, Number, Volume, Summary, Genre, Tags, Writer, Penciller, Web. Use it as a fallback when there is no online match, and use its Number/Volume to improve ordering when the filename gives nothing.

### 6.5 Editing and locks
The owner can edit any metadata field by hand (titles, description, tags, status, links, poster). An edited field is added to `locked_fields` and is never overwritten by refresh. Custom tags can be added; they have `kind = custom`.

### 6.6 Refresh
- Per series: **Refresh metadata** (re-fetch from the matched IDs).
- Global: scheduled refresh of `RELEASING` series once a week (configurable), throttled.

---

## 7. Features (UI)

### 7.1 Layout and navigation
- Mobile-first responsive web app. Must feel good on an Android phone in portrait. Desktop layout is the same UI, wider grid.
- Top bar: app name, search, filter button. Bottom nav on mobile (Library, Continue Reading, Needs Attention, Settings) or a simple menu.
- Dark theme by default, light theme option.
- Use a PWA manifest (`display: standalone`/`fullscreen`, icons) so the owner can "Add to Home screen" in Chrome. This is a small file, not a separate app.

### 7.2 Library grid
- **Grid of poster cards**: poster (2:3), title, small badges: unread count, publication status, reading status. Progress bar for in-progress series.
- Column count adapts to screen width (≈3 per row on a phone, more on desktop). Card size setting (small/medium/large).
- **Infinite scroll** with cursor/offset pagination from the API (page size ~60). Lazy-load images (`loading="lazy"`), use the small cached poster.
- Title shown in the owner's preferred language: setting **Title language = English / Romaji / Native**, falling back in that order to whatever exists, then the folder name.
- Long-press (mobile) / right-click: quick actions (mark read/unread, set reading status, fix match).

### 7.3 Search, filter, sort
- **Search** matches any title (en/romaji/native/synonyms), folder name, author.
- **Filters** (combinable, shown as chips; state kept in the URL so it survives reload and back button):
  - Genres and tags (include and exclude, e.g. include *Romance*, exclude *Horror*)
  - Publication status (Releasing, Finished, Hiatus, Cancelled)
  - Reading status (Unread, Reading, Completed, On hold, Dropped, Plan to read)
  - Match status (Matched, Needs attention, Unmatched)
  - Format / country (Manga, Manhwa, Manhua, One-shot, Novel)
  - Collection (finished series only, best effort): Complete (volumes or chapters 1..N all here),
    Incomplete (something missing), Can't tell (no total, mixed numbering, not finished). The
    series page shows "8 / 10 volumes"; incomplete series get a mark on their card.
  - Library
  - Favorites only
  - Year range, score range (nice to have)
- **Sort:** Title, Recently added, Recently read, Recently updated (new files), Score, Year, Unread count. Ascending/descending.

### 7.4 Series page
- Banner/poster, all three titles (primary large, others smaller), synonyms collapsible.
- Status chips: publication status, reading status (editable dropdown), favorite toggle.
- Description (expand/collapse), genres and tags as chips (tap a chip → library filtered by it; spoiler tags hidden behind a toggle).
- Authors/artists, year, chapters/volumes total, score.
- **Links**: buttons for AniList, MyAnimeList, MangaUpdates, MangaDex, official links.
- Buttons: **Continue / Start reading**, **Fix match**, **Refresh metadata**, **Edit metadata**, **Change poster** (upload an image, choose from provider covers, or pick a page from a book).
- Book list: in reading order, each with thumbnail, label ("Vol 1 Ch 3", "Ch 52", "Extra 2"), page count, progress (unread / page X of Y / read). Mark read/unread individually and "mark all previous as read".
- **Files** tab: actual file names, parsed values, revision/duplicate flags, manual reorder/override (section 4.6).

### 7.5 Reader
- Opens a book at the saved page.
- **Fullscreen:** button that calls the Fullscreen API; also enter fullscreen automatically on first tap if the setting is enabled. Hide UI chrome while reading; tap the centre to show/hide controls.
- Modes (per-series setting, then the library folder's, then a global default): **Pages** (default; the next page is always on the right, like every digital reader) and **Vertical/webtoon** (continuous scroll, no gaps).
- Pages: tap zones (left quarter back, right quarter forward; or a one-handed layout) and swipe to turn pages; volume keys are not available in browsers, ignore.
- Fit: fit width, fit height, fit screen, original. Pinch-zoom and double-tap zoom in paged mode.
- Double-page spread on landscape/desktop (nice to have, phase 5).
- Bottom slider to jump to a page, with page number.
- **Preload** the next 3–5 pages; serve images at a size suited to the screen (see 8.3).
- At the end of a book: "Next: Ch 11" button / automatic continue to the next book in order.
- Progress is saved on every page turn (debounced) and on leaving the reader. A book is marked completed when the last page is reached.
- Keep the screen awake while reading (Screen Wake Lock API, when available).

### 7.6 Reading progress and status
- Per-book progress (page, completed).
- Series reading status: if the owner set one manually, use it; otherwise derive: no progress → Unread, some → Reading, all non-extra books completed → Completed.
- **Continue Reading** view: series with in-progress books, most recent first, tapping opens the series page, where its Continue button resumes reading.
- Optional later: sync progress to AniList (phase 6, needs AniList OAuth).

### 7.7 Needs attention
A list of: series with failed/low-confidence matches (with "Fix match"), series matched to the same metadata entry (grouped by the shared AniList/MAL/MangaUpdates/MangaDex ID), series with duplicate files, series with chapter gaps (e.g. 14 → 52), unreadable/corrupt files, text EPUBs waiting for the novel reader.

### 7.8 Settings
- Library roots (add/remove, rescan now).
- Title language, theme, card size.
- Reader defaults (mode, fit, auto-fullscreen, preload count).
- Metadata: include adult results, auto-match on/off, auto-accept threshold, refresh schedule.
- Optional single password (see 10).

---

## 8. Backend

### 8.1 Stack
- Python 3.12, **FastAPI**, Uvicorn.
- SQLite via SQLAlchemy/SQLModel.
- `httpx` for provider APIs, `rapidfuzz` for title matching, `Pillow` for images, `rarfile` + `unrar`, `py7zr`, `natsort`.
- Background work: a simple in-process worker (asyncio task queue or APScheduler). No Redis/Celery.
- Frontend: keep it simple and dependency-light. Either server-rendered pages (Jinja2) + small vanilla JS/Alpine.js/htmx, or a small SPA with a tiny bundler. **No heavy framework unless clearly worth it.** Must be fast on a phone.

### 8.2 Scanning
- On startup and on demand (button), plus a periodic rescan (default every 6 hours). Optionally watch with `watchdog` (polling mode works better on NAS shares).
- **Incremental:** compare path + size + mtime; only open new or changed files. Removed files are marked missing (keep progress for 30 days in case they come back, e.g. a temporarily unmounted share).
- Opening an archive only to count pages and generate a thumbnail. Large libraries must scan in the background with progress shown in the UI.
- After scanning a series: run the parser, apply overrides, compute `sort_index`, set duplicate/revision flags, queue metadata matching for new series.

### 8.3 Image serving
- `GET /api/books/{id}/pages/{n}?w=1080` streams page *n* straight from the archive.
- Optional resize to width `w` (cache resized pages on disk under `/data/cache/pages`, LRU-limited by size setting, default 2 GB). Originals served when `w` is not given.
- Correct `Content-Type`, `ETag`/`Cache-Control` headers so the browser caches pages.
- Thumbnails for books and posters cached under `/data/cache`.

### 8.4 API (suggested)
```
GET    /api/libraries                       POST /api/libraries   DELETE /api/libraries/{id}
POST   /api/scan                            GET  /api/scan/status
GET    /api/series?cursor=&limit=&q=&genre=&tag=&exclude_tag=&pub_status=&read_status=&match=&format=&sort=&order=
GET    /api/series/{id}                     PATCH /api/series/{id}           (edit metadata, sets locks)
GET    /api/series/{id}/books
POST   /api/series/{id}/match/search        (body: query) -> candidates
POST   /api/series/{id}/match               (body: provider, provider_id | url)
POST   /api/series/{id}/unmatch             POST /api/series/{id}/ignore-match
POST   /api/series/{id}/refresh
POST   /api/series/{id}/poster              (upload | provider cover | book page)
PATCH  /api/books/{id}                      (overrides, hidden)
POST   /api/series/{id}/reorder             (list of book ids)
GET    /api/books/{id}/pages/{n}
GET    /api/books/{id}/progress             PUT /api/books/{id}/progress
POST   /api/series/{id}/mark-read           POST /api/series/{id}/mark-unread
GET    /api/continue-reading
GET    /api/attention
GET    /api/tags                            GET /api/settings  PUT /api/settings
GET    /api/health
```

---

## 9. Docker

### 9.1 Image
- Base: `python:3.12-slim`. Install `unrar` (non-free; if unavailable on the base distro, use `unrar-free` or `libarchive` via `rarfile`'s backend options, and document which RAR versions work).
- Run as a non-root user with configurable `PUID`/`PGID` env vars (default 1000/1000), but **also work when Synology runs it as root**.
- Expose 8090. Healthcheck on `/api/health`.

### 9.2 Volumes
| Container path | NAS folder | Mode |
|---|---|---|
| `/manga` | the owner's manga shared folder | **read-only** |
| `/data` | `docker/mangavibe` | read-write (database, covers, cache, config) |

Additional library roots can be mounted as `/library2`, etc., and added in Settings.

### 9.3 Environment variables
| Variable | Default | Purpose |
|---|---|---|
| `TZ` | `UTC` | Timezone (owner uses `Europe/Sofia`) |
| `LIBRARY_PATHS` | `/manga` | Comma-separated library roots on first start |
| `PORT` | `8090` | Listen port |
| `APP_PASSWORD` | empty | Optional login password |
| `PUID` / `PGID` | `1000` | File ownership for `/data` |
| `CACHE_MAX_GB` | `2` | Page cache limit |

### 9.4 First start must
- Create `/data/mangavibe.db`, `/data/covers`, `/data/cache` if missing.
- Run migrations.
- Add `LIBRARY_PATHS` as libraries and start a scan.
- Log clearly what it is doing; on errors (e.g. `/data` not writable) log a plain-English message explaining the fix (folder permissions on Synology).

### 9.5 Synology instructions (write these as `docs/SYNOLOGY.md`)
For the older Docker package GUI: getting the image onto the NAS, Create container → General Settings → **Advanced Settings before clicking Next** (Volume, Port Settings, Environment tabs), how to stop/edit/restart, how to view logs, how to update to a new image version, and the File Station permission fix (Properties → Permission → Create → Everyone → Read/Write → apply to sub-folders).

---

## 10. Security
- Intended for LAN only. Document using Synology VPN or Tailscale for remote reading; do **not** recommend port forwarding.
- Optional `APP_PASSWORD`: simple login page, session cookie (long-lived on the phone), no user management.
- Path safety: every file access must resolve inside a configured library root (no path traversal via API parameters).
- Never write inside library roots.

---

## 11. Performance targets
- Library of ~1,000 series / ~30,000 files must scroll smoothly on a mid-range Android phone.
- Library grid first load < 1 s on LAN after the first scan.
- Page turn feels instant thanks to preloading; first page of a book appears < 1 s.
- Initial full scan may take a while; it runs in the background and the UI stays usable.

---

## 12. Testing
- `pytest`.
- Parser: all cases in 4.5, including the shuffled full-folder test.
- Archive readers: small fixture archives (zip, rar if `unrar` present, image folder, image EPUB) generated in tests.
- Matching: unit tests for query cleaning and scoring with recorded (mocked) AniList responses; no live API calls in tests.
- API smoke tests with FastAPI's `TestClient`.
- Before finishing each phase: run the tests and start the app locally with a small sample library.

---

## 13. Project structure (suggested)
```
mangavibe/
  app/
    main.py            FastAPI app, startup
    config.py
    db.py  models.py   migrations/
    scanner/           walker.py, parser.py, archives.py, epub.py
    metadata/          anilist.py, mangaupdates.py, mangadex.py, matcher.py, worker.py
    api/               series.py, books.py, reader.py, settings.py, attention.py
    images.py          resizing, thumbnails, cache
    web/               templates/ static/ (css, js, icons, manifest.json)
  tests/
  docs/SYNOLOGY.md
  Dockerfile  docker-compose.yml  requirements.txt  README.md  DECISIONS.md  SPEC.md
```

---

## 14. Phases (build in this order)

Each phase ends with: tests passing, app running locally, short note in `README.md` on what works.

**Phase 1 — Core library and reader (the "usable" version)**
- Project skeleton, config, SQLite models, first-start creation of `/data`.
- Scanner: series folders, books (zip/cbz, rar/cbr, image folders), page counts, thumbnails.
- Filename parser + ordering + revisions/duplicates (all tests in 4.5).
- Library grid with infinite scroll, series page with book list.
- Reader: pages/vertical, tap/swipe, fullscreen, preload, progress saving, next-book.
- Continue Reading.
- Dockerfile + `docs/SYNOLOGY.md`. **The owner deploys this to the NAS before phase 2.**

**Phase 2 — Metadata**
- AniList client, automatic matching with confidence, poster download, titles en/romaji/native, description, status, genres, tags, links (AniList, MAL).
- MangaUpdates link/ID.
- Fix match dialog (search + paste URL), unmatch, ignore.
- Title language setting. Needs Attention page.

**Phase 3 — Filtering and status**
- Search, all filters and sorts from 7.3, URL-synced filter state.
- Reading status (manual + derived), favorites, mark read/unread, mark previous read.
- Tag chips → filtered library.

**Phase 4 — Editing and management**
- Edit metadata with locks, custom tags, poster change (upload/provider/page).
- Files tab: overrides, reorder, hide; overrides survive rescans.
- Settings page, scheduled rescans and metadata refresh, optional password.

**Phase 5 — Polish**
- Image EPUBs, cb7, PDF.
- Double-page spreads, zoom improvements, wake lock.
- MangaDex fallback provider. ComicInfo.xml reading.
- Performance pass on a large library.

**Phase 6 — Optional**
- Text EPUB reader (epub.js): font size, theme, position saving.
- AniList progress sync (OAuth).
- Komga-compatible API subset so Android apps like Mihon could read from it.

---

## 15. Working agreement for the AI session
- Ask the owner only when a decision is expensive to undo; otherwise decide, record it in `DECISIONS.md`, and continue.
- Explain deployment steps in plain language with exact clicks for the Synology Docker GUI; the owner is not a developer.
- Never add steps that modify, rename or delete the owner's manga files.
- Prefer fewer dependencies and less code. Explain any new dependency in one line.
- When a phase is done, give the owner: what changed, how to update the container on the NAS, and what to test on the phone.

---

## 16. Plugins (owner decision before phase 1)

The library stays **read-only**; plugins extend the app, never the files.

- A plugin is a Python file or package in `/data/plugins`, a subclass of `app.plugins.Plugin`.
  Loaded at startup and via Settings → Reload plugins; each can be disabled in Settings.
  A plugin that fails to load or crashes in a hook is logged and skipped.
- Capabilities:
  - **Metadata providers:** `search()` / `fetch()`. The built-in AniList and MangaUpdates
    providers (phase 2) are written as plugins in `app/plugins/builtin/` using the same interface,
    so extra sources (MangaDex, Bangumi, …) can be added the same way. Fix-match shows one tab per provider.
  - **Extra fields:** stored in the `plugin_field` table (series_id, plugin, key, label, value JSON),
    shown on the series page. (Phase 3: filterable.)
  - **Scan hooks:** `on_book_scanned` may change parsed numbers; `on_series_scanned` runs after ordering.
  - **Custom actions:** buttons on the series page that run plugin code and return a message.
- Plugins read books and sidecar files through a read-only context object (`ctx`).
- Docs: `docs/PLUGINS.md`; example: `examples/plugins/translation_groups.py`.
