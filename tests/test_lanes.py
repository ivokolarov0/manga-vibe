"""The quick lane (app/lanes.py): quick database requests run QUICK_SLOTS at a time; the others aren't held
up; a slot is free again once the answer starts being sent."""
import time

import anyio

from app.lanes import QUICK_SLOTS, QuickLane


def scope(path, method="GET", **extra):
    return {"type": "http", "method": method, "path": path, "headers": [], **extra}


class Busy:
    """An app that works a while, counting how many requests are in it at once. With `stream`, it starts its
    answer at once and then takes its time sending the rest (a poster to a slow phone)."""

    def __init__(self, stream=False):
        self.now = self.most = 0
        self.stream = stream

    async def __call__(self, scope, receive, send):
        self.now += 1
        self.most = max(self.most, self.now)
        if self.stream:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            self.now -= 1  # worked out; the rest is sending
            await anyio.sleep(0.05)
        else:
            await anyio.sleep(0.05)
            self.now -= 1
            await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def run_all(lane, scopes):
    async def nothing(_):
        pass

    async with anyio.create_task_group() as tg:
        for s in scopes:
            tg.start_soon(lane, s, None, nothing)


def test_quick_requests_take_turns():
    app = Busy()
    anyio.run(run_all, QuickLane(app), [scope(f"/api/series/{i}") for i in range(8)])
    assert app.most == QUICK_SLOTS


def test_other_requests_are_not_held_up():
    for s in (scope("/api/links/3/icon"),  # may ask another website
              scope("/api/plugins/x/api/y"),  # a plugin's own
              scope("/api/series/3", method="DELETE"),  # deletes files
              scope("/api/series/3", **{"mangavibe.internal": True})):  # the server's own call
        app = Busy()
        anyio.run(run_all, QuickLane(app), [dict(s) for _ in range(6)])
        assert app.most == 6, s


def test_a_slot_is_free_once_the_answer_starts():
    app = Busy(stream=True)
    start = time.monotonic()
    anyio.run(run_all, QuickLane(app), [scope(f"/api/series/{i}/poster") for i in range(8)])
    # eight slow sends at once take one send's time, not four (8 / 2 slots) of them
    assert time.monotonic() - start < 0.15


def test_an_answer_sent_from_another_task_frees_its_slot():
    """Starlette sends a file from a task of its own (FileResponse): that must free the slot too."""
    async def file_app(scope, receive, send):
        async with anyio.create_task_group() as tg:
            tg.start_soon(send, {"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    lane = QuickLane(file_app)
    anyio.run(run_all, lane, [scope(f"/api/series/{i}/poster") for i in range(6)])
    assert lane.slots.borrowed_tokens == 0


def test_the_app_takes_progress_and_lists_in_the_lane():
    from app.lanes import QUICK
    for method, path in (("GET", "/api/series"), ("GET", "/api/series/12/books"), ("PUT", "/api/books/4/progress"),
                         ("GET", "/api/books/4/thumbnail"), ("HEAD", "/api/series/1/poster")):
        assert QUICK[method].match(path), path
    for method, path in (("GET", "/api/books/4/pages/1"), ("GET", "/api/series/1/badges"),
                         ("POST", "/api/series/1/refresh"), ("GET", "/api/attention")):
        assert method not in QUICK or not QUICK[method].match(path), path
