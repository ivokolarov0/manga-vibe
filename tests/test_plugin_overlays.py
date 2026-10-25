"""Plugin API v3-v5: page overlays, notes and speech, per-page and per-series data, book badges and buttons,
plugin threads."""
import io
import os
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

from tests.conftest import TMP, make_zip

PLUGIN = Path(__file__).parent.parent / "examples" / "plugins" / "page_info.py"
PLUGINS_DIR = TMP / "data" / "plugins"

BROKEN = '''
from app.plugins import Plugin

class Broken(Plugin):
    id = "broken-overlay"
    overlay_label = "Broken"

    def page_overlay(self, ctx, book, page):
        raise RuntimeError("boom")

    def book_badges(self, ctx, books):
        raise RuntimeError("boom")
'''


@pytest.fixture()
def page_info(client):
    from app.plugins import manager
    dest = PLUGINS_DIR / "page_info.py"
    shutil.copy(PLUGIN, dest)
    client.post("/api/plugins/reload")
    yield manager.get("page-info")
    dest.unlink()
    client.post("/api/plugins/reload")


def series_id(folder):
    """By folder name: other tests change titles (metadata, edits)."""
    from app.db import SessionLocal
    from app.models import Series
    with SessionLocal() as db:
        return db.query(Series.id).filter_by(folder_name=folder).scalar()


def first_book(client, folder="Series B"):
    return client.get(f"/api/series/{series_id(folder)}/books").json()[0]


def wait_ready(client, book_id, page, timeout=15):
    """Ask like the reader does until the plugin's layer is no longer pending."""
    end = time.time() + timeout
    while time.time() < end:
        layer = client.get(f"/api/books/{book_id}/overlays/{page}").json()["layers"][0]
        if not layer["pending"]:
            return layer
        time.sleep(0.05)
    raise AssertionError("page never got ready")


def test_no_overlays_without_plugins(client):
    book = first_book(client)
    info = client.get(f"/api/books/{book['id']}").json()
    assert info["overlays"] == [] and info["actions"] == []
    assert client.get(f"/api/books/{book['id']}/overlays/0").json()["layers"] == []
    assert book["badges"] == []


def test_overlay_pending_then_ready(client, page_info):
    book = first_book(client)
    client.post(f"/api/books/{book['id']}/actions/page-info/clear")
    info = client.get(f"/api/books/{book['id']}").json()
    assert info["overlays"] == [{"plugin": "page-info", "name": "Page info", "label": "Page info"}]
    assert {a["key"] for a in info["actions"]} == {"all", "clear"}

    first = client.get(f"/api/books/{book['id']}/overlays/0").json()
    assert first["layers"][0]["pending"] and first["layers"][0]["message"] == "Working…"

    layer = wait_ready(client, book["id"], 0)
    box = layer["boxes"][0]
    assert box["text"].endswith("% light") and "×" in box["text"]
    assert 0 <= box["x"] <= 1 and 0 <= box["w"] <= 1 and box["cls"] == "page-info"
    # the pages after it were prepared too, without being asked for
    wait_ready(client, book["id"], 1)

    # the reader can ask for only the overlays it has switched on
    assert client.get(f"/api/books/{book['id']}/overlays/0?plugins=other").json()["layers"] == []
    assert client.get(f"/api/books/{book['id']}/overlays/0?plugins=page-info").json()["layers"]
    assert client.get(f"/api/books/{book['id']}/overlays/{book['page_count']}").status_code == 404


def test_hide_a_box(client, page_info):
    book = first_book(client)
    url = f"/api/books/{book['id']}/overlays"
    client.delete(f"{url}/hidden")
    box = wait_ready(client, book["id"], 0)["boxes"][0]
    assert box["id"] == f"{box['x']:.3f},{box['y']:.3f}" and box["hidden"] is False

    r = client.put(f"{url}/0/hidden", json={"plugin": "page-info", "box": box["id"]}).json()
    assert r == {"hidden_boxes": 1}
    client.put(f"{url}/0/hidden", json={"plugin": "page-info", "box": box["id"]})  # twice: still one
    assert client.get(f"/api/books/{book['id']}").json()["hidden_boxes"] == 1
    assert client.get(f"{url}/0").json()["layers"][0]["boxes"][0]["hidden"] is True
    assert client.get(f"{url}/1").json()["layers"][0]["boxes"] == [] or \
        not client.get(f"{url}/1").json()["layers"][0]["boxes"][0]["hidden"]  # only that page

    r = client.put(f"{url}/0/hidden", json={"plugin": "page-info", "box": box["id"], "hidden": False}).json()
    assert r == {"hidden_boxes": 0}
    client.put(f"{url}/0/hidden", json={"plugin": "page-info", "box": box["id"]})
    assert client.delete(f"{url}/hidden").json() == {"shown": 1, "hidden_boxes": 0}
    assert client.get(f"{url}/0").json()["layers"][0]["boxes"][0]["hidden"] is False
    assert client.put(f"{url}/999/hidden", json={"plugin": "page-info", "box": "x"}).status_code == 404


def test_badges_and_book_actions(client, page_info):
    book = first_book(client)
    client.post(f"/api/books/{book['id']}/actions/page-info/clear")
    assert first_book(client)["badges"] == []

    msg = client.post(f"/api/books/{book['id']}/actions/page-info/all").json()["message"]
    assert msg == "Preparing every page"
    for p in range(book["page_count"]):
        wait_ready(client, book["id"], p)
    badges = first_book(client)["badges"]
    assert badges == [{"plugin": "page-info", "name": "Page info", "text": "Info ✓"}]

    client.post(f"/api/books/{book['id']}/actions/page-info/clear")
    assert first_book(client)["badges"] == []
    assert client.post(f"/api/books/{book['id']}/actions/page-info/nope").status_code == 404


def test_read_page_and_page_data(client, page_info):
    from app.db import SessionLocal
    from app.models import Book
    from app.plugins import Context
    book_id = first_book(client, "Hana to Akuma")["id"]
    with SessionLocal() as db:
        ctx = Context(page_info, db)
        book = db.get(Book, book_id)
        full = Image.open(io.BytesIO(ctx.read_page(book, 0)))
        small = Image.open(io.BytesIO(ctx.read_page(book, 0, max_width=200)))
        assert full.size == (400, 600) and small.size == (200, 300) and small.format == "WEBP"
        # a page that already fits comes back as it is: no re-encoding blur
        assert ctx.read_page(book, 0, max_width=1600) == ctx.read_page(book, 0)
        with pytest.raises(IndexError):
            ctx.read_page(book, 999)

        ctx.clear_page_data(book)
        ctx.set_page_data(book, 2, {"lines": ["a", "b"]})
        ctx.set_page_data(book, 0, "first")
        ctx.set_page_data(book, 0, "first again")  # replaces
        db.commit()
        assert ctx.get_page_data(book, 0) == "first again"
        assert ctx.page_data(book) == {0: "first again", 2: {"lines": ["a", "b"]}}
        assert ctx.page_data_counts([book]) == {book_id: 2}
        ctx.set_page_data(book, 2, None)
        db.commit()
        assert ctx.get_page_data(book, 2, "gone") == "gone"


def test_page_data_cleared_when_the_file_changes(client, page_info):
    from app.db import SessionLocal
    from app.models import Book
    from app.plugins import Context
    from app.scanner import walker
    book = first_book(client, "Hana to Akuma")
    with SessionLocal() as db:
        ctx = Context(page_info, db)
        ctx.set_page_data(db.get(Book, book["id"]), 0, "old")
        db.commit()

    walker.scan_all()  # unchanged file: kept
    with SessionLocal() as db:
        assert Context(page_info, db).get_page_data(db.get(Book, book["id"]), 0) == "old"

    path = TMP / "manga" / "Hana to Akuma" / book["file_name"]
    make_zip(path, pages=book["page_count"])  # same pages, new file
    os.utime(path, (time.time() + 5, time.time() + 5))
    walker.scan_all()
    with SessionLocal() as db:
        assert Context(page_info, db).get_page_data(db.get(Book, book["id"]), 0) is None


def test_threads_stop_on_reload_and_switch_off(client, page_info):
    from app.plugins import manager
    book = first_book(client)
    client.post(f"/api/books/{book['id']}/actions/page-info/clear")
    client.get(f"/api/books/{book['id']}/overlays/0")
    thread = page_info.thread
    assert thread.is_alive()

    client.put("/api/plugins/page-info", json={"enabled": False})
    thread.join(5)
    assert not thread.is_alive()
    assert client.get(f"/api/books/{book['id']}").json()["overlays"] == []

    client.put("/api/plugins/page-info", json={"enabled": True})
    wait_ready(client, book["id"], 0)  # a new thread starts when needed
    thread = page_info.thread
    client.post("/api/plugins/reload")
    thread.join(5)
    assert not thread.is_alive()
    assert manager.get("page-info") is not page_info


def test_a_broken_plugin_does_not_break_the_reader(client):
    dest = PLUGINS_DIR / "broken_overlay.py"
    dest.write_text(BROKEN, encoding="utf-8")
    client.post("/api/plugins/reload")
    try:
        book = first_book(client)
        layers = client.get(f"/api/books/{book['id']}/overlays/0").json()["layers"]
        assert layers == [{"plugin": "broken-overlay", "pending": False,
                           "message": "broken-overlay failed: RuntimeError", "note": "", "speech": "",
                           "boxes": []}]
        assert first_book(client)["badges"] == []
    finally:
        dest.unlink()
        client.post("/api/plugins/reload")


NOTED = '''
from app.plugins import Box, Overlay, Plugin

class Noted(Plugin):
    id = "noted-overlay"
    overlay_label = "Noted"

    def page_overlay(self, ctx, book, page):
        return Overlay(boxes=[Box(0.1, 0.1, 0.2, 0.1, text="Hi")], note=f"Full context · page {page + 1}",
                       speech=f"Page {page + 1}. Haruka says: Hi.")
'''


def test_overlay_note(client):
    dest = PLUGINS_DIR / "noted_overlay.py"
    dest.write_text(NOTED, encoding="utf-8")
    client.post("/api/plugins/reload")
    try:
        book = first_book(client)
        layer = client.get(f"/api/books/{book['id']}/overlays/1").json()["layers"][0]
        assert layer["note"] == "Full context · page 2" and layer["message"] == "" and layer["boxes"]
        # API 5: the text read aloud and handed to screen readers
        assert layer["speech"] == "Page 2. Haruka says: Hi."
    finally:
        dest.unlink()
        client.post("/api/plugins/reload")


def test_series_data(client, page_info):
    from app.db import SessionLocal
    from app.models import PluginSeriesData, Series
    from app.plugins import Context
    sid = series_id("Series B")
    with SessionLocal() as db:
        ctx = Context(page_info, db)
        series = db.get(Series, sid)
        ctx.clear_series_data(series)
        ctx.set_series_data(series, "notes", {"characters": ["Haruka"]})
        ctx.set_series_data(sid, "count", 1)          # the id works too (e.g. book.series_id)
        ctx.set_series_data(series, "count", 2)       # replaces
        db.commit()
        assert ctx.get_series_data(series, "notes") == {"characters": ["Haruka"]}
        assert ctx.series_data(sid) == {"notes": {"characters": ["Haruka"]}, "count": 2}
        assert ctx.get_series_data(series, "nope", "default") == "default"
        # another plugin's values are its own
        other = Context(type("Other", (), {"id": "other"})(), db)
        assert other.series_data(series) == {}
        # never shown on the series page
        assert all(f["plugin"] != "page-info" for f in client.get(f"/api/series/{sid}").json()["plugin_fields"])

        ctx.set_series_data(series, "count", None)
        db.commit()
        assert ctx.series_data(series) == {"notes": {"characters": ["Haruka"]}}
        ctx.clear_series_data(series, "notes")
        db.commit()
        assert ctx.series_data(series) == {}
        assert db.query(PluginSeriesData).filter_by(series_id=sid).count() == 0
