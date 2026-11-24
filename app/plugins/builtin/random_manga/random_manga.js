/* Random manga (built-in plugin, see __init__.py): the Random series buttons (library page, Sidebar)
   and Pick another on the picked series' page. Uses window.MangaVibe from app.js. */
MangaVibe.onStart(() => {
  const MV = window.MangaVibe;
  const API = "/api/plugins/random-manga/api/pick";

  Alpine.store("randomManga", {
    picked: null,  // the series the last pick opened: its page shows Pick another
    recent: [],    // picked lately: not picked again while others are left
    busy: false,
    // pick a series and open its page; `current` (the page shown) is never picked. The answer:
    // { series: { id, title } } or { series: null, message } (also shown as a toast)
    async pick(current = null) {
      if (this.busy) return null;
      this.busy = true;
      try {
        const p = MV.listScope();  // e.g. the library picked
        const avoid = this.recent.filter((id) => id !== current);
        if (current) p.set("current", current);
        if (avoid.length) p.set("recent", avoid.join(","));
        const r = await MV.api(`${API}?${p}`);
        if (!r.series) {
          MV.toast(r.message, 5000);
          return r;
        }
        this.picked = r.series.id;
        this.recent = [r.series.id, ...this.recent.filter((id) => id !== r.series.id)].slice(0, 50);
        location.hash = `#/series/${r.series.id}`;
        return r;
      } catch (e) {
        MV.toast(e.message, 6000, "error");
        return null;
      } finally {
        this.busy = false;
      }
    },
  });

  MV.on("random-manga", "stop", () => { Alpine.store("randomManga").picked = null; });

  // for an assistant or voice control (the Remote control plugin, Plugin API 13)
  MV.screenCommand?.("random-manga", "pick", {
    description: "Open a series picked at random from the library shown (read and dropped ones left out by default)",
  }, async () => {
    const r = await Alpine.store("randomManga").pick();
    if (!r) throw new Error("Could not pick a series");
    return r.series ? { opened: r.series } : { opened: null, why: r.message };
  });
});
