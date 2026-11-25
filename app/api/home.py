"""The Home page (issue #291): rows of what's new, each a carousel of the newest 10.

The app's own rows are Continue reading, Recently added and Recently updated (CORE). Plugins add rows of their
own with `home_sections` and home_section() (Plugin API 30), e.g. the libraries plugin's Libraries row. The
page (the view:home plugin's, built in: app/plugins/builtin/home_page) asks GET /api/home once for every row
it shows, not once per row.

Home is always the first page (#/; the library is #/library). Each person picks, in Settings → Display, the
rows' order and which are hidden (`home_layout`: {"order": [ids], "hidden": [ids]}), a personal setting.
A row's id is its key for the app's own, "<plugin id>:<key>" for a plugin's.
"""
import logging
import traceback

from fastapi import APIRouter, Depends, Request

from app.api import filters
from app.api.common import latest_labels, load_states, series_card, series_counts, title_lang
from app.api.filters import visible_series
from app.db import get_db
from app.i18n import N_, t
from app.models import Series
from app.plugins import Context, HomeSection, implements, manager as plugins

router = APIRouter(prefix="/api")
log = logging.getLogger("mangavibe.plugins")

LIMIT = 10  # cards per row

CORE = [HomeSection("continue", N_("Continue reading"), see_all="#/continue", order=20),
        HomeSection("added", N_("Recently added"), see_all="#/library?sort=added", order=30),
        HomeSection("updated", N_("Recently updated"), see_all="#/library?sort=updated", order=40)]
KINDS = ("series", "tiles")
SHAPES = ("poster", "wide")  # how a tiles row's cards look: as tall as a series card, or 16:9 with the title on it
TILE_KEYS = ("id", "title", "subtitle", "image", "href", "icon")


def all_sections() -> list[tuple[str, object, HomeSection]]:
    """[(id, plugin or None, section)] of every row there is, in the default order: by each row's `order`
    (the libraries first, then the app's own, then the plugins' that say nothing)."""
    out = [(s.key, None, s) for s in CORE]
    seen = {s.key for s in CORE}
    for p in plugins.enabled():
        for s in getattr(p, "home_sections", None) or []:
            sid = f"{p.id}:{getattr(s, 'key', '')}"
            # a row that's made wrong (or twice) is left out and logged; it never takes Home down
            if not (isinstance(s, HomeSection) and s.kind in KINDS and s.key and isinstance(s.key, str)
                    and isinstance(s.title, str) and s.title and isinstance(s.see_all, str)
                    and s.shape in SHAPES and isinstance(s.order, int)) or sid in seen:
                log.error("Plugin %s: its Home row %r is left out (made wrong, or there twice)", p.id, s)
                continue
            seen.add(sid)
            out.append((sid, p, s))
    return sorted(out, key=lambda x: x[2].order)  # stable: the same order keeps the plugins' order


def clean_layout(value) -> dict:
    """A saved home_layout as {"order": [ids], "hidden": [ids]}; anything else in it is dropped."""
    value = value if isinstance(value, dict) else {}

    def ids(key):
        found = value.get(key)
        return list(dict.fromkeys(x for x in found if isinstance(x, str) and 0 < len(x) <= 120))[:200] \
            if isinstance(found, list) else []
    return {"order": ids("order"), "hidden": ids("hidden")}


def ordered(db) -> list[tuple[str, object, HomeSection, bool]]:
    """[(id, plugin, section, hidden)] in this person's order: the ones they put in order first, then the
    rest (e.g. a plugin's switched on since) in the default order."""
    from app.api.system import personal_setting
    layout = clean_layout(personal_setting(db, "home_layout"))
    rank = {sid: i for i, sid in enumerate(layout["order"])}
    found = sorted(enumerate(all_sections()), key=lambda x: (rank.get(x[1][0], len(rank)), x[0]))
    hidden = set(layout["hidden"])
    return [(sid, p, s, sid in hidden) for _, (sid, p, s) in found]


def section_info(sid, plugin, s) -> dict:
    """What the page and Settings get of a row. layout: how its series cards are drawn (the card view's
    "continue", with the book and page to go on with, or "library")."""
    return {"id": sid, "plugin": plugin.id if plugin else None, "title": t(s.title), "kind": s.kind,
            "layout": "continue" if sid == "continue" else "library", "shape": s.shape,
            "see_all": s.see_all if s.see_all.startswith("#/") else ""}


def series_cards(db, request, rows) -> list[dict]:
    """The library's cards for these series, in their order, leaving out what this request may not see."""
    ids = list(dict.fromkeys(s.id for s in rows if s is not None and getattr(s, "id", None)))[:LIMIT]
    if not ids:
        return []
    found = {s.id: s for s in db.query(Series).filter(Series.id.in_(ids), Series.missing_since.is_(None),
                                                      *visible_series(db, request))}
    page = [found[i] for i in ids if i in found]
    load_states(db, page)
    counts, lang = series_counts(db, [s.id for s in page]), title_lang(db)
    have_of = filters.collections(db, [s for s in page if s.publication_status == "FINISHED"])
    latest_of = latest_labels(db, [s.id for s in page])
    from app.api.series import _utc_seconds
    out = []
    for s in page:
        card = series_card(s, counts[s.id], lang)
        card["incomplete"] = have_of.get(s.id, {}).get("state") == "incomplete"
        card["latest"] = latest_of.get(s.id)
        card["updated_at"] = _utc_seconds(s.updated_at)
        out.append(card)
    return out


def core_items(db, request, key) -> list[dict]:
    if key == "continue":
        from app.api.series import continue_cards
        return continue_cards(db, request, LIMIT)
    q = db.query(Series).filter(Series.missing_since.is_(None), *visible_series(db, request))
    if key == "added":
        q = q.order_by(Series.added_at.desc(), Series.id.desc())
    else:  # updated: new books after it was added (the scanner's books_added_at), the last first
        q = q.filter(Series.books_added_at.is_not(None)).order_by(Series.books_added_at.desc(), Series.id.desc())
    return series_cards(db, request, q.limit(LIMIT).all())


def tiles(found) -> list[dict]:
    """A plugin's tiles, with only the keys the page draws, as text. One without a title or an id (the page
    tells the tiles apart by it), or with an id seen before, is left out."""
    out, ids = [], set()
    for x in list(found or []):
        if not isinstance(x, dict) or not x.get("title") or x.get("id") in (None, "") or str(x["id"]) in ids:
            continue
        ids.add(str(x["id"]))
        out.append({k: "" if x.get(k) is None else str(x[k]) for k in TILE_KEYS})
        if len(out) >= LIMIT:
            break
    return out


def items_of(db, request, plugin, s) -> list[dict]:
    """The row's cards. A plugin's that fails is logged and its row left out: Home still shows."""
    if plugin is None:
        return core_items(db, request, s.key)
    if not implements(plugin, "home_section"):
        return []
    try:
        found = plugin.home_section(Context(plugin, db), request, s.key, LIMIT)
        return series_cards(db, request, found or []) if s.kind == "series" else tiles(found)
    except Exception:
        db.rollback()
        log.error("Plugin %s failed in home_section(%r):\n%s", plugin.id, s.key, traceback.format_exc())
        return []


@router.get("/home")
def home(request: Request, db=Depends(get_db)):
    """Every row this person shows on Home, in their order, with up to LIMIT cards each, of the series the
    request may see (e.g. not a locked library's). Rows with nothing in them are left out. all_hidden: the
    person hid every row (Settings → Display), so an empty Home says so instead of "no series"."""
    out, rows = [], ordered(db)
    for sid, plugin, s, hidden in rows:
        if hidden:
            continue
        items = items_of(db, request, plugin, s)
        if items:
            out.append({**section_info(sid, plugin, s), "items": items})
    return {"sections": out, "all_hidden": bool(rows) and all(hidden for *_, hidden in rows)}


@router.get("/home/sections")
def home_sections(db=Depends(get_db)):
    """Every row there is, in this person's order, and whether they hid it (Settings → Display)."""
    return [{**section_info(sid, plugin, s), "hidden": hidden} for sid, plugin, s, hidden in ordered(db)]
