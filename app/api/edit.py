"""Editing a series by hand: metadata fields (locked against refresh), poster, link icons."""
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.api.common import ensure_inside_library, get_or_404
from app.db import get_db
from app.metadata import favicon, http, service as metadata
from app.models import Book, Link, Series, SeriesTag, Tag
from app.plugins import Context
from app.posters import MAX_UPLOAD, PosterError, remove_poster, save_poster_bytes
from app.scanner import archives

router = APIRouter(prefix="/api")

Short = Field(default=None, max_length=300)


class AuthorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="", max_length=100)


class LinkIn(BaseModel):
    site: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=1000)

    @field_validator("url")
    @classmethod
    def http_only(cls, v):
        p = urlparse(v.strip())
        if p.scheme not in ("http", "https") or not p.netloc:
            raise ValueError("Links must start with http:// or https://")
        return v.strip()


class MetadataEdit(BaseModel):
    """Only the fields that are sent are changed; each changed field is locked."""
    title_english: str | None = Short
    title_romaji: str | None = Short
    title_native: str | None = Short
    synonyms: list[str] | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=20000)
    publication_status: Literal["RELEASING", "FINISHED", "HIATUS", "CANCELLED", "NOT_YET_RELEASED",
                                "UNKNOWN"] | None = None
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    format: Literal["MANGA", "ONE_SHOT", "NOVEL"] | None = None
    country_of_origin: Literal["JP", "KR", "CN", "TW"] | None = None
    volumes_total: int | None = Field(default=None, ge=0, le=100000)
    chapters_total: int | None = Field(default=None, ge=0, le=100000)
    average_score: int | None = Field(default=None, ge=0, le=100)
    authors: list[AuthorIn] | None = Field(default=None, max_length=40)
    genres: list[str] | None = Field(default=None, max_length=60)
    tags: list[str] | None = Field(default=None, max_length=200)
    links: list[LinkIn] | None = Field(default=None, max_length=30)
    unlock: list[str] = []  # fields to hand back to automatic metadata


SIMPLE = ["title_english", "title_romaji", "title_native", "synonyms", "description", "publication_status",
          "start_year", "format", "country_of_origin", "volumes_total", "chapters_total", "average_score"]


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    if isinstance(v, list):
        return [x for x in (_clean(i) for i in v) if x]
    return v


@router.put("/series/{series_id}/metadata")
def edit_metadata(series_id: int, body: MetadataEdit, db=Depends(get_db)):
    s = get_or_404(db, Series, series_id)
    locked = set(s.locked_fields or [])
    sent = body.model_fields_set - {"unlock"}
    for f in SIMPLE:
        if f in sent:
            v = _clean(getattr(body, f))
            if f == "synonyms":
                v = v or []
            if f == "publication_status":
                v = v or "UNKNOWN"
            setattr(s, f, v)
            locked.add(f)
    if "authors" in sent:
        s.authors = [{"name": a.name.strip(), "role": a.role.strip()} for a in body.authors or []]
        locked.add("authors")
    if "genres" in sent or "tags" in sent:
        now_tags = _tags(db, s)  # keep the half that wasn't sent
        genres = _clean(body.genres) if "genres" in sent else [n for n, k in now_tags if k == "genre"]
        tags = _clean(body.tags) if "tags" in sent else [n for n, k in now_tags if k != "genre"]
        metadata.set_tags(db, s, genres or [], [{"name": t} for t in tags or []])
        locked.add("tags")
    if "links" in sent:
        metadata.set_links(db, s, [{"site": l.site.strip(), "url": l.url} for l in body.links or []])
        locked.add("links")
    if "title_romaji" in sent or "title_english" in sent:
        s.sort_title = (s.title_romaji or s.title_english or s.folder_name).lower()
    locked -= set(body.unlock)
    s.locked_fields = sorted(locked)
    db.commit()
    return {"ok": True, "locked_fields": s.locked_fields}


def _tags(db, s):
    """[(name, kind)] of the series' current genres and tags."""
    return (db.query(Tag.name, Tag.kind).join(SeriesTag, SeriesTag.tag_id == Tag.id)
            .filter(SeriesTag.series_id == s.id).all())


# ---------------------------------------------------------------- poster

def _lock_poster(s, lock: bool):
    locked = set(s.locked_fields or [])
    (locked.add if lock else locked.discard)("poster")
    s.locked_fields = sorted(locked)


@router.post("/series/{series_id}/poster/upload")
def upload_poster(series_id: int, file: UploadFile = File(...), db=Depends(get_db)):
    """Upload an image as the poster. It is checked and re-encoded; the original is not kept."""
    s = get_or_404(db, Series, series_id)
    data = file.file.read(MAX_UPLOAD + 1)  # never read more than the limit into memory
    try:
        save_poster_bytes(s, data, "upload")
    except PosterError as e:
        raise HTTPException(400, str(e))
    _lock_poster(s, True)
    db.commit()
    return {"ok": True}


class PosterChoice(BaseModel):
    source: Literal["page", "provider", "first_page"]
    book_id: int | None = None
    page: int = Field(default=0, ge=0)


@router.post("/series/{series_id}/poster")
def choose_poster(series_id: int, body: PosterChoice, db=Depends(get_db)):
    """Poster from a page of one of this series' books, from the metadata provider, or automatic."""
    s = get_or_404(db, Series, series_id)
    if body.source == "page":
        b = db.get(Book, body.book_id or 0)
        if b is None or b.series_id != s.id:
            raise HTTPException(404, "That book is not in this series")
        path = str(ensure_inside_library(db, b.path))
        try:
            data, _ = archives.read_page(path, b.format, body.page)
            save_poster_bytes(s, data, "page")
        except IndexError:
            raise HTTPException(404, "No such page")
        except (PosterError, archives.ArchiveError) as e:
            raise HTTPException(400, str(e))
        _lock_poster(s, True)
    elif body.source == "provider":
        src = s.metadata_source
        p = src and metadata.provider(src["provider"])
        if not p:
            raise HTTPException(400, "This series has no metadata source; use Fix match first")
        try:
            url = p.fetch(Context(p, db), src["id"]).get("cover_url")
            if not url:
                raise HTTPException(404, "The provider has no cover for this series")
            save_poster_bytes(s, http.get_bytes(url), "provider")
        except http.ProviderError as e:
            raise HTTPException(503, f"Provider unavailable: {e}")
        except PosterError as e:
            raise HTTPException(400, str(e))
        _lock_poster(s, False)
    else:
        remove_poster(s)
        _lock_poster(s, False)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- link icons

@router.get("/links/{link_id}/icon")
def link_icon(link_id: int, db=Depends(get_db)):
    link = get_or_404(db, Link, link_id)
    png = favicon.icon_png(link.url)
    if png is None:
        raise HTTPException(404, "No icon")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=604800"})
