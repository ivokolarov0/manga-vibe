"""Deleting a series: its folder on the drive and everything the app keeps about it (issue #104)."""
import shutil

from tests.conftest import TMP, make_zip


def make_series(client, name, files=("ch01.zip", "ch02.zip", "ch05.zip")):
    from app.scanner import walker
    folder = TMP / "manga" / name
    for f in files:
        make_zip(folder / f"{name} {f}")
    walker.scan_all()
    items = client.get("/api/series", params={"q": name}).json()["items"]
    return folder, next(s["id"] for s in items if s["title"] == name)


def rows(sid, book_ids):
    """How many rows still point at the series or its books, per table."""
    from sqlalchemy import text
    from app.db import SessionLocal
    out = {}
    with SessionLocal() as db:
        for table in ("series", "series_tag", "link", "plugin_field", "plugin_series"):
            col = "id" if table == "series" else "series_id"
            out[table] = db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :s"), {"s": sid}).scalar()
        ids = ",".join(map(str, book_ids)) or "0"
        for table, col in (("book", "id"), ("progress", "book_id"), ("plugin_page", "book_id"),
                           ("overlay_hidden", "book_id")):
            out[table] = db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {col} IN ({ids})")).scalar()
    return out


def add_everything(sid, book_ids):
    """Progress, tags, links, plugin data, a poster, thumbnails and an ignored attention row."""
    from app import config
    from app.db import SessionLocal, set_setting
    from app.models import (Link, OverlayHidden, PluginField, PluginPage, PluginSeriesData, Progress,
                            SeriesTag, Tag)
    with SessionLocal() as db:
        tag = db.query(Tag).filter_by(name="Delete-test tag").one_or_none() or Tag(name="Delete-test tag")
        db.add(tag)
        db.flush()
        db.add_all([SeriesTag(series_id=sid, tag_id=tag.id), Link(series_id=sid, site="AniList", url="x"),
                    PluginField(series_id=sid, plugin="p", key="k", label="K", value=1),
                    PluginSeriesData(series_id=sid, plugin="p", key="k", value=1),
                    Progress(book_id=book_ids[0], page=2),
                    PluginPage(plugin="p", book_id=book_ids[0], page=0, value=1),
                    OverlayHidden(book_id=book_ids[0], plugin="p", page=0, box="1,1")])
        set_setting(db, "attention_ignored", [f"gap:{sid}", f"err:{book_ids[0]}", f"same:{sid}-999999",
                                              "gap:999999"])
        db.commit()
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    for p in (config.COVERS_DIR / f"{sid}.jpg", config.COVERS_DIR / f"{sid}_sm.webp"):
        p.write_bytes(b"x")
    (config.PAGES_DIR / str(book_ids[0])).mkdir(parents=True, exist_ok=True)
    (config.PAGES_DIR / str(book_ids[0]) / "a_0_800.webp").write_bytes(b"x")


def test_delete_info(client):
    folder, sid = make_series(client, "Info Series")
    info = client.get(f"/api/series/{sid}/delete-info").json()
    assert info["can_delete"] is True and info["reason"] is None
    assert info["folder"] == str(folder) and info["files"] == 3 and info["size"] > 0
    shutil.rmtree(folder)
    client.post(f"/api/series/{sid}/rescan")


def test_delete_removes_folder_and_everything_in_the_database(client):
    from app import config
    from app.db import SessionLocal, get_setting
    from app.scanner import walker
    folder, sid = make_series(client, "Unwanted Series")
    book_ids = [b["id"] for b in client.get(f"/api/series/{sid}/books?all=true").json()]
    add_everything(sid, book_ids)
    assert all(n > 0 for n in rows(sid, book_ids).values())

    assert client.delete(f"/api/series/{sid}").json() == {"deleted": True}

    assert not folder.exists()
    assert rows(sid, book_ids) == {k: 0 for k in rows(sid, book_ids)}   # nothing left behind
    assert client.get(f"/api/series/{sid}").status_code == 404
    assert not (config.COVERS_DIR / f"{sid}.jpg").exists()
    assert not (config.COVERS_DIR / f"{sid}_sm.webp").exists()
    assert not (config.PAGES_DIR / str(book_ids[0])).exists()
    assert not any((config.THUMBS_DIR / f"{b}.webp").exists() for b in book_ids)
    with SessionLocal() as db:  # its ignored attention rows are gone, other series' stay
        assert get_setting(db, "attention_ignored") == ["gap:999999"]
    walker.scan_all()  # the next scan doesn't bring it back
    assert not client.get("/api/series", params={"q": "Unwanted Series"}).json()["items"]


def test_read_only_mount_is_explained_and_nothing_is_touched(client, monkeypatch):
    from app.scanner import deleting
    folder, sid = make_series(client, "Read Only Series")
    monkeypatch.setattr(deleting, "_read_only_mount", lambda path: True)
    info = client.get(f"/api/series/{sid}/delete-info").json()
    assert info["can_delete"] is False and ":ro" in info["reason"] and info["files"] == 3
    r = client.delete(f"/api/series/{sid}")
    assert r.status_code == 400 and "read-only" in r.json()["detail"]
    # a writable mount, but the app's user may not write there: a different fix
    monkeypatch.setattr(deleting, "_read_only_mount", lambda path: False)
    monkeypatch.setattr(deleting.os, "access", lambda path, mode: False)
    r = client.delete(f"/api/series/{sid}")
    assert r.status_code == 400 and "PUID" in r.json()["detail"]
    assert folder.is_dir() and client.get(f"/api/series/{sid}").status_code == 200
    monkeypatch.undo()
    client.delete(f"/api/series/{sid}")


def test_refuses_folders_that_are_not_a_series_folder(client):
    """Only a folder directly inside a library folder: never the library itself or something deeper."""
    from app.db import SessionLocal
    from app.models import Series
    folder, sid = make_series(client, "Odd Place Series")
    for bad in (TMP / "manga", folder / "sub"):
        bad.mkdir(exist_ok=True)
        with SessionLocal() as db:
            db.get(Series, sid).folder_path = str(bad)
            db.commit()
        r = client.delete(f"/api/series/{sid}")
        assert r.status_code == 400 and "directly inside a library folder" in r.json()["detail"]
        assert bad.is_dir()
    with SessionLocal() as db:
        db.get(Series, sid).folder_path = str(folder)
        db.commit()
    assert client.delete(f"/api/series/{sid}").status_code == 200
    assert (TMP / "manga" / "Hana to Akuma").is_dir()  # the rest of the library is untouched


def test_partly_deleted_keeps_the_series_with_what_is_left(client, monkeypatch):
    from app.scanner import deleting
    folder, sid = make_series(client, "Stuck Series")
    stuck = folder / "Stuck Series ch05.zip"
    real_rmtree = shutil.rmtree

    def rmtree(path, onexc=None, **kw):  # every file goes except one that is "in use"
        for f in path.iterdir():
            if f != stuck:
                f.unlink()
        onexc(stuck.unlink, str(stuck), OSError(16, "Device or resource busy"))

    monkeypatch.setattr(deleting.shutil, "rmtree", rmtree)
    r = client.delete(f"/api/series/{sid}")
    assert r.status_code == 500
    assert "1 files could not be deleted" in r.json()["detail"] and "busy" in r.json()["detail"]
    labels = [b["label"] for b in client.get(f"/api/series/{sid}/books").json()]
    assert labels == ["Ch 5"]  # rescanned: only what is left
    monkeypatch.setattr(deleting.shutil, "rmtree", real_rmtree)
    assert client.delete(f"/api/series/{sid}").status_code == 200


def test_plugins_see_the_series_before_it_goes(client, monkeypatch):
    from app.plugins import manager
    folder, sid = make_series(client, "Hooked Series")
    seen = []
    real = manager.run_hook
    monkeypatch.setattr(manager, "run_hook", lambda db, hook, *a: (seen.append((hook, a[0].folder_name, folder.exists()))
                                                                    if hook == "on_series_deleted" else real(db, hook, *a)))
    assert client.delete(f"/api/series/{sid}").status_code == 200
    assert seen == [("on_series_deleted", "Hooked Series", True)]


def test_missing_series(client):
    assert client.delete("/api/series/999999").status_code == 404
    assert client.get("/api/series/999999/delete-info").status_code == 404
