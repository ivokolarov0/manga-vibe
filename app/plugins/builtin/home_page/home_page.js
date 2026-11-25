/* The built-in Home page (view:home, Plugin API 30): homeRow() is a row's component, its carousel. The rows
   and their cards are MangaVibe.home ($store.home).

   The carousel is Embla Carousel (static/js/vendor/embla-carousel.umd.js, window.EmblaCarousel): the row
   (x-ref "track") is its viewport, .home-slides moves, each .home-item is a slide. It drags with the mouse
   and swipes with a finger (with momentum, stopping at a card), and a drag doesn't open the card it ends on.
   The arrows move a screen of cards; Tab brings the card it reaches into view (watchFocus). Cards added or
   taken away (Home loaded again) are picked up by itself (watchSlides). Without Embla the row still scrolls
   sideways (home.css). Nothing else from the app but window.MangaVibe. */
MangaVibe.onStart(() => {
  Alpine.data("homeRow", () => {
    let embla = null;  // the row's carousel: kept out of Alpine's reactive data (no proxy around it)
    return {
    canPrev: false, canNext: false,
    selecting: false,  // the library card view asks (multi-select is the library's only)
    init() {
      if (!window.EmblaCarousel) return;
      // once the row's cards are drawn (the x-for inside runs after this). Its own viewport, looked up in
      // its own element: $refs isn't filled in yet while init() runs
      this.$nextTick(() => {
        const view = this.$root.querySelector(".home-track");
        if (!view) return;
        const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
        embla = EmblaCarousel(view, {
          container: ".home-slides", slides: ".home-item",
          align: "start", containScroll: "trimSnaps", slidesToScroll: "auto",
          skipSnaps: true,  // a strong swipe can go past several cards
          duration: still ? 10 : 25, watchFocus: true,
        });
        const update = () => {
          this.canPrev = embla.canScrollPrev();
          this.canNext = embla.canScrollNext();
        };
        embla.on("select", update).on("reInit", update).on("resize", update);
        update();
        view.classList.add("carousel");  // home.css: Embla moves the cards now, not the scrollbar
        // Tab to a card out of sight: the browser scrolls the viewport itself, on top of Embla's move
        // (watchFocus), and the two would disagree. Undo it; Embla brings the card into view
        view.addEventListener("scroll", () => { if (view.scrollLeft) view.scrollLeft = 0; }, { passive: true });
      });
    },
    destroy() { embla?.destroy(); },
    // a screen of cards back or on
    step(dir) { dir < 0 ? embla?.scrollPrev() : embla?.scrollNext(); },
    carousel() { return embla; },  // the row's Embla, for a script of yours (and the tests)
    };
  });
});
