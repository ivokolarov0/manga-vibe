"""The plugin's API, at /api/plugins/anilist-recommendations/api/...: a series' recommendations, ready
to show (/series/<id>), and the covers saved on the server (/covers/<AniList id>?series=<id>)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.plugins import Context
from app.plugins.sdk import Series, display_title, get_setting, poster_version, title_lang, visible_series
from app.plugins.web import plugin_ctx

from . import core

router = APIRouter()
COVER_CACHE = "public, max-age=2592000"  # a month: a cover's file never changes under its name


def _series(ctx, request, series_id):
    """The series, if the person asking may see it (e.g. not in a locked library)."""
    s = (ctx.db.query(Series).filter(Series.id == series_id, Series.missing_since.is_(None),
                                     *visible_series(ctx.db, request)).one_or_none())
    if s is None:
        raise HTTPException(404, "Series not found")
    return s


def _number(ctx, key, fallback):
    try:
        return int(ctx.setting(key))
    except (TypeError, ValueError):
        return fallback


@router.get("/series/{series_id}")
def for_series(series_id: int, request: Request, ctx: Context = Depends(plugin_ctx)):
    """The series' recommendations: {items: [{id, title, format, country, year, cover, href, local}]}. Series in
    the library link to their page here (local, with their own poster), the others to AniList. No
    AniList match: no items."""
    s = _series(ctx, request, series_id)
    if not s.anilist_id:
        return {"items": []}
    adult = get_setting(ctx.db, "include_adult", False)
    items = [i for i in core.recommendations(ctx, s, _number(ctx, "days", 7))
             if i["rating"] > 0 and (adult or not i["adult"]) and i["id"] != s.anilist_id]
    items = items[:_number(ctx, "count", 10)]
    ids = [i["id"] for i in items]
    have = {}
    for x in (ctx.db.query(Series).filter(Series.anilist_id.in_(ids), Series.missing_since.is_(None),
                                          *visible_series(ctx.db, request)).order_by(Series.id) if ids else []):
        have.setdefault(x.anilist_id, x)  # two folders of one series: the first
    lang, hotlink = title_lang(ctx.db), ctx.setting("covers") == "hotlink"
    out = []
    for i in items:
        row = {"id": i["id"], "format": i["format"], "country": i.get("country"), "year": i["year"]}
        local = have.get(i["id"])
        if local:
            row.update(title=display_title(local, lang), href=f"#/series/{local.id}", local=True,
                       cover=f"/api/series/{local.id}/poster?size=sm&v={poster_version(local)}")
        else:
            cover = i["cover"] if hotlink else f"/api/plugins/{ctx.plugin.id}/api/covers/{i['id']}?series={s.id}"
            row.update(title=core.title(i, lang), href=i["url"], local=False, cover=cover if i["cover"] else None)
        out.append(row)
    return {"items": out}


@router.get("/covers/{anilist_id}")
def cover(anilist_id: int, series: int, request: Request, ctx: Context = Depends(plugin_ctx)):
    """A recommended series' cover, from AniList the first time, then from the server's own copy."""
    path = core.saved_cover(ctx, anilist_id)
    if path is None:
        s = _series(ctx, request, series)  # only covers of what that series recommends
        saved = ctx.get_series_data(s, core.KEY) or {}
        item = next((i for i in saved.get("items") or [] if i["id"] == anilist_id), None)
        path = core.save_cover(ctx, anilist_id, item["cover"]) if item and item.get("cover") else None
    if path is None:
        raise HTTPException(404, "No cover")
    return FileResponse(path, media_type=core.MEDIA_TYPES[path.suffix], headers={"Cache-Control": COVER_CACHE})
