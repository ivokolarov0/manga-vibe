/* AniList recommendations: anilistRecs() is the panel at the end of the series page (panel.html). It asks
   the plugin's API for the series shown, again when another series is opened or its match changes. */
MangaVibe.onStart(() => {
  Alpine.data("anilistRecs", () => ({
    items: [],
    shown: null,  // the series (and match) the items are for

    init() {
      this.load();
      this.$watch("$store.series.loads", () => this.load());
    },

    async load() {
      const s = MangaVibe.series.s;
      if (!s) return;
      const key = `${s.id}:${s.metadata_source?.provider || ""}:${s.metadata_source?.id || ""}`;
      if (key === this.shown) return;
      this.shown = key;
      this.items = [];
      try {
        const r = await MangaVibe.api(`/api/plugins/anilist-recommendations/api/series/${s.id}`);
        if (this.shown === key) this.items = r.items;
      } catch {
        // AniList or the plugin can't answer: the page just shows no recommendations
      }
    },

    sub(r) {
      return [MangaVibe.ui.kind({ format: r.format, country_of_origin: r.country }) || "Manga", r.year]
        .filter(Boolean).join(" · ");
    },
  }));
});
