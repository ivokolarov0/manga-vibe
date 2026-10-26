"""Scanner, archive reading and API smoke tests."""
import shutil
import time
from pathlib import Path

from tests.conftest import TMP


def series_by_name(client):
    return {s["title"]: s for s in client.get("/api/series").json()["items"]}


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert (TMP / "data" / "mangavibe.db").exists()
    assert (TMP / "data" / "covers").is_dir()


def test_series_found_and_junk_ignored(client):
    names = series_by_name(client)
    assert set(names) == {"Hana to Akuma", "Series B", "Broken"}  # "Empty" is not a series


def test_book_order_and_revisions(client):
    sid = series_by_name(client)["Hana to Akuma"]["id"]
    books = client.get(f"/api/series/{sid}/books").json()
    assert [b["label"] for b in books] == ["Vol 1 Ch 1", "Vol 1 Ch 3", "Ch 52", "Extra 2"]
    assert books[1]["revision"] == 2
    all_books = client.get(f"/api/series/{sid}/books?all=1").json()
    hidden = [b["file_name"] for b in all_books if b["hidden"]]
    assert hidden == ["Hana_to_Akuma_v01_ch03_[OT].zip"]


def test_image_folder_book(client):
    sid = series_by_name(client)["Series B"]["id"]
    books = client.get(f"/api/series/{sid}/books").json()
    assert len(books) == 1  # @eaDir ignored
    assert books[0]["format"] == "folder" and books[0]["label"] == "Ch 5"
    assert books[0]["page_count"] == 3


def test_broken_file_reported(client):
    sid = series_by_name(client)["Broken"]["id"]
    books = client.get(f"/api/series/{sid}/books").json()
    assert books[0]["error"]


def test_pages_and_resize(client):
    sid = series_by_name(client)["Hana to Akuma"]["id"]
    book = client.get(f"/api/series/{sid}/books").json()[0]
    assert book["page_count"] == 10  # __MACOSX / Thumbs.db not counted
    r = client.get(f"/api/books/{book['id']}/pages/0")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]
    r2 = client.get(f"/api/books/{book['id']}/pages/0", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304
    small = client.get(f"/api/books/{book['id']}/pages/1?w=200")
    assert small.headers["content-type"] == "image/webp"
    assert client.get(f"/api/books/{book['id']}/pages/99").status_code == 404
    assert client.get(f"/api/books/{book['id']}/thumbnail").status_code == 200
    assert client.get(f"/api/series/{sid}/poster").status_code == 200


def test_progress_and_continue_reading(client):
    sid = series_by_name(client)["Hana to Akuma"]["id"]
    books = client.get(f"/api/series/{sid}/books").json()
    first = books[0]
    client.put(f"/api/books/{first['id']}/progress", json={"page": 4})
    cont = client.get("/api/continue-reading").json()
    assert cont[0]["id"] == sid and cont[0]["continue"] == {
        "book_id": first["id"], "page": 4, "label": "Vol 1 Ch 1", "page_count": 10}
    assert series_by_name(client)["Hana to Akuma"]["reading_status"] == "reading"

    r = client.put(f"/api/books/{first['id']}/progress", json={"page": 9}).json()
    assert r["completed"] is True  # last page reached
    info = client.get(f"/api/books/{first['id']}").json()
    assert info["next"]["id"] == books[1]["id"]
    assert client.get(f"/api/series/{sid}").json()["continue"]["book_id"] == books[1]["id"]

    client.post(f"/api/series/{sid}/mark-read")
    assert series_by_name(client)["Hana to Akuma"]["reading_status"] == "completed"
    client.post(f"/api/series/{sid}/mark-unread")
    assert series_by_name(client)["Hana to Akuma"]["unread_count"] == 4


def test_incremental_rescan_and_missing(client):
    from app.scanner import walker
    hana = TMP / "manga" / "Hana to Akuma"
    moved = TMP / "moved.zip"
    shutil.move(hana / "[KnO]Hana_to_Akuma52.zip", moved)
    walker.scan_all()
    sid = series_by_name(client)["Hana to Akuma"]["id"]
    assert "Ch 52" not in [b["label"] for b in client.get(f"/api/series/{sid}/books").json()]
    shutil.move(moved, hana / "[KnO]Hana_to_Akuma52.zip")
    walker.scan_all()
    assert "Ch 52" in [b["label"] for b in client.get(f"/api/series/{sid}/books").json()]


def test_path_safety(client):
    from fastapi import HTTPException

    from app.api.common import ensure_inside_library
    from app.db import SessionLocal
    with SessionLocal() as db:
        try:
            ensure_inside_library(db, str(TMP / "manga" / ".." / "data" / "mangavibe.db"))
            assert False, "should refuse"
        except HTTPException as e:
            assert e.status_code == 403


def test_overlay_look_settings(client):
    s = client.get("/api/settings").json()
    assert s["overlay_opacity"] == 100 and s["overlay_outline"] == "off"
    client.put("/api/settings", json={"overlay_opacity": 25, "overlay_outline": "thick"})
    s = client.get("/api/settings").json()
    assert s["overlay_opacity"] == 25 and s["overlay_outline"] == "thick"


def test_page_look_settings(client):
    s = client.get("/api/settings").json()
    assert (s["reader_brightness"], s["reader_contrast"], s["reader_colors"]) == (100, 100, "normal")
    client.put("/api/settings", json={"reader_brightness": 70, "reader_contrast": 125, "reader_colors": "dark-warm"})
    s = client.get("/api/settings").json()
    assert (s["reader_brightness"], s["reader_contrast"], s["reader_colors"]) == (70, 125, "dark-warm")
    client.put("/api/settings", json={"reader_brightness": 100, "reader_contrast": 100, "reader_colors": "normal"})


def test_settings(client):
    assert client.get("/api/settings").json()["reader_mode"] == "paged"
    client.put("/api/settings", json={"reader_mode": "vertical", "bogus": 1})
    s = client.get("/api/settings").json()
    assert s["reader_mode"] == "vertical" and "bogus" not in s
    assert s["reader_autohide"] == 5
    client.put("/api/settings", json={"reader_autohide": 0})
    assert client.get("/api/settings").json()["reader_autohide"] == 0
    assert s["reader_drawer_side"] == "right"
    client.put("/api/settings", json={"reader_drawer_side": "left"})
    assert client.get("/api/settings").json()["reader_drawer_side"] == "left"
    assert s["reader_tap_layout"] == "default" and "reader_tap_invert" not in s
    client.put("/api/settings", json={"reader_tap_layout": "one-handed"})
    s = client.get("/api/settings").json()
    assert s["reader_tap_layout"] == "one-handed"
    assert s["reader_volume_keys"] is True
    client.put("/api/settings", json={"reader_volume_keys": False})
    assert client.get("/api/settings").json()["reader_volume_keys"] is False


def test_example_plugin(client):
    from app.plugins import manager
    from app.scanner import walker
    plugin_dir = TMP / "data" / "plugins"
    shutil.copy(Path(__file__).parent.parent / "examples" / "plugins" / "translation_groups.py",
                plugin_dir)
    (plugin_dir / "broken.py").write_text("raise RuntimeError('boom')")
    data = client.post("/api/plugins/reload").json()
    assert [p["id"] for p in data["plugins"]] == ["anilist", "mangaupdates", "translation-groups"]
    assert "broken.py" in data["errors"]  # a broken plugin is reported, app keeps working

    (TMP / "manga" / "Hana to Akuma" / "series.json").write_text(
        '{"chapters": {"Hana_to_Akuma_Extra2.zip": 7}}')
    time.sleep(0.01)
    (TMP / "manga" / "Hana to Akuma" / "Hana_to_Akuma_Extra2.zip").touch()  # force re-read
    walker.scan_all()
    sid = series_by_name(client)["Hana to Akuma"]["id"]
    s = client.get(f"/api/series/{sid}").json()
    assert {"label": "Translation groups", "value": "KnO, OT"}.items() <= s["plugin_fields"][0].items()
    msg = client.post(f"/api/series/{sid}/actions/translation-groups/gaps").json()["message"]
    assert "7 -> 52" in msg
    labels = [b["label"] for b in client.get(f"/api/series/{sid}/books").json()]
    assert labels == ["Vol 1 Ch 1", "Vol 1 Ch 3", "Ch 7", "Ch 52"]  # series.json fixed the extra
    manager.disabled.add("translation-groups")
    assert client.get(f"/api/series/{sid}").json()["actions"] == []
    manager.disabled.clear()


def test_mark_unread_forgets_book(client):
    sid = series_by_name(client)["Hana to Akuma"]["id"]
    books = client.get(f"/api/series/{sid}/books").json()
    client.put(f"/api/books/{books[0]['id']}/progress", json={"page": 3})
    last = books[-1]["id"]
    client.put(f"/api/books/{last}/progress", json={"page": 5, "completed": True})
    client.put(f"/api/books/{last}/progress", json={"page": 0, "completed": False})  # "Unread" button
    cont = client.get(f"/api/series/{sid}").json()["continue"]
    assert cont["book_id"] == books[0]["id"] and cont["page"] == 3
    client.post(f"/api/series/{sid}/mark-unread")


def test_mark_unread_removes_from_continue(client):
    sid = series_by_name(client)["Hana to Akuma"]["id"]
    books = client.get(f"/api/series/{sid}/books").json()
    client.put(f"/api/books/{books[0]['id']}/progress", json={"page": 3})
    assert sid in [c["id"] for c in client.get("/api/continue-reading").json()]
    client.post(f"/api/series/{sid}/mark-unread")  # "Mark all unread"
    assert sid not in [c["id"] for c in client.get("/api/continue-reading").json()]

    # unreading the only started book one by one also drops the series
    client.put(f"/api/books/{books[1]['id']}/progress", json={"page": 2})
    assert sid in [c["id"] for c in client.get("/api/continue-reading").json()]
    client.put(f"/api/books/{books[1]['id']}/progress", json={"page": 0, "completed": False})
    assert sid not in [c["id"] for c in client.get("/api/continue-reading").json()]


def test_old_mangashelf_database_is_adopted(tmp_path, monkeypatch):
    import sqlite3

    from app import config, db
    old = tmp_path / "mangashelf.db"
    con = sqlite3.connect(old)
    con.execute("CREATE TABLE marker (x)")
    con.execute("INSERT INTO marker VALUES (42)")
    con.commit()
    con.close()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mangavibe.db")
    db._adopt_old_database()
    assert not old.exists() and (tmp_path / "mangavibe.db").exists()
    assert sqlite3.connect(tmp_path / "mangavibe.db").execute("SELECT x FROM marker").fetchone() == (42,)
    db._adopt_old_database()  # second start: nothing to do


def test_manual_chapter_range_reorders_and_survives_rescan(client):
    from app.scanner import walker
    from tests.conftest import make_zip
    folder = TMP / "manga" / "Odd Names"
    make_zip(folder / "Odd Names ch05.zip")
    make_zip(folder / "random_upload_final.zip")   # really chapters 10-18
    make_zip(folder / "Odd Names ch20.zip")
    walker.scan_all()
    sid = series_by_name(client)["Odd Names"]["id"]
    books = {b["file_name"]: b for b in client.get(f"/api/series/{sid}/books").json()}
    odd = books["random_upload_final.zip"]
    assert [b["label"] for b in client.get(f"/api/series/{sid}/books").json()][-1] == "random_upload_final"

    r = client.patch(f"/api/books/{odd['id']}", json={"chapter": 10, "chapter_end": 18})
    assert r.status_code == 200 and r.json()["label"] == "Ch 10-18" and r.json()["manual"]
    order = lambda: [b["label"] for b in client.get(f"/api/series/{sid}/books").json()]
    assert order() == ["Ch 5", "Ch 10-18", "Ch 20"]

    assert client.post(f"/api/series/{sid}/rescan").json() == {"removed": False, "books": 3, "errors": 0}
    walker.scan_all()
    assert order() == ["Ch 5", "Ch 10-18", "Ch 20"]          # kept after rescans

    assert client.patch(f"/api/books/{odd['id']}", json={"chapter": 18, "chapter_end": 10}).status_code == 400
    client.patch(f"/api/books/{odd['id']}", json={"hidden": True})
    assert order() == ["Ch 5", "Ch 20"]
    client.patch(f"/api/books/{odd['id']}", json={"reset": True})
    assert order()[-1] == "random_upload_final"
    import shutil
    shutil.rmtree(folder)
    walker.scan_all()


def test_rescan_single_series_picks_up_new_file(client):
    from tests.conftest import make_zip
    sid = series_by_name(client)["Hana to Akuma"]["id"]
    before = len(client.get(f"/api/series/{sid}/books").json())
    new = TMP / "manga" / "Hana to Akuma" / "Hana_to_Akuma99.zip"
    make_zip(new)
    assert client.post(f"/api/series/{sid}/rescan").json()["books"] >= before + 1
    assert "Ch 99" in [b["label"] for b in client.get(f"/api/series/{sid}/books").json()]
    new.unlink()
    client.post(f"/api/series/{sid}/rescan")


def test_rescan_deleted_series_removes_it(client):
    """Issue #9: rescanning a series whose folder was deleted hides it instead of a 404."""
    from app.scanner import walker
    from tests.conftest import make_zip
    folder = TMP / "manga" / "Gone Series"
    folder.mkdir()
    make_zip(folder / "Gone Series ch01.zip")
    walker.scan_all()
    sid = series_by_name(client)["Gone Series"]["id"]
    shutil.rmtree(folder)
    assert client.post(f"/api/series/{sid}/rescan").json()["removed"] is True
    assert "Gone Series" not in series_by_name(client)
    # the folder coming back brings the series back
    folder.mkdir()
    make_zip(folder / "Gone Series ch01.zip")
    walker.scan_all()
    assert series_by_name(client)["Gone Series"]["id"] == sid
    shutil.rmtree(folder)
    walker.scan_all()


def test_duplicate_groups_and_ignoring_rows(client):
    from app.scanner import walker
    from tests.conftest import make_zip
    folder = TMP / "manga" / "Dup Series"
    for name in ("[A] Dup Series ch01.zip", "[B] Dup Series ch01.zip", "[A] Dup Series ch02.zip",
                 "[B] Dup Series ch02.zip", "Dup Series ch09.zip"):
        make_zip(folder / name)
    walker.scan_all()
    att = client.get("/api/attention").json()
    groups = [d for d in att["duplicates"] if d["series"] == "Dup Series"]
    assert [g["label"] for g in groups] == ["Ch 1", "Ch 2"]          # one card per chapter
    assert [len(g["files"]) for g in groups] == [2, 2]
    assert {"thumbnail", "page_count", "file_size"} <= set(groups[0]["files"][0])
    assert groups[0]["cover"].startswith(f"/api/series/{groups[0]['series_id']}/poster?size=sm&v=")
    assert client.get(groups[0]["cover"]).status_code == 200
    gap = next(g for g in att["gaps"] if g["series"] == "Dup Series")
    before = att["count"]

    # ignore one duplicate group and the gap: they disappear from the lists and the badge
    assert client.post("/api/attention/ignore", json={"key": groups[0]["key"]}).json() == {"ok": True}
    client.post("/api/attention/ignore", json={"key": gap["key"]})
    att = client.get("/api/attention").json()
    assert [g["label"] for g in att["duplicates"] if g["series"] == "Dup Series"] == ["Ch 2"]
    assert not [g for g in att["gaps"] if g["series"] == "Dup Series"]
    assert att["count"] == before - 1 and att["ignored_counts"]["duplicates"] == 1
    shown = client.get("/api/attention?ignored=true").json()
    assert any(d["key"] == groups[0]["key"] and d["ignored"] for d in shown["duplicates"])

    client.post("/api/attention/ignore", json={"key": groups[0]["key"], "ignore": False})  # restore
    assert len([g for g in client.get("/api/attention").json()["duplicates"] if g["series"] == "Dup Series"]) == 2
    assert client.post("/api/attention/ignore", json={"key": "drop table;"}).status_code == 400
    import shutil
    shutil.rmtree(folder)
    walker.scan_all()


def test_attention_count_matches_full_lists(client):
    from app.scanner import walker
    from tests.conftest import make_zip
    folder = TMP / "manga" / "Count Series"
    for name in ("[A] Count Series ch01.zip", "[B] Count Series ch01.zip"):
        make_zip(folder / name)
    (folder / "broken.cbz").write_bytes(b"nope")
    walker.scan_all()
    full = client.get("/api/attention").json()["count"]
    assert client.get("/api/attention/count").json()["count"] == full
    dup = next(d for d in client.get("/api/attention").json()["duplicates"] if d["series"] == "Count Series")
    client.post("/api/attention/ignore", json={"key": dup["key"]})
    assert client.get("/api/attention/count").json()["count"] == full - 1 == client.get("/api/attention").json()["count"]
    client.post("/api/attention/ignore", json={"key": dup["key"], "ignore": False})

    # rescanning one series doesn't change the last full scan's error count
    errors = walker.status["errors"]
    sid = series_by_name(client)["Count Series"]["id"]
    assert client.post(f"/api/series/{sid}/rescan").json()["errors"] == 1
    assert walker.status["errors"] == errors
    import shutil
    shutil.rmtree(folder)
    walker.scan_all()


def test_same_match_groups_link_through_any_id():
    from app.api.attention import same_match_groups
    rows = [(1, {"anilist_id": 5}), (2, {"anilist_id": 5, "mal_id": 9}), (3, {"mal_id": 9}),
            (4, {"anilist_id": 6}), (5, {"mangadex_id": "x"})]
    [(key, ids, shared)] = same_match_groups(rows)
    assert key == "same:1-2-3" and ids == [1, 2, 3]
    assert shared == [("anilist_id", "5"), ("mal_id", "9")]
    big = same_match_groups([(i, {"anilist_id": 1}) for i in range(1000, 1030)])
    assert len(big[0][0]) <= 85  # long groups get a short key, which the ignore list accepts


def test_same_match_attention_list(client):
    """Issue #96: two series matched to the same entry are listed together; they can be ignored."""
    from app.db import SessionLocal
    from app.models import Series
    by_name = series_by_name(client)
    a, b = by_name["Hana to Akuma"]["id"], by_name["Series B"]["id"]
    with SessionLocal() as db:
        db.get(Series, a).anilist_id = db.get(Series, b).anilist_id = 777
        db.commit()
    try:
        att = client.get("/api/attention").json()
        [g] = att["same"]
        assert g["key"] == f"same:{min(a, b)}-{max(a, b)}"
        assert g["shared"] == [{"field": "anilist_id", "id": "777"}]
        assert {m["series_id"] for m in g["members"]} == {a, b}
        assert {"folder", "library", "books"} <= set(g["members"][0])
        assert client.get("/api/attention/count").json()["count"] == att["count"]
        assert client.post("/api/attention/ignore", json={"key": g["key"]}).json() == {"ok": True}
        after = client.get("/api/attention").json()
        assert after["same"] == [] and after["count"] == att["count"] - 1
        assert client.get("/api/attention/count").json()["count"] == after["count"]
        client.post("/api/attention/ignore", json={"key": g["key"], "ignore": False})
    finally:
        with SessionLocal() as db:
            db.get(Series, a).anilist_id = db.get(Series, b).anilist_id = None
            db.commit()


def test_tag_with_comma_filters(client):
    from app.db import SessionLocal
    from app.metadata import service
    from app.models import Series
    sid = series_by_name(client)["Berserk"]["id"] if "Berserk" in series_by_name(client) else \
        client.get("/api/series").json()["items"][0]["id"]
    with SessionLocal() as db:
        service.set_tags(db, db.get(Series, sid), ["Dark, Gritty"], [])
        db.commit()
    ids = [s["id"] for s in client.get("/api/series", params={"genre": "Dark, Gritty"}).json()["items"]]
    assert ids == [sid]
    assert sid not in [s["id"] for s in client.get("/api/series", params={"exclude": "Dark, Gritty"}).json()["items"]]
    with SessionLocal() as db:
        service.set_tags(db, db.get(Series, sid), [], [])
        db.commit()


def test_scan_single_library_only_touches_that_library(client):
    """Issue #21: scanning one library folder leaves the other libraries alone."""
    from app.db import SessionLocal
    from app.models import Library, Series
    from app.scanner import walker
    from tests.conftest import make_zip
    other_root = TMP / "manga2"
    make_zip(other_root / "Other Series" / "Other_ch01.zip")
    while walker.status["running"]:
        time.sleep(0.01)
    other_id = client.post("/api/libraries", json={"root_path": str(other_root)}).json()["id"]
    while walker.status["running"]:  # adding a folder scans only that folder
        time.sleep(0.01)
    assert walker.status["library_id"] == other_id
    with SessionLocal() as db:
        main_id = db.query(Library).filter_by(root_path=str(TMP / "manga")).one().id

    new = TMP / "manga" / "Brand New"
    make_zip(new / "Brand_New_ch01.zip")
    try:
        assert client.post(f"/api/libraries/{other_id}/scan").json()["started"] is True
        while walker.status["running"]:
            time.sleep(0.01)
        assert walker.status["library_id"] == other_id
        assert "Brand New" not in series_by_name(client)  # other library not scanned
        assert "Other Series" in series_by_name(client)

        walker.scan_all(main_id)
        assert "Brand New" in series_by_name(client)
        with SessionLocal() as db:
            libs = {s.folder_name: s.library_id for s in db.query(Series)}
        assert libs["Brand New"] == main_id and libs["Other Series"] == other_id
        assert client.post("/api/libraries/9999/scan").status_code == 404
    finally:
        shutil.rmtree(new)
        walker.scan_all()
        client.delete(f"/api/libraries/{other_id}")


def test_numbering_is_stored_and_kept_current(client):
    from app.db import SessionLocal
    from app.models import Series
    from app.scanner import walker
    from tests.conftest import make_zip
    folder = TMP / "manga" / "Numbered Series"
    for name in ("Numbered Series ch01.zip", "Numbered Series ch02.zip", "Numbered Series ch05.zip"):
        make_zip(folder / name)
    walker.scan_all()
    sid = series_by_name(client)["Numbered Series"]["id"]

    def stored():
        with SessionLocal() as db:
            return db.get(Series, sid).numbering

    assert stored()["chapters"] == [[1, 2], [5, 5]]
    assert any(g["series_id"] == sid for g in client.get("/api/attention").json()["gaps"])
    # an owner's chapter range fills the gap: the stored numbering follows
    book = next(b for b in client.get(f"/api/series/{sid}/books").json() if b["chapter"] == 2)
    client.patch(f"/api/books/{book['id']}", json={"chapter": 2, "chapter_end": 4})
    assert stored()["chapters"] == [[1, 5]]
    assert not any(g["series_id"] == sid for g in client.get("/api/attention").json()["gaps"])
    # a database from before the column: worked out on start
    with SessionLocal() as db:
        db.query(Series).filter(Series.id == sid).update({Series.numbering: None})
        db.commit()
        assert client.get(f"/api/series/{sid}").json()["collection"]["unit"] == "chapter"  # read before filling
        walker.fill_numbering(db)
    assert stored()["chapters"] == [[1, 5]]
    shutil.rmtree(folder)
    walker.scan_all()
