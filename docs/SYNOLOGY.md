# Installing MangaVibe on the Synology NAS

These steps are for the older **Docker** package in DSM (no "Projects"/Compose screen).
You build the image on your PC, copy one file to the NAS, and create the container with the wizard.

Your manga folder is mounted **read-only**: MangaVibe can never change, rename or delete your files.

---

## 1. Build the image on your PC (once per new version)

On the PC, in the project folder (`D:\projects\manga-vibe`), with Docker Desktop running:

```bash
docker build -t mangavibe:latest .
```

```bash
docker save mangavibe:latest -o mangavibe.tar
```

This makes `mangavibe.tar` (about 100–150 MB).

> Alternative: if you have a Docker Hub account, `docker tag mangavibe:latest <you>/mangavibe:latest`
> and `docker push <you>/mangavibe:latest`, then on the NAS use **Registry** to download it instead of steps 2–3.

## 2. Copy the file to the NAS

Open **File Station** and upload `mangavibe.tar` to any folder, e.g. `docker/`.

## 3. Import the image

1. Open the **Docker** app → **Image** (left menu).
2. **Add** → **Add From File** → pick `docker/mangavibe.tar` → OK.
3. Wait until `mangavibe:latest` appears in the list.

## 4. Create the data folder

In **File Station**, create the folder `docker/mangavibe`. This holds the database, covers,
cache and plugins. (MangaVibe creates everything inside it on first start.)

## 5. Create the container

1. **Image** → select `mangavibe:latest` → **Launch**.
2. **Container Name:** `mangavibe`.
3. Click **Advanced Settings** *before* clicking Next.
   - **Advanced Settings** tab: tick **Enable auto-restart**.
   - **Volume** tab:
     - **Add Folder** → choose `docker/mangavibe` → Mount path: `/data` → leave Read-Only **unticked**.
     - **Add Folder** → choose your manga shared folder → Mount path: `/manga` → tick **Read-Only**.
   - **Port Settings** tab: change the Local Port from "Auto" to **8090**, Container Port **8090**, TCP.
     (Do not use 49153 or 8085; Komga and Komf use those.)
   - **Environment** tab: click **+** and add
     - `TZ` = `Europe/Sofia`
     - optional: `APP_PASSWORD` = a password (login is added in a later version)
4. **Apply** → **Next** → tick "Run this container after the wizard is finished" → **Done** (or **Apply**).

Open **http://192.168.1.{ADD YOUR ADDRESS HERE}:8090** on your phone or PC. The first scan starts by itself;
progress is shown under **Settings**.

## Phone: add to home screen

In Chrome on Android: open the address above → menu **⋮** → **Add to Home screen**.
Over plain `http://` on your home network Chrome opens it like a normal tab; the reader's
fullscreen button (⛶, or tap once if "Fullscreen on first tap" is on) hides the browser bars.

## Viewing logs

**Docker** → **Container** → select `mangavibe` → **Details** → **Log** tab.
The app writes plain-English messages there, e.g. what it scanned, or how to fix a permission problem.

## Stop, edit, restart

- **Stop/Start:** **Container** → toggle switch next to `mangavibe`.
- **Change settings (volumes, port, env):** stop the container → **Edit** → change → OK → start it.

## Updating to a new version

1. Build and save the new image on the PC (step 1), upload the `.tar` and **Add From File** again (steps 2–3).
   It replaces `mangavibe:latest`.
2. **Container** → stop `mangavibe` → **Action** → **Reset** (DSM 7) or **Clear** (DSM 6).
   This recreates the container from the new image and keeps your settings.
3. Start it again.

Your database, progress and covers live in `docker/mangavibe`, so nothing is lost.
If Reset/Clear is not offered: delete the container and create it again with step 5. Same result.

### Upgrading from MangaShelf (the app's old name)

The image is now called `mangavibe:latest`, so the old container can't simply be reset onto it:

1. Import the new image (steps 1–3).
2. **Container** → stop the old `mangashelf` container → note its volumes, port and environment
   (**Edit** shows them) → **Delete** it. Deleting the container does not touch your folders.
3. Create a new container from `mangavibe:latest` with step 5, using the **same** data folder
   (e.g. your existing `docker/mangashelf`; there is no need to rename it) and the same manga folder.
4. On first start the old `mangashelf.db` is renamed to `mangavibe.db` automatically. Your reading
   progress, metadata, covers and settings are kept. The log says "Renamed the old MangaShelf database".
5. Optional: delete the old `mangashelf:latest` image under **Image**.

## Fixing "Cannot write to the data folder"

If the log says MangaVibe can't write to `/data`:

1. **File Station** → right-click `docker/mangavibe` → **Properties** → **Permission** tab.
2. **Create** → User or group: **Everyone** → tick **Read** and **Write** → OK.
3. Tick **Apply to this folder, sub-folders and files** → **Save**.
4. Restart the container.

## About PUID / PGID

By default the container starts as root (that's how Synology runs containers) and then switches
to user `PUID`:`PGID` (default 1000:1000) **if that user can read your manga folder**. If not,
it keeps running as root and logs a note. Both are fine: your manga folder is read-only either way.

## Adding more library folders

Stop the container → **Edit** → **Volume** → add another folder mounted as `/library2`
(Read-Only) → start it → in MangaVibe **Settings → Library folders → + Add folder** → pick `library2`
→ **Use this folder**. A folder added by mistake can be removed with **Remove** (your files are not touched).

## Changing the look

Edit `docker/mangavibe/custom.css` with File Station (it's created on first start, with examples
inside) and reload the page. See `docs/PLUGINS.md` → Styling.

## Plugins

Copy plugin `.py` files into `docker/mangavibe/plugins` with File Station, then
**Settings** → **Reload plugins**. See `docs/PLUGINS.md`.

## Reading away from home

Don't forward port 8090 on your router. Use **Tailscale** (there is a Synology package) or
Synology **VPN Server**, then open the same address as at home.
