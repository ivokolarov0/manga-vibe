"""collection(): owned vs. total volumes/chapters, and complete / incomplete / unknown."""
from types import SimpleNamespace

from app.api.common import collection


def book(volume=None, chapter=None, chapter_end=None, extra=False, hidden=False):
    return SimpleNamespace(eff_volume=volume, eff_chapter=chapter, eff_chapter_end=chapter_end,
                           eff_extra=extra, hidden=hidden)


def series(volumes=None, chapters=None, status="FINISHED"):
    return SimpleNamespace(volumes_total=volumes, chapters_total=chapters, publication_status=status)


def state(s, books):
    return collection(s, books)["state"]


def test_volumes():
    books = [book(volume=v) for v in range(1, 9)]
    assert collection(series(volumes=8), books) == {"unit": "volume", "owned": 8, "total": 8, "state": "complete"}
    missing = collection(series(volumes=8), books[:3] + books[4:])
    assert missing["owned"] == 7 and missing["state"] == "incomplete"
    # duplicates, hidden files, extras and half volumes don't count
    extra = books[:7] + [book(volume=7), book(volume=8, hidden=True), book(extra=True), book(volume=7.5)]
    assert state(series(volumes=8), extra) == "incomplete"


def test_chapters_with_ranges():
    books = [book(chapter=1, chapter_end=10), book(chapter=11), book(chapter=12, chapter_end=20)]
    assert state(series(chapters=20), books) == "complete"
    c = collection(series(chapters=30), books)
    assert (c["owned"], c["state"]) == (20, "incomplete")
    # books with both a volume and a chapter number count by chapter
    assert state(series(chapters=2), [book(volume=1, chapter=1), book(volume=1, chapter=2)]) == "complete"


def test_unknown():
    vols = [book(volume=1), book(volume=2)]
    assert state(series(), vols) == "unknown"                                   # no total
    assert state(series(chapters=50), vols) == "unknown"                        # total is in the other unit
    assert state(series(volumes=2, status="RELEASING"), vols) == "unknown"      # still coming out
    assert collection(series(volumes=3, status="RELEASING"), vols)["owned"] == 2  # still shown as 2 / 3
    assert state(series(volumes=2, chapters=20), vols + [book(chapter=15)]) == "unknown"  # mixed
    assert state(series(volumes=1), [book()]) == "unknown"                      # no numbers at all


def test_numbering_ranges_and_gaps():
    from app.api.common import chapter_gaps, collection_of, numbering
    books = [book(chapter=1, chapter_end=3), book(chapter=4), book(chapter=9), book(chapter=12, chapter_end=14),
             book(chapter=20, hidden=True), book(chapter=30, extra=True)]
    num = numbering(books)
    assert num == {"unit": "chapter", "have": [[1, 4], [9, 9], [12, 14]], "chapters": [[1, 4], [9, 9], [12, 14]]}
    assert chapter_gaps(num) == [(4, 9), (9, 12)]
    # numbers outside 1..total don't count towards it
    c = collection_of(series(chapters=10), numbering([book(chapter=0, chapter_end=10), book(chapter=11)]))
    assert (c["owned"], c["state"]) == (10, "complete")
    assert collection_of(series(), numbering([book(chapter=0, chapter_end=10)]))["owned"] == 11
    # a mix of volumes and chapters has no unit, but its chapter gaps are still found
    mixed = numbering([book(volume=1), book(chapter=5), book(chapter=8)])
    assert mixed["unit"] is None and chapter_gaps(mixed) == [(5, 8)]
