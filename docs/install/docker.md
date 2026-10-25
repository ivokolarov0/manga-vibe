# Install on other servers (Unraid, Portainer, Docker Compose, …)

[← Back to the README](../../README.md)

> [!CAUTION]
> **Never make MangaVibe reachable from the internet** (no port forwarding, no public reverse proxy).
> It has no login. To read away from home, use a VPN: see [Reading away from home](../away-from-home.md).

The image is [`ivokolarov/mangavibe`](https://hub.docker.com/r/ivokolarov/mangavibe), for Intel and
ARM. Give it port **8090**, auto-restart, and two folders:

| In the container | What it is | Mode |
|---|---|---|
| `/manga` | your manga folder | **read-only** |
| `/data` | database, covers, cache, plugins (empty to start with) | read-write |

## Docker Compose

```yaml
services:
  mangavibe:
    image: ivokolarov/mangavibe:latest
    container_name: mangavibe
    ports:
      - "8090:8090"
    volumes:
      - /path/to/manga:/manga:ro
      - /path/to/mangavibe-data:/data
    restart: unless-stopped
```

Then `docker compose up -d`, and open **http://\<server address\>:8090**.

## Settings (environment variables)

All optional:

| Variable | Default | What it does |
|---|---|---|
| `TZ` | `UTC` | Your time zone, e.g. `Europe/Sofia` |
| `LIBRARY_PATHS` | `/manga` | More than one library: `/manga,/manga2` (mount each one read-only) |
| `RESCAN_HOURS` | `6` | How often the library is scanned for new files |
| `CACHE_MAX_GB` | `2` | Space for resized pages and thumbnails |
| `PUID` / `PGID` | `1000` | User the app runs as, if that user can read your manga folder |
| `AUTO_UPDATE` | `1` | `0` turns off update checks |

## When a version needs a new image

MangaVibe updates itself when you tap **Update now**. Once in a while a version needs a new Docker
image, and the update message says so. Your library, progress and settings are kept:

- **Docker Compose:** `docker compose pull && docker compose up -d`
- **Unraid, Portainer and others:** pull `ivokolarov/mangavibe:latest` again and recreate the
  container with the same settings (Unraid: **Force update** on the container).

## Problems?

See [Troubleshooting](../troubleshooting.md).
