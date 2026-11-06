/* Accounts (built-in plugin, see __init__.py): the login (and, the first time, making the admin), the
   account menu in the top bar, and Settings → Accounts for admins. Uses window.MangaVibe (app.js).
   Logging in or out reloads the page: everything on it is that person's. */
MangaVibe.onStart(() => {
  const MV = MangaVibe, ID = "accounts", API = "/api/plugins/accounts/api";

  Alpine.store("accounts", {
    me: null,          // the logged-in account { id, name, admin }, or null
    setup: false,      // no account yet: the login makes the first admin
    gate: false,       // the login covers the page
    list: [],          // Settings → Accounts (admins)
    menuOpen: false,
    pwOpen: false,     // the change-my-password dialog
    async loadMe() {
      const r = await MV.api(`${API}/me`);
      Object.assign(this, { me: r.user, setup: r.setup });
    },
    async load() {
      if (this.me?.admin) this.list = await MV.api(`${API}/accounts`).catch(() => this.list);
    },
    async logout() {
      await MV.api(`${API}/logout`, { method: "POST" }).catch(() => {});
      location.reload();
    },
  });
  const store = Alpine.store("accounts");

  /* the login, or the first admin */
  Alpine.data("accountsLogin", () => ({
    name: "", password: "", again: "", error: "", busy: false,
    init() { this.$watch("$store.accounts.gate", (on) => on && this.$nextTick(() => this.$refs.name?.focus())); },
    async submit() {
      if (this.busy) return;
      if (store.setup && this.password !== this.again) return (this.error = "The two passwords are different");
      this.busy = true;
      this.error = "";
      try {
        await MV.api(`${API}/${store.setup ? "setup" : "login"}`, { method: "POST",
          body: { name: this.name, password: this.password } });
        location.reload();
      } catch (e) {
        this.error = e.message;
        this.password = this.again = "";
        this.busy = false;
      }
    },
  }));

  /* the account menu's Change password */
  Alpine.data("accountsPassword", () => ({
    current: "", password: "", again: "",
    init() { this.$watch("$store.accounts.pwOpen", (on) => on && Object.assign(this, { current: "", password: "", again: "" })); },
    async save() {
      if (this.password !== this.again) return MV.toast("The two new passwords are different");
      try {
        await MV.api(`${API}/me/password`, { method: "PUT", body: { current: this.current, password: this.password } });
        store.pwOpen = false;
        MV.toast("Password changed. Your other devices need it now.", 5000);
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
  }));

  /* Settings → Accounts (admins) */
  Alpine.data("accountsSettings", () => ({
    get store() { return store; },
    add: { name: "", password: "", admin: false },
    async create() {
      try {
        const a = await MV.api(`${API}/accounts`, { method: "POST", body: this.add });
        this.add = { name: "", password: "", admin: false };
        await store.load();
        MV.toast(`${a.name} can log in now.`);
      } catch (e) { MV.toast(e.message, 6000, "error"); }
    },
    async patch(a, body, msg) {
      try {
        await MV.api(`${API}/accounts/${a.id}`, { method: "PATCH", body });
        MV.toast(msg);
      } catch (e) { MV.toast(e.message, 6000, "error"); }
      await store.load();
    },
    rename(a, v) {
      v = v.trim();
      if (v && v !== a.name) this.patch(a, { name: v }, `Renamed to ${v}`);
    },
    newPassword(a) {
      const pw = prompt(`New password for ${a.name} (at least 4 characters)`);
      if (pw) this.patch(a, { password: pw }, `${a.name}'s password is changed; they're logged out everywhere.`);
    },
    async remove(a) {
      if (!confirm(`Delete the account "${a.name}"?\n\nIts reading progress, favorites and saved searches are `
        + "deleted too. The series and files stay.")) return;
      try {
        await MV.api(`${API}/accounts/${a.id}`, { method: "DELETE" });
        MV.toast(`${a.name} is deleted`);
      } catch (e) { MV.toast(e.message, 6000, "error"); }
      await store.load();
    },
  }));

  /* ---- what the app asks (MangaVibe.on): only while the plugin is on ---- */
  MV.on(ID, "start", async () => {
    await store.loadMe().catch(() => {});
    if (!store.me) {
      // nothing else loads until someone logs in, which reloads the page
      store.gate = true;
      return new Promise(() => {});
    }
    if (store.me.admin) MV.settingsPage(ID, "accounts", "Accounts", "👥", 1);
  });
  // switched off: everyone is the owner again, with the first admin's reading
  MV.on(ID, "stop", () => location.reload());
  MV.on(ID, "settings-open", () => store.load());
  // logged out meanwhile (another device changed the password, or it ran out): log in again
  window.addEventListener("mv-login-needed", () => { if (store.me) location.reload(); });
});
