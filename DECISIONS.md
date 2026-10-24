# Decisions

Choices made where the spec was open. Newest at the bottom.

## Phase 1

- **Library stays read-only, plugins included** (owner's choice). Plugins add metadata, extra
  fields, scan hooks and buttons, but all their data goes into the database in `/data`. No
  write-back of ComicInfo.xml, no renaming.
- **Plugins are plain Python files** in `/data/plugins`, loaded at startup or via "Reload plugins".
  No sandbox: plugins run with the app's rights. The read-only mount is what protects the files.
  A broken plugin is logged and skipped. The built-in AniList/MangaUpdates providers (phase 2)
  will use the same `Plugin` interface (`app/plugins/builtin/`).
- **Plugin fields** are stored in a separate `plugin_field` table (series, plugin, key, value)
  instead of new columns, so plugins never need database migrations.
- **Sort order inside a series:** volume-only books first (by volume), then chapters (by chapter,
  volume as tie-breaker), then extras (by extra number), then unnumbered files (natural sort).
  The reference parser put volume-only books after chapters and unnumbered before extras; spec 4.3
  says otherwise, so it was changed.
- **Frontend is a single page** (`app/web/index.html` + `app/web/static/{css,js,icons}`), no build step.
- **Pages are resized to fixed widths** (720/1080/1440/1800/2160/2880) picked from the screen size, so
  the disk cache gets reused. Resized pages are WebP. Page URLs include the file's mtime, so the
  browser can cache them forever and still notice changed files.
- **Synology junk folders** (`@eaDir`, `#recycle`, `#snapshot`, `__MACOSX`, dot-folders) are never
  scanned. `@eaDir` holds Synology thumbnails that would otherwise become fake books.
- **Empty series folders** are not shown until they contain a book.
- **Missing files** are hidden immediately and deleted from the database (with their progress) after
  30 days. If a whole library root disappears (share not mounted), nothing is marked missing.
- **Root vs PUID:** the container starts as root, switches to PUID:PGID only if that user can read the
  first library folder, otherwise stays root and logs why. Synology users rarely have uid 1000, and
  a library the app cannot read would look like an empty library.
- **No service worker.** Chrome only allows true "installed app" mode over HTTPS; on
  `http://192.168.1.215` "Add to Home screen" gives a shortcut and the reader uses the Fullscreen
  API instead. The Wake Lock API also needs HTTPS, so screen-awake only works via HTTPS (e.g. Tailscale serve).
- **Vertical reader tracks the page in the middle of the screen** (not IntersectionObserver), because
  lazy-loaded images shift the layout and made the counter jump ahead, which could mark a book read too early.
- **A completed book stays completed** when re-read from the start; only "Unread" clears it.
- **Migrations** are a hand-written list in `app/db.py` (`MIGRATIONS`) with a `schema_version` table.
  New databases are created from the models directly.
- **Local Python 3.14 for development**, Docker image uses Python 3.12 as the spec says.

## Phase 2–3 (metadata, filters, needs attention)

- **AniList was disabled by AniList itself on 2026-10-21** ("temporarily disabled due to severe stability
  issues", HTTP 403). So matching never depends on one provider: AniList first, then MangaUpdates.
  A provider that fails is skipped for 15 minutes (one log line, not one per series).
- **Fallback matches get upgraded.** A series matched via MangaUpdates only because AniList was down is
  flagged (`metadata_source.retry_primary`); every 6 hours the worker tries AniList again for those.
- **All providers down ≠ failed.** The series stay "unmatched" and the whole queue is retried after
  30 minutes, instead of flooding Needs Attention.
- **MangaDex not added yet**: its API also returned 503 on the same day; it stays a phase 5 item.
- **MangaUpdates data mapping:** `type` gives format/country (Manhwa → KR …), the native title is the
  first associated title in that country's script, `bayesian_rating × 10` is the score, the top 20
  categories by votes become tags. MangaUpdates has no "English title" field, so that stays empty.
- **Search results carry full AniList data**, so an auto match costs one AniList request (+1–2 for the
  MangaUpdates link), not search + fetch.
- **Reading status is computed in SQL** (one aggregate subquery) so it can be filtered and sorted.
- **Filter state is in the URL hash** (`#/?genre=Romance&exclude=Horror&read=reading`). Tag chips cycle
  include → exclude → off. Sort moved from the top bar into the filter sheet.
- **Needs Attention badge** counts failed matches, unreadable files and duplicates. Chapter gaps and
  unsupported formats are listed but not counted (often expected).
- New DB columns (`match_candidates`, `metadata_source`, `banner_url`) are added by migration 1.

## Archive fixes (from the owner's real errors)

- **Format from the first bytes, not the extension.** ZIPs named `.rar` and RARs/7z named `.cbz` are common.
- **7-Zip as backup reader** (`7zip` + `7zip-rar` in the image): Python's zipfile can't decompress
  Deflate64/PPMd (Windows/WinZip use them for big files) and gives "compression method is not supported".
  7-Zip also gives `.7z/.cb7` support and a second RAR reader.
- **Archives inside archives** (volume zip holding chapter zips) are read one level deep; pages are named
  `inner.zip!/001.jpg` and sorted naturally. The last inner archives are kept in memory (150 MB max).
- **A broken first page no longer breaks the book**: the thumbnail is taken from the first of 5 pages that decodes.
- **"No pictures found" now lists what the file contains**, so the owner can see why.
- **Books with errors are re-checked on every scan** (a replaced file clears its error), without marking the
  series as "updated" when nothing changed.
- More image types: `.jpe/.jfif/.bmp/.tif/.tiff`; TIFF pages are converted to WebP because browsers can't show TIFF.
- MIME types for `.webp/.avif` are registered at startup: the slim image has no `/etc/mime.types`.

## UI on Alpine.js

- **The UI uses Alpine.js 3.14.9** (owner's choice, allowed by the spec). Markup lives in `index.html`,
  data and actions in `app.js` (one `Alpine.data` component per page, one `Alpine.store("app")` for the
  route and shared state). Alpine is vendored in `static/js/vendor/alpine.min.js`: no CDN, works offline on the LAN.
- **The reader stays plain JavaScript** (`reader.js`): touch, swipe, zoom, preloading and scroll
  tracking are direct browser work. The router calls `Reader.open()` / `Reader.close()`.
- `common.js` holds helpers both use (`api`, `toast`, `settings`).
- **All pages are in one document** and shown with `x-show`, so the library keeps its loaded cards.
  Scroll position is handled by the router (`history.scrollRestoration = "manual"`): new pages open
  at the top, returning to the library restores its old position.
- Templates use `x-text` everywhere, so provider text can't inject HTML (no manual escaping needed).
- "Unread" on a single book now deletes its progress, so Continue doesn't jump to that book.

## CSS, settings, folders, formats

- **CSS split per feature** in `app/web/static/css/` (tokens, base, layout, library, series, match,
  attention, settings, reader), written with native CSS nesting.
- **Overrides without breaking things:** every built-in file is inside `@layer`. `/custom.css` (plugin
  `css` + `docker/mangavibe/custom.css`) is unlayered and loaded last, so it always wins without
  `!important`. Re-theming is done through the tokens in `tokens.css`.
- **Folder picker is server-side.** A browser's native folder dialog shows the phone/PC's own files,
  not the NAS, so Settings lists folders inside the container (system folders and `/data` hidden).
- **Removed libraries stay removed**: remembered in the `removed_libraries` setting, so a folder from
  `LIBRARY_PATHS` isn't re-added on the next start. Adding it again clears that.
- **Sort and filter are two menus.** Picking a sort closes its menu; the filter menu stays open while
  you select several things. Changing pages closes both.
- **EPUB:** pages follow the book's spine (reading order), not file names. A book whose spine has more
  text pages than pictures is a light novel: listed under "Not supported" until the novel reader exists.
- **PDF:** rendered page by page with PyMuPDF at 1600 px wide (JPEG). PyMuPDF is AGPL-licensed, which
  is fine for a personal, self-hosted app.
- **7z/cb7** were already handled by the 7-Zip program in the image.

## Reader gestures, manual chapter numbers, per-series rescan

- **Paged reader zoom/pan is our own code** (Pointer Events + a CSS transform on the page), not the
  browser's page zoom, so the controls stay put. Pinch 1–5×, one-finger pan when zoomed (clamped so no
  empty space shows), double tap 2.5× at the finger, mouse wheel on desktop. Zoomed past 1.6× the page
  is swapped for the full-resolution image so it stays sharp.
- **🔒 lock** turns zoom and moving off and returns to the fitted page. It's a per-device preference
  (localStorage), because it depends on the phone, not on the library.
- **Page turns slide**: on a swipe the page follows the finger, slides out and the next one slides in
  from the other side; taps and arrow keys use the same slide. Short swipes snap back.
- **Tap zones are separate from the reading direction** (issue #38, like Mihon's "invert tapping").
  Direction still sets page order, swipe, slide, slider and arrow keys; Settings → Reader can invert
  the tap sides (RTL: tap right for next) or pick a one-handed layout (everything is "next" except a
  narrow "previous" edge and a middle box for the controls) or turn tapping off. Arrow keys don't
  follow the invert: they point at the page on that side. Vertical mode has no tap paging, so
  nothing changes there.
- **Manual numbers per file** (Files tab → Edit): volume, chapter, "to chapter" (a range in one file),
  extra, hidden. Stored in the database next to the file's path (`override_*`, new column
  `override_chapter_end` via migration 2), never in the file. Only values that differ from the file
  name are stored, so "Use file name again" clears everything. They survive rescans.
- **Rescan this series** re-opens every file of that series (not only changed ones) and re-orders it.
  It waits for, or refuses during, a full library scan so the two never write at the same time.
- **Read state colours** in the book list: green edge + ✓ for read, accent edge + progress bar for started.

## Editing, posters, icons, Settings layout

- **Edit series** (✎ Edit, or click the poster): every field the owner changes is added to
  `locked_fields` and never overwritten by refresh/auto-match; "Unlock" hands fields back.
  Only changed fields are sent, so untouched ones keep following the provider.
- **Poster upload security** (`app/posters.py`): max 10 MB (never more is read), max 40 MP (no
  decompression bombs), must decode as JPEG/PNG/WebP/GIF/BMP/AVIF, always re-encoded to a new JPEG +
  WebP (EXIF and anything hidden is dropped), stored as `covers/<id>.jpg` regardless of the uploaded
  name. Provider covers and book-page posters go through the same function.
- **Link icons** are fetched by the server, not the browser (no tracking, no mixed content), only from
  public internet addresses (checked again on every redirect, so no NAS/LAN pages), size-limited,
  re-encoded to a 32×32 PNG and cached per host for a week. SVG icons (MangaUpdates only has one) are
  drawn to PNG with PyMuPDF; the SVG itself is never served, so scripts in it can't run.
- **Needs attention moved into Settings**, which now has a sidebar (a scrolling strip on phones).
  The badge sits on the Settings tab. Old `#/attention` links redirect.
- **Duplicates** are one card per chapter with the files side by side (thumbnail, pages, size,
  Open, Hide this one).
- **Ignoring rows**: errors, duplicate groups and chapter gaps can be marked as false alarms. Keys are
  stored in the `attention_ignored` setting; "Show ignored" brings them back.
- **One stylesheet link**: `css/main.css` declares the layer order and `@import`s the feature files and
  `/custom.css`. Static files are sent with `Cache-Control: no-cache` (browsers re-check, usually a quick
  304), so there are no `?v=` numbers to bump by hand.
- **Reader with a mouse**: only the left button starts a gesture; holding it moves the page when there's
  room to move; a drag never turns pages and never counts as a click; the browser's own image dragging
  is turned off (Firefox ignores the CSS rule, so also `draggable=false`).

## Review fixes

- **Upload size is limited before parsing**: an ASGI middleware refuses `/poster/upload` bodies over
  10 MB (by Content-Length, or by counting chunked bodies) with 413, so nothing big is spooled to disk.
- **Icon fetches connect to the IP that was checked** (Host header and TLS name stay the real host), so
  DNS rebinding can't turn a public name into a LAN address between check and connect. "No icon" is only
  cached when the site really answered; outages and 5xx are retried on the next view.
- **The badge polls `/api/attention/count`**, which reads only flagged rows; the full lists (they need
  every book for chapter gaps) load only while Settings is open.
- **Genre/tag filters use one URL parameter per tag** (`genre=a&genre=b`): tag names may contain commas.
- Library cards take their read counts from the same aggregate query used for filtering and sorting.

## Auto-update

- **Code updates without a new image** (issue #5). The image is the base (Python, unrar/7zip, pip
  packages); the app's code is downloaded from the newest GitHub release into
  `/data/code/<commit>/` and `docker/entrypoint.sh` starts it from there (`/data/code/current`).
  It survives a container Reset because it lives in `/data`. Repo: `UPDATE_REPO`;
  `AUTO_UPDATE=0` turns it all off.
- **GitHub releases, not a `releases` branch.** Releasing used to be a PR from `main` into
  `releases`; now it is one `gh release create`. The tag (e.g. `v1.4.0`) is what users see, the
  release page is the "What's new" link, and pre-releases are not offered. Versions are still
  stored by commit (`/data/code/<commit>/`), so the entrypoint and older installs work unchanged.
  The `releases` branch is kept, frozen at the first version that reads releases: installs from
  before that still poll the branch, update to that version, and from then on follow releases.
- **New pip packages are installed by the app** with `uv pip install --target /data/code/<commit>/deps`,
  only the lines that differ from the image's `requirements.txt`. uv picks the right wheel for Intel
  or ARM by itself. The image has uv instead of pip: pip bundles old copies of urllib3, msgpack and
  setuptools that security scanners flag.
- **A changed Dockerfile or entrypoint needs a new image**: the app compares them (line endings
  ignored) with copies kept in `/opt/mangavibe/image` and refuses the update with a message.
  GitHub Actions builds `ghcr.io/ivokolarov0/mangavibe` and Docker Hub `ivokolarov/mangavibe`
  (amd64 + arm64) for every published release, tagged `latest`, the version (`1.4.0`) and the
  commit, so published images always match a released version. Pre-releases don't get `latest`.
  Docker Hub is the install path in README.md because most Docker GUIs (e.g. Synology) search it by default.
- **Safety**: a version is installed only if `import app.main` works in a separate process. After
  install the process re-runs the entrypoint with `exec`, so it works without Docker's auto-restart.
  A version that fails to start 3 times is replaced by the previous one by the entrypoint and
  remembered as failed. Database migrations only go forward, so going back is a last resort.
- **Daily check, user decides**: the server asks GitHub once a day (unauthenticated API, 4 requests);
  the page shows a toast that stays until **Update now**, **Skip this version** (stored on the server,
  for all devices) or **Later** (this browser, one day).


## Library filters: A–Z and filter panel (issue #48)

- **First-letter filter** (`letter=B`, `#` for the rest) is done on the server on the displayed title,
  so it follows Title language and falls back to the folder name. Titles in other scripts (Japanese,
  Korean…) and those starting with a number or symbol are under `#`. Empty title fields are now skipped
  in SQL like on the cards, so sorting and the letter match what is shown.
- **Filter, Sort and Letter are a toolbar in the library**, not icons next to the search (it wasn't clear
  what ⚲/⇅ were). Labelled buttons with a funnel, up/down arrows and "Az"; a button with something set gets
  an accent outline. Phones: above the quick filters; wide screens: same row, on the right.
- **Letters open in their own sheet** (# first, then A–Z, 48 px buttons), because a 27-letter strip was too
  small to tap on a phone. Picking a letter closes it, like Sort; the letter also shows as a `Starts with B` chip.
- **Filter sheet groups fold** (accordion, not separate modals: one place, fewer taps). Reading status is
  open by default, and any group with something selected opens when the sheet opens. Each heading shows
  how many options are on. Chips say their state with a symbol too (✓ include, ⊘ exclude), not colour only.


## Plugin API v2: reading events, plugin settings, callbacks (for tracker sync)

- **Tracker syncs (MyAnimeList, AniList…) are plugins, not built in.** Each lives in its own repo as one
  `.py` file the owner drops into the plugins folder. The app only gained general hooks, nothing
  site-specific.
- **Three reading events**: book started, book completed, series status changed (the status the app
  shows: manual choice, else worked out from progress). They fire only for the owner's own changes,
  not scans (a new chapter turning "completed" into "reading" isn't something the owner did) and not
  `ctx.set_reading_status` (so a pull from a tracker can't push itself back).
- **Events run on one background thread, after commit**, in order, with a fresh database session per
  plugin. Reading never waits for a website; a crashing plugin is logged and skipped. No retry queue:
  a tracker plugin can offer a "Sync all" button to catch up.
- **Plugin settings are text boxes** declared by the plugin (`SettingDef`), saved in the plugin's own
  settings. Secret values are never sent back to the browser. Buttons in Settings (`global_actions`) and
  a status line cover "Connect", "Sync all", "Connected as …".
- **`/api/plugins/<id>/web/<path>`** lets a plugin answer a GET itself (OAuth callback). `ctx.web_url()`
  builds the address from the last request's host, because the app doesn't know its own address
  (NAS IP, Tailscale, domain…).


## Plugin dropdowns on the series page

- **A plugin can show a dropdown** (`Choice`) under the reading status, e.g. the status on a tracker
  site, so the owner can change it there without opening the website. The plugin says which option is
  selected (`get_choice`) and is called when the owner picks one (`set_choice`).
- **`get_choice` must not call a website.** It runs every time the series page opens; it reads what the
  plugin saved. By default it's the plugin field with the same key, and that field is hidden while the
  dropdown shows, so the value isn't listed twice.
- **`get_choice` can return `False`** to hide the dropdown for one series (e.g. no MyAnimeList id), and
  `None` for "unknown". A crashing `get_choice` only hides its dropdown; the page still opens.
- **`ProviderError.status`** carries the HTTP code, so a plugin can tell "not found" (e.g. removing
  something that's already gone) from a real failure.


## Plugin updates from GitHub releases

- **A plugin names its repo** (`repo = "owner/name"`) and is updated from that repo's newest published
  release: the file with the installed file's name, at the release's tag. Releases, not the main
  branch, so a merge doesn't reach users until the author publishes it, the same as the app itself.
- **Never installed by itself: Update or Skip.** A plugin is code with full access to MangaVibe's data,
  so new code only goes in when the owner presses Update. The daily check (which follows the app's
  "Check for updates every day" setting and `AUTO_UPDATE=0`) only looks. A skipped version isn't
  offered again; a newer release is.
- **The new file is loaded on its own first** and must define a plugin with the same id; otherwise
  nothing changes. It's compiled from its text, not from a cached .pyc, which could belong to an
  earlier download of the same size and time. The old file goes to `plugins/.backup/`.
- **Only single-file plugins in the plugins folder** are updated. Built-in plugins come with the app;
  package folders would need a different download.
