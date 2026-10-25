# Plugins

[← Back to the README](../README.md)

Plugins add features without changing the app. A plugin is a single `.py` file.

## Available plugins

| Plugin | What it does |
|---|---|
| [MyAnimeList sync](https://github.com/ivokolarov0/manga-vibe-ext-mal-profile-sync) | Sends Reading / Completed to your MyAnimeList list as you read, and pulls them back with **Sync all**. Its README shows how to connect your account step by step. |
| [Manga Translate](https://github.com/ivokolarov0/manga-vibe-ext-translate) | Shows English over the speech bubbles of Japanese manga while you read. Small AI models on your own computer find, read and translate the text: free, private, nothing is sent online. Needs a computer with a graphics card on your network (or the same one). |
| [Manga Accessibility](https://github.com/ivokolarov0/manga-vibe-ext-accessibility) | For readers who are blind or have low vision: describes every panel, who says what and the sound effects, and MangaVibe reads it aloud. Needs only Ollama with a model that can see pictures (e.g. gemma4), on a computer on your network. |

> [!WARNING]
> **Only install plugins you trust.** A plugin is code that runs inside MangaVibe with full access
> to its data, including the logins other plugins save. Install plugins from this list or ones whose
> code you've read.

## Install a plugin

1. Download the plugin's `.py` file (on GitHub: open the file → **Download raw file**).
2. Put it in the `plugins` folder of your data folder: `docker/mangavibe/plugins` on a Synology, or
   `plugins` inside the folder you mounted as `/data` (create it if it isn't there).
3. In MangaVibe: **Settings → Plugins → Reload plugins**. The plugin appears in the list. If it has
   settings, buttons or updates, its **Settings** button opens a page with them.

In the reader, everything a plugin adds is under **Plugins** at the bottom of the reading options:
one row per plugin, which opens a panel with its switches and buttons.

## Update a plugin

Plugins that publish releases on GitHub have an **Updates** part on their page (**Settings →
Plugins → Settings** next to the plugin). The list shows *Update X* next to a plugin with a new
version:

- MangaVibe checks once a day and shows *Version X is available*. **Check for updates** looks right away.
- Press **Update to X** to install it, or **Skip this version** if you don't want it. A skipped
  version isn't offered again, but a newer one is. **Show it again** brings it back.

Plugins never update by themselves. The new version is checked before it replaces the old one, and
your plugin settings are kept. For other plugins, replace the file and press **Reload plugins**.

## Switch off or remove a plugin

Switch a plugin off with its toggle in **Settings → Plugins**. To remove it, delete its file and
press **Reload plugins**.

## Write your own

See [PLUGINS.md](PLUGINS.md) for how plugins work, the events they can react to, and the examples in
[examples/plugins](../examples/plugins).
