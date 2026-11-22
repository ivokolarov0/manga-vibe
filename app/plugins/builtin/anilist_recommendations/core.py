"""AniList's recommendations for a series, asked for once and kept with the series (ctx.set_series_data,
key "recs") until they are older than the plugin's setting "days". Covers saved on the server go to
ctx.cache_dir / "covers", one file per AniList id."""
import threading
import time
from urllib.parse import urlparse

URL = "https://graphql.anilist.co"
QUERY = """query ($id: Int) { Media(id: $id, type: MANGA) {
  recommendations(sort: [RATING_DESC, ID], perPage: 25) { nodes { rating mediaRecommendation {
    id type isAdult siteUrl format countryOfOrigin startDate { year } title { romaji english native } coverImage { large }
  } } } } }"""
KEY = "recs"
RETRY_AFTER_FAILURE = 15 * 60  # AniList didn't answer: ask again after this many seconds, not on every view
TITLE_ORDER = {"english": ("english", "romaji", "native"), "romaji": ("romaji", "english", "native"),
               "native": ("native", "romaji", "english")}
# the start of each picture format AniList serves covers in, and the file ending it's saved with
IMAGE_TYPES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"GIF8": ".gif", b"RIFF": ".webp"}
MEDIA_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(series_id):
    with _locks_guard:
        return _locks.setdefault(series_id, threading.Lock())


def ask_anilist(ctx, anilist_id) -> list[dict]:
    """The recommendations AniList has for this manga, best rated first. [] when AniList doesn't know it.
    Raises ctx.http.ProviderError when it can't be reached."""
    try:
        r = ctx.http.post_json(URL, {"query": QUERY, "variables": {"id": int(anilist_id)}}, retries=1)
    except ctx.http.ProviderError as e:
        if e.status == 404:  # no such manga (any more)
            return []
        raise
    media = (r.get("data") or {}).get("Media")
    if r.get("errors") and not media:
        raise ctx.http.ProviderError("AniList: " + "; ".join(e.get("message", "?") for e in r["errors"]))
    items = []
    for node in ((media or {}).get("recommendations") or {}).get("nodes") or []:
        m = node.get("mediaRecommendation")
        if not m or m.get("type") not in (None, "MANGA"):
            continue
        items.append({
            "id": m["id"], "rating": node.get("rating") or 0, "adult": bool(m.get("isAdult")),
            "title": m.get("title") or {}, "format": m.get("format"), "country": m.get("countryOfOrigin"),
            "year": (m.get("startDate") or {}).get("year"),
            "cover": (m.get("coverImage") or {}).get("large"), "url": m.get("siteUrl"),
        })
    return items


def recommendations(ctx, series, days) -> list[dict]:
    """The series' recommendations: the saved ones while they are fresh, else AniList's (saved for `days`).
    When AniList can't be reached, the saved ones (or none) until it's tried again a little later."""
    def fresh(saved):
        return saved and saved.get("for") == series.anilist_id and saved.get("until", 0) > time.time()

    saved = ctx.get_series_data(series, KEY)
    if fresh(saved):
        return saved["items"]
    with _lock(series.id):  # two views of one series at once ask AniList once
        ctx.db.expire_all()
        saved = ctx.get_series_data(series, KEY)
        if fresh(saved):
            return saved["items"]
        old = saved["items"] if saved and saved.get("for") == series.anilist_id else []
        try:
            items, until = ask_anilist(ctx, series.anilist_id), time.time() + days * 86400
        except ctx.http.ProviderError as e:
            ctx.log.warning("No recommendations for %s: %s", series.folder_name, e)
            items, until = old, time.time() + RETRY_AFTER_FAILURE
        ctx.set_series_data(series, KEY, {"for": series.anilist_id, "until": until, "items": items})
        ctx.db.commit()
        return items


def title(item, lang):
    t = item.get("title") or {}
    return next((t[k] for k in TITLE_ORDER.get(lang, TITLE_ORDER["english"]) if t.get(k)), "?")


# --- covers saved on the server ------------------------------------------------------------------
def covers_dir(ctx):
    path = ctx.cache_dir / "covers"
    path.mkdir(exist_ok=True)
    return path


def saved_cover(ctx, anilist_id):
    return next((p for p in covers_dir(ctx).glob(f"{int(anilist_id)}.*") if p.suffix in MEDIA_TYPES), None)


def save_cover(ctx, anilist_id, url):
    """Download a cover from AniList's image server and keep it; its path, or None."""
    host = urlparse(url or "").hostname or ""
    if not url.startswith("https://") or not (host == "anilist.co" or host.endswith(".anilist.co")):
        return None  # only AniList's own pictures
    try:
        data = ctx.http.get_bytes(url, retries=1)
    except ctx.http.ProviderError as e:
        ctx.log.warning("Cover %s: %s", url, e)
        return None
    ext = next((x for start, x in IMAGE_TYPES.items() if data.startswith(start)), None)
    if not ext:
        return None
    path = covers_dir(ctx) / f"{int(anilist_id)}{ext}"
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)
    return path


def cache_size(ctx):
    """How many covers are saved, and how many bytes they take."""
    files = [p for p in covers_dir(ctx).iterdir() if p.suffix in MEDIA_TYPES]
    return len(files), sum(p.stat().st_size for p in files)
