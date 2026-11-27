"""Plugin API 20 (issue #208): the Edit and Fix match dialogs, the top bar and Settings' sections as
plugins. A top bar without the Settings button gets the built-in one instead; a Settings section whose
plugin is off is gone, menu entry and all; Settings -> Plugins can't be replaced.
"""
import json
import re
import shutil

import pytest

from tests.conftest import TMP

PLUGINS_DIR = TMP / "data" / "plugins"


def settings_menu(page):
    return [p["key"] for p in json.loads(re.search(r"window\.MV_SETTINGS_PAGES = (.*?);\s", page).group(1))]


def info(client, pid):
    return next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == pid)


def test_the_built_in_ones(client):
    page = client.get("/").text
    assert 'id="settings-link"' in page and 'data-slot="topbar"' in page  # the top bar, with Settings
    assert 'x-data="editSeries()"' in page and 'data-slot="series-edit"' in page
    assert 'x-data="fixMatch()"' in page
    for section in ("library", "display", "reader", "app", "metadata", "plugins", "updates", "backup"):
        assert f"section === '{section}'" in page, section
    assert settings_menu(page) == ["library", "display", "app", "reader", "metadata", "plugins", "updates", "backup"]
    for pid in ("nav", "edit-dialog", "fix-match-dialog", "settings-display", "settings-updates"):
        assert info(client, pid)["warnings"] == [], pid


def test_a_section_switched_off_is_gone(client):
    r = client.put("/api/plugins/settings-updates", json={"enabled": False})
    assert r.status_code == 200, r.text  # not a job the app needs
    try:
        page = client.get("/").text
        assert "section === 'updates'" not in page and "updates" not in settings_menu(page)
        assert "section === 'plugins'" in page  # the core's
    finally:
        client.put("/api/plugins/settings-updates", json={"enabled": True})


CODE = '''
from app.plugins import Plugin

class Mine(Plugin):
    id = "{pid}"
    name = "Mine"
    category = "appearance"
    provides = {provides}
    settings_pages = {pages}
'''


@pytest.fixture
def mine(client, tmp_path):
    """mine(pid, provides, template_name, template, pages): a plugin of the test's own, switched on."""
    made = []

    def install(pid, provides, name, template, pages=()):
        folder = tmp_path / pid.replace("-", "_")
        (folder / "templates" / "views").mkdir(parents=True)
        (folder / "__init__.py").write_text(CODE.format(pid=pid, provides=provides, pages=list(pages)), encoding="utf-8")
        (folder / "templates" / "views" / name).write_text(template, encoding="utf-8")
        shutil.copytree(folder, PLUGINS_DIR / folder.name)
        client.post("/api/plugins/reload")
        r = client.put(f"/api/plugins/{pid}", json={"enabled": True})
        made.append((folder.name, r.json().get("stopped", [])))
        return r
    yield install
    for name, stopped in made:
        for builtin in stopped:
            client.put(f"/api/plugins/{builtin}", json={"enabled": True})
        shutil.rmtree(PLUGINS_DIR / name)
    client.post("/api/plugins/reload")


def test_a_top_bar_without_settings_gets_the_built_in_one(client, mine, caplog):
    mine("my-nav", {"view:nav": 1}, "nav.html", '<header class="my-nav" x-data>{{ slot("topbar") }}</header>')
    page = client.get("/").text
    assert "my-nav" not in page and 'id="settings-link"' in page  # the built-in bar
    assert "leaves out settings_link" in caplog.text
    assert any("settings_link()" in w for w in info(client, "my-nav")["warnings"])


def test_a_top_bar_of_its_own(client, mine):
    mine("my-nav", {"view:nav": 1}, "nav.html",
         '<header class="my-nav" x-data>{{ slot("topbar") }}{{ settings_link() }}</header>')
    page = client.get("/").text
    assert 'class="my-nav"' in page and 'id="settings-link"' in page
    assert info(client, "my-nav")["warnings"] == []


def test_the_sidebar(client):
    """The app's navigation since the redesign (#303): on at first and the fallback, drawing the sidebar with
    Settings as an entry and the libraries in it. The top bar is the alternative: picking it switches the
    sidebar off."""
    from app.plugins import manager
    assert info(client, "nav-sidebar")["enabled"] is True and info(client, "nav")["enabled"] is False
    assert manager.builtin_for("view:nav").id == "nav-sidebar"
    page = client.get("/").text
    assert 'class="sidebar"' in page and 'class="with-sidebar"' in page
    assert page.count('id="settings-link"') == 1 and 'class="nav-label">Settings' in page
    assert 'data-slot="topbar"' in page
    # the libraries in the column (the nav-items slot), as well as their tabs (for phones)
    assert 'data-slot="nav-items"' in page and 'class="nav-section nav-libraries"' in page
    assert 'class="tabs shelf-tabs"' in page
    assert info(client, "nav-sidebar")["warnings"] == []
    nav_job = next(j for j in client.get("/api/plugins").json()["jobs"] if j["key"] == "view:nav")
    assert set(nav_job["plugins"]) >= {"nav", "nav-sidebar"} and nav_job["exclusive"]  # one "pick one" group
    r = client.put("/api/plugins/nav", json={"enabled": True})
    assert r.status_code == 200, r.text
    try:
        assert "nav-sidebar" in r.json()["stopped"]
        page = client.get("/").text
        assert 'class="sidebar"' not in page and 'id="settings-link"' in page
        assert "nav-libraries" not in page  # the top bar leaves the nav-items slot out
        assert manager.builtin_for("view:nav").id == "nav-sidebar"  # still the fallback
    finally:
        client.put("/api/plugins/nav-sidebar", json={"enabled": True})
    assert info(client, "nav")["enabled"] is False


def test_the_sidebar_takes_over_once_on_the_update():
    """NEW_DEFAULTS: an install from before the redesign, where the top bar did the job nobody picked, gets
    the sidebar once; not when the owner picked the top bar, nor over a plugin they installed, nor twice."""
    from app.plugins import PluginManager, manager

    class FakeDb:
        def __init__(self, **settings):
            self.settings = settings

        def commit(self):
            pass

    def run(disabled, chosen=None, seen=None, mine=False):
        db = FakeDb(plugins_disabled=disabled, jobs_chosen=chosen or {}, plugins_known=sorted(m.plugins),
                    jobs_defaults_seen=seen or {})
        m.disabled, m.chosen = set(disabled), dict(chosen or {})
        if mine:
            m.is_builtin = lambda pid: pid != "nav"
        changed = m._new_defaults(db, set(m.plugins))
        m.__dict__.pop("is_builtin", None)
        return changed, m.disabled, db.settings["jobs_defaults_seen"]

    m = PluginManager()
    m.plugins, m.paths = dict(manager.plugins), dict(manager.paths)
    import app.db
    get, put = app.db.get_setting, app.db.set_setting
    app.db.get_setting = lambda db, key, default=None: db.settings.get(key, default)
    app.db.set_setting = lambda db, key, value: db.settings.__setitem__(key, value)
    try:
        changed, off, seen = run(["nav-sidebar"])
        assert changed and "nav" in off and "nav-sidebar" not in off and seen == {"view:nav": "nav-sidebar"}
        changed, off, _ = run(["nav-sidebar"], seen={"view:nav": "nav-sidebar"})  # only once
        assert not changed and "nav-sidebar" in off
        changed, off, _ = run(["nav-sidebar"], chosen={"view:nav": "nav"})  # the owner picked the top bar
        assert not changed and "nav-sidebar" in off
        changed, off, _ = run(["nav-sidebar"], mine=True)  # a top bar the owner installed
        assert not changed and "nav-sidebar" in off
    finally:
        app.db.get_setting, app.db.set_setting = get, put


def test_a_settings_section_of_its_own(client, mine):
    r = mine("my-display", {"settings:display": 1}, "settings-display.html",
             '<section x-show="section === \'display\'"><h2>My display</h2></section>',
             pages=[{"key": "display", "label": "Looks", "icon": "palette", "order": 20, "personal": True}])
    assert "settings-display" in r.json()["stopped"]
    page = client.get("/").text
    assert "My display" in page and settings_menu(page).count("display") == 1


def test_settings_plugins_cant_be_replaced(client, mine):
    r = mine("my-plugins", {"settings:plugins": 1}, "x.html", "")
    assert "doesn't have" in info(client, "my-plugins")["blocked"]  # no such job: Plugins is the core's
