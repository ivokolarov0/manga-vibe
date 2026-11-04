"""Accounts (app/users.py) and who sees what (app/access.py): own progress, status, favorites and
settings, shared libraries and single series (read only), admin-only server settings, signing in."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


def ids_of(c, **params):
    """{title: id} of the series this account's library grid shows."""
    return {s["title"]: s["id"] for s in c.get("/api/series", params={"limit": 200, **params}).json()["items"]}


def seen(c, **params):
    """The ids of the series this account's library grid shows."""
    return set(ids_of(c, **params).values())


def first_book(c, sid):
    return c.get(f"/api/series/{sid}/books").json()[0]


@pytest.fixture
def family(client):
    """The admin (on `client`, now with a password and signed in), Kid (a PIN) and Guest (no password),
    each on a browser of their own. Afterwards everything is as before: one admin, no password."""
    from app.main import app
    assert client.get("/api/auth").json()["open"] is True
    # a second account needs the admin to have a password first
    assert client.post("/api/users/admin", json={"name": "Kid"}).status_code == 400
    assert client.put("/api/account/password", json={"password": "admin-pw"}).status_code == 200
    kid_id = client.post("/api/users/admin", json={"name": "Kid", "password": "1234", "kind": "pin"}).json()["id"]
    guest_id = client.post("/api/users/admin", json={"name": "Guest"}).json()["id"]
    assert client.post("/api/users/admin", json={"name": "kid"}).status_code == 400  # names are unique
    kid, guest = TestClient(app), TestClient(app)
    assert kid.post("/api/auth/sign-in", json={"user_id": kid_id, "password": "1234"}).status_code == 200
    assert guest.post("/api/auth/sign-in", json={"user_id": guest_id}).status_code == 200
    yield {"admin": client, "kid": kid, "guest": guest, "kid_id": kid_id, "guest_id": guest_id}
    for uid in (kid_id, guest_id):
        client.delete(f"/api/users/admin/{uid}")
    assert client.put("/api/account/password", json={"current": "admin-pw", "password": ""}).status_code == 200
    assert client.get("/api/auth").json()["open"] is True


def test_one_account_without_a_password_needs_no_sign_in(client):
    a = client.get("/api/auth").json()
    assert a["open"] and a["user"]["is_admin"] and a["accounts"] == 1
    assert client.get("/api/series").status_code == 200


def test_signed_out_gets_nothing_but_the_sign_in_page(client, family):
    from app.main import app
    stranger = TestClient(app)
    a = stranger.get("/api/auth").json()
    assert a["user"] is None and not a["open"]
    assert {p["name"]: p["password_kind"] for p in a["profiles"]} == {"Admin": "password", "Kid": "pin", "Guest": None}
    hana = ids_of(client)["Hana to Akuma"]
    book = first_book(client, hana)["id"]
    for url in ("/api/series", "/api/settings", "/api/shelves", "/api/continue-reading", f"/api/series/{hana}",
                f"/api/series/{hana}/poster", f"/api/books/{book}/pages/0", f"/api/books/{book}/thumbnail",
                "/api/libraries", "/api/users"):
        assert stranger.get(url).status_code == 401, url
    assert stranger.get("/").status_code == 200 and stranger.get("/api/health").status_code == 200
    # the admin can hide the profiles: the page then asks for a name
    client.put("/api/settings", json={"profile_picker": False})
    assert stranger.get("/api/auth").json()["profiles"] is None
    client.put("/api/settings", json={"profile_picker": True})
    # wrong passwords; then signing in by name
    assert stranger.post("/api/auth/sign-in", json={"name": "Admin", "password": "nope"}).status_code == 403
    assert stranger.post("/api/auth/sign-in", json={"name": "Nobody", "password": "x"}).status_code == 403
    assert stranger.post("/api/auth/sign-in", json={"name": "admin", "password": "admin-pw"}).status_code == 200
    assert stranger.get("/api/auth").json()["user"]["name"] == "Admin"
    stranger.post("/api/auth/sign-out")
    assert stranger.get("/api/series").status_code == 401


def test_a_pin_cant_be_guessed_quickly(client, family):
    from app.main import app
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/sign-in", json={"user_id": family["kid_id"], "password": "0000"}).status_code == 403
    r = c.post("/api/auth/sign-in", json={"user_id": family["kid_id"], "password": "1234"})
    assert r.status_code == 429 and "Try again" in r.json()["detail"]


def test_a_new_password_signs_out_the_other_browsers(client, family):
    from app.main import app
    kid, other = family["kid"], TestClient(app)
    other.post("/api/auth/sign-in", json={"user_id": family["kid_id"], "password": "1234"})
    assert other.get("/api/series").status_code == 200
    assert kid.put("/api/account/password", json={"current": "1234", "password": "12"}).status_code == 400
    assert kid.put("/api/account/password", json={"current": "1234", "password": "4321", "kind": "pin"}).status_code == 200
    assert kid.get("/api/series").status_code == 200   # this browser stays signed in
    assert other.get("/api/series").status_code == 401
    # the admin resets it (e.g. forgotten)
    client.patch(f"/api/users/admin/{family['kid_id']}", json={"password": "1234", "kind": "pin"})
    assert kid.get("/api/series").status_code == 401
    assert kid.post("/api/auth/sign-in", json={"user_id": family["kid_id"], "password": "1234"}).status_code == 200


def test_only_the_admin_runs_the_server(client, family):
    kid = family["kid"]
    for method, url in (("get", "/api/libraries"), ("get", "/api/plugins"), ("post", "/api/scan"),
                        ("get", "/api/update"), ("get", "/api/folders"), ("get", "/api/metadata/providers"),
                        ("post", "/api/users/admin"), ("delete", f"/api/users/admin/{family['guest_id']}"),
                        ("get", "/api/shelves?all=true")):
        assert getattr(kid, method)(url).status_code == 403, url
    assert kid.put("/api/settings", json={"auto_match": False}).status_code == 403
    assert client.get("/api/settings").json()["auto_match"] is True
    # its own settings are its own
    assert kid.put("/api/settings", json={"theme": "light", "title_language": "native"}).status_code == 200
    assert kid.get("/api/settings").json()["theme"] == "light"
    assert client.get("/api/settings").json()["theme"] == "dark"
    # the names of the others, for sharing; the details only for the admin
    users = kid.get("/api/users").json()
    assert {u["name"] for u in users} == {"Admin", "Kid", "Guest"} and "is_admin" not in users[0]
    assert client.get("/api/users").json()[0]["is_admin"] is True
    # the last admin stays one, and can't remove itself
    me = client.get("/api/auth").json()["user"]["id"]
    assert client.patch(f"/api/users/admin/{me}", json={"is_admin": False}).status_code == 400
    assert client.delete(f"/api/users/admin/{me}").status_code == 400


def test_a_new_account_sees_nothing_until_something_is_shared(client, family):
    kid = family["kid"]
    assert kid.get("/api/series").json()["total"] == 0
    assert kid.get("/api/shelves").json() == []
    hana = ids_of(client)["Hana to Akuma"]
    book = first_book(client, hana)
    # not shared: its page, thumbnail, poster and details answer as if there were no such series
    for url in (f"/api/series/{hana}", f"/api/series/{hana}/books", f"/api/series/{hana}/poster",
                f"/api/books/{book['id']}", f"/api/books/{book['id']}/pages/0",
                f"/api/books/{book['id']}/thumbnail", f"/api/books/{book['id']}/progress"):
        assert kid.get(url).status_code == 404, url
    assert kid.put(f"/api/books/{book['id']}/progress", json={"page": 1}).status_code == 404
    assert kid.get("/api/series", params={"q": "Hana"}).json()["total"] == 0
    assert kid.get("/api/tags").json() == []
    assert kid.get("/api/attention").json()["count"] == 0


def test_two_readers_have_their_own_progress(client, family):
    admin, kid = client, family["kid"]
    hana = ids_of(admin)["Hana to Akuma"]
    shelf = admin.get(f"/api/series/{hana}").json()["shelf"]["id"]
    assert admin.put(f"/api/shelves/{shelf}/shares", json={"user_ids": [family["kid_id"]]}).status_code == 200
    try:
        books = admin.get(f"/api/series/{hana}/books").json()
        before = {b["id"]: (b["page"], b["completed"]) for b in books}
        kid.put(f"/api/books/{books[0]['id']}/progress", json={"page": 3})
        kid.patch(f"/api/series/{hana}", json={"favorite": True, "reading_status": "on_hold"})
        kid.patch(f"/api/series/{hana}", json={"reader_mode": "vertical"})
        mine = kid.get(f"/api/series/{hana}").json()
        assert mine["favorite"] and mine["reading_status"] == "on_hold" and mine["direction"]["source"] == "series"
        assert mine["access"] == "read" and mine["owner"] == "Admin"
        assert kid.get(f"/api/series/{hana}/books").json()[0]["page"] == 3
        assert hana in [s["id"] for s in kid.get("/api/continue-reading").json()]
        assert hana in seen(kid, fav=True) and hana in seen(kid, read="on_hold")
        # nothing changed for the admin
        theirs = admin.get(f"/api/series/{hana}").json()
        assert theirs["access"] == "edit" and theirs["owner"] is None
        assert {b["id"]: (b["page"], b["completed"]) for b in admin.get(f"/api/series/{hana}/books").json()} == before
        assert hana not in seen(admin, read="on_hold") and hana not in seen(admin, fav=True)
        assert mine["continue"]["book_id"] == books[0]["id"] and mine["continue"]["page"] == 3
    finally:
        kid.post(f"/api/series/{hana}/mark-unread")
        kid.patch(f"/api/series/{hana}", json={"favorite": False, "clear_reading_status": True, "reader_mode": "default"})
        admin.put(f"/api/shelves/{shelf}/shares", json={"user_ids": []})


def test_a_shared_library_is_read_only(client, family):
    admin, kid = client, family["kid"]
    hana = ids_of(admin)["Hana to Akuma"]
    shelf = admin.get(f"/api/series/{hana}").json()["shelf"]["id"]
    admin.put(f"/api/shelves/{shelf}/shares", json={"user_ids": [family["kid_id"]]})
    try:
        tab = kid.get("/api/shelves").json()
        assert [(s["id"], s["mine"], s["owner"]["name"]) for s in tab] == [(shelf, False, "Admin")]
        assert hana in seen(kid) and hana in seen(kid, shelf=shelf)
        book = first_book(kid, hana)["id"]
        assert kid.get(f"/api/books/{book}/pages/0").status_code == 200
        # reading is fine; changing the series isn't
        assert kid.put(f"/api/books/{book}/progress", json={"page": 1}).status_code == 200
        for method, url, body in (("put", f"/api/series/{hana}/metadata", {"description": "x"}),
                                  ("patch", f"/api/books/{book}", {"volume": 9}),
                                  ("post", f"/api/series/{hana}/refresh", None),
                                  ("post", f"/api/series/{hana}/rescan", None),
                                  ("post", f"/api/series/{hana}/poster", {"source": "first_page"}),
                                  ("delete", f"/api/series/{hana}", None),
                                  ("get", f"/api/series/{hana}/delete-info", None),
                                  ("put", f"/api/series/{hana}/shares", {"user_ids": []}),
                                  ("patch", f"/api/shelves/{shelf}", {"name": "Mine now"}),
                                  ("put", f"/api/shelves/{shelf}/shares", {"user_ids": []}),
                                  ("delete", f"/api/shelves/{shelf}", None)):
            kw = {"json": body} if body is not None else {}
            r = kid.request(method.upper(), url, **kw)
            assert r.status_code == 403, (url, r.status_code)
        assert kid.patch(f"/api/series/{hana}", json={"shelf_id": 0}).status_code == 403
        assert kid.post("/api/series/bulk", json={"ids": [hana], "action": "refresh"}).json()["done"] == 0
        assert kid.get("/api/attention").json()["count"] == 0  # nothing of its own to fix
        # stop sharing: gone for the kid; its progress comes back when it's shared again
        admin.put(f"/api/shelves/{shelf}/shares", json={"user_ids": []})
        assert hana not in seen(kid) and kid.get(f"/api/series/{hana}").status_code == 404
        admin.put(f"/api/shelves/{shelf}/shares", json={"user_ids": [family["kid_id"]]})
        assert kid.get(f"/api/books/{book}/progress").json()["page"] == 1
    finally:
        kid.post(f"/api/series/{hana}/mark-unread")
        admin.put(f"/api/shelves/{shelf}/shares", json={"user_ids": []})


def test_one_series_shared_on_its_own(client, family):
    admin, guest = client, family["guest"]
    found = ids_of(admin)
    hana, other = found["Hana to Akuma"], found["Series B"]
    assert admin.get(f"/api/series/{hana}/shares").status_code == 200
    assert admin.put(f"/api/series/{hana}/shares", json={"user_ids": [family["guest_id"]]}).status_code == 200
    mine = guest.post("/api/shelves", json={"name": "Favourites"}).json()["id"]
    try:
        # only that one: not the rest of its library, not through search or counts
        assert set(ids_of(guest).values()) == {hana}
        assert guest.get("/api/series", params={"q": "Series B"}).json()["total"] == 0
        assert guest.get(f"/api/series/{other}").status_code == 404
        tabs = {s["id"]: s for s in guest.get("/api/shelves").json()}
        assert tabs[-1]["name"] == "Shared with me" and tabs[-1]["series"] == 1 and tabs[mine]["series"] == 0
        assert set(ids_of(guest, shelf=-1).values()) == {hana}
        s = guest.get(f"/api/series/{hana}").json()
        assert s["shared_alone"] and s["shelf"]["id"] == -1 and s["access"] == "read"
        # the guest puts it in a library of its own; the owner's stays where it was
        before = admin.get(f"/api/series/{hana}").json()["shelf"]
        assert guest.patch(f"/api/series/{hana}", json={"shelf_id": mine}).status_code == 200
        assert set(ids_of(guest, shelf=mine).values()) == {hana} and not ids_of(guest, shelf=-1)
        assert -1 not in {x["id"] for x in guest.get("/api/shelves").json()}
        assert admin.get(f"/api/series/{hana}").json()["shelf"] == before
        # someone else's library isn't the guest's to put it in
        assert guest.patch(f"/api/series/{hana}", json={"shelf_id": before["id"]}).status_code == 404
        # stop sharing
        admin.put(f"/api/series/{hana}/shares", json={"user_ids": []})
        assert not ids_of(guest) and guest.get(f"/api/series/{hana}").status_code == 404
        assert guest.get(f"/api/books/{first_book(admin, hana)['id']}/pages/0").status_code == 404
    finally:
        admin.put(f"/api/series/{hana}/shares", json={"user_ids": []})
        guest.delete(f"/api/shelves/{mine}")


def test_same_series_shared_twice_shows_once(client, family):
    admin, kid = client, family["kid"]
    hana = ids_of(admin)["Hana to Akuma"]
    shelf = admin.get(f"/api/series/{hana}").json()["shelf"]["id"]
    admin.put(f"/api/shelves/{shelf}/shares", json={"user_ids": [family["kid_id"]]})
    admin.put(f"/api/series/{hana}/shares", json={"user_ids": [family["kid_id"]]})
    try:
        items = kid.get("/api/series", params={"limit": 200}).json()["items"]
        assert [s["id"] for s in items].count(hana) == 1
    finally:
        admin.put(f"/api/series/{hana}/shares", json={"user_ids": []})
        admin.put(f"/api/shelves/{shelf}/shares", json={"user_ids": []})


def test_the_admin_gives_a_library_away(client, family):
    admin, kid = client, family["kid"]
    new = admin.post("/api/shelves", json={"name": "For the kid"}).json()["id"]
    assert admin.patch(f"/api/shelves/{new}", json={"owner_id": family["kid_id"]}).status_code == 200
    tab = kid.get("/api/shelves").json()
    assert [(s["id"], s["mine"]) for s in tab] == [(new, True)]
    assert kid.patch(f"/api/shelves/{new}", json={"name": "Mine"}).status_code == 200
    # removing the account gives its libraries to the admin
    every = {s["id"]: s for s in admin.get("/api/shelves", params={"all": True}).json()}
    assert every[new]["owner"]["name"] == "Kid"
    admin.delete(f"/api/users/admin/{family['kid_id']}")
    assert {s["id"]: s for s in admin.get("/api/shelves").json()}[new]["owner"]["name"] == "Admin"
    admin.delete(f"/api/shelves/{new}")


def test_plugins_know_whose_reading_it_is(client, family):
    from app.db import SessionLocal
    from app.models import Series
    from app.plugins import Context, Plugin
    hana = ids_of(client)["Hana to Akuma"]
    with SessionLocal() as db:
        s = db.get(Series, hana)
        kid = Context(Plugin(), db, family["kid_id"])
        kid.set_reading_status(s, "dropped")
        db.commit()
        assert kid.reading_status(s) == "dropped" and kid.user_name == "Kid"
        assert Context(Plugin(), db).reading_status(s) != "dropped"  # the admin's
        kid.set_reading_status(s, None)
        db.commit()


def test_an_unlock_token_is_no_session(client, family):
    """The same key signs both, in the same shape: neither may pass for the other."""
    from types import SimpleNamespace
    from app import shelves, users
    from app.db import SessionLocal
    from app.main import app
    with SessionLocal() as db:
        kid = users.accounts(db)[family["kid_id"]]
        # an unlock token for a library whose id and lock version match the kid's id and session version
        token = shelves.make_token(db, SimpleNamespace(id=kid.id, lock_version=kid.session_version))
        assert users.account_of(db, token) is None
        db.commit()
    c = TestClient(app)
    c.cookies.set(users.COOKIE, token)
    assert c.get("/api/series").status_code == 401


def test_signing_in_locks_what_the_last_person_unlocked(client, family):
    r = family["kid"].post("/api/auth/sign-in", json={"user_id": family["kid_id"], "password": "1234"})
    gone = [c for c in r.headers.get_list("set-cookie") if c.startswith("mv_unlocked=")]
    assert gone and "Max-Age=0" in gone[0]


def test_plugin_pages_are_for_every_account(client, family):
    # not refused as an admin page: the plugin answers (here: there's no such plugin)
    r = family["guest"].get("/api/plugins/nope/web/callback")
    assert r.status_code == 404
    assert family["guest"].get("/api/plugins").status_code == 403


def test_moving_a_series_no_longer_shared(client, family):
    from app.db import SessionLocal
    from app.models import SeriesShare
    admin, guest = client, family["guest"]
    hana = ids_of(admin)["Hana to Akuma"]
    admin.put(f"/api/series/{hana}/shares", json={"user_ids": [family["guest_id"]]})
    assert guest.get(f"/api/series/{hana}").status_code == 200
    with SessionLocal() as db:  # stopped by another process: the caches don't know yet
        db.query(SeriesShare).filter_by(series_id=hana).delete()
        db.commit()
    assert guest.patch(f"/api/series/{hana}", json={"shelf_id": 0}).status_code == 404
    assert guest.get(f"/api/series/{hana}").status_code == 404


def test_removing_an_account_tidies_its_shares(client, family):
    admin, kid = client, family["kid"]
    hana = ids_of(admin)["Hana to Akuma"]
    shelf = admin.get(f"/api/series/{hana}").json()["shelf"]["id"]
    # the kid's own library holds Hana, shared with the admin on its own
    mine = kid.post("/api/shelves", json={"name": "Kid stuff"}).json()["id"]
    admin.patch(f"/api/shelves/{shelf}", json={"owner_id": family["kid_id"]})
    kid.put(f"/api/series/{hana}/shares", json={"user_ids": [1]})
    assert any(s["id"] == -1 for s in admin.get("/api/shelves").json())
    admin.delete(f"/api/users/admin/{family['kid_id']}")
    tabs = admin.get("/api/shelves").json()
    assert not any(s["id"] == -1 for s in tabs)  # not twice: in its library and in Shared with me
    assert [i for i in admin.get("/api/series", params={"limit": 200}).json()["items"] if i["id"] == hana]
    assert {shelf, mine} <= {s["id"] for s in tabs}
    admin.delete(f"/api/shelves/{mine}")


def test_giving_a_library_away(client, family):
    admin, guest = client, family["guest"]
    hana = ids_of(admin)["Hana to Akuma"]
    admin.put(f"/api/series/{hana}/shares", json={"user_ids": [family["guest_id"]]})
    x = guest.post("/api/shelves", json={"name": "Manga"}).json()["id"]
    guest.patch(f"/api/series/{hana}", json={"shelf_id": x})
    assert hana in seen(guest, shelf=x)
    try:
        # the admin gives the guest's "Manga" to the kid, who has a "Manga" already
        family["kid"].post("/api/shelves", json={"name": "manga"})
        admin.patch(f"/api/shelves/{x}", json={"owner_id": family["kid_id"]})
        # the guest's series is back in Shared with me, not lost in a library it can't see
        assert hana in seen(guest, shelf=-1)
        names = sorted(s["name"] for s in family["kid"].get("/api/shelves").json())
        assert names == ["Manga (2)", "manga"]
        # the admin can't give a name its new owner already uses
        assert admin.patch(f"/api/shelves/{x}", json={"owner_id": family["kid_id"], "name": "MANGA"}).status_code == 400
    finally:
        admin.put(f"/api/series/{hana}/shares", json={"user_ids": []})


def test_the_admin_edits_accounts(client, family):
    me = client.get("/api/auth").json()["user"]["id"]
    guest = family["guest_id"]
    # a password and admin at once
    assert client.patch(f"/api/users/admin/{guest}", json={"is_admin": True, "password": "1111", "kind": "pin"}
                        ).status_code == 200
    assert client.patch(f"/api/users/admin/{guest}", json={"is_admin": False}).status_code == 200
    assert client.patch(f"/api/users/admin/{guest}", json={"is_admin": True, "password": ""}).status_code == 400
    # its own password is changed in Settings → Account (with the current one), not here
    assert client.patch(f"/api/users/admin/{me}", json={"password": "other-pw"}).status_code == 400
    assert client.get("/api/series").status_code == 200


def test_no_admin_without_a_password(client, family):
    r = client.post("/api/users/admin", json={"name": "Boss", "password": "", "is_admin": True})
    assert r.status_code == 400 and "Boss" not in {u["name"] for u in client.get("/api/users").json()}


def test_signing_in_by_name_gives_nothing_away(client, family):
    from app.main import app
    c = TestClient(app)
    wrong_pin = c.post("/api/auth/sign-in", json={"name": "Kid", "password": "0000"}).json()["detail"]
    nobody = c.post("/api/auth/sign-in", json={"name": "Nobody", "password": "0000"}).json()["detail"]
    assert wrong_pin == nobody == "Wrong name or password"
    # with the profiles hidden, accounts can't be tried by their number either
    client.put("/api/settings", json={"profile_picker": False})
    try:
        assert c.post("/api/auth/sign-in", json={"user_id": family["guest_id"]}).status_code == 403
        assert c.post("/api/auth/sign-in", json={"name": "guest"}).status_code == 200
    finally:
        client.put("/api/settings", json={"profile_picker": True})
    from app import shelves
    shelves.throttle.forget(("account", family["kid_id"]))


def test_a_new_pin_keeps_how_long_you_stay_signed_in(client, family):
    from app.main import app
    c = TestClient(app)
    c.post("/api/auth/sign-in", json={"user_id": family["kid_id"], "password": "1234", "remember": False})
    r = c.put("/api/account/password", json={"current": "1234", "password": "5678", "kind": "pin"})
    session = [x for x in r.headers.get_list("set-cookie") if x.startswith("mv_session=")][0]
    assert "Max-Age" not in session and "expires" not in session.lower()  # still until the browser closes


def test_clearing_a_library_password_keeps_the_accounts_slow_down(client, family):
    from app import shelves
    from app.db import SessionLocal
    from app.main import app
    c = TestClient(app)
    for _ in range(shelves.MAX_TRIES):
        c.post("/api/auth/sign-in", json={"user_id": family["kid_id"], "password": "0000"})
    with SessionLocal() as db:
        shelves.clear_passwords(db, "all")
    assert c.post("/api/auth/sign-in", json={"user_id": family["kid_id"], "password": "1234"}).status_code == 429
    shelves.throttle.forget(("account", family["kid_id"]))


def test_nothing_is_remembered_about_what_isnt_there(client):
    from app import access
    from app.db import SessionLocal
    with SessionLocal() as db:
        assert access.item(db, "books", 987654) is None
    assert ("books", 987654) not in access._cache["items"]  # checked again once a scan adds it


def test_upgrade_from_before_libraries(tmp_path):
    """Migration 15 when create_all has just made the shelf table, owner_id included."""
    from app import db as appdb
    from app.models import Shelf, User, UserSeries, UserSetting
    engine = create_engine(f"sqlite:///{tmp_path / 'older.db'}")
    with engine.begin() as conn:
        for q in ("CREATE TABLE series (id INTEGER PRIMARY KEY, reading_status VARCHAR, reader_mode VARCHAR, "
                  "favorite BOOLEAN NOT NULL, last_read_at DATETIME)",
                  "CREATE TABLE progress (id INTEGER PRIMARY KEY, book_id INTEGER UNIQUE, page INTEGER, "
                  "completed BOOLEAN, updated_at DATETIME)",
                  "CREATE TABLE setting (key VARCHAR PRIMARY KEY, value JSON)"):
            conn.execute(text(q))
        for table in (User.__table__, UserSeries.__table__, UserSetting.__table__, Shelf.__table__):
            table.create(conn)
        conn.execute(text("INSERT INTO shelf (id, name, lock_version, created_at) VALUES (1, 'manga', 0, '2026-01-01')"))
        appdb.MIGRATIONS[14](conn)
        assert conn.execute(text("SELECT owner_id FROM shelf")).fetchall() == [(1,)]
    engine.dispose()


def test_upgrade_gives_everything_to_the_admin(tmp_path):
    """Migration 15 on a database from before accounts."""
    from app import db as appdb
    from app.models import User, UserSeries, UserSetting
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        for q in ("CREATE TABLE series (id INTEGER PRIMARY KEY, reading_status VARCHAR, reader_mode VARCHAR, "
                  "favorite BOOLEAN NOT NULL, last_read_at DATETIME)",
                  "CREATE TABLE progress (id INTEGER PRIMARY KEY, book_id INTEGER UNIQUE, page INTEGER, "
                  "completed BOOLEAN, updated_at DATETIME)",
                  "CREATE INDEX ix_progress_updated_at ON progress (updated_at)",
                  "CREATE INDEX ix_progress_book_state ON progress (book_id, completed, page)",
                  "CREATE TABLE setting (key VARCHAR PRIMARY KEY, value JSON)",
                  "CREATE TABLE shelf (id INTEGER PRIMARY KEY, name VARCHAR)",
                  "INSERT INTO series VALUES (1, 'on_hold', 'vertical', 1, '2026-01-02 03:04:05'), "
                  "(2, NULL, NULL, 0, NULL)",
                  "INSERT INTO progress VALUES (7, 10, 4, 0, '2026-01-02 03:04:05'), (8, 11, 9, 1, '2026-01-01 00:00:00')",
                  "INSERT INTO setting VALUES ('theme', '\"light\"'), ('saved_searches', '[{\"id\": 1}]'), "
                  "('auto_match', 'false')",
                  "INSERT INTO shelf VALUES (1, 'Manga')"):
            conn.execute(text(q))
        for table in (User.__table__, UserSeries.__table__, UserSetting.__table__):
            table.create(conn)
        appdb.MIGRATIONS[14](conn)
        q = lambda sql: conn.execute(text(sql)).fetchall()  # noqa: E731
        assert q("SELECT id, name, is_admin, password_hash FROM account") == [(1, "Admin", 1, None)]
        assert q("SELECT id, user_id, book_id, page, completed FROM progress ORDER BY id") == [(7, 1, 10, 4, 0), (8, 1, 11, 9, 1)]
        assert q("SELECT user_id, series_id, reading_status, reader_mode, favorite FROM user_series") == \
            [(1, 1, "on_hold", "vertical", 1)]
        assert sorted(q("SELECT key FROM user_setting WHERE user_id = 1")) == [("saved_searches",), ("theme",)]
        assert q("SELECT key FROM setting") == [("auto_match",)]  # the server's stay
        assert q("SELECT owner_id FROM shelf") == [(1,)]
        # a second account can read the same book now
        conn.execute(text("INSERT INTO account (id, name, is_admin, session_version, created_at) "
                          "VALUES (2, 'B', 0, 0, '2026-01-03 00:00:00')"))
        conn.execute(text("INSERT INTO progress (user_id, book_id, page, completed, updated_at) "
                          "VALUES (2, 10, 1, 0, '2026-01-03 00:00:00')"))
    engine.dispose()
