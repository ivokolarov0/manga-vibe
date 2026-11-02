"""A library folder's path outside Docker: set in Settings → Library folders, shown and copied on
the series page with the series' own folder added."""
from types import SimpleNamespace

from app.api.series import host_path
from app.db import SessionLocal
from app.models import Series


def lib(root, host):
    return SimpleNamespace(root_path=root, host_path=host)


def test_host_path_joins_the_folder_under_the_root():
    assert host_path(lib("/manga", "/volume1/manga"), "/manga/MF Ghost (Digital)") == "/volume1/manga/MF Ghost (Digital)"
    assert host_path(lib("/manga/", "/volume1/manga/"), "/manga/A/B") == "/volume1/manga/A/B"
    # a Windows or SMB path gets backslashes
    assert host_path(lib("/manga", "\\\\NAS\\manga"), "/manga/MF Ghost") == "\\\\NAS\\manga\\MF Ghost"
    assert host_path(lib("/manga", "D:\\Manga\\"), "/manga/One/Two") == "D:\\Manga\\One\\Two"
    # the library folder itself
    assert host_path(lib("/manga", "/volume1/manga"), "/manga") == "/volume1/manga"


def test_no_host_path_without_a_setting_or_outside_the_root():
    assert host_path(None, "/manga/A") is None
    assert host_path(lib("/manga", None), "/manga/A") is None
    assert host_path(lib("/manga", "/volume1/manga"), "/mangaX/A") is None  # not under /manga


def test_set_and_clear_it(client):
    l = client.get("/api/libraries").json()[0]
    with SessionLocal() as db:
        s = db.query(Series).filter_by(library_id=l["id"]).first()
        sid, rest = s.id, s.folder_path[len(l["root_path"]):].replace("\\", "/").strip("/")
    try:
        assert client.get(f"/api/series/{sid}").json()["host_path"] is None
        assert client.patch(f"/api/libraries/{l['id']}", json={"host_path": "  /volume1/manga/ "}).status_code == 200
        assert client.get("/api/libraries").json()[0]["host_path"] == "/volume1/manga/"
        assert client.get(f"/api/series/{sid}").json()["host_path"] == "/volume1/manga/" + rest
        # the folder's library is left alone when only the path changes, and the other way round
        assert client.get("/api/libraries").json()[0]["shelf_id"] == l["shelf_id"]
        client.patch(f"/api/libraries/{l['id']}", json={"shelf_id": l["shelf_id"]})
        assert client.get("/api/libraries").json()[0]["host_path"] == "/volume1/manga/"
    finally:
        client.patch(f"/api/libraries/{l['id']}", json={"host_path": ""})
    assert client.get("/api/libraries").json()[0]["host_path"] is None
