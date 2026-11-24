"""Settings → Backup (issue #262): everything in one file, and back. The round trip, the tokens left out,
and files from older and newer versions."""
import io
import json
import sqlite3
import zipfile

from tests.conftest import TMP


def _wait_for_scan():
    from app.scanner import walker
    while walker.status["running"]:
        pass
    walker.scan_all()  # the import marked every file for reading again: done here, before other tests


def export(client, tokens=False) -> bytes:
    r = client.get(f"/api/backup/export?tokens={'true' if tokens else 'false'}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "mangavibe-backup-" in r.headers["content-disposition"]
    return r.content


def import_(client, data: bytes):
    return client.post("/api/backup/import", content=data, headers={"Content-Type": "application/zip"})


def _db_of(data: bytes, tmp_path, name="export.db"):
    """The database inside an export, as a file to look into."""
    path = tmp_path / name
    path.write_bytes(zipfile.ZipFile(io.BytesIO(data)).read("mangavibe.db"))
    return path


def _rewrite(data: bytes, tmp_path, manifest=None, schema=None) -> bytes:
    """The export with another manifest and/or schema_version in its database."""
    src = zipfile.ZipFile(io.BytesIO(data))
    m = {**json.loads(src.read("manifest.json")), **(manifest or {})}
    db = src.read("mangavibe.db")
    if schema is not None:
        path = _db_of(data, tmp_path, "rewrite.db")
        conn = sqlite3.connect(path)
        conn.execute("UPDATE schema_version SET version = ?", (schema,))
        conn.commit()
        conn.close()
        db = path.read_bytes()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            z.writestr(n, json.dumps(m) if n == "manifest.json" else db if n == "mangavibe.db" else src.read(n))
    return out.getvalue()


def _first_book(client):
    s = next(x for x in client.get("/api/series").json()["items"] if x["title"] == "Hana to Akuma")
    return s, client.get(f"/api/series/{s['id']}/books").json()[0]


def test_export_holds_the_database_posters_and_a_manifest(client):
    from app import config
    from app.db import MIGRATIONS
    (config.COVERS_DIR / "999999.jpg").write_bytes(b"poster")  # an uploaded poster exists nowhere else
    try:
        z = zipfile.ZipFile(io.BytesIO(export(client)))
        m = json.loads(z.read("manifest.json"))
        assert m["app"] == "MangaVibe" and m["format"] == 1 and m["schema"] == len(MIGRATIONS)
        assert m["series"] > 0 and m["books"] > 0 and m["tokens"] is False
        assert z.read("covers/999999.jpg") == b"poster"
        assert "custom.css" in z.namelist()
    finally:
        (config.COVERS_DIR / "999999.jpg").unlink()


def test_round_trip_gives_back_progress_and_settings(client, tmp_path):
    from app import config
    s, book = _first_book(client)
    client.put(f"/api/books/{book['id']}/progress", json={"page": 4})
    client.put("/api/settings", json={"card_size": "large"})
    (config.COVERS_DIR / "999998.jpg").write_bytes(b"poster")
    data = export(client)
    try:
        # everything changes after the export...
        client.put(f"/api/books/{book['id']}/progress", json={"page": 0, "completed": False})
        client.put("/api/settings", json={"card_size": "small"})
        (config.COVERS_DIR / "999998.jpg").unlink()
        (config.COVERS_DIR / "stray.jpg").write_bytes(b"another server's")

        r = import_(client, data)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["series"] > 0 and out["books"] > 0
        _wait_for_scan()

        # ...and the import puts it back
        assert client.get("/api/settings").json()["card_size"] == "large"
        _, again = _first_book(client)
        assert again["id"] == book["id"] and again["page"] == 4
        assert (config.COVERS_DIR / "999998.jpg").read_bytes() == b"poster"
        assert not (config.COVERS_DIR / "stray.jpg").exists()
        # the database from before the import was kept, with what had changed
        saved = sorted((TMP / "data" / "backups").glob("before-import-*.db"))
        assert saved and str(saved[-1]) == out["safety_copy"]
        conn = sqlite3.connect(saved[-1])
        assert conn.execute("SELECT value FROM setting WHERE key = 'card_size'").fetchone()[0] == '"small"'
        conn.close()
    finally:
        (config.COVERS_DIR / "999998.jpg").unlink(missing_ok=True)
        client.put(f"/api/books/{book['id']}/progress", json={"page": 0, "completed": False})
        client.put("/api/settings", json={"card_size": "medium"})


def test_tokens_stay_out_unless_asked_for(client, tmp_path):
    client.put("/api/plugins/github-token", json={"token": "ghp_secret"})
    try:
        def token(data, name):
            conn = sqlite3.connect(_db_of(data, tmp_path, name))
            try:
                return conn.execute("SELECT value FROM setting WHERE key = 'plugin_github_token'").fetchone()
            finally:
                conn.close()
        assert token(export(client), "without.db") is None
        assert token(export(client, tokens=True), "with.db") == ('"ghp_secret"',)
        assert json.loads(zipfile.ZipFile(io.BytesIO(export(client, tokens=True))).read("manifest.json"))["tokens"]
    finally:
        client.put("/api/plugins/github-token", json={"token": ""})


def test_a_file_from_a_newer_version_is_refused(client, tmp_path):
    from app.db import MIGRATIONS
    data = export(client)
    before = client.get("/api/settings").json()["card_size"]
    for bad in (_rewrite(data, tmp_path, manifest={"schema": len(MIGRATIONS) + 1, "version": "v99.0.0"}),
                _rewrite(data, tmp_path, manifest={"format": 2}),
                _rewrite(data, tmp_path, schema=len(MIGRATIONS) + 1)):  # the manifest says otherwise
        r = import_(client, bad)
        assert r.status_code == 400, r.text
        assert "newer MangaVibe" in r.json()["detail"] and "Update MangaVibe first" in r.json()["detail"]
    assert "v99.0.0" in import_(client, _rewrite(data, tmp_path, manifest={"schema": 999, "version": "v99.0.0"})).json()["detail"]
    assert client.get("/api/settings").json()["card_size"] == before  # nothing changed


def test_a_file_from_an_older_version_is_updated(client, tmp_path):
    from app import backup
    from app.db import MIGRATIONS
    older = _rewrite(export(client), tmp_path, manifest={"schema": len(MIGRATIONS) - 1},
                     schema=len(MIGRATIONS) - 1)
    r = import_(client, older)
    assert r.status_code == 200, r.text
    _wait_for_scan()
    from app import db as database
    conn = sqlite3.connect(database.engine.url.database)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == len(MIGRATIONS)
    finally:
        conn.close()
    assert backup.FORMAT == 1


def test_not_an_export_is_refused(client):
    r = import_(client, b"not a zip")
    assert r.status_code == 400 and "not a .zip" in r.json()["detail"]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("hello.txt", "hi")
    r = import_(client, out.getvalue())
    assert r.status_code == 400 and "isn't a MangaVibe export" in r.json()["detail"]


def test_settings_has_a_backup_page(client):
    page = client.get("/").text
    assert "section === 'backup'" in page
    assert '@click="exportBackup()"' in page and ":class=\"{ busy: exporting }\"" in page
    assert 'importBackup($event)' in page and 'x-text="importText()"' in page


def test_export_is_prepared_then_downloaded_once(client):
    r = client.post("/api/backup/export?tokens=false")
    assert r.status_code == 200, r.text
    ready = r.json()
    assert ready["id"].startswith("export-") and ready["file"].startswith("mangavibe-backup-") and ready["size"] > 0
    d = client.get(f"/api/backup/download/{ready['id']}")
    assert d.status_code == 200 and d.headers["content-type"] == "application/zip"
    assert ready["file"] in d.headers["content-disposition"]
    assert json.loads(zipfile.ZipFile(io.BytesIO(d.content)).read("manifest.json"))["app"] == "MangaVibe"
    assert client.get(f"/api/backup/download/{ready['id']}").status_code == 404  # removed once sent
    assert client.get("/api/backup/download/..%2Fmangavibe.db").status_code == 404
    assert client.get("/api/backup/download/tmpabc.zip").status_code == 404  # only prepared exports


def test_settings_of_a_plugin_that_isnt_loaded_stay_out(client, tmp_path):
    from app.db import SessionLocal, set_setting
    with SessionLocal() as db:
        set_setting(db, "plugin:gone-plugin", {"api_key": "secret", "shown": 1})
        db.commit()
    try:
        def row(data, name):
            conn = sqlite3.connect(_db_of(data, tmp_path, name))
            try:
                return conn.execute("SELECT value FROM setting WHERE key = 'plugin:gone-plugin'").fetchone()
            finally:
                conn.close()
        assert row(export(client), "without.db") is None
        assert "secret" in row(export(client, tokens=True), "with.db")[0]
    finally:
        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("DELETE FROM setting WHERE key = 'plugin:gone-plugin'"))
            db.commit()


def test_a_failed_import_puts_everything_back(client, monkeypatch):
    from app import backup, config
    data = export(client)
    client.put("/api/settings", json={"card_size": "small"})
    (config.COVERS_DIR / "999997.jpg").write_bytes(b"this server's")
    css = config.CUSTOM_CSS.read_bytes() if config.CUSTOM_CSS.is_file() else None
    real_swap = backup._swap_files

    def swap_then_fail(new, old):
        real_swap(new, old)
        raise OSError("disk full")
    monkeypatch.setattr(backup, "_swap_files", swap_then_fail)
    try:
        try:
            r = import_(client, data)
            assert r.status_code == 500
        except OSError:
            pass  # the test client raises the server's error
        assert client.get("/api/settings").json()["card_size"] == "small"
        assert (config.COVERS_DIR / "999997.jpg").read_bytes() == b"this server's"
        assert (config.CUSTOM_CSS.read_bytes() if config.CUSTOM_CSS.is_file() else None) == css
        assert client.get("/api/plugins").status_code == 200  # the plugins were started again
    finally:
        (config.COVERS_DIR / "999997.jpg").unlink(missing_ok=True)
        client.put("/api/settings", json={"card_size": "medium"})


def test_import_removes_a_custom_css_the_file_doesnt_have(client, tmp_path):
    from app import config
    data = export(client)
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            if n != "custom.css":
                z.writestr(n, src.read(n))
    css = config.CUSTOM_CSS.read_bytes() if config.CUSTOM_CSS.is_file() else b""
    config.CUSTOM_CSS.write_bytes(b"body { display: none }")
    try:
        assert import_(client, out.getvalue()).status_code == 200
        _wait_for_scan()
        assert not config.CUSTOM_CSS.exists()
    finally:
        config.CUSTOM_CSS.write_bytes(css)


def test_bad_poster_names_in_the_file_are_skipped(client, tmp_path):
    src = zipfile.ZipFile(io.BytesIO(export(client)))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            z.writestr(n, src.read(n))
        z.writestr("covers/..", b"x")
        z.writestr("covers/.", b"x")
    assert import_(client, out.getvalue()).status_code == 200
    _wait_for_scan()


def test_import_errors_are_in_the_persons_language(client, tmp_path):
    from app.db import MIGRATIONS
    bad = _rewrite(export(client), tmp_path, manifest={"schema": len(MIGRATIONS) + 1, "version": "v99.0.0"})
    r = client.post("/api/backup/import", content=bad,
                    headers={"Content-Type": "application/zip", "Accept-Language": "de"})
    assert r.status_code == 400
    assert "v99.0.0" in r.json()["detail"] and "newer MangaVibe" not in r.json()["detail"]


def test_a_metadata_job_from_before_an_import_is_dropped(monkeypatch):
    from app.metadata import worker
    ran = []
    monkeypatch.setattr(worker, "_handle_now", lambda kind, sid: ran.append((kind, sid)))
    generation = worker._generation  # taken from the queue now...
    worker.clear()                   # ...an import ran meanwhile
    assert worker._handle("refresh", 42, generation) is None and ran == []
    worker._handle("refresh", 42, worker._generation)
    assert ran == [("refresh", 42)]


def test_import_waits_for_plugin_threads(client):
    import threading
    from app.plugins import manager
    stopped = threading.Event()
    p = next(iter(manager.plugins.values()))
    from app.db import SessionLocal
    from app.plugins import Context
    with SessionLocal() as db:
        def work(ctx):
            while not ctx.should_stop():
                stopped.wait(0.05)
            stopped.set()
        Context(p, db).run_in_thread(work)
    assert manager.stop_all_threads(timeout=10)
    assert stopped.is_set()
