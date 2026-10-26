# Install on Linux

[← Back to the README](../../README.md)

> [!CAUTION]
> **Never make MangaVibe reachable from the internet** (no port forwarding, no public reverse proxy).
> It has no login. To read away from home, use a VPN: see [Reading away from home](../away-from-home.md).

## 1. Install Docker

Skip this if you already have Docker:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

## 2. Start MangaVibe

Change `$HOME/Manga` if your manga is somewhere else:

```bash
sudo docker run -d --name mangavibe --restart unless-stopped -p 8090:8090 -v "$HOME/Manga:/manga" -v "$HOME/mangavibe-data:/data" ivokolarov/mangavibe:latest
```

- `$HOME/mangavibe-data` is where MangaVibe keeps its own data (database, covers, plugins).
- `/manga` is mounted read-write so **Delete series** can remove series you don't want. Add `:ro` after `/manga` (`...:/manga:ro`) if MangaVibe should never be able to change your files; deleting is then turned off.

Prefer Docker Compose, or want to change the time zone or add a second library? See
[Other servers and Docker Compose](docker.md).

## 3. Open it

Open **http://localhost:8090**. The first scan starts by itself; covers and details fill in over the
next minutes. MangaVibe starts by itself after a reboot.

**On your phone:** see [Phone and Android app](../phone.md). The computer's address is shown by
`hostname -I` (the first one).

## When a version needs a new image

MangaVibe updates itself when you tap **Update now**. Once in a while a version needs a new Docker
image, and the update message says so. Your library, progress and settings are kept:

```bash
sudo docker pull ivokolarov/mangavibe:latest
sudo docker rm -f mangavibe
```

Then run the same `docker run` line from step 2 again.

## Problems?

See [Troubleshooting](../troubleshooting.md).
