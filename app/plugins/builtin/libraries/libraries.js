/* Libraries (built-in plugin, see __init__.py): Manga, Comics... The home screen's tabs pick one (or
   All); the library grid, filters, Continue reading and Needs attention follow it (shelf=<id> on their
   API calls, MangaVibe.setListScope). A library with a password asks for it first; the server then lets
   this browser in for a while (a cookie), until "Lock now" or a new tab. Uses window.MangaVibe (app.js). */
MangaVibe.onStart(() => {
  const MV = MangaVibe, ID = "libraries", API = "/api/plugins/libraries/api";
  const PICK_KEY = "shelf";  // localStorage: the library picked on the home screen
  const saved = () => { try { return +localStorage.getItem(PICK_KEY) || 0; } catch { return 0; } };

  /* A new tab starts with every library locked again ("until the tab closes"). sessionStorage lives
     as long as the tab, so a reload doesn't lock. */
  async function lockOnNewTab() {
    try {
      if (sessionStorage.getItem("tab")) return;
      sessionStorage.setItem("tab", "1");
    } catch { return; }
    await MV.api(`${API}/shelves/lock`, { method: "POST" }).catch(() => {});
  }

  Alpine.store("libraries", {
    list: [],                // GET /shelves: [{ id, name, reader_mode, has_password, locked, series, folders }]
    picked: saved(),         // the library the home screen shows: its id, or 0 for All
    unlock: { open: false, id: null, onDone: null },  // the password dialog
    moveOpen: false,         // the multi-select's Move dialog
    moveUrl: `${API}/move`,
    edit: { value: "0", orig: "0", info: null },  // the series Edit dialog's Library field ("0": its folder's)

    name(id) { return this.list.find((s) => s.id === id)?.name || ""; },
    get showAll() { return this.list.length > 1; },
    // unlocked libraries with a password: "Lock now" locks them again
    get anyOpen() { return this.list.some((s) => s.has_password && !s.locked); },

    /* Load the libraries, and keep the picked one if it can be shown: else the only library there is
       (with one library, "All" would be the same), else All. A locked one isn't kept. */
    async load() {
      this.list = await MV.api(`${API}/shelves`);
      const open = this.list.filter((s) => !s.locked);
      let id = open.some((s) => s.id === this.picked) ? this.picked : 0;
      if (this.list.length === 1 && open.length === 1) id = open[0].id;
      this.set(id, false);  // the owner's own pick stays saved for when it's unlocked again
    },
    set(id, remember = true) {
      if (remember) try { localStorage.setItem(PICK_KEY, String(id)); } catch {}
      this.picked = id;
      MV.setListScope(ID, { shelf: id || null });
    },
    pick(id) {
      if (this.list.find((s) => s.id === id)?.locked) return this.openUnlock(id, () => this.set(id));
      this.set(id);
    },
    /* A library picked anywhere but the library and Continue (Home's tabs and cards, the sidebar on another
       page): show it in the Library, once it's open (a locked one asks for its password first). On the
       library and Continue, picking only switches what they show. */
    choose(id) {
      if (["library", "continue"].includes(Alpine.store("app").view)) return this.pick(id);
      const open = () => { this.set(id); location.hash = "#/library"; };
      if (this.list.find((s) => s.id === id)?.locked) return this.openUnlock(id, open);
      open();
    },
    // after unlocking, locking, adding or removing a library: everything shown may change
    async changed() {
      await this.load().catch(() => {});
      MV.listsChanged();
    },
    openUnlock(id, onDone) { this.unlock = { open: true, id, onDone }; },
    async add() {
      const name = prompt(MV.t("Name of the new library, e.g. Comics"));
      if (!name?.trim()) return null;
      try {
        const r = await MV.api(`${API}/shelves`, { method: "POST", body: { name } });
        await this.load();
        MV.toast(MV.t("{name} added. Move series to it with Edit, or several at once: hold a card to select them. A whole folder: Settings → Library folders.",
                      { name: r.name }), 9000);
        return r;
      } catch (e) { MV.toast(e.message, 6000, "error"); return null; }
    },
    async addAndPick() {
      const r = await this.add();
      if (r) this.set(r.id);
    },
    async lockNow() {
      await MV.api(`${API}/shelves/lock`, { method: "POST" }).catch(() => {});
      MV.toast(MV.t("Locked. The password is needed again."));
      await this.changed();
    },
    movedText(n, id) {
      return id ? MV.tn("Moved {n} series to {library}", "Moved {n} series to {library}", n, { library: this.name(id) })
        : MV.tn("Moved {n} series to their folders' libraries", "Moved {n} series to their folders' libraries", n);
    },
    // Settings → Library folders: the library a folder's series show in
    folderShelf(folderId) { return this.list.find((s) => s.folders.some((f) => f.id === folderId))?.id ?? null; },
    async setFolder(folder, v) {
      try {
        await MV.api(`${API}/folders/${folder.id}`, { method: "PUT", body: { shelf_id: +v } });
        MV.toast(MV.t("Its series show in {library} now", { library: this.name(+v) }));
      } catch (e) { MV.toast(e.message, 6000, "error"); }
      await this.changed();
    },
    // with accounts: which libraries each account sees ({ "<id>": [shelf ids] }; missing: all). Admins only.
    access: {},
    async loadAccess() {
      if (Alpine.store("app").admin) this.access = await MV.api(`${API}/access`).catch(() => this.access);
    },
    sees(accountId, shelfId) {
      const ids = this.access[String(accountId)];
      return !ids || ids.includes(shelfId);
    },
    async setSees(account, shelfId, on) {
      const all = this.list.map((s) => s.id);
      const now = new Set(this.access[String(account.id)] || all);
      on ? now.add(shelfId) : now.delete(shelfId);
      const shelves = all.every((id) => now.has(id)) ? null : [...now];  // all ticked: no limit
      try {
        await MV.api(`${API}/access/${account.id}`, { method: "PUT", body: { shelves } });
        MV.toast(on ? MV.t("{account} sees {library} now", { account: account.name, library: this.name(shelfId) })
          : MV.t("{account} doesn't see {library} now", { account: account.name, library: this.name(shelfId) }));
      } catch (e) { MV.toast(e.message, 6000, "error"); }
      await this.loadAccess();
    },
    // the series Edit dialog's choices: [value, label]; "0" follows the folder
    get editOptions() {
      const own = this.edit.info?.folder_shelf?.name;
      return [["0", own ? MV.t("Like its folder: {library}", { library: own }) : MV.t("Like its folder")],
        ...this.list.map((x) => [String(x.id), x.name + (x.locked ? " 🔒" : "")])];
    },
  });
  const store = Alpine.store("libraries");

  // for an assistant or voice control (the Remote control plugin, Plugin API 13): the home screen's tabs
  MV.screenCommand?.(ID, "list", {
    description: "The libraries (Manga, Comics...) this screen can show, which one it shows, and which are locked",
  }, async () => {
    await store.load();
    return { showing: store.picked ? store.name(store.picked) : "All",
      libraries: store.list.map((s) => ({ id: s.id, name: s.name, locked: s.locked, series: s.series })),
      hint: "library_search takes more: {shelf: <id>} for one library" };
  });
  MV.screenCommand?.(ID, "pick", {
    description: "Show one library on the home screen (its name or id; All for every library). A locked one "
      + "asks for its password on the screen",
    params: { library: { type: "string" } }, required: ["library"],
  }, async ({ library }) => {
    await store.load();
    const want = String(library ?? "").trim().toLowerCase();
    if (want === "all" || want === "0") { store.set(0); return { showing: "All" }; }
    const s = store.list.find((x) => String(x.id) === want || x.name.toLowerCase() === want)
      || store.list.find((x) => x.name.toLowerCase().includes(want));
    if (!s) throw new Error(`No library "${library}": ${store.list.map((x) => x.name).join(", ")}`);
    store.pick(s.id);
    return s.locked ? { asking_for_password: s.name } : { showing: s.name };
  });

  /* the password dialog of a locked library (store.openUnlock) */
  Alpine.data("libraryUnlock", () => ({
    password: "", error: "", busy: false,
    get name() { return store.name(store.unlock.id) || MV.t("This library"); },
    init() {
      this.$watch("$store.libraries.unlock.open", (open) => {
        if (!open) return;
        Object.assign(this, { password: "", error: "", busy: false });
        this.$nextTick(() => this.$refs.pw?.focus());
      });
    },
    close() { store.unlock.open = false; },
    async submit() {
      if (this.busy || !this.password) return;
      const u = store.unlock;
      this.busy = true;
      this.error = "";
      try {
        await MV.api(`${API}/shelves/${u.id}/unlock`, { method: "POST", body: { password: this.password } });
        this.close();
        await store.changed();
        u.onDone?.();
      } catch (e) {
        this.error = e.message;
        this.password = "";
      } finally { this.busy = false; }
    },
  }));

  /* Settings → Libraries */
  Alpine.data("librarySettings", () => ({
    get store() { return store; },
    pw: null,  // the password form of one library: { id, current, password, again }
    async patch(s, body, msg = MV.t("Saved")) {
      try {
        await MV.api(`${API}/shelves/${s.id}`, { method: "PATCH", body });
        MV.toast(msg);
      } catch (e) { MV.toast(e.message, 6000, "error"); }
      await store.load().catch(() => {});
    },
    rename(s, v) {
      v = v.trim();
      if (v && v !== s.name) this.patch(s, { name: v }, MV.t("Renamed to {name}", { name: v }));
    },
    async remove(s) {
      if (s.locked) return store.openUnlock(s.id, () => this.remove(store.list.find((x) => x.id === s.id)));
      if (store.list.length < 2) return MV.toast(MV.t("This is the only library. Add another one first."), 5000);
      if (!confirm(MV.t("Delete the library “{name}”?", { name: s.name }) + "\n\n"
        + MV.t("No series or files are deleted: its folders and the series moved to it show in another library."))) return;
      try {
        const r = await MV.api(`${API}/shelves/${s.id}`, { method: "DELETE" });
        MV.toast(MV.t("Deleted. Its series are in {library} now.", { library: r.moved_to.name }), 5000);
        await store.changed();
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
    openPassword(s) { this.pw = { id: s.id, current: "", password: "", again: "" }; },
    async savePassword(s, remove = false) {
      const f = this.pw;
      if (!remove && !f.password) return MV.toast(MV.t("Type the new password"));
      if (!remove && f.password !== f.again) return MV.toast(MV.t("The two new passwords are different"));
      try {
        await MV.api(`${API}/shelves/${s.id}/password`, { method: "PUT",
          body: { current: f.current, password: remove ? "" : f.password } });
        this.pw = null;
        MV.toast(remove ? MV.t("Password removed")
          : MV.t("Password saved. This browser stays unlocked until “Lock now” or a new tab."), 6000);
        await store.changed();
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
    async lock(s) {
      await MV.api(`${API}/shelves/${s.id}/lock`, { method: "POST" }).catch(() => {});
      MV.toast(MV.t("{library} is locked", { library: s.name }));
      await store.changed();
    },
    folders(s) { return s.folders.map((f) => f.root_path).join(", ") || MV.t("No folders: only series moved here by hand"); },
  }));

  /* ---- what the app asks (MangaVibe.on): only while the plugin is on ---- */
  MV.on(ID, "start", async () => {
    await lockOnNewTab();
    MV.settingsPage(ID, "shelves", MV.t("Libraries"), "books", 0);
    await store.load().catch(() => {});
  });
  MV.on(ID, "stop", () => {
    Object.assign(store, { list: [], moveOpen: false });
    store.unlock.open = false;
  });
  // the series counts; with accounts, who sees which
  MV.on(ID, "settings-open", () => Promise.all([store.load().catch(() => {}), store.loadAccess()]));
  MV.on(ID, "folders-changed", () => store.changed());  // a folder gets its library, or its library goes
  // a locked library's series, book or page: ask for the password, then try again
  MV.on(ID, "denied", (detail, retry) => {
    if (!detail?.shelf) return null;
    store.openUnlock(detail.shelf, retry);
    return true;
  });
  // the series page: which library it shows in, once there is more than one
  MV.on(ID, "series-info", (s) => {
    const info = s.plugin_data?.libraries;
    if (!info?.shelf || store.list.length < 2) return null;
    return [[MV.t("Library"), info.shelf.name
      + (info.shelf_override ? " " + MV.t("(moved here; its folder's is {library})", { library: info.folder_shelf?.name || MV.t("gone") }) : "")]];
  });
  // Home's Libraries row (#291): a library's card shows that library in the Library (a locked one asks first)
  MV.on(ID, "home-tile", (item) => {
    store.choose(+item.id);
    return true;
  });
  // the library page with nothing to show: say why
  MV.on(ID, "empty-library", () => {
    const list = store.list;
    // every library is locked (e.g. the only one has a password): nothing can be shown until one is opened
    if (list.length && list.every((s) => s.locked)) {
      return list.length === 1 ? MV.t("{library} is locked. Tap its tab above to unlock it.", { library: list[0].name })
        : MV.t("Every library is locked. Tap a tab above to unlock it.");
    }
    if (store.picked && list.length > 1) {
      return MV.t("Nothing in {library} yet. Move series here with Edit, or select several (hold a card) and use ⋯ → Move to library. A whole folder: Settings → Library folders.",
                  { library: store.name(store.picked) });
    }
    return null;
  });
  // the series Edit dialog's Library field
  MV.on(ID, "edit-open", (s) => {
    const info = s.plugin_data?.libraries || null;
    const value = String(info?.shelf_override ? info.shelf?.id || 0 : 0);
    store.edit = { value, orig: value, info };
  });
  MV.on(ID, "edit-changed", () => store.edit.value !== store.edit.orig);
  MV.on(ID, "edit-save", async (s) => {
    if (store.edit.value === store.edit.orig) return null;
    const id = +store.edit.value;
    // not metadata: which library it shows in (survives rescans and refreshes)
    await MV.api(`${API}/series/${s.id}`, { method: "PUT", body: { shelf_id: id } });
    await store.load().catch(() => {});  // the series counts
    const target = store.list.find((x) => x.id === id);
    if (target?.locked) return { leave: true, message: MV.t("Saved. It's in {library} now, which is locked.", { library: target.name }) };
    return { message: target ? MV.t("Saved. It shows in {library} now.", { library: target.name })
      : MV.t("Saved. It shows in its folder's library now.") };
  });
});
