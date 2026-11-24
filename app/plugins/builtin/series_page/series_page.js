/* The built-in series page (view:series, Plugin API 18): seriesPage() is the component its markup
   (templates/views/series.html) uses. It keeps only what's open on the screen (the ⋯ menu, the dialogs,
   "show more", the number form); the series, its books and every action are MangaVibe.series. It uses
   nothing else from the app but window.MangaVibe, so a page of your own can do the same. */
MangaVibe.onStart(() => {
  const c = () => MangaVibe.series;

  Alpine.data("seriesPage", () => {
    const page = {
      // what's open on screen
      menuOpen: false, readerOpen: false,
      showAllTags: false, showSpoilers: false, descLong: false, descOpen: false,
      editing: null, form: {}, copied: false,
      del: { open: false, info: null, busy: false },
      seen: null,  // the series shown last: another one starts with "show more" and the tags closed again

      init() {
        this.$watch("$store.series.loads", async () => {
          const s = c().s;
          if (!s) return;
          if (this.seen !== s.id) {
            this.seen = s.id;
            Object.assign(this, { showAllTags: false, showSpoilers: false, descOpen: false, menuOpen: false });
          }
          await this.$nextTick();
          const d = this.$refs.desc;  // scrollHeight is the full text height, even while it's shortened
          this.descLong = !!d && d.scrollHeight > parseFloat(getComputedStyle(d).lineHeight) * 6.5;
        });
      },

      get visibleTags() {
        return c().tagList.filter((tag, i) => (this.showAllTags || i < 12) && (!tag.spoiler || this.showSpoilers));
      },
      get spoilerCount() { return c().tagList.filter((tag) => tag.spoiler).length; },
      tagUrl(name) { return MangaVibe.ui.libraryUrl({ genre: name }); },
      // the Author row: the writers and artists (the API's `creators`, without translators and other
      // staff), each name once with its roles ("OTO Hisamu" as Author and Artist: one link)
      get authors() {
        return (c().s?.creators || []).map((a) => ({ name: a.name, roles: a.roles.join(", ") }));
      },
      authorUrl(name) { return MangaVibe.ui.libraryUrl({ author: name }); },

      // the actions, as the markup calls them
      back() { c().back(); },
      setStatus(v) { c().setStatus(v); },
      setChoice(choice, v) { c().setChoice(choice, v.slice(1)); },  // options carry a "=", so one may be ""
      runAction(a) { c().runAction(a); },
      fixMatch() { c().fixMatch(); },
      editInfo(tab) { c().edit(tab); },
      toggleFav() { c().toggleFavorite(); },
      refreshMeta() { c().refreshMetadata(); },
      async setReader(v) { await c().setReadingMode(v); this.readerOpen = false; },
      setTab(tab) { c().setTab(tab); },
      rescan() { c().rescan(); },
      markAll(read) { c().markAll(read); },
      toggleBook(b) { c().toggleBook(b); },
      readUpTo(b) { c().readUpTo(b); },
      unreadBefore(i) { return c().unreadBefore(i); },
      bookState(b) { return c().bookState(b); },
      progressPct(b) { return c().bookProgress(b); },

      // the Files tab: a file's own numbers
      startEdit(b) { this.form = c().numbersForm(b); this.editing = b.id; },
      async saveEdit(b) { if (await c().saveNumbers(b, this.form)) this.editing = null; },
      resetEdit(b) { this.editing = null; c().resetNumbers(b); },

      async copyPath() {
        if (!(await c().copyPath())) return;
        this.copied = true;
        clearTimeout(this.copiedTimer);
        this.copiedTimer = setTimeout(() => { this.copied = false; }, 1600);
      },

      // delete the series and its folder: what it would delete first, then yes or no
      async openDelete() {
        this.del = { open: true, info: null, busy: false };
        const info = await c().deleteInfo();
        if (info) this.del.info = info; else this.del.open = false;
      },
      closeDelete() { if (!this.del.busy) this.del.open = false; },
      async confirmDelete() {
        this.del.busy = true;
        await c().delete();
        this.del = { open: false, info: null, busy: false };
      },

      sizeText: MangaVibe.ui.fileSize,
      mb(n) { return MangaVibe.fmtNum(n / 1048576, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " MB"; },
      n: MangaVibe.ui.number,
    };
    // what the markup reads from MangaVibe.series under the same names
    for (const name of ["s", "books", "tab", "error", "alt", "facts", "details", "genres", "tagList", "matchLabel",
                        "statusLabels", "readerModes", "readerModeLabel", "autoHint", "actionBusy", "choiceBusy",
                        "rescanning", "refreshing"]) {
      Object.defineProperty(page, name, { get: () => c()[name], enumerable: true });
    }
    return page;
  });
});
