"""Background metadata worker: one thread, a queue, retries when providers are down."""
import logging
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal, get_setting
from app.metadata import service
from app.models import Series

log = logging.getLogger("mangavibe.metadata")

RETRY_MINUTES = 30
REFRESH_DAYS = 7

_q: "queue.Queue[tuple[str, int]]" = queue.Queue()
_pending: set = set()
_lock = threading.Lock()
status = {"queued": 0, "current": None, "done": 0, "matched": 0, "failed": 0,
          "providers_down_until": None,
          # the current run of work (e.g. 2000 new series), for a progress bar: series handled
          # since the queue was last empty, and when that run started (unix time)
          "batch_done": 0, "batch_started": None}


def enqueue(kind, series_id):
    with _lock:
        if (kind, series_id) in _pending:
            return
        _pending.add((kind, series_id))
        if status["current"] is None and _q.empty():  # idle: a new run starts
            status["batch_done"], status["batch_started"] = 0, time.time()
    _q.put((kind, series_id))
    status["queued"] = _q.qsize()


def enqueue_unmatched():
    """Queue every series that was never matched (called after scans and on startup)."""
    with SessionLocal() as db:
        if not get_setting(db, "auto_match", True):
            return
        ids = [sid for (sid,) in db.query(Series.id).filter(Series.match_status == "unmatched",
                                                            Series.missing_since.is_(None))]
    for sid in ids:
        enqueue("match", sid)
    if ids:
        log.info("Queued %d series for metadata matching", len(ids))


def enqueue_stale_releasing():
    cutoff = datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)
    with SessionLocal() as db:
        rows = db.query(Series.id, Series.matched_at).filter(
            Series.publication_status == "RELEASING", Series.metadata_source.is_not(None)).all()
    for sid, at in rows:
        if at is None or (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) < cutoff:
            enqueue("refresh", sid)


def _run():
    service.http.run_in_background()  # the owner's own searches and matches go first
    while True:
        kind, sid = _q.get()
        with _lock:
            _pending.discard((kind, sid))
        status["queued"] = _q.qsize()
        db = SessionLocal()
        try:
            s = db.get(Series, sid)
            if s is None:
                continue
            status["current"] = s.folder_name
            if kind == "match":
                if s.match_status != "unmatched":
                    continue  # matched by hand meanwhile, or ignored
                ok = service.auto_match(db, s)
                status["matched" if ok else "failed"] += 1
            elif kind == "refresh":
                service.refresh(db, s)
            elif kind == "upgrade":
                service.upgrade_to_primary(db, s)
            elif kind == "editions":
                service.fill_editions(db, s)
            db.commit()
            status["done"] += 1
        except service.AllProvidersDown:
            db.rollback()
            _providers_down(kind, sid)
        except service.http.ProviderError as e:
            db.rollback()  # refresh/upgrade while the provider is down: the next cycle tries again
            log.warning("Metadata %s for series %s skipped: %s", kind, sid, e)
        except Exception:
            db.rollback()
            log.exception("Metadata %s failed for series %s", kind, sid)
        finally:
            db.close()
            status["batch_done"] += 1
            status["current"] = None


def _providers_down(kind, sid):
    """Put the series (and everything still queued) back after a pause."""
    until = datetime.now(timezone.utc) + timedelta(minutes=RETRY_MINUTES)
    status["providers_down_until"] = until.isoformat()
    log.warning("No metadata provider reachable; retrying in %d minutes", RETRY_MINUTES)
    waiting = [(kind, sid)]
    while not _q.empty():
        waiting.append(_q.get())
    with _lock:
        _pending.clear()
    status["queued"] = len(waiting)

    def later():
        status["providers_down_until"] = None
        for k, s in waiting:
            enqueue(k, s)
    t = threading.Timer(RETRY_MINUTES * 60, later)
    t.daemon = True
    t.start()


def enqueue_upgrades():
    with SessionLocal() as db:
        rows = db.query(Series.id, Series.metadata_source).filter(Series.match_status == "auto").all()
    for sid, src in rows:
        if src and src.get("retry_primary"):
            enqueue("upgrade", sid)


def enqueue_missing_editions():
    """Matched series without the MangaUpdates editions list (matched before it was stored)."""
    with SessionLocal() as db:
        ids = [sid for (sid,) in db.query(Series.id).filter(
            Series.editions.is_(None), Series.mangaupdates_id.is_not(None), Series.missing_since.is_(None))]
    for sid in ids:
        enqueue("editions", sid)


def _periodic():
    """Every 6 hours: refresh stale RELEASING series, retry AniList for fallback matches."""
    while True:
        time.sleep(6 * 3600)
        try:
            enqueue_stale_releasing()
            enqueue_upgrades()
        except Exception:
            log.exception("Scheduled metadata refresh failed")


def start():
    threading.Thread(target=_run, daemon=True, name="metadata").start()
    threading.Thread(target=_periodic, daemon=True, name="metadata-periodic").start()
    enqueue_unmatched()
    enqueue_upgrades()
    enqueue_missing_editions()
