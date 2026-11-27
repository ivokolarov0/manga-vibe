# Decisions

Choices made where the spec was open. Newest at the bottom.

## Phase 1

- **Library stays read-only, plugins included** (owner's choice; since replaced for one case: see
  [Delete series](#delete-series-the-one-way-the-app-changes-the-library)). Plugins add metadata, extra
  fields, scan hooks and buttons, but all their data goes into the database in `/data`. No
  write-back of ComicInfo.xml, no renaming.
- **Plugins are plain Python files** in `/data/plugins`, loaded at startup or via "Reload plugins".
  No sandbox: plugins run with the app's rights. The read-only mount is what protects the files.
  A broken plugin is logged and skipped. The built-in AniList/MangaUpdates providers (phase 2)
  will use the same `Plugin` interface (`app/plugins/builtin/`).
- **Plugin fields** are stored in a separate `plugin_field` table (series, plugin, key, value)
  instead of new columns, so plugins never need database migrations.
- **Sort order inside a series:** numbered books by volume, then chapter (a whole volume before its
  chapters, chapters without a volume after all volumes), then extras (by extra number), then
  unnumbered files (natural sort). Earlier all volume-only books came first and chapters after
  them by chapter number, so `v05, v06, v01 ch01` happened in series mixing whole volumes and
  chapters. The reference parser put unnumbered before extras; spec 4.3 says otherwise.
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
- **7-Zip as backup reader** (`7zz`, the 7-Zip project's static Linux build, checksum pinned in the
  Dockerfile; it was Debian's `7zip` + `7zip-rar` until Debian stable fell behind on 7-Zip fixes
  and scanners kept flagging it): Python's zipfile can't decompress
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
  text pages than pictures is a light novel: a text book (format `epub-text`).
- **Light novels are read by a plugin, not by the app** (Plugin API 14). The app only does what every
  reader needs: the scan keeps a text book as a book, its spine documents ("sections") count as its
  pages, so progress, Continue reading, Read/Unread, reading events and tracker sync work unchanged;
  the thumbnail is the EPUB's cover. A plugin with `reads = ["epub-text"]` and a reader from
  `MangaVibe.registerReader()` gets the whole reader screen; the app still owns opening, leaving,
  saving progress, Android back/volume keys and the wake lock. Without such a plugin switched on, text
  books are listed under "Not supported" again (worked out per request, so switching the plugin needs
  no rescan). Reaching the last section doesn't mark a text book read (it's a long chapter, not a
  page): the reader says `completed`. Where inside a section the reader is, is the plugin's own data.
- **A series of light novels matches the novel.** The matcher's penalty for `NOVEL` results turns
  around when most of a series' books are text books: then manga adaptations score lower.
- **PDF:** rendered page by page with PyMuPDF at 1600 px wide (JPEG). PyMuPDF is AGPL-licensed, which
  is fine for a personal, self-hosted app.
- **7z/cb7** were already handled by the 7-Zip program in the image.

## Reader gestures, manual chapter numbers, per-series rescan

- **Paged reader zoom/pan is our own code** (Pointer Events + a CSS transform on the page), not the
  browser's page zoom, so the controls stay put. Pinch 1–5×, one-finger pan when zoomed (clamped so no
  empty space shows), double tap 2.5× at the finger, Ctrl + mouse wheel on desktop (issue #80). Zoomed past 1.6× the page
  is swapped for the full-resolution image so it stays sharp.
- **🔒 lock** turns zoom and moving off and returns to the fitted page. It's a per-device preference
  (localStorage), because it depends on the phone, not on the library.
- **Page turns slide**: on a swipe the page follows the finger, slides out and the next one slides in
  from the other side; taps and arrow keys use the same slide. Short swipes snap back.
- **Tap zones are separate from the reading direction** (issue #38, like Mihon's "invert tapping").
  Replaced by "No right to left" below: the invert went with the direction.
  Direction still sets page order, swipe, slide, slider and arrow keys; Settings → Reader can invert
  the tap sides (RTL: tap right for next) or pick a one-handed layout (everything is "next" except a
  narrow "previous" edge and a middle box for the controls) or turn tapping off. Arrow keys don't
  follow the invert: they point at the page on that side. Vertical mode has no tap paging, so
  nothing changes there.
- **Inverted taps are the default** (owner's choice, with the Android app): in right to left, tapping
  the right side turns forward, which is where the thumb is. Off gives the paper-book layout (left = next).
  The switch only applies to right to left: left to right always turns forward on the right (with
  the old "swap both ways", an inverted default would have sent LTR books backwards on a right tap).
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
  The badge sits on the Settings button (⚙, top right of the header; issue #107). Old `#/attention` links redirect.
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
- **Going back on purpose** (issue #181): Settings → Updates lists earlier releases (two GitHub
  requests, only when the owner presses Show versions: releases, and tags for their commits), plus
  the versions still in `/data/code` and the image's own code, which need no download. Releases newer
  than the running one aren't listed; that's what Update is for. The version left becomes the
  skipped one (when it's the newest), so the toast doesn't offer it straight back.
- **The database across versions**: migrations still only go forward. Every install whose version
  has more migrations than the database copies it first (SQLite's backup, to
  `/data/code/.db-backups/schema-<n>.db`, newest 3 kept). Each version's migration count is found when
  it's tested (`import app.main` in a separate process) and kept in its `.info.json`. Going back to a
  version with fewer migrations needs a choice: put back the copy at its count (changes since then are
  lost), or keep the newer database (older code ignores columns it doesn't know, and its own
  migrations don't run again because `schema_version` is higher, but data a newer migration rewrote may
  confuse it). Without a copy, only "keep" is offered. The copy is put back right before the restart,
  after the switch, and a failed restore undoes the switch.


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
- **Length filters on the published totals** (metadata `chapters_total`/`volumes_total`), not the books
  on disk: simpler, and it's what tells a quick read from a long run. Owned counts can come later as
  an aggregate over books. Series with no total (ongoing, no metadata) don't match a range unless
  "Also show series with no known total" is ticked (`length_unknown=true`).


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
- **Permissions are declared, not enforced.** A plugin lists what it uses in `permissions` (short
  sentences). An update that adds an entry is installed only after the owner approves exactly the new
  ones (the install answers 409 with the list; the browser asks and sends them back). Python code in
  the same process can't be fenced in, so they tell the owner what changed rather than claim to stop
  anything.
- **Only single-file plugins in the plugins folder** are updated. Built-in plugins come with the app;
  package folders would need a different download.
- **Going back** (issue #181): the copies updates keep in `plugins/.backup/` can be put back with no
  download, and any published release can be installed by its tag (older ones too). Both go through
  the same checks as an update (loads, same id, new permissions approved), and the installed version
  is kept in `.backup/` in turn, so going forward again works the same way. Going back from the newest
  release skips it, so the daily check doesn't offer it straight back.

## A page per plugin

- **Settings → Plugins is a list; each plugin's settings are on its own page** (`#/settings/plugin-<id>`).
  With the settings, buttons and updates of every plugin under it, the list got too long to scan. A
  plugin gets a page (and a **Settings** button) only if it has settings, buttons or updates; the
  built-in metadata plugins have nothing to set, so they stay one row with a toggle. The row still shows
  the status line, and *Update X* when a new version is out, so nothing important hides behind the
  button. **Uses:** (the permissions) is a bullet list, on the plugin's page, or in the row for a
  plugin without a page; joined with dots it was a wall of text.

## Plugin API v3: drawing on reader pages

- **MangaVibe draws, plugins decide what.** The app only knows "boxes with text over a page"; OCR,
  translation or anything else lives in a plugin (the translator is a separate repo with a server on
  a PC with a GPU). Nobody gets those models unless they install that plugin.
- **Boxes are fractions of the page**, not pixels, so they fit every page width the reader asks for
  and every zoom. In paged mode the overlay layer copies the image's size and transform (watched with
  a MutationObserver), so zoom, pan, swipe and the page slide move it without being changed.
- **The reader asks per page, the plugin answers fast.** `page_overlay` reads what the plugin saved;
  a page that isn't ready answers `pending` and is asked again every 2.5 s while it's still being
  read. Asking is also the plugin's signal of what you're reading, so it can work on that page (and
  the next ones) first. No push channel: polling one page every few seconds costs nothing.
- **A switch per series, on the device.** Translation is wanted for the raw Japanese series, not for
  the rest, and it's a reading preference like the zoom lock, so it lives in localStorage.
- **Per-page data in its own table** (`plugin_page`), deleted with the book and when its file changes
  on disk (a forced re-read of an unchanged file keeps it), so a translation never belongs to an older
  version of the file.
- **Threads of the plugin's own** (`ctx.run_in_thread`). The shared plugin worker runs events one at a
  time; a translation queue that runs for an hour would hold up every tracker sync behind it. The
  thread watches `ctx.should_stop()`, which a reload, update or switch-off sets, so old plugin objects
  don't keep working after their file was replaced.
- **Book buttons sit in the reader**, not on every row of the book list: "translate this chapter" is
  something you want while reading it. Badges on the book list show how far a plugin got.
- **Hiding a box is the app's job, not the plugin's.** A wrong translation over the art should go away
  whatever plugin drew it. The × only shows while the reader's controls do, so it never sits on the
  text while reading, and it goes to the box corner away from the nearer bar (title bar at the top,
  controls card at the bottom) so the bar can't swallow the tap. Hidden boxes are kept on the server
  (all devices), keyed by position, so they stay hidden when the plugin redoes the page.
- **How boxes look is a reader setting, not a plugin one** (issue #78). Box opacity and a white outline
  around the text apply to every overlay plugin, so they're two reader settings (Settings → Reader,
  and quick switches in the reader options while an overlay is on), set as CSS variables on the
  reader (`--ov-bg-opacity`, `--ov-text-stroke`). The background is `color-mix()` of `--ov-bg` with
  transparent, so a plugin that colours its boxes with `--ov-bg` keeps the opacity setting. The outline
  is `-webkit-text-stroke` with `paint-order: stroke fill`, so it sits behind the letters, and the
  text gets padding of half the stroke so the fitted text isn't cut off at the box edge.
- **Per-series data of the plugin's own** (`plugin_series`, plugin API 4). A translator keeps notes on
  the characters and the story from chapter to chapter; `PluginField` would show them on the series
  page, and the plugin's settings are one blob the owner's Save rewrites. So a separate table, keyed
  by plugin, series and key, never shown, deleted with the series.
- **A page note in the top bar** (`Overlay.note`, plugin API 4), e.g. "Full context · Haruka, Taro".
  `message` is a status pill over the page, right for "Translating…" but too much for a line that's
  there on every page. The note sits under the chapter's name in the top bar, so it only shows while
  the controls do.
- **Live progress by asking, not pushing.** A translation queue changes the book labels and the plugin's
  status line for minutes. The series page asks a labels-only endpoint (`/api/series/<id>/badges`)
  every 3 s, and Settings asks for the status lines; after a minute without a change it's every 30 s
  (15 s in Settings), and nothing while the tab is hidden. Same reasoning as the reader's overlays: no
  push channel to keep alive, and a small query every few seconds costs nothing.

## Accessibility: read aloud, screen readers, page look (plugin API 5)

- **The plugin writes the words, the reader speaks them.** `Overlay.speech` is the page as plain text in
  reading order (e.g. the Manga Accessibility plugin's "Panel 1. … Haruka says: …"). A plugin is Python on
  the server and can't make sound on the reader's device, and a voice on the server would mean audio
  files per page; the device's own voice costs nothing and the reader picks it. Without `speech`, read
  aloud reads the boxes' text, so a translator gets it for free.
- **Browser speech synthesis, and Android's TextToSpeech in the app.** The app's WebView has no
  `speechSynthesis`, so the app adds `speak(id, text, rate)` / `stopSpeaking()` to its bridge and calls
  `MangaVibeApp.spoke(id)` when done. An older app without them shows no Read aloud switch.
- **Short parts.** Chrome stops one long utterance after about 15 s, so the text goes as one utterance
  per line (long lines cut after a sentence). Every stop raises a counter, so an `end` from a reading that
  was cancelled (Chrome sends one) never turns a page. The part being read is remembered (the browser's
  `start` event, the app's `MangaVibeApp.speaking`), so a new speed or voice carries on from that line at
  once; queued utterances keep the speed they were given.
- **Read once per page; "Turn pages by itself" goes on to the next page and chapter**, so a chapter can be
  listened to without touching the screen. A page with only a problem message (e.g. "set the server")
  is said but never turned, or a broken setup would skip through the whole book.
- **Screen readers get the same text** in a hidden `aria-live` region (TalkBack, NVDA, VoiceOver read it in
  the owner's own voice and speed). It stays empty while Read aloud is on, or the page would be heard twice.
- **The reader's options keep one row per thing, each opening a panel.** With plugins, page look and
  read aloud, the drawer had grown to twenty buttons. Now it has a **Page look** row and, last and after a
  line (so Fullscreen and Reader settings don't look like plugins), one row per plugin under **Plugins**
  with its On/Off. A row opens a panel that slides down over the drawer. **Everything a plugin brings is in
  its panel**: its switch, its book buttons, Read aloud (it reads the plugin's text) and the look of its
  boxes. Read aloud and the box look are one setting for all plugins, shown in each panel. The panel is
  built from what `/api/books/<id>` already lists (overlays and book actions, now with the plugin's
  `name`), so plugins don't change. The panel's ×, Escape, the Android back button or a tap on the faded
  drawer go back one step, to the drawer.
- **Each series has a reading direction, worked out when it isn't set** (`app/direction.py`). Replaced by
  "No right to left" below: only the series and library folder choices are left. It used to be
  the series' own choice or the global default, so a manhwa read right to left until someone noticed, and
  a plugin describing pages couldn't tell manga order from comic order. Now, in order: the owner's choice
  for the series; its metadata, where matched as from Japan is manga (`rtl`) and matched otherwise
  (manhwa, manhua, OEL, western) is `ltr` (by `country_of_origin`, since both sites call a manhwa
  `MANGA`); for a series that isn't matched, its library folder's direction (`library.reader_mode`,
  Settings → Library folders), so a folder of comics can say so; then Settings → Reader (manga unless
  changed). The reader and plugins (`ctx.reading_direction`) use the same answer, and the series page shows
  what "Automatic" comes to and why.
- **Speech settings are per device** (localStorage): voices differ from device to device. **Brightness,
  contrast and colours are reader settings** like the fit, a CSS `filter` on the page images only, so
  overlay boxes and controls keep their colours. "Inverted" is `invert(1) hue-rotate(180deg)` (colours keep
  their hue) and comes first, so lower brightness dims the white lines instead of lifting a black page.

## Android app

- **A WebView wrapper, not a native reader.** The reader is the web app; the app (`android/`) only
  shows the server on the whole screen and adds what a browser can't do over plain HTTP: no browser bars,
  the screen kept on (`FLAG_KEEP_SCREEN_ON`, where the browser's Wake Lock needs HTTPS) and the
  volume keys. New MangaVibe versions reach the app without updating it.
- **Android only.** An iPhone app needs a Mac and a paid Apple developer account, and the App Store
  turns down apps that are only a web view.
- **In this repo, released with the server.** `.github/workflows/android.yml` attaches
  `MangaVibe.apk` to every release, signed with one key from the repository secrets (Android refuses
  an update signed with another key). Not in the Docker image: running apps update their code from
  GitHub without a new image, so a bundled APK would go stale.
- **No Chrome install prompt, an intent link instead.** Chrome's own "install app" banner and
  `getInstalledRelatedApps()` need HTTPS, a Play Store listing and a fixed web address. The banner
  (Android browsers, library page, dismissible) and Settings → Android app use
  `intent://open#Intent;scheme=mangavibe;package=…;S.url=<page>;S.browser_fallback_url=<APK>;end`:
  Chrome opens the app if it's installed, and downloads the APK if not. The page goes along, so the
  app connects to that server by itself on its first start. A link for another server asks first.
- **The page and the app talk through two objects.** The app adds `window.MangaVibeAndroid`
  (`readerState(open, volumeKeys)`, `changeServer()`, `server()`, `version()`), and only answers the
  server's own page. The page adds `window.MangaVibeApp` (`volumeKey(dir)`, `back()`). Links to other
  sites open in the browser.
- **Volume down = next page**, one page per press (holding doesn't repeat). Vertical mode scrolls
  85% of a screen. It's a server setting like the other reader settings (`reader_volume_keys`), shown
  only in the app.
- **Back closes what's open first:** the reader's options, then the reader (as its back button does),
  then any sheet or dialog (they all close on Escape), and only then goes back in history.
- **Plain HTTP is allowed for every address**, since the server is at an address only the user
  knows, usually without HTTPS.

## No right to left: every paged series turns forward to the right

- **Pages or vertical, nothing else.** Digital manga and comics apps all turn forward to the right: → is
  the next page, a tap on the right goes forward, a swipe to the left brings the next page in. A right to
  left mode only made the controls disagree with every other reader (and needed an "invert tap zones"
  setting to undo it), so it's gone. `reader_mode` is `paged` or `vertical`, set on the series, else its
  library folder (since issue #145: its library), else Settings → Reader; the metadata (from Japan or not) no longer decides anything.
  Migration 4 turns stored `rtl`/`ltr` into `paged` and drops `reader_tap_invert`. What stays is the
  reading hand (Settings → Reader): it moves the buttons to the left or right thumb, not the page order.
  `ctx.reading_direction` keeps its name and returns `paged` or `vertical`.
- **A tap in the middle opens the menu.** It used to only show the bars, and the options needed a second
  tap on ☰. Now the bars and the options drawer come up together; a tap beside the drawer closes it and
  the bars hide on their own. Tapping off ("swipe only") and zoomed-in taps do the same.

## Delete series: the one way the app changes the library

- **The owner changed the read-only decision** (issue #104): they want to delete series they don't care
  about without leaving the app. Only that: a whole series folder, when the owner asks. Renaming, moving,
  tagging and converting files stay out of scope, and plugins still can't write to the library.
- **The mount is the switch.** Install docs now mount `/manga` read-write; `:ro` (Synology: Read-only)
  turns deleting off. No separate setting: the mount already says what the app may do, and a setting
  could disagree with it. The dialog tells apart a read-only mount (statvfs `ST_RDONLY`) and a folder the
  app's user (`PUID`) can't write to, since the fixes differ.
- **Only a series folder.** It must be a real folder (not a link) directly inside its library folder; the
  library folder itself and anything deeper are refused. It waits for, or refuses during, a full scan.
- **Folder first, then the database.** If some files can't be deleted (in use, no permission), the series
  stays and is rescanned, so what is left still shows up. Once the folder is gone the series row is
  deleted at once (not hidden for `KEEP_MISSING_DAYS` like a folder that disappears by itself): books,
  progress, tags, links and plugin data go with it through `ON DELETE CASCADE`; its poster, thumbnails,
  cached pages and ignored Needs attention rows are removed too.
- **Plugins** get `on_series_deleted(ctx, series)` just before (plugin API 6), to clean up their own data
  elsewhere.
- **No password, so anyone who reaches the app can delete.** `docs/away-from-home.md` says to mount
  read-only before opening it to the internet.

## Editions: counting volumes against the edition the files are from

- **AniList has one volume count** (usually the regular edition), so a complete kanzenban or
  omnibus set showed as Incomplete. **MangaUpdates lists every edition** in its status
  ("18 Kanzenban Volumes (Complete)") and in each English publisher's notes ("14 Omnibus; 3-in-1");
  these are parsed into `series.editions` (`app/metadata/editions.py`). Google Books and Open Library
  have page counts per printed book but need a key or miss many series, and their titles are messy.
- **Pages per file pick the edition.** The median pages of the volume files is stored in the
  numbering. A regular volume is about 200 pages; files at least 1.3× that are from a bigger edition:
  `regular volumes × 200 ÷ pages` estimates the count, and the nearest listed edition with fewer volumes
  (but not fewer than the highest volume number here) is used. Thinner files keep AniList's count.
- **The owner's pick wins** (Edit → Edition, `edition_volumes`). Not a locked field: a refresh
  updates the editions list but never the pick. AniList's count stays as the series' length.
- **Series matched earlier** get their editions in the background at start (one MangaUpdates request
  each, behind the owner's own requests).

- **Extras aren't guessed from file names** (owner's choice). They are written too many ways
  (`12+extra`, `12+omake`, `12.5`, chapter titles like "Something Special"), and a wrong guess left
  the chapter out of the count. A name's number is its chapter; the owner marks extras by hand.

## Libraries: Manga, Comics... apart from the folders (issue #145)

- **A library is a shelf in the code.** `Library` has been a scanned folder from the start, and plugins
  read `series.library_id` as the folder, so that stays. The new idea, what the app calls a *library*,
  is `Shelf` (table `shelf`; since Plugin API 8 the built-in plugin `app/plugins/builtin/libraries/`, see
  [Libraries as a built-in plugin](#libraries-as-a-built-in-plugin-plugin-api-8)). Folders and series point to one:
  `Library.shelf_id` (every folder has one) and `Series.shelf_id` (only when the owner moved it; None
  follows the folder). The SQL for "the series' library" is `shelves.shelf_column()`.
- **Moves never touch files and survive rescans.** The scanner never writes `Series.shelf_id`, so a
  rescan or restart can't move a series back. Edit, the multi-select's "Move to library" and the
  folder's Library setting are the only ways to change it.
- **Upgrade: one library per folder, named like it**, with the folder's old reading mode (migration 14
  adds the columns, `ensure_folder_shelves()` fills them at start). An install with one folder sees one
  library and nothing else changes. Two folders with the same name get two libraries ("manga (2)"),
  each keeping its own mode. The folder's `reader_mode` is cleared once copied, so it can't come back.
- **New folders get their own library too** (`shelf_for_folder()`), unless the owner already made one
  with that name and no folder in it ("Comics" made before adding /comics). `made_for_folder` marks the
  ones the app made: removing the folder removes such a library while it's empty, unless the owner
  renamed it, set its mode or gave it a password; a library the owner made always stays.
- **Deleting a library deletes no series.** Its folders go to another library (one without a password if
  there is one), series moved to it follow their folder again. The last library can't be deleted.
- **Reading mode per library, not per folder:** series, then its library, then Settings → Reader
  (`app/direction.py`). Right to left stays gone (see above), so it's only Pages or Vertical.
- **Home screen tabs.** The libraries are tabs above the library grid and Continue (with + to add one).
  With one library it's picked and "All" is left out; with more, "All" comes first and the last pick is
  remembered in the browser. The grid, filters, tag counts, Continue reading and Needs attention follow
  the pick (`shelf=` on the API).

## Library passwords

- **No accounts, so a password per library.** It keeps a library private from others using the same app;
  it isn't protection from anyone who can read the files or `/data`. Settings → Libraries says so.
- **Only a hash is stored**: `hashlib.scrypt` (n=2^14, r=8, p=1, 16-byte salt), as
  `scrypt$n$r$p$salt$hash`. Changing or removing it needs the current one.
- **Unlocking gives the browser a signed token** in the HttpOnly `mv_unlocked` cookie, one per library:
  `id.lock_version.expiry.hmac`, signed with a random key kept in the `unlock_secret` setting, good for
  12 hours. A session cookie, so closing the browser forgets it. A changed password raises
  `lock_version`, which makes every old token useless.
- **"Until the tab closes"**: cookies can't follow a tab, so the page itself sends "lock everything" when
  a new tab starts (a flag in sessionStorage, which a reload keeps). "Lock now" takes the token out.
- **The server checks, not only the page.** `LockGuard` (ASGI middleware) refuses everything under
  `/api/series/<id>`, `/api/books/<id>` and `/api/links/<id>` of a locked library's series: details,
  pages, thumbnails, posters, progress, plugin buttons, link icons, changes. The lists (grid, search,
  tags, Continue, Needs attention, counts) leave its series out (`shelves.visible()`). Renaming it,
  changing its mode, deleting it, moving its folder away or removing that folder all need it unlocked,
  since each would show or remove its series. The 403 says which library, so the page asks for its
  password and carries on.
- **Cheap for the reader.** LockGuard does nothing until some library has a password. After that it keeps
  the protected libraries and the library of each series, book and link it looked up for 10 seconds, and
  every move, password change or delete clears that at once; pages and thumbnails mostly don't touch the
  database.
- **Guessing is slow**: after 5 wrong passwords in a row each try waits (30 s, doubling, up to 15 min),
  per library, in memory.
- **Forgot it:** `CLEAR_LIBRARY_PASSWORD=<name or all>` for one start (the main way: it works whatever
  code the auto-update runs), or `python -m app.plugins.builtin.libraries.core clear-password <name>`.

## Saved searches instead of filter libraries

- **Tried first: a library made of filters** ("Isekai": the series of a library that match saved filters),
  with its own tab. The owner found it looked wrong next to real libraries, so a set of filters is
  a **saved search** instead: a name for the library page's query string (filters and sort, not the page),
  in the `saved_searches` setting (now the built-in plugin `app/plugins/builtin/saved_searches/`, see
  [Features as built-in plugins](#features-as-built-in-plugins-plugin-api-7)). The Saved button next to Filter / Sort / Letter
  lists them; picking one sets them on whichever library tab is shown. The same name again replaces it.
- **No new table**: a short list the page reads whole, like the ignored Needs attention rows.
- **The filters moved to `app/api/filters.py`** (from `app/api/series.py`) so the grid and every other list
  use the same code; `visible_series()` there adds what plugins allow (the libraries plugin: the library
  and lock conditions).

## Plugins and libraries

- **Nothing a plugin uses changed.** `series.library_id` is still the folder; `ctx.reading_direction()`
  now goes series → library → Settings → Reader; the plugin API version stays 6. A plugin's own database
  work (sync all, background queues) still sees every series: it runs on the server, and a password only
  decides what a browser sees.
- **Plugin buttons, overlays and pages of a locked library's series are refused** like the rest of its
  content (they live under `/api/series/<id>` and `/api/books/<id>`), until the library is unlocked.
- Checked against the owner's plugins (accessibility, MyAnimeList sync, official chapters, translate) and
  the examples: none read `Library`, the moved filter functions or the changed page markup. Their
  `/api/tags`, `/api/chat` calls go to Ollama, not to MangaVibe.

## Features as built-in plugins (Plugin API 7)

- **The core is the grid, the series page and the reader; features around them become plugins** that
  come with the app (`app/plugins/builtin/`), so the owner can switch one off in Settings → Plugins and
  anyone can write a replacement. Saved searches is the first; libraries and accounts are next.
- **A plugin can have its own API**: `router` (a FastAPI APIRouter) is served at
  `/api/plugins/<id>/api/...`. One mount (`PluginApi`, app/plugins/web.py) looks the plugin up on every
  request, so Reload plugins and switching one off work at once without restarting; off = 404.
- **And its own UI**: `html` puts markup into named slots of index.html (`<!-- slot:<name> -->`), and `js`
  is served as `/plugins/<id>.js`, run after app.js. The page loads with the switched-on plugins' markup
  already in, so Alpine sees it like its own. One script per plugin, so a mistake in one doesn't stop the
  app.
- **Switching one on or off doesn't reload the page** (first version did; the owner found it odd next to
  the other plugins' switches). Each slot stays in the page as a `display: contents` box and each
  plugin's markup has a box of its own, so app.js adds it (from `GET /api/plugins/<id>/ui`, plus its
  script and `/plugins/<id>.css`) or removes it; Alpine sets up added markup by itself. Scripts register
  in `MangaVibe.onStart()`, which runs at once when Alpine has already started. A script stays loaded
  once it ran; without its markup it shows nothing. Reload plugins still reloads the page.
- **Slots are added as features move out**, not ahead of time: `library-tools`, `library-filters`,
  `sheets` for now. `window.MangaVibe` (app.js) is the only JS a plugin should rely on; the rest of app.js
  can change.
- **Saved searches keep their data**: still the `saved_searches` setting, so nothing is migrated. Its
  address moved from `/api/saved-searches` to `/api/plugins/saved-searches/api/searches`.
- **Plugin CSS stays outside the layers**, after the built-in styles and before custom.css, so the
  owner's custom.css still wins over a built-in plugin's look.

## Libraries as a built-in plugin (Plugin API 8)

- **Libraries moved out of the core** into `app/plugins/builtin/libraries/` (core.py = the old
  app/shelves.py, api.py = the old app/api/shelves.py plus moving series and folders). Switched off, the
  app is one list of everything; the core no longer knows libraries exist.
- **The core got general hooks instead**, the same ones accounts will use:
  - `series_filter(ctx, request)`: SQL conditions for what a request may see, used by every list and
    count (`filters.visible_series`), multi-select and the "is any of these hidden" checks (attention
    ignore, removing a folder). **Not caught**: a failing filter fails the request rather than showing
    what it hides.
  - `middleware`: an ASGI middleware per plugin, chained by `PluginMiddleware` (rebuilt only when the set
    of plugins changes). LockGuard is the libraries plugin's.
  - `reading_mode(ctx, series)` → (mode, why): direction.py goes series → plugin → Settings → Reader;
    the series page shows the why ("the mode of Comics") instead of knowing about libraries.
  - `series_info()` (the series page's `plugin_data`), `on_start()` (start, Reload plugins, switched
    on), `on_folders_changed()`, `on_folder_removed()`, `can_switch_off()`.
- **Data stays where it was** (owner's choice): the `shelf` table and the `shelf_id` columns stay in
  models.py, unused while the plugin is off. No migration, nothing to lose; switched back on, `on_start`
  gives folders added meanwhile their library.
- **Switching it off is refused while a library has a password** (owner's choice), with the names in the
  message: off, those series would show to everyone.
- **Built-in plugins are imported under their real name** (`app.plugins.builtin.<name>`), once, so a
  folder plugin's modules and their state (password caches, the unlock key) exist one time, also after
  Reload plugins. Other folder plugins can now use relative imports.
- **The page:** more slots (home-top, select-menu, library-end, series-edit, settings, folder-row,
  attention-note) and events (`MangaVibe.on`: start, stop, denied, series-info, edit-open/-changed/-save,
  empty-library, folders-changed, settings-open). Lists follow `MangaVibe.setListScope()` (shelf=3 on the
  grid, tags, Continue and Needs attention calls); Settings pages come from `$store.app.settingsNav`.
  A 403 goes to `MangaVibe.denied()`, which the plugin answers by asking for the password.
- **Markup switched on later is set up by the page, not by Alpine's watcher**: added with the watcher
  paused, then `Alpine.initTree` box by box. With the watcher, one failing x-for stopped every other box
  from being set up. Slots inside a repeated part (a folder's row) are filled in the shown copies and in
  their `<template>`.
- **The grid's load key includes `listsDirty`** instead of clearing the key to force a reload: clearing
  it let two loads of the same page run at once and add the same series twice.

## Accounts (Plugin API 9)

Owner's choices: login always required; per account: progress, favorites, saved searches, reader
settings; an admin picks the libraries each account sees (library passwords stay).

- **The core knows "who is asking", nothing about logins** (`app/users.py`). A context variable holds the
  request's user; the accounts plugin's middleware sets it. Without accounts it's the owner (id 1), who
  may do everything, so the app works as before. A context variable rather than a parameter: it reaches
  every query (they run in the request's thread, which copies it) without changing every function.
- **Per-user data in the core**: `progress.user_id` (one row per user and book) and `series_state`
  (reading status set by hand, favorite, last read, per user). Migration 15 moved what there was to user 1
  and dropped the old series columns; progress was rebuilt (SQLite can't drop its one-row-per-book rule).
  `Series.reading_status/favorite/last_read_at` stay as Python properties reading the current user's
  row, so most code didn't change; SQL uses `common.my_state()` / `my_progress_join()`.
- **The first account is user 1**: it gets everything read before accounts, can't be deleted and stays an
  admin (so there's always one). Switched off again, the app is the owner's with that reading.
- **Admins-only is marked in the core** (`dependencies=[users.manage]`): settings other than Reader,
  folders, scans, plugins, metadata and posters, matching, deleting, updates, plugin buttons; the library
  plugin's add/rename/delete/password/move. Everyone else may read, set their own status and favorites,
  mark read, keep saved searches and set Settings → Reader. The page hides what they can't use
  (`$store.app.admin`), but the server is what refuses.
- **Reader settings per user** are `user:<id>:<key>` settings over the shared ones (`personal_key()`);
  saved searches the same. Display settings stay the app's (admins).
- **Reading events only for the owner**: tracker plugins are set up once for the app; another account's
  reading mustn't go to the owner's MyAnimeList.
- **Accounts start switched off** (`enabled_by_default = False`, remembered in `plugins_known`): an update
  must not start asking everyone to log in. Switched on, the next page asks for the first admin; anyone
  who opens the app before that could make it, like most self-hosted apps on first run.
- **Login**: scrypt hashes (`app/passwords.py`, shared with library passwords), wrong guesses slow down
  per name (a wrong name counts too, so names can't be found by trying). A signed cookie for 30 days;
  changing a password raises the account's version, which logs it out everywhere else. Stored in the
  `accounts` setting: a short list, no table, like the plugin settings.
- **Which libraries an account sees** is the libraries plugin's (`libraries_access` setting); its
  series_filter and LockGuard treat a library the account may not see like a locked one that no password
  opens (it isn't even listed). Its checkboxes go into the accounts page through the `account-row` slot,
  a slot inside another plugin's markup, so neither plugin imports the other.

## Watching the library folders

- **The system reports changes** (`app/scanner/watcher.py`, `watchdog`: inotify, ReadDirectoryChangesW,
  FSEvents), like Jellyfin's real-time monitoring, so nothing is read while the folders are quiet. The
  6-hour full scan stays, for anything the watcher can't see.
- **One series folder at a time, after it goes quiet**: events are gathered per series folder (the
  folder right inside the library folder) and it is scanned `WATCH_DELAY` (10) seconds after its last
  change, so a copy in progress is scanned once, when it's done. Only that folder is scanned
  (`walker.scan_folder`), with the same incremental rules as a full scan; a deleted folder hides its
  series. It takes the scan lock without waiting: during a full scan the folder waits for the next round.
- **Reading isn't a change**: opened/closed-without-writing and attribute events are ignored. Windows
  reports file reads as changes all the same, so a scan after a change doesn't reopen broken books
  (that would be a change again, and so on); full scans still re-check them.
- **Polling where the system reports nothing**: network shares (cifs, nfs) don't hear about changes
  made by other computers, and Docker Desktop's Windows folders (9p) report nothing (tried in WSL). On
  Linux, `/proc/self/mountinfo` tells the file system of each library folder; those are checked every
  30 seconds, as is a folder the system can't watch (inotify's limit). `WATCH_FOLDERS=poll` checks every
  folder that way, `0` turns watching off.
- **The library folders are re-read every minute**, so added, removed and remounted folders are picked
  up without hooks in the API.

## Optimize (built-in plugin)

Owner's choices: JPEG XL by default, lossy only; replace the originals once checked, no copy kept;
JPEG pages are converted too.

- **A plugin, off by default** (`enabled_by_default = False`): it's the second thing in the app that
  changes the owner's files (after Delete series), so an update mustn't put a button on every series
  page that does that. It uses the plugin API's own buttons, settings, status line and book badges, so
  it has no markup or script of its own.
- **JPEG XL is fine for the reader**: the reader asks for pages at the screen's width, and those are
  sent as WebP whatever the file holds (and cached), so a JPEG XL page costs ~15 ms more to show once.
  Other comic apps may not read JPEG XL or AVIF; the setting says so and offers WebP.
- **Checked before anything is replaced**: the new book is a hidden `.mvopt-…` file next to the old
  one (scans and the watcher skip dot files). Every converted page is decoded once; the archive is
  read back (`testzip`), and its pages must be the old ones in the old order with the new extension.
  A new extension that would change the natural sort, or two pages that would get the same name
  (`001.png` and `001.jpg`), keep the page or the book as it is.
- **Swapped under the scanner's lock, and the book row updated at once** (path, size, mtime): the next
  scan sees an unchanged book, so the row and everything keyed by it stay: progress, manual numbers,
  plugins' page data (same pages, same order). Before swapping it checks the old file's size and
  mtime against what was converted; a file changed meanwhile is left alone.
- **A page must get 10% smaller** or it stays: no quality lost for a few bytes. On JPEGs saved at very
  high quality (official releases, ~97), *High* (JPEG XL 92) often stays under that, so most pages are
  kept; *Normal* gets them to ~73%.
- **The new file looks like the old one**: EXIF rotation is applied, a colour profile (Adobe RGB, CMYK)
  is converted to sRGB (a profile that can't be read keeps the page), unused alpha is dropped, and a
  page is made grey only when every pixel is grey (checked at full size: a thumbnail averages a small
  coloured title away).
- **A folder book's pages are swapped one by one**: new page in, old one out; an old page that can't be
  removed takes its new one out again, so no page is ever there twice, and the row is updated to what
  was swapped.
- **Read-only, no permission and a full drive aren't remembered** as a book's result (`Blocked`): the
  series stops there, and the books are tried again once that's fixed. Optimize all leaves out library
  folders that can't be written and says why.
- **The queue is changed under one lock, read fresh each time**, by the worker and the buttons alike.
  A button that finds the worker still running (finishing, stopping, or an old plugin object after
  Reload plugins) leaves a note, and the worker starts a new one for the plugin that is on now.
- **RAR and 7z become `.cbz`** (nothing writes them), keeping the book's row; the old file is deleted
  only once the new one is in place, and if that fails the new one goes, so a book is never there twice.
- **Conversion runs without the scanner's lock** (minutes per book); only the swap holds it, waiting
  for a full scan to finish rather than refusing.
- **One worker, half the CPU cores for the encoder**, so reading stays smooth on a NAS. The queue is a
  setting (`optimize_queue`), so "Optimize all" carries on after a restart. Per book, what happened
  is kept as the plugin's series data at the file's size and mtime: a done book (or one with nothing
  to convert) isn't tried again until its file changes; a failed one is retried by "Optimize this
  series", not by "Optimize all".

## Design refresh: two themes, icons, motion

- **Two full themes from the same tokens** (`css/tokens.css`): dark stays the default, light has its
  own accent (deeper, readable as text on white), state colours and softer shadows. `color-scheme`
  follows the theme, so native lists, scrollbars and date fields match. New tokens: `--accent-soft`,
  `--hover`, `--glass`, `--shadow-sm` / `--shadow` / `--shadow-lg`, `--backdrop`, `--radius-pill`,
  `--ease`. `--accent-text` is dark in the dark theme (the coral is light) and white in the light one.
- **Theme setting gets "Match device"** (`theme: auto`), and **the top bar has a sun/moon button**.
  The setting is the app's (admins, everyone gets it); the button is this device's own choice
  (`localStorage "theme"`), so a phone can be dark while the desktop is light, and people who aren't
  admins can pick too. Tapping the button back to what the app's theme gives, or choosing a theme in
  Settings on that device, ends the device's own choice, so it follows the app (and the OS) again.
  `<html data-theme>` is always `dark` or `light` (`applyTheme()` in `common.js`), so custom.css
  rules for `[data-theme="light"]` keep working. An inline script in `<head>` sets the theme used
  last before the first paint (no flash).
- **Later: the sun/moon button left the top bar.** You pick a theme once, so it took room in the bar
  for nothing. The theme moved to Settings → Display only, and became each person's own setting
  (like Settings → Reader, starting from the owner's): people who aren't admins see the Display page
  with just that row. Per-device choices went with the button (the old `localStorage "theme"` is
  cleared, or it would win over the setting with no way to change it); "Match device" covers a
  phone in dark and a desktop in light.
- **One SVG sprite for icons** at the top of `index.html` (`<symbol id="i-…">`, line icons on a 24
  grid), used as `<svg class="ic"><use href="#i-star"/></svg>` or `icon("star")` in scripts. It
  replaces the emoji and text symbols (they looked different on every device and sat off-centre).
  No icon font, no extra request, and they take the text colour. Settings menu icons are sprite names;
  a plugin may still give any character (`navIcon()` shows it as text). The reader keeps its own.
- **Motion is short and only when something appears**: a page fades in, a sheet slides up (a dialog
  grows in on desktop), menus open from their corner, the theme change fades, a favorite's star
  bounces. All of it is CSS (`@keyframes` in `base.css`), and "reduce motion" on the device switches
  it off in one rule.
- **Checkboxes that switch something on are drawn as switches** (a settings row, a plugin row); other
  checkboxes are a rounded box. Both are the real `<input type="checkbox">`, restyled.
- **Bars are see-through** (`--glass` with a backdrop blur), covers have a hairline so white covers
  don't melt into the light page, and the bottom navigation marks the current page with a pill.

## Desktop reader, fullscreen, the controls' auto-hide (issue #80)

- **The wheel scrolls, it doesn't zoom.** A page taller than the window (fit width, original size,
  zoomed) scrolls; at its end the wheel turns the page. A spin of the wheel (events less than
  250 ms apart, a touchpad swipe with its momentum too) either scrolls or turns, never both: the page
  stops at its end, and the next spin turns it. So it can't fly through a chapter, and you never miss
  the bottom of a page. Scrolling back up shows the previous page from its bottom. A page that fits
  turns at once. Ctrl + wheel zooms (a touchpad pinch arrives as Ctrl + wheel). Zoomed in, the wheel
  only moves the page, like taps then don't turn it.
- **A page taller than the window starts at its top**, on phones too (it used to open at its middle).
  Leaving a zoom, or a swipe that snaps back, keeps the place instead of jumping to the middle.
- **Keys:** ← → turn; ↓ ↑, Page Down/Up and Space / Shift + Space go down a page by a step, then on
  to the next (a held key stops at the end of a page); Home/End; F fullscreen. Vertical mode gets the
  scrolling keys too. Keys go to a focused slider or dropdown, not to the page as well.
- **On a computer back and fullscreen are in the top corners**, not stacked over the card by the
  thumb; the mouse at the top edge brings the controls up like the bottom edge, and the pointer hides
  when it rests while reading. Dragging and click zones stay. Two-page spreads are left for later:
  they need the overlays, zoom and the page count to work on two pictures at once.
- **The controls go away when you read on:** turning the page hides them (not while the drawer is
  open or they're in use), and a tap in the middle while they're up hides them instead of opening
  the drawer again. The 5 seconds stay the default: with these two, the timer only matters when you
  leave them up and do nothing.
- **Fullscreen comes back after the phone took it**: when it ends while the page is hidden (locked,
  another app) or just as it comes back (browsers send that event late), the next tap goes fullscreen
  again; browsers allow fullscreen only on a tap. Leaving it yourself (Esc, the back gesture, the
  button) is kept. Going to the next chapter stays fullscreen (the reader isn't closed in between).

## Plugin tools on the page being read (Plugin API 12)

For Manga Translate's editor (change the text, move and style boxes, read text that was missed).

- **The editor is the plugin's, not the reader's.** The reader only gives a plugin's script what it
  can't know itself: a `book_actions` button it may handle in the page (the `book-action` event; a
  handler that returns `true` keeps the server out of it), what is being read
  (`MangaVibe.reader.where()`), and a way to draw the overlays again (`MangaVibe.reader.redraw()`).
  Before, the plugin had to find these in the reader's markup, which can change in any version.
- **No button without the plugin.** The button is one of the plugin's `book_actions`, so it's in the
  plugin's panel of the reading options only while that plugin is installed and switched on. The
  reader has no edit button of its own.
- **Not an `Action` field** (e.g. `client=True`): an older MangaVibe would refuse the unknown
  argument and the plugin wouldn't load. With the event, the same plugin file runs on both: where
  nobody handles the button, `run_book_action` gets it as before.

## Remote control: an MCP server (Plugin API 13, issue #192)

So an assistant, and later a voice through the Manga Accessibility plugin, can use the whole app.

- **A plugin of its own, off by default** ("Remote control (MCP)", the `mcp/` folder plugin in
  [manga-vibe-mcp](https://github.com/ivokolarov0/manga-vibe-mcp)): it's a new way into the app, so
  installing it mustn't open it. It first came built in (`app/plugins/builtin/mcp/`, 2.17.0) and was
  moved to its own repo right after, like the other optional plugins: most people won't use it, it
  shouldn't sit in every install as an extra way in, and it can be released on its own pace. What it
  needs from the app stays here as Plugin API 13 (`McpTool`, `run_mcp_tool()`, `find_user()`,
  `users.SCOPE_KEY`, `MangaVibe.screenCommand()`, the reader API, the `mangavibe.internal` scope mark),
  so saved searches and libraries keep their tools, with app-side tests in `tests/test_plugin_api13.py`.
  **After updating from 2.17.0** the plugin is gone (`MOVED` in app/plugins/__init__.py): its id is
  forgotten from `plugins_known` and added to `plugins_disabled`, so installing it later starts it off
  like a new plugin and old keys don't work again until the owner switches it on; whoever had it on is
  told in Settings → Plugins where it went (`plugins_moved`), until it's installed. The keys stay in
  the settings, for when it is. The paths below (`protocol.py`, `screens.py`...)
  are in that repo. The owner switches it on; then everyone makes
  their own keys in Settings → Remote control (a page every account sees).
- **Two kinds of tools.** *Data* tools (search, filters, a series, Continue, settings, marking read)
  answer from the server, so they work with no screen open. *Screen* tools act on an app the person
  has open: navigate, turn pages, press buttons, say what is shown.
- **Data tools call the app's own API**, as an ASGI request through the whole app (every middleware,
  so the plugins' guards too), as the key's person. Not the route functions directly: those take
  FastAPI dependencies, and calling them would skip LockGuard and `series_filter`. This way a tool can
  never do more than the person could in the app: owner-only settings stay owner-only, a locked
  library stays locked (the unlock is a browser cookie, which an assistant doesn't have). The calls
  are marked so `RememberAddress` doesn't take their made-up host as the app's address. `api_request`
  opens the rest of the API the same way, for anything without a tool of its own.
- **Screens poll for commands** (long polling: each open page asks, and waits up to 25 seconds;
  `screens.py`). Not a WebSocket: uvicorn has no WebSocket library in the image, and an extra
  dependency for this isn't worth it. Not an event stream: a poll is a plain request that gets through
  every proxy and the Android WebView, and a page that goes away just stops asking. Kept in memory:
  MangaVibe is one process. State changes (shown, touched, moved to another page) are sent at once on
  their own, so the poll is never cut short; a cut poll could lose a command handed to it.
- **Which screen**: the one asked for (its id or a part of its name), else the person's screen that is
  shown and was touched last. Only their own screens: with accounts, each person's are apart.
- **Generic over specific for the UI.** `screen_describe` lists the controls the page shows (in the
  dialog or sheet on top, or the reader's open panel, else the page) with their accessible names and
  state, each with a ref; `screen_press` / `screen_fill` / `screen_key` use them. That reaches every
  button, including plugins' and future ones, the way a screen reader does, so voice control can't
  fall behind. Specific tools are added where saying it is easier (navigate, the library's filters, the
  reader's page and book); the reader got a small API for them (`MangaVibe.reader.state/step/goTo...`),
  doing what its buttons do. A test checks that the tools' filters, sorts and settings match the page's.
- **Keys, not the login.** An MCP client has no cookie, and a person's password shouldn't sit in a
  config file. A key is `mvk_<user id>_<secret>`; only its SHA-256 is kept (the secret is random, so a
  slow hash adds nothing), in the person's own `mcp_keys` setting, so deleting an account deletes its
  keys (`users.forget`). A key is checked by the plugin's middleware, which runs before the accounts
  plugin's; it puts the user in the request's scope (`users.SCOPE_KEY`), and AccountsGuard takes a
  user found there instead of asking for a login. Only server code can put it there.
- **Who a key belongs to** is asked of the plugins (`find_user`, Plugin API 13), so the MCP plugin
  doesn't import the accounts plugin. Without accounts only the owner (1) exists: a key made by
  someone else while accounts were on stops working when they're switched off.
- **The protocol is written out** (`protocol.py`, ~100 lines): JSON-RPC over Streamable HTTP,
  stateless, answers as JSON (no stream, no sessions). The `mcp` package would bring its own
  dependencies for a handful of methods. `/mcp` is the address (short, to type into a client); the
  middleware hands it to the plugin's own route. Calls with an `Origin` from another site are refused,
  as the spec asks (else any web page could drive the app from someone's browser). The key may also
  come as `?key=` for clients that can't set headers.
- **Plugins add tools** both ways: `mcp_tools` + `run_mcp_tool()` on the server, and
  `MangaVibe.screenCommand()` in the page (offered while a screen with the plugin is open). Not an
  `Action` field: a new class (`McpTool`) an older MangaVibe doesn't import would stop the plugin
  loading, so plugins that want to run on both import it inside a `try`. Saved searches and libraries
  have tools of their own as examples.
- **Settings changed by an assistant show at once** on the person's open screens (a command with no
  answer wanted, `MangaVibe.reloadSettings()`), and series changes make them reload their lists.
- **Not done here**: Claude's website and phone app connect from Anthropic's servers, which can't reach
  a server at home, so they need a public HTTPS address and OAuth; MangaVibe stays off the internet.
  The voice part is the accessibility plugin's (a follow-up there).

## The app as a hub (issue #202)

The owner shouldn't be stuck with a part of the app they don't like: the library page, the series
page, the cards, the filters, a Settings section. So MangaVibe becomes a **hub**: a small core, and
plugins that together make the app, each of which can be switched off and replaced. Until now plugins
could only *add* (slots, hooks, events); this adds *replacing*. It's done in steps (#203–#211), each a
Plugin API version, and the app works after every one.

- **The core keeps** only what no plugin can safely do, or what every plugin shares: the plugin
  manager, **Settings → Plugins and safe mode** (so a broken plugin can never lock the owner out), the
  data (series, books, progress, users, plugin storage), file access, the HTTP server and events, the
  page shell (routing, slots, `window.MangaVibe`), and the jobs with their contracts and tests.
- **Built-in plugins aren't special** (#209): they'll use only the public SDK, so a replacement can
  always do what they do. **Views own markup, not logic** (#205–#207): loading, saving progress,
  marking read, deleting are SDK calls, so a replacement can look different but can't lose data.
- **Reading status stays in the core** (#210): it's worked out in SQL and in Python, which must agree.

### Jobs (Plugin API 15, issue #203)

- **A job is a part of the app** a plugin does: `provides = {"library-query": 1}`, the number being
  the version of the job's contract. `requires` names the jobs it needs another plugin to do. The jobs
  are listed in `app/contracts/` and added there as parts of the app become plugins (none in this step:
  it only adds the framework).
- **Exclusive jobs, one plugin at a time.** Switching one on switches the other off, after the other's
  `can_switch_off()` agrees (libraries with a password can't be replaced silently). Settings asks
  first ("This replaces …"). Two on anyway (switched by hand in an older version): the one the owner
  picked last wins (`jobs_chosen`), else one of the owner's own over a built-in, and the other is
  switched off when the plugins load.
- **A new plugin for a job already done starts off**, so installing a replacement never takes over by
  itself; the owner picks it. A fresh install gets the built-in one (new built-ins are settled first).
- **Required jobs** can't be left undone: switching off the only plugin doing one is refused (409),
  so the way to change it is to switch on the replacement.
- **Blocked, not switched off.** A plugin that can't run (a job or contract version this MangaVibe
  doesn't have, a need no running plugin meets) stays switched on but is left out of `enabled()`, like
  a switched-off one, with the reason in Settings. It starts by itself once the reason is gone (e.g.
  the plugin it needs is switched on). Blocking is worked out again whenever the plugins, switches or
  jobs change, from a cheap key, so code that changes `disabled` directly (tests) stays right. A need
  is met only by the same contract version: v2 of a job may give something else than v1.
- **Switching returns what started and stopped** (the plugin, the one it replaced, the ones whose
  need is met now), so the page adds and takes out their markup without reloading, and each newly
  running plugin gets `on_start`.
- **Safe mode is `/?safe=1`**: the page is put together with the built-in plugins only (markup,
  scripts and styles), with a bar on every page. The server keeps running every plugin: what breaks
  the app in practice is a page that won't draw, and this is enough to reach Settings → Plugins and
  switch the culprit off. `main.css` imports `/custom.css`, so the page can't leave plugin CSS out by
  itself: the safe page sets a session cookie that `/custom.css` reads (and leaves out custom.css too),
  and the normal page clears it.

### The page from Jinja templates (issue #213)

- **Jinja2, on the server only.** The page was one 1600-line `index.html` with `<!-- part:x -->` and
  `<!-- slot:x -->` comments replaced as strings. Replacing a view (#205) needs blocks, includes and
  pieces with arguments; Jinja is Python's Blade (`extends`, `block`, `include`, macros), Starlette
  knows it, and it's one small dependency. No template engine in the browser (Handlebars and the
  like): Alpine already draws there, a second one would be two ways to do the same thing.
- **No clash with Alpine**: Alpine uses `x-text` and `:attr`, never `{{ }}` (the markup had none), so
  in a template `{{ }}` is always Jinja.
- **Split by what will be replaced**: `page.html` includes `shell/`, `views/` (one per view),
  `settings/` (one per section), `sheets/`, `dialogs/`; its regions are `{% block %}`s. The two parts
  became macros in `ui/macros.html` (`setting_rows(rows)` takes the list, so the three copies of the
  `<template x-for>` around it went too).
- **Slots are a function**, `slot("name")`: a name not in `SLOTS` is an error, so a typo fails the page
  and its test instead of leaving a slot nobody fills. A plugin's own markup isn't a template (its
  `{{` stays text, and a plugin can't run code in the page's templates through it); a slot it holds for
  others stays a `<!-- slot:x -->` comment, filled one level deep as before.
- **Strict**: autoescape on (plugin markup goes in as `Markup`), `StrictUndefined`, a test that every
  template is used. Compiled once, reloaded when a file's time changes.
- **The same page**: the rendered page was compared with the old one with every built-in plugin on;
  apart from comments and line breaks it's identical. Plugin templates (a view plugin extending a
  built-in view and changing one block) come with the views, #205, since which plugin's template is
  used depends on which plugin does the view's job.

### Library filters and sorting as a plugin (Plugin API 16, issue #204)

- **The first job, `library-query`**: exclusive and required. The filters and sorts lived in four
  places that had to agree (the SQL in filters.py, the sorts in `list_series()`, the lists in app.js,
  and a copy of the parameters in saved searches). Now there's one: the built-in plugin's
  `query_schema()`, and everything else reads it (`GET /api/library/schema`).
- **The schema is data, the UI draws it by kind** (choice, tags, length, toggle, letter, text). A
  plugin adding a `choice` gets its group, chips and saved searches with no markup; the kinds with a UI
  of their own (tags, length, letter) show only when the plugin has them. A whole UI of its own is the
  views' business (#205).
- **The plugin gets a `ListQuery`, not the request's query.** The core builds what may be seen
  (missing series, other plugins' `series_filter`, the library), and applies it again after the plugin,
  so a filter plugin can't show a locked library's series even if it builds a new query. The core adds
  the last sorts (title, id) so paging stays stable whatever the plugin sorts by.
- **The reading counts are joined by the core, after the plugin** (`lq.reading()` only makes the
  subquery). Joining inside the plugin was easy to get wrong: `lq.query = lq.query.filter(
  lq.reading_status() ...)` looks up `.filter` on the query before the join, and loses it (a cartesian
  product, caught by running the tests with SQLAlchemy warnings as errors).
- **Falling back**: a plugin that fails with anything but an `HTTPException` is logged and the built-in
  one is asked instead (`manager.doing(job)`), the first use of the hub's fallback rule.
- **Numbers**: FastAPI used to refuse a bad `year_min` (422); the plugin reads raw query parameters, so
  `number()` refuses them the same way. `year_min`, `year_max`, `score_min` of 0 still mean no limit.
- **Saved searches `requires` the job** and keeps the parameters its plugin names, so a replacement's
  own filters are saved too.
- **Contract tests** (`tests/contracts/`) run through the app's API against every plugin doing the
  job: the built-in one and the example that extends it. Remote control (manga-vibe-mcp) still has a
  copy of the built-in filters and sorts; its test now compares with the built-in plugin. It could read
  `GET /api/library/schema` instead, and then follow a replacement too. (It does from v1.1.0,
  manga-vibe-mcp#2, keeping the copies for older MangaVibe.)

### Views a plugin can replace, starting with the cards (Plugin API 17, issue #205)

- **A view is a template in the plugin's `templates/` folder**, not markup in a Python string (the issue
  first said `views = {"card": markup}`). With #213's Jinja templates, a view plugin can extend the
  built-in view and change one block, which is what most people want ("the same card, without the
  line under the title"). A folder plugin's `templates/` is reachable as `@<plugin id>/<path>`.
- **`{{ view("card", layout=...) }}`, a function, not a template name the loader swaps.** Which plugin
  draws a view depends on the switches and on safe mode; a loader that gave different files for the
  same name would fight Jinja's template cache. The function picks the plugin and renders
  `@<id>/views/card.html`, a name that always means the same file.
- **The page keeps the card's link**, the view is what's inside it: the link opens the series and does
  multi-select (pointer handlers, `aria-pressed`), which a card plugin shouldn't have to copy or could
  break. `layout` is `library` or `continue`; grid and list are one markup and CSS, as before.
- **Failing safe**: the view is rendered on its own, inside try; a missing template or an error falls
  back to the built-in plugin's and is logged, so a broken card never breaks the page. Settings shows it
  too (`warnings`), worked out by rendering the plugin's view alone, with a slot() that notes which
  slots it used: that's also how a missing `card-badges` is found, without loading the page.
- **Reload on switch**: views are drawn when the page is put together, inside Alpine `x-for` templates,
  so switching a view plugin reloads the page instead of swapping markup in place.
- **`MangaVibe.ui`**: the helpers the built-in card used from app.js (`cardSub`, the "Updated…" line)
  and the ones the next views will need, as public names. The built-in card uses only those.

### The series page as a view (Plugin API 18, issue #206)

- **The logic moved out first.** `seriesView()` mixed loading, actions and display. Now
  `MangaVibe.series` (an Alpine store, `seriesController()` in app.js) has the data and every action, and
  loads what the route shows by itself (an `Alpine.effect` on the route and `progressDirty`). A page of
  one's own can't get marking read or deleting a folder wrong: it calls them.
- **Actions return, they don't open things.** What's on screen (the ⋯ menu, dialogs, "show more", the
  number form) is the view's: `deleteInfo()` then `delete()`, `numbersForm()` then `saveNumbers()` (true
  when saved), so a view decides how to ask and show.
- **The built-in page is a plugin with a script**: its markup and `seriesPage()`, which uses only
  `window.MangaVibe`. Its names are the old component's, read through to `MangaVibe.series`, so the
  markup barely changed.
- **The built-in view plugins' scripts are always loaded**, on or off (and served): their view is the
  fallback, and a page that extends theirs uses their component. Tracking which plugin drew a view and
  loading only its script was tried first; it missed the extending case.
- **Blocks** (`head`, `about`, `details`, `side`, `tags`, `books`) so a page can change one part. A block
  left out with a slot in it (e.g. `books` holds `book-row`) is reported like any missing slot.
- **Checked in Firefox** (Playwright): loading, favorite, read/unread, reading mode, the Files tab's
  number form, the delete dialog and moving to another series, with the built-in page and with a page
  that extends it while the built-in plugin is off.

### The library page and Continue reading as views (Plugin API 19, issue #207)

- **`MangaVibe.library` and `MangaVibe.continue`**: `library()` and `continueView()` became stores, as the
  series page did. Watching outside a component needed a small `watchValue()` (an `Alpine.effect` that
  only calls back on a change); the sentinel the endless scroll watches is the view's element, handed over
  with `watchEnd(el)`, and the scroll after a page change uses the page's own section.
- **Two plugins, not one**: one plugin doing both jobs would be switched off whole when someone replaces
  only Continue, leaving the library page's job to the fallback.
- **The card links are macros the page gives** (`library_card()`, `continue_card()`): a library page can
  lay the grid out its own way but can't break multi-select (hold, shift-click, the click after a hold),
  and the card is always the card view's.
- **`MangaVibe.bind(store)`**: the built-in library page's component is just the store's names. Plugins'
  markup in `select-menu` and `library-end` was written against those names (`bulk()`, `selCount`,
  `selMenu`), so a page that binds the store keeps them working. Functions are bound to the store, so
  `this` is right whoever calls them.
- **Checked in Firefox** (Playwright): all series load, the quick buttons, chips and Clear all, the list
  view, hold to select (without opening the series), tap and Select all, a bulk action, numbered pages,
  Libraries' Move dialog from the select menu, and Continue reading.

### Dialogs, the top bar and Settings' sections (Plugin API 20, issue #208)

- **The dialogs' logic stays in the core** as the components `editSeries()` and `fixMatch()`; the
  markup is the plugins'. They're one-off forms nothing else reads, so a store (as for the pages) would add
  nothing: a dialog of one's own uses the same component, and its saving and matching are the app's.
- **The top bar must keep the Settings button**: a view can now have *parts*, markup the page gives and the
  view must draw (`settings_link()`). Leaving one out isn't only a warning, the built-in view is drawn
  instead: a top bar without Settings would leave the owner no way to Settings → Plugins (except safe mode).
- **Settings' sections are optional views**: nothing is drawn while no plugin does the job, instead of
  falling back to the built-in one, so switching a section off takes it away. Their menu entries come from
  the server, the running plugins' `settings_pages` in the page (`MV_SETTINGS_PAGES`), not from the plugins'
  scripts: a built-in section plugin's script would be loaded even while it's off (built-in view scripts
  always are), and would add its entry back.
- **One plugin per section**, five classes in one package: one plugin for all of them would be switched
  off whole when someone replaces only Display.
- **Settings -> Plugins, a plugin's page and Needs attention stay in the core.** Needs attention as a job
  plugins can add lists to is #219.
- **Switching a plugin for any job the page draws reloads it** (`page_jobs` in `GET /api/plugins`).
- **Checked in Firefox** (Playwright): the menu in its order, every section, a setting saved, switching
  Updates off in Settings -> Plugins (gone after the reload), the Edit dialog saving, Fix match with its
  sources, and the search box.

### Needs attention lists as a shared job (Plugin API 21, issue #219)

- **A shared job**, `attention:list`: any number of plugins add lists. The app's six are the built-in
  "Needs attention" plugin's, with the same row keys, so what the owner ignored stays ignored after the
  update. The core keeps what every list shares: ignoring, "Show ignored", the counts and the frame.
- **The core asks per list** (`attention_rows(ctx, key, request)`), not one call for everything: a list
  that fails is logged and empty, the others still show. The built-in lists lost the one big pass that
  shared its queries; each is still only the flagged rows, and the badge (asked every minute) has
  `attention_count()`, which loads only ids and keys, as before.
- **Ignoring looks the row up** instead of telling the series from the key's shape (`err:<book>` →
  its series): a plugin's keys can be anything, and a row the request can see is exactly one it may
  ignore, so a locked library's rows can't be ignored (403, as before). The page sends the list, so only
  that one is worked out.
- **The menu and the help come from the server** (`MV_ATTENTION_LISTS` in the page), like Settings' pages;
  the red counts follow each list's `alert` instead of a list in app.js.
- **Each list's markup is its plugin's template** (or a plain one), drawn into the core's frame under
  `x-if="kind === '<key>'"`; the list key is a plain name, so it's safe in the attribute.
- **A taken key is left out**, first come first served: a plugin can't replace a built-in list by its key
  (it replaces the built-in plugin by switching it off, then its key is free).
- **Checked in Firefox** (Playwright): every list in the menu with the right red counts, each list's rows,
  a plugin's list in the plain template, and ignoring and restoring a row.

### Built-in plugins use only the public SDK; saved searches are the job's (Plugin API 22, issue #209)

- **The SDK is a module of names, `app.plugins.sdk`**, re-exporting the rows, settings, password hashing,
  the parser's sort, the reading modes, title display and the metadata helpers from where they live. The
  built-ins import those from it, so the app can move its insides while the names stay. Not wrappers on
  `ctx` for everything: `get_setting(db, key)` etc. are already plain functions, and a second way to do
  the same would only be more to keep.
- **A test walks `app/plugins/builtin/`** (AST imports) and fails on anything outside the SDK and the
  plugin's own package. What remains is listed with its reason and the list must stay exact, so it only
  shrinks: the Optimize plugin rewrites library files (scanner lock, archives, cached pages), and the
  libraries plugin's env setting and command line. Their scripts are checked for the app's page globals
  (`api`, `toast`...): none, they already used `window.MangaVibe`.
- **Saved searches are the `saved-searches` job's data** (shared job), in `jobs:saved-searches`, moved
  there by migration 18 for the owner and every account, so a replacement plugin sees everyone's lists.
  Libraries' assignments stay the libraries plugin's: libraries aren't a replaceable job yet.
- **Deprecation rule**: a name leaves the SDK only after one minor version of warning.

### The parser, the matcher and book labels as server jobs (Plugin API 23, issue #210)

- **Three jobs, each a built-in plugin with the code that was in the core**: `scanner:parser`
  ("File name parser"), `metadata:matcher` ("Title matcher") and `book:label` ("Book labels"). The core's
  `parse()`, `matcher.clean_query/score/pick` and `book_label()` keep their names and ask the plugin
  doing the job (`ask_job()` in `app/plugins`), so the scanner, the metadata service and the lists didn't
  change, and every existing test passes as it was.
- **Plain functions, not `ctx` hooks**: they're asked per book and per result (a scan parses thousands of
  names, a list labels every book). Going through the plugin costs about 13 µs a call.
- **A broken replacement falls back to the built-in one** (logged once per plugin and hook), even when
  the built-in plugin is off, as for the views. Only `book:label` can be left with nobody doing it: then
  books are called by their file names.
- **The order of a series' books stays the core's** (`Parsed.sort_key`, revisions, duplicates): it's
  what the owner's own numbers and hiding work on. A parser only gives the numbers.
- **Switching the parser reads the numbers again** at the next library scan: the scan remembers which
  plugin and version read each library (`jobs:scanner:parser-used`). Only the names are read again, not
  the files, and the book rows stay, so progress and page data are kept. The first scan after this update
  only remembers the parser.
- **Reading status stays in the core**: it's worked out both in SQL and in Python, and they must agree.

### The image reader as a plugin (Plugin API 24, issue #211)

- **`reader.js` is the reader host now; the built-in "Image reader" plugin draws the pages.** The split
  follows what other plugins depend on: the host keeps the bars and the reading options, progress, the end
  card, overlays, plugin buttons, read aloud, fullscreen and the Android keys, so a translator or read
  aloud works whoever draws the pages. The plugin has the paged and vertical drawing, zoom and pan,
  gestures, the wheel and keys, the fit, the zoom lock and the page look, moved over unchanged.
- **Registered like a reader of its own** (`registerReader(id, "images", …)`, Plugin API 14), with what
  the host needs on top: `pageBox()` for overlays, `modes`, `key()`/`wheel()`, and its options in the drawer
  (`drawer()`/`bindDrawer()`). `setMode` stays the host's (it sets the series' mode and opens the book
  again), so the reader only says which modes it draws; with one, the mode switch is hidden.
- **Overlays follow an element, not a mode.** Before, paged mode copied the image's transform and vertical
  mode put a layer in each page box. Now the host does the first for any page element the reader names,
  which covers both (and a replacement) without the reader knowing about overlays.
- **The drawer's rows come after the reader opens**, so they show its settings; the reader's rows sit in a
  `display: contents` wrapper, so the drawer's layout (and the phone order) is as before.
- **The built-in reader's script is always in the page** (like the built-in views) and the book lists
  who draws it (`image_readers`): when the plugin doing the job fails to open, the built-in one does,
  even switched off.
- **Its styles stay in reader.css**: plugin CSS is unlayered (it's for overriding), and the pages' rules
  belong in the reader's layer. The fit classes moved from `#reader` to the pages' area.
- **No browser tests in the suite** (it has no browser): the contract test checks the server side and the
  script's registration; the reader was clicked through in Firefox with Playwright (paged, vertical, zoom
  with an overlay, the drawer, the end card, `MangaVibe.reader.*`, a replacement reader and a broken one).

### AniList recommendations as a built-in plugin (Plugin API 26, issue #244)

- **A built-in plugin, not core**: `app/plugins/builtin/anilist_recommendations/`, using only the SDK. A
  panel in the existing `series-end` slot, filled by its own route, so the series page and its contract
  don't change, and switched off it leaves nothing behind on the page.
- **Asked for after the page shows, not in `series_info()`**: AniList can be slow or down, and the series
  page must not wait for it. The panel fetches `/api/plugins/anilist-recommendations/api/series/<id>`
  and stays hidden while there's nothing (no match, no recommendations, AniList down): no error shown.
- **Kept per series with `set_series_data`**, with an expiry (the "days" setting, a week by default), so
  a series page asks AniList at most once a week. A failure keeps what was saved and asks again after
  15 minutes, not on every view. A lock per series stops two views asking twice. Requests go through
  `ctx.http` (the AniList throttle, 30 a minute) with one retry, so a page view never waits minutes.
  Library and title language are applied when answering, not when saving, so they never go stale.
- **Covers saved on the server by default** ("cache"): the browser then only talks to MangaVibe, covers
  show offline, and AniList's image server sees one request per cover ever. Hotlinking is the setting
  for owners who'd rather not use the disk (a few hundred KB per series). Hotlinked images go with
  `referrerpolicy="no-referrer"`. The cover route only downloads covers the series' saved answer lists,
  and only from `anilist.co` hosts, so it can't be used to fetch anything else.
- **`ctx.cache_dir` (Plugin API 26)**: plugins had nowhere to put files. It's under the data folder's
  `cache`, so it's clearly something that can be thrown away.
- **Series already in the library** (same `anilist_id`, and visible to the person asking) link to their
  page and use their own poster, so a locked library's series never shows up.
- **On by default**: the app already talks to AniList for metadata, and the panel only shows for series
  matched there.

### Reader: moving the mouse brings up the controls (issue #254)

- **Any real movement over the pages shows the bars** (not the drawer), like the top and bottom edges
  did before; they hide again after `reader_autohide`. Touch and pen are unchanged.
- **8 px from a resting point, not per event**: the pointer has to get 8 px away from where it was
  when the bars last hid, when it last rested (the 2 s after which the pointer hides), or after the
  last movement that counted. A mouse that jitters on its own surface wanders back and forth around
  that point and never gets there, so it doesn't bring the bars or the pointer back while you read.
- **Not while dragging, and not just after the bars hid**: a held button only moves the resting
  point, and for 600 ms after the bars hide (a click that turned the page) a movement doesn't bring
  them back.
- **JS, not CSS**: `:hover` can't tell how far the pointer moved, and the bars are shown by `showUi()`.

### Other languages (issue #263, Plugin API 27)

- **gettext `.po` files** (the owner's choice), not JSON: translators' tools (Poedit, Weblate) read them,
  they carry plural rules, and the English text as the key means a missing translation is just English.
  `messages.pot` is the template; `bg`, `de` and `ja` are the first languages.
- **Read by the app itself** (`app/i18n.py`), no Babel and no compiled `.mo` files: a small reader, one
  less dependency and no build step. Plural rules come from each file's `Plural-Forms`: on the server
  through Python's `gettext.c2py`, in the browser as a function made from the same expression, which is
  checked to hold only `n`, numbers and operators first. A fraction (1.5 seconds) takes the form of 2.
- **One helper everywhere: `t()` and `tn()`**, in Jinja, in Alpine expressions and in scripts, with
  `{name}` placeholders (not `%(name)s`) so the same text works on both sides. `N_()` marks Python texts
  that are translated later. `python -m app.i18n` finds the texts by reading the code, so they must be
  plain strings; it also brings the `.po` files up to date, and a test fails until it has been run.
- **Templates are translated on the server** (the page is drawn per request anyway); texts that change in
  the browser are translated there from the language's texts, which the page carries (`MV_I18N`). So
  changing the language reloads the page instead of swapping texts in place.
- **The language is a personal setting** (`language`: `auto` or a code), like the theme. `auto` takes the
  first language of the browser's `Accept-Language` that the app has, else English.
- **Plugins** ship `i18n/<lang>.po` next to their `__init__.py`. Their texts are added for words the
  app's files don't translate; they can't change the app's own words.
- **Step by step**: this first step has the machinery, the picker, and the top bar, banners, toasts and
  Settings → Display / Reader / Android app translated. The rest of the page moves to `t()` in the next
  steps, then a test that fails on text written straight into a template.
- **Step 2a, the rest of Settings**: Library folders, Metadata, Updates, Plugins, a plugin's page, Needs
  attention, the folder picker, Libraries and Accounts. Jinja's `t()` escapes the translation and fills in
  its placeholders, so markup (a `<code>`, an Alpine-bound `<b>`) can sit inside a translated sentence
  without the translation itself ever being HTML. Plugin markup (not Jinja) uses `th()` for that.
- **Texts that come from the server through the API** (job labels and descriptions, plugin categories, the
  Needs attention lists' labels) are marked `N_()` and translated in the browser with `t()`; the API stays
  language-free. Built-in plugins' names, descriptions and permissions, and the server's error messages,
  are a later step.
- **Step 2b, the library and the series page**: the library page, its filter / sort / letter sheets,
  multi-select, cards, Continue reading, the series page, the Edit and Fix match dialogs, Saved searches,
  AniList recommendations and Settings → Backup. The library-query schema keeps English labels (assistants
  read it too) marked `N_()`; `deriveLib()` translates them once, so the sheets, chips and saved searches
  all show the page's language. A text with a number in it is one translated sentence per unit ("{n}+
  chapters", "Up to {n} volumes") instead of English words joined around the number. Book labels ("Vol 1")
  and errors from the server stay English until the server-texts step.
- **Server texts: every request runs in the person's language** (the `Language` middleware in app/main.py,
  innermost, so the accounts plugin has said who is asking: their setting, else Accept-Language). Then the
  server just calls `t()`: book labels ("Bd. 1 Kap. 1"), error messages, plugins' names, descriptions,
  permissions, settings and buttons in the API, status lines, the reading-mode reason. Not for files
  (pages, thumbnails, posters, static: no words, no extra database read) and not for the server's own calls
  for assistants (`mangavibe.internal`): those keep the app's English words.
- **Error messages are translated on their way out** (one exception handler) instead of at each of ~120
  `raise`: `HTTPException(404, "…")` texts are found by the extractor, which reads Python with `ast` (so
  text split over lines is one msgid, and comments don't count). Messages with values are `t()` with
  placeholders. The client no longer looks at an error's words (Fix match checks the 503 status).
- **Step 3, a test against text written straight in** (tests/test_no_hardcoded_text.py): pattern checks, not
  a full HTML/JS parser. They look where people read text (between tags, the attributes a screen reader or
  a tooltip shows, the Alpine bindings that set text, and toast / confirm / prompt), and count a quoted
  value as text only when it starts with a capital letter or holds two words, so values like 'files' and
  class names pass. Text a script puts into `textContent` or `innerHTML` some other way isn't checked:
  those few places are covered by review and by the reader's markup going through `esc(t(…))`.
- **Step 2c, the reader**: reader.js builds its markup as strings, so every translated text that goes into
  `innerHTML` is escaped (`esc(t(…))`); the image reader (a plugin) uses `MangaVibe.t` and its own
  `escHtml`. Messages only for developers (`console.error`) stay English.

### Export and import (issue #262)

- **One .zip**: `manifest.json` (app, file format, schema = migrations the database has had, version,
  date, counts), a copy of `mangavibe.db` made with SQLite's backup API (consistent while the app
  writes), `covers/` (an uploaded poster exists nowhere else) and `custom.css`. The whole database
  rather than a JSON dump of chosen tables: it already holds the library data, every user's progress,
  the accounts and all settings, plugins' included, and its migrations already know how to bring an
  old one up to date. Caches and the manga files stay out; so do plugins installed into
  `/data/plugins` (code, not data: their settings go along, install them again).
- **Versions**: a file whose format or schema is newer than this app is refused ("Update MangaVibe
  first"), checked in the manifest and again in the database itself. An older one is migrated on its
  own copy (`db.migrate()`, the code `init_db()` uses) before anything is replaced.
- **Import replaces everything**, after a confirm. No merge: matching users, series and books between
  two databases by name and path is a feature of its own, and "replace" is what moving or restoring
  needs. The database being replaced is copied to `/data/backups/before-import-<date>.db` first (the
  newest 3 are kept).
- **No restart**: the file's database is copied over the running one with SQLite's backup API and
  the connections are dropped, then plugins are loaded again. A restart only exists in the Docker image
  (`updater.restart()`), not in development or on Android. While it runs, the scanner's lock is held
  and the metadata worker waits (`worker.busy`) and its queue is emptied: their ids would point at
  the other database's series.
- **Books are matched by path.** Every book is marked to be read again (`mtime = -1`), the thumbnail
  and page caches are emptied (kept by book id, which now means other books), this server's
  `LIBRARY_PATHS` are added and a scan starts. Progress of a book that isn't there is kept like any
  missing file's: hidden, removed after 30 days; a library folder that's missing as a whole is left
  alone, so it can be mounted and scanned.
- **Tokens are left out by default** (the GitHub token and plugin settings marked `secret`; the copy
  is VACUUMed so they aren't left in free pages). "Include tokens" puts them in. Password hashes
  (accounts, locked libraries) and the keys that sign logins always go along: without them nobody
  could log in after an import.
- **A page of the core's** (Settings → Backup, like Plugins), not a plugin's section: restoring has to
  work whatever the plugins do. Admins only. Export is a plain download link; import sends the file as
  the request body (not a form), written straight to the data volume.

### Random manga as a built-in plugin (Plugin API 28, issue #275)

- **A built-in plugin, not core**: `app/plugins/builtin/random_manga/`, using only the SDK and the query
  API. Buttons in the existing `library-tools`, `nav-items` and `series-actions` slots; switched off,
  nothing is left on the page.
- **Picked on the server**, with `ORDER BY random()` over the same series the library grid starts from
  (not missing, `visible_series()`), so locked libraries are never picked. The status filter is
  `ListQuery.reading_status()`, the status the app shows (a manual one, else worked out from progress),
  each person's own.
- **From the library shown**: the page sends the lists' scope (`MangaVibe.listScope()`, new in Plugin
  API 28, e.g. the libraries plugin's `shelf=3`). Not the library page's filters: a random pick is for
  "anything", and Pick another works the same from any series page.
- **Pick another** shows only on the page the last pick opened. `current` is never picked again;
  `recent` (the last 50 picks, kept in the page) are avoided while anything else is left, so rerolling
  doesn't bounce between two series.
- **Settings are the owner's**, like every plugin setting, and on by default (Yes/No dropdowns: plugin
  settings have no switch). Nothing left gives a message saying why, not a silent nothing.
- **Room on phones**: the library page's tools (Filter, Sort, Letter, Saved) were already 8px wider than a
  400px phone; the row now scrolls sideways like the quick filters, and Random is only its icon there.
  The Sidebar's entry is in its column only: the phone bar has no room for a fourth tab. The bar now
  styles `button.nav-entry` like its links, as docs/PLUGINS.md already said it did.

### The activity log (Plugin API 29, issue #279; step 1 #280)

- **Writing is the core's, keeping is a plugin's**: `activity.log()` (app/activity.py) only builds the entry
  and sends `on_activity`; the built-in Activity log plugin stores it. Switched off (or replaced), nothing
  breaks, and with no plugin listening `emit()` skips the queue: log() costs nothing.
- **In the background**: the entry goes through the plugin worker like the reading events, so a request
  that logs never waits for the database write. The time and who are taken when log() is called, not
  when it's stored.
- **Who**: a small core middleware (`activity.Requests`) marks the requests; inside one, the user is
  `users.current()` (read at log() time, after the accounts guard set it), with the client address and
  `via: "key"` when the scope carries a user (the MCP server). Outside a request it's *System*
  (`user_id` None): background threads don't inherit the request's context. Not logged in (a failed
  login) is user 0, the accounts plugin's NOBODY.
- **Kinds, not sentences**: an entry keeps the kind, a summary (the name of the thing at the time) and
  details, so step 3's page can say it in the reader's language. The kinds and their groups are listed
  once, `activity.KINDS`; a plugin's own kinds are allowed (`<id>.<what>`) and fall in *other*.
- **Secrets can't get in**: details are cleaned before they leave log(): any key whose name says it's a
  password, token, secret, key, cookie or hash is dropped, at any depth; details over 8000 characters
  are replaced by a note.
- **The plugin's own table**, not a model in app/models.py: made with `create_all(checkfirst)` when the
  plugin starts (app start, switched on, after a backup import) and again if it's missing when an entry
  comes. It keeps the user's name at the time, so a removed account's entries still read well. Paged by
  id (`before`), which is also the order entries were written in.
- **The API is the plugin's**: `/api/plugins/activity-log/api/entries` (like every built-in plugin's), not
  `/api/activity`; the whole router is admins only (`dependencies=[manage]`).

### What the activity log records (issue #279, step 2 #281)

- **Logged where the change is made, after it's saved**: in the API route (or deleting.py, the walker,
  the updater), after the commit, so a refused or failed action leaves no entry. The list is in
  docs/PLUGINS.md (The activity log).
- **Logging in is the account's**: the login request has nobody logged in yet, so the entry is written
  `acting_as` the account. A failed login is user 0 (not logged in), with the name tried and why, never
  the password; the address is the entry's. A failed library unlock is logged the same way.
- **Scans**: one someone starts logs `scan.started` as theirs and `scan.finished` (as System: the
  scanner's thread) with counts the walker now keeps (new and missing series, new, changed and missing
  books). The app's own scans (at start, every few hours) log only a `scan.finished` that changed
  something, or the log would fill with nothing. Rescans of one series (the watcher) aren't logged.
- **Settings**: only the app's (an admin's); each person's own (theme, reader, page size) are theirs.
  A secret plugin setting and the GitHub token say only that they changed or were cleared.
- **Metadata by hand only**: editing, a poster, the series' reading mode, Fix match, Unmatch, Refresh
  (one series, or many from the multi-select as one entry). Automatic matching isn't logged: it's the
  app's own work on every new series.
- **Updates**: `update.started` from the request; `update.finished` at the next start, when the version
  differs from the one last started (setting `running_version`), so it's there after a restart and also
  for a new Docker image; `update.failed` from the updater's thread.
- **Not there, so not logged**: merging series, deleting one book and removing a plugin aren't things
  the app does (their kinds were taken out of KINDS). MCP keys are the Remote control plugin's
  (manga-vibe-mcp): it can log `account.key_created` / `account.key_removed` itself.
- **Switching the Activity log off isn't in it**: the entry is written by the plugin, which is off by
  the time it would be. Switching it on again is its first entry.

### Settings → Activity (issue #279, step 3 #282)

- **The plugin's page**, added from its script (`MangaVibe.settingsPage` in "start", right after Plugins,
  only when `$store.app.admin`), like Accounts: switching the plugin on or off adds or removes it without
  a reload. Not `settings_pages`, which only counts when the page loads. The API is admins only anyway.
- **Sentences in the page, not on the server**: an entry keeps its kind and values; activity_log.js says
  it (`SAID`, one `MV.t()` per kind, so the extractor finds them) in the reader's language. Who comes
  first, in bold, and the sentence has no subject ("Ivo · Deleted the series X"), so one text works for
  everyone. A plugin's own kind shows as its kind and summary. A test checks every kind of the app has
  its sentence.
- **Days and times in the browser**: grouped by the local day (Today, Yesterday, then the date); the time
  is relative for the last six hours (`Intl.RelativeTimeFormat`, moving on every 30 s), else the clock
  time; the full date is the `<time>`'s title. The date filter sends the local day's start and end as
  UTC moments, so "today" means the admin's today.
- **Each entry a `<details>`**: native, keyboard and screen reader friendly; the marker sits right before
  the time, no heading inside the summary, and the link to the target goes in the opened part (an
  interactive element inside `<summary>` is a trap). The details' common names and values are
  translated; others show as they are.
- **Failures stand out**: kinds ending in `.failed` or `_failed` (a failed login or unlock, a plugin that
  didn't start, an update that went wrong) get a warning tint and bar.
- **Paging by scrolling**: an `IntersectionObserver` near the end loads the next 50 (`before`), with a
  Show more button as a fallback; an answer to an older filter is dropped (a sequence number).
- **Search on the server**, every word anywhere (summary, name, kind, address, details as JSON text),
  with `%` and `_` escaped; the people filter comes from the log itself (System and "not logged in" too,
  and accounts that are gone, under their last name).
- **Dividers between entries** (`.activity-entry ~ .activity-entry`), never under the last one (#245).

### Keeping, clearing and exporting the activity log (issue #279, step 4 #283)

- **Keep entries for 30 days, 90 (default), a year or forever**: the plugin's own setting (`keep_days`),
  set on the Activity page, not on the plugin's page in Settings → Plugins: it's where the admin looks
  at the log. Changing it is logged like any plugin setting.
- **The clean-up is the plugin's thread** (`ctx.run_in_thread`): at start, then every 24 hours, checking
  `should_stop()` every second, so a reload, switching off or a backup import (which waits for plugin
  threads) never waits long. A shorter time removes at once, in the request. Removing old entries isn't
  logged: it would be an entry a day about nothing.
- **Clearing is logged**: after the delete, `activity.cleared` with the count, by the person who did it,
  so an empty log always says why. Asked first with the app's usual confirm(); the toast says how many.
- **CSV on the server, streamed**: what the filters show, read in batches of 500 by id, so a long log
  never sits in memory; a session of its own, as the response outlives the request's. The column names
  and "System" are translated before streaming (the language belongs to the request). Times in UTC and
  in the admin's zone: its IANA name when the server knows it (the Docker image has tzdata), else the
  browser's current offset. A byte order mark for spreadsheet programs, and a `'` before a cell starting
  with `= + - @` (CSV injection: a summary is what someone typed, e.g. a login name).
- **The backup leaves the log out by default** ("Include the activity log", off): it holds addresses and
  failed logins, which a file kept elsewhere shouldn't carry unasked. The copy keeps the table, emptied,
  and is VACUUMed. app/backup.py names the table (`ACTIVITY_TABLE`): the one place the core knows a
  plugin's table, as the backup is the core's. Importing a file without the log empties this server's,
  like everything else an import replaces; the import itself is then the first entry.

### The Home page (Plugin API 30, issue #291)

- **Home is `#/` and always there; the library moved to `#/library`** (the owner's choice: no switch to turn
  Home off, which made every library address depend on a setting). Every library address is built in one
  place (`libraryAt()` / `libraryUrl()` in app.js), so filters, sorts, the search box, paging and saved
  searches (they keep only the query) all go to `#/library?…`. An old `#/?genre=…` address (a bookmark, a
  plugin's link) is replaced with `#/library?genre=…`, so nothing saved breaks.
- **The rows are the app's and the plugins', the page is a view**: `view:home` (built in: `home_page/`) only
  draws `MangaVibe.home.sections`. A plugin that replaces the page keeps every row; one that adds a row
  (`home_sections` + `home_section()`) works with any page. The app's own rows (Continue reading, Recently
  added, Recently updated) are the core's (app/api/home.py), like Continue reading's API.
- **One call for the page** (`GET /api/home`): every shown row with its 10 cards, the empty and hidden
  ones left out. A plugin's row that fails is logged and left out. A plugin's series row gives `Series` rows
  and the core makes the cards, filtered again with `visible_series()`, so a plugin can't show a locked
  library's series by mistake, and every card looks like the library's.
- **Recently updated is new books, kept in `Series.books_added_at`** (migration 20). `updated_at` was no
  good: the scanner sets it when books go missing or are read again too. The scanner sets `books_added_at`
  only for a new book in a series that has been here more than an hour (a folder still being copied in is
  part of adding it), so a new series isn't in both rows. The library's "updated" sort (the row's See all)
  orders by it first, then by `updated_at`, so both start with the same series. The migration takes
  `updated_at` for series it set more than an hour after they were added.
- **Hardened after review**: a plugin row that's made wrong (not strings, `see_all=None`) or there twice is
  logged and left out instead of failing Home; tiles without an id (or twice) are dropped; only the newest
  `/api/home` answer is used (quick library tab switches); unchanged rows aren't drawn again; Home keeps its
  scroll when you come back; Settings' saves go one after another, each with the order as it is then;
  every row hidden says so on Home, with a button to Settings.
- **The Libraries cards show a random series' cover**, a different one each time Home loads. They're wide
  (the owner asked for media-server style library cards): `HomeSection(shape="wide")`, 16:9, twice a series
  card's width, the big poster cut to fit (from its upper part, where faces and titles mostly are) and
  darkened so the library's name, written large across it, reads on any cover; the count under the card. The
  name under it stays for screen readers only (the one on the picture is `aria-hidden`).
- **Home is across the libraries, with no tabs**: the library tabs show on the Library and Continue only,
  and Home's call sends no list scope, so its rows are everything the person may see (a locked library's
  series stay out: the plugin's `series_filter()` still runs). The Libraries row is how Home opens one.
  Its cards and the sidebar's libraries call the plugin's `choose(id)`: on the library and Continue it
  only switches what they show, anywhere else it picks the library and opens `#/library` (a locked one asks
  for its password first). The Libraries row is at the top of Home by default (`HomeSection.order`: 10, the
  app's own rows 20–40) and has a card for every library, even when there's only one (the owner's call).
- **Per person**: `home_layout` (`{order, hidden}`) is a personal setting. A row someone never put in
  order (a plugin switched on later) comes after theirs, in the default order.
- **Settings → Display → Home page**: the rows as setting rows (dividers between
  rows, #245), each with a drag handle (HTML drag and drop, `setData` for Firefox), up / down buttons for the
  keyboard and touch (`aria-disabled` at the ends, so focus stays on the button) and a show box.
- **The carousels are Embla Carousel** (8.5.2, MIT, vendored as `static/js/vendor/embla-carousel.umd.js`
  like Alpine: no CDN, works offline). A hand-made drag on a scrolling row was buggy (snapping fighting the
  drag, clicks after drags); Embla does mouse and touch dragging with momentum, stopping at a card, no click
  after a drag, and keeps up with cards added or removed. Chosen over Swiper: about 7 KB gzipped against
  ~40 KB plus CSS, no styles of its own, no framework. Options: `align: "start"`, `containScroll:
  "trimSnaps"`, `slidesToScroll: "auto"` (the arrows move a screen of cards), `skipSnaps` (a strong swipe goes
  past several), `watchFocus` (Tab brings the card into view; the viewport's own scroll, which the browser
  also does on focus, is undone), a short `duration` with reduced motion. The viewport is found in the row's
  own element on the next tick (its `$refs` aren't there yet in `init()`), and the instance stays out of
  Alpine's reactive data. Without the script the row still scrolls natively, with CSS snapping.
- The arrows only with a mouse (`hover: hover`) and only while there's somewhere to go. The cards are
  the card view (`home_card()` draws Continue reading's for that row, the library's for the others), with
  `content-visibility` off so cards out of sight aren't blank while swiping.

### The UI kit (Plugin API 31, issue #294; step 1 #295)

- **Our own kit, not a library.** Web Awesome/Shoelace (some components behind a paid tier), Basecoat,
  Pines and Penguin (Tailwind and a build), Pico, Oat and Kelp (a look of their own that fights
  `tokens.css`) were looked at and turned down by the owner. The pieces were already in the app, scattered
  over `base.css`, `layout.css` and `series.css`; the kit puts them in one place, documents them
  (`docs/UI-KIT.md`) and shows them all at `/kit`.
- **Plain class names stay** (`.btn`, `.panel`, `.modal`): plugins already use them, so no `mv-` prefix.
  The kit is built with today's look; the new look (#302, #303) comes last, through the tokens and the
  kit, so it reaches every page and plugin at once.
- **A `kit` cascade layer between `base` and `layout`**, one file per piece in `css/kit/`. Pieces that were
  in `base` and `layout` keep their place against the page rules (layout, components, pages still win), so
  nothing changed on screen (Firefox screenshots of every page, dark and light, wide and phone, compared
  pixel for pixel with main). Only the round `.icon-btn` and `.menu-pop` moved down from the `pages` layer;
  that lets the hover the app always meant for `.icon-btn` show (the pages layer used to cover it).
- **No `:where()`**: plugin CSS and `custom.css` are outside every layer, so they already win whatever the
  specificity; wrapping the kit in `:where()` would only have reordered its own variants. What a plugin
  gets instead is **custom properties per piece** (`--btn-bg`, `--panel-radius`, `--dialog-width`…) that
  fall back to the tokens: set on a box, they change only the pieces inside it; unset, the piece follows
  the theme.
- **Behaviour in a registry, `MangaVibe.kit`**: a piece is `{ options, setup(ctx) }`, and its directive
  (`x-dialog`) asks for it each time an element starts, so `extend(pluginId, name, fn)` or
  `define(pluginId, name, piece)` reach every use without touching markup; `data-kit-options` changes one
  use. Like `MangaVibe.on`, a plugin's changes count only while it's on. Events are `kit-close` etc. (a
  hyphen, not a colon, so `@kit-close` works in Alpine), cancelable, so a plugin can stop a piece without
  replacing it. `x-dialog` now ignores Escape for dialogs that aren't shown (it ran every closed dialog's
  close before).
- **The kit page is `/kit`, not a view of the app**: a page of its own (`app/web/kit/kit.html`), so the
  app's page doesn't carry it, open without logging in (no data on it, only public files). It loads the
  app's CSS with plugins' and `custom.css`, so a theme shows there; it's in English, like the docs.

### Dialogs for the keyboard and screen readers (Plugin API 32, issue #294, step 2 #296)

- **The markup stays; `x-dialog` does the rest.** Moving to a native `<dialog>` with `showModal()` would
  have given the focus and the page behind for free, but every dialog of the app and of plugins'
  (`.modal`/`.sheet` shown with `x-show`) would have had to change. Instead `x-dialog` watches its element
  (a MutationObserver on `style`, `hidden`, `class`, `x-cloak`, and its cleanup for `x-if`) and sees it show
  and hide, so plugins get it without changing anything.
- **The page behind is `inert`**: everything beside the dialog and beside each of its parents, given back
  when it hides. Live regions (the toast) are left out, so they're still read. A dialog opened from another
  takes itself out of what the first made inert. Tab also goes round inside the box: `inert` keeps the focus
  out of the page, not out of the browser's own bar.
- **The focus goes to the box, not the first field**, unless something has `autofocus` (or the option
  `focus: "field"`): on a phone a focused field opens the keyboard over half of the dialog (the Edit
  dialog's title). A screen reader still reads the dialog's name. Focus a component put in already (the
  library password) stays.
- **The name is the `.sheet-head`'s `<b>`**, which every dialog already has, else `data-kit-label` or the
  first heading; a dialog whose content waits for data (the Edit dialog's `x-if`) is named when its title
  comes.
- **The focus goes back** to what opened it; an item of a menu that closed is hidden by then, so it goes to
  the menu's button.
- **Only the top dialog closes** on Escape, a backdrop click or the Android back button (which sends
  Escape), and a listener that called `preventDefault()` on the Escape (a menu inside the dialog, #297)
  keeps it open.
- **No scrolling behind**: `overflow: hidden` on the root while a modal shows, with `scrollbar-gutter:
  stable` only when there was a scrollbar, so nothing moves sideways.

### Menus (Plugin API 33, issue #294, step 3 #297)

- **`x-menu="menuOpen"` binds to the page's own state**, so the open flag stays where it was
  (`menuOpen`, `selMenu`, `$store.accounts.menuOpen`) and plugins' items that say `menuOpen = false` (the
  docs told them to) keep working. The directive reads and sets the expression (`ctx.get()`/`ctx.set()`,
  as `x-model` does) and follows it with Alpine's `effect`. A plain `x-menu` keeps the state itself, for
  plugins that don't want any.
- **The kit owns the button's click**, the click outside and Escape: the markup lost its `@click`
  toggles, `@click.outside` and `@keydown.escape` (a test checks every `.menu`). Two toggles on one
  button would cancel out.
- **The APG menu button pattern**: `role="menu"`/`menuitem`, the items out of the Tab order (`tabindex=-1`)
  and reached with the arrows, Home/End and a letter; Tab closes. The focus only moves into the menu when
  it was opened from the keyboard (a click with `detail === 0`, or Up/Down); a mouse click leaves it.
- **Escape is used up**: the menu calls `preventDefault()`, a dialog around it leaves an Escape that was
  already used, and the library's select bar now does too (Escape in its ⋯ menu closed the menu *and*
  ended the selection). A dialog's own Escape is used up the same way.
- **The click outside is a `click`**, like Alpine's `@click.outside` was, not `pointerdown`, so a click a
  script makes still closes it.
- **Staying on screen without anchor positioning**: after it opens, `place()` measures the pop-up and
  flips it (`data-kit-align="start"`, `data-kit-side="top"`). A few lines that work in every browser the
  app runs in (older Android WebViews included), without depending on CSS anchor positioning.
- **Popovers** (`popover: true`) share the open and close but not the menu roles or keys: any content,
  Tab moves through it, and the focus leaving it closes it.

### Tabs and choices (Plugin API 34, issue #294, step 4 #298)

- **The markup keeps choosing**: every tab row and choice already had its picked button's class (`.active`,
  `.on`) and an `@click` that picks, often with more behind it (`setTab()` writes the address, `choose()`
  may ask for a password, `setSort()` closes the sheet). `x-tabs` and `x-choice` don't take that over: they
  read the class (a MutationObserver sees it change) and, on an arrow key, move the focus and `click()`
  the button, so the page's own `@click` runs. Nothing to bind, and the hand-written `role` /
  `aria-selected` / `aria-checked` / `aria-pressed` are gone (a test checks).
- **Automatic picking, `manual` where picking does more**: the APG's default for tabs and radio groups is
  that the arrows pick. Where picking closes something or starts something (the sort-by list and the
  reading mode close their sheet or dialog, Fix match's sources search, a locked library asks for its
  password), `manual` makes the arrows only move and Enter pick.
- **The grid / list switch is a radio group** now, not two `aria-pressed` buttons: it's one choice of two.
- **Panels by `data-tab` / `data-tab-panel`** inside the tabs' parent, which is where every panel already
  sits; ids are made when missing.
- **Which button is picked doesn't depend on being on screen**: a group in a closed sheet or a hidden page is
  read while hidden, and opening the sheet changes nothing inside the group, so the kit looks at the
  button's own `x-show` / `hidden` instead.
- **The move dialog's `.options` stay plain buttons**: each one moves the series, it's a list of actions.

### Fields and setting rows (Plugin API 35, issue #294, step 5 #299)

- **One directive for both shapes**: `x-field` on a `.field` (the Edit dialog's: `<label>`, `.hint`, the
  control, `.error`) and on a `.setting-row` / folder card `.lib-option` (a name, a `<small>` hint, the
  control). It finds the parts, gives them ids and ties them together; a MutationObserver looks again when
  an error appears or an `x-if` adds the control.
- **Named by the name alone**: a setting row is a `<label>` around everything, so its hint was read as part
  of the control's name ("Find metadata automatically For new series after each scan"). The control now
  gets `aria-labelledby` the name and `aria-describedby` the hint. That needs the name in an element of
  its own: the few rows whose name was a bare text got a `<span>` (a test checks).
- **Errors**: a field's `.error` is `role="alert"` (read out when it appears) and makes the control
  `aria-invalid` (a red border) while it shows: the libraries' unlock dialog uses it. A form-wide error
  (login, Fix match, the folder picker) is a `<p class="err" role="alert">`.
- **Switches are `role="switch"`**, but only the checkboxes the CSS draws as switches (`.switch`, right in
  a `.setting-row` / `.row-buttons` / `.lib-option`): the Home rows' show-boxes stay checkboxes.
- **`.setting-row` and the Settings card moved to the kit layer** (`css/kit/rows.css`), with
  `.settings-card` for a card outside Settings. Settings' pages stay cards by themselves. Every page looked
  the same afterwards (the step 4 screenshots; the only differences were two libraries added to the
  test data).
- **Plugins' old `.edit-form label` fields keep working**: `edit.css` keeps those rules, so a plugin's
  `series-edit` field written the old way still lines up; the built-in ones are `.field`s now.
- **No range piece**: the only range is the reader's page slider, which has its name and shows the page.

### Toasts, progress, badges, loading and tooltips (Plugin API 36, issue #294, step 6 #300)

- **The toast isn't its own live region any more**: the visible `#toast` (with its Undo button) is plain,
  and the text goes to two hidden regions, `#toast-say` (`role="status"`) and `#toast-alert`
  (`role="alert"`, for errors). Switching one element's `aria-live` or role between messages isn't read
  reliably, and a live region with a button in it reads the button too. A region is emptied before the new
  text, 50 ms later, so the same message twice is read twice.
- **An action makes the toast wait**: at least 6 s, and paused while the mouse or the focus is on it, so
  it can be reached (WCAG 2.2.1). Only then can it be clicked (`pointer-events`), so a plain toast never
  sits in the way of a tap.
- **`toast.timer` is now a number per toast**, not a timer: the reader's "start of book" toast compares it
  to know its toast is still the one showing.
- **Badges with words**: an `aria-label` on a plain `<span>` isn't read (no role), so the cards' icon and
  number badges and the plugins' "needs attention" dot are `role="img"`. A card's reading bar is
  `aria-hidden`: its line of text already says "Reading · 3/10".
- **Skeletons only for the first load**, in the grid they fill (the library, Continue reading, Home's
  rows), `aria-hidden`, with "Loading…" in a hidden status. Later pages keep the spinner line.
- **`x-tooltip` replaces `title` on icon buttons**: a `title` never shows on keyboard focus nor on a
  phone, and is read by some screen readers after the name. The tooltip shows on hover (after 400 ms) and
  on keyboard focus (`:focus-visible`), not after a tap; it's `aria-hidden`, and the button's name is the
  same words (it fills in `aria-label` when there is none). A click the keyboard made (`detail === 0`, the
  grid/list switch's arrows) leaves it showing.
- **The progress bar is the kit's `.progress`**, named by the line above it (`aria-labelledby` through
  Alpine's `x-id`) with its value as a percentage; without `aria-valuenow` it slides (kept moving slowly
  with "reduce motion", like the spinners).

### The built-in plugins on the kit (Plugin API 37, issue #294, step 7 #301)

- **Most of it was done by the earlier steps**: the menus, tabs, choices, fields, dialogs, tooltips and
  toasts the built-in plugins draw were moved onto the kit as each piece was made. What was left was looked
  for with a scan of the plugins' CSS (any rule on a kit class setting a look property) and of their
  markup (own SVG icons). It found Saved searches (its own button, empty state and icons) and Random manga
  (its icon, three copies).
- **Saved searches**: the Save search button is a `.btn.small` made a dashed pill with `--btn-bg`,
  `--btn-border` and `--btn-radius` (pixel-identical, at rest and on hover); its empty state is the kit's
  `.empty` (the one visible change: its icon in a soft circle like every other empty state), tightened in
  the sheet with the new `--empty-gap` / `--empty-padding`; the delete button got `x-tooltip`; its icons are
  the sprite's.
- **A `shuffle` icon in the sprite** for Random manga, which had the same SVG three times.
- **Two tests keep it so**: a built-in plugin's CSS may only change a kit piece through custom properties,
  and its markup takes icons from the sprite (the image reader's JS keeps its own set).
- **The reader stays as it is**: its bar, options drawer and panels are a design of their own over the page
  (large touch targets, dark over any page), not copies of kit pieces, and they already do what the kit
  does (`aria-expanded`, `inert` while closed, Escape closing the panel, then the drawer, then the reader;
  the options' switches are `aria-pressed` buttons with their state written next to them). Its end card's
  button was already the kit's `.btn`.
- **Examples**: the Sakura theme shows the two ways to restyle (tokens, and a piece's custom properties);
  the plugin reference has a menu, a field and a tooltip in a slot.

### The new look: tokens, fonts and the kit (Plugin API 38, issue #294, step 8 #302)

- **The approved redesign's first half** (the mockup and spec are on #302): the tokens and the kit's
  pieces. The pages' layouts (sidebar, Home hero, series header, filter bar, grouped Settings) are step 9.
- **Warm darks** instead of the blue-grey ones, hairline borders (`rgb(255 238 226 / 0.08)`), a new
  `--surface3` and `--faint`. The orange stays. **Old token names stay** (`--surface2`, `--border`…): only
  their values changed, so plugins and `custom.css` keep working.
- **The accent's gradient** (`--accent-gradient`) on primary buttons, progress bars, switches and ticked
  checkboxes. Its other end, `--accent-2`, is *made from* `--accent` (relative colour: lighter, the hue
  turned 25° towards yellow; mixed with white where relative colours aren't supported), not a fixed orange:
  an owner's `custom.css` with a blue accent would otherwise fade from blue into orange.
  `--btn-primary-glow` is new because the light under a primary button can't be mixed from a gradient.
- **Bricolage Grotesque (titles) and Plus Jakarta Sans (the rest), vendored** in `static/fonts/` as
  variable woff2 (from Fontsource 5.3.0), split by script with `unicode-range`, with their OFL licences.
  No CDN: the app works offline. They have no basic Cyrillic or Japanese, so Bulgarian and Japanese text
  falls back to the system fonts in the stacks. `h1`, `h2` and a dialog's title use the display face.
- **The segmented's chosen one is a raised tile** (as in the mockup's Settings), not filled with the
  accent; `.segmented.accent` keeps the filled look. Its buttons have no 110px minimum any more
  (`--segmented-min`).
- **Choices right in a setting row**: `inline: true` in `SETTING_ROWS` draws a `.segmented`; `x-field`
  names it like any control. The macro's rows became `<div>`s: a click on a `<label>` presses the first
  button inside it. A click on the name still flips the switch or focuses the dropdown (a click handler).
  Which rows use it is step 9's.
- **Sizes**: controls keep their 40px height (the top bar's height depends on it); radii grew (12px,
  cards 20px, covers `--radius-cover` 15px, dialogs 24px).

### The redesign's pages (issue #303, step 9)

One PR per page, on step 8's tokens and kit.

- **Navigation: the sidebar is the default.** The Sidebar plugin (it already listed the libraries) is on
  by default and the top bar is the alternative. An existing install changes over once, on the update
  (`NEW_DEFAULTS` + `jobs_defaults_seen` in `app/plugins`), only when the top bar was doing the job as the
  untouched default: not when the owner had picked a navigation (`jobs_chosen`), nor over a plugin they
  installed. A generic table rather than a one-off, for the next built-in default that changes.
- **The column**: the logo is its top (the top bar's `.brand` widened to the column and given its colour, so
  no markup moved and a nav plugin's contract is unchanged); the bar's line runs under the page only;
  Settings at the bottom under a line. The libraries have coloured dots (their place in the list picks
  from six colours: there's no colour per library to store) and their series counts. The account menu
  stays in the top bar's `topbar` slot: moving it would change that slot's contract.
- **The phone bar floats**: a rounded glass bar clear of the edges, the current page's whole entry tinted
  with the accent. Both nav plugins share it (`layout.css`); the top bar's "Navigation bar: Top" tabs keep
  their own look.
- **Home: Continue reading is the hero.** The first series of the Continue reading row, big, at the top,
  with up to three more under "Also reading" and "See all" past four, in place of that row (so hiding the
  row in Settings hides the hero too). The backdrop is the cover itself, blurred and saturated under a
  dark veil (CSS only, no colour extraction), the same in both themes since it's a picture. The hero always
  comes first, whatever the row's place in the person's order: it's the page's one thing to do.
- **Covers**: 15px corners and a deeper shadow everywhere a series card shows; the carousel's track got
  room at the bottom so the shadow isn't cut off. Wide tiles (the libraries) write their name bottom-left
  in the display face.
- **Series page**: the cover blurred behind the top of the page instead of AniList's banner (the banner is
  missing for most series and a different picture; the cover always matches). Year, score, status, type
  and genres are glass pills over the title (54px at most), then "by" the authors (links to their
  series), books and unread. Continue, the status and star / Edit / ⋯ are one row beside the cover; the
  Continue button says where, with the page under it (`page_count` added to the series' `continue`).
- **The books are a grid of covers** (each book's first page): read ones faded with a check, the one
  being read with its progress and an accent ring, a round button to mark one read. **The list stays**,
  behind a grid/list switch kept per browser, because it has what the grid hasn't room for (read up to
  here, the `book-row` slot for plugins' buttons).
- **Wide screens: one grid, as before the redesign** (about across, details and the other panels two side
  by side, tags, the books across). A first try put the books on the left and everything else in a 330px
  column on the right; the owner turned it down: the important bits were all squeezed to the right and a
  long book list left a big empty space under the column. Phones: about, the books, then the rest; the
  reordering lowers the two that move up, since plugins' panels in a `display: contents` slot are grid
  items too and would otherwise jump ahead.
- **Library**: a big title, the library picked in the sidebar (read from `$store.libraries` with `?.`, so
  the page still says "Library" without the Libraries plugin) with its series count, then one bar on a card:
  the quick filters as quiet chips (the one on in the text colour, inverted), then Filter, Sort, Letter,
  plugins' tools and the grid / list switch (moved up from the count line). The line under the bar is only
  for numbered pages now. The quick filters have no counts: the API gives the total of the current filter
  only, and five more counts per load weren't worth it.
- **Settings: titled cards, inline choices, switches.** `SETTING_ROWS` entries got `group` (the title of the
  card a row starts), and Display and Reader draw them with `setting_groups` (cards under the page's title,
  which has a line under it). Two or three short choices are `inline` (a segmented in the row); On/Off rows
  became switches (`check`); long lists and long words (Tap zones, Fit, the overlay percentages) stay
  dropdowns. The Home page's rows are small cards with a switch, gaps instead of dividers. Pages of other
  plugins are unchanged: a section is a card unless it has `.settings-groups`.
- **Continue reading**: the library's big title with a count. Its cards' bar shows how far into the book to
  go on with (what the line under the card says, "p8/12"), not the whole series: early in a long series
  that was a sliver. The bar is inset and rounded with the accent's gradient, like the mockup's.
- **Chromium**: every page checked in Chromium (Playwright's, 156) at 1280 and 400 wide, dark and light,
  as well as Firefox: the same. Needs attention keeps its layout; the new tokens and kit carry it.

### The library's Added filter and the kit's date picker (Plugin API 39, issue #322)

- **The filter**: `added_from` and `added_to` (days, `2026-10-01`; either may be left out), a new schema kind
  `dates` in the library-query plugin, so the filter sheet, the pill ("Added: 1 Oct – 15 Nov"), the
  Filter button's count and saved searches come from the schema, as for the other kinds. Named
  `added_from`/`added_to`, not the issue's bare `from`/`to`: the library's URL holds every filter, and a
  later date filter (read, updated) needs names of its own.
- **Whole days in the person's time zone.** The page sends the browser's time zone (`tz=Europe/Sofia`) with
  the library's requests while a dates filter is on; it isn't a schema parameter, so the URL and saved
  searches keep the days, not the zone (a link opened elsewhere means that reader's days). Without one
  (assistants) it's UTC. `app.plugins.query.day_range()` turns the days into UTC times: from the start of
  the first day to the start of the day after the last. `tzdata` is a requirement now: Python on Windows
  has no time zones of its own (the Docker image has the system's).
- **A date picker of our own** (kit piece `date`, `x-date`), not `<input type="date">`, which looks
  different in every browser and ignores the theme. The field stays a text field (typing works; it reads
  the language's order and month names); the calendar is a combobox's dialog with a grid (the ARIA date
  picker pattern). Days are `"YYYY-MM-DD"` strings worked out on UTC dates, so summer time can't shift
  one. The week's first day comes from `Intl.Locale` week info, and where the browser has none (Firefox)
  from the language's region. A from/to pair is two fields in a `.date-range`; a pick the wrong way round
  swaps them. The quick picks set *from* only ("since"), so a saved "Last 7 days" stays the days it was
  saved with rather than pretending to move.
- **Where the calendar goes**: inside the dialog the field is in (the filter sheet), so that dialog's
  focus trap, `inert` and Escape keep working around it; on a phone a bottom sheet over its own backdrop,
  and a tap sets `inputmode="none"` for the moment so the phone's keyboard doesn't come up.
- Its keys and typing are tested in node on the piece's own methods (`move()`, `parse()`), as the kit
  has no browser tests; the clicks were checked in Firefox and Chromium at 1280 and 400 wide.
