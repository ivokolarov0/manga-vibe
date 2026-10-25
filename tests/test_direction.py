"""Which way a series reads (app/direction.py): the owner's choice, its metadata, its library folder, then
Settings → Reader."""
from app.db import SessionLocal
from app.models import Series


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
        set_series(sid, country_of_origin=None, format=None, reader_mode=None)
        client.put("/api/settings", json={"reader_mode": "rtl"})
        # not matched, nothing set: Settings → Reader (manga by default)
        assert direction(client, sid) == ("rtl", "default")
        client.put("/api/settings", json={"reader_mode": "ltr"})
        assert direction(client, sid) == ("ltr", "default")
        client.put("/api/settings", json={"reader_mode": "rtl"})

        # the library folder's direction, for its unmatched series
        for l in client.get("/api/libraries").json():
            assert client.patch(f"/api/libraries/{l['id']}", json={"reader_mode": "vertical"}).status_code == 200
        assert direction(client, sid) == ("vertical", "library")
        assert client.get("/api/libraries").json()[0]["reader_mode"] == "vertical"
        assert client.patch(f"/api/libraries/{lib}", json={"reader_mode": "sideways"}).status_code == 400

        # matched: from Japan is manga, anything else reads left to right (the library's choice no longer counts)
        set_series(sid, country_of_origin="JP", format="MANGA")
        assert direction(client, sid) == ("rtl", "metadata")
        set_series(sid, country_of_origin="KR", format="MANGA")  # a manhwa
        assert direction(client, sid) == ("ltr", "metadata")
        set_series(sid, country_of_origin=None, format="MANGA")  # MangaUpdates' OEL: no country
        assert direction(client, sid) == ("ltr", "metadata")

        # the owner's choice wins; "default" works it out again
        client.patch(f"/api/series/{sid}", json={"reader_mode": "vertical"})
        assert direction(client, sid) == ("vertical", "series")
        client.patch(f"/api/series/{sid}", json={"reader_mode": "default"})
        assert direction(client, sid) == ("ltr", "metadata")
    finally:
        client.put("/api/settings", json={"reader_mode": old_default})
        for l in client.get("/api/libraries").json():
            client.patch(f"/api/libraries/{l['id']}", json={"reader_mode": "default"})
        set_series(sid, **old)


def test_plugins_get_the_direction(client):
    from app.plugins import Context, Plugin
    sid = series_id()
    with SessionLocal() as db:
        s = db.get(Series, sid)
        ctx = Context(Plugin(), db)
        assert ctx.reading_direction(s) in ("rtl", "ltr", "vertical")
