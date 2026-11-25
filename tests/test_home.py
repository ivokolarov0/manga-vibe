"""The Home page's rows (issue #291, app/api/home.py): GET /api/home and /api/home/sections, the rows' order and
which are hidden (home_layout), and the libraries plugin's Libraries row."""
import shutil
from datetime import timedelta

import pytest

from app.api.home import clean_layout, tiles
from app.db import SessionLocal
from app.models import Series
from app.scanner import walker
from tests.conftest import TMP, make_zip
from tests.test_shelves import L, comics  # noqa: F401 - comics is a fixture

CORE = ["continue", "added", "updated"]


def folder(name):
    """A series' id by its folder (other tests change titles, e.g. by matching)."""
    with SessionLocal() as db:
        return db.query(Series.id).filter(Series.folder_name == name).scalar()


def rows(client, **params):
    r = client.get("/api/home", params=params)
    assert r.status_code == 200, r.text
    return {s["id"]: s for s in r.json()["sections"]}


@pytest.fixture(autouse=True)
def fresh_layout(client):
    yield
    client.put("/api/settings", json={"home_layout": {}})


def test_the_rows_and_their_default_order(client):
    found = client.get("/api/home/sections").json()
    assert [s["id"] for s in found][:3] == CORE
    assert found[0] == {"id": "continue", "plugin": None, "title": "Continue reading", "kind": "series",
                        "layout": "continue", "see_all": "#/continue", "hidden": False}
    added = next(s for s in found if s["id"] == "added")
    assert added["see_all"] == "#/library?sort=added" and added["layout"] == "library"


def test_recently_added_newest_first(client):
    with SessionLocal() as db:
        want = [s.id for s in db.query(Series).filter(Series.missing_since.is_(None))
                .order_by(Series.added_at.desc(), Series.id.desc()).limit(10)]
    added = rows(client)["added"]
    assert [s["id"] for s in added["items"]] == want and len(added["items"]) <= 10
    card = added["items"][0]
    assert {"title", "poster", "reading_status", "unread_count", "incomplete", "updated_at"} <= set(card)


def updated_ids(client):
    return [s["id"] for s in rows(client).get("updated", {"items": []})["items"]]


def test_recently_updated_is_new_books(client):
    """A new book in a series that has been here a while puts it first; a book going missing, a series that
    was only just added (its first books, or more found right after) don't. The library's "updated" sort (its
    See all) starts the same way."""
    root = TMP / "manga" / "Updated Later"
    make_zip(root / "Updated_Later_v01.zip")
    walker.scan_all()
    sid = folder("Updated Later")
    make_zip(root / "Updated_Later_v02.zip")  # minutes after it was added: still part of adding it
    walker.scan_all()
    assert sid not in updated_ids(client)
    with SessionLocal() as db:  # it has been here for days
        s = db.get(Series, sid)
        s.added_at = s.added_at - timedelta(days=3)
        db.commit()
    (root / "Updated_Later_v02.zip").unlink()  # a book going missing is no update
    walker.scan_all()
    assert sid not in updated_ids(client)
    make_zip(root / "Updated_Later_v03.zip")  # a new book is
    walker.scan_all()
    try:
        assert updated_ids(client)[0] == sid
        library = client.get("/api/series", params={"sort": "updated", "limit": 5}).json()["items"]
        assert library[0]["id"] == sid
    finally:
        shutil.rmtree(root)
        walker.scan_all()


def test_continue_reading_row(client):
    sid = folder("Series B")
    book = client.get(f"/api/series/{sid}/books").json()[0]["id"]
    assert client.put(f"/api/books/{book}/progress", json={"page": 1}).status_code == 200
    row = rows(client)["continue"]
    assert row["items"][0]["id"] == sid and row["items"][0]["continue"]["book_id"] == book


def test_order_and_hidden_rows(client):
    r = client.put("/api/settings", json={"home_layout": {"order": ["updated", "added"], "hidden": ["continue"]}})
    assert r.status_code == 200 and r.json()["home_layout"] == {"order": ["updated", "added"], "hidden": ["continue"]}
    found = client.get("/api/home/sections").json()
    # the ones put in order first, the rest after them in the default order
    assert [s["id"] for s in found][:3] == ["updated", "added", "continue"]
    assert next(s for s in found if s["id"] == "continue")["hidden"]
    assert "continue" not in rows(client) and "added" in rows(client)
    assert client.get("/api/home").json()["all_hidden"] is False


def test_every_row_hidden(client):
    """Home then says so (all_hidden), not "no series yet"."""
    ids = [s["id"] for s in client.get("/api/home/sections").json()]
    client.put("/api/settings", json={"home_layout": {"hidden": ids}})
    assert client.get("/api/home").json() == {"sections": [], "all_hidden": True}


def test_tiles_without_an_id_or_twice_are_left_out():
    assert tiles([{"title": "A"}, {"id": 1, "title": "B"}, {"id": "1", "title": "C"}, {"id": 2}, "x"]) == [
        {"id": "1", "title": "B", "subtitle": "", "image": "", "href": "", "icon": ""}]


def test_a_saved_layout_is_cleaned():
    assert clean_layout(None) == {"order": [], "hidden": []}
    assert clean_layout({"order": ["a", 3, "a", "", "b"], "hidden": "x", "other": 1}) == {"order": ["a", "b"], "hidden": []}


def test_the_page_has_home(client):
    page = client.get("/").text
    assert 'x-show="$store.app.view === \'home\'"' in page and 'class="home-page-view"' in page
    # Home is #/ and always there; the library is #/library
    assert 'href="#/library"' in page and 'x-data="homeSettings()"' in page
    assert "home_page" not in client.get("/api/settings").json()  # no switch: Home can't be turned off


def test_the_libraries_row(client, comics):  # noqa: F811
    """A card per library once there are two, with its newest series' cover and its count; a locked one
    with neither. It follows the libraries plugin: off, there's no row."""
    tiles = {t["title"]: t for t in rows(client)["libraries:libraries"]["items"]}
    assert set(tiles) == {"manga", "comics"}
    assert tiles["comics"]["subtitle"] == "2 series" and tiles["comics"]["image"].startswith("/api/series/")
    assert tiles["comics"]["href"] == "#/library"
    cid = int(tiles["comics"]["id"])
    client.put(f"{L}/shelves/{cid}/password", json={"password": "secret"})
    client.post(f"{L}/shelves/{cid}/lock")
    found = rows(client)
    locked = next(t for t in found["libraries:libraries"]["items"] if t["title"] == "comics")
    assert locked["subtitle"] == "Locked" and locked["image"] == "" and locked["icon"] == "lock"
    # its series are left out of the other rows too
    assert "Comic One" not in [s["title"] for s in found["added"]["items"]]
    with SessionLocal() as db:
        from app.plugins.builtin.libraries import core as shelves
        shelves.clear_passwords(db, "all")
    client.put("/api/plugins/libraries", json={"enabled": False})
    assert "libraries:libraries" not in rows(client)
    assert "libraries:libraries" not in [s["id"] for s in client.get("/api/home/sections").json()]


def test_one_library_has_no_libraries_row(client):
    assert "libraries:libraries" not in rows(client)
