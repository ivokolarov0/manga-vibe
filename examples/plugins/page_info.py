"""Example plugin: draws a small label over reader pages (plugin API 3).

It shows the pattern a slow page plugin (e.g. a translator) follows:

- the reader asks page_overlay() for the page you're on; a page that isn't done yet is queued and the
  answer is Overlay(pending=True), so the reader shows "Working…" and asks again a moment later
- one thread of the plugin's own works through the queue (ctx.run_in_thread), saves each result with
  ctx.set_page_data() and stops when ctx.should_stop() says so (plugin reloaded or switched off)
- the next pages are queued too, so they're ready by the time you turn to them
- book_badges() shows how much of each book is done; book_actions are buttons in the reader

The "work" here is only measuring the page: its size and how dark it is.
"""
import io
import queue
import threading

from PIL import Image, ImageStat

from app.models import Book
from app.plugins import Action, Box, Overlay, Plugin

AHEAD = 3  # pages after the one being read that are prepared too


class PageInfo(Plugin):
    id = "page-info"
    name = "Page info"
    version = "1.0"
    description = "Example: a label over each reader page with its size and brightness."
    overlay_label = "Page info"
    book_actions = [Action("all", "Prepare every page"), Action("clear", "Forget this book")]
    permissions = ["Reads page images"]
    # --ov-bg rather than background, so the reader's box opacity setting still applies
    css = ".ov-box.page-info { --ov-bg: rgb(0 0 0 / .7); --ov-outline: #000; color: #fff; border-radius: 4px; }"

    def __init__(self):
        self.jobs = queue.Queue()
        self.queued = set()          # (book_id, page) waiting or being worked on
        self.lock = threading.Lock()
        self.thread = None

    # --- what the reader asks -------------------------------------------------------------------
    def page_overlay(self, ctx, book, page):
        done = ctx.page_data(book)  # {page: value} of the whole book, one query
        for p in range(page, min(page + AHEAD + 1, book.page_count)):
            if p not in done:
                self.queue(ctx, book.id, p)
        info = done.get(page)
        if info is None:
            return Overlay(pending=True, message="Working…")
        return Overlay(boxes=[Box(0.02, 0.94, 0.5, 0.05, text=info, title="Made by the Page info plugin",
                                  cls="page-info")])

    def book_badges(self, ctx, books):
        done = ctx.page_data_counts(books)
        return {b.id: ("Info ✓" if done[b.id] >= b.page_count else f"Info {done[b.id]}/{b.page_count}")
                for b in books if b.id in done}

    def run_book_action(self, ctx, key, book):
        if key == "clear":
            ctx.clear_page_data(book)
            return "Forgot this book"
        for p in range(book.page_count):
            if ctx.get_page_data(book, p) is None:
                self.queue(ctx, book.id, p)
        return "Preparing every page"

    # --- the worker ---------------------------------------------------------------------------
    def queue(self, ctx, book_id, page):
        with self.lock:
            if (book_id, page) in self.queued:
                return
            self.queued.add((book_id, page))
            self.jobs.put((book_id, page))
            if self.thread is None or not self.thread.is_alive():
                self.thread = ctx.run_in_thread(self.work)

    def work(self, ctx):
        while not ctx.should_stop():
            try:
                book_id, page = self.jobs.get(timeout=1)
            except queue.Empty:
                continue  # stay ready; should_stop() ends the loop on reload or switch-off
            try:
                book = ctx.db.get(Book, book_id)
                if book is not None and ctx.get_page_data(book, page) is None:
                    with Image.open(io.BytesIO(ctx.read_page(book, page))) as im:
                        light = ImageStat.Stat(im.convert("L")).mean[0] / 255
                        text = f"{im.width}×{im.height} · {round(light * 100)}% light"
                    ctx.set_page_data(book, page, text)
                    ctx.db.commit()  # the reader can show it right away
            except Exception:
                ctx.db.rollback()
                ctx.log.exception("Page %s of book %s failed", page, book_id)
            finally:
                with self.lock:
                    self.queued.discard((book_id, page))
