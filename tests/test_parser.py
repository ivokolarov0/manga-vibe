import random

import pytest

from app.scanner.parser import order, parse

HANA = "Hana to Akuma"

# (filename, series, volume, chapter, chapter_end, revision, extra, extra_index)
CASES = [
    ("Hana_to_Akuma_v01_ch01_[OT].zip", HANA, 1, 1, None, 1, False, None),
    ("Hana_to_Akuma_v01_ch03v2_[OT].zip", HANA, 1, 3, None, 2, False, None),
    ("[KnO]Hana_to_Akuma52.zip", HANA, None, 52, None, 1, False, None),
    ("Hana_to_Akuma10.zip", HANA, None, 10, None, 1, False, None),
    ("Hana_to_Akuma_Bonus_Chapter_[FBD].zip", HANA, None, None, None, 1, False, None),
    ("Hana_to_Akuma_ExtraChapter.zip", HANA, None, None, None, 1, False, None),
    ("Hana_to_Akuma_Extra2.zip", HANA, None, 2, None, 1, False, None),
    ("Hana_to_Akuma_v2.zip", HANA, 2, None, None, 1, False, None),
    ("Berserk Vol.03 (Digital).cbz", "Berserk", 3, None, None, 1, False, None),
    ("[Group] 20th Century Boys c010-012.rar", "20th Century Boys", None, 10, 12, 1, False, None),
    ("Some Title - Chapter 10.5.zip", "Some Title", None, 10.5, None, 1, False, None),
    ("Some Title 015a.cbz", "Some Title", None, 15.1, None, 1, False, None),
    ("Kingdom v05 c040.cbz", "Kingdom", 5, 40, None, 1, False, None),
    ("Title Omake.zip", "Title", None, None, None, 1, False, None),
    ("ワンピース 第3巻.zip", "ワンピース", 3, None, None, 1, False, None),
    ("ワンピース 第12話.zip", "ワンピース", None, 12, None, 1, False, None),
    # a chapter's title doesn't make it an extra or give it a volume (issue #129)
    ("Chapter 333 - Something Special", "", None, 333, None, 1, False, None),
    ("Chapter 267 - Extra The President Has Donkey Ears", "", None, 267, None, 1, False, None),
    ("Chapter 448 - An Assassin From The Past (Around Volume 15).cbz", "", None, 448, None, 1, False, None),
    ("Vol.02 Ch.010: Bonus Round.cbz", "", 2, 10, None, 1, False, None),
    ("Title Extra Chapter 3 - Something.cbz", "Title", None, 3, None, 1, False, None),
    ("Title Chapter 12 Extra.cbz", "Title", None, 12, None, 1, False, None),
    # extras are never guessed from names: the number they carry is their chapter
    ("Title 12+extra.cbz", "Title", None, 12, None, 1, False, None),
    ("Title 12+omake.cbz", "Title", None, 12, None, 1, False, None),
    ("Title 12.5.cbz", "Title", None, 12.5, None, 1, False, None),
]


def test_chapter_titles_leave_no_false_gaps():
    """Hayate the Combat Butler (issue #129): titles with "Special" / "Extra" used to make extras."""
    from app.api.common import BookNumbers, chapter_gaps, numbering
    names = ["Chapter 052 - Nagi - Andalucia dream", "Chapter 053 - Good Luck With Special Training, Nagi!",
             "Chapter 054 - Winning or Losing Is For The Runner", "Chapter 219 - We Are Not Innovators!",
             "Chapter 224 - Getting a Girl into a Good Mood", "Chapter 266 - And It Becomes a Smile",
             "Chapter 267 - Extra The President Has Donkey Ears", "Chapter 268 - The Long Stroy Is Over",
             "Chapter 332 - A Genius's Strategy", "Chapter 333 - Something Special", "Chapter 334 - Identity Crisis",
             "Chapter 347 - Are You A Magnet Or Someting",
             "Chapter 348 - Chef Maria's Summer Vegetable Special, Round 1",
             "Chapter 349 - I Don't Want to Think About Getting Fat Anymore", "Chapter 328"]
    ps = [parse(n) for n in names]
    num = numbering([BookNumbers(p.volume, p.chapter, p.chapter_end, p.extra) for p in ps])
    assert chapter_gaps(num) == [(54, 219), (219, 224), (224, 266), (268, 328), (328, 332), (334, 347)]


@pytest.mark.parametrize("name,series,vol,ch,ch_end,rev,extra,extra_idx", CASES)
def test_parse(name, series, vol, ch, ch_end, rev, extra, extra_idx):
    p = parse(name, series)
    assert p.volume == vol
    assert p.chapter == pytest.approx(ch) if ch is not None else p.chapter is None
    assert p.chapter_end == ch_end
    assert p.version == rev
    assert p.extra == extra
    assert p.extra_index == extra_idx


def test_more_formats():
    assert parse("Chapter 1~3.zip").chapter_end == 3
    assert parse("Some #7.cbz").chapter == 7
    assert parse("random file.zip").chapter is None


def hana_folder():
    files = []
    for c in range(1, 6):
        files.append(f"Hana_to_Akuma_v01_ch{c:02d}{'v2' if c in (3, 4) else ''}_[OT].zip")
    for c in range(6, 10):
        files.append(f"Hana_to_Akuma_v02_ch{c:02d}{'v2' if c in (7, 8) else ''}_[OT].zip")
    files += [f"Hana_to_Akuma{c}.zip" for c in range(10, 15)]
    files += [f"[KnO]Hana_to_Akuma{c}.zip" for c in range(52, 59)]
    unnumbered = ["Hana_to_Akuma_Bonus_Chapter_[FBD].zip", "Hana_to_Akuma_ExtraChapter.zip"]
    files += unnumbered
    files.append("Hana_to_Akuma_v01_ch03_[OT].zip")  # older revision
    return files, unnumbered


@pytest.mark.parametrize("seed", range(5))
def test_full_folder(seed):
    files, unnumbered = hana_folder()
    random.Random(seed).shuffle(files)
    kept, skipped, dups = order(files, HANA)

    numbered = [p for p in kept if p.chapter is not None]
    assert [p.chapter for p in numbered] == list(range(1, 15)) + list(range(52, 59))
    assert [p.name for p in kept[len(numbered):]] == sorted(unnumbered)
    assert not any(p.extra for p in kept)  # extras are only marked by the owner
    assert [p.name for p in skipped] == ["Hana_to_Akuma_v01_ch03_[OT].zip"]
    assert dups == []


def test_same_revision_duplicates_are_kept():
    kept, skipped, dups = order(["[A] T ch01.zip", "[B] T ch01.zip"], "T")
    assert len(kept) == 2 and not skipped and len(dups) == 1


def test_natural_sort_of_unnumbered():
    kept, _, _ = order(["file10 b.zip", "file2 b.zip"], "")
    assert [p.name for p in kept] == ["file2 b.zip", "file10 b.zip"]


def test_group_order_volumes_chapters_extras_unnumbered():
    from app.scanner.parser import Parsed, order_parsed
    files = ["Readme thing.zip", "T Omake.zip", "T ch05.zip", "Vol 02.cbz", "Vol 01.cbz"]
    parsed = [parse(f, "T") for f in files] + [Parsed(name="Marked extra.zip", extra=True)]  # by the owner
    kept, _, _ = order_parsed(parsed)
    assert [p.name for p in kept] == ["Vol 01.cbz", "Vol 02.cbz", "T ch05.zip", "Marked extra.zip",
                                      "Readme thing.zip", "T Omake.zip"]


def test_marked_extra_with_a_chapter_stays_after_that_chapter():
    from app.scanner.parser import Parsed, order_parsed
    parsed = [parse(f, "Nana") for f in ["Nana ch06.zip", "Nana ch05.zip", "Nana ch04.zip", "Nana v02.cbz", "Nana v01.cbz"]]
    parsed += [Parsed(name="Nana ch05 side.zip", chapter=5, extra=True),  # marked extra by the owner
               Parsed(name="Nana v02 bonus.cbz", volume=2, extra=True),
               Parsed(name="Nana loose extra.zip", extra=True)]
    kept, _, dups = order_parsed(parsed)
    assert [p.name for p in kept] == ["Nana v01.cbz", "Nana v02.cbz", "Nana v02 bonus.cbz", "Nana ch04.zip",
                                      "Nana ch05.zip", "Nana ch05 side.zip", "Nana ch06.zip", "Nana loose extra.zip"]
    assert dups == []  # an extra is not a duplicate of its chapter
    assert kept[5].label() == "Ch 5 extra"
