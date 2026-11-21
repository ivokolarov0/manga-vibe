"""The reader:images job (Plugin API 24, issue #211): the pages in the reader are drawn by a plugin. The built-in
one does it; a book says who draws it (the plugin doing the job, then the built-in one); the built-in reader's
script is always in the page and served, as the fallback, even while another plugin does the job.

What happens in the browser (paged and vertical, overlays following zoom and scroll, MangaVibe.reader.*, a
replacement and a broken one) was checked in Firefox with Playwright; see the PR.
"""
import re
from pathlib import Path

import pytest

from app.plugins import manager
from tests.conftest import TMP

PLUGINS_DIR = TMP / "data" / "plugins"
SCRIPT = Path(__file__).resolve().parents[2] / "app" / "plugins" / "builtin" / "image_reader" / "image_reader.js"

PLAIN = '''
from app.plugins import Plugin

class PlainReader(Plugin):
    id = "plain-reader"
    name = "Plain reader"
    provides = {"reader:images": 1}
    js = """MangaVibe.registerReader("plain-reader", "images", {
      modes: ["paged"], open(el, book, ctl) {}, close() {}, step() {}, goTo() {}, pageBox() { return null; } });"""
'''


def a_book(client):
    sid = client.get("/api/series").json()["items"][0]["id"]
    return client.get(f"/api/series/{sid}/books").json()[0]["id"]


def test_the_job(client):
    from app.contracts import JOBS
    job = JOBS["reader:images"]
    assert job.required and job.exclusive
    assert manager.provider_of("reader:images").id == "image-reader"
    book = client.get(f"/api/books/{a_book(client)}").json()
    assert book["image_readers"] == ["image-reader"]
    assert '<script src="/plugins/image-reader.js"></script>' in client.get("/").text


def test_the_built_in_script_registers_the_contract():
    js = SCRIPT.read_text(encoding="utf-8")
    assert 'MangaVibe.registerReader("image-reader", "images", {' in js
    impl = js[js.index('MangaVibe.registerReader("image-reader"'):]
    for name in ("modes", "open", "close", "goTo", "step", "key", "wheel", "pageBox", "drawer", "bindDrawer", "state"):
        assert re.search(rf"\b{name}\b", impl), name
    # it reports what it shows and the end of the book through ctl
    for call in ("ctl.shown(", "ctl.end()", "ctl.start()", "ctl.tap(", "ctl.menu()"):
        assert call in js, call


@pytest.fixture
def plain(client):
    path = PLUGINS_DIR / "plain_reader.py"
    path.write_text(PLAIN, encoding="utf-8")
    client.post("/api/plugins/reload")
    yield
    path.unlink()
    client.post("/api/plugins/reload")
    client.put("/api/plugins/image-reader", json={"enabled": True})


def test_a_replacement(client, plain):
    # a new plugin for the job starts off; switching it on switches the built-in one off
    assert client.put("/api/plugins/plain-reader", json={"enabled": True}).status_code == 200
    assert manager.provider_of("reader:images").id == "plain-reader"
    book = client.get(f"/api/books/{a_book(client)}").json()
    assert book["image_readers"] == ["plain-reader", "image-reader"]  # the built-in one is the fallback
    page = client.get("/").text
    assert '<script src="/plugins/plain-reader.js"></script>' in page
    assert '<script src="/plugins/image-reader.js"></script>' in page  # off, but still in the page
    assert client.get("/plugins/image-reader.js").status_code == 200
    # the job is required: the last plugin doing it can't be switched off
    assert client.put("/api/plugins/plain-reader", json={"enabled": False}).status_code != 200
