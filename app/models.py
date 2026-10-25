"""Database tables (SPEC section 5). All fields exist now; later phases fill them."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Library(Base):
    __tablename__ = "library"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    root_path: Mapped[str] = mapped_column(String, unique=True)
    # direction for its series that aren't matched (see app/direction.py); None: Settings → Reader
    reader_mode: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Series(Base):
    __tablename__ = "series"
    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("library.id", ondelete="CASCADE"), index=True)
    folder_path: Mapped[str] = mapped_column(String, unique=True)
    folder_name: Mapped[str] = mapped_column(String)
    sort_title: Mapped[str] = mapped_column(String, index=True)

    # metadata (phase 2)
    title_english: Mapped[str | None] = mapped_column(String)
    title_romaji: Mapped[str | None] = mapped_column(String)
    title_native: Mapped[str | None] = mapped_column(String)
    synonyms: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    publication_status: Mapped[str] = mapped_column(String, default="UNKNOWN")
    start_year: Mapped[int | None] = mapped_column(Integer)
    format: Mapped[str | None] = mapped_column(String)
    country_of_origin: Mapped[str | None] = mapped_column(String)
    chapters_total: Mapped[int | None] = mapped_column(Integer)
    volumes_total: Mapped[int | None] = mapped_column(Integer)
    average_score: Mapped[int | None] = mapped_column(Integer)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    anilist_id: Mapped[int | None] = mapped_column(Integer)
    mal_id: Mapped[int | None] = mapped_column(Integer)
    mangaupdates_id: Mapped[str | None] = mapped_column(String)
    mangadex_id: Mapped[str | None] = mapped_column(String)
    poster_path: Mapped[str | None] = mapped_column(String)
    poster_source: Mapped[str] = mapped_column(String, default="first_page")
    match_status: Mapped[str] = mapped_column(String, default="unmatched")
    match_confidence: Mapped[int | None] = mapped_column(Integer)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_fields: Mapped[list] = mapped_column(JSON, default=list)
    match_candidates: Mapped[list | None] = mapped_column(JSON)   # top results when not auto-accepted
    metadata_source: Mapped[dict | None] = mapped_column(JSON)    # {"provider": "anilist", "id": "123"}
    banner_url: Mapped[str | None] = mapped_column(String)

    reading_status: Mapped[str | None] = mapped_column(String)
    reader_mode: Mapped[str | None] = mapped_column(String)  # per-series reader direction
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    has_duplicates: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    books: Mapped[list["Book"]] = relationship(back_populates="series", cascade="all, delete-orphan")


class Book(Base):
    __tablename__ = "book"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String, unique=True)
    file_name: Mapped[str] = mapped_column(String)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float] = mapped_column(Float, default=0)
    format: Mapped[str] = mapped_column(String)  # zip, rar, 7z, folder, epub, pdf
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String)  # unreadable/corrupt file message

    volume: Mapped[float | None] = mapped_column(Float)
    chapter: Mapped[float | None] = mapped_column(Float)
    chapter_end: Mapped[float | None] = mapped_column(Float)
    extra: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_index: Mapped[float | None] = mapped_column(Float)
    revision: Mapped[int] = mapped_column(Integer, default=1)

    override_volume: Mapped[float | None] = mapped_column(Float)
    override_chapter: Mapped[float | None] = mapped_column(Float)
    override_chapter_end: Mapped[float | None] = mapped_column(Float)  # "chapters 10-18" in one file
    override_extra: Mapped[bool | None] = mapped_column(Boolean)
    sort_override: Mapped[int | None] = mapped_column(Integer)
    hidden_by_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # older revision or owner-hidden
    duplicate_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_index: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_path: Mapped[str | None] = mapped_column(String)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    series: Mapped[Series] = relationship(back_populates="books")

    # what ordering and labels use: the owner's manual values win over the parsed file name
    @property
    def eff_volume(self):
        return self.override_volume if self.override_volume is not None else self.volume

    @property
    def eff_chapter(self):
        return self.override_chapter if self.override_chapter is not None else self.chapter

    @property
    def eff_chapter_end(self):
        return self.override_chapter_end if self.override_chapter is not None else self.chapter_end

    @property
    def eff_extra(self):
        return self.override_extra if self.override_extra is not None else self.extra

    @property
    def has_override(self):
        return any(v is not None for v in (self.override_volume, self.override_chapter,
                                            self.override_extra)) or self.hidden_by_owner

    progress: Mapped["Progress | None"] = relationship(back_populates="book", uselist=False,
                                                       cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tag"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String, default="tag")  # genre, tag, custom


class SeriesTag(Base):
    __tablename__ = "series_tag"
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True)
    rank: Mapped[int | None] = mapped_column(Integer)
    is_spoiler: Mapped[bool] = mapped_column(Boolean, default=False)


class Link(Base):
    __tablename__ = "link"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    site: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)


class Progress(Base):
    __tablename__ = "progress"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id", ondelete="CASCADE"), unique=True)
    page: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    book: Mapped[Book] = relationship(back_populates="progress")


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class PluginPage(Base):
    """Per-page values written by plugins (e.g. the translated text of one page).
    Deleted with the book, and when the book's file changes."""
    __tablename__ = "plugin_page"
    __table_args__ = (UniqueConstraint("plugin", "book_id", "page"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plugin: Mapped[str] = mapped_column(String)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PluginSeriesData(Base):
    """Per-series values a plugin keeps for itself (e.g. a translator's notes on the characters).
    Unlike PluginField, never shown in the app. Deleted with the series."""
    __tablename__ = "plugin_series"
    __table_args__ = (UniqueConstraint("plugin", "series_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plugin: Mapped[str] = mapped_column(String)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OverlayHidden(Base):
    """An overlay box the owner hid in the reader (e.g. a wrong translation over the art).
    `box` is the box's position on the page ("x,y"), so it survives the plugin redoing the page."""
    __tablename__ = "overlay_hidden"
    __table_args__ = (UniqueConstraint("book_id", "plugin", "page", "box"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id", ondelete="CASCADE"), index=True)
    plugin: Mapped[str] = mapped_column(String)
    page: Mapped[int] = mapped_column(Integer)
    box: Mapped[str] = mapped_column(String)


class PluginField(Base):
    """Extra per-series values written by plugins (e.g. 'translation group')."""
    __tablename__ = "plugin_field"
    __table_args__ = (UniqueConstraint("series_id", "plugin", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    plugin: Mapped[str] = mapped_column(String)
    key: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
