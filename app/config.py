"""Settings from environment variables (see SPEC section 9.3)."""
import os
from pathlib import Path


def _env(name, default):
    value = os.environ.get(name, "").strip()
    return value or default


DATA_DIR = Path(_env("DATA_DIR", "/data"))
LIBRARY_PATHS = [p.strip() for p in _env("LIBRARY_PATHS", "/manga").split(",") if p.strip()]
PORT = int(_env("PORT", "8090"))
APP_PASSWORD = _env("APP_PASSWORD", "")
CACHE_MAX_GB = float(_env("CACHE_MAX_GB", "2"))
RESCAN_HOURS = float(_env("RESCAN_HOURS", "6"))
START_WORKERS = _env("START_WORKERS", "1") == "1"  # tests turn background metadata off

DB_PATH = DATA_DIR / "mangavibe.db"
COVERS_DIR = DATA_DIR / "covers"
CACHE_DIR = DATA_DIR / "cache"
THUMBS_DIR = CACHE_DIR / "thumbs"
PAGES_DIR = CACHE_DIR / "pages"
PLUGINS_DIR = DATA_DIR / "plugins"
CUSTOM_CSS = DATA_DIR / "custom.css"

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"   # css/, js/, icons/, manifest.json
