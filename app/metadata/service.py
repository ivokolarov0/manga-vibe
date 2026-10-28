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

def prepare(db, series, data, provider_id_name, fresh=False):
    """The network part of applying metadata: the cover, the MangaUpdates lookup and editions.
    Call it before anything is written, then pass the result to apply(). SQLite lets one
    connection write at a time, so waiting on the network after the first write would hold up
    everything else (a manual match, saving reading progress) until the downloads finish.
    fresh: the series' metadata is cleared before apply() (manual match, upgrade)."""
    locked = set(series.locked_fields or [])
    cover = None
    if data.get("cover_url") and "poster" not in locked and series.poster_source != "upload":
        try:
            cover = http.get_bytes(data["cover_url"])
        except http.ProviderError as e:
            log.warning("Could not use the cover for %s: %s", series.folder_name, e)
    secondary = None
    has_mu = (data.get("ids") or {}).get("mangaupdates_id") or (not fresh and series.mangaupdates_id)
    if provider_id_name != "mangaupdates" and not has_mu:
        value = lambda f: getattr(series, f) if f in locked else data.get(f)  # as apply() leaves it
        titles = [t for t in (value("title_romaji"), value("title_english"), value("title_native"),
                              *(value("synonyms") or [])) if t]
        secondary = find_secondary(db, series, titles, value("start_year"))
    mu_id = ((data.get("ids") or {}).get("mangaupdates_id") or (secondary and secondary.provider_id)
             or (None if fresh else series.mangaupdates_id))
    found = data.get("editions") if provider_id_name == "mangaupdates" else fetch_editions(db, series, mu_id)
    return {"cover": cover, "secondary": secondary, "editions": found}


def fetch_editions(db, series, mu_id):
    """The editions MangaUpdates lists for the series (regular, kanzenban, omnibus...), or None
    when there's no MangaUpdates entry or it can't be reached (the ones found before are kept)."""
    mu = provider("mangaupdates")
    if mu is None or not mu_id:
        return None
    try:
        return mu.fetch(Context(mu, db), mu_id).get("editions")
    except http.ProviderError as e:
        log.info("MangaUpdates editions skipped for %s: %s", series.folder_name, e)
        return None


def apply(db, series, data, provider_id_name, provider_item_id, status="manual", confidence=None,
          extras=None):
    """Write the metadata. extras: what prepare() downloaded (cover, MangaUpdates match)."""
    extras = extras or {}
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
        keep = []  # a refresh keeps the MangaUpdates link found earlier (it isn't looked up again)
        if series.mangaupdates_id and provider_id_name != "mangaupdates":
            keep = [{"site": l.site, "url": l.url}
                    for l in db.query(Link).filter_by(series_id=series.id, site="MangaUpdates")]
        set_links(db, series, (data.get("links") or []) + keep)
    series.metadata_source = {"provider": provider_id_name, "id": str(provider_item_id)}
    series.match_status = status
    series.match_confidence = confidence
    series.match_candidates = None
    series.matched_at = now()
    if series.title_romaji or series.title_english:
        series.sort_title = (series.title_romaji or series.title_english).lower()
    if extras.get("cover"):
        try:
            save_poster_bytes(series, extras["cover"], "provider")
        except PosterError as e:
            log.warning("Could not use the cover for %s: %s", series.folder_name, e)
    if extras.get("editions") is not None:
        series.editions = extras["editions"]
    mu = extras.get("secondary")
    if mu:
        series.mangaupdates_id = mu.provider_id
        add_links(db, series, [{"site": "MangaUpdates", "url": mu.url}] if mu.url else [])


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
    custom = {tid for (tid,) in db.query(Tag.id).filter_by(kind="custom")}
    old = db.query(SeriesTag).filter(SeriesTag.series_id == series.id)
    if custom:
        old = old.filter(SeriesTag.tag_id.not_in(custom))
    old.delete()
    db.flush()
    items = [("genre", {"name": g}) for g in genres] + [("tag", t) for t in tags]
    names = {item["name"].strip() for _, item in items} - {""}
    existing = {t.name: t for t in db.query(Tag).filter(Tag.name.in_(names))} if names else {}
    seen = set()
    for kind, item in items:
        name = item["name"].strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        tag = existing.get(name)
        if tag is None:
            tag = Tag(name=name, kind=kind)
            db.add(tag)
            db.flush()
        if tag.id in custom:
            continue
        db.add(SeriesTag(series_id=series.id, tag_id=tag.id, rank=item.get("rank"),
                         is_spoiler=bool(item.get("spoiler"))))


def clear_metadata(db, series):
    locked = set(series.locked_fields or [])
    for f in SERIES_FIELDS:
        if f not in locked:
            setattr(series, f, [] if f in ("synonyms", "authors") else None)
    if "publication_status" not in locked:
        series.publication_status = "UNKNOWN"
    for f in ID_FIELDS:
        setattr(series, f, None)
    series.editions = None  # another series' editions; the owner's pick (edition_volumes) stays
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
            extras = prepare(db, series, data, p.id)
            apply(db, series, data, p.id, best.provider_id, "auto", scored[0][0], extras)
            if PRIMARY_ORDER[0] in down:
                # matched with a fallback only because AniList was down: try AniList again later
                series.metadata_source = {**series.metadata_source, "retry_primary": True}
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


def find_secondary(db, series, titles, year):
    """After an AniList match: the MangaUpdates entry for the link/ID, if it's a confident match."""
    mu = provider("mangaupdates")
    if mu is None or not titles:
        return None
    try:
        cands = mu.search(Context(mu, db), titles[0], year)
    except http.ProviderError as e:
        log.info("MangaUpdates lookup skipped for %s: %s", series.folder_name, e)
        return None
    scored = sorted(((max(matcher.score(t, [c.title, *c.other_titles], year, c.year)
                          for t in titles[:3]), c) for c in cands), key=lambda x: -x[0])
    return matcher.pick(scored, 90, 5)


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
    extras = prepare(db, series, data, p.id, fresh=True)
    clear_metadata(db, series)
    apply(db, series, data, p.id, item_id, "manual", 100, extras)


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
    data = p.fetch(Context(p, db), src["id"])
    extras = prepare(db, series, data, p.id)
    apply(db, series, data, p.id, src["id"], series.match_status, series.match_confidence, extras)


def fill_editions(db, series):
    """A series matched before editions were stored: look them up once (a background job)."""
    if series.editions is not None or not series.mangaupdates_id:
        return
    found = fetch_editions(db, series, series.mangaupdates_id)
    if found is None:
        raise http.ProviderError("MangaUpdates is not reachable")  # tried again at the next start
    series.editions = found


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
    data = best.data or p.fetch(Context(p, db), best.provider_id)
    extras = prepare(db, series, data, p.id, fresh=True)  # looks up the MangaUpdates link again
    clear_metadata(db, series)
    apply(db, series, data, p.id, best.provider_id, "auto", scored[0][0], extras)
    log.info("Upgraded %s to %s metadata", series.folder_name, p.name)
    return True
