"""MangaVibe.reader.zoom() and .pan() (Plugin API 41, for eye control): the page script offers them, the
reader host passes them on to the image reader, and the built-in image reader does them."""
import re
from pathlib import Path

from app.plugins import API_VERSION

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app" / "web" / "static" / "js"
APP = (JS / "app.js").read_text(encoding="utf-8")
HOST = (JS / "reader.js").read_text(encoding="utf-8")
IMAGES = (ROOT / "app" / "plugins" / "builtin" / "image_reader" / "image_reader.js").read_text(encoding="utf-8")


def test_api_version():
    assert API_VERSION >= 41


def test_page_script_offers_zoom_and_pan():
    assert "zoom: (scale, x, y, animate = true) => Reader.zoom(" in APP
    assert "pan: (dx, dy, animate = false) => Reader.pan(" in APP


def test_host_passes_them_to_the_image_reader():
    assert re.search(r"state, step, openBook, setMode, options, overlay, press, zoom, pan,", HOST)
    assert 'imgCall("zoomTo"' in HOST and 'imgCall("panBy"' in HOST
    assert "zoom: (R.img && imgCall(\"state\")?.zoom) || 1" in HOST  # state().zoom


def test_image_reader_does_them():
    assert "function zoomTo(" in IMAGES and "function panBy(" in IMAGES
    registered = IMAGES[IMAGES.index('MangaVibe.registerReader("image-reader"'):]
    assert "zoomTo, panBy" in registered
