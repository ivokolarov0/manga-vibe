# Installing MangaVibe on the Synology NAS

These steps are for the older **Docker** package in DSM (no "Projects"/Compose screen); the newer
**Container Manager** has the same screens. You download the image from Docker Hub and create the
container with the wizard. No PC needed.

Your manga folder is mounted **read-only**: MangaVibe can never change, rename or delete your files.

---

## 1. Download the image

1. Open the **Docker** app → **Registry** (left menu).
2. Search for `ivokolarov/mangavibe` → select it → **Download** → tag **latest** → **Select**.
3. Open **Image** and wait until `ivokolarov/mangavibe:latest` appears in the list (about 100–150 MB).

The image is built automatically for Intel and ARM NAS models every time a new version is released.

> **Alternative: build the image on your PC** (to try your own changes, or if the NAS has no
> internet). In the project folder, with Docker Desktop running, run `python build.py --save`.
> This makes `mangavibe.tar`. Upload it with **File Station** (e.g. to `docker/`), then
> **Image** → **Add** → **Add From File** → pick it. The image is then called `mangavibe:latest`;
> use that name instead of `ivokolarov/mangavibe:latest` below.

## 2. Create the data folder

In **File Station**, create the folder `docker/mangavibe`. This holds the database, covers,
cache and plugins. (MangaVibe creates everything inside it on first start.)

## 3. Create the container

1. **Image** → select `ivokolarov/mangavibe:latest` → **Launch**.
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

### Automatic updates (most versions)

MangaVibe checks GitHub once a day for a new version on the `releases` branch. When there is one,
a message appears at the top of the page:

- **Update now**: downloads the new code into `docker/mangavibe/code`, installs any new Python
  packages, tests that it starts, and restarts the app. The page reloads by itself. Nothing else to do.
- **Skip this version**: you won't be asked about this version again (you will about the next one).
- **Later**: hides the message for a day.

**Settings → Updates** shows the running version, has **Check now**, and turns the daily check on or off.
To turn updates off completely, add the environment variable `AUTO_UPDATE` = `0` to the container.

If a new version does not start 3 times in a row, MangaVibe goes back to the version before it
(the container log says so) and marks the new one as failed. To go back to the code built into the
image by hand: stop the container, delete `docker/mangavibe/code/current` in File Station, start it.

### When a new image is needed

Rarely, a version changes the Dockerfile (e.g. a new system program). Then the message says
**needs a new Docker image** and the update is not installed. The new image is on Docker Hub
a few minutes after the version is released:

1. **Registry** → search `ivokolarov/mangavibe` → **Download** → tag **latest** again.
   It replaces the old `ivokolarov/mangavibe:latest`.
2. **Container** → stop `mangavibe` → **Action** → **Reset** (DSM 7) or **Clear** (DSM 6).
   This recreates the container from the new image and keeps your settings.
3. Start it again.

Your database, progress and covers live in `docker/mangavibe`, so nothing is lost.
If Reset/Clear is not offered: delete the container and create it again with step 3. Same result.

(If you use a PC-built image instead: run `python build.py --save` again, **Add From File** the new
`.tar`, then Reset and start the container as above.)

### Moving to the Docker Hub image

If your container was made from a PC-built image (`mangavibe:latest`) or from the old
`mangashelf:latest`, Reset can't switch it to `ivokolarov/mangavibe:latest`. Make a new one instead:

1. Download the image (step 1).
2. **Container** → stop the old container → note its volumes, port and environment
   (**Edit** shows them) → **Delete** it. Deleting the container does not touch your folders.
3. Create a new container from `ivokolarov/mangavibe:latest` with step 3, using the **same** data
   folder (e.g. your existing `docker/mangavibe`, or `docker/mangashelf`; no need to rename it) and
   the same manga folder.
4. Coming from MangaShelf: on first start the old `mangashelf.db` is renamed to `mangavibe.db`
   automatically. Your reading progress, metadata, covers and settings are kept.
   The log says "Renamed the old MangaShelf database".
5. Optional: delete the old image (`mangavibe:latest` or `mangashelf:latest`) under **Image**.

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
