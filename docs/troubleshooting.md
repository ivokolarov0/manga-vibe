# Troubleshooting

[← Back to the README](../README.md)

- **Logs:** `docker logs mangavibe`, or the container's log view in your Docker GUI.
- **"Cannot write to the data folder":** the user the app runs as can't write to the folder mounted
  as `/data`. Give it read and write access (e.g. `chown -R 1000:1000 /path/to/mangavibe-data`, or
  the folder's permission settings on a NAS), then restart the container.
- **Which user it runs as:** the container starts as root and switches to `PUID`:`PGID` if that user
  can read your manga folder; otherwise it stays root and logs why. To use your own user, set
  `PUID`/`PGID` to its ids (`id -u`, `id -g`).
- **"Delete series" is greyed out:** the dialog says why. *Mounted read-only*: remove `:ro` from the
  manga folder (on a Synology, untick **Read-only** in the container's volume settings) and recreate
  the container. *Can't delete files in this folder*: the user the app runs as may not write there;
  give it write access, or set `PUID`/`PGID` to the user that owns the files.
- **"id already in use" when starting the container (Synology):** Docker kept the old container's
  state after a stop or an edit. Restart **Container Manager** (Package Center → Stop, then Run), or
  the NAS. Your library and settings are kept; they live in the `/data` folder.
- **Adding another library folder:** mount it into the container, e.g. as `/manga2`, and
  restart. Then go to **Settings** → **Library folders** → **+ Add folder** and pick it. **Remove**
  takes it out of the library again; your files are not touched.
- **A file is broken or can't be read:** it's listed in **Settings → Needs attention → Errors**, with
  what's inside it. Replace the file; the next scan picks the new one up.
- **A new version doesn't start:** after 3 failed starts in a row, MangaVibe goes back to the previous
  version by itself.
- **Going back to the code in the image:** stop the container, delete `code/current` in the data
  folder, start it again.
