"""Settings from environment variables (see SPEC section 9.3)."""
import os
from pathlib import Path


def _env(name, default):
    value = os.environ.get(name, "").strip()
    return value or default


DATA_DIR = Path(_env("DATA_DIR", "/data"))
LIBRARY_PATHS = [p.strip() for p in _env("LIBRARY_PATHS", "/manga").split(",") if p.strip()]
CACHE_MAX_GB = float(_env("CACHE_MAX_GB", "2"))
RESCAN_HOURS = float(_env("RESCAN_HOURS", "6"))
# app/scanner/watcher.py: "1" watches the library folders for changes (network shares and Docker
# Desktop's Windows folders, which don't report them, are checked every 30 seconds), "poll" checks
# every folder that way, "0" leaves it to RESCAN_HOURS
WATCH_FOLDERS = _env("WATCH_FOLDERS", "1").lower()
WATCH_DELAY = float(_env("WATCH_DELAY", "10"))  # seconds without changes before a series is rescanned
START_WORKERS = _env("START_WORKERS", "1") == "1"  # tests turn background metadata off
# forgot a library's password: its name (or "all") removes the password on the next start
CLEAR_LIBRARY_PASSWORD = _env("CLEAR_LIBRARY_PASSWORD", "")

DB_PATH = DATA_DIR / "mangavibe.db"
COVERS_DIR = DATA_DIR / "covers"
CACHE_DIR = DATA_DIR / "cache"
THUMBS_DIR = CACHE_DIR / "thumbs"
PAGES_DIR = CACHE_DIR / "pages"
PLUGINS_DIR = DATA_DIR / "plugins"
CUSTOM_CSS = DATA_DIR / "custom.css"

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"   # css/, js/, icons/, manifest.json

# auto-update (app/updater.py): the code comes from this repo's newest GitHub release; AUTO_UPDATE=0 turns it off
UPDATE_REPO = _env("UPDATE_REPO", "ivokolarov0/manga-vibe")
AUTO_UPDATE = _env("AUTO_UPDATE", "1") != "0"
IMAGE_DIR = Path(_env("IMAGE_DIR", "/opt/mangavibe/image"))  # copies of the files the image was built from
ENTRYPOINT = Path(_env("ENTRYPOINT", "/entrypoint.sh"))
