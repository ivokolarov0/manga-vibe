# Install on Windows

[← Back to the README](../../README.md)

> [!CAUTION]
> **Never make MangaVibe reachable from the internet** (no port forwarding, no public reverse proxy).
> It has no login. To read away from home, use a VPN: see [Reading away from home](../away-from-home.md).

MangaVibe runs in **Docker Desktop**, a free app that runs programs in their own sealed box. You set
it up once; after that MangaVibe updates itself.

## 1. Install Docker Desktop

1. Download **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** and install it.
   Restart the PC if it asks.
2. Open Docker Desktop and wait until it says **Engine running**. You can skip signing in.

## 2. Start MangaVibe

1. Open **PowerShell**: Start menu → type *PowerShell* → Enter.
2. Paste this line, but first change `D:\Manga` to the folder your manga is in, then press Enter:

   ```powershell
   docker run -d --name mangavibe --restart unless-stopped -p 8090:8090 -v "D:\Manga:/manga" -v "C:\MangaVibe:/data" ivokolarov/mangavibe:latest
   ```

   - To get a folder's path, open it in File Explorer and click the address bar.
   - `C:\MangaVibe` is where MangaVibe keeps its own data (database, covers, plugins). It's created
     for you.
   - `/manga` is mounted read-write so **Delete series** can remove series you don't want. Add `:ro` after `/manga` (`...:/manga:ro`) if MangaVibe should never be able to change your files; deleting is then turned off.

## 3. Open it

Open **http://localhost:8090** in your browser. The first scan starts by itself; covers and details
fill in over the next minutes.

**Start it with the PC:** Docker Desktop → **Settings** → **General** → turn on **Start Docker
Desktop when you sign in**. MangaVibe itself restarts with Docker.

**On your phone:** see [Phone and Android app](../phone.md). You'll need the PC's address:
Settings → Network & internet → Wi-Fi (or Ethernet) → your network → **IPv4 address**.

## When a version needs a new image

MangaVibe updates itself when you tap **Update now**. Once in a while a version needs a new Docker
image, and the update message says so. Your library, progress and settings are kept. In PowerShell:

```powershell
docker pull ivokolarov/mangavibe:latest
docker rm -f mangavibe
```

Then run the same `docker run` line from step 2 again.

## Problems?

See [Troubleshooting](../troubleshooting.md).
