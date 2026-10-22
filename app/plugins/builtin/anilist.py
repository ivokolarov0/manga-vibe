"""AniList metadata provider (primary). GraphQL, no API key."""
import re

from app.db import get_setting
from app.metadata import http
from app.metadata.text import clean_html
from app.plugins import Candidate, Plugin

URL = "https://graphql.anilist.co"
FIELDS = """
  id idMal siteUrl title { romaji english native } synonyms description(asHtml: false)
  status format countryOfOrigin chapters volumes averageScore startDate { year }
  genres tags { name rank isMediaSpoiler } coverImage { extraLarge large } bannerImage
  staff(perPage: 8, sort: RELEVANCE) { edges { role node { name { full } } } }
  externalLinks { site url type }
"""
SEARCH = "query ($s: String, $adult: Boolean) { Page(perPage: 10) { media(search: $s, type: MANGA, " \
         "isAdult: $adult, sort: SEARCH_MATCH) {" + FIELDS + "} } }"
BY_ID = "query ($id: Int) { Media(id: $id, type: MANGA) {" + FIELDS + "} }"
URL_ID = re.compile(r"anilist\.co/manga/(\d+)")


def normalize(m):
    links = [{"site": "AniList", "url": m["siteUrl"]}]
    if m.get("idMal"):
        links.append({"site": "MyAnimeList", "url": f"https://myanimelist.net/manga/{m['idMal']}"})
    for l in m.get("externalLinks") or []:
        if l.get("url") and l.get("type") in ("INFO", "STREAMING", None):
            links.append({"site": l["site"], "url": l["url"]})
    authors, seen = [], {}
    for e in (m.get("staff") or {}).get("edges", []):
        name = e["node"]["name"]["full"]
        role = re.sub(r"\s*\(.*\)", "", e.get("role") or "")
        if name in seen:
            if role and role not in seen[name]["role"]:
                seen[name]["role"] += ", " + role
            continue
        seen[name] = {"name": name, "role": role}
        authors.append(seen[name])
    t = m["title"]
    return {
        "title_english": t.get("english"), "title_romaji": t.get("romaji"), "title_native": t.get("native"),
        "synonyms": m.get("synonyms") or [], "description": clean_html(m.get("description")),
        "publication_status": m.get("status") or "UNKNOWN",
        "start_year": (m.get("startDate") or {}).get("year"), "format": m.get("format"),
        "country_of_origin": m.get("countryOfOrigin"), "chapters_total": m.get("chapters"),
        "volumes_total": m.get("volumes"), "average_score": m.get("averageScore"), "authors": authors,
        "genres": m.get("genres") or [],
        "tags": [{"name": x["name"], "rank": x.get("rank"), "spoiler": bool(x.get("isMediaSpoiler"))}
                 for x in m.get("tags") or []],
        "links": links,
        "cover_url": (m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large"),
        "banner_url": m.get("bannerImage"),
        "ids": {"anilist_id": m["id"], "mal_id": m.get("idMal")},
    }


def to_candidate(m):
    d = normalize(m)
    titles = [d["title_romaji"], d["title_english"], d["title_native"], *d["synonyms"]]
    return Candidate(provider_id=str(m["id"]), title=d["title_romaji"] or d["title_english"] or "?",
                     url=m["siteUrl"], cover_url=d["cover_url"], year=d["start_year"], format=d["format"],
                     status=d["publication_status"], other_titles=[x for x in titles[1:] if x], data=d)


def _gql(query, variables):
    r = http.post_json(URL, {"query": query, "variables": variables})
    if r.get("errors"):
        raise http.ProviderError("AniList: " + "; ".join(e.get("message", "?") for e in r["errors"]))
    return r["data"]


class AniList(Plugin):
    id = "anilist"
    name = "AniList"
    description = "Built-in metadata provider: titles, description, tags, cover, links."
    is_provider = True
    url = "https://anilist.co"

    def search(self, ctx, query, year=None):
        adult = None if get_setting(ctx.db, "include_adult", False) else False
        media = _gql(SEARCH, {"s": query, "adult": adult})["Page"]["media"]
        return [to_candidate(m) for m in media]

    def fetch(self, ctx, provider_id):
        return normalize(_gql(BY_ID, {"id": int(provider_id)})["Media"])

    def id_from_url(self, url):
        m = URL_ID.search(url)
        return m.group(1) if m else None
