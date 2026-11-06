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
    // after unlocking, locking, adding or removing a library: everything shown may change
    async changed() {
      await this.load().catch(() => {});
      MV.listsChanged();
    },
    openUnlock(id, onDone) { this.unlock = { open: true, id, onDone }; },
    async add() {
      const name = prompt("Name of the new library, e.g. Comics");
      if (!name?.trim()) return null;
      try {
        const r = await MV.api(`${API}/shelves`, { method: "POST", body: { name } });
        await this.load();
        MV.toast(`${r.name} added. Move series to it with Edit, or several at once: hold a card to select them. `
          + "A whole folder: Settings → Library folders.", 9000);
        return r;
      } catch (e) { MV.toast(e.message, 6000, "error"); return null; }
    },
    async addAndPick() {
      const r = await this.add();
      if (r) this.set(r.id);
    },
    async lockNow() {
      await MV.api(`${API}/shelves/lock`, { method: "POST" }).catch(() => {});
      MV.toast("Locked. The password is needed again.");
      await this.changed();
    },
    movedText(n, id) { return `Moved ${n} series to ${id ? this.name(id) : "their folders' libraries"}`; },
    // Settings → Library folders: the library a folder's series show in
    folderShelf(folderId) { return this.list.find((s) => s.folders.some((f) => f.id === folderId))?.id ?? null; },
    async setFolder(folder, v) {
      try {
        await MV.api(`${API}/folders/${folder.id}`, { method: "PUT", body: { shelf_id: +v } });
        MV.toast(`Its series show in ${this.name(+v)} now`);
      } catch (e) { MV.toast(e.message, 6000, "error"); }
      await this.changed();
    },
    // the series Edit dialog's choices: [value, label]; "0" follows the folder
    get editOptions() {
      const own = this.edit.info?.folder_shelf?.name;
      return [["0", own ? `Like its folder: ${own}` : "Like its folder"],
        ...this.list.map((x) => [String(x.id), x.name + (x.locked ? " 🔒" : "")])];
    },
  });
  const store = Alpine.store("libraries");

  /* the password dialog of a locked library (store.openUnlock) */
  Alpine.data("libraryUnlock", () => ({
    password: "", error: "", busy: false,
    get name() { return store.name(store.unlock.id) || "This library"; },
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
    async patch(s, body, msg = "Saved") {
      try {
        await MV.api(`${API}/shelves/${s.id}`, { method: "PATCH", body });
        MV.toast(msg);
      } catch (e) { MV.toast(e.message, 6000, "error"); }
      await store.load().catch(() => {});
    },
    rename(s, v) {
      v = v.trim();
      if (v && v !== s.name) this.patch(s, { name: v }, `Renamed to ${v}`);
    },
    async remove(s) {
      if (s.locked) return store.openUnlock(s.id, () => this.remove(store.list.find((x) => x.id === s.id)));
      if (store.list.length < 2) return MV.toast("This is the only library. Add another one first.", 5000);
      if (!confirm(`Delete the library "${s.name}"?\n\nNo series or files are deleted: its folders and the series `
        + "moved to it show in another library.")) return;
      try {
        const r = await MV.api(`${API}/shelves/${s.id}`, { method: "DELETE" });
        MV.toast(`Deleted. Its series are in ${r.moved_to.name} now.`, 5000);
        await store.changed();
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
    openPassword(s) { this.pw = { id: s.id, current: "", password: "", again: "" }; },
    async savePassword(s, remove = false) {
      const f = this.pw;
      if (!remove && !f.password) return MV.toast("Type the new password");
      if (!remove && f.password !== f.again) return MV.toast("The two new passwords are different");
      try {
        await MV.api(`${API}/shelves/${s.id}/password`, { method: "PUT",
          body: { current: f.current, password: remove ? "" : f.password } });
        this.pw = null;
        MV.toast(remove ? "Password removed" : "Password saved. This browser stays unlocked until “Lock now” or a new tab.", 6000);
        await store.changed();
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
    async lock(s) {
      await MV.api(`${API}/shelves/${s.id}/lock`, { method: "POST" }).catch(() => {});
      MV.toast(`${s.name} is locked`);
      await store.changed();
    },
    folders(s) { return s.folders.map((f) => f.root_path).join(", ") || "No folders: only series moved here by hand"; },
  }));

  /* ---- what the app asks (MangaVibe.on): only while the plugin is on ---- */
  MV.on(ID, "start", async () => {
    await lockOnNewTab();
    MV.settingsPage(ID, "shelves", "Libraries", "📚", 0);
    await store.load().catch(() => {});
  });
  MV.on(ID, "stop", () => {
    Object.assign(store, { list: [], moveOpen: false });
    store.unlock.open = false;
  });
  MV.on(ID, "settings-open", () => store.load().catch(() => {}));  // the series counts
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
    return [["Library", info.shelf.name
      + (info.shelf_override ? ` (moved here; its folder's is ${info.folder_shelf?.name || "gone"})` : "")]];
  });
  // the library page with nothing to show: say why
  MV.on(ID, "empty-library", () => {
    const list = store.list;
    // every library is locked (e.g. the only one has a password): nothing can be shown until one is opened
    if (list.length && list.every((s) => s.locked)) {
      const one = list.length === 1;
      return `${one ? list[0].name + " is" : "Every library is"} locked. Tap ${one ? "its" : "a"} tab above to unlock it.`;
    }
    if (store.picked && list.length > 1) {
      return `Nothing in ${store.name(store.picked)} yet. Move series here with Edit, or select several `
        + "(hold a card) and use ⋯ → Move to library. A whole folder: Settings → Library folders.";
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
    if (target?.locked) return { leave: true, message: `Saved. It's in ${target.name} now, which is locked.` };
    return { message: `Saved. It shows in ${target?.name || "its folder's library"} now.` };
  });
});
