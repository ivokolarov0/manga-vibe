"""The activity log as a CSV file (Settings → Activity → Export as CSV): what the page's filters show.

One row per entry, newest first: the time in UTC and in the admin's time zone, who (id and name), the
address and whether it came with a key, the kind and its group, the summary, the target, and the details
as JSON. The column names are in the admin's language; the rest is the data as kept. Starts with a byte
order mark, so spreadsheet programs read it as UTF-8. A cell that a spreadsheet would run as a formula
(=, +, -, @ at the start) gets a ' in front.
"""
import csv
import io
import json
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.plugins.sdk import SessionLocal, t

from . import store

FORMULA = ("=", "+", "-", "@", "\t", "\r")
BOM = chr(0xFEFF)  # the byte order mark


def zone(name: str | None, offset_minutes: int) -> tzinfo:
    """The admin's time zone: its IANA name when this server knows it, else the browser's offset now."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone(timedelta(minutes=offset_minutes))


def safe(value) -> str:
    """A cell's text, harmless in a spreadsheet."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA) else text


def header() -> list[str]:
    return [t("Time (UTC)"), t("Time (local)"), t("User ID"), t("User"), t("Address"), t("With a key"),
            t("Kind"), t("Group"), t("Summary"), t("Target"), t("Target ID"), t("Details")]


def row_of(r, tz, words) -> list[str]:
    at = r.at.replace(tzinfo=timezone.utc)
    who = words["system"] if r.user_id is None else r.user_name
    return [safe(v) for v in (
        at.strftime("%Y-%m-%d %H:%M:%S"), at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "" if r.user_id is None else r.user_id, who, r.ip, words["yes"] if r.via == "key" else "",
        r.kind, r.grp, r.summary, r.target_type, r.target_id,
        json.dumps(r.details or {}, ensure_ascii=False, sort_keys=True))]


def csv_rows(filters: dict, tz):
    """The file, a piece at a time: the header (in the language of the request, worked out now), then
    the rows, read in batches from a database session of the export's own."""
    head, words = header(), {"system": t("System"), "yes": t("Yes")}

    def lines():
        buf = io.StringIO()
        out = csv.writer(buf, lineterminator="\r\n")
        buf.write(BOM)
        out.writerow(head)
        with SessionLocal() as db:
            for n, r in enumerate(store.each(db, **filters), 1):
                out.writerow(row_of(r, tz, words))
                if n % 200 == 0:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
        yield buf.getvalue()

    return lines()
