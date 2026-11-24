"""Random manga, a built-in plugin (app/plugins/builtin/random_manga/, issue #275): a series picked at random,
with read (completed) and dropped series left out by default."""
PID = "random-manga"
API = f"/api/plugins/{PID}/api/pick"


def _series(client):
    return {s["id"]: s for s in client.get("/api/series?limit=200").json()["items"]}


def _picks(client, query="", n=40):
    """The ids picked in n tries (None when nothing was picked)."""
    return {(client.get(f"{API}?{query}").json()["series"] or {}).get("id") for _ in range(n)}


def _set(client, **settings):
    assert client.put(f"/api/plugins/{PID}/settings", json=settings).status_code == 200


def test_pick_leaves_out_read_and_dropped(client):
    every = _series(client)
    ids = sorted(every)
    assert len(ids) >= 3
    done, dropped = ids[0], ids[1]
    try:
        client.patch(f"/api/series/{done}", json={"reading_status": "completed"})
        client.patch(f"/api/series/{dropped}", json={"reading_status": "dropped"})
        r = client.get(API).json()
        assert r["series"]["id"] in every and r["series"]["title"] == every[r["series"]["id"]]["title"]
        assert r["message"] == ""
        picked = _picks(client)
        assert done not in picked and dropped not in picked
        # the settings: read ones may be picked again, then dropped ones too
        _set(client, exclude_completed="no")
        assert done in _picks(client, n=150) and dropped not in _picks(client)
        _set(client, exclude_completed="no", exclude_dropped="no")
        assert dropped in _picks(client, n=150)
    finally:
        _set(client, exclude_completed="", exclude_dropped="")
        for sid in (done, dropped):
            client.patch(f"/api/series/{sid}", json={"clear_reading_status": True})


def test_pick_another(client):
    ids = sorted(_series(client))
    # never the series shown; the ones picked lately only when nothing else is left
    assert ids[0] not in _picks(client, f"current={ids[0]}")
    assert _picks(client, f"current={ids[0]}&recent={','.join(map(str, ids[1:-1]))}") == {ids[-1]}
    assert _picks(client, f"current={ids[0]}&recent={','.join(map(str, ids[1:]))}") <= set(ids[1:])


def test_nothing_left_says_so(client):
    ids = sorted(_series(client))
    try:
        for sid in ids:
            client.patch(f"/api/series/{sid}", json={"reading_status": "completed"})
        client.patch(f"/api/series/{ids[0]}", json={"reading_status": "dropped"})
        r = client.get(API).json()
        assert r == {"series": None, "message": "Nothing to pick: every series here is read or dropped."}
        _set(client, exclude_dropped="no")
        assert client.get(API).json()["series"]["id"] == ids[0]
        r = client.get(f"{API}?current={ids[0]}").json()
        assert r == {"series": None, "message": "This is the only series left to pick."}
        _set(client, exclude_dropped="yes")
        assert client.get(API).json()["message"] == "Nothing to pick: every series here is read or dropped."
        _set(client, exclude_completed="no", exclude_dropped="yes")
        assert client.get(API).json()["series"]["id"] != ids[0]
        # in another language
        r = client.get(f"{API}?current=0", headers={"Accept-Language": "de"})
        assert r.json()["series"] is not None
        _set(client, exclude_completed="yes", exclude_dropped="no")
        client.patch(f"/api/series/{ids[0]}", json={"reading_status": "completed"})
        assert client.get(API).json()["message"] == "Nothing to pick: every series here is read."
        assert client.get(API, headers={"Accept-Language": "de"}).json()["message"] == \
            "Nichts zu ziehen: Jede Serie hier ist gelesen."
    finally:
        _set(client, exclude_completed="", exclude_dropped="")
        for sid in ids:
            client.patch(f"/api/series/{sid}", json={"clear_reading_status": True})


def test_settings_are_yes_or_no(client):
    assert client.put(f"/api/plugins/{PID}/settings", json={"exclude_completed": "maybe"}).status_code == 400
    plugin = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == PID)
    assert plugin["enabled"] and plugin["ui"]


def test_mcp_tool(client):
    from app.plugins import Context, manager
    from app.db import SessionLocal
    p = manager.plugins[PID]
    with SessionLocal() as db:
        r = p.run_mcp_tool(Context(p, db), "pick", {})
    assert r["series"]["id"] in _series(client)


def test_markup_and_switched_off(client):
    page = client.get("/").text
    assert "random-tool" in page and "random-again" in page
    assert '<script src="/plugins/random-manga.js">' in page
    assert "MV.listScope()" in client.get("/plugins/random-manga.js").text
    try:
        client.put(f"/api/plugins/{PID}", json={"enabled": False})
        page = client.get("/").text
        assert "random-tool" not in page and "random-manga.js" not in page
        assert client.get(API).status_code == 404
    finally:
        client.put(f"/api/plugins/{PID}", json={"enabled": True})
    assert client.get(API).status_code == 200
