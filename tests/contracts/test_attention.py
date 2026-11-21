"""The attention:list job (Plugin API 21, issue #219): Needs attention lists are plugins'. The built-in ones
keep their keys; a plugin adds a list of its own (shown, counted, ignorable); with the built-in plugin off
only the others' show.
"""
import json
import re
import shutil

import pytest

from tests.conftest import TMP

PLUGINS_DIR = TMP / "data" / "plugins"
BUILTIN = ["unreadable", "matches", "same", "duplicates", "gaps", "unsupported"]

CODE = '''
from app.models import Series
from app.plugins import Plugin

class NoCover(Plugin):
    id = "no-cover"
    name = "No cover"
    category = "library"
    provides = {"attention:list": 1}
    attention_lists = [{"key": "nocover", "label": "No cover", "help": "Series without a poster of their own.",
                        "alert": True},
                       {"key": "gaps", "label": "Taken", "help": "A key the built-in lists have."}]

    def attention_rows(self, ctx, key, request):
        rows = ctx.db.query(Series).filter(Series.missing_since.is_(None)).order_by(Series.id).limit(2).all()
        return [{"key": f"nocover:{s.id}", "series_id": s.id, "series": s.folder_name, "text": "No poster"}
                for s in rows]
'''


def page_lists(client):
    return json.loads(re.search(r"window\.MV_ATTENTION_LISTS = (.*?);", client.get("/").text).group(1))


def test_the_built_in_lists(client):
    a = client.get("/api/attention").json()
    assert [x["key"] for x in a["lists"]] == BUILTIN
    assert {x["key"] for x in a["lists"] if x["alert"]} == {"unreadable", "matches", "same", "duplicates"}
    assert all(x["plugin"] == "attention-lists" for x in a["lists"])
    assert a["count"] == client.get("/api/attention/count").json()["count"]
    assert [x["key"] for x in page_lists(client)] == BUILTIN
    page = client.get("/").text
    for key in BUILTIN:
        assert f"kind === '{key}'" in page, key


@pytest.fixture
def no_cover(client):
    path = PLUGINS_DIR / "no_cover.py"
    path.write_text(CODE, encoding="utf-8")
    client.post("/api/plugins/reload")
    yield
    path.unlink()
    client.post("/api/plugins/reload")
    client.put("/api/plugins/attention-lists", json={"enabled": True})


def test_a_plugins_list(client, no_cover):
    a = client.get("/api/attention").json()
    keys = [x["key"] for x in a["lists"]]
    assert keys == BUILTIN + ["nocover"]  # its "gaps" is left out: the built-in one has it
    assert len(a["nocover"]) == 2 and a["nocover"][0]["text"] == "No poster"
    built_in = sum(len(a[k]) for k in ("unreadable", "matches", "same", "duplicates"))
    before = client.get("/api/attention/count").json()["count"]
    assert a["count"] == before == built_in + 2  # an alert list: its rows count
    assert "nocover" in [x["key"] for x in page_lists(client)]
    assert "kind === 'nocover'" in client.get("/").text  # drawn with the plain template
    # ignoring one of its rows, then bringing it back
    row = a["nocover"][0]["key"]
    assert client.post("/api/attention/ignore", json={"key": row, "list": "nocover"}).json() == {"ok": True}
    assert client.get("/api/attention/count").json()["count"] == before - 1
    assert client.get("/api/attention?ignored=true").json()["ignored_counts"]["nocover"] == 1
    assert client.post("/api/attention/ignore", json={"key": row, "ignore": False}).json() == {"ok": True}
    # a key no list has can't be ignored
    assert client.post("/api/attention/ignore", json={"key": "nocover:99999"}).status_code == 403


def test_with_the_built_in_lists_off(client, no_cover):
    assert client.put("/api/plugins/attention-lists", json={"enabled": False}).status_code == 200  # shared: not needed
    a = client.get("/api/attention").json()
    # its "gaps" list is free now: nobody else has the key
    assert [x["key"] for x in a["lists"]] == ["nocover", "gaps"]
    assert [x["key"] for x in page_lists(client)] == ["nocover", "gaps"]
    client.put("/api/plugins/no-cover", json={"enabled": False})
    assert client.get("/api/attention").json()["lists"] == [] and page_lists(client) == []
    client.put("/api/plugins/no-cover", json={"enabled": True})
