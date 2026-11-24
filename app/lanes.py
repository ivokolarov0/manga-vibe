"""The quick lane: the app's quick database requests run at most QUICK_SLOTS at a time.

Python's sqlite3 lets go of the GIL for every row a query returns, and must get it back after. With many
threads working at once (the web server runs up to 40), each spends most of its time waiting for the
others: on a big library, six library lists at once went at 5 a second and took 1.3 s each, against 72 a
second and 82 ms each two at a time. A library page asks for its list and dozens of posters at once.

So the requests that only read the database and answer at once (the library list, a series and its
books, posters, thumbnails, progress...) queue for a slot instead of all running together. A slot is free
again as soon as the answer starts, not when a poster has reached a slow phone. Requests that can wait on
something else (another website, a plugin, files being deleted, a backup) don't take a slot: they'd hold
it while doing nothing. Pages of a book have their own limit (app/api/books.py PAGE_THREADS).
"""
import re

import anyio

QUICK_SLOTS = 2

# what the quick lane takes: method -> the paths (only the core's own routes, whose work is the database)
QUICK = {
    "GET": re.compile(r"^/api/(series|series/\d+|series/\d+/books|series/\d+/poster|continue-reading|tags|authors"
                      r"|libraries|settings|library/schema|scan/status|books/\d+|books/\d+/progress"
                      r"|books/\d+/thumbnail)$"),
    "PUT": re.compile(r"^/api/books/\d+/progress$"),
}
QUICK["HEAD"] = QUICK["GET"]


class QuickLane:
    """ASGI middleware: the QUICK requests take one of QUICK_SLOTS slots while they're worked out."""

    def __init__(self, app, slots: int = QUICK_SLOTS):
        self.app = app
        self.slots = anyio.CapacityLimiter(slots)

    async def __call__(self, scope, receive, send):
        paths = QUICK.get(scope.get("method", "")) if scope["type"] == "http" else None
        # not the server's own calls on someone's behalf: one may come from a request holding a slot
        if paths is None or not paths.match(scope["path"]) or scope.get("mangavibe.internal"):
            return await self.app(scope, receive, send)
        # held for the request, not the task: a file answer is sent from a task of its own
        borrower = object()
        await self.slots.acquire_on_behalf_of(borrower)
        held = True

        def free():
            nonlocal held
            if held:
                held = False
                self.slots.release_on_behalf_of(borrower)

        async def sending(message):
            if message["type"] == "http.response.start":
                free()  # worked out: sending it (a poster to a slow phone) needs no slot
            await send(message)

        try:
            await self.app(scope, receive, sending)
        finally:
            free()
