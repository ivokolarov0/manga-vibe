# Install on a Mac

[← Back to the README](../../README.md)

> [!CAUTION]
> **Never make MangaVibe reachable from the internet** (no port forwarding, no public reverse proxy).
> It has no login. To read away from home, use a VPN: see [Reading away from home](../away-from-home.md).

MangaVibe runs in **Docker Desktop**, a free app that runs programs in their own sealed box. You set
it up once; after that MangaVibe updates itself.

## 1. Install Docker Desktop

1. Download **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**. Pick **Apple
   Silicon** or **Intel** (Apple menu → **About This Mac** shows which one you have).
2. Open it and wait until it says **Engine running**. You can skip signing in.

## 2. Start MangaVibe

Open **Terminal** (Spotlight → type *Terminal*), paste this and press Enter:

```bash
docker run -d --name mangavibe --restart unless-stopped -p 8090:8090 -v "$HOME/Manga:/manga" -v "$HOME/MangaVibe:/data" ivokolarov/mangavibe:latest
```

- This expects your manga in a folder called **Manga** in your home folder. If it's somewhere else,
  change `$HOME/Manga` to that path, e.g. `/Volumes/MyDrive/Manga`.
- `$HOME/MangaVibe` is where MangaVibe keeps its own data (database, covers, plugins). It's created
  for you.
- `/manga` is mounted read-write so **Delete series** can remove series you don't want. Add `:ro` after `/manga` (`...:/manga:ro`) if MangaVibe should never be able to change your files; deleting is then turned off.

## 3. Open it

Open **http://localhost:8090** in your browser. The first scan starts by itself; covers and details
fill in over the next minutes.

**Start it with the Mac:** Docker Desktop → **Settings** → **General** → turn on **Start Docker
Desktop when you sign in**. MangaVibe itself restarts with Docker.

**On your phone:** see [Phone and Android app](../phone.md). You'll need the Mac's address:
System Settings → Wi-Fi → **Details** next to your network → TCP/IP → **IP address**.

## When a version needs a new image

MangaVibe updates itself when you tap **Update now**. Once in a while a version needs a new Docker
image, and the update message says so. Your library, progress and settings are kept. In Terminal:

```bash
docker pull ivokolarov/mangavibe:latest
docker rm -f mangavibe
```

Then run the same `docker run` line from step 2 again.

## Problems?

See [Troubleshooting](../troubleshooting.md).
