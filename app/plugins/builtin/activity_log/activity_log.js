/* Activity log (built-in plugin, see __init__.py): Settings → Activity, for admins. Who did what, and when,
   newest first, grouped by day; filters (person, kind, dates, words); more as you scroll. Each entry opens
   (a <details>) to show what was kept with it. Uses window.MangaVibe (app.js); the words: MV.t() (#263). */
MangaVibe.onStart(() => {
  const MV = MangaVibe, ID = "activity-log", API = "/api/plugins/activity-log/api", PAGE = 50;

  // what happened, without who: the person (or System) is shown before it. `v` is the entry's details with
  // name (the summary: what it's about) and n (a count). A kind this page doesn't know (a plugin's own)
  // shows as its kind and summary.
  const SAID = {
    "account.login": () => MV.t("Logged in"),
    "account.logout": () => MV.t("Logged out"),
    "account.login_failed": (v) => MV.t("Failed to log in as {name}", v),
    "account.added": (v) => MV.t("Added the account {name}", v),
    "account.removed": (v) => MV.t("Deleted the account {name}", v),
    "account.renamed": (v) => MV.t("Renamed the account {old} to {new}", v),
    "account.admin_given": (v) => MV.t("Made {name} an admin", v),
    "account.admin_taken": (v) => MV.t("Took admin rights from {name}", v),
    "account.password_changed": () => MV.t("Changed their password"),
    "account.password_reset": (v) => MV.t("Set a new password for {name}", v),
    "account.access_changed": (v) => MV.t("Changed which libraries {name} sees", v),
    "account.key_created": (v) => MV.t("Made a key for {name}", v),
    "account.key_removed": (v) => MV.t("Removed a key of {name}", v),
    "folder.added": (v) => MV.t("Added the folder {name}", v),
    "folder.removed": (v) => MV.t("Removed the folder {name}", v),
    "folder.changed": (v) => MV.t("Changed the folder {name}", v),
    "scan.started": (v) => (v.name ? MV.t("Started a scan of {name}", v) : MV.t("Started a scan of every folder")),
    "scan.finished": (v) => (v.name ? MV.t("Scan of {name} finished", v) : MV.t("Scan finished")),
    "series.deleted": (v) => MV.t("Deleted the series {name} and its folder", v),
    "series.moved": (v) => (v.count > 1
      ? (v.to ? MV.tn("Moved {n} series to {to}", "Moved {n} series to {to}", v.count, v)
        : MV.tn("Moved {n} series back to their folders' libraries", "Moved {n} series back to their folders' libraries", v.count, v))
      : (v.to ? MV.t("Moved {name} to {to}", v) : MV.t("Moved {name} back to its folder's library", v))),
    "metadata.changed": (v) => MV.t("Edited {name}", v),
    "metadata.matched": (v) => MV.t("Matched {name}", v),
    "metadata.unmatched": (v) => MV.t("Unmatched {name}", v),
    "metadata.refetched": (v) => (v.count
      ? MV.tn("Refreshed the metadata of {n} series", "Refreshed the metadata of {n} series", v.count)
      : MV.t("Refreshed the metadata of {name}", v)),
    "library.added": (v) => MV.t("Added the library {name}", v),
    "library.changed": (v) => MV.t("Changed the library {name}", v),
    "library.removed": (v) => MV.t("Deleted the library {name}", v),
    "library.password_set": (v) => MV.t("Set a password on the library {name}", v),
    "library.password_removed": (v) => MV.t("Took the password off the library {name}", v),
    "library.unlock_failed": (v) => MV.t("Failed to unlock the library {name}", v),
    "plugin.installed": (v) => MV.t("Installed {name} {to}", v),
    "plugin.enabled": (v) => MV.t("Switched {name} on", v),
    "plugin.disabled": (v) => MV.t("Switched {name} off", v),
    "plugin.updated": (v) => MV.t("Updated {name} from {from} to {to}", v),
    "plugin.restored": (v) => MV.t("Went back to {name} {to}", v),
    "plugin.failed": (v) => MV.t("{name} failed to start", v),
    "plugin_source.added": (v) => MV.t("Added the plugin source {name}", v),
    "plugin_source.removed": (v) => MV.t("Removed the plugin source {name}", v),
    "safe_mode.on": () => MV.t("Opened the app in safe mode"),
    "safe_mode.off": () => MV.t("Left safe mode"),
    "setting.changed": (v) => (v.secret ? (v.cleared ? MV.t("Cleared {name}", v) : MV.t("Changed {name}", v))
      : MV.t("Changed {name} from {old} to {new}", v)),
    "backup.exported": () => MV.t("Exported a backup"),
    "backup.imported": (v) => (v.name ? MV.t("Imported a backup made {name}", v) : MV.t("Imported a backup")),
    "update.started": (v) => (v.rollback ? MV.t("Started going back to {to}", v) : MV.t("Started an update to {to}", v)),
    "update.finished": (v) => MV.t("MangaVibe was updated from {from} to {to}", v),
    "update.failed": () => MV.t("An update failed"),
  };
  // the page's kinds filter: the groups (activity.GROUPS)
  const GROUPS = [["accounts", MV.t("Accounts")], ["library", MV.t("Library")], ["plugins", MV.t("Plugins")],
    ["settings", MV.t("Settings")], ["app", MV.t("The app")], ["other", MV.t("Other")]];
  // where an entry's target opens, while it's still there
  const LINKS = {
    series: (id) => `#/series/${id}`, plugin: (id) => `#/settings/plugin-${id}`,
    account: () => "#/settings/accounts", folder: () => "#/settings/library", library: () => "#/settings/shelves",
  };

  // the details' names and some of their values, in the page's language; others are shown as they are
  const LABELS = {
    reason: MV.t("Why"), name: MV.t("Name"), fields: MV.t("Fields"), unlocked: MV.t("Unlocked"),
    old: MV.t("Before"), new: MV.t("After"), from: MV.t("From"), to: MV.t("To"), version: MV.t("Version"),
    folder: MV.t("Folder"), books: MV.t("Books"), series: MV.t("Series"), count: MV.t("How many"),
    partly: MV.t("Partly"), files_left: MV.t("Files left"), host_path: MV.t("Path outside Docker"),
    library: MV.t("Library"), libraries: MV.t("Libraries"), moved_to: MV.t("Moved to"), admin: MV.t("Admin"),
    first: MV.t("The first account"), setting: MV.t("Setting"), plugin: MV.t("Plugin"), secret: MV.t("Secret"),
    cleared: MV.t("Cleared"), provider: MV.t("Provider"), id: "ID", url: MV.t("Address"), title: MV.t("Title"),
    ignore: MV.t("Never match"), poster: MV.t("Poster"), started: MV.t("Also switched on"),
    stopped: MV.t("Also switched off"), permissions: MV.t("Permissions accepted"), source: MV.t("Source"),
    error: MV.t("Error"), file: MV.t("File"), new_series: MV.t("New series"), new_books: MV.t("New books"),
    changed_books: MV.t("Changed books"), missing_series: MV.t("Series gone"), missing_books: MV.t("Books gone"),
    errors: MV.t("Errors"), seconds: MV.t("Seconds"), rollback: MV.t("Going back"), database: MV.t("Database"),
    size: MV.t("Size"), private_settings_included: MV.t("With the tokens"), safety_copy: MV.t("Copy from before"),
  };
  const VALUES = {
    wrong_password: MV.t("Wrong password"), no_such_account: MV.t("No such account"),
    too_many_tries: MV.t("Too many tries"),
  };
  const rtf = new Intl.RelativeTimeFormat(MV.lang, { numeric: "auto" });
  const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const shown = (v) => (v === null || v === undefined || v === "" ? "—" : typeof v === "boolean" ? (v ? MV.t("Yes") : MV.t("No"))
    : Array.isArray(v) ? (v.length ? v.map(shown).join(", ") : "—")
      : typeof v === "object" ? ("old" in v || "new" in v ? `${shown(v.old)} → ${shown(v.new)}` : JSON.stringify(v)) : String(v));

  Alpine.data("activityLog", () => ({
    entries: [], people: [], more: false, loading: false, failed: "", loaded: false,
    f: { user: "", group: "", from: "", to: "", q: "" },
    now: Date.now(), timer: null, observer: null, seq: 0,
    groups: GROUPS,
    init() {
      this.$watch("section", (s) => (s === "activity" ? this.open() : this.close()));
      if (this.section === "activity") this.open();
    },
    destroy() { this.close(); },
    open() {
      this.reload();
      MV.api(`${API}/people`).then((p) => (this.people = p)).catch(() => {});
      clearInterval(this.timer);
      this.timer = setInterval(() => (this.now = Date.now()), 30000);  // "5 minutes ago" moves on
      this.$nextTick(() => this.watchEnd());
    },
    close() {
      clearInterval(this.timer);
      this.observer?.disconnect();
      this.observer = null;
    },
    // more come as the end of the list nears the screen (the API's `before` paging)
    watchEnd() {
      if (this.observer || !this.$refs.end) return;
      this.observer = new IntersectionObserver((seen) => {
        if (seen.some((x) => x.isIntersecting)) this.loadMore();
      }, { rootMargin: "600px 0px" });
      this.observer.observe(this.$refs.end);
    },
    get filtered() { return Object.values(this.f).some(Boolean); },
    query(before) {
      const p = new URLSearchParams({ limit: PAGE });
      if (before) p.set("before", before);
      if (this.f.user !== "") p.set("user", this.f.user);
      if (this.f.group) p.set("group", this.f.group);
      if (this.f.q.trim()) p.set("q", this.f.q.trim());
      // the days in the page's time zone, sent as moments: from the start of `from` to the end of `to`
      if (this.f.from) p.set("since", new Date(`${this.f.from}T00:00`).toISOString());
      if (this.f.to) {
        const end = new Date(`${this.f.to}T00:00`);
        end.setDate(end.getDate() + 1);
        p.set("until", end.toISOString());
      }
      return p;
    },
    async reload() {
      const seq = ++this.seq;
      this.loading = true;
      this.failed = "";
      try {
        const r = await MV.api(`${API}/entries?${this.query()}`);
        if (seq !== this.seq) return;  // the filters changed meanwhile: that answer is the one that counts
        Object.assign(this, { entries: r.entries, more: r.more, loaded: true });
      } catch (e) {
        if (seq === this.seq) this.failed = e.message;
      } finally {
        if (seq === this.seq) this.loading = false;
      }
    },
    async loadMore() {
      if (this.loading || !this.more || !this.entries.length) return;
      const seq = this.seq;
      this.loading = true;
      try {
        const r = await MV.api(`${API}/entries?${this.query(this.entries.at(-1).id)}`);
        if (seq !== this.seq) return;
        this.entries = [...this.entries, ...r.entries];
        this.more = r.more;
      } catch (e) {
        this.failed = e.message;
      } finally {
        this.loading = false;
      }
    },
    clear() {
      this.f = { user: "", group: "", from: "", to: "", q: "" };
      this.reload();
    },
    // the entries by day, newest first: [{ key, label, entries }]
    get days() {
      const out = [];
      for (const e of this.entries) {
        const d = new Date(e.at), key = dayKey(d);
        if (out.at(-1)?.key !== key) out.push({ key, label: this.dayLabel(d), entries: [] });
        out.at(-1).entries.push(e);
      }
      return out;
    },
    dayLabel(d) {
      const today = new Date(this.now), yesterday = new Date(this.now);
      yesterday.setDate(yesterday.getDate() - 1);
      if (dayKey(d) === dayKey(today)) return MV.t("Today");
      if (dayKey(d) === dayKey(yesterday)) return MV.t("Yesterday");
      return MV.fmtDate(d, { weekday: "long", day: "numeric", month: "long",
        ...(d.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
    },
    // when: a moment ago, minutes or hours ago today; else the time (the day is the group's heading)
    when(e) {
      const s = Math.round((new Date(e.at).getTime() - this.now) / 1000);
      if (s > -45) return rtf.format(0, "second");
      if (s > -3600) return rtf.format(Math.round(s / 60), "minute");
      if (s > -6 * 3600) return rtf.format(Math.round(s / 3600), "hour");
      return MV.fmtTime(e.at, { hour: "numeric", minute: "2-digit" });
    },
    full(e) { return MV.fmtDateTime(e.at, { dateStyle: "full", timeStyle: "medium" }); },
    who(e) {
      if (e.user_id === null) return MV.t("System");
      if (e.user_id === 0) return MV.t("Someone not logged in");
      return e.user_name || MV.t("Owner");
    },
    personLabel(p) { return this.who({ user_id: p.id, user_name: p.name }); },
    personValue(p) { return p.id === null ? "system" : String(p.id); },
    what(e) {
      const say = SAID[e.kind];
      const v = { ...e.details, name: e.summary, count: e.details?.count };
      return say ? say(v) : [e.kind, e.summary].filter(Boolean).join(": ");
    },
    // a failure (a failed login, a plugin that didn't start, an update that went wrong) stands out
    warn(e) { return /(\.|_)failed$/.test(e.kind); },
    link(e) {
      const [type, id] = e.target || [];
      return e.target_exists && LINKS[type] ? LINKS[type](id) : "";
    },
    // what's kept with the entry, as [label, value] rows
    rows(e) {
      const out = [[MV.t("When"), this.full(e)]];
      if (e.ip) out.push([MV.t("Address"), e.ip]);
      if (e.via === "key") out.push([MV.t("How"), MV.t("With a key (e.g. an assistant)")]);
      for (const [k, v] of Object.entries(e.details || {})) {
        if (k === "size" && typeof v === "number") out.push([LABELS.size, MV.fmtNum(v / 1048576, { maximumFractionDigits: 1 }) + " MB"]);
        else out.push([LABELS[k] || k, typeof v === "string" && VALUES[v] ? VALUES[v] : shown(v)]);
      }
      out.push([MV.t("Kind"), e.kind]);
      return out;
    },
  }));

  MV.on(ID, "start", () => {
    if (!Alpine.store("app").admin) return;
    const nav = Alpine.store("app").settingsNav, plugins = nav.findIndex(([k]) => k === "plugins");
    MV.settingsPage(ID, "activity", MV.t("Activity"), "list", plugins < 0 ? Infinity : plugins + 1);
  });
});
