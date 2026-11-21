"""Image reader (built-in plugin, issue #211): does the `reader:images` job, drawing a book's pages in the
reader: paged or vertical (webtoons), zoom and pan, taps and swipes, the wheel and keys, the fit (Fit
screen / width / height / Original) with the zoom lock, and the page look (brightness, contrast, colours).

The reader host (app/web/static/js/reader.js) keeps the rest, which other plugins depend on whoever draws
the pages: the bars and the reading options, progress, the next book and the end card, plugin overlays and
buttons, read aloud, fullscreen and the Android keys. The contract is in docs/PLUGINS.md, "The
reader:images job". Its styles are in reader.css (the reader's layer).

Replace it with a plugin of your own that provides {"reader:images": 1} and registers its reader with
MangaVibe.registerReader("<its id>", "images", {...}). Its script is always in the page, so it's the
fallback when the one doing the job fails.
"""
from pathlib import Path

from app.plugins import Plugin

HERE = Path(__file__).parent


class ImageReader(Plugin):
    id = "image-reader"
    name = "Image reader"
    category = "reader"
    version = "1.0"
    description = ("Shows the pages of a book: page by page or one long strip (webtoons), with zoom, "
                   "swipes, tap zones, the fit and the page look. Built in.")
    permissions = ["Shows the pages of the book being read; connects to nothing"]
    provides = {"reader:images": 1}
    js = (HERE / "image_reader.js").read_text(encoding="utf-8")
