"""Auto-update: image checks, requirements diff, install/switch/rollback files, API. No network, no real pip."""
import io
import tarfile
from pathlib import Path

import pytest

from app import config, updater
from tests.conftest import TMP

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


def version_files(dockerfile="FROM python\nRUN x\n", req=BASE_REQ, main="X = 1\n"):
    return {"Dockerfile": dockerfile, "docker/entrypoint.sh": "#!/bin/sh\nexec app\n",
            "requirements.txt": req, "app/__init__.py": "", "app/main.py": main, "README.md": "skip me"}


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
    updater.latest = {"sha": SHA1, "date": "2026-10-01T10:00:00Z", "message": "First", "needs_image": False}
    d1 = updater.install(SHA1, downloader=downloader(version_files()))
    assert d1 == code / SHA1 and (d1 / "app" / "main.py").exists()
    assert not (d1 / "README.md").exists()  # only the files the app needs
    assert (code / "current").read_text() == SHA1 and not (code / "previous").exists()
    assert '"First"' in (d1 / ".info.json").read_text()
    assert image == []  # same packages as the image: pip is not run

    updater.install(SHA2, downloader=downloader(version_files(req=BASE_REQ + "natsort==8.4.0\n")))
    assert image == [["natsort==8.4.0"]]
    assert (code / "current").read_text() == SHA2 and (code / "previous").read_text() == SHA1

    (code / "failed").write_text(SHA3)
    updater.install(SHA3, downloader=downloader(version_files()))
    assert not (code / "failed").exists()  # a version that installs again is no longer marked failed
    assert sorted(p.name for p in code.iterdir() if p.is_dir()) == [SHA2, SHA3]  # oldest removed


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
    assert updater.current() == {"sha": SHA1, "date": None, "message": None, "builtin": True}
    d = config.DATA_DIR / "code" / SHA2
    (d / "app").mkdir(parents=True)
    (d / ".info.json").write_text('{"date": "2026-10-02T00:00:00Z", "message": "Two"}')
    monkeypatch.setattr(updater, "__file__", str(d / "app" / "updater.py"))
    assert updater.current() == {"sha": SHA2, "date": "2026-10-02T00:00:00Z", "message": "Two", "builtin": False}
    updater.mark_started()
    assert (d / ".ok").exists()


def test_update_api(client, image, monkeypatch):
    info = client.get("/api/update").json()
    assert info["supported"] and info["available"] is None
    monkeypatch.setattr(updater, "fetch_latest", lambda: {"sha": SHA2, "date": "2026-10-02T00:00:00Z",
                                                           "message": "Two", "needs_image": False})
    info = client.post("/api/update/check").json()
    assert info["available"]["short"] == "2222222" and not info["available"]["skipped"]
    assert client.post("/api/update/skip", json={"sha": SHA2}).json()["available"]["skipped"]
    assert not client.delete("/api/update/skip").json()["available"]["skipped"]
    assert client.post("/api/update/install", json={"sha": SHA3}).status_code == 409  # not the newest

    started = []
    monkeypatch.setattr(updater, "install_in_background", lambda sha: started.append(sha))
    assert client.post("/api/update/install", json={"sha": SHA2}).json() == {"ok": True}
    assert started == [SHA2]

    updater.latest["needs_image"] = True
    assert client.post("/api/update/install", json={"sha": SHA2}).status_code == 409


def test_update_api_outside_docker(client):
    assert not client.get("/api/update").json()["supported"]  # tests run without /opt/mangavibe/image
    assert client.post("/api/update/check").status_code == 400
    assert client.get("/api/settings").json()["auto_update"] is True
