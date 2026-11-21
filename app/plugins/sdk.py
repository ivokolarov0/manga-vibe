"""The public SDK for plugins (Plugin API 22, issue #209): what a plugin may import from MangaVibe besides
`app.plugins` itself (Plugin, Context...), `app.plugins.web`, `app.plugins.query` and `app.users`.

The built-in plugins use only these (tests/contracts/test_sdk.py checks it), so a replacement can always
do what the built-in one does. Where the app keeps these things inside may change; the names here don't,
except by the deprecation rule in docs/PLUGINS.md (The SDK): a name taken out first warns for one
minor version of the Plugin API.

    from app.plugins.sdk import Series, Book, get_setting, display_title
"""
# The database rows a plugin may read. Change library files only through the app (ctx, the scanner);
# rows a plugin owns are its own (PluginSeriesData through ctx.set_series_data, settings).
from app.models import Book, Library, Link, PluginSeriesData, Series, SeriesTag, Shelf, Tag

# A database session of its own (with SessionLocal() as db) and the FastAPI dependency for one; settings
# (ctx.settings is the plugin's own; these read and write any key, e.g. a job's data, jobs:<job>)
from app.db import SessionLocal, get_db, get_setting, set_setting

# Password hashing (scrypt) and the throttle against guessing
from app.passwords import MAX_TRIES, Throttle, TooManyTries, check_password, hash_password

# Reading direction: the modes a series or library can have (ctx.reading_direction() works one out)
from app.direction import MODES as READING_MODES

# The parser's numbers: sort names the way the scanner does (Vol 2 before Vol 10)
from app.scanner.parser import natural_key

# Book formats (Book.format): the ones the app reads; text books (light novels) a plugin's reader reads
from app.scanner.archives import SUPPORTED as BOOK_FORMATS, TEXT_BOOK, TEXT_EPUB

# Titles and numbers the way the app shows them
from app.api.common import book_label, chapter_gaps, display_title, numberings, poster_version, title_lang

# For a plugin's own API (router): a row or 404, and the series the person asking may see
from app.api.common import get_or_404
from app.api.filters import visible_series

# For metadata plugins: throttled HTTP with retries (also ctx.http), text clean-up, editions, the ids
from app.metadata import editions, http
from app.metadata.service import ID_FIELDS
from app.metadata.text import clean_html, has_script

__all__ = [
    "Book", "Library", "Link", "PluginSeriesData", "Series", "SeriesTag", "Shelf", "Tag",
    "SessionLocal", "get_db", "get_setting", "set_setting",
    "MAX_TRIES", "Throttle", "TooManyTries", "check_password", "hash_password",
    "READING_MODES", "natural_key", "BOOK_FORMATS", "TEXT_BOOK", "TEXT_EPUB",
    "book_label", "chapter_gaps", "display_title", "numberings", "poster_version", "title_lang",
    "get_or_404", "visible_series",
    "editions", "http", "ID_FIELDS", "clean_html", "has_script",
]
