"""MangaUpdates metadata provider. Adds its link/ID to AniList matches, and is the
fallback for titles not on AniList (or while AniList is down)."""
import re

from app.metadata import http
from app.metadata.text import clean_html, has_script
from app.plugins import Candidate, Plugin

API = "https://api.mangaupdates.com/v1"
URL_ID = re.compile(r"mangaupdates\.com/series/([0-9a-z]+)", re.I)
# MangaUpdates type -> (AniList-style format, country)
TYPES = {"Manga": ("MANGA", "JP"), "Manhwa": ("MANGA", "KR"), "Manhua": ("MANGA", "CN"),
         "Novel": ("NOVEL", None), "Doujinshi": ("MANGA", "JP"), "OEL": ("MANGA", None)}
NATIVE_SCRIPT = {"JP": "ja", "KR": "ko", "CN": "zh"}
MAX_TAGS = 20


def status_of(s):
    text = (s.get("status") or "").lower()
    if "hiatus" in text:
        return "HIATUS"
    if "discontinued" in text or "cancelled" in text or "canceled" in text:
        return "CANCELLED"
    if s.get("completed") or "complete" in text:
        return "FINISHED"
    if "ongoing" in text:
        return "RELEASING"
    return "UNKNOWN"


def year_of(s):
    m = re.match(r"\d{4}", str(s.get("year") or ""))
    return int(m.group()) if m else None


def normalize(s):
    fmt, country = TYPES.get(s.get("type"), (None, None))
    assoc = [a["title"] for a in s.get("associated") or [] if a.get("title")]
    script = NATIVE_SCRIPT.get(country)
    native = next((a for a in assoc if script and has_script(a, script)), None)
    authors, by_name = [], {}
    for a in s.get("authors") or []:
        if a["name"] in by_name:
            by_name[a["name"]]["role"] += ", " + a.get("type", "")
        else:
            by_name[a["name"]] = {"name": a["name"], "role": a.get("type", "")}
            authors.append(by_name[a["name"]])
    cats = sorted(s.get("categories") or [], key=lambda c: -(c.get("votes") or 0))[:MAX_TAGS]
    rating = s.get("bayesian_rating")
    image = ((s.get("image") or {}).get("url") or {}).get("original")
    return {
        "title_english": None, "title_romaji": s.get("title"), "title_native": native,
        "synonyms": [a for a in assoc if a != native], "description": clean_html(s.get("description")),
        "publication_status": status_of(s), "start_year": year_of(s), "format": fmt,
        "country_of_origin": country, "chapters_total": None, "volumes_total": None,
        "average_score": round(rating * 10) if rating else None, "authors": authors,
        "genres": [g["genre"] for g in s.get("genres") or []],
        "tags": [{"name": c["category"], "rank": None, "spoiler": False} for c in cats],
        "links": [{"site": "MangaUpdates", "url": s["url"]}] if s.get("url") else [],
        "cover_url": image, "banner_url": None,
        "ids": {"mangaupdates_id": str(s["series_id"])},
    }


class MangaUpdates(Plugin):
    id = "mangaupdates"
    name = "MangaUpdates"
    description = "Built-in metadata provider: MangaUpdates link, and fallback metadata."
    is_provider = True
    url = "https://www.mangaupdates.com"

    def search(self, ctx, query, year=None):
        r = http.post_json(f"{API}/series/search", {"search": query, "perpage": 10})
        out = []
        for res in r.get("results") or []:
            rec = res["record"]
            others = [res["hit_title"]] if res.get("hit_title") and res["hit_title"] != rec["title"] else []
            out.append(Candidate(
                provider_id=str(rec["series_id"]), title=clean_html(rec["title"]) or "?", url=rec.get("url"),
                cover_url=((rec.get("image") or {}).get("url") or {}).get("original"),
                year=year_of(rec), format=rec.get("type"), other_titles=others))
        return out

    def fetch(self, ctx, provider_id):
        return normalize(http.get_json(f"{API}/series/{provider_id}"))

    def id_from_url(self, url):
        m = URL_ID.search(url)
        if not m:
            return None
        return str(int(m.group(1), 36))  # URLs use base-36 ids; the API uses decimal
