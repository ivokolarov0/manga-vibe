# Install on a Synology NAS

[← Back to the README](../../README.md)

> [!CAUTION]
> **Never make MangaVibe reachable from the internet**: no port forwarding, and no rule for it in
> *Login Portal → Reverse Proxy*. It has no login. To read away from home, use a VPN: see
> [Reading away from home](../away-from-home.md).

Newer NAS (DSM 7.2+) use the **Container Manager** package; older ones use **Docker**. The steps are
the same; where a button has a different name, the older one is in brackets.

## 1. Install Container Manager

**Package Center** → install **Container Manager** (or **Docker**).

## 2. Make a folder for MangaVibe's data

**File Station** → open the `docker` shared folder → create a folder named `mangavibe`. This is
where MangaVibe keeps its database, covers and plugins.

## 3. Download the image

Open **Container Manager** (**Docker**) → **Registry** → search `ivokolarov/mangavibe` →
**Download** → tag `latest`. Wait until it shows up under **Image**.

## 4. Start the container

**Image** → select `ivokolarov/mangavibe` → **Run** (**Launch**):

- Container name `mangavibe`. Tick **Enable auto-restart** (on older DSM it's in **Advanced
  Settings**).
- **Port Settings**: change the local port from *Auto* to `8090` (container port `8090`).
- **Volume Settings** (**Volume**) → **Add Folder**: pick your manga folder, mount path `/manga`,
  tick **Read-only**.
- **Add Folder** again: pick `docker/mangavibe`, mount path `/data`.
- **Next** → **Done** (**Apply** → **Next** → **Done**).

## 5. Open it

Open **http://\<your NAS address\>:8090**: the same address you use for DSM, with `:8090` at the end
(e.g. `http://192.168.1.20:8090`). The first scan starts by itself; covers and details fill in over
the next minutes.

**On your phone:** see [Phone and Android app](../phone.md).

## When a version needs a new image

MangaVibe updates itself when you tap **Update now**. Once in a while a version needs a new Docker
image, and the update message says so. Your library, progress and settings are kept:

1. **Registry** → download `ivokolarov/mangavibe` `latest` again.
2. **Container** → select `mangavibe` → stop it → **Action** → **Reset** (**Clear**).
3. Start it again.

## Problems?

See [Troubleshooting](../troubleshooting.md).
