"""Accounts, a built-in plugin (app/plugins/builtin/accounts/), and the core's users (app/users.py): each
account has its own reading, favorites and reader settings; admins manage the app; an admin picks the
libraries an account sees."""
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, set_setting
from app.models import Progress, SeriesState, Setting
from app.plugins import manager
from app.plugins.builtin.accounts import core as accounts
from app.plugins.builtin.libraries import core as shelves

A = "/api/plugins/accounts/api"
L = "/api/plugins/libraries/api"


def series_ids(c, **params):
    r = c.get("/api/series", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {s["title"]: s["id"] for s in r.json()["items"]}


@pytest.fixture
def accounts_on(client):
    """Accounts switched on, with the admin "Ivo" logged in on `client`; a second browser for others."""
    from app.main import app
    assert next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "accounts")["enabled"] is False
    assert client.put("/api/plugins/accounts", json={"enabled": True}).status_code == 200
    other = TestClient(app)
    try:
        yield other
    finally:
        # off again (not through the API: nobody may be logged in any more), and forget the accounts
        client.cookies.clear()
        manager.set_enabled("accounts", False)
        with SessionLocal() as db:
            set_setting(db, "plugins_disabled", sorted(manager.disabled))
            db.query(Setting).filter(Setting.key.in_([accounts.KEY, shelves.ACCESS_KEY])).delete()
            db.query(Setting).filter(Setting.key.like("user:%")).delete(synchronize_session=False)
            db.query(Progress).filter(Progress.user_id != 1).delete()
            db.query(SeriesState).filter(SeriesState.user_id != 1).delete()
            db.commit()
        accounts.invalidate()
        shelves.invalidate()


def test_off_by_default_then_setup(client, accounts_on):
    # on: nothing works without logging in, except the page itself and the login
    assert client.get("/api/series").status_code == 401
    assert client.get("/api/series").json()["detail"]["login"] is True
    assert client.get("/").status_code == 200
    assert client.get(f"{A}/me").json() == {"user": None, "setup": True}
    assert client.post(f"{A}/login", json={"name": "x", "password": "yyyy"}).status_code == 403
    # the first account is the admin, with the owner's reading (user 1)
    r = client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    assert r.json()["user"] == {"id": 1, "name": "Ivo", "admin": True}
    assert client.post(f"{A}/setup", json={"name": "Two", "password": "secret2"}).status_code == 409
    assert client.get("/api/settings").json()["me"] == {"id": 1, "name": "Ivo", "can_manage": True}
    assert client.get("/api/series").status_code == 200


def test_each_account_reads_on_its_own(client, accounts_on):
    other = accounts_on
    # before accounts: the owner read a book and starred a series
    client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    sid = series_ids(client)["Series B"]
    book = client.get(f"/api/series/{sid}/books").json()[0]["id"]
    client.put(f"/api/books/{book}/progress", json={"page": 1})
    client.patch(f"/api/series/{sid}", json={"favorite": True})
    client.post(f"{A}/accounts", json={"name": "Kid", "password": "kid1", "admin": False})
    assert client.post(f"{A}/accounts", json={"name": "kid", "password": "kid2"}).status_code == 400  # same name

    assert other.post(f"{A}/login", json={"name": "kid", "password": "nope"}).status_code == 403
    assert other.post(f"{A}/login", json={"name": " Kid ", "password": "kid1"}).status_code == 200
    me = other.get("/api/settings").json()["me"]
    assert me["name"] == "Kid" and me["can_manage"] is False
    # the kid's own: nothing read, no favorites, nothing to continue
    assert client.get(f"/api/books/{book}/progress").json()["page"] == 1
    assert other.get(f"/api/books/{book}/progress").json() == {"page": 0, "completed": False}
    assert "Series B" in series_ids(client, fav=1) and not series_ids(other, fav=1)
    assert sid in [s["id"] for s in client.get("/api/continue-reading").json()]
    assert sid not in [s["id"] for s in other.get("/api/continue-reading").json()]
    # what the kid does stays the kid's
    other.put(f"/api/books/{book}/progress", json={"page": 2})
    other.patch(f"/api/series/{sid}", json={"reading_status": "on_hold"})
    assert client.get(f"/api/books/{book}/progress").json()["page"] == 1
    assert other.get(f"/api/series/{sid}").json()["manual_reading_status"] == "on_hold"
    assert client.get(f"/api/series/{sid}").json()["manual_reading_status"] is None
    assert "Series B" in series_ids(other, read="on_hold") and "Series B" not in series_ids(client, read="on_hold")
    # Settings → Reader is each one's own; the rest only for admins
    assert other.put("/api/settings", json={"reader_mode": "vertical"}).status_code == 200
    assert other.get("/api/settings").json()["reader_mode"] == "vertical"
    assert client.get("/api/settings").json()["reader_mode"] != "vertical"
    assert other.get(f"/api/series/{sid}").json()["direction"]["mode"] == "vertical"
    assert other.put("/api/settings", json={"theme": "light"}).status_code == 403
    # the library page's own "per page" picker is for everyone
    assert other.put("/api/settings", json={"library_page_size": 120}).json()["library_page_size"] == 120
    assert client.get("/api/settings").json()["library_page_size"] != 120
    # and saved searches
    other.post("/api/plugins/saved-searches/api/searches", json={"name": "Mine", "query": "fav=1"})
    assert [x["name"] for x in other.get("/api/plugins/saved-searches/api/searches").json()] == ["Mine"]
    assert client.get("/api/plugins/saved-searches/api/searches").json() == []

    # deleting the kid deletes the kid's reading
    kid = next(a["id"] for a in client.get(f"{A}/accounts").json() if a["name"] == "Kid")
    assert client.delete(f"{A}/accounts/{kid}").status_code == 200
    with SessionLocal() as db:
        assert not db.query(Progress).filter(Progress.user_id == kid).count()
        assert not db.query(SeriesState).filter(SeriesState.user_id == kid).count()
    assert other.get("/api/series").status_code == 401  # logged out: the account is gone
    client.put(f"/api/books/{book}/progress", json={"page": 0, "completed": False})
    client.patch(f"/api/series/{sid}", json={"favorite": False})


def test_only_admins_manage(client, accounts_on):
    other = accounts_on
    client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    client.post(f"{A}/accounts", json={"name": "Kid", "password": "kid1"})
    other.post(f"{A}/login", json={"name": "Kid", "password": "kid1"})
    sid = series_ids(other)["Series B"]
    book = other.get(f"/api/series/{sid}/books").json()[0]["id"]
    for method, url, body in (
            ("post", "/api/scan", None), ("get", "/api/plugins", None), ("get", "/api/folders", None),
            ("put", f"/api/series/{sid}/metadata", {"title_english": "x"}), ("delete", f"/api/series/{sid}", None),
            ("patch", f"/api/series/{sid}", {"reader_mode": "vertical"}), ("post", f"/api/series/{sid}/refresh", None),
            ("patch", f"/api/books/{book}", {"volume": 2}), ("put", "/api/plugins/accounts", {"enabled": False}),
            ("get", "/api/update", None), ("post", f"{A}/accounts", {"name": "Hack", "password": "hack"}),
            ("post", f"{L}/shelves", {"name": "Mine"}), ("post", "/api/series/bulk", {"ids": [sid], "action": "refresh"})):
        r = getattr(other, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, url, r.status_code)
    # but reading things are the kid's to do
    assert other.patch(f"/api/series/{sid}", json={"favorite": True}).status_code == 200
    assert other.post("/api/series/bulk", json={"ids": [sid], "action": "read"}).json()["done"] == 1
    assert other.post(f"/api/series/{sid}/mark-unread").status_code == 200
    # unread on series never touched makes no empty rows
    kid = other.get("/api/settings").json()["me"]["id"]
    ids = list(series_ids(other).values())
    other.post("/api/series/bulk", json={"ids": ids, "action": "unread"})
    with SessionLocal() as db:
        empty = db.query(SeriesState).filter(SeriesState.user_id == kid, SeriesState.favorite.is_(False),
                                             SeriesState.reading_status.is_(None),
                                             SeriesState.last_read_at.is_(None)).count()
    assert empty <= 1  # at most Series B's, whose favorite was set and cleared
    # the first account stays an admin and can't be deleted
    assert client.patch(f"{A}/accounts/1", json={"admin": False}).status_code == 400
    assert client.delete(f"{A}/accounts/1").status_code == 400


def test_password_change_logs_out_elsewhere(client, accounts_on):
    other = accounts_on
    client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    other.post(f"{A}/login", json={"name": "Ivo", "password": "secret1"})
    assert other.get("/api/series").status_code == 200
    assert client.put(f"{A}/me/password", json={"current": "wrong", "password": "newpass"}).status_code == 403
    assert client.put(f"{A}/me/password", json={"current": "secret1", "password": "newpass"}).status_code == 200
    assert client.get("/api/series").status_code == 200   # this browser stays in
    assert other.get("/api/series").status_code == 401    # the other one is out
    assert other.post(f"{A}/login", json={"name": "Ivo", "password": "newpass"}).status_code == 200
    client.post(f"{A}/logout")
    assert client.get("/api/series").status_code == 401


def test_admin_picks_the_libraries(client, accounts_on):
    other = accounts_on
    client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    kid = client.post(f"{A}/accounts", json={"name": "Kid", "password": "kid1"}).json()["id"]
    kids = client.post(f"{L}/shelves", json={"name": "Kids"}).json()["id"]
    sid = series_ids(client)["Series B"]
    other_sid = series_ids(client)["Hana to Akuma"]
    client.put(f"{L}/series/{sid}", json={"shelf_id": kids})
    try:
        other.post(f"{A}/login", json={"name": "Kid", "password": "kid1"})
        assert len(other.get(f"{L}/shelves").json()) == 2  # no limit yet
        # nothing to guard so far (no passwords, no limits): the limit must still apply at once
        shelves.protected_hint = shelves.access_hint = False
        assert client.put(f"{L}/access/{kid}", json={"shelves": [kids]}).status_code == 200
        assert other.get(f"/api/series/{other_sid}").status_code == 403
        assert [s["name"] for s in other.get(f"{L}/shelves").json()] == ["Kids"]
        assert set(series_ids(other)) == {"Series B"}
        assert other.get(f"/api/series/{other_sid}").status_code == 403  # LockGuard too
        assert other.get(f"/api/series/{sid}").status_code == 200
        assert other.get(f"{L}/access").status_code == 403  # admins only
        # admins see everything; back to all
        assert "Hana to Akuma" in series_ids(client)
        client.put(f"{L}/access/{kid}", json={"shelves": None})
        assert "Hana to Akuma" in series_ids(other)
    finally:
        client.put(f"{L}/series/{sid}", json={"shelf_id": 0})
        client.delete(f"{L}/shelves/{kids}")


def test_switched_off_everyone_is_the_owner(client, accounts_on):
    client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    sid = series_ids(client)["Series B"]
    client.patch(f"/api/series/{sid}", json={"favorite": True})
    assert client.put("/api/plugins/accounts", json={"enabled": False}).status_code == 200
    client.cookies.clear()
    try:
        # no login, and the first admin's reading is the owner's
        assert client.get("/api/settings").json()["me"] == {"id": 1, "name": "", "can_manage": True}
        assert "Series B" in series_ids(client, fav=1)
    finally:
        client.patch(f"/api/series/{sid}", json={"favorite": False})
