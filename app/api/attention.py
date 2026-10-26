"""Needs Attention: unreadable files, unsupported formats, duplicates, chapter gaps, failed matches.

Every row has a `key`. A row the owner marks as "ignore" (a false alarm) is hidden from the
lists and the badge; the keys are kept in the `attention_ignored` setting and can be restored.
Keys: err:<book id>, dup:<series id>:<chapter or volume>, gap:<series id>.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.common import book_label, display_title, poster_version, title_lang
from app.db import get_db, get_setting, set_setting
from app.models import Book, Series
from app.scanner.archives import SUPPORTED, TEXT_EPUB

router = APIRouter(prefix="/api")
KEY_RE = re.compile(r"^(err|dup|gap):[0-9a-z:._-]{1,80}$")
MAX_IGNORED = 5000


def chapter_gaps(books):
    """[(after, before)] where whole chapters are missing, e.g. [(14, 52)]."""
    covered = set()
    for b in books:
        ch, extra = b.eff_chapter, b.eff_extra
        if ch is None or extra or b.hidden:
            continue
        end = b.eff_chapter_end if b.eff_chapter_end is not None else ch
        covered.update(range(int(ch), int(end) + 1))
    nums = sorted(covered)
    return [(a, b) for a, b in zip(nums, nums[1:]) if b - a > 1]


def _num(v):
    return "" if v is None else (str(int(v)) if float(v).is_integer() else str(v))


def duplicate_groups(books):
    """Books with the same number, grouped: [(key part, [books])]."""
    groups = {}
    for b in sorted((b for b in books if b.duplicate_flag), key=lambda b: b.sort_index):
        if b.eff_chapter is not None:
            part = f"c{_num(b.eff_chapter)}-{_num(b.eff_chapter_end)}"
        else:
            part = f"v{_num(b.eff_volume)}"
        groups.setdefault(part, []).append(b)
    return list(groups.items())


def _book_info(b):
    return {"book_id": b.id, "file": b.file_name, "path": b.path, "label": book_label(b),
            "page_count": b.page_count, "file_size": b.file_size, "revision": b.revision,
            "thumbnail": f"/api/books/{b.id}/thumbnail?v={int(b.mtime)}", "hidden": b.hidden_by_owner}


@router.get("/attention")
def attention(ignored: bool = False, db=Depends(get_db)):
    """The lists. With ignored=true, ignored rows are included (marked "ignored": true)."""
    lang = title_lang(db)
    skip = set(get_setting(db, "attention_ignored", []) or [])
    series = {s.id: s for s in db.query(Series).filter(Series.missing_since.is_(None))}
    name = lambda sid: display_title(series[sid], lang)
    lists = {k: [] for k in ("matches", "unreadable", "unsupported", "duplicates", "gaps")}
    hidden = {k: 0 for k in lists}

    def add(kind, item):
        item["ignored"] = item.get("key") in skip
        if item["ignored"]:
            hidden[kind] += 1
            if not ignored:
                return
        lists[kind].append(item)

    for b in db.query(Book).filter(Book.error.is_not(None), Book.missing_since.is_(None)):
        if b.series_id not in series:
            continue
        not_yet = b.format not in SUPPORTED or b.error.startswith(TEXT_EPUB)
        add("unsupported" if not_yet else "unreadable", {
            "key": f"err:{b.id}", "series_id": b.series_id, "series": name(b.series_id), "book_id": b.id,
            "file": b.file_name, "path": b.path, "error": b.error, "format": b.format})

    books_by_series = {}
    for b in db.query(Book).filter(Book.missing_since.is_(None)):
        books_by_series.setdefault(b.series_id, []).append(b)
    for sid, s in series.items():
        books = books_by_series.get(sid, [])
        if s.has_duplicates:
            for part, group in duplicate_groups(books):
                add("duplicates", {"key": f"dup:{sid}:{part}", "series_id": sid, "series": name(sid),
                                   "cover": f"/api/series/{sid}/poster?size=sm&v={poster_version(s)}",
                                   "label": book_label(group[0]), "files": [_book_info(b) for b in group]})
        g = chapter_gaps(books)
        if g:
            add("gaps", {"key": f"gap:{sid}", "series_id": sid, "series": name(sid),
                         "gaps": [f"{a} → {b}" for a, b in g]})
        if s.match_status == "failed":
            add("matches", {"series_id": sid, "series": name(sid), "folder": s.folder_name,
                            "candidates": (s.match_candidates or [])[:3]})

    order = lambda x: (x["series"].lower(), x.get("key", ""))
    out = {k: sorted(v, key=order) for k, v in lists.items()}
    count = lambda k: sum(1 for x in out[k] if not x["ignored"])
    out["count"] = count("matches") + count("unreadable") + count("duplicates")
    out["ignored_counts"] = hidden
    return out


@router.get("/attention/count")
def attention_count(db=Depends(get_db)):
    """Just the badge number (same rules as attention()["count"]), loading only flagged rows,
    so polling it every minute stays cheap on a big library."""
    skip = set(get_setting(db, "attention_ignored", []) or [])
    live = {sid for (sid,) in db.query(Series.id).filter(Series.missing_since.is_(None))}
    errors = sum(
        1 for bid, fmt, err, sid in db.query(Book.id, Book.format, Book.error, Book.series_id)
        .filter(Book.error.is_not(None), Book.missing_since.is_(None))
        if sid in live and fmt in SUPPORTED and not err.startswith(TEXT_EPUB) and f"err:{bid}" not in skip)
    flagged = {}
    for b in db.query(Book).filter(Book.duplicate_flag.is_(True), Book.missing_since.is_(None)):
        if b.series_id in live:
            flagged.setdefault(b.series_id, []).append(b)
    dups = sum(1 for sid, books in flagged.items() for part, _ in duplicate_groups(books)
               if f"dup:{sid}:{part}" not in skip)
    matches = (db.query(Series).filter(Series.match_status == "failed", Series.missing_since.is_(None)).count())
    return {"count": errors + dups + matches}


class IgnoreBody(BaseModel):
    key: str = Field(max_length=100)
    ignore: bool = True


@router.post("/attention/ignore")
def ignore_row(body: IgnoreBody, db=Depends(get_db)):
    """Hide one row as a false alarm (or bring it back with ignore=false)."""
    if not KEY_RE.match(body.key):
        raise HTTPException(400, "Unknown row")
    keys = set(get_setting(db, "attention_ignored", []) or [])
    (keys.add if body.ignore else keys.discard)(body.key)
    if len(keys) > MAX_IGNORED:
        raise HTTPException(400, "Too many ignored rows")
    set_setting(db, "attention_ignored", sorted(keys))
    db.commit()
    return {"ok": True}
