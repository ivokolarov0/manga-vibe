"""Needs Attention: unreadable files, unsupported formats, duplicates, chapter gaps, failed matches,
and series matched to the same metadata entry.

Every row has a `key`. A row the owner marks as "ignore" (a false alarm) is hidden from the
lists and the badge; the keys are kept in the `attention_ignored` setting and can be restored.
Keys: err:<book id>, dup:<series id>:<chapter or volume>, gap:<series id>, same:<series ids>.
"""
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.api.common import book_label, book_numbers, covered_chapters, display_title, poster_version, title_lang
from app.db import get_db, get_setting, set_setting
from app.metadata.service import ID_FIELDS
from app.models import Book, Library, Series
from app.scanner.archives import SUPPORTED, TEXT_EPUB

router = APIRouter(prefix="/api")
KEY_RE = re.compile(r"^(err|dup|gap|same):[0-9a-z:._-]{1,80}$")
MAX_IGNORED = 5000


def chapter_gaps(books):
    """[(after, before)] where whole chapters are missing, e.g. [(14, 52)]."""
    nums = sorted(covered_chapters(books))
    return [(a, b) for a, b in zip(nums, nums[1:]) if b - a > 1]


def same_match_groups(rows):
    """Series that share a metadata ID (AniList, MAL, MangaUpdates or MangaDex).
    rows: [(series id, {id field: value})]. Returns [(key, [series ids], [(id field, value)])],
    where series linked through different IDs (A~B by AniList, B~C by MAL) end up in one group."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_id = {}
    for sid, ids in rows:
        for f in ID_FIELDS:
            if ids.get(f) is not None:
                by_id.setdefault((f, str(ids[f])), []).append(sid)
    for sids in by_id.values():
        for other in sids[1:]:
            parent[find(other)] = find(sids[0])
    groups = {}
    for (f, v), sids in by_id.items():
        if len(sids) > 1:
            g = groups.setdefault(find(sids[0]), [set(), []])
            g[0].update(sids)
            g[1].append((f, v))
    out = []
    for members, shared in groups.values():
        ids = sorted(members)
        part = "-".join(map(str, ids))
        if len(part) > 80:  # a big group: keep the key short (the ignore list checks the length)
            part = hashlib.sha1(part.encode()).hexdigest()
        out.append((f"same:{part}", ids, sorted(shared, key=lambda x: ID_FIELDS.index(x[0]))))
    return out


def _series_ids(db):
    cols = [getattr(Series, f) for f in ID_FIELDS]
    q = db.query(Series.id, *cols).filter(Series.missing_since.is_(None))
    return [(sid, dict(zip(ID_FIELDS, vals))) for sid, *vals in q]


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


def _book_counts(db, series_ids):
    """{series id: books not hidden by the owner}"""
    if not series_ids:
        return {}
    q = (db.query(Book.series_id, func.count(Book.id))
         .filter(Book.series_id.in_(series_ids), Book.missing_since.is_(None),
                 Book.hidden_by_owner.is_not(True))
         .group_by(Book.series_id))
    return dict(q.all())


@router.get("/attention")
def attention(ignored: bool = False, db=Depends(get_db)):
    """The lists. With ignored=true, ignored rows are included (marked "ignored": true)."""
    lang = title_lang(db)
    skip = set(get_setting(db, "attention_ignored", []) or [])
    lists = {k: [] for k in ("matches", "same", "unreadable", "unsupported", "duplicates", "gaps")}
    hidden = {k: 0 for k in lists}

    def add(kind, item):
        item["ignored"] = item.get("key") in skip
        if item["ignored"]:
            hidden[kind] += 1
            if not ignored:
                return
        lists[kind].append(item)

    # Only flagged rows are loaded in full; on a big library almost every series and book is fine,
    # and loading them all as objects made this list take seconds.
    live = {sid for (sid,) in db.query(Series.id).filter(Series.missing_since.is_(None))}
    errors = [b for b in db.query(Book).filter(Book.error.is_not(None), Book.missing_since.is_(None))
              if b.series_id in live]
    flagged = {}
    for b in db.query(Book).filter(Book.duplicate_flag.is_(True), Book.missing_since.is_(None)):
        if b.series_id in live:
            flagged.setdefault(b.series_id, []).append(b)
    numbered = book_numbers(db, func.coalesce(Book.override_chapter, Book.chapter).is_not(None))
    gaps = {sid: g for sid, books in numbered.items() if sid in live and (g := chapter_gaps(books))}
    failed = [sid for (sid,) in db.query(Series.id).filter(Series.match_status == "failed",
                                                           Series.missing_since.is_(None))]
    same = same_match_groups(_series_ids(db))
    needed = {b.series_id for b in errors} | set(flagged) | set(gaps) | set(failed) \
        | {sid for _, sids, _ in same for sid in sids}
    series = {s.id: s for s in db.query(Series).filter(Series.id.in_(needed))} if needed else {}
    name = lambda sid: display_title(series[sid], lang)

    for b in errors:
        not_yet = b.format not in SUPPORTED or b.error.startswith(TEXT_EPUB)
        add("unsupported" if not_yet else "unreadable", {
            "key": f"err:{b.id}", "series_id": b.series_id, "series": name(b.series_id), "book_id": b.id,
            "file": b.file_name, "path": b.path, "error": b.error, "format": b.format})
    for sid, books in flagged.items():
        for part, group in duplicate_groups(books):
            add("duplicates", {"key": f"dup:{sid}:{part}", "series_id": sid, "series": name(sid),
                               "cover": f"/api/series/{sid}/poster?size=sm&v={poster_version(series[sid])}",
                               "label": book_label(group[0]), "files": [_book_info(b) for b in group]})
    for sid, g in gaps.items():
        add("gaps", {"key": f"gap:{sid}", "series_id": sid, "series": name(sid),
                     "gaps": [f"{a} → {b}" for a, b in g]})
    for sid in failed:
        s = series[sid]
        add("matches", {"series_id": sid, "series": name(sid), "folder": s.folder_name,
                        "cover": f"/api/series/{sid}/poster?size=sm&v={poster_version(s)}",
                        "candidates": (s.match_candidates or [])[:3]})

    libraries = {l.id: l.name for l in db.query(Library)}
    book_counts = _book_counts(db, {sid for _, sids, _ in same for sid in sids})
    for key, sids, shared in same:
        members = sorted((series[sid] for sid in sids), key=lambda s: name(s.id).lower())
        add("same", {"key": key, "series": name(members[0].id), "shared": [{"field": f, "id": v} for f, v in shared],
                     "members": [{"series_id": s.id, "series": name(s.id), "folder": s.folder_path,
                                  "library": libraries.get(s.library_id, ""), "match_status": s.match_status,
                                  "books": book_counts.get(s.id, 0)}
                                 for s in members]})

    order = lambda x: (x["series"].lower(), x.get("key", ""))
    out = {k: sorted(v, key=order) for k, v in lists.items()}
    count = lambda k: sum(1 for x in out[k] if not x["ignored"])
    out["count"] = count("matches") + count("same") + count("unreadable") + count("duplicates")
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
    same = sum(1 for key, _, _ in same_match_groups(_series_ids(db)) if key not in skip)
    return {"count": errors + dups + matches + same}


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
