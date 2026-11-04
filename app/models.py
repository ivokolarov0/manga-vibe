"""Database tables (SPEC section 5). All fields exist now; later phases fill them."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """An account (app/users.py). The first one is the admin. Each has its own reading progress,
    reading status, favorites and settings, and sees only its own libraries and what is shared with it."""
    __tablename__ = "account"
    # ids are never used again: a removed account's sessions must not fit a new one
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    # "scrypt$n$r$p$salt$hash" (shelves.hash_password); None: no password (a click on the profile is enough)
    password_hash: Mapped[str | None] = mapped_column(String)
    # what the sign-in page asks for: "pin" (digits, a number pad) or "password"; None without one
    password_kind: Mapped[str | None] = mapped_column(String)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # goes up when the password changes or the account is signed out everywhere: older sessions stop working
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UserSeries(Base):
    """One account's own state for a series: what used to sit on Series for everybody."""
    __tablename__ = "user_series"
    user_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), primary_key=True,
                                           index=True)
    reading_status: Mapped[str | None] = mapped_column(String)  # a manual choice; None: from progress
    reader_mode: Mapped[str | None] = mapped_column(String)     # paged | vertical; None: its library's
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class UserSetting(Base):
    """One account's settings (theme, reader, home screen...); server settings stay in Setting."""
    __tablename__ = "user_setting"
    user_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class ShelfShare(Base):
    """A whole library shared with another account, read only. Series added to it later are shared too."""
    __tablename__ = "shelf_share"
    shelf_id: Mapped[int] = mapped_column(ForeignKey("shelf.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), primary_key=True,
                                         index=True)


class SeriesShare(Base):
    """One series shared with another account without the rest of its library, read only. It shows in
    their "Shared with me" library, or in one of their own (shelf_id) if they put it there."""
    __tablename__ = "series_share"
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), primary_key=True,
                                         index=True)
    shelf_id: Mapped[int | None] = mapped_column(ForeignKey("shelf.id", ondelete="SET NULL"))


class Shelf(Base):
    """What the app calls a *library* (Manga, Comics...): a set of series shown together, with its own
    reader default and an optional password. Called a shelf in the code, because `Library` below is
    a scanned library folder (the name it has had since the start). Series are on their folder's
    shelf unless the owner put them on another one (`Series.shelf_id`). See app/shelves.py."""
    __tablename__ = "shelf"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    # the account it belongs to: they see it, change it and share it (app/access.py)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("account.id", ondelete="SET NULL"), index=True)
    # reading mode (paged or vertical) for its series (see app/direction.py); None: Settings → Reader
    reader_mode: Mapped[str | None] = mapped_column(String)
    # "scrypt$n$r$p$salt$hash" (app/shelves.py), never the password itself; None: not locked
    password_hash: Mapped[str | None] = mapped_column(String)
    # goes up whenever the password changes, so browsers unlocked with the old one are locked again
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    # the folder this shelf was made for by the app (named like it); removing that folder removes the
    # shelf too while it's empty. None once the owner renames or sets it up, or if they made it.
    made_for_folder: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Library(Base):
    """A library folder (LIBRARY_PATHS, or added in Settings): what the scanner reads. Its series show
    on its shelf (the app's "library", see Shelf)."""
    __tablename__ = "library"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    root_path: Mapped[str] = mapped_column(String, unique=True)
    # the library its series show in, unless a series picks another one
    shelf_id: Mapped[int | None] = mapped_column(ForeignKey("shelf.id", ondelete="SET NULL"))
    # no longer used: the reading mode moved to the shelf (copied there and cleared by
    # shelves.shelf_for_folder() after migration 14)
    reader_mode: Mapped[str | None] = mapped_column(String)
    # the same folder outside Docker (e.g. /volume1/manga or \\NAS\manga), typed by the owner:
    # the app only sees root_path, the mount inside the container
    host_path: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Series(Base):
    __tablename__ = "series"
    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("library.id", ondelete="CASCADE"), index=True)  # its folder
    # the library (shelf) the owner moved it to; None: its folder's. Never changed by a scan.
    shelf_id: Mapped[int | None] = mapped_column(ForeignKey("shelf.id", ondelete="SET NULL"), index=True)
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

    # no longer used: each account's reading status, reading mode, favorite and last read moved to
    # UserSeries (migration 15). Named apart so nothing reads them by mistake.
    old_reading_status: Mapped[str | None] = mapped_column("reading_status", String)
    old_reader_mode: Mapped[str | None] = mapped_column("reader_mode", String)
    old_favorite: Mapped[bool] = mapped_column("favorite", Boolean, default=False)
    # the owner says the collection is complete whatever the numbers say (e.g. omnibus editions);
    # not metadata, so a refresh never changes it
    collection_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    # the editions MangaUpdates lists (app/metadata/editions.py): [{"volumes": 18, "names": ["Kanzenban"]}]
    editions: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    # the owner's pick of edition (its volume count); None: worked out from the pages per file
    edition_volumes: Mapped[int | None] = mapped_column(Integer)
    has_duplicates: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    old_last_read_at: Mapped[datetime | None] = mapped_column("last_read_at", DateTime(timezone=True), index=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # what the books' numbers say (app/api/common.py numbering()), stored whenever they change;
    # None until worked out
    numbering: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))

    books: Mapped[list["Book"]] = relationship(back_populates="series", cascade="all, delete-orphan")


# Covering index for what the library grid, reading status and collection checks read about many
# books at once: SQLite answers those from the index alone, without loading the (wide) book rows.
BOOK_NUMBERS_COLUMNS = ("series_id", "missing_since", "hidden", "extra", "override_extra", "override_volume",
                        "volume", "override_chapter", "chapter", "override_chapter_end", "chapter_end",
                        "page_count")


class Book(Base):
    __tablename__ = "book"
    __table_args__ = (
        Index("ix_book_numbers", *BOOK_NUMBERS_COLUMNS),
        # Needs attention looks for the few broken and duplicate files; these skip all the others.
        # Written "IS 1" because that's what SQLAlchemy sends for .is_(True): "= 1" wouldn't be used.
        Index("ix_book_error", "series_id", sqlite_where=text("error IS NOT NULL")),
        Index("ix_book_duplicate", "series_id", sqlite_where=text("duplicate_flag IS 1")),
    )
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

    progress: Mapped[list["Progress"]] = relationship(back_populates="book", cascade="all, delete-orphan",
                                                      passive_deletes=True)


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
    """How far one account has read one book."""
    __tablename__ = "progress"
    __table_args__ = (UniqueConstraint("user_id", "book_id"),
                      # covering: the per-series counts read only these columns
                      Index("ix_progress_book_state", "user_id", "book_id", "completed", "page"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id", ondelete="CASCADE"), index=True)
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
