"""Matching, providers (recorded responses, no network), metadata API and filters."""
import io
import json

import httpx
import pytest
from PIL import Image

from app.metadata import http, matcher

# ---------------------------------------------------------------- recorded provider responses

HANA_AL = {
    "id": 30002, "idMal": 2002, "siteUrl": "https://anilist.co/manga/30002",
    "title": {"romaji": "Hana to Akuma", "english": "The Flower and the Demon", "native": "花と悪魔"},
    "synonyms": ["Devil with Flower"], "description": "Vivi, a demon...<br><br><i>(Source)</i>",
    "status": "FINISHED", "format": "MANGA", "countryOfOrigin": "JP", "chapters": 54, "volumes": 10,
    "averageScore": 77, "startDate": {"year": 2007}, "genres": ["Romance", "Comedy"],
    "tags": [{"name": "Shoujo", "rank": 90, "isMediaSpoiler": False},
             {"name": "Demons", "rank": 80, "isMediaSpoiler": False},
             {"name": "Twist Ending", "rank": 60, "isMediaSpoiler": True}],
    "coverImage": {"extraLarge": "https://img.test/hana.jpg"}, "bannerImage": None,
    "staff": {"edges": [{"role": "Story & Art", "node": {"name": {"full": "Hisamu Oto"}}}]},
    "externalLinks": [{"site": "Official Site", "url": "https://example.jp/hana", "type": "INFO"}],
}
OTHER_AL = {**HANA_AL, "id": 99, "title": {"romaji": "Hanako-kun", "english": None, "native": None},
            "synonyms": [], "siteUrl": "https://anilist.co/manga/99", "idMal": None}
MU_SEARCH = {"results": [{"hit_title": "Hana to Akuma", "record": {
    "series_id": 39643425366, "title": "Hana to Akuma",
    "url": "https://www.mangaupdates.com/series/i7mo6s6/hana-to-akuma",
    "image": {"url": {"original": "https://img.test/mu.jpg"}}, "type": "Manga", "year": "2007"}}]}
MU_SERIES = {
    "series_id": 39643425366, "title": "Hana to Akuma",
    "url": "https://www.mangaupdates.com/series/i7mo6s6/hana-to-akuma",
    "associated": [{"title": "Devil with Flower"}, {"title": "花と悪魔"}, {"title": "花与恶魔"}],
    "description": "15-years-ago Vivi, the demon...<BR>", "image": {"url": {"original": "https://img.test/mu.jpg"}},
    "type": "Manga", "year": "2007", "bayesian_rating": 8.56, "status": "10 Volumes (Complete)",
    "completed": True, "genres": [{"genre": "Fantasy"}, {"genre": "Romance"}],
    "categories": [{"category": "Adopted Protagonist", "votes": 16}],
    "authors": [{"name": "OTO Hisamu", "type": "Author"}, {"name": "OTO Hisamu", "type": "Artist"}],
}

state = {"anilist_up": True, "mu_up": True, "al_results": [HANA_AL], "calls": []}


def _png():
    b = io.BytesIO()
    Image.new("RGB", (460, 650), (200, 30, 30)).save(b, "JPEG")
    return b.getvalue()


def handler(request: httpx.Request):
    state["calls"].append(request.url.host)
    if request.url.host == "graphql.anilist.co":
        if not state["anilist_up"]:
            return httpx.Response(403, json={"errors": [{"message": "API disabled", "status": 403}]})
        body = json.loads(request.content)
        if "$id" in body["query"]:
            m = next(x for x in [HANA_AL, OTHER_AL] if x["id"] == body["variables"]["id"])
            return httpx.Response(200, json={"data": {"Media": m}})
        return httpx.Response(200, json={"data": {"Page": {"media": state["al_results"]}}})
    if request.url.host == "api.mangaupdates.com":
        if not state["mu_up"]:
            return httpx.Response(503)
        if request.url.path.endswith("/series/search"):
            return httpx.Response(200, json=MU_SEARCH)
        return httpx.Response(200, json=MU_SERIES)
    if request.url.host == "img.test":
        return httpx.Response(200, content=_png())
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def mock_http():
    from app.metadata import service
    service._down_until.clear()
    http.transport = httpx.MockTransport(handler)
    state.update(anilist_up=True, mu_up=True, al_results=[HANA_AL], calls=[])
    yield
    http.transport = None


# ---------------------------------------------------------------- matcher units

@pytest.mark.parametrize("folder,query,year", [
    ("[Group] Berserk (1989) (Digital)", "Berserk", 1989),
    ("Hana_to_Akuma", "Hana to Akuma", None),
    ("One Piece {tags} (Official)", "One Piece", None),
    ("20th Century Boys", "20th Century Boys", None),
])
def test_clean_query(folder, query, year):
    assert matcher.clean_query(folder) == (query, year)


def test_scoring_and_pick():
    assert matcher.score("Hana to Akuma", ["Hana to Akuma!"]) == 100
    assert matcher.score("hana to akuma", ["Devil with Flower", "花と悪魔", "Hana to Akuma"]) == 100
    assert matcher.score("Berserk", ["Berserk"], cand_format="NOVEL") == 85
    assert matcher.score("Berserk", ["Berserk"], 1989, 2016) == 90
    assert matcher.pick([(100, "a"), (95, "b")]) is None       # not 10 points ahead
    assert matcher.pick([(100, "a"), (60, "b")]) == "a"
    assert matcher.pick([(85, "a")]) is None                     # below threshold


def test_mangaupdates_url_ids():
    from app.plugins.builtin.mangaupdates import MangaUpdates
    assert MangaUpdates().id_from_url("https://www.mangaupdates.com/series/i7mo6s6/hana") == "39643425366"


# ---------------------------------------------------------------- matching against the test library

def hana(client):
    items = client.get("/api/series?q=hana").json()["items"]
    return next(s for s in items if "Hana" in s["title"] or "Flower" in s["title"])["id"]


def run_auto_match(sid):
    from app.db import SessionLocal
    from app.metadata import service
    from app.models import Series
    with SessionLocal() as db:
        s = db.get(Series, sid)
        s.match_status = "unmatched"
        ok = service.auto_match(db, s)
        db.commit()
        return ok


def test_auto_match_anilist(client):
    sid = hana(client)
    client.post(f"/api/series/{sid}/unmatch")
    assert run_auto_match(sid) is True
    s = client.get(f"/api/series/{sid}").json()
    assert s["title"] == "The Flower and the Demon"            # English title preferred
    assert s["titles"]["native"] == "花と悪魔"
    assert s["description"] == "Vivi, a demon...\n\n(Source)"   # html cleaned
    assert s["match_status"] == "auto" and s["match_confidence"] == 100
    sites = {l["site"] for l in s["links"]}
    assert {"AniList", "MyAnimeList", "MangaUpdates", "Official Site"} <= sites
    assert {"Romance", "Shoujo"} <= {t["name"] for t in s["tags"]}
    assert [t for t in s["tags"] if t["name"] == "Twist Ending"][0]["spoiler"] is True
    assert client.get(s["poster"]).headers["content-type"] == "image/webp"
    assert client.get(s["poster_large"]).headers["content-type"] == "image/jpeg"


def test_title_language_setting(client):
    sid = hana(client)
    client.post(f"/api/series/{sid}/match", json={"provider": "anilist", "provider_id": "30002"})
    client.put("/api/settings", json={"title_language": "native"})
    assert client.get(f"/api/series/{sid}").json()["title"] == "花と悪魔"
    client.put("/api/settings", json={"title_language": "english"})


def test_fallback_to_mangaupdates_when_anilist_down_then_upgrade(client):
    from app.db import SessionLocal
    from app.metadata import service
    from app.models import Series
    sid = hana(client)
    client.post(f"/api/series/{sid}/unmatch")
    state["anilist_up"] = False
    assert run_auto_match(sid) is True
    s = client.get(f"/api/series/{sid}").json()
    assert s["metadata_source"]["provider"] == "mangaupdates" and s["metadata_source"]["retry_primary"]
    assert s["publication_status"] == "FINISHED" and s["titles"]["native"] == "花と悪魔"
    assert s["average_score"] == 86

    state["anilist_up"] = True
    service._down_until.clear()
    with SessionLocal() as db:
        assert service.upgrade_to_primary(db, db.get(Series, sid)) is True
        db.commit()
    s = client.get(f"/api/series/{sid}").json()
    assert s["metadata_source"] == {"provider": "anilist", "id": "30002"}
    assert "MangaUpdates" in {l["site"] for l in s["links"]}


def test_all_providers_down_keeps_unmatched(client):
    from app.metadata.service import AllProvidersDown
    sid = hana(client)
    client.post(f"/api/series/{sid}/unmatch")
    state.update(anilist_up=False, mu_up=False)
    with pytest.raises(AllProvidersDown):
        run_auto_match(sid)
    assert client.get(f"/api/series/{sid}").json()["match_status"] == "unmatched"


def test_ambiguous_goes_to_needs_attention_then_fix(client):
    sid = hana(client)
    client.post(f"/api/series/{sid}/unmatch")
    state["al_results"] = [OTHER_AL]   # only a poor match on AniList
    state["mu_up"] = False
    assert run_auto_match(sid) is False
    att = client.get("/api/attention").json()
    item = next(m for m in att["matches"] if m["series_id"] == sid)
    assert item["candidates"][0]["title"] == "Hanako-kun"

    r = client.post(f"/api/series/{sid}/match", json={"url": "https://anilist.co/manga/30002/Hana-to-Akuma"})
    assert r.status_code == 200 and r.json()["match_status"] == "manual"
    assert sid not in [m["series_id"] for m in client.get("/api/attention").json()["matches"]]


def test_search_unmatch_ignore(client):
    sid = hana(client)
    res = client.post(f"/api/series/{sid}/match/search", json={"query": "hana to akuma"}).json()
    assert res[0]["provider_id"] == "30002" and res[0]["score"] == 100
    mu = client.post(f"/api/series/{sid}/match/search",
                     json={"query": "hana to akuma", "provider": "mangaupdates"}).json()
    assert mu[0]["provider_id"] == "39643425366"
    state["anilist_up"] = False
    assert client.post(f"/api/series/{sid}/match/search", json={"query": "x"}).status_code == 503
    client.post(f"/api/series/{sid}/ignore-match")
    s = client.get(f"/api/series/{sid}").json()
    assert s["match_status"] == "ignored" and s["title"] == "Hana to Akuma" and s["links"] == []
    assert s["tags"] == []


# ---------------------------------------------------------------- filters

def test_filters(client):
    sid = hana(client)
    client.post(f"/api/series/{sid}/match", json={"provider": "anilist", "provider_id": "30002"})
    ids = lambda q: [s["id"] for s in client.get(f"/api/series?{q}").json()["items"]]
    assert ids("genre=Romance") == [sid]
    total = client.get("/api/series").json()["total"]
    assert sid not in ids("exclude=Romance") and len(ids("exclude=Romance")) == total - 1
    assert ids("pub=FINISHED") == [sid]
    assert ids("match=matched") == [sid]
    assert ids("format=manhwa") == []
    assert ids("q=Devil") == [sid]            # synonym search
    assert ids("q=Oto") == [sid]              # author search
    assert sid in ids("read=unread")
    book = client.get(f"/api/series/{sid}/books").json()[0]
    client.put(f"/api/books/{book['id']}/progress", json={"page": 2})
    assert sid in ids("read=reading") and sid not in ids("read=unread")
    assert ids("sort=score")[0] == sid
    tags = {t["name"]: t["count"] for t in client.get("/api/tags").json()}
    assert tags["Romance"] == 1
