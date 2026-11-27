/* Saved searches (built-in plugin, see __init__.py): the Saved button, the Save search button in the
   row of filters, and the sheet listing them. Uses window.MangaVibe from app.js. */
MangaVibe.onStart(() => {
  const MV = window.MangaVibe;
  const API = "/api/plugins/saved-searches/api/searches";
  const same = (query) => MV.filterQuery(new URLSearchParams(query));

  Alpine.store("savedSearches", {
    items: [],
    async load() { this.items = await MV.api(API).catch(() => this.items); },
    // filters on: they can be saved
    get canSave() { return !!MV.filterQuery(MV.libraryFilters()); },
    // the saved search the library page shows right now, if any
    get current() {
      const now = MV.filterQuery(MV.libraryFilters());
      return now ? this.items.find((x) => same(x.query) === now) || null : null;
    },
    async save() {
      const p = MV.libraryFilters(), query = MV.filterQuery(p);
      const name = await MV.prompt({ title: MV.t("Save this search"), text: MV.filterLabels(query).join(", "),
                                     label: MV.t("Name"), value: p.getAll("genre").find(Boolean) || "", confirm: MV.t("Save") });
      if (!name?.trim()) return;
      try {
        const r = await MV.api(API, { method: "POST", body: { name, query } });
        await this.load();
        MV.toast(MV.t("Saved as “{name}”. The Saved button brings it back, in any library.", { name: r.name }), 5000);
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
    async remove(x) {
      if (!await MV.confirm({ title: MV.t("Delete the saved search “{name}”?", { name: x.name }), danger: true,
                              confirm: MV.t("Delete") })) return;
      await MV.api(`${API}/${x.id}`, { method: "DELETE" }).catch((e) => MV.toast(e.message, 6000, "error"));
      await this.load();
    },
  });
  Alpine.store("savedSearches").load();

  // for an assistant or voice control (the Remote control plugin, Plugin API 13)
  MV.screenCommand?.("saved-searches", "show", {
    description: "Show the library with one of your saved searches (by its name; saved_searches__list lists them)",
    params: { name: { type: "string" } }, required: ["name"],
  }, async ({ name }) => {
    const store = Alpine.store("savedSearches");
    await store.load();
    const want = String(name || "").trim().toLowerCase();
    const x = store.items.find((i) => i.name.toLowerCase() === want)
      || store.items.find((i) => i.name.toLowerCase().includes(want));
    if (!x) throw new Error(`No saved search "${name}": ${store.items.map((i) => i.name).join(", ") || "none saved"}`);
    MV.showLibrary(new URLSearchParams(x.query));
    return { showing: x.name, filters: MV.filterLabels(x.query) };
  });

  Alpine.data("savedSearchesSheet", () => ({
    get store() { return this.$store.savedSearches; },
    isOn(x) { return this.store.current?.id === x.id; },
    parts: MV.filterLabels,
    init() { this.$watch("$store.app.sheet", (s) => s === "saved-searches" && this.store.load()); },
    pick(x) {  // like a menu: pick and close; picking the one shown clears it
      MV.showLibrary(this.isOn(x) ? new URLSearchParams() : new URLSearchParams(x.query));
      this.$store.app.sheet = null;
    },
  }));
});
