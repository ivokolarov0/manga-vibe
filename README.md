# MangaVibe

> [!WARNING]
> **This app is fully vibe coded.** Every line was written by an AI assistant (Claude), guided by
> conversation rather than written or reviewed line by line by a human developer. It has automated
> tests and has been checked in a browser, but it has not had a professional security audit.
> Use it at your own risk, keep it on your home network (see [Reading away from home](#reading-away-from-home)),
> and keep backups of anything you care about. Your manga folder is mounted read-only, so the app
> can't change your files.

<p align="center">
  <img src="docs/media/desktop.webp" width="74%" alt="MangaVibe on a computer: the library grid, a genre filter, a series page with its books in order and the files behind them, and the Needs attention lists">
  &nbsp;
  <img src="docs/media/mobile.webp" width="23%" alt="MangaVibe on a phone: opening a series and reading it right to left with swipes, taps, pinch zoom, the page slider and vertical mode">
</p>
<p align="center"><sub>The series and art shown are made up for the demo.</sub></p>

## What is this?

MangaVibe is a self-hosted manga server that runs anywhere Docker does: a NAS, a home server or your PC.
Point it at your manga folder and it:

- **Finds and orders your books**, even with messy file names (`Vol 3`, `ch03v2`, `c10-18`, `15a`,
  Japanese titles, extras, duplicates).
- **Fetches covers and details** (titles, description, genres, authors, status) from AniList and
  MangaUpdates.
- **Gives you a fast reader in your phone's browser.** Right-to-left, left-to-right or webtoon,
  with swipe, tap zones, pinch zoom, and your progress saved on every page.

It reads `.cbz/.zip`, `.cbr/.rar`, `.cb7/.7z`, image `.epub`, `.pdf` and folders of images, without
unpacking them. Nothing is uploaded anywhere, and your files are never renamed or changed.

## Install

The image is on Docker Hub as [`ivokolarov/mangavibe`](https://hub.docker.com/r/ivokolarov/mangavibe)
(Intel and ARM). You need two folders:

| In the container | What it is | Mode |
|---|---|---|
| `/manga` | your manga folder | **read-only** |
| `/data` | database, covers, cache, plugins (empty to start with) | read-write |

With a Docker GUI (Synology Container Manager, Unraid, Portainer, …): search the registry for
`ivokolarov/mangavibe`, download the `latest` tag and create a container with the two folders
above, port **8090** and auto-restart on.

### Docker

```bash
docker run -d --name mangavibe --restart unless-stopped \
  -p 8090:8090 \
  -v /path/to/manga:/manga:ro \
  -v /path/to/mangavibe-data:/data \
  ivokolarov/mangavibe:latest
```

### Docker Compose

```yaml
services:
  mangavibe:
    image: ivokolarov/mangavibe:latest
    container_name: mangavibe
    ports:
      - "8090:8090"
    volumes:
      - /path/to/manga:/manga:ro
      - /path/to/mangavibe-data:/data
    restart: unless-stopped
```

Then open **http://\<your-server\>:8090**. The first scan starts by itself.

### Settings (environment variables, all optional)

| Variable | Default | What it does |
|---|---|---|
| `TZ` | `UTC` | Your time zone, e.g. `Europe/Sofia` |
| `LIBRARY_PATHS` | `/manga` | More than one library: `/manga,/manga2` (mount each one read-only) |
| `RESCAN_HOURS` | `6` | How often the library is scanned for new files |
| `CACHE_MAX_GB` | `2` | Space for resized pages and thumbnails |
| `PUID` / `PGID` | `1000` | User the app runs as, if that user can read your manga folder |
| `AUTO_UPDATE` | `1` | `0` turns off update checks |

## Organizing your manga

Each folder directly inside your library is one series. Files inside it can be named almost
any way you like:

```
/manga
├── One Piece/
│   ├── One Piece v01.cbz
│   ├── One Piece v02.cbz
│   └── One Piece ch1050-1060.cbz
├── Berserk/
│   └── Berserk Vol.01/          ← a folder of images is one book
└── Solo Leveling/
    └── Solo Leveling c001.epub
```

If a file ends up in the wrong place, fix it on the series page → **Files** tab. You can set its
volume, chapter or a chapter range by hand, or mark it as an extra or hidden. Only MangaVibe's
database changes; the file itself does not.

## Using it

- **Library**: all your series with covers. Search titles, authors and alternative names; filter by
  genre, tag, reading status, format or favorites; sort by title, date added, last read, score and more.
- **Series page**: details and the list of books. Mark books read or unread (or everything before
  one), set a reading status, add favorites. If the details are wrong, **Fix match** lets you search
  AniList or MangaUpdates, or paste a link.
- **Reader**: tap or swipe to turn pages; tap the middle to show the controls (reading direction,
  fit, page slider, fullscreen). At the end of a book it offers the next one.
- **Continue**: everything you're in the middle of, so you can pick up where you left off.
- **Settings**: look and theme, reader defaults, library folders and **Rescan all**, metadata
  sources and updates. A number on the
  Settings icon means something **needs attention**: a series that couldn't be matched, a file that
  can't be read, duplicates or missing chapters.

**On your phone:** open the address in your browser, then use **Add to Home Screen**. It opens
full screen like an app.

## Updating

MangaVibe checks for a new version once a day and shows a message. Tap **Update now**, and it
installs the new version and restarts by itself. You don't have to touch Docker.

Once in a while, a version needs a new image, and the message tells you so. Then pull the new image
and recreate the container. Your library, progress and settings are kept in `/data`.

```bash
docker pull ivokolarov/mangavibe:latest
docker compose up -d          # or: stop, remove and run the container again
```

In a Docker GUI: download the `latest` tag again, then recreate (or "reset") the container with the
same settings.

If a new version doesn't start 3 times in a row, MangaVibe goes back to the previous one by itself.

## Reading away from home

Don't open port 8090 to the internet; the app has no login yet. Use a VPN such as **Tailscale**
or your router's or NAS's VPN server, then open the same address as at home.

## Customizing

- **Look:** edit `custom.css` in your data folder (created on first start, with examples), then reload the page.
- **Plugins:** put plugin `.py` files in `plugins/` in your data folder, then **Settings** → **Reload plugins**.
  See [docs/PLUGINS.md](docs/PLUGINS.md).

## Q&A

**Why another manga server?**
I tried the popular options and didn't like them. I wanted an easy way to get metadata, and it
turned out I had to run an extra Docker container and install a browser extension that needed
access to all domains (it didn't work when I limited it to specific ones). After all that,
identify still didn't work. So I said f*ck it, I'll vibe code my own: extensible, with metadata
and match % built in from the start. I didn't feel like making a mobile app, so the browser is enough.

**Why is the manga folder read-only?**
The code was written almost entirely with Claude, and it may have gaps. I don't want to be
responsible if something decides it should delete a file in your collection. So I explicitly
don't want the app to have any permission to touch your files.

**Why is there no user login?**
Too much of a headache, and it means handling sensitive information I don't want to be responsible
for. Also, someone will want Google/Apple login, then 2FA, then passkeys, then "forgot password",
which means a mailing service or an SMTP server... In short, tedious, and I don't feel like it.
At most there will be a PIN page or a simple password.

**Why only manga and not comics?**
I'm making it for myself, and I mostly read manga. That said, you can already read comics with it:
switch the reading direction to left-to-right, change the fit, and so on. If you want more
comic-specific features, there's a plugin system, so you can vibe code a plugin for them.

**Why is there no option to rename or optimize files?**
Again, I don't want the app to have access to your files. Rename and optimize them outside the
app; I don't want to be responsible if something important disappears.

**Do you accept feature requests and changes?**
Not sure. Small things like "this icon isn't centered" or "there's no hover here": no. This was
mainly meant as a personal project. If people like it and want to add features, pull requests are
welcome, but I work 9 to 6 every day, so I don't know how much time I'll have to review them. I'm
not saying no for now, but it may be easier to fork it and change it yourself without waiting for me.

**You're a developer and you vibe coded everything. Why?**
I enjoy having more free time. With my work hours and the "free" time I have, the whole project
would probably have taken me a few months. What you see here took 2–3 hours to vibe code a working
base version and another 6–10 hours to polish it and add features. If you're one of those people
who hate everything AI... this is not the right place. Go to the art section. ;)

**Why is there no executable file?**
Docker runs it anywhere, from a NAS to your desktop, packs everything it needs and keeps it away
from your files. Making installers for every OS sounds like a job, and I already
have one of those.

**If you don't plan to accept pull requests, why make it public?**
It makes updating easier, and if someone wants a quick and dirty solution, here you go.

## Troubleshooting

- **Logs:** `docker logs mangavibe`, or the container's log view in your Docker GUI.
- **"Cannot write to the data folder":** the user the app runs as can't write to the folder mounted
  as `/data`. Give it read and write access (e.g. `chown -R 1000:1000 /path/to/mangavibe-data`, or
  the folder's permission settings on a NAS), then restart the container.
- **Which user it runs as:** the container starts as root and switches to `PUID`:`PGID` if that user
  can read your manga folder; otherwise it stays root and logs why. Both are fine, because the manga
  folder is read-only either way. To use your own user, set `PUID`/`PGID` to its ids (`id -u`, `id -g`).
- **Adding another library folder:** mount it into the container (read-only), e.g. as `/manga2`, and
  restart. Then go to **Settings** → **Library folders** → **+ Add folder** and pick it. **Remove**
  takes it out of the library again; your files are not touched.
- **Going back to the code in the image:** stop the container, delete `code/current` in the data
  folder, start it again.

---

## For developers

[SPEC.md](SPEC.md) is the full plan and [DECISIONS.md](DECISIONS.md) explains the choices made.

### Run on your PC

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python dev.py
```

Then open http://localhost:8090. `dev.py` creates a small fake library in `dev-library/`.
To use real manga: set `LIBRARY_PATHS=D:\Manga` before running it.

Working in a git worktree? Run `python preview.py` from it instead: it borrows the main
checkout's `.venv` and `dev-library`, gives each worktree its own port and database, opens the
browser and prints a LAN address for testing on a phone. Several worktrees can run at once.

### Tests

```bash
.venv/Scripts/python -m pytest
```

### Docker image

- `python build.py` builds `mangavibe:latest` locally, `--run` starts it with `docker-compose.yml`,
  `--save` writes `mangavibe.tar` (import it elsewhere with `docker load -i mangavibe.tar`).
- Releases: publish a GitHub release from `main`, e.g. `gh release create v1.4.0 --generate-notes`.
  GitHub Actions publishes the image to Docker Hub and ghcr.io (`.github/workflows/image.yml`), and
  running apps pick up the new code through auto-update. A pre-release (`--prerelease`) gets an
  image but is not offered to running apps. The old `releases` branch is no longer used.

### Layout

```
app/main.py             startup, routes
app/config.py           environment variables
app/models.py, db.py    SQLite tables, first start, migrations
app/scanner/            parser.py (file names), archives.py (reading), walker.py (scanning)
app/api/                series.py, books.py, system.py
app/plugins/            plugin system
app/updater.py          auto-update from the newest GitHub release
app/images.py           thumbnails, resized page cache
app/web/index.html      the page: Alpine templates for every view
app/web/static/css/     styles: main.css imports one file per feature (CSS layers)
app/web/static/js/      app.js (Alpine data), reader.js (plain-JS reader), common.js,
                        vendor/alpine.min.js
app/web/static/icons/   app icons; manifest.json next to them
examples/plugins/       example plugin
docker/entrypoint.sh    container start script
```
