"""How a series reads (app/direction.py): paged or vertical. The owner's choice, its library folder, then
Settings → Reader. Paged always turns forward to the right, so there is no right to left any more."""
from sqlalchemy import text

from app import db as appdb
from app.db import SessionLocal
from app.models import Library, Series


def series_id(folder="Series B"):
    with SessionLocal() as db:
        return db.query(Series.id).filter_by(folder_name=folder).scalar()


def set_series(sid, **values):
    with SessionLocal() as db:
        s = db.get(Series, sid)
        for k, v in values.items():
            setattr(s, k, v)
        db.commit()


def direction(client, sid):
    d = client.get(f"/api/series/{sid}").json()["direction"]
    book = client.get(f"/api/series/{sid}/books").json()[0]
    # the reader gets the same, with the book
    assert client.get(f"/api/books/{book['id']}").json()["reader_mode"] == d["mode"]
    return d["mode"], d["source"]


def test_direction_in_order(client):
    sid = series_id()
    lib = client.get("/api/libraries").json()[0]["id"]
    with SessionLocal() as db:
        s = db.get(Series, sid)
        old = {k: getattr(s, k) for k in ("country_of_origin", "format", "reader_mode")}
    old_default = client.get("/api/settings").json()["reader_mode"]
    try:
        set_series(sid, reader_mode=None)
        client.put("/api/settings", json={"reader_mode": "paged"})
        # nothing set: Settings → Reader
        assert direction(client, sid) == ("paged", "default")
        client.put("/api/settings", json={"reader_mode": "vertical"})
        assert direction(client, sid) == ("vertical", "default")
        client.put("/api/settings", json={"reader_mode": "paged"})

        # the metadata no longer decides anything: a manga from Japan pages like the rest
        set_series(sid, country_of_origin="JP", format="MANGA")
        assert direction(client, sid) == ("paged", "default")

        # the library folder's mode
        for l in client.get("/api/libraries").json():
            assert client.patch(f"/api/libraries/{l['id']}", json={"reader_mode": "vertical"}).status_code == 200
        assert direction(client, sid) == ("vertical", "library")
        assert client.get("/api/libraries").json()[0]["reader_mode"] == "vertical"
        for bad in ("sideways", "rtl", "ltr"):
            assert client.patch(f"/api/libraries/{lib}", json={"reader_mode": bad}).status_code == 400
            assert client.patch(f"/api/series/{sid}", json={"reader_mode": bad}).status_code == 400

        # the owner's choice wins; "default" works it out again
        client.patch(f"/api/series/{sid}", json={"reader_mode": "paged"})
        assert direction(client, sid) == ("paged", "series")
        client.patch(f"/api/series/{sid}", json={"reader_mode": "default"})
        assert direction(client, sid) == ("vertical", "library")
    finally:
        client.put("/api/settings", json={"reader_mode": old_default})
        for l in client.get("/api/libraries").json():
            client.patch(f"/api/libraries/{l['id']}", json={"reader_mode": "default"})
        set_series(sid, **old)


def test_old_directions_become_pages(client):
    """Migration 4: rtl and ltr (series, library folders, the default) all become paged; vertical stays."""
    sid, other = series_id(), series_id("Hana to Akuma")
    lib = client.get("/api/libraries").json()[0]["id"]
    old_default = client.get("/api/settings").json()["reader_mode"]
    try:
        set_series(sid, reader_mode="rtl")
        set_series(other, reader_mode="vertical")
        with SessionLocal() as db:
            db.get(Library, lib).reader_mode = "ltr"
            db.commit()
        with appdb.engine.begin() as conn:
            conn.execute(text("INSERT OR REPLACE INTO setting (key, value) VALUES ('reader_mode', '\"rtl\"')"))
            conn.execute(text("INSERT OR REPLACE INTO setting (key, value) VALUES ('reader_tap_invert', 'true')"))
            appdb.MIGRATIONS[3](conn)
        with SessionLocal() as db:
            assert db.get(Series, sid).reader_mode == "paged"
            assert db.get(Series, other).reader_mode == "vertical"
            assert db.get(Library, lib).reader_mode == "paged"
            assert appdb.get_setting(db, "reader_mode") == "paged"
            assert appdb.get_setting(db, "reader_tap_invert") is None
    finally:
        client.put("/api/settings", json={"reader_mode": old_default})
        client.patch(f"/api/libraries/{lib}", json={"reader_mode": "default"})
        set_series(sid, reader_mode=None)
        set_series(other, reader_mode=None)


def test_plugins_get_the_direction(client):
    from app.plugins import Context, Plugin
    sid = series_id()
    with SessionLocal() as db:
        s = db.get(Series, sid)
        ctx = Context(Plugin(), db)
        assert ctx.reading_direction(s) in ("paged", "vertical")
