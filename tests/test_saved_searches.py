"""Saved searches, a built-in plugin (app/plugins/builtin/saved_searches/): a name for the library
page's filters and sort, e.g. Isekai. Also covers what plugins add to the web app (app/plugins/web.py)."""
API = "/api/plugins/saved-searches/api/searches"


def test_saved_searches(client):
    assert client.get(API).json() == []
    # only filters and sort are kept, not the page
    r = client.post(API, json={"name": " Isekai ", "query": "?genre=Isekai&sort=year&page=3&x=1"})
    first = r.json()
    assert first["name"] == "Isekai" and first["query"] == "genre=Isekai&sort=year"
    client.post(API, json={"name": "Reading", "query": "read=reading"})
    # the same name again replaces its filters
    again = client.post(API, json={"name": "isekai", "query": "genre=Isekai&genre=Magic"}).json()
    assert again["id"] == first["id"] and again["query"] == "genre=Isekai&genre=Magic"
    assert [x["name"] for x in client.get(API).json()] == ["Isekai", "Reading"]
    assert client.post(API, json={"name": "Empty", "query": "page=2"}).status_code == 400
    assert client.post(API, json={"name": "", "query": "fav=1"}).status_code == 422
    for x in client.get(API).json():
        assert client.delete(f"{API}/{x['id']}").json() == {"ok": True}
    assert client.delete(f"{API}/999").status_code == 404
    assert client.get(API).json() == []


def test_switched_off(client):
    """Off in Settings -> Plugins: no Saved button, no script, its API answers 404; the searches stay."""
    client.post(API, json={"name": "Faves", "query": "fav=1"})
    plugin = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "saved-searches")
    assert plugin["enabled"] and plugin["ui"]
    page = client.get("/").text
    assert "<!-- slot:" not in page  # every slot is filled in (or emptied)
    assert "savedSearchesSheet" in page and '<script src="/plugins/saved-searches.js">' in page
    assert '<div class="plugin-ui" data-plugin="saved-searches">' in page
    assert "Alpine.store(\"savedSearches\"" in client.get("/plugins/saved-searches.js").text
    assert ".saved-item" in client.get("/custom.css").text
    # what the page adds when the plugin is switched on without reloading (app.js)
    ui = client.get("/api/plugins/saved-searches/ui").json()
    assert set(ui["html"]) == {"library-tools", "library-filters", "sheets"}
    assert ui["js"] == "/plugins/saved-searches.js" and ui["css"] == "/plugins/saved-searches.css"
    assert ".saved-item" in client.get(ui["css"]).text
    try:
        client.put("/api/plugins/saved-searches", json={"enabled": False})
        page = client.get("/").text
        assert "savedSearchesSheet" not in page and "saved-searches.js" not in page
        assert '<div class="plugin-slot" data-slot="library-tools"></div>' in page  # empty, kept for later
        assert client.get("/plugins/saved-searches.js").status_code == 404
        assert client.get("/plugins/saved-searches.css").status_code == 404
        assert client.get("/api/plugins/saved-searches/ui").status_code == 404
        assert ".saved-item" not in client.get("/custom.css").text
        assert client.get(API).status_code == 404
    finally:
        client.put("/api/plugins/saved-searches", json={"enabled": True})
    assert [x["name"] for x in client.get(API).json()] == ["Faves"]
    for x in client.get(API).json():
        client.delete(f"{API}/{x['id']}")


def test_unknown_plugin_api(client):
    assert client.get("/api/plugins/nope/api/x").status_code == 404
    assert client.get("/api/plugins/anilist/api/x").status_code == 404  # a plugin without a router
