"""The library's "Added" filter (issue #322): added_from and added_to are whole days, inclusive, in the
person's time zone (the page sends tz); saved searches keep the days but not the time zone."""
from datetime import datetime

import pytest

from app.db import SessionLocal
from app.models import Series


@pytest.fixture
def added(client):
    """Three series with known added times (UTC), put back afterwards."""
    times = [datetime(2026, 9, 30, 23, 30), datetime(2026, 10, 15, 12, 0), datetime(2026, 11, 15, 22, 30)]
    with SessionLocal() as db:
        rows = db.query(Series).order_by(Series.id).limit(3).all()
        assert len(rows) == 3
        before = [(s.id, s.added_at) for s in rows]
        for s, at in zip(rows, times):
            s.added_at = at
        others = db.query(Series).filter(~Series.id.in_([s.id for s in rows])).all()
        kept = [(s.id, s.added_at) for s in others]
        for s in others:  # out of the way: long before the days tested
            s.added_at = datetime(2020, 1, 1)
        db.commit()
    yield [sid for sid, _ in before]
    with SessionLocal() as db:
        for sid, at in before + kept:
            db.get(Series, sid).added_at = at
        db.commit()


def test_added_between_two_days(client, added):
    a, b, c = added
    ids = lambda q: sorted(s["id"] for s in client.get(f"/api/series?limit=200&{q}").json()["items"])
    # in UTC: a on 30 Sep, b on 15 Oct, c on 15 Nov
    assert ids("added_from=2026-10-01") == sorted([b, c])
    assert ids("added_to=2026-10-15") == sorted([a, b] + [s for s in ids("") if s not in added])
    assert ids("added_from=2026-10-01&added_to=2026-11-15") == sorted([b, c])  # both days count
    assert ids("added_from=2026-10-15&added_to=2026-10-15") == [b]
    assert ids("added_from=2026-11-16") == []
    # in Sofia (UTC+3 then, +2 after 25 Oct): a is on 1 Oct and c on 16 Nov
    sofia = "tz=Europe/Sofia"
    assert ids(f"added_from=2026-10-01&added_to=2026-11-15&{sofia}") == sorted([a, b])
    assert ids(f"added_from=2026-11-16&{sofia}") == [c]
    # in New York (UTC-4): c is still on 15 Nov
    assert ids("added_from=2026-11-15&added_to=2026-11-15&tz=America/New_York") == [c]
    # a time zone the server doesn't know: UTC
    assert ids("added_from=2026-10-01&tz=Mars/Olympus") == sorted([b, c])
    # empty is no limit; anything but a day is refused
    assert ids("added_from=&added_to=") == ids("")
    for bad in ("added_from=yesterday", "added_to=2026-13-01", "added_from=2026-1-5"):
        assert client.get(f"/api/series?{bad}").status_code == 422, bad


def test_added_is_in_the_schema_and_saved_searches_keep_it(client):
    schema = client.get("/api/library/schema").json()
    added = next(f for f in schema["filters"] if f["key"] == "added")
    assert added["kind"] == "dates" and added["params"] == ["added_from", "added_to"]
    assert "added_from" in schema["params"] and "tz" not in schema["params"]
    r = client.post("/api/plugins/saved-searches/api/searches",
                    json={"name": "Added in autumn", "query": "added_from=2026-09-01&added_to=2026-11-30&tz=Europe/Sofia"})
    assert r.status_code == 200, r.text
    assert r.json()["query"] == "added_from=2026-09-01&added_to=2026-11-30"  # the days, not the time zone
    client.delete(f"/api/plugins/saved-searches/api/searches/{r.json()['id']}")
