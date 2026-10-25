# For developers

[← Back to the README](../README.md)

[SPEC.md](../SPEC.md) is the full plan and [DECISIONS.md](../DECISIONS.md) explains the choices made.
Plugins: [PLUGINS.md](PLUGINS.md).

## Run on your PC

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python dev.py
```

Then open http://localhost:8090. `dev.py` creates a small fake library in `dev-library/`.
To use real manga: set `LIBRARY_PATHS=D:\Manga` before running it.

Working in a git worktree? Run `python preview.py` from it instead: it borrows the main checkout's
`.venv` and `dev-library`, gives each worktree its own port and database, opens the browser and
prints a LAN address for testing on a phone. Several worktrees can run at once.

## Tests

```bash
.venv/Scripts/python -m pytest
```

## Docker image

- `python build.py` builds `mangavibe:latest` locally, `--run` starts it with `docker-compose.yml`,
  `--save` writes `mangavibe.tar` (import it elsewhere with `docker load -i mangavibe.tar`).
- Releases: publish a GitHub release from `main`, e.g. `gh release create v1.4.0 --generate-notes`.
  GitHub Actions publishes the image to Docker Hub and ghcr.io (`.github/workflows/image.yml`) and
  builds the Android app (`.github/workflows/android.yml`), and running apps pick up the new code
  through auto-update. A pre-release (`--prerelease`) gets an image but is not offered to running
  apps. The old `releases` branch is no longer used.

## Layout

```
app/main.py             startup, routes
app/config.py           environment variables
app/models.py, db.py    SQLite tables, first start, migrations
app/direction.py        how a series reads, paged or vertical (its own, library folder, default)
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
android/                the Android app: MangaVibe in a fullscreen WebView (android/README.md)
examples/plugins/       example plugins
docker/entrypoint.sh    container start script
```
