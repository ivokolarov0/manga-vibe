"""The reading counts the database keeps (series_total, series_read; triggers in app/db.py) always match a
count of the books and progress, whatever changed them: reading, marking, the owner's numbers, a scan, a
series deleted with everything in it, another user's data removed."""
import shutil

from sqlalchemy import text

from tests.conftest import TMP, make_zip

SERIES = TMP / "manga" / "Counted"


def stored_and_counted():
    """(the stored counts, a fresh count of everything), as sets of rows. The fresh count is made in a
    transaction that's rolled back."""
    from app import db as database
    read = lambda c: (set(c.execute(text("SELECT * FROM series_total WHERE total OR main")).all()),  # noqa: E731
                      set(c.execute(text("SELECT * FROM series_read WHERE done OR started OR done_main")).all()))
    with database.engine.connect() as c:
        stored = read(c)
        database.rebuild_reading_counts(c)
        counted = read(c)
        c.rollback()
    return stored, counted


def check():
    stored, counted = stored_and_counted()
    assert stored == counted


def counts_of(client, sid):
    s = client.get(f"/api/series/{sid}").json()
    return s["book_count"], s["unread_count"], s["reading_status"]


def test_counts_follow_every_change(client):
    from app import users
    from app.db import SessionLocal
    from app.models import Series
    from app.scanner import walker

    check()  # what the tests before this one left
    for n in (1, 2, 3, 4):
        make_zip(SERIES / f"Counted c{n:02d}.zip")
    make_zip(SERIES / "Counted Omake.zip")
    walker.scan_all()
    with SessionLocal() as db:
        sid = db.query(Series.id).filter_by(folder_name="Counted").scalar()
    books = client.get(f"/api/series/{sid}/books").json()
    ids = [b["id"] for b in books]
    assert counts_of(client, sid) == (5, 5, "unread")
    check()

    client.put(f"/api/books/{ids[0]}/progress", json={"page": 1})  # started
    check()
    client.put(f"/api/books/{ids[0]}/progress", json={"page": 2, "completed": True})  # read
    client.put(f"/api/books/{ids[1]}/progress", json={"page": 1})
    assert counts_of(client, sid) == (5, 4, "reading")
    check()
    client.put(f"/api/books/{ids[1]}/progress", json={"page": 0, "completed": False})  # forgotten
    check()

    # the owner's numbers: an extra, a hidden book, and back
    client.patch(f"/api/books/{ids[2]}", json={"extra": True})
    client.patch(f"/api/books/{ids[3]}", json={"hidden": True})
    check()
    client.post(f"/api/series/{sid}/mark-read")
    assert counts_of(client, sid) == (4, 0, "completed")
    check()
    client.patch(f"/api/books/{ids[3]}", json={"reset": True})  # shown again, not read
    assert counts_of(client, sid)[:2] == (5, 1)
    check()
    client.post("/api/series/bulk", json={"ids": [sid], "action": "unread"})
    check()

    # another user reads, then their data is removed
    with users.acting_as(users.User(7, "seven", can_manage=False)):
        for b in ids[:3]:
            client.put(f"/api/books/{b}/progress", json={"page": 1, "completed": True})
        assert counts_of(client, sid)[1] == 2
    assert counts_of(client, sid)[1] == 5  # the owner's own counts didn't move
    check()
    with SessionLocal() as db:
        users.forget(db, 7)
        db.commit()
    check()

    # a file goes away (the scan marks it missing), a book moves to another series by hand
    client.put(f"/api/books/{ids[4]}/progress", json={"page": 1, "completed": True})
    (SERIES / books[4]["file_name"]).unlink()
    walker.scan_all()
    check()
    other = client.get("/api/series").json()["items"][0]["id"]
    with SessionLocal() as db:
        db.execute(text("UPDATE book SET series_id = :o WHERE id = :b"), {"o": other, "b": ids[0]})
        db.commit()
    check()
    with SessionLocal() as db:
        db.execute(text("UPDATE book SET series_id = :s WHERE id = :b"), {"s": sid, "b": ids[0]})
        db.commit()

    # the series goes in one statement: the database deletes its books and progress (cascade)
    client.put(f"/api/books/{ids[1]}/progress", json={"page": 1, "completed": True})
    with SessionLocal() as db:
        db.execute(text("DELETE FROM series WHERE id = :s"), {"s": sid})
        db.commit()
        assert not db.execute(text("SELECT 1 FROM series_total WHERE series_id = :s"), {"s": sid}).first()
    check()
    shutil.rmtree(SERIES)
    walker.scan_all()
    check()


def test_old_database_gets_its_counts(tmp_path):
    """The migration that adds the counts fills them from what was read before."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app import db as database
    from app.models import Book, Library, Progress, Series
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    database.migrate(eng)
    with Session(eng) as db:
        db.add(Library(id=1, name="m", root_path="/m"))
        db.add(Series(id=1, library_id=1, folder_path="/m/a", folder_name="a", sort_title="a"))
        for i in (1, 2, 3):
            db.add(Book(id=i, series_id=1, path=f"/m/a/{i}.zip", file_name=f"{i}.zip", format="zip",
                        page_count=10, extra=i == 3, sort_index=i))
        db.add(Progress(user_id=1, book_id=1, page=9, completed=True))
        db.commit()
    with eng.begin() as c:
        # as before the counts existed: no triggers, nothing counted, one migration to go
        for name, _ in database.READING_COUNT_TRIGGERS:
            c.execute(text(f"DROP TRIGGER {name}"))
        c.execute(text("DELETE FROM series_total"))
        c.execute(text("DELETE FROM series_read"))
        c.execute(text("UPDATE schema_version SET version = :v"), {"v": len(database.MIGRATIONS) - 1})
    database.migrate(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT series_id, total, main FROM series_total")).all() == [(1, 3, 2)]
        assert c.execute(text("SELECT user_id, series_id, done, started, done_main FROM series_read")).all() == [
            (1, 1, 1, 0, 1)]
        assert len(c.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")).all()) == len(
            database.READING_COUNT_TRIGGERS)
    eng.dispose()
