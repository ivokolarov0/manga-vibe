"""Needs attention (built-in plugin, issue #219): the app's own lists, doing the shared `attention:list` job.

Files that can't be opened, books no reader can show, series with no confident match, series matched to the
same entry, duplicate chapters and missing chapters. Each row has a `key`; ignoring one (a false alarm) is
the core's (app/api/attention.py), so these keys must stay as they are: err:<book id>,
dup:<series id>:<chapter or volume>, gap:<series id>, same:<series ids>. Matches have no key: a failed
match is fixed, not ignored.

The rows' markup is templates/attention/*.html (in Settings' attention component). Other plugins add lists
the same way: attention_lists, attention_rows(), and a template of their own or the plain one.
"""
import hashlib

from sqlalchemy import func

from app.plugins import Plugin
from app.plugins.sdk import (BOOK_FORMATS as SUPPORTED, ID_FIELDS, TEXT_BOOK, TEXT_EPUB, Book, Library,
                             Series, book_label, chapter_gaps, display_title, numberings, poster_version,
                             title_lang, visible_series)


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
         .filter(Book.series_id.in_(series_ids), Book.missing_since.is_(None), Book.hidden_by_owner.is_not(True))
         .group_by(Book.series_id))
    return dict(q.all())


def _series_ids(db, shown):
    cols = [getattr(Series, f) for f in ID_FIELDS]
    q = db.query(Series.id, *cols).filter(Series.missing_since.is_(None), *shown)
    return [(sid, dict(zip(ID_FIELDS, vals))) for sid, *vals in q]


def _live(db, shown):
    return {sid for (sid,) in db.query(Series.id).filter(Series.missing_since.is_(None), *shown)}


def _flagged(db, live):
    out = {}
    for b in db.query(Book).filter(Book.duplicate_flag.is_(True), Book.missing_since.is_(None)):
        if b.series_id in live:
            out.setdefault(b.series_id, []).append(b)
    return out


class Rows:
    """Works out the rows of one list, for one request (what it may see: `shown`). Only flagged rows are
    loaded in full; on a big library almost every series and book is fine."""

    def __init__(self, db, shown):
        self.db, self.shown, self.lang = db, shown, title_lang(db)
        self._series = {}

    def series(self, ids):
        missing = set(ids) - set(self._series)
        if missing:
            self._series.update({s.id: s for s in self.db.query(Series).filter(Series.id.in_(missing))})
        return self._series

    def name(self, sid):
        return display_title(self.series([sid])[sid], self.lang)

    def files(self, kind):
        """unreadable: files that can't be opened. unsupported: formats not read yet, and text books (light
        novels) while no plugin has a reader for them."""
        from app.plugins import manager
        live = _live(self.db, self.shown)
        errors = [b for b in self.db.query(Book).filter(Book.error.is_not(None), Book.missing_since.is_(None))
                  if b.series_id in live]
        out = []
        for b in errors:
            not_yet = b.format not in SUPPORTED or b.error.startswith(TEXT_EPUB)
            if ("unsupported" if not_yet else "unreadable") == kind:
                out.append({"key": f"err:{b.id}", "series_id": b.series_id, "series": self.name(b.series_id),
                            "book_id": b.id, "file": b.file_name, "path": b.path, "error": b.error, "format": b.format})
        if kind == "unsupported" and not manager.reader_for(TEXT_BOOK):
            for b in self.db.query(Book).filter(Book.format == TEXT_BOOK, Book.missing_since.is_(None)):
                if b.series_id in live:
                    out.append({"key": f"err:{b.id}", "series_id": b.series_id, "series": self.name(b.series_id),
                                "book_id": b.id, "file": b.file_name, "path": b.path, "format": b.format,
                                "error": "A text book (a light novel). Add a reader for text books in Settings → "
                                         "Plugins to read it (e.g. Light novel reader)."})
        return out

    def duplicates(self):
        out = []
        for sid, books in _flagged(self.db, _live(self.db, self.shown)).items():
            s = self.series([sid])[sid]
            for part, group in duplicate_groups(books):
                out.append({"key": f"dup:{sid}:{part}", "series_id": sid, "series": self.name(sid),
                            "cover": f"/api/series/{sid}/poster?size=sm&v={poster_version(s)}",
                            "label": book_label(group[0]), "files": [_book_info(b) for b in group]})
        return out

    def gaps(self):
        # a series marked complete by hand (omnibus editions) has no missing chapters to report
        rows = self.db.query(Series.id, Series.numbering).filter(
            Series.missing_since.is_(None), *self.shown, Series.collection_complete.is_not(True)).all()
        found = {sid: g for sid, num in numberings(self.db, rows).items() if (g := chapter_gaps(num))}
        return [{"key": f"gap:{sid}", "series_id": sid, "series": self.name(sid),
                 "gaps": [str(a + 1) if b - a == 2 else f"{a + 1}–{b - 1}" for a, b in g]}
                for sid, g in found.items()]

    def matches(self):
        failed = [sid for (sid,) in self.db.query(Series.id).filter(
            Series.match_status == "failed", Series.missing_since.is_(None), *self.shown)]
        out = []
        for sid in failed:
            s = self.series([sid])[sid]
            out.append({"series_id": sid, "series": self.name(sid), "folder": s.folder_name,
                        "cover": f"/api/series/{sid}/poster?size=sm&v={poster_version(s)}",
                        "candidates": (s.match_candidates or [])[:3]})
        return out

    def same(self):
        groups = same_match_groups(_series_ids(self.db, self.shown))
        ids = {sid for _, sids, _ in groups for sid in sids}
        series = self.series(ids)
        libraries = {lib.id: lib.name for lib in self.db.query(Library)}
        book_counts = _book_counts(self.db, ids)
        out = []
        for key, sids, shared in groups:
            members = sorted((series[sid] for sid in sids), key=lambda s: self.name(s.id).lower())
            out.append({"key": key, "series": self.name(members[0].id),
                        "shared": [{"field": f, "id": v} for f, v in shared],
                        "members": [{"series_id": s.id, "series": self.name(s.id), "folder": s.folder_path,
                                     "library": libraries.get(s.library_id, ""), "match_status": s.match_status,
                                     "books": book_counts.get(s.id, 0)} for s in members]})
        return out


class AttentionLists(Plugin):
    id = "attention-lists"
    name = "Needs attention"
    category = ["library", "system"]
    version = "1.0"
    description = ("Settings → Needs attention: files that can't be opened, missing metadata, series matched "
                   "to the same entry, duplicates, missing chapters, books no reader shows. Built in.")
    permissions = ["Lists problems in your library in Settings; connects to nothing"]
    provides = {"attention:list": 1}
    # alert: counts towards the badge on Settings (missing chapters and unsupported books don't)
    attention_lists = [
        {"key": "unreadable", "label": "Errors", "help": "These files could not be opened. The message says why.",
         "alert": True, "view": "files"},
        {"key": "matches", "label": "Missing metadata",
         "help": "No confident match was found online. Pick the right one, or open Fix match.", "alert": True,
         "view": "matches"},
        {"key": "same", "label": "Same match",
         "help": "These series are matched to the same entry online. Often one folder is split in two, or one "
                 "match is wrong.", "alert": True, "view": "same"},
        {"key": "duplicates", "label": "Duplicates", "help": "Two or more files with the same chapter number. "
         "Both are kept.", "alert": True, "view": "duplicates"},
        {"key": "gaps", "label": "Missing chapters", "help": "Gaps in the chapter numbers. Often fine (volumes "
         "may cover them).", "view": "gaps"},
        {"key": "unsupported", "label": "Not supported", "help": "Light novels (text EPUBs) are listed here "
         "until a reader for text books is added in Plugins.", "view": "files"},
    ]

    def attention_rows(self, ctx, key, request):
        rows = Rows(ctx.db, visible_series(ctx.db, request))
        if key in ("unreadable", "unsupported"):
            return rows.files(key)
        return getattr(rows, key)()

    def attention_count(self, ctx, key, request, ignored):
        """The badge's share, cheaper than the rows: only ids and keys are loaded."""
        db, shown = ctx.db, visible_series(ctx.db, request)
        if key == "unreadable":
            live = _live(db, shown)
            return sum(1 for bid, fmt, err, sid in db.query(Book.id, Book.format, Book.error, Book.series_id)
                       .filter(Book.error.is_not(None), Book.missing_since.is_(None))
                       if sid in live and fmt in SUPPORTED and not err.startswith(TEXT_EPUB)
                       and f"err:{bid}" not in ignored)
        if key == "duplicates":
            return sum(1 for sid, books in _flagged(db, _live(db, shown)).items()
                       for part, _ in duplicate_groups(books) if f"dup:{sid}:{part}" not in ignored)
        if key == "matches":
            return db.query(Series).filter(Series.match_status == "failed", Series.missing_since.is_(None),
                                           *shown).count()
        if key == "same":
            return sum(1 for k, _, _ in same_match_groups(_series_ids(db, shown)) if k not in ignored)
        return 0
