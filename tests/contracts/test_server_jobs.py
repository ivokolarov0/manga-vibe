"""The server jobs (Plugin API 23, issue #210): scanner:parser, metadata:matcher and book:label are plugins'.

The contract tests ask the plugin doing each job, with the cases of tests/test_parser.py and
tests/test_metadata.py: a replacement that runs them sees what it does differently. Then a replacement
of each: a rescan after switching the parser reads the numbers again and keeps progress; labels follow
the plugin, and with the job off books are called by their file names; a parser that fails falls back
to the built-in one.
"""
import shutil

import pytest

from app.plugins import manager
from tests.conftest import TMP, make_zip
from tests.test_parser import CASES

PLUGINS_DIR = TMP / "data" / "plugins"
FOLDER = TMP / "manga" / "Parser Test"


def doing(client, job):
    client.get("/api/health")  # the plugins are loaded
    p = manager.provider_of(job)
    assert p is not None, f"nobody does {job}"
    return p


# --- scanner:parser ----------------------------------------------------------------------------

@pytest.mark.parametrize("name,series,vol,ch,ch_end,rev,extra,extra_idx", CASES)
def test_parser_contract(client, name, series, vol, ch, ch_end, rev, extra, extra_idx):
    p = doing(client, "scanner:parser").parse_name(name, series)
    assert (p.name, p.volume, p.chapter_end, p.version, p.extra, p.extra_index) == \
           (name, vol, ch_end, rev, extra, extra_idx)
    assert p.chapter == pytest.approx(ch) if ch is not None else p.chapter is None


# --- metadata:matcher ----------------------------------------------------------------------------

@pytest.mark.parametrize("folder,query,year", [
    ("[Group] Berserk (1989) (Digital)", "Berserk", 1989),
    ("Hana_to_Akuma", "Hana to Akuma", None),
    ("One Piece {tags} (Official)", "One Piece", None),
    ("20th Century Boys", "20th Century Boys", None),
])
def test_matcher_query_contract(client, folder, query, year):
    assert tuple(doing(client, "metadata:matcher").match_query(folder)) == (query, year)


def test_matcher_score_and_pick_contract(client):
    m = doing(client, "metadata:matcher")
    assert m.match_score("Hana to Akuma", ["Hana to Akuma!"]) == 100
    assert m.match_score("hana to akuma", ["Devil with Flower", "花と悪魔", "Hana to Akuma"]) == 100
    assert m.match_score("Berserk", ["Berserk"], cand_format="NOVEL") == 85    # manga library: not the novel
    assert m.match_score("Berserk", ["Berserk"], novel=True, cand_format="MANGA") == 85  # and the other way
    assert m.match_score("Berserk", ["Berserk"], 1989, 2016) == 90             # the year is off
    assert m.match_pick([(100, "a"), (95, "b")], 90, 10) is None              # not 10 points ahead
    assert m.match_pick([(100, "a"), (60, "b")], 90, 10) == "a"
    assert m.match_pick([(85, "a")], 90, 10) is None                          # below the threshold
    assert m.match_pick([], 90, 10) is None


# --- replacements ----------------------------------------------------------------------------------

VOLUME_SEVEN = '''
from app.plugins import Plugin
from app.plugins.sdk import Parsed

class Sevens(Plugin):
    id = "sevens"
    name = "Everything is volume 7"
    provides = {"scanner:parser": 1}

    def parse_name(self, filename, series_title=""):
        if "boom" in filename:
            raise RuntimeError("broken")
        return Parsed(name=filename, volume=7, chapter=float(len(filename)))
'''

LABELS = '''
from app.plugins import Plugin

class Words(Plugin):
    id = "words"
    name = "Labels in words"
    provides = {"book:label": 1}

    def book_label(self, book):
        return f"Volume {book.eff_volume:g}" if book.eff_volume is not None else "Something"
'''


@pytest.fixture
def plugin(client):
    made = []

    def install(name, code):
        """A new plugin for a job someone does starts off: switching it on switches the built-in one off."""
        path = PLUGINS_DIR / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        made.append(path)
        assert client.post("/api/plugins/reload").status_code == 200
        assert client.put(f"/api/plugins/{name}", json={"enabled": True}).status_code == 200
    yield install
    for path in made:
        path.unlink()
    client.post("/api/plugins/reload")
    for pid in ("filename-parser", "book-labels"):
        client.put(f"/api/plugins/{pid}", json={"enabled": True})


@pytest.fixture
def folder(client):
    from app.scanner import walker
    make_zip(FOLDER / "Parser Test v01.zip")
    make_zip(FOLDER / "Parser Test v02.zip")
    make_zip(FOLDER / "Parser Test boom v03.zip")
    walker.scan_all()
    sid = next(s["id"] for s in client.get("/api/series").json()["items"] if s["title"] == "Parser Test")
    yield sid
    shutil.rmtree(FOLDER)
    walker.scan_all()


def books(client, sid):
    return {b["file_name"]: b for b in client.get(f"/api/series/{sid}/books").json()}


def test_switching_the_parser_reads_the_numbers_again(client, plugin, folder):
    from app.scanner import walker
    before = books(client, folder)
    assert [before[f"Parser Test v0{i}.zip"]["volume"] for i in (1, 2)] == [1, 2]
    first = before["Parser Test v01.zip"]["id"]
    client.put(f"/api/books/{first}/progress", json={"page": 2})

    plugin("sevens", VOLUME_SEVEN)
    assert manager.provider_of("scanner:parser").id == "sevens"  # the owner's own wins the job
    walker.scan_all()
    after = books(client, folder)
    assert {b["id"] for b in after.values()} == {b["id"] for b in before.values()}  # the same rows
    assert after["Parser Test v01.zip"]["volume"] == 7 == after["Parser Test v02.zip"]["volume"]
    assert after["Parser Test v01.zip"]["chapter"] == len("Parser Test v01.zip")
    assert after["Parser Test boom v03.zip"]["volume"] == 3  # it failed there: the built-in parser read it
    assert after["Parser Test v01.zip"]["page"] == 2  # progress is the book's: kept

    walker.scan_all()  # nothing switched: nothing read again, nothing changes
    assert books(client, folder) == after


def test_switching_back(client, folder):
    from app.scanner import walker
    walker.scan_all()  # the plugin from the test before is gone: the built-in parser again
    assert books(client, folder)["Parser Test v01.zip"]["volume"] == 1


def test_labels_follow_the_plugin(client, plugin, folder):
    assert books(client, folder)["Parser Test v01.zip"]["label"] == "Vol 1"  # the built-in one
    plugin("words", LABELS)
    assert books(client, folder)["Parser Test v01.zip"]["label"] == "Volume 1"
    # nobody doing the job (it isn't required): the file's name
    for pid in ("words", "book-labels"):
        assert client.put(f"/api/plugins/{pid}", json={"enabled": False}).status_code == 200
    assert books(client, folder)["Parser Test v01.zip"]["label"] == "Parser Test v01"
    assert client.put("/api/plugins/book-labels", json={"enabled": True}).status_code == 200
    assert books(client, folder)["Parser Test v01.zip"]["label"] == "Vol 1"


def test_the_jobs(client):
    from app.contracts import JOBS
    assert JOBS["scanner:parser"].required and JOBS["scanner:parser"].exclusive
    assert JOBS["metadata:matcher"].required and JOBS["metadata:matcher"].exclusive
    assert not JOBS["book:label"].required and JOBS["book:label"].exclusive
    assert {p.id for p in (manager.builtin_for(j) for j in ("scanner:parser", "metadata:matcher", "book:label"))} \
        == {"filename-parser", "title-matcher", "book-labels"}
