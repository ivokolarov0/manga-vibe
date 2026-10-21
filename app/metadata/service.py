"""Apply provider metadata to a series: matching, manual match, unmatch, refresh, posters."""
import logging
import time
from datetime import datetime, timezone

from app import config
from app.db import get_setting
from app.metadata import http, matcher
from app.models import Link, SeriesTag, Tag
from app.plugins import Context, manager as plugins
from app.posters import PosterError, save_poster_bytes

log = logging.getLogger("mangavibe.metadata")

PRIMARY_ORDER = ["anilist", "mangaupdates"]  # other provider plugins are tried after these
SERIES_FIELDS = ["title_english", "title_romaji", "title_native", "synonyms", "description",
                 "publication_status", "start_year", "format", "country_of_origin", "chapters_total",
                 "volumes_total", "average_score", "authors", "banner_url"]
ID_FIELDS = ["anilist_id", "mal_id", "mangaupdates_id", "mangadex_id"]


DOWN_MINUTES = 15
_down_until: dict[str, float] = {}  # provider id -> time.monotonic() when to try it again


def _is_down(pid):
    return _down_until.get(pid, 0) > time.monotonic()


def _mark_down(p, err):
    if not _is_down(p.id):
        log.warning("%s is not reachable (%s); skipping it for %d minutes", p.name, err, DOWN_MINUTES)
    _down_until[p.id] = time.monotonic() + DOWN_MINUTES * 60


class AllProvidersDown(Exception):
    """No provider could be reached; try again later instead of marking the series failed."""


def now():
    return datetime.now(timezone.utc)


def provider(pid):
    return next((p for p in plugins.providers() if p.id == pid), None)


def ordered_providers():
    ps = plugins.providers()
    return sorted(ps, key=lambda p: PRIMARY_ORDER.index(p.id) if p.id in PRIMARY_ORDER else 99)


# ---------------------------------------------------------------- applying

def apply(db, series, data, provider_id_name, provider_item_id, status="manual", confidence=None,
          download_poster=True):
    locked = set(series.locked_fields or [])
    for f in SERIES_FIELDS:
        if f not in locked and f in data:
            setattr(series, f, data[f])
    for f, v in (data.get("ids") or {}).items():
        if f in ID_FIELDS and v is not None:
            setattr(series, f, str(v) if f in ("mangaupdates_id", "mangadex_id") else int(v))
    if "tags" not in locked:
        set_tags(db, series, data.get("genres") or [], data.get("tags") or [])
    if "links" not in locked:
        set_links(db, series, data.get("links") or [])
    series.metadata_source = {"provider": provider_id_name, "id": str(provider_item_id)}
    series.match_status = status
    series.match_confidence = confidence
    series.match_candidates = None
    series.matched_at = now()
    if series.title_romaji or series.title_english:
        series.sort_title = (series.title_romaji or series.title_english).lower()
    if download_poster and data.get("cover_url") and "poster" not in locked \
            and series.poster_source != "upload":
        save_poster(series, data["cover_url"])


def add_links(db, series, links):
    have = {(l.site, l.url) for l in db.query(Link).filter_by(series_id=series.id)}
    for l in links:
        if (l["site"], l["url"]) not in have:
            db.add(Link(series_id=series.id, site=l["site"], url=l["url"]))


def set_links(db, series, links):
    db.query(Link).filter_by(series_id=series.id).delete()
    db.flush()
    add_links(db, series, links)


def set_tags(db, series, genres, tags):
    """Replace provider genres/tags; the owner's custom tags are kept."""
    custom = {t.id for t in db.query(Tag).filter_by(kind="custom")}
    for st in db.query(SeriesTag).filter_by(series_id=series.id):
        if st.tag_id not in custom:
            db.delete(st)
    db.flush()
    seen = set()
    for kind, items in (("genre", [{"name": g} for g in genres]), ("tag", tags)):
        for item in items:
            name = item["name"].strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            tag = db.query(Tag).filter(Tag.name == name).one_or_none()
            if tag is None:
                tag = Tag(name=name, kind=kind)
                db.add(tag)
                db.flush()
            if tag.id in custom:
                continue
            db.add(SeriesTag(series_id=series.id, tag_id=tag.id, rank=item.get("rank"),
                             is_spoiler=bool(item.get("spoiler"))))


def save_poster(series, url):
    """Download a provider cover; it goes through the same checks as an uploaded poster."""
    try:
        save_poster_bytes(series, http.get_bytes(url), "provider")
    except (http.ProviderError, PosterError) as e:
        log.warning("Could not use the cover for %s: %s", series.folder_name, e)


def clear_metadata(db, series):
    locked = set(series.locked_fields or [])
    for f in SERIES_FIELDS:
        if f not in locked:
            setattr(series, f, [] if f in ("synonyms", "authors") else None)
    if "publication_status" not in locked:
        series.publication_status = "UNKNOWN"
    for f in ID_FIELDS:
        setattr(series, f, None)
    if "tags" not in locked:
        set_tags(db, series, [], [])
    if "links" not in locked:
        set_links(db, series, [])
    if series.poster_source == "provider":
        for p in (config.COVERS_DIR / f"{series.id}.jpg", config.COVERS_DIR / f"{series.id}_sm.webp"):
            p.unlink(missing_ok=True)
        series.poster_path, series.poster_source = None, "first_page"
    series.sort_title = series.folder_name.lower()
    series.metadata_source = None
    series.match_confidence = None
    series.matched_at = now()  # changes the poster URL so browsers reload it


# ---------------------------------------------------------------- matching

def _scored(candidates, query, year):
    out = [(matcher.score(query, [c.title, *c.other_titles], year, c.year, c.format), c)
           for c in candidates]
    return sorted(out, key=lambda x: -x[0])


def _candidate_dict(pid, sc, c):
    return {"provider": pid, "provider_id": c.provider_id, "title": c.title,
            "other_titles": c.other_titles[:4], "year": c.year, "format": c.format,
            "status": c.status, "url": c.url, "cover_url": c.cover_url, "score": sc}


def auto_match(db, series):
    """Search providers in order; accept a confident result, otherwise mark 'failed' with candidates.
    Raises AllProvidersDown when no provider answered."""
    query, year = matcher.clean_query(series.folder_name)
    threshold = int(get_setting(db, "match_threshold", 90))
    saved, reached, down = [], False, []
    for p in ordered_providers():
        if _is_down(p.id):
            down.append(p.id)
            continue
        try:
            cands = p.search(Context(p, db), query, year)
        except http.ProviderError as e:
            _mark_down(p, e)
            down.append(p.id)
            continue
        reached = True
        scored = _scored(cands, query, year)
        best = matcher.pick(scored, threshold)
        if best:
            data = best.data or p.fetch(Context(p, db), best.provider_id)
            apply(db, series, data, p.id, best.provider_id, "auto", scored[0][0])
            if PRIMARY_ORDER[0] in down:
                # matched with a fallback only because AniList was down: try AniList again later
                series.metadata_source = {**series.metadata_source, "retry_primary": True}
            if p.id != "mangaupdates":
                add_secondary(db, series)
            log.info("Matched %s -> %s (%s, %d%%)", series.folder_name, best.title, p.name, scored[0][0])
            return True
        if not saved:
            saved = [_candidate_dict(p.id, sc, c) for sc, c in scored[:5]]
    if not reached:
        raise AllProvidersDown()
    series.match_status = "failed"
    series.match_candidates = saved
    series.match_confidence = saved[0]["score"] if saved else 0
    log.info("No confident match for %s (best %s%%)", series.folder_name, series.match_confidence)
    return False


def add_secondary(db, series):
    """After an AniList match, add the MangaUpdates link/ID if it's a confident match."""
    mu = provider("mangaupdates")
    if mu is None or series.mangaupdates_id:
        return
    titles = [t for t in (series.title_romaji, series.title_english, series.title_native,
                          *(series.synonyms or [])) if t]
    if not titles:
        return
    try:
        cands = mu.search(Context(mu, db), titles[0], series.start_year)
    except http.ProviderError as e:
        log.info("MangaUpdates lookup skipped for %s: %s", series.folder_name, e)
        return
    scored = sorted(((max(matcher.score(t, [c.title, *c.other_titles], series.start_year, c.year)
                          for t in titles[:3]), c) for c in cands), key=lambda x: -x[0])
    best = matcher.pick(scored, 90, 5)
    if best:
        series.mangaupdates_id = best.provider_id
        add_links(db, series, [{"site": "MangaUpdates", "url": best.url}] if best.url else [])


def search(db, series, provider_id_name, query):
    p = provider(provider_id_name)
    if p is None:
        raise ValueError(f"Unknown provider {provider_id_name}")
    _, year = matcher.clean_query(series.folder_name)
    return [_candidate_dict(p.id, sc, c) for sc, c in _scored(p.search(Context(p, db), query, year), query, year)]


def manual_match(db, series, provider_id_name=None, item_id=None, url=None):
    if url:
        for p in plugins.providers():
            found = getattr(p, "id_from_url", lambda u: None)(url)
            if found:
                provider_id_name, item_id = p.id, found
                break
        else:
            raise ValueError("Not an AniList or MangaUpdates link")
    p = provider(provider_id_name)
    if p is None:
        raise ValueError(f"Unknown provider {provider_id_name}")
    data = p.fetch(Context(p, db), item_id)
    clear_metadata(db, series)
    apply(db, series, data, p.id, item_id, "manual", 100)
    if p.id != "mangaupdates":
        add_secondary(db, series)


def unmatch(db, series, ignore=False):
    clear_metadata(db, series)
    series.match_candidates = None
    series.match_status = "ignored" if ignore else "unmatched"


def refresh(db, series):
    src = series.metadata_source
    if not src:
        raise ValueError("This series has no metadata source yet; use Fix match.")
    p = provider(src["provider"])
    if p is None:
        raise ValueError(f"Provider {src['provider']} is not installed or disabled")
    apply(db, series, p.fetch(Context(p, db), src["id"]), p.id, src["id"],
          series.match_status, series.match_confidence)
    if p.id != "mangaupdates":
        add_secondary(db, series)


def upgrade_to_primary(db, series):
    """Series matched via a fallback while AniList was down: switch to AniList if it now matches."""
    p = provider(PRIMARY_ORDER[0])
    src = series.metadata_source or {}
    if p is None or not src.get("retry_primary") or series.match_status != "auto":
        return False
    query, year = matcher.clean_query(series.folder_name)
    scored = _scored(p.search(Context(p, db), query, year), query, year)  # raises ProviderError if still down
    best = matcher.pick(scored, int(get_setting(db, "match_threshold", 90)))
    if best is None:
        series.metadata_source = {k: v for k, v in src.items() if k != "retry_primary"}
        return False
    clear_metadata(db, series)
    apply(db, series, best.data or p.fetch(Context(p, db), best.provider_id), p.id, best.provider_id,
          "auto", scored[0][0])
    add_secondary(db, series)  # looks up the MangaUpdates link again
    log.info("Upgraded %s to %s metadata", series.folder_name, p.name)
    return True
