"""Jobs (Plugin API 15, issue #203): plugins that do a part of the app, replace each other, and need
each other; safe mode."""
import pytest

from app import contracts
from app.contracts import Job, jobs_of
from app.plugins import BUILTIN, Plugin, PluginManager, SwitchRefused, job_problem
from tests.conftest import TMP


@pytest.fixture
def jobs(monkeypatch):
    """Two test jobs next to the app's own: an exclusive one the app needs, and a shared one."""
    monkeypatch.setattr(contracts, "JOBS", {
        **contracts.JOBS,
        "page": Job("page", "The page", versions=(1, 2), exclusive=True, required=True),
        "extra": Job("extra", "Extras", exclusive=False),
    })
    return contracts.JOBS


def make(pid, builtin=False, **attrs):
    cls = type(pid.title(), (Plugin,), {"id": pid, "name": pid.title(), **attrs})
    return cls(), builtin


def manager(*plugins, off=()):
    m = PluginManager()
    for p, builtin in plugins:
        m.plugins[p.id] = p
        m.paths[p.id] = (BUILTIN / p.id / "__init__.py") if builtin else (TMP / "plugins" / f"{p.id}.py")
    m.disabled = set(off)
    return m


def test_jobs_of():
    assert jobs_of(None) == {} and jobs_of({}) == {}
    assert jobs_of(["a", "b"]) == {"a": 1, "b": 1}
    assert jobs_of({" a ": 2}) == {"a": 2}
    for bad in ("a", {"a": 0}, {"a": "1"}, {"a": True}, {"": 1}, {1: 1}):
        with pytest.raises(ValueError):
            jobs_of(bad)


def test_a_job_or_version_this_app_doesnt_have(jobs):
    unknown, _ = make("unknown", provides={"nope": 1})
    newer, _ = make("newer", provides={"page": 3})
    needs, _ = make("needs", requires={"nope": 1})
    wrong, _ = make("wrong", provides="page")
    fine, _ = make("fine", provides={"page": 2})
    assert "doesn't have" in job_problem(unknown)
    assert "newer MangaVibe" in job_problem(newer) and "v1, v2" in job_problem(newer)
    assert "needs “nope”" in job_problem(needs)
    assert "set up wrong" in job_problem(wrong)
    assert job_problem(fine) == ""
    m = manager((unknown, False), (newer, False), (needs, False), (wrong, False))
    assert set(m.blocked) == {"unknown", "newer", "needs", "wrong"}
    assert m.enabled() == [] and m.get("unknown") is None


def test_needs_and_a_chain_of_needs(jobs):
    page = make("page", builtin=True, provides={"page": 1})
    extra = make("extra", provides={"extra": 1}, requires={"page": 1})
    user = make("user", requires={"extra": 1})
    m = manager(page, extra, user)
    assert m.blocked == {}
    assert [p.id for p in m.enabled()] == ["page", "extra", "user"]
    m.disabled = {"page"}  # without the page, extra can't run, and so user can't either
    assert set(m.blocked) == {"extra", "user"}
    assert "Needs a plugin that does The page (v1)" in m.blocked["extra"]
    assert m.enabled() == []
    # a need at another version isn't met
    m2 = manager(make("page", provides={"page": 2}), make("x", requires={"page": 1}))
    assert "x" in m2.blocked


def test_two_plugins_on_for_one_exclusive_job(jobs):
    builtin = make("builtin", builtin=True, provides={"page": 1})
    mine = make("mine", provides={"page": 1})
    m = manager(builtin, mine)
    assert set(m.blocked) == {"builtin"}  # the owner's own wins
    assert "Mine does The page now" in m.blocked["builtin"]
    m.chosen = {"page": "builtin"}       # ...unless the owner picked the other one last
    assert set(m.blocked) == {"mine"}
    # a shared job: both run
    m2 = manager(make("a", provides={"extra": 1}), make("b", provides={"extra": 1}))
    assert m2.blocked == {} and [p.id for p in m2.providers_of("extra")] == ["a", "b"]


SWITCHES = ("plugins_disabled", "plugins_known", "jobs_chosen")


@pytest.fixture
def db(client):
    """A session for a test manager's switches. They're saved in the same settings as the app's own
    plugins' (e.g. Accounts off), so they're put back afterwards."""
    from app.db import SessionLocal, get_setting, set_setting
    with SessionLocal() as s:
        saved = {k: get_setting(s, k, None) for k in SWITCHES}
        yield s
        s.rollback()
        for k, v in saved.items():
            set_setting(s, k, v)
        s.commit()


def test_switching_on_a_replacement(jobs, db):
    builtin = make("builtin", builtin=True, provides={"page": 1}, html={"sheets": "<i></i>"})
    mine = make("mine", provides={"page": 1})
    user = make("user", requires={"page": 1})
    m = manager(builtin, mine, user, off={"mine"})
    assert m.provider_of("page").id == "builtin"
    done = m.switch(db, "mine", True)
    assert done == {"started": ["mine"], "stopped": ["builtin"]}  # user keeps running: its need is met
    assert m.disabled == {"builtin"} and m.chosen == {"page": "mine"}
    assert m.provider_of("page").id == "mine"
    from app.db import get_setting
    assert get_setting(db, "plugins_disabled") == ["builtin"]
    assert get_setting(db, "jobs_chosen") == {"page": "mine"}


def test_the_only_plugin_doing_a_required_job_stays_on(jobs, db):
    m = manager(make("builtin", builtin=True, provides={"page": 1}), make("mine", provides={"page": 1}),
                off={"mine"})
    with pytest.raises(SwitchRefused, match="which MangaVibe needs"):
        m.switch(db, "builtin", False)
    assert m.disabled == {"mine"}
    m.switch(db, "mine", True)       # replacing it is the way
    assert m.disabled == {"builtin"}
    # a plugin for a job nobody needs switches off as usual
    m2 = manager(make("a", provides={"extra": 1}))
    assert m2.switch(db, "a", False) == {"started": [], "stopped": ["a"]}


def test_a_plugin_that_cant_be_switched_off_isnt_replaced(jobs, db):
    locked = make("locked", builtin=True, provides={"page": 1},
                  can_switch_off=lambda self, ctx: "A library has a password")
    m = manager(locked, make("mine", provides={"page": 1}), off={"mine"})
    with pytest.raises(SwitchRefused, match="Mine can't replace Locked: A library has a password"):
        m.switch(db, "mine", True)
    assert m.disabled == {"mine"} and m.chosen == {}


def test_switching_on_starts_the_plugins_whose_need_is_met(jobs, db):
    started = []
    page = make("page", provides={"page": 1})
    user = make("user", requires={"page": 1}, on_start=lambda self, ctx: started.append(self.id))
    m = manager(page, user, off={"page"})
    assert "user" in m.blocked
    assert m.switch(db, "page", True) == {"started": ["page", "user"], "stopped": []}
    assert started == ["user"]


def test_a_new_replacement_starts_off(jobs, db):
    from app.db import set_setting
    builtin = make("builtin", builtin=True, provides={"page": 1})
    mine = make("mine", provides={"page": 1})
    # a fresh install: both new, the built-in one does the job
    set_setting(db, "plugins_known", [])
    set_setting(db, "plugins_disabled", [])
    set_setting(db, "jobs_chosen", {})
    m = manager(mine, builtin)
    m.load_switches(db)
    assert m.disabled == {"mine"}
    # installed later: it starts off too
    set_setting(db, "plugins_known", ["builtin"])
    set_setting(db, "plugins_disabled", [])
    m = manager(builtin, make("mine", provides={"page": 1}))
    m.load_switches(db)
    assert m.disabled == {"mine"}
    # both on from before (e.g. switched on by hand): the one picked last stays
    set_setting(db, "plugins_known", ["builtin", "mine"])
    set_setting(db, "plugins_disabled", [])
    set_setting(db, "jobs_chosen", {"page": "builtin"})
    m = manager(builtin, make("mine", provides={"page": 1}))
    m.load_switches(db)
    assert m.disabled == {"mine"} and m.chosen == {"page": "builtin"}


# --- through the app ----------------------------------------------------------------

MINE = '''
from app.plugins import Plugin

class Mine(Plugin):
    id = "mine-page"
    name = "My page"
    category = "library"
    provides = {"page": 1}
    html = {"sheets": "<div id='mine-sheet'></div>"}
    js = "window.MINE = 1;"
    css = ".mine-css { color: red; }"
'''


@pytest.fixture
def installed(client, jobs):
    from app.plugins import manager as live
    path = TMP / "data" / "plugins" / "mine_page.py"
    path.write_text(MINE, encoding="utf-8")
    client.post("/api/plugins/reload")
    yield path
    path.unlink()
    client.post("/api/plugins/reload")
    live.chosen.pop("page", None)


def test_api_sends_jobs_and_switches(client, installed):
    r = client.get("/api/plugins").json()
    mine = next(p for p in r["plugins"] if p["id"] == "mine-page")
    assert mine["provides"] == {"page": 1} and mine["requires"] == {} and mine["blocked"] == ""
    assert mine["enabled"] is True  # nobody else does the page here, so it started on
    page = next(j for j in r["jobs"] if j["key"] == "page")
    assert page["doing"] == ["mine-page"] and page["required"] and page["exclusive"]
    # the only one doing a job the app needs
    off = client.put("/api/plugins/mine-page", json={"enabled": False})
    assert off.status_code == 409 and "which MangaVibe needs" in off.json()["detail"]


def test_safe_mode_leaves_out_the_owners_plugins(client, installed):
    page = client.get("/").text
    assert "mine-sheet" in page and "/plugins/mine-page.js" in page
    assert ".mine-css" in client.get("/custom.css").text
    safe = client.get("/?safe=1")
    assert "mine-sheet" not in safe.text and "/plugins/mine-page.js" not in safe.text
    assert 'data-plugin="saved-searches"' in safe.text  # the built-in ones are still there
    assert safe.cookies.get("mv_safe") == "1"
    css = client.get("/custom.css").text  # the cookie came with it
    assert ".mine-css" not in css
    back = client.get("/")
    assert "mine-sheet" in back.text and "mv_safe" not in client.cookies
    assert ".mine-css" in client.get("/custom.css").text


def test_builtin_plugins_have_no_job_problems(client):
    from app.plugins import manager as live
    for p in live.plugins.values():
        assert job_problem(p) == "", p.id
