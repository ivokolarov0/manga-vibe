/* The built-in Home page (view:home, Plugin API 30): homeRow() is a row's component, the arrow buttons of its
   carousel and swiping it with the mouse. The rows and their cards are MangaVibe.home ($store.home). Phones and
   touch screens swipe the row sideways by themselves; a mouse drags it (press, move, let go: it glides on a
   little and stops at a card), and a drag doesn't open the card it ends on. Tab goes through its cards (the
   browser scrolls each one into view). Nothing else from the app but window.MangaVibe. */
const DRAG_START = 6;  // px the mouse moves before a press is a drag, not a click

MangaVibe.onStart(() => {
  Alpine.data("homeRow", () => ({
    canPrev: false, canNext: false,
    selecting: false,  // the library card view asks (multi-select is the library's only)
    drag: null,        // a mouse press on the row: { id, x, left, moved, t, v }
    dragged: false,    // the press that just ended was a drag: its click opens nothing

    // ---- swiping with the mouse ----
    press(e) {
      if (e.pointerType !== "mouse" || e.button !== 0) return;  // touch and pens scroll the row themselves
      const el = this.$refs.track;
      this.drag = { id: e.pointerId, x: e.clientX, left: el.scrollLeft, moved: false, t: e.timeStamp, v: 0 };
    },
    pull(e) {
      const d = this.drag, el = this.$refs.track;
      if (!d || e.pointerId !== d.id) return;
      const dx = e.clientX - d.x;
      if (!d.moved) {
        if (Math.abs(dx) < DRAG_START) return;
        d.moved = true;
        el.setPointerCapture(e.pointerId);
        el.classList.add("dragging");  // no snapping while the mouse holds it (home.css)
      }
      const before = el.scrollLeft;
      el.scrollLeft = d.left - dx;
      const dt = Math.max(e.timeStamp - d.t, 1);
      d.v = 0.8 * ((el.scrollLeft - before) / dt) + 0.2 * d.v;  // px per ms, smoothed
      d.t = e.timeStamp;
    },
    release(e) {
      const d = this.drag, el = this.$refs.track;
      if (!d || e.pointerId !== d.id) return;
      this.drag = null;
      if (!d.moved) return;
      this.dragged = true;
      setTimeout(() => (this.dragged = false));  // after the click this release brings
      // glide on a little (the speed of the last moves), then stop at the nearest card
      const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
      const aim = el.scrollLeft + (still || e.timeStamp - d.t > 100 ? 0 : d.v * 220);
      // where each card starts, as a scroll position: the first card is in place at 0
      const cards = [...el.querySelectorAll(".home-item")];
      const starts = cards.map((c) => c.offsetLeft - cards[0].offsetLeft);
      const nearest = starts.reduce((a, b) => (Math.abs(b - aim) < Math.abs(a - aim) ? b : a), aim);
      const to = Math.max(0, Math.min(el.scrollWidth - el.clientWidth, nearest));
      el.scrollTo({ left: to, behavior: still ? "auto" : "smooth" });
      // snapping back on once the glide has ended (else it would cut it short)
      const done = () => el.classList.remove("dragging");
      if ("onscrollend" in el) el.addEventListener("scrollend", done, { once: true });
      setTimeout(done, 600);
    },
    // a drag's click opens nothing (the card under the mouse when it ended)
    swallow(e) {
      if (!this.dragged) return;
      e.preventDefault();
      e.stopPropagation();
    },

    init() {
      this.$nextTick(() => this.update());
      const track = this.$refs.track;
      if (window.ResizeObserver && track) {
        this.seen = new ResizeObserver(() => this.update());
        this.seen.observe(track);
      }
    },
    destroy() { this.seen?.disconnect(); },
    // which arrows can go anywhere; right to left pages scroll the other way (scrollLeft below 0)
    update() {
      const el = this.$refs.track;
      if (!el) return;
      const x = Math.abs(el.scrollLeft), room = el.scrollWidth - el.clientWidth;
      this.canPrev = x > 2;
      this.canNext = x < room - 2;
    },
    // a screen of cards back or on (less one card, so where you were stays in sight)
    step(dir) {
      const el = this.$refs.track, card = el?.querySelector(".home-item");
      if (!el) return;
      const by = Math.max(el.clientWidth - (card ? card.offsetWidth : 0), card ? card.offsetWidth : 100);
      const rtl = getComputedStyle(el).direction === "rtl" ? -1 : 1;
      const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.scrollBy({ left: dir * by * rtl, behavior: still ? "auto" : "smooth" });
    },
  }));
});
