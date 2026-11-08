"""Watching the library folders: a changed series is rescanned on its own (app/scanner/watcher.py)."""
import shutil
import time
from pathlib import Path

import pytest

from tests.conftest import TMP, make_zip

ROOT = TMP / "manga"


def library_id():
    from app.db import SessionLocal
    from app.models import Library
    with SessionLocal() as db:
        return db.query(Library).filter_by(root_path=str(ROOT)).one().id


def series(folder: Path):
    """(missing, number of active books) of the series in this folder, or None."""
    from app.db import SessionLocal
    from app.models import Series
    with SessionLocal() as db:
        s = db.query(Series).filter_by(folder_path=str(folder)).one_or_none()
        if s is None:
            return None
        return s.missing_since is not None, sum(1 for b in s.books if b.missing_since is None)


def test_series_folder():
    from app.scanner.watcher import series_folder
    root = Path("/manga")
    assert series_folder(root, str(root / "One Piece" / "v01" / "ch1.cbz")) == root / "One Piece"
    assert series_folder(root, str(root / "One Piece")) == root / "One Piece"
    assert series_folder(root, str(root)) is None
    assert series_folder(root, str(Path("/elsewhere/x.cbz"))) is None
    # what the scanner skips doesn't start a scan: Synology thumbnails, hidden temp files
    assert series_folder(root, str(root / "One Piece" / "@eaDir" / "x.jpg")) is None
    assert series_folder(root, str(root / "One Piece" / ".ch2.cbz.part")) is None


def test_pending_waits_for_quiet():
    from app.scanner.watcher import Pending
    now = [0.0]
    p = Pending(10, clock=lambda: now[0])
    p.touch("a")
    now[0] = 8
    p.touch("a")  # still copying: the wait starts again
    p.touch("b")
    now[0] = 15
    assert p.ready() == []
    now[0] = 18
    assert p.ready() == ["a", "b"]
    assert p.ready() == []  # taken off the list


def test_scan_folder(client):
    from app.scanner import walker
    lib = library_id()
    folder = ROOT / "Watched Series"
    make_zip(folder / "Watched Series ch01.zip")
    assert walker.scan_folder(lib, folder) is True
    assert series(folder) == (False, 1)

    make_zip(folder / "Watched Series ch02.zip")
    assert walker.scan_folder(lib, folder) is False  # not a new series, but the chapter is added
    assert series(folder) == (False, 2)

    shutil.rmtree(folder)
    walker.scan_folder(lib, folder)
    assert series(folder) == (True, 2)  # hidden like a full scan does, not forgotten

    make_zip(folder / "Watched Series ch01.zip")
    walker.scan_folder(lib, folder)
    assert series(folder) == (False, 1)  # back again


def test_scan_folder_waits_for_a_full_scan(client):
    from app.scanner import walker
    assert walker._lock.acquire(blocking=False)
    try:
        with pytest.raises(walker.Busy):
            walker.scan_folder(library_id(), ROOT / "Watched Series")
    finally:
        walker._lock.release()


def test_falls_back_to_checking(client, monkeypatch):
    """A folder the system can't watch (e.g. Linux's limit on watched folders) is checked instead."""
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver
    from app.scanner import watcher

    class Full(Observer):
        def start(self):
            raise OSError(28, "inotify watch limit reached")

    monkeypatch.setattr(watcher, "Observer", Full)
    w = watcher.Watcher(mode="1", delay=0.3)
    w.sync()
    try:
        assert isinstance(w.watches[library_id()][1], PollingObserver)
    finally:
        w.stop()


MOUNTS = """\
22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw
40 22 0:50 / /manga rw,relatime - cifs //nas/manga rw,vers=3.1.1
41 22 0:51 / /mnt/my\\040drive rw - 9p D:\\134 rw,aname=drvfs
42 40 8:2 / /manga/local rw - btrfs /dev/sdb1 rw
"""


def test_file_system(monkeypatch, tmp_path):
    from app.scanner import watcher
    (tmp_path / "mountinfo").write_text(MOUNTS)
    monkeypatch.setattr(watcher, "MOUNTINFO", tmp_path / "mountinfo")
    monkeypatch.setattr(watcher.os.path, "realpath", lambda p: p)
    assert watcher.file_system("/manga") == "cifs"
    assert watcher.file_system("/manga/One Piece") == "cifs"
    assert watcher.file_system("/manga/local/x") == "btrfs"  # the deepest mount wins
    assert watcher.file_system("/mangaX") == "ext4"          # not inside /manga
    assert watcher.file_system("/mnt/my drive/Manga") == "9p"
    monkeypatch.setattr(watcher, "MOUNTINFO", tmp_path / "missing")
    assert watcher.file_system("/manga") is None              # not Linux


def test_share_is_checked_not_watched(client, monkeypatch, tmp_path):
    """A network share or Docker Desktop folder on Windows reports no changes: checked instead."""
    import os
    from watchdog.observers.polling import PollingObserver
    from app.scanner import watcher
    (tmp_path / "mountinfo").write_text(f"40 22 0:50 / {os.path.realpath(ROOT)} rw - 9p D: rw\n")
    monkeypatch.setattr(watcher, "MOUNTINFO", tmp_path / "mountinfo")
    w = watcher.Watcher(mode="1", delay=0.3)
    w.sync()
    try:
        assert isinstance(w.watches[library_id()][1], PollingObserver)
    finally:
        w.stop()


@pytest.mark.parametrize("mode", ["1", "poll"])
def test_new_series_is_found(client, monkeypatch, mode):
    from app.scanner import watcher
    monkeypatch.setattr(watcher, "POLL_SECONDS", 0.2)
    w = watcher.Watcher(mode=mode, delay=0.3)
    w.sync()
    try:
        assert library_id() in w.watches
        time.sleep(0.5)  # the polling observer takes its first look at the folders
        folder = ROOT / f"Copied Series {mode}"
        make_zip(folder / "Copied ch01.zip")
        make_zip(folder / "Copied ch02.zip")
        deadline = time.monotonic() + 10
        while series(folder) != (False, 2) and time.monotonic() < deadline:
            w.scan_ready()
            time.sleep(0.1)
        assert series(folder) == (False, 2)
    finally:
        w.stop()
    assert w.watches == {}
