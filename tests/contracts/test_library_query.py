"""The library-query contract (Plugin API 16, issue #204), run against every plugin doing the job: the
built-in "Library filters and sorting" and the example that extends it.

To check your own plugin: put its file in examples/plugins (or point PLUGINS at its path) and add it
below. Each test runs with that plugin doing the job, through the app's own API.
"""
import shutil
from pathlib import Path

import pytest

from tests.conftest import TMP

EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "plugins"
BUILTIN = "library-query"
# plugin id -> its file to install (None: built in)
PLUGINS = {BUILTIN: None, "description-filter": EXAMPLES / "description_filter.py"}
SORT_PAGE = 2  # small pages, so paging is tested on the small test library


@pytest.fixture(params=list(PLUGINS))
def doing(request, client):
    """The plugin `request.param`, installed and switched on: it does the library-query job."""
    pid, src = request.param, PLUGINS[request.param]
    dest = TMP / "data" / "plugins" / src.name if src else None
    if dest:
        shutil.copy(src, dest)
        client.post("/api/plugins/reload")
        r = client.put(f"/api/plugins/{pid}", json={"enabled": True})
        assert r.status_code == 200, r.text
        assert BUILTIN in r.json()["stopped"]  # it replaced the built-in one
    schema = client.get("/api/library/schema").json()
    assert schema["plugin"] == pid
    yield pid, schema
    if dest:
        assert client.put(f"/api/plugins/{BUILTIN}", json={"enabled": True}).status_code == 200
        dest.unlink()
        client.post("/api/plugins/reload")


def sample(f, client):
    """A value for each of a filter's parameters, to send."""
    kind, params = f["kind"], f["params"]
    if kind == "choice":
        return {params[0]: f["options"][0][0]}
    if kind == "tags":
        tags = client.get("/api/tags").json()
        name = tags[0]["name"] if tags else "Nothing like this"
        return {p: name if i == 0 else f"Not {name}" for i, p in enumerate(params)}  # include, exclude
    if kind == "names":
        return {params[0]: "Nobody Like This"}
    if kind == "letter":
        return {params[0]: "h"}
    if kind == "dates":
        return {params[0]: "2000-01-01", params[1]: "2099-12-31"}  # from, to (whole days)
    if kind == "text":
        return {params[0]: "a"}
    return {p: "1" for p in params}  # length, range, toggle: a number or an on


def ids(client, **params):
    r = client.get("/api/series", params={"ids_only": "true", **params})
    assert r.status_code == 200, r.text
    return r.json()["ids"]


def test_schema(doing):
    _, schema = doing
    assert schema["filters"] and schema["sorts"]
    seen = set()
    for f in schema["filters"]:
        assert f["key"] and f["label"] and f["kind"] and f["params"], f
        assert not seen & set(f["params"]), f"{f['key']}: a parameter another filter reads"
        seen |= set(f["params"])
        if f["kind"] == "choice":
            assert f["options"] and all(len(o) == 2 for o in f["options"])
    assert set(schema["params"]) == seen
    for s in schema["sorts"]:
        assert s["key"] and s["label"] and s["order"] in ("asc", "desc") and len(s["order_labels"]) == 2
    assert schema["default_sort"] in {s["key"] for s in schema["sorts"]}
    assert not {"sort", "order", "page", "cursor", "limit", "library", "ids_only"} & seen  # the core's


def test_every_filter_works_and_narrows(doing, client):
    _, schema = doing
    everything = set(ids(client))
    for f in schema["filters"]:
        found = ids(client, **sample(f, client))
        assert set(found) <= everything, f["key"]
        assert len(found) == len(set(found)), f"{f['key']}: a series listed twice"


def test_every_parameter_is_kept_by_saved_searches(doing, client):
    _, schema = doing
    query = "&".join(f"{k}={v}" for f in schema["filters"] for k, v in sample(f, client).items())
    saved = client.post("/api/plugins/saved-searches/api/searches",
                        json={"name": "contract", "query": query + "&page=3&sort=" + schema["sorts"][-1]["key"]})
    assert saved.status_code == 200, saved.text
    try:
        kept = saved.json()["query"]
        for p in schema["params"]:
            assert f"{p}=" in kept, p
        assert "page=" not in kept and "sort=" in kept
    finally:
        client.delete(f"/api/plugins/saved-searches/api/searches/{saved.json()['id']}")


@pytest.mark.parametrize("order", ["", "asc", "desc"])
def test_sorting_is_stable_across_pages(doing, client, order):
    """Every sort, in pages: the same series, in the same order as all at once, none twice."""
    _, schema = doing
    for s in schema["sorts"]:
        params = {"sort": s["key"], **({"order": order} if order else {})}
        all_at_once = ids(client, **params)
        paged, cursor = [], 0
        while cursor is not None:
            r = client.get("/api/series", params={**params, "cursor": cursor, "limit": SORT_PAGE}).json()
            paged += [x["id"] for x in r["items"]]
            cursor = r["next_cursor"]
        assert paged == all_at_once, s["key"]


def test_a_hidden_series_stays_hidden(doing, client, monkeypatch):
    """What another plugin hides (series_filter, e.g. a locked library) no filter or sort brings back."""
    from app.models import Series
    from app.plugins import manager
    _, schema = doing
    hide = ids(client)[0]
    monkeypatch.setattr(manager, "series_conditions", lambda db, request: [Series.id != hide])
    assert hide not in ids(client)
    for f in schema["filters"]:
        assert hide not in ids(client, **sample(f, client)), f["key"]
    for s in schema["sorts"]:
        assert hide not in ids(client, sort=s["key"]), s["key"]


def test_bad_numbers_are_refused_not_a_crash(doing, client):
    _, schema = doing
    for f in schema["filters"]:
        if f["kind"] in ("length", "range", "dates"):
            r = client.get("/api/series", params={f["params"][0]: "lots"})
            assert r.status_code < 500, f["key"]
