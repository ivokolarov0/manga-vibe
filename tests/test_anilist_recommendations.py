"""AniList recommendations (issue #244): the built-in plugin that shows "If you liked this" at the end of the
series page. Recorded AniList answers, no network."""
import io
import json

import httpx
import pytest
from PIL import Image

from app.metadata import http
from app.models import PluginSeriesData, Series

PID = "anilist-recommendations"
API = f"/api/plugins/{PID}/api"
HANA_AL, B_AL = 70001, 50001


def media(mid, title, rating, adult=False, cover=True):
    return {"rating": rating, "mediaRecommendation": {
        "id": mid, "type": "MANGA", "isAdult": adult, "siteUrl": f"https://anilist.co/manga/{mid}",
        "format": "MANGA", "countryOfOrigin": "KR", "startDate": {"year": 2015},
        "title": {"romaji": f"{title} romaji", "english": title, "native": None},
        "coverImage": {"large": f"https://s4.anilist.co/file/{mid}.jpg" if cover else None}}}


RECS = [media(B_AL, "Series B on AniList", 40), media(50002, "Elsewhere", 30),
        media(50003, "Not liked", 0), media(50004, "Adult", 20, adult=True), media(50005, "No cover", 10, cover=False),
        {"rating": 5, "mediaRecommendation": None}]
state = {"up": True, "calls": []}


def jpeg():
    b = io.BytesIO()
    Image.new("RGB", (46, 65), (30, 30, 200)).save(b, "JPEG")
    return b.getvalue()


def handler(request: httpx.Request):
    state["calls"].append(request.url.host)
    if request.url.host == "graphql.anilist.co":
        if not state["up"]:
            return httpx.Response(500)
        body = json.loads(request.content)
        if body["variables"]["id"] != HANA_AL:
            return httpx.Response(404, json={"data": {"Media": None}, "errors": [{"message": "Not Found.", "status": 404}]})
        return httpx.Response(200, json={"data": {"Media": {"recommendations": {"nodes": RECS}}}})
    if request.url.host == "s4.anilist.co":
        return httpx.Response(200, content=jpeg())
    return httpx.Response(404)


@pytest.fixture
def series(client):
    """Hana to Akuma matched on AniList, Series B too (as one of its recommendations); nothing saved."""
    from app.db import SessionLocal
    http.transport = httpx.MockTransport(handler)
    state.update(up=True, calls=[])
    with SessionLocal() as db:
        hana = db.query(Series).filter_by(folder_name="Hana to Akuma").one()
        b = db.query(Series).filter_by(folder_name="Series B").one()
        old = {hana.id: hana.anilist_id, b.id: b.anilist_id}
        hana.anilist_id, b.anilist_id = HANA_AL, B_AL
        db.query(PluginSeriesData).filter_by(plugin=PID).delete()
        db.commit()
        ids = {"hana": hana.id, "b": b.id}
    client.post(f"/api/plugins/{PID}/actions/clear")
    yield ids
    http.transport = None
    client.put(f"/api/plugins/{PID}/settings", json={"covers": "", "count": "", "days": ""})
    with SessionLocal() as db:
        for sid, aid in old.items():
            db.get(Series, sid).anilist_id = aid
        db.query(PluginSeriesData).filter_by(plugin=PID).delete()
        db.commit()


def test_on_by_default_with_its_panel(client):
    info = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == PID)
    assert info["enabled"] is True and info["warnings"] == []
    assert any("graphql.anilist.co" in x for x in info["permissions"])
    page = client.get("/").text
    assert 'x-data="anilistRecs()"' in page


def test_recommendations(client, series):
    items = client.get(f"{API}/series/{series['hana']}").json()["items"]
    assert [i["id"] for i in items] == [B_AL, 50002, 50005]  # no zero rating, no adult, no empty entry
    here, there, bare = items
    assert here["local"] and here["href"] == f"#/series/{series['b']}"
    assert here["cover"].startswith(f"/api/series/{series['b']}/poster")  # its own poster
    assert not there["local"] and there["href"] == "https://anilist.co/manga/50002"
    assert there["title"] == "Elsewhere" and there["year"] == 2015 and there["country"] == "KR"
    assert there["cover"] == f"{API}/covers/50002?series={series['hana']}"  # saved on the server by default
    assert bare["cover"] is None
    # kept: the second view doesn't ask AniList
    calls = len(state["calls"])
    assert client.get(f"{API}/series/{series['hana']}").json()["items"] == items
    assert len(state["calls"]) == calls


def test_covers_are_saved_on_the_server(client, series):
    client.get(f"{API}/series/{series['hana']}")
    r = client.get(f"{API}/covers/50002?series={series['hana']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "max-age" in r.headers["cache-control"]
    calls = len(state["calls"])
    assert client.get(f"{API}/covers/50002?series={series['hana']}").content == r.content
    assert len(state["calls"]) == calls  # from the server's copy
    # only covers the series recommends
    assert client.get(f"{API}/covers/12345?series={series['hana']}").status_code == 404
    assert "covers" in client.get("/api/plugins").text  # the status line counts them


def test_hotlinked_covers(client, series):
    assert client.put(f"/api/plugins/{PID}/settings", json={"covers": "hotlink"}).status_code == 200
    here, there, _ = client.get(f"{API}/series/{series['hana']}").json()["items"]
    assert here["local"] and here["cover"].startswith("/api/series/")  # still its own poster
    assert there["cover"] == "https://s4.anilist.co/file/50002.jpg"
    assert client.put(f"/api/plugins/{PID}/settings", json={"count": "1"}).status_code == 400  # not an option


def test_how_many(client, series, monkeypatch):
    monkeypatch.setitem(globals(), "RECS", [media(60000 + n, f"Rec {n}", 50 - n) for n in range(8)])
    assert client.put(f"/api/plugins/{PID}/settings", json={"count": "5"}).status_code == 200
    assert len(client.get(f"{API}/series/{series['hana']}").json()["items"]) == 5


def test_no_match_or_anilist_down_shows_nothing(client, series):
    assert client.get(f"{API}/series/{series['b']}").json() == {"items": []}  # AniList: no such manga
    hana = client.get(f"/api/series/{series['hana']}").json()
    from app.db import SessionLocal
    with SessionLocal() as db:
        db.get(Series, series["hana"]).anilist_id = None
        db.commit()
    calls = len(state["calls"])
    assert client.get(f"{API}/series/{series['hana']}").json() == {"items": []}  # not matched: not asked
    assert len(state["calls"]) == calls
    with SessionLocal() as db:
        db.get(Series, series["hana"]).anilist_id = HANA_AL
        db.commit()
    state["up"] = False
    r = client.get(f"{API}/series/{series['hana']}")
    assert r.status_code == 200 and r.json() == {"items": []}
    calls = len(state["calls"])
    client.get(f"{API}/series/{series['hana']}")
    assert len(state["calls"]) == calls  # not asked again on every view while it's down
    assert hana["title"]


def test_switched_off_shows_nothing(client, series):
    assert client.put(f"/api/plugins/{PID}", json={"enabled": False}).status_code == 200
    try:
        assert 'x-data="anilistRecs()"' not in client.get("/").text
        assert client.get(f"{API}/series/{series['hana']}").status_code == 404
    finally:
        client.put(f"/api/plugins/{PID}", json={"enabled": True})
