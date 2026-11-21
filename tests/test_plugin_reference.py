"""docs/PLUGIN-REFERENCE.md stays true: every Python sample in it runs, its plugins use only hooks and
attributes the app has, its jobs exist, and its scripts use only what window.MangaVibe has."""
import re
from pathlib import Path

import pytest

from app.contracts import JOBS
from app.plugins import Plugin

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "PLUGIN-REFERENCE.md").read_text(encoding="utf-8")
APP_JS = "\n".join((ROOT / "app" / "web" / "static" / "js" / f).read_text(encoding="utf-8")
                   for f in ("app.js", "reader.js"))


def blocks(lang):
    return re.findall(rf"^```{lang}\n(.*?)^```", DOC, re.S | re.M)


PYTHON = blocks("python")
JS = blocks("js")

# hooks a job's contract adds (docs/PLUGINS.md), on top of the ones on Plugin
JOB_HOOKS = {"parse_name", "match_query", "match_score", "match_pick", "book_label", "query_schema",
             "filter_query", "sort_order", "attention_rows", "attention_count"}
JOB_ATTRS = {"attention_lists"}
# a method named like this is meant as a hook, so it must be one (helpers have other names)
HOOK_PREFIXES = ("on_", "run_", "get_", "set_", "handle_", "page_", "book_", "series_", "match_", "parse_",
                 "query_", "filter_", "sort_", "attention_", "can_", "reading_", "find_", "status")


def plugins_in(code):
    ns = {"__name__": "plugin_reference_sample"}
    exec(compile(code, "PLUGIN-REFERENCE.md", "exec"), ns)  # noqa: S102 - our own docs
    return [v for v in ns.values() if isinstance(v, type) and issubclass(v, Plugin) and v is not Plugin]


def test_there_are_samples():
    assert len(PYTHON) > 50 and len(JS) > 5


@pytest.mark.parametrize("code", PYTHON, ids=lambda c: c.splitlines()[0][:60])
def test_python_sample(code):
    assert code.startswith("#"), "every sample starts with a comment saying what it does"
    for cls in plugins_in(code):
        assert cls.id, f"{cls.__name__} has no id"
        for name, value in vars(cls).items():
            if name.startswith("_"):
                continue
            if callable(value):
                if name.startswith(HOOK_PREFIXES):
                    assert hasattr(Plugin, name) or name in JOB_HOOKS, f"{cls.__name__}.{name} isn't a hook"
            elif not isinstance(value, (staticmethod, classmethod)):
                assert hasattr(Plugin, name) or name in JOB_ATTRS, f"{cls.__name__}.{name} isn't a plugin attribute"
        for job in {**cls.provides, **cls.requires}:
            assert job in JOBS, f"{cls.__name__} names the job {job}, which MangaVibe doesn't have"
        p = cls()
        # the jobs' pure hooks answer the way the core expects
        if "parse_name" in vars(cls):
            from app.plugins.sdk import Parsed
            assert isinstance(p.parse_name("Demo v03 ch012.cbz", "Demo"), Parsed)
        if "match_query" in vars(cls):
            query, _year = p.match_query("Demo (2020)")
            assert isinstance(query, str)
            assert 0 <= p.match_score(query, [query]) <= 100


def test_scripts_use_only_what_the_page_has():
    names = {n for code in JS + PYTHON for n in re.findall(r"MangaVibe\.(\w+)", code)}
    for name in names:
        assert re.search(rf"\b{name}\b\s*[:(]|MangaVibe\.{name}\s*=|\"{name}\"", APP_JS), f"MangaVibe.{name}"
    reader = {n for code in JS for n in re.findall(r"MangaVibe\.reader\.(\w+)", code)}
    for name in reader:
        assert re.search(rf"\b{name}\b", APP_JS), f"MangaVibe.reader.{name}"
    events = {e for code in JS + PYTHON for e in re.findall(r"MangaVibe\.on\(\"[^\"]+\", \"([\w-]+)\"", code)}
    for event in events:
        assert f'"{event}"' in APP_JS, f"the event {event}"


def slug(heading):
    """GitHub's anchor for a heading."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def test_links_inside_the_page_go_somewhere():
    anchors = {slug(h) for h in re.findall(r"^#+ (.+)$", DOC, re.M)}
    for link in re.findall(r"\]\(#([\w-]+)\)", DOC):
        assert link in anchors, f"#{link}"
