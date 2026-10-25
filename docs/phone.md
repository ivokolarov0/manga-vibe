# Phone and Android app

[← Back to the README](../README.md)

## In the phone's browser

Your phone must be on the same Wi-Fi as the computer or NAS that runs MangaVibe.

1. Open **http://\<computer address\>:8090** in the phone's browser, e.g. `http://192.168.1.20:8090`.
   The install guide for your system says where to find the address
   ([Windows](install/windows.md), [Mac](install/mac.md), [Linux](install/linux.md); on a Synology it's
   the address you use for DSM).
2. Use the browser's **Add to Home Screen**, so it opens full screen like an app.

The computer has to be on (and Docker Desktop running) while you read.

## Android app

On Android you can read in the MangaVibe app instead of the browser. It shows the same MangaVibe
(same library, progress and settings), and adds:

- fullscreen, without browser bars
- the screen stays on while you read
- the volume buttons turn pages (switch it off in Settings → Android app)
- **Read aloud** with the phone's own voice (Android settings → Text-to-speech)

**Install it:** open MangaVibe in your phone's browser and tap **Open app** on the banner (or in
Settings → Android app). The first time, this downloads `MangaVibe.apk`: open it and allow installing
apps from your browser when Android asks. Then tap **Open app** again: the app opens and connects to
your server by itself. You can also type the server's address in the app (e.g. `192.168.1.20:8090`).

The app isn't on the Play Store. It comes from this project's
[releases](https://github.com/ivokolarov0/manga-vibe/releases/latest) and works over plain HTTP on
your home network. More in [android/README.md](../android/README.md).

## Away from home

Use a VPN, never an open port: see [Reading away from home](away-from-home.md).
