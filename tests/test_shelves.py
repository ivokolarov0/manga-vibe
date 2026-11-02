"""Libraries (Manga, Comics...), called shelves in the code (app/shelves.py): a folder's series show in
the folder's library unless a series is moved to another one; per-library reading mode; passwords."""
import shutil
import time

import pytest

from app import shelves
from app.db import SessionLocal
from app.models import Library, Series, Shelf
from app.scanner import walker
from tests.conftest import TMP, make_zip


def wait_for_scan():
    while walker.status["running"]:
        time.sleep(0.01)


def names(client, **params):
    r = client.get("/api/series", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {s["title"]: s["id"] for s in r.json()["items"]}


def shelf_named(client, name):
    return next(s for s in client.get("/api/shelves").json() if s["name"] == name)


@pytest.fixture
def comics(client):
    """A second folder, /comics, with two series: added in Settings, so it gets its own library."""
    root = TMP / "comics"
    make_zip(root / "Comic One" / "Comic_One_001.zip")
    make_zip(root / "Comic Two" / "Comic_Two_001.zip")
    wait_for_scan()
    folder = client.post("/api/libraries", json={"root_path": str(root)}).json()["id"]
    wait_for_scan()
    yield folder
    client.cookies.clear()
    with SessionLocal() as db:
        shelves.clear_passwords(db, "all")
    client.delete(f"/api/libraries/{folder}")
    for s in client.get("/api/shelves").json():
        if s["name"] not in ("manga",):
            client.delete(f"/api/shelves/{s['id']}")
    with SessionLocal() as db:
        db.query(Series).update({Series.shelf_id: None})
        db.commit()
    shutil.rmtree(root, ignore_errors=True)


def test_one_folder_is_one_library(client):
    """An install with one folder: one library named like it, holding everything, as before."""
    libs = client.get("/api/shelves").json()
    manga = shelf_named(client, "manga")
    folder = next(f for f in client.get("/api/libraries").json() if f["root_path"] == str(TMP / "manga"))
    assert folder["shelf_id"] == manga["id"]
    assert manga["folders"][0]["id"] == folder["id"] and not manga["locked"] and not manga["has_password"]
    assert names(client) == names(client, shelf=manga["id"]) and len(libs) >= 1


def test_upgrade_gives_each_folder_a_library(client):
    """Migration 14 + ensure_folder_shelves(): every folder gets its own library named like it, with
    the reading mode it had (then cleared on the folder). Two folders with the same name stay apart."""
    with SessionLocal() as db:
        a = Library(name="Old Folder", root_path=str(TMP / "old-folder"), reader_mode="paged")
        b = Library(name="old folder", root_path=str(TMP / "old-folder-2"), reader_mode="vertical")
        db.add_all([a, b])
        db.commit()
        try:
            shelves.ensure_folder_shelves(db)
            sa, sb = db.get(Shelf, a.shelf_id), db.get(Shelf, b.shelf_id)
            assert (sa.name, sa.reader_mode, sa.made_for_folder) == ("Old Folder", "paged", a.id)
            assert (sb.name, sb.reader_mode) == ("old folder (2)", "vertical")
            assert a.reader_mode is None and b.reader_mode is None
        finally:
            ids = [a.shelf_id, b.shelf_id]
            db.delete(a)
            db.delete(b)
            db.query(Shelf).filter(Shelf.id.in_(ids)).delete()
            db.commit()


def test_a_library_made_by_the_owner_stays(client):
    """A library the owner made before adding its folder takes the folder, and stays when the folder
    is removed; one the app made for a folder goes with it, unless the owner set it up."""
    keep, made_here = TMP / "Keep Me", TMP / "Made Here"
    for root in (keep, made_here):
        make_zip(root / f"In {root.name}" / "Book_001.zip")
    mine = client.post("/api/shelves", json={"name": "keep me"}).json()["id"]
    client.patch(f"/api/shelves/{mine}", json={"reader_mode": "vertical"})
    folder_of = lambda fid: next(f for f in client.get("/api/libraries").json() if f["id"] == fid)  # noqa: E731
    folders = []
    try:
        for root in (keep, made_here):
            wait_for_scan()
            folders.append(client.post("/api/libraries", json={"root_path": str(root)}).json()["id"])
        wait_for_scan()
        # the owner's "keep me" (no folder yet) takes /Keep Me; /Made Here gets a new one
        assert folder_of(folders[0])["shelf_id"] == mine
        made = folder_of(folders[1])["shelf_id"]
        assert made != mine and shelf_named(client, "Made Here")["id"] == made
        client.patch(f"/api/shelves/{made}", json={"name": "Renamed"})  # now it's the owner's
        for fid in folders:
            assert client.delete(f"/api/libraries/{fid}").status_code == 200
        assert shelf_named(client, "keep me")["reader_mode"] == "vertical"
        assert shelf_named(client, "Renamed")["id"] == made
    finally:
        for fid in folders:
            client.delete(f"/api/libraries/{fid}")
        for s in client.get("/api/shelves").json():
            if s["name"] != "manga":
                client.delete(f"/api/shelves/{s['id']}")
        for root in (keep, made_here):
            shutil.rmtree(root, ignore_errors=True)


def test_folder_and_series_libraries_survive_rescans(client, comics):
    comics_shelf = shelf_named(client, "comics")
    manga = shelf_named(client, "manga")
    assert set(names(client, shelf=comics_shelf["id"])) == {"Comic One", "Comic Two"}
    assert "Comic One" not in names(client, shelf=manga["id"])
    assert "Comic One" in names(client)  # All
    # one series from /manga shows under comics, without moving files
    sid = names(client)["Series B"]
    assert client.patch(f"/api/series/{sid}", json={"shelf_id": comics_shelf["id"]}).status_code == 200
    s = client.get(f"/api/series/{sid}").json()
    assert s["shelf"]["id"] == comics_shelf["id"] and s["shelf_override"] and s["folder_shelf"]["id"] == manga["id"]
    assert "Series B" in names(client, shelf=comics_shelf["id"]) and "Series B" not in names(client, shelf=manga["id"])

    # rescans and a restart don't move it back
    walker.scan_all()
    client.post(f"/api/series/{sid}/rescan")
    with SessionLocal() as db:
        walker.ensure_libraries(db, [str(TMP / "manga")])
    assert "Series B" in names(client, shelf=comics_shelf["id"])
    # the tags and Continue reading follow the library too
    assert client.get("/api/tags", params={"shelf": comics_shelf["id"]}).status_code == 200
    assert client.get("/api/continue-reading", params={"shelf": comics_shelf["id"]}).status_code == 200

    # "Use the folder's library" puts it back
    client.patch(f"/api/series/{sid}", json={"shelf_id": 0})
    assert "Series B" in names(client, shelf=manga["id"])
    assert client.patch(f"/api/series/{sid}", json={"shelf_id": 9999}).status_code == 404

    # a whole folder to another library
    client.patch(f"/api/libraries/{comics}", json={"shelf_id": manga["id"]})
    assert "Comic One" in names(client, shelf=manga["id"])
    client.patch(f"/api/libraries/{comics}", json={"shelf_id": comics_shelf["id"]})


def test_bulk_move(client, comics):
    comics_shelf = shelf_named(client, "comics")
    with SessionLocal() as db:
        ids = [db.query(Series.id).filter_by(folder_name=f).scalar() for f in ("Series B", "Hana to Akuma")]
    ids_in = lambda **p: set(names(client, **p).values())  # noqa: E731
    r = client.post("/api/series/bulk", json={"ids": ids, "action": "shelf", "shelf_id": comics_shelf["id"]})
    assert r.json()["done"] == 2
    assert set(ids) <= ids_in(shelf=comics_shelf["id"])
    client.post("/api/series/bulk", json={"ids": ids, "action": "shelf", "shelf_id": 0})
    assert not set(ids) & ids_in(shelf=comics_shelf["id"])
    assert client.post("/api/series/bulk", json={"ids": ids, "action": "shelf"}).status_code == 400


def test_reader_mode_per_library(client, comics):
    comics_shelf, manga = shelf_named(client, "comics"), shelf_named(client, "manga")
    sid = names(client)["Series B"]
    mode = lambda: client.get(f"/api/series/{sid}").json()["direction"]  # noqa: E731
    try:
        client.patch(f"/api/shelves/{comics_shelf['id']}", json={"reader_mode": "vertical"})
        client.patch(f"/api/shelves/{manga['id']}", json={"reader_mode": "paged"})
        assert mode() == {"mode": "paged", "source": "library"}
        # moved to Comics: Comics' mode
        client.patch(f"/api/series/{sid}", json={"shelf_id": comics_shelf["id"]})
        assert mode() == {"mode": "vertical", "source": "library"}
        # unless it has its own
        client.patch(f"/api/series/{sid}", json={"reader_mode": "paged"})
        assert mode() == {"mode": "paged", "source": "series"}
    finally:
        client.patch(f"/api/series/{sid}", json={"reader_mode": "default", "shelf_id": 0})
        client.patch(f"/api/shelves/{manga['id']}", json={"reader_mode": "default"})


def test_add_rename_delete(client, comics):
    r = client.post("/api/shelves", json={"name": "  Manhwa "})
    new = r.json()["id"]
    assert r.json()["name"] == "Manhwa"
    assert client.post("/api/shelves", json={"name": "manhwa"}).status_code == 400  # same name
    assert client.patch(f"/api/shelves/{new}", json={"name": "Webtoons"}).status_code == 200
    assert client.patch(f"/api/shelves/{new}", json={"name": "comics"}).status_code == 400

    # deleting: its folders and series fall back to another library, nothing disappears
    sid = names(client)["Series B"]
    client.patch(f"/api/series/{sid}", json={"shelf_id": new})
    client.patch(f"/api/libraries/{comics}", json={"shelf_id": new})
    before = set(names(client))
    r = client.delete(f"/api/shelves/{new}")
    assert r.status_code == 200
    fallback = r.json()["moved_to"]["id"]
    assert set(names(client)) == before
    assert {"Comic One", "Comic Two"} <= set(names(client, shelf=fallback))
    assert client.get(f"/api/series/{sid}").json()["shelf_override"] is False


def test_the_last_library_stays(client):
    with SessionLocal() as db:
        others = db.query(Shelf).filter(Shelf.name != "manga").all()
        assert not others, "other tests left libraries behind"
    manga = shelf_named(client, "manga")
    assert client.delete(f"/api/shelves/{manga['id']}").status_code == 400


def test_password(client, comics):
    comics_shelf = shelf_named(client, "comics")
    cid = comics_shelf["id"]
    one = names(client)["Comic One"]
    book = client.get(f"/api/series/{one}/books").json()[0]["id"]
    client.put(f"/api/books/{book}/progress", json={"page": 1})
    client.put(f"/api/series/{one}/metadata", json={"links": [{"site": "Site", "url": "https://example.com/x"}]})
    link = client.get(f"/api/series/{one}").json()["links"][0]["id"]
    assert client.put(f"/api/shelves/{cid}/password", json={"password": ""}).status_code == 400
    assert client.put(f"/api/shelves/{cid}/password", json={"password": "secret"}).status_code == 200
    with SessionLocal() as db:
        stored = db.get(Shelf, cid).password_hash
    assert stored.startswith("scrypt$") and "secret" not in stored
    # setting it leaves this browser unlocked; Lock now locks it
    assert "Comic One" in names(client)
    client.post(f"/api/shelves/{cid}/lock")

    shelf = shelf_named(client, "comics")
    assert shelf["locked"] and shelf["has_password"] and shelf["series"] is None
    # hidden everywhere: All, search, Continue reading, Needs attention, counts
    assert "Comic One" not in names(client) and not names(client, q="Comic")
    assert one not in [s["id"] for s in client.get("/api/continue-reading").json()]
    folder = next(f for f in client.get("/api/libraries").json() if f["id"] == comics)
    assert folder["series"] == 0
    assert client.get("/api/series", params={"shelf": cid}).status_code == 403
    assert client.get("/api/attention").status_code == 200
    # the server refuses its content, not only the page
    for url in (f"/api/series/{one}", f"/api/series/{one}/books", f"/api/series/{one}/poster",
                f"/api/books/{book}", f"/api/books/{book}/pages/0", f"/api/books/{book}/thumbnail",
                f"/api/books/{book}/progress"):
        assert client.get(url).status_code == 403, url
    assert client.patch(f"/api/series/{one}", json={"shelf_id": 0}).status_code == 403
    r = client.post("/api/series/bulk", json={"ids": [one], "action": "shelf", "shelf_id": 0})
    assert r.json()["done"] == 0
    assert client.delete(f"/api/shelves/{cid}").status_code == 403  # would show its series elsewhere
    manga = shelf_named(client, "manga")["id"]
    assert client.patch(f"/api/libraries/{comics}", json={"shelf_id": manga}).status_code == 403
    # nor can it be renamed, its folder removed (with its series), its links or attention rows used
    assert client.patch(f"/api/shelves/{cid}", json={"name": "Mine now"}).status_code == 403
    assert client.delete(f"/api/libraries/{comics}").status_code == 403
    assert client.get(f"/api/links/{link}/icon").status_code == 403
    assert client.post("/api/attention/ignore", json={"key": f"err:{book}"}).status_code == 403
    assert client.post("/api/attention/ignore", json={"key": f"gap:{one}"}).status_code == 403
    # a series moved into it is locked at once (LockGuard's cache is cleared)
    b = names(client)["Series B"]
    assert client.get(f"/api/series/{b}").status_code == 200
    client.patch(f"/api/series/{b}", json={"shelf_id": cid})
    assert client.get(f"/api/series/{b}").status_code == 403

    # wrong password, then the right one
    assert client.post(f"/api/shelves/{cid}/unlock", json={"password": "nope"}).status_code == 403
    assert client.post(f"/api/shelves/{cid}/unlock", json={"password": "secret"}).status_code == 200
    client.patch(f"/api/series/{b}", json={"shelf_id": 0})  # Series B back to its folder's library
    assert client.get(f"/api/books/{book}/pages/0").status_code == 200
    assert "Comic One" in names(client) and shelf_named(client, "comics")["series"] == 2

    # a new tab locks everything again
    client.post("/api/shelves/lock")
    assert client.get(f"/api/series/{one}").status_code == 403

    # changing the password needs the current one, and locks browsers unlocked with the old one
    client.post(f"/api/shelves/{cid}/unlock", json={"password": "secret"})
    old_cookie = client.cookies.get(shelves.COOKIE)
    assert client.put(f"/api/shelves/{cid}/password", json={"current": "x", "password": "new"}).status_code == 403
    assert client.put(f"/api/shelves/{cid}/password", json={"current": "secret", "password": "new"}).status_code == 200
    assert client.get(f"/api/series/{one}").status_code == 200  # this browser stays unlocked
    client.cookies.clear()
    client.cookies.set(shelves.COOKIE, old_cookie)
    assert client.get(f"/api/series/{one}").status_code == 403
    client.cookies.clear()

    # removing it
    assert client.put(f"/api/shelves/{cid}/password", json={"current": "new", "password": ""}).json() == \
        {"ok": True, "has_password": False}
    assert client.get(f"/api/series/{one}").status_code == 200


def test_wrong_passwords_slow_down(client, comics):
    cid = shelf_named(client, "comics")["id"]
    client.put(f"/api/shelves/{cid}/password", json={"password": "secret"})
    client.post("/api/shelves/lock")
    for _ in range(shelves.MAX_TRIES):
        assert client.post(f"/api/shelves/{cid}/unlock", json={"password": "guess"}).status_code == 403
    r = client.post(f"/api/shelves/{cid}/unlock", json={"password": "secret"})
    assert r.status_code == 429 and "Try again in" in r.json()["detail"]
    shelves._fails.clear()
    assert client.post(f"/api/shelves/{cid}/unlock", json={"password": "secret"}).status_code == 200


def test_forgot_password(client, comics):
    cid = shelf_named(client, "comics")["id"]
    client.put(f"/api/shelves/{cid}/password", json={"password": "secret"})
    client.post("/api/shelves/lock")
    with SessionLocal() as db:
        assert shelves.clear_passwords(db, "nothing like it") == []
        assert shelves.clear_passwords(db, "COMICS") == ["comics"]
    assert "Comic One" in names(client)
    client.put(f"/api/shelves/{cid}/password", json={"password": "secret"})
    with SessionLocal() as db:
        shelves.clear_from_env(db, "all")
        assert db.get(Shelf, cid).password_hash is None


def test_removing_a_folder_removes_its_empty_library(client):
    root = TMP / "extra-folder"
    make_zip(root / "Extra" / "Extra_001.zip")
    wait_for_scan()
    folder = client.post("/api/libraries", json={"root_path": str(root)}).json()["id"]
    wait_for_scan()
    assert shelf_named(client, "extra-folder")["folders"][0]["id"] == folder
    client.delete(f"/api/libraries/{folder}")
    assert "extra-folder" not in [s["name"] for s in client.get("/api/shelves").json()]
    shutil.rmtree(root, ignore_errors=True)


def test_password_hash():
    h = shelves.hash_password("pässword")
    assert shelves.check_password("pässword", h)
    assert not shelves.check_password("password", h)
    assert not shelves.check_password("x", None) and not shelves.check_password("x", "garbage")
