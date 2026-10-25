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
- **Permissions are declared, not enforced.** A plugin lists what it uses in `permissions` (short
  sentences). An update that adds an entry is installed only after the owner approves exactly the new
  ones (the install answers 409 with the list; the browser asks and sends them back). Python code in
  the same process can't be fenced in, so they tell the owner what changed rather than claim to stop
  anything.
- **Only single-file plugins in the plugins folder** are updated. Built-in plugins come with the app;
  package folders would need a different download.

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
  library folder, else Settings → Reader; the metadata (from Japan or not) no longer decides anything.
  Migration 4 turns stored `rtl`/`ltr` into `paged` and drops `reader_tap_invert`. What stays is the
  reading hand (Settings → Reader): it moves the buttons to the left or right thumb, not the page order.
  `ctx.reading_direction` keeps its name and returns `paged` or `vertical`.
- **A tap in the middle opens the menu.** It used to only show the bars, and the options needed a second
  tap on ☰. Now the bars and the options drawer come up together; a tap beside the drawer closes it and
  the bars hide on their own. Tapping off ("swipe only") and zoomed-in taps do the same.
