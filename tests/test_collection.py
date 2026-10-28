"""collection(): owned vs. total volumes/chapters, and complete / incomplete / unknown."""
from types import SimpleNamespace

from app.api.common import collection


def book(volume=None, chapter=None, chapter_end=None, extra=False, hidden=False, pages=0):
    return SimpleNamespace(eff_volume=volume, eff_chapter=chapter, eff_chapter_end=chapter_end,
                           eff_extra=extra, hidden=hidden, page_count=pages)


def series(volumes=None, chapters=None, status="FINISHED", marked=False, editions=None, picked=None):
    return SimpleNamespace(volumes_total=volumes, chapters_total=chapters, publication_status=status,
                           collection_complete=marked, editions=editions, edition_volumes=picked)


def state(s, books):
    return collection(s, books)["state"]


def test_volumes():
    books = [book(volume=v) for v in range(1, 9)]
    assert collection(series(volumes=8), books) == {"unit": "volume", "owned": 8, "total": 8, "state": "complete",
                                                   "manual": False}
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


def test_marked_complete_by_hand():
    # a 37-volume series owned as 12 omnibus volumes: complete because the owner says so
    omnibus = [book(volume=v) for v in range(1, 13)]
    c = collection(series(volumes=37, marked=True), omnibus)
    assert (c["owned"], c["total"], c["state"], c["manual"]) == (12, 37, "complete", True)
    assert collection(series(volumes=37), omnibus)["manual"] is False
    # whatever the numbers or the status say
    assert state(series(status="RELEASING", marked=True), [book(volume=1), book(chapter=3)]) == "complete"


# ---------------------------------------------------------------- editions

CAT_EYED_BOY = {  # MangaUpdates, as it lists the editions
    "status": "3 Volumes (Complete, by Shounen Gahosha in 1969)  \n5 Volumes (Reprint, by Shogakukan in 1976)   \n"
              "3 Volumes (Wide-ban, by Asahi Sonorama in 1986)  \n2 Volumes (Bunkoban, by Shogakukan in 1991)   ",
    "publishers": [{"type": "English", "publisher_name": "Viz",
                    "notes": "2 Volumes - Complete; 2 Volumes Perfect Edition - Complete"},
                   {"type": "Original", "publisher_name": "Shogakukan", "notes": "1976, 1991"}],
}
FMA = {
    "status": "27 Volumes (Complete)  \n18 Kanzenban Volumes (Complete)  \n14 Keisouban Volumes (Complete)  \n"
              "150 Webtoon Chapters (Ongoing)",
    "publishers": [{"type": "English", "publisher_name": "Viz",
                    "notes": "27 Vols - Complete | 18 Fullmetal Edition - Complete"},
                   {"type": "English", "publisher_name": "Dark Horse", "notes": "14 Omnibus; print, 3-in-1 - Ongoing"}],
}


def test_editions_from_mangaupdates():
    from app.metadata.editions import from_mangaupdates
    assert from_mangaupdates(CAT_EYED_BOY) == [
        {"volumes": 5, "names": []},
        {"volumes": 3, "names": ["Wide-ban, Asahi Sonorama"]},
        {"volumes": 2, "names": ["Bunkoban, Shogakukan", "Viz", "Viz Perfect Edition"]}]
    assert from_mangaupdates(FMA) == [  # webtoon chapters aren't an edition of volumes
        {"volumes": 27, "names": ["Viz"]},
        {"volumes": 18, "names": ["Kanzenban", "Viz Fullmetal Edition"]},
        {"volumes": 14, "names": ["Keisouban", "Dark Horse Omnibus"]}]
    assert from_mangaupdates({"status": "6 Volumes (Complete)", "publishers": [{"type": "English",
                              "publisher_name": "Kodansha USA", "notes": ""}]}) == [{"volumes": 6, "names": []}]
    assert from_mangaupdates({}) == []


def test_edition_picked_from_pages_per_volume():
    from app.metadata.editions import from_mangaupdates, pick
    cat, fma = from_mangaupdates(CAT_EYED_BOY), from_mangaupdates(FMA)
    assert pick(5, cat, 600) == 2       # thick perfect edition, even with only volume 1 here
    assert pick(5, cat, 200) == 5       # regular volumes
    assert pick(5, cat, 600, top=3) == 3  # volume 3 is here: not the 2-volume edition
    assert pick(27, fma, 290) == 18     # kanzenban
    assert pick(27, fma, 580) == 14     # 3-in-1
    assert pick(27, fma, 190) == 27
    assert pick(27, [], 600) == 27 and pick(27, fma, None) == 27 and pick(None, fma, 600) is None


def test_collection_counts_against_the_edition():
    from app.metadata.editions import from_mangaupdates
    cat = from_mangaupdates(CAT_EYED_BOY)
    perfect = [book(volume=1, pages=620), book(volume=2, pages=640)]
    c = collection(series(volumes=5, editions=cat), perfect)
    assert (c["owned"], c["total"], c["state"]) == (2, 2, "complete")
    assert c["edition"] == {"volumes": 2, "names": ["Bunkoban, Shogakukan", "Viz", "Viz Perfect Edition"],
                            "regular": 5, "manual": False}
    # regular volumes: still 2 of 5, and no edition mentioned
    c = collection(series(volumes=5, editions=cat), [book(volume=1, pages=190), book(volume=2, pages=200)])
    assert (c["total"], c["state"], "edition" in c) == (5, "incomplete", False)
    # the owner's pick wins over the pages
    c = collection(series(volumes=5, editions=cat, picked=5), perfect)
    assert (c["total"], c["state"], c["edition"]["manual"]) == (5, "incomplete", True)
    # the median pages per volume file is stored with the numbering
    from app.api.common import numbering
    assert numbering(perfect + [book(volume=3, pages=10), book(extra=True, pages=999)])["pages"] == 620
    assert "pages" not in numbering([book(chapter=1, pages=20)])
