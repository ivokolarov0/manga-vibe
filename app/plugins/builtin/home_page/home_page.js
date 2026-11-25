/* The built-in Home page (view:home, Plugin API 30): homeRow() is a row's component, the arrow buttons of its
   carousel. The rows and their cards are MangaVibe.home ($store.home). Phones and touch screens swipe the row
   sideways; Tab goes through its cards (the browser scrolls each one into view). Nothing else from the app
   but window.MangaVibe. */
MangaVibe.onStart(() => {
  Alpine.data("homeRow", () => ({
    canPrev: false, canNext: false,
    selecting: false,  // the library card view asks (multi-select is the library's only)
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
