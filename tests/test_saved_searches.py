"""Saved searches (app/api/saved.py): a name for the library page's filters and sort, e.g. Isekai."""


def test_saved_searches(client):
    assert client.get("/api/saved-searches").json() == []
    # only filters and sort are kept, not the page
    r = client.post("/api/saved-searches", json={"name": " Isekai ", "query": "?genre=Isekai&sort=year&page=3&x=1"})
    first = r.json()
    assert first["name"] == "Isekai" and first["query"] == "genre=Isekai&sort=year"
    client.post("/api/saved-searches", json={"name": "Reading", "query": "read=reading"})
    # the same name again replaces its filters
    again = client.post("/api/saved-searches", json={"name": "isekai", "query": "genre=Isekai&genre=Magic"}).json()
    assert again["id"] == first["id"] and again["query"] == "genre=Isekai&genre=Magic"
    assert [x["name"] for x in client.get("/api/saved-searches").json()] == ["Isekai", "Reading"]
    assert client.post("/api/saved-searches", json={"name": "Empty", "query": "page=2"}).status_code == 400
    assert client.post("/api/saved-searches", json={"name": "", "query": "fav=1"}).status_code == 422
    for x in client.get("/api/saved-searches").json():
        assert client.delete(f"/api/saved-searches/{x['id']}").json() == {"ok": True}
    assert client.delete("/api/saved-searches/999").status_code == 404
    assert client.get("/api/saved-searches").json() == []
