"""Changes from another website are refused; plugin scripts are revalidated cheaply; Continue reading
works out where to go on for many series at once."""


def series_ids():
    """{folder name: series id} (other tests give the series titles)."""
    from app.db import SessionLocal
    from app.models import Series
    with SessionLocal() as db:
        return dict(db.query(Series.folder_name, Series.id))


def status(client, sid):
    return client.get(f"/api/series/{sid}").json()["reading_status"]


def test_changes_from_another_website_are_refused(client):
    sid = series_ids()["Series B"]
    client.post(f"/api/series/{sid}/mark-unread")
    url = f"/api/series/{sid}/mark-read"
    # what a page on another site sends (e.g. fetch(..., {mode: "no-cors", method: "POST"}))
    r = client.post(url, headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"})
    assert r.status_code == 403 and "another website" in r.json()["detail"]["message"]
    assert status(client, sid) == "unread"  # nothing was changed
    # an older browser without Sec-Fetch-Site: its Origin tells
    assert client.post(url, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post(url, headers={"Origin": "null"}).status_code == 403
    # reading is fine from anywhere (images, links)
    assert client.get(f"/api/series/{sid}", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    # the app's own page, the address typed in, a reverse proxy, and scripts without browser headers
    for headers in ({"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"},
                    {"Origin": "http://testserver"},
                    {"Origin": "https://manga.example", "X-Forwarded-Host": "manga.example"},
                    {}):
        assert client.post(url, headers=headers).status_code == 200, headers
    client.post(f"/api/series/{sid}/mark-unread")


def test_plugin_scripts_are_not_downloaded_again(client):
    first = client.get("/plugins/library-page.js")
    etag = first.headers["etag"]
    assert first.status_code == 200 and first.headers["cache-control"] == "no-cache"
    again = client.get("/plugins/library-page.js", headers={"If-None-Match": etag})
    assert again.status_code == 304 and not again.content
    # a proxy that compresses makes it weak; still the same file
    assert client.get("/plugins/library-page.js", headers={"If-None-Match": "W/" + etag}).status_code == 304
    css = client.get("/custom.css")
    assert client.get("/custom.css", headers={"If-None-Match": css.headers["etag"]}).status_code == 304


def test_continue_reading_goes_on_after_the_last_book_read(client):
    from app.api.common import continue_target, continue_targets
    from app.db import SessionLocal

    ids = series_ids()
    hana, b = ids["Hana to Akuma"], ids["Series B"]
    client.post(f"/api/series/{hana}/mark-unread")
    client.post(f"/api/series/{b}/mark-unread")
    books = client.get(f"/api/series/{hana}/books").json()
    assert len(books) == 4
    try:
        # the third book read first, then the first one started, then the second one finished: the
        # next one not read after the second is the fourth
        client.put(f"/api/books/{books[2]['id']}/progress", json={"page": 0, "completed": True})
        client.put(f"/api/books/{books[0]['id']}/progress", json={"page": 2})
        client.put(f"/api/books/{books[1]['id']}/progress", json={"page": 0, "completed": True})
        with SessionLocal() as db:
            found = continue_targets(db, [hana, b])
            assert found[hana][0].id == books[3]["id"] and found[hana][1] == 0
            assert found[b][0].id == client.get(f"/api/series/{b}/books").json()[0]["id"]  # nothing read: the first
            assert continue_target(db, hana)[0].id == books[3]["id"]
        cont = {c["id"]: c["continue"] for c in client.get("/api/continue-reading").json()}
        assert cont[hana]["book_id"] == books[3]["id"]

        # the last one started again: back to it, on its page
        client.put(f"/api/books/{books[0]['id']}/progress", json={"page": 5})
        with SessionLocal() as db:
            book, page = continue_target(db, hana)
            assert (book.id, page) == (books[0]["id"], 5)

        client.post(f"/api/series/{hana}/mark-read")
        with SessionLocal() as db:
            assert continue_target(db, hana) == (None, 0)  # all read
    finally:
        client.post(f"/api/series/{hana}/mark-unread")
