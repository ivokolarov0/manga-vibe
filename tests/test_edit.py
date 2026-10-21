"""Editing series metadata (with locks), poster changes (security!) and link icons."""
import io

import httpx
import pytest
from PIL import Image

from app import config
from app.metadata import favicon, http
from tests.conftest import png
from tests.test_metadata import HANA_AL, handler, state


def hana_id(client):
    return next(s["id"] for s in client.get("/api/series?q=hana").json()["items"])


@pytest.fixture
def mock_http():
    from app.metadata import service
    service._down_until.clear()
    http.transport = httpx.MockTransport(handler)
    state.update(anilist_up=True, mu_up=True, al_results=[HANA_AL], calls=[])
    yield
    http.transport = None


def jpeg_with_exif(size=(400, 600)):
    im = Image.new("RGB", size, (10, 120, 200))
    exif = Image.Exif()
    exif[0x010E] = "secret description"  # ImageDescription
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


# ---------------------------------------------------------------- metadata editing

def test_edit_fields_are_locked_against_refresh(client, mock_http):
    sid = hana_id(client)
    client.post(f"/api/series/{sid}/match", json={"provider": "anilist", "provider_id": "30002"})
    r = client.put(f"/api/series/{sid}/metadata", json={
        "title_english": "  My Own Title ", "description": "Mine.", "start_year": 2008,
        "genres": ["Romance", "Fantasy"], "tags": ["Butler"],
        "authors": [{"name": "Hisamu Oto", "role": "Story & Art"}],
        "links": [{"site": "Official", "url": "https://example.com/hana"}]})
    assert r.status_code == 200
    s = client.get(f"/api/series/{sid}").json()
    assert s["title"] == "My Own Title" and s["description"] == "Mine." and s["start_year"] == 2008
    assert {t["name"] for t in s["tags"]} == {"Romance", "Fantasy", "Butler"}
    assert [l["site"] for l in s["links"]] == ["Official"]
    assert {"title_english", "description", "tags", "links", "authors"} <= set(s["locked_fields"])

    client.post(f"/api/series/{sid}/refresh")                      # provider data comes back...
    s = client.get(f"/api/series/{sid}").json()
    assert s["title"] == "My Own Title" and s["description"] == "Mine."  # ...but locked fields stay
    assert s["titles"]["native"] == "花と悪魔"                        # unlocked fields still refresh

    client.put(f"/api/series/{sid}/metadata", json={"unlock": ["title_english", "description"]})
    client.post(f"/api/series/{sid}/refresh")
    s = client.get(f"/api/series/{sid}").json()
    assert s["title"] == "The Flower and the Demon" and "title_english" not in s["locked_fields"]
    client.put(f"/api/series/{sid}/metadata", json={"unlock": s["locked_fields"]})


@pytest.mark.parametrize("body", [
    {"start_year": 1500}, {"publication_status": "SOMETIMES"}, {"average_score": 140},
    {"links": [{"site": "x", "url": "javascript:alert(1)"}]}, {"title_english": "x" * 301},
])
def test_edit_rejects_bad_values(client, body):
    assert client.put(f"/api/series/{hana_id(client)}/metadata", json=body).status_code == 422


# ---------------------------------------------------------------- poster: security

def upload(client, sid, data, name="cover.jpg", ctype="image/jpeg"):
    return client.post(f"/api/series/{sid}/poster/upload", files={"file": (name, data, ctype)})


def test_upload_poster_is_reencoded_without_metadata(client):
    sid = hana_id(client)
    r = upload(client, sid, jpeg_with_exif(), name="../../evil.php")
    assert r.status_code == 200
    saved = config.COVERS_DIR / f"{sid}.jpg"
    assert saved.exists() and not (config.DATA_DIR / "evil.php").exists()
    with Image.open(saved) as im:
        assert im.format == "JPEG" and im.width == 400
        assert 0x010E not in im.getexif()                 # EXIF stripped
    s = client.get(f"/api/series/{sid}").json()
    assert s["poster_source"] == "upload" and "poster" in s["locked_fields"]
    assert client.get(s["poster_large"]).headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("data,why", [
    (b"<?php system($_GET['x']); ?>", "not an image"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "not an image"),
    (b"", "empty"),
    (b"GIF89a" + b"\x00" * 50 + b"<script>alert(1)</script>", "image"),  # fake GIF hiding a script
])
def test_upload_rejects_non_images(client, data, why):
    r = upload(client, hana_id(client), data, name="x.jpg")
    assert r.status_code == 400 and why in r.json()["detail"].lower()


def test_upload_rejects_too_big_file(client):
    r = upload(client, hana_id(client), b"\xff\xd8" + b"0" * (11 * 1024 * 1024))
    assert r.status_code == 413 and "larger than" in r.json()["detail"]   # refused before parsing


def test_upload_limit_without_content_length(client):
    def chunks():  # chunked body: no Content-Length to check up front
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n\r\n'
        for _ in range(12):
            yield b"0" * (1024 * 1024)
    r = client.post(f"/api/series/{hana_id(client)}/poster/upload", content=chunks(),
                    headers={"content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413


def test_icon_not_cached_when_site_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(favicon, "ICON_DIR", tmp_path)
    monkeypatch.setattr(favicon, "_public_ip", PUBLIC)
    monkeypatch.setattr(http, "transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    assert favicon.icon_png("https://icons.example/") is None
    assert not list(tmp_path.iterdir())                      # a 503 is temporary: nothing cached
    monkeypatch.setattr(http, "transport", httpx.MockTransport(icon_handler))
    assert favicon.icon_png("https://icons.example/") is not None   # works on the next try


def test_upload_rejects_decompression_bomb(client):
    buf = io.BytesIO()
    Image.new("1", (10000, 10000)).save(buf, "PNG")   # tiny file, 100 million pixels
    assert len(buf.getvalue()) < 100_000
    r = upload(client, hana_id(client), buf.getvalue(), name="bomb.png", ctype="image/png")
    assert r.status_code == 400 and "size" in r.json()["detail"]


def test_poster_from_book_page_and_back_to_automatic(client):
    sid = hana_id(client)
    book = client.get(f"/api/series/{sid}/books").json()[0]
    assert client.post(f"/api/series/{sid}/poster", json={"source": "page", "book_id": book["id"], "page": 2}).status_code == 200
    s = client.get(f"/api/series/{sid}").json()
    assert s["poster_source"] == "page" and "poster" in s["locked_fields"]
    # a book from another series is refused
    other = next(x for x in client.get("/api/series").json()["items"] if x["id"] != sid)
    other_book = client.get(f"/api/series/{other['id']}/books").json()[0]
    assert client.post(f"/api/series/{sid}/poster", json={"source": "page", "book_id": other_book["id"]}).status_code == 404
    client.post(f"/api/series/{sid}/poster", json={"source": "first_page"})
    s = client.get(f"/api/series/{sid}").json()
    assert s["poster_source"] == "first_page" and "poster" not in s["locked_fields"]
    assert not (config.COVERS_DIR / f"{sid}.jpg").exists()


# ---------------------------------------------------------------- link icons

PUBLIC = lambda h: "93.184.216.34" if h.endswith(".example") else None  # fake DNS: only *.example is public


def icon_handler(request: httpx.Request):
    host = request.headers["host"]  # the URL holds the pinned IP; Host keeps the name
    if host == "icons.example":
        if request.url.path == "/":
            return httpx.Response(200, html='<html><head><link rel="shortcut icon" href="/static/fav.png"></head></html>')
        if request.url.path == "/static/fav.png":
            return httpx.Response(200, content=png((255, 0, 0), (64, 64)))
    if host == "redirector.example":
        return httpx.Response(302, headers={"location": "https://192.168.1.215/admin.png"})
    return httpx.Response(404)


def test_link_icon_fetched_reencoded_and_cached(client, monkeypatch, tmp_path):
    monkeypatch.setattr(favicon, "ICON_DIR", tmp_path)
    monkeypatch.setattr(favicon, "_public_ip", PUBLIC)
    monkeypatch.setattr(http, "transport", httpx.MockTransport(icon_handler))
    png_bytes = favicon.icon_png("https://icons.example/series/123")
    with Image.open(io.BytesIO(png_bytes)) as im:
        assert im.format == "PNG" and im.size == (32, 32)
    monkeypatch.setattr(http, "transport", httpx.MockTransport(lambda r: httpx.Response(500)))
    assert favicon.icon_png("https://icons.example/other") == png_bytes   # from the cache


def test_link_icon_never_reaches_private_addresses(monkeypatch, tmp_path):
    monkeypatch.setattr(favicon, "ICON_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(http, "transport", httpx.MockTransport(lambda r: calls.append(str(r.url)) or icon_handler(r)))
    monkeypatch.setattr(favicon, "_public_ip", PUBLIC)
    assert favicon.icon_png("https://localhost/x") is None
    assert favicon.icon_png("https://192.168.1.215/x") is None
    assert favicon.icon_png("https://redirector.example/") is None       # redirect into the LAN
    assert not any("192.168" in c or "localhost" in c for c in calls)
    assert all("93.184.216.34" in c for c in calls)   # every request went to the checked IP
    monkeypatch.undo()
    assert favicon._public_ip("localhost") is None and favicon._public_ip("127.0.0.1") is None


def test_svg_icon_with_script_becomes_plain_png(monkeypatch, tmp_path):
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
           b'<script>alert(document.cookie)</script><rect width="32" height="32" fill="red"/></svg>')

    def h(request):
        if request.url.path == "/":
            return httpx.Response(200, html='<link rel="icon" href="/logo.svg" type="image/svg+xml">')
        if request.url.path == "/logo.svg":
            return httpx.Response(200, content=svg, headers={"content-type": "image/svg+xml"})
        return httpx.Response(404)
    monkeypatch.setattr(favicon, "ICON_DIR", tmp_path)
    monkeypatch.setattr(favicon, "_public_ip", lambda h: "93.184.216.34")
    monkeypatch.setattr(http, "transport", httpx.MockTransport(h))
    out = favicon.icon_png("https://svg.example/")
    assert out.startswith(b"\x89PNG") and b"script" not in out
    with Image.open(io.BytesIO(out)) as im:
        assert im.convert("RGB").getpixel((16, 16))[0] > 200   # the red square was drawn
