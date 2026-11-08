"""Watches the library folders and rescans a series soon after its files change, so a new series or
chapter shows up within seconds instead of at the next scheduled scan (RESCAN_HOURS, which stays as
a safety net for changes the watcher can't see).

The operating system reports the changes (inotify on Linux, ReadDirectoryChangesW on Windows,
FSEvents on macOS), so nothing is read while the folders are quiet. Copying a series causes many
events: they are gathered per series folder and the folder is scanned once nothing has changed in
it for WATCH_DELAY seconds. Network shares changed from another computer and Docker Desktop
folders on Windows report nothing. Folders on such file systems (NO_EVENTS) are checked every
POLL_SECONDS instead, and so is a folder the system can't watch (e.g. Linux's limit on watched
folders). WATCH_FOLDERS=poll checks every folder that way.
"""
import logging
import os
import re
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app import config
from app.db import SessionLocal
from app.models import Library
from app.scanner import walker

log = logging.getLogger("mangavibe.watch")

POLL_SECONDS = 30
SYNC_SECONDS = 60  # how often the list of library folders is checked (added, removed, remounted)
# what changes files; opening or reading a book (opened, closed_no_write) and chmod (attrib) don't
CHANGES = {"created", "deleted", "modified", "moved", "closed"}
# file systems that don't report changes made elsewhere: network shares (another computer) and
# Docker Desktop's Windows folders (9p, which it may show as fakeowner)
NO_EVENTS = {"9p", "fakeowner", "cifs", "smb3", "nfs", "nfs4", "fuse.sshfs"}
MOUNTINFO = Path("/proc/self/mountinfo")


def file_system(path) -> str | None:
    """The type of file system a folder is on, e.g. "ext4" or "cifs" (Linux only, else None)."""
    try:
        lines = MOUNTINFO.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    target = os.path.realpath(path)
    best, kind = "", None
    for line in lines:
        left, _, right = line.partition(" - ")
        fields = left.split()
        if len(fields) < 5 or not right:
            continue
        mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])  # \040 is a space
        inside = target == mount or target.startswith(mount.rstrip("/") + "/")
        if inside and len(mount) >= len(best):  # the deepest mount; a later one covers an earlier one
            best, kind = mount, right.split()[0]
    return kind


def series_folder(root: Path, path: str) -> Path | None:
    """The series folder (a folder right inside the library folder) a changed path belongs to,
    or None for the library folder itself and for folders and files the scanner skips."""
    try:
        parts = Path(path).relative_to(root).parts
    except ValueError:
        return None
    if not parts or any(walker._skip(p) for p in parts):
        return None
    return root / parts[0]


class Pending:
    """Series folders that changed, each with the time of its last change."""

    def __init__(self, delay, clock=time.monotonic):
        self.delay, self.clock = delay, clock
        self._changed = {}  # (library id, series folder) -> time of the last change
        self._lock = threading.Lock()

    def touch(self, key):
        with self._lock:
            self._changed[key] = self.clock()

    def ready(self):
        """The folders without changes for `delay` seconds, taken off the list."""
        now = self.clock()
        with self._lock:
            keys = [k for k, t in self._changed.items() if now - t >= self.delay]
            for k in keys:
                del self._changed[k]
        return keys


class _Handler(FileSystemEventHandler):
    def __init__(self, pending, lib_id, root: Path):
        self.pending, self.lib_id, self.root = pending, lib_id, root

    def on_any_event(self, event):
        if event.event_type not in CHANGES:
            return
        for path in (event.src_path, getattr(event, "dest_path", "")):  # a move changes both places
            folder = series_folder(self.root, str(path)) if path else None
            if folder is not None:
                self.pending.touch((self.lib_id, folder))


class Watcher:
    def __init__(self, mode=None, delay=None):
        self.mode = mode or config.WATCH_FOLDERS
        self.pending = Pending(config.WATCH_DELAY if delay is None else delay)
        self.watches = {}  # library id -> (root path, observer)

    def sync(self):
        """Watch every library folder that exists; stop watching removed or unmounted ones."""
        with SessionLocal() as db:
            roots = {lib.id: lib.root_path for lib in db.query(Library)}
        for lib_id, (root, observer) in list(self.watches.items()):
            if roots.get(lib_id) != root or not observer.is_alive() or not Path(root).is_dir():
                self._stop(lib_id)
        for lib_id, root in roots.items():
            if lib_id not in self.watches and Path(root).is_dir():
                observer = self._watch(lib_id, root)
                if observer is not None:
                    self.watches[lib_id] = (root, observer)

    def _watch(self, lib_id, root):
        handler = _Handler(self.pending, lib_id, Path(root))
        kind = file_system(root) if self.mode != "poll" else None
        if kind in NO_EVENTS:
            log.info("%s is on a %s file system, which doesn't report changes: checking it every %d "
                     "seconds instead", root, kind, POLL_SECONDS)
        elif self.mode != "poll":
            observer = Observer()
            try:
                observer.schedule(handler, root, recursive=True)
                observer.start()
                log.info("Watching %s for changes", root)
                return observer
            except OSError as e:
                observer.stop()
                log.warning("Can't watch %s for changes (%s); checking it every %d seconds instead",
                            root, e, POLL_SECONDS)
        observer = PollingObserver(timeout=POLL_SECONDS)
        try:
            observer.schedule(handler, root, recursive=True)
            observer.start()
        except OSError as e:
            observer.stop()
            log.warning("Can't check %s for changes: %s", root, e)
            return None
        if self.mode == "poll":
            log.info("Checking %s for changes every %d seconds", root, POLL_SECONDS)
        return observer

    def _stop(self, lib_id):
        root, observer = self.watches.pop(lib_id)
        observer.stop()
        observer.join(timeout=5)
        log.info("Stopped watching %s", root)

    def stop(self):
        for lib_id in list(self.watches):
            self._stop(lib_id)

    def scan_ready(self):
        """Scan the folders that have been quiet long enough. One that can't be scanned yet
        (another scan is running) waits for the next round."""
        for lib_id, folder in self.pending.ready():
            try:
                walker.scan_folder(lib_id, folder)
                log.info("Scanned %s after changes", folder)
            except walker.Busy:
                self.pending.touch((lib_id, folder))
            except Exception:
                log.exception("Failed to scan series folder %s", folder)

    def run(self):
        last_sync = 0.0
        while True:
            if time.monotonic() - last_sync >= SYNC_SECONDS:
                last_sync = time.monotonic()
                try:
                    self.sync()
                except Exception:
                    log.exception("Could not update the watched folders")
            self.scan_ready()
            time.sleep(1)


def start():
    """Start watching in the background (from app.main), unless WATCH_FOLDERS=0."""
    if config.WATCH_FOLDERS == "0":
        return None
    w = Watcher()
    threading.Thread(target=w.run, daemon=True, name="watcher").start()
    return w
