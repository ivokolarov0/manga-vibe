"""Auto-update: image checks, requirements diff, install/switch/rollback files, API. No network, no real pip."""
import io
import json
import sqlite3
import tarfile
from pathlib import Path

import httpx
import pytest

from app import config, updater

ROOT = Path(__file__).parent.parent
SHA1, SHA2, SHA3 = "1" * 40, "2" * 40, "3" * 40
BASE_REQ = "fastapi==1.0   # web\nhttpx==0.28.1\n"


@pytest.fixture
def image(monkeypatch, tmp_path):
    """A fake /opt/mangavibe/image and a data folder of our own."""
    img = tmp_path / "image"
    img.mkdir()
    (img / "Dockerfile").write_text("FROM python\r\nRUN x\r\n")
    (img / "entrypoint.sh").write_text("#!/bin/sh\nexec app\n")
    (img / "requirements.txt").write_text(BASE_REQ)
    entry = tmp_path / "entrypoint.sh"
    entry.write_text("")
    monkeypatch.setattr(config, "IMAGE_DIR", img)
    monkeypatch.setattr(config, "ENTRYPOINT", entry)
    monkeypatch.setattr(config, "AUTO_UPDATE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(updater, "latest", None)
    pip_calls = []
    real_run = updater._run

    def fake_run(cmd, cwd, env=None, timeout=900):
        if "pip" in cmd:
            pip_calls.append(cmd[cmd.index("--target") + 2:])
            return None
        return real_run(cmd, cwd, env, timeout)

    monkeypatch.setattr(updater, "_run", fake_run)
    return pip_calls


def version_files(dockerfile="FROM python\nRUN x\n", req=BASE_REQ, main="X = 1\n", migrations=None):
    files = {"Dockerfile": dockerfile, "docker/entrypoint.sh": "#!/bin/sh\nexec app\n",
             "requirements.txt": req, "app/__init__.py": "", "app/main.py": main, "README.md": "skip me"}
    if migrations is not None:
        files["app/db.py"] = f"MIGRATIONS = [None] * {migrations}\n"
    return files


def downloader(files):
    def download(sha, dest):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(f"manga-vibe-{sha}/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        updater._extract(buf.getvalue(), dest)
    return download


def test_needs_new_image(image):
    same = {"Dockerfile": "FROM python\nRUN x  \n", "docker/entrypoint.sh": "#!/bin/sh\r\nexec app\r\n"}
    assert not updater.needs_new_image(same)  # line endings and trailing spaces don't count
    assert updater.needs_new_image({**same, "Dockerfile": "FROM python\nRUN apt-get install y\n"})
    assert updater.needs_new_image({"Dockerfile": same["Dockerfile"]})


def test_new_requirements(image):
    new = "FastAPI==1.0\nhttpx==0.29.0\nrapidfuzz==3.0 # fuzzy\n\n# comment\n"
    assert updater.new_requirements(new) == ["httpx==0.29.0", "rapidfuzz==3.0"]
    assert updater.new_requirements(BASE_REQ) == []


def test_install_switch_and_cleanup(image):
    code = config.DATA_DIR / "code"
    updater.latest = {"sha": SHA1, "version": "v1.0.0", "date": "2026-10-01T10:00:00Z", "message": "First",
                      "needs_image": False}
    d1 = updater.install(SHA1, downloader=downloader(version_files()))
    assert d1 == code / SHA1 and (d1 / "app" / "main.py").exists()
    assert not (d1 / "README.md").exists()  # only the files the app needs
    assert (code / "current").read_text() == SHA1 and not (code / "previous").exists()
    assert json.loads((d1 / ".info.json").read_text()) == {
        "sha": SHA1, "version": "v1.0.0", "date": "2026-10-01T10:00:00Z", "message": "First",
        "migrations": None}  # this test version has no app/db.py
    assert image == []  # same packages as the image: pip is not run

    updater.install(SHA2, downloader=downloader(version_files(req=BASE_REQ + "natsort==8.4.0\n")))
    assert image == [["natsort==8.4.0"]]
    assert (code / "current").read_text() == SHA2 and (code / "previous").read_text() == SHA1

    (code / "failed").write_text(SHA3)
    updater.install(SHA3, downloader=downloader(version_files()))
    assert not (code / "failed").exists()  # a version that installs again is no longer marked failed
    assert sorted(p.name for p in code.iterdir() if p.is_dir()) == [SHA2, SHA3]  # oldest removed


def test_install_reuses_packages_of_the_running_version(image):
    """A package the image doesn't have is installed once, not again at every update."""
    code = config.DATA_DIR / "code"
    jxl = BASE_REQ + "pillow-jxl-plugin==1.3.8\n"
    updater.install(SHA1, downloader=downloader(version_files(req=jxl)))
    assert image == [["pillow-jxl-plugin==1.3.8"]]
    (code / SHA1 / "deps" / "pkg.py").write_text("")  # stands in for the installed package

    updater.install(SHA2, downloader=downloader(version_files(req=jxl, main="X = 2\n")))
    assert image == [["pillow-jxl-plugin==1.3.8"]]  # not installed again
    assert (code / SHA2 / "deps" / "pkg.py").exists()

    # one more package: only that one is installed, on top of the copy
    updater.install(SHA3, downloader=downloader(version_files(req=jxl + "natsort==8.4.0\n")))
    assert image[-1] == ["natsort==8.4.0"] and (code / SHA3 / "deps" / "pkg.py").exists()

    # a package's version changed: start clean and install everything
    updater.install(SHA1, downloader=downloader(version_files(req=BASE_REQ + "pillow-jxl-plugin==1.4.0\n")))
    assert image[-1] == ["pillow-jxl-plugin==1.4.0"] and not (code / SHA1 / "deps" / "pkg.py").exists()


def test_install_remembers_the_image(image):
    """The entrypoint runs a new image's own code when a download was made for another image."""
    (config.IMAGE_DIR / "sha").write_text(SHA3 + "\n")
    d = updater.install(SHA1, downloader=downloader(version_files()))
    assert (d / ".image").read_text() == SHA3


def test_cached_check_is_dropped_after_new_image(image, client):
    from app import db as database
    (config.IMAGE_DIR / "sha").write_text(SHA1)
    old = {"sha": SHA2, "date": None, "message": "Two", "needs_image": True, "image": SHA1}
    with database.SessionLocal() as s:
        database.set_setting(s, "update_latest", old)
        s.commit()
    updater.load_cached()
    assert updater.latest == old  # same image: keep it
    (config.IMAGE_DIR / "sha").write_text(SHA2)
    updater.load_cached()
    assert updater.latest is None  # new image: "needs a new image" must be checked again


def test_install_refuses_new_image(image):
    with pytest.raises(RuntimeError, match="new Docker image"):
        updater.install(SHA1, downloader=downloader(version_files(dockerfile="FROM node\n")))
    code = config.DATA_DIR / "code"
    assert not (code / "current").exists() and not list(code.iterdir())  # staging removed


def test_install_keeps_old_version_when_import_fails(image):
    updater.install(SHA1, downloader=downloader(version_files()))
    with pytest.raises(RuntimeError, match="broken"):
        updater.install(SHA2, downloader=downloader(version_files(main="raise RuntimeError('broken')\n")))
    code = config.DATA_DIR / "code"
    assert (code / "current").read_text() == SHA1
    assert not (code / SHA2).exists()


def test_install_rejects_bad_sha(image):
    with pytest.raises(RuntimeError, match="No version"):
        updater.install("../../etc", downloader=downloader(version_files()))


def test_current_version(image, monkeypatch):
    (config.IMAGE_DIR / "sha").write_text(SHA1 + "\n")
    assert updater.current() == {"sha": SHA1, "version": None, "date": None, "message": None, "builtin": True}
    updater.latest = {"sha": SHA1, "version": "v1.0.0", "date": "2026-10-01T00:00:00Z", "message": None}
    assert updater.current()["version"] == "v1.0.0"  # the image is the newest release: show its tag
    d = config.DATA_DIR / "code" / SHA2
    (d / "app").mkdir(parents=True)
    (d / ".info.json").write_text('{"version": "v1.1.0", "date": "2026-10-02T00:00:00Z", "message": "Two"}')
    monkeypatch.setattr(updater, "__file__", str(d / "app" / "updater.py"))
    assert updater.current() == {"sha": SHA2, "version": "v1.1.0", "date": "2026-10-02T00:00:00Z",
                                 "message": "Two", "builtin": False}
    updater.mark_started()
    assert (d / ".ok").exists()


def github(monkeypatch, release, dockerfile="FROM python\nRUN x\n"):
    """Answer the updater's GitHub requests from memory. Returns the requested URLs."""
    seen = []

    def handler(req):
        url = str(req.url)
        seen.append(url)
        if url.endswith("/releases/latest"):
            return httpx.Response(200, json=release) if release else httpx.Response(404, json={})
        if "/commits/" in url:
            return httpx.Response(200, json={"sha": SHA2})
        if url.endswith("/Dockerfile"):
            return httpx.Response(200, text=dockerfile)
        if url.endswith("/docker/entrypoint.sh"):
            return httpx.Response(200, text="#!/bin/sh\nexec app\n")
        return httpx.Response(404)

    monkeypatch.setattr(updater, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


RELEASE = {"tag_name": "v1.4.0", "name": "v1.4.0", "published_at": "2026-10-20T08:00:00Z",
           "html_url": "https://github.com/ivokolarov0/manga-vibe/releases/tag/v1.4.0", "body": "notes"}


def test_fetch_latest_release(image, monkeypatch):
    seen = github(monkeypatch, RELEASE)
    info = updater.fetch_latest()
    assert info == {"sha": SHA2, "version": "v1.4.0", "date": "2026-10-20T08:00:00Z", "message": None,
                    "url": RELEASE["html_url"], "notes": "notes", "changes": [], "image": "", "needs_image": False}
    assert seen[1].endswith("/commits/v1.4.0")  # the tag's commit is what gets installed
    assert f"/{SHA2}/Dockerfile" in seen[2]

    github(monkeypatch, {**RELEASE, "name": "Reader tap zones"}, dockerfile="FROM node\n")
    info = updater.fetch_latest()
    assert info["message"] == "Reader tap zones" and info["needs_image"]


def test_release_notes():
    body = ("This version needs a new Docker image.\r\n\r\n## What's Changed\r\n"
            "* Keep the poster shadow under the buttons by @ivo in https://github.com/ivokolarov0/manga-vibe/pull/174\r\n"
            "* Reader tap zones\r\n\r\n## New Contributors\r\n"
            "* @someone made their first contribution in https://github.com/ivokolarov0/manga-vibe/pull/170\r\n\r\n"
            "**Full Changelog**: https://github.com/ivokolarov0/manga-vibe/compare/v2.6.0...v2.6.1")
    assert updater.release_notes(body) == {
        "notes": "This version needs a new Docker image.",
        "changes": ["Keep the poster shadow under the buttons (#174)", "Reader tap zones"]}
    assert updater.release_notes("") == {"notes": None, "changes": []}


def test_no_release_yet(image, monkeypatch):
    github(monkeypatch, None)
    assert updater.check(force=True) is None
    assert "published release" in updater.status["error"]


def test_update_api(client, image, monkeypatch):
    info = client.get("/api/update").json()
    assert info["supported"] and info["available"] is None
    monkeypatch.setattr(updater, "fetch_latest", lambda: {"sha": SHA2, "date": "2026-10-02T00:00:00Z",
                                                           "message": "Two", "needs_image": False})
    info = client.post("/api/update/check").json()
    assert info["available"]["short"] == "2222222" and not info["available"]["skipped"]  # no tag: commit
    assert client.post("/api/update/skip", json={"sha": SHA2}).json()["available"]["skipped"]
    assert not client.delete("/api/update/skip").json()["available"]["skipped"]
    assert client.post("/api/update/install", json={"sha": SHA3}).status_code == 409  # not the newest

    started = []
    monkeypatch.setattr(updater, "install_in_background", lambda sha, db=None: started.append(sha))
    assert client.post("/api/update/install", json={"sha": SHA2}).json() == {"ok": True}
    assert started == [SHA2]

    updater.latest["needs_image"] = True
    assert client.post("/api/update/install", json={"sha": SHA2}).status_code == 409

    updater.latest["version"] = "v1.4.0"
    assert client.get("/api/update").json()["available"]["short"] == "v1.4.0"


def test_update_api_outside_docker(client):
    assert not client.get("/api/update").json()["supported"]  # tests run without /opt/mangavibe/image
    assert client.post("/api/update/check").status_code == 400
    assert client.get("/api/settings").json()["auto_update"] is True
    assert client.get("/api/update/versions").status_code == 400
    assert client.post("/api/update/rollback", json={"sha": SHA1}).status_code == 400


# ---------------- going back to an earlier version ----------------

SHA4 = "4" * 40


@pytest.fixture
def dbfile(monkeypatch, tmp_path):
    """A database of our own at 3 migrations, with a table to tell its copies apart."""
    f = tmp_path / "mv.db"
    sql(f, "CREATE TABLE schema_version (version INTEGER)")
    sql(f, "INSERT INTO schema_version VALUES (3)")
    sql(f, "CREATE TABLE note (text TEXT)")
    monkeypatch.setattr(updater, "_db_file", lambda: f)
    monkeypatch.setattr(updater, "offered", {})
    monkeypatch.setattr(updater, "pending_restore", None)
    monkeypatch.setattr(updater, "status", dict(updater.status))
    return f


def sql(f, query):
    conn = sqlite3.connect(f)
    try:
        rows = conn.execute(query).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def offline():
    raise httpx.ConnectError("offline")


def running(monkeypatch, sha):
    """Pretend the code runs from DATA_DIR/code/<sha>."""
    monkeypatch.setattr(updater, "__file__", str(config.DATA_DIR / "code" / sha / "app" / "updater.py"))


def release(sha, tag, date):
    return {"sha": sha, "version": tag, "date": date, "message": None, "url": None, "notes": None, "changes": []}


def test_go_back_to_the_previous_version_on_disk(image, dbfile, monkeypatch):
    code = config.DATA_DIR / "code"
    updater.install(SHA1, downloader=downloader(version_files(migrations=3)),
                    meta={"version": "v1.0.0", "date": "2026-10-01T00:00:00Z"})
    assert not (code / ".db-backups").exists()  # as many migrations as the database: no copy needed
    sql(dbfile, "INSERT INTO note VALUES ('before')")
    updater.install(SHA2, downloader=downloader(version_files(migrations=4, main="X = 2\n")),
                    meta={"version": "v1.1.0", "date": "2026-10-02T00:00:00Z"})
    # v1.1.0 has a migration more: the database was copied before it starts
    assert sql(code / ".db-backups" / "schema-3.db", "SELECT text FROM note") == [("before",)]
    # v1.1.0 starts, its migration runs and the owner reads on
    sql(dbfile, "UPDATE schema_version SET version=4")
    sql(dbfile, "INSERT INTO note VALUES ('after')")
    running(monkeypatch, SHA2)
    monkeypatch.setattr(updater, "fetch_releases", offline)

    vs = updater.versions()
    assert vs["current"]["short"] == "v1.1.0" and "not reachable" in vs["error"]
    # still on disk, so GitHub isn't needed
    assert [(v["short"], v["on_disk"], v["builtin"]) for v in vs["versions"]] == [("v1.0.0", True, False)]
    with pytest.raises(updater.NeedsDbChoice) as e:
        updater.check_rollback(SHA1)
    assert (e.value.detail["have"], e.value.detail["target"]) == (4, 3) and e.value.detail["backup"]["date"]

    def no_download(sha, dest):
        raise AssertionError("downloaded")

    assert updater.rollback(SHA1, "restore", downloader=no_download) == SHA2
    assert (code / "current").read_text() == SHA1 and (code / "previous").read_text() == SHA2
    assert sql(dbfile, "SELECT version FROM schema_version") == [(4,)]  # put back right before the restart
    updater._finish(SHA2)
    assert sql(dbfile, "SELECT text FROM note") == [("before",)] and updater.db_schema() == 3
    assert updater.pending_restore is None
    assert (code / SHA2 / "app" / "main.py").exists()  # the version left stays: forward again, no download


def test_older_than_the_database_without_a_copy(image, dbfile):
    code = config.DATA_DIR / "code"
    with pytest.raises(updater.NeedsDbChoice) as e:
        updater.install(SHA1, downloader=downloader(version_files(migrations=2)))
    assert e.value.detail == {"have": 3, "target": 2, "backup": None}
    assert not (code / "current").exists()  # nothing switched
    with pytest.raises(updater.NeedsDbChoice):
        updater.install(SHA1, downloader=downloader(version_files(migrations=2)), db_choice="restore")  # no copy
    updater.install(SHA1, downloader=downloader(version_files(migrations=2)), db_choice="keep")
    assert (code / "current").read_text() == SHA1
    assert updater.pending_restore is None and updater.db_schema() == 3


def test_versions_list_and_going_back_to_a_release(image, dbfile, monkeypatch, tmp_path, client):
    from app import db as database
    code = config.DATA_DIR / "code"
    (config.IMAGE_DIR / "sha").write_text(SHA1)
    builtin = tmp_path / "builtin"
    (builtin / "app").mkdir(parents=True)
    for name, text in version_files(migrations=3).items():
        if name.startswith("app/"):
            (builtin / name).write_text(text)
    monkeypatch.setattr(config, "BUILTIN_DIR", builtin)
    updater.install(SHA2, downloader=downloader(version_files(migrations=3)),
                    meta={"version": "v1.1.0", "date": "2026-10-02T00:00:00Z"})
    running(monkeypatch, SHA2)
    monkeypatch.setattr(updater, "fetch_releases", lambda: [
        release(SHA4, "v1.2.0", "2026-10-03T00:00:00Z"), release(SHA2, "v1.1.0", "2026-10-02T00:00:00Z"),
        release(SHA3, "v1.0.5", "2026-10-01T12:00:00Z"), release(SHA1, "v1.0.0", "2026-10-01T00:00:00Z")])

    vs = updater.versions()
    # newer than the running one is an update, not going back; v1.0.0 is the image's own code
    assert [(v["short"], v["on_disk"], v["builtin"]) for v in vs["versions"]] == [
        ("v1.0.5", False, False), ("v1.0.0", True, True)]
    with pytest.raises(RuntimeError, match="isn't in the list"):
        updater.rollback(SHA4)

    # a release that isn't on disk is downloaded like an update
    updater.latest = {"sha": SHA2}
    left = updater.rollback(SHA3, downloader=downloader(version_files(migrations=3)))
    assert left == SHA2 and (code / "current").read_text() == SHA3
    assert json.loads((code / SHA3 / ".info.json").read_text())["version"] == "v1.0.5"
    # the version left is the newest: it's skipped until a newer one is out
    try:
        updater.skip_left(left)
        with database.SessionLocal() as s:
            assert database.get_setting(s, "update_skipped") == SHA2
    finally:
        with database.SessionLocal() as s:
            database.set_setting(s, "update_skipped", None)
            s.commit()

    # the image's own code: no download, code/current goes away
    updater.versions()
    updater.rollback(SHA1)
    assert not (code / "current").exists() and (code / "previous").read_text() == SHA3


def test_fetch_releases(image, monkeypatch):
    rels = [RELEASE, {**RELEASE, "tag_name": "v1.5.0-rc", "prerelease": True},
            {**RELEASE, "tag_name": "v1.6.0", "draft": True},
            {**RELEASE, "tag_name": "v1.3.0", "name": "Older"}, {**RELEASE, "tag_name": "v0.1.0"}]
    tags = [{"name": "v1.4.0", "commit": {"sha": SHA2}}, {"name": "v1.3.0", "commit": {"sha": SHA1}},
            {"name": "v1.5.0-rc", "commit": {"sha": SHA3}}]

    def handler(req):
        if req.url.path.endswith("/releases"):
            return httpx.Response(200, json=rels)
        if req.url.path.endswith("/tags"):
            return httpx.Response(200, json=tags)
        return httpx.Response(404)

    monkeypatch.setattr(updater, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    # no drafts or pre-releases, and none whose tag isn't in the tags list
    assert [(v["version"], v["sha"], v["message"]) for v in updater.fetch_releases()] == [
        ("v1.4.0", SHA2, None), ("v1.3.0", SHA1, "Older")]


def test_rollback_api(client, image, dbfile, monkeypatch):
    updater.install(SHA1, downloader=downloader(version_files(migrations=3)),
                    meta={"version": "v1.0.0", "date": "2026-10-01T00:00:00Z"})
    updater.install(SHA2, downloader=downloader(version_files(migrations=4)),
                    meta={"version": "v1.1.0", "date": "2026-10-02T00:00:00Z"})
    sql(dbfile, "UPDATE schema_version SET version=4")
    running(monkeypatch, SHA2)
    monkeypatch.setattr(updater, "fetch_releases", offline)
    assert [v["sha"] for v in client.get("/api/update/versions").json()["versions"]] == [SHA1]

    r = client.post("/api/update/rollback", json={"sha": SHA1})
    assert r.status_code == 409 and r.json()["detail"]["needs_db"]["target"] == 3  # the browser asks
    assert client.post("/api/update/rollback", json={"sha": SHA1, "db": "drop"}).status_code == 422
    assert client.post("/api/update/rollback", json={"sha": SHA3}).status_code == 409  # not in the list
    started = []
    monkeypatch.setattr(updater, "rollback_in_background", lambda sha, db=None: started.append((sha, db)))
    assert client.post("/api/update/rollback", json={"sha": SHA1, "db": "restore"}).json() == {"ok": True}
    assert started == [(SHA1, "restore")]


def test_rollback_in_background_asks_about_the_database(image, dbfile, monkeypatch):
    """A download is only known to be older than the database once it's there: the status asks."""
    code = config.DATA_DIR / "code"
    monkeypatch.setattr(updater, "offered", {SHA1: {**release(SHA1, "v0.9.0", None), "on_disk": False, "builtin": False}})
    monkeypatch.setattr(updater, "_download", downloader(version_files(migrations=2)))
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    restarts = []
    monkeypatch.setattr(updater, "restart", lambda: restarts.append(1))

    def wait():
        assert updater.lock.acquire(timeout=60)
        updater.lock.release()

    updater.rollback_in_background(SHA1)
    wait()
    assert updater.status["state"] == "error" and not restarts
    assert updater.status["needs_db"] == {"have": 3, "target": 2, "backup": None}
    updater.rollback_in_background(SHA1, "keep")
    wait()
    assert restarts == [1] and (code / "current").read_text() == SHA1
