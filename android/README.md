# MangaVibe for Android

A small app that shows your MangaVibe server on the whole screen, between the phone's status and
navigation bars. All the reading happens in the web app; the Android app adds what a browser can't do over plain HTTP:

- no browser bars (the phone's own status and navigation bars stay visible)
- the screen stays on while the reader is open
- the volume buttons turn pages (Settings → Android app, on by default)
- the back button closes the reader's options, sheets and dialogs before going back

It needs no Play Store and no HTTPS. On first start it asks for the server's address
(e.g. `192.168.1.20:8090`). The easier way: open MangaVibe in the phone's browser and tap
**Open app** on the banner or in Settings → Android app. The app then opens and connects by itself.
If the app isn't installed, the same button downloads it.

Every address the app connects to is saved (e.g. the home network's and the Tailscale one). Settings →
Android app → Change server lists them to switch with one tap (hold one to forget it). When the
current address doesn't answer, the app tries the other saved ones and switches to the first that does.

## How it fits together

- `MainActivity.kt`: the WebView, the setup and "can't reach" screens, the system bars, the volume and back keys.
  Links to other sites (AniList, GitHub) open in the browser.
- `Bridge.kt`: `window.MangaVibeAndroid` in the page: `readerState(open, volumeKeys)`, `changeServer()`,
  `server()`, `version()`.
- The page answers through `window.MangaVibeApp`: `volumeKey("next" | "prev")` and `back()`
  (`app/web/static/js/app.js`, `reader.js`).
- The web app knows it runs in the app from `window.MangaVibeAndroid` (and `MangaVibeAndroid/<version>`
  at the end of the user agent).
- "Open app" is a Chrome intent link: `intent://open#Intent;scheme=mangavibe;package=…;S.url=<page>;S.browser_fallback_url=<APK>;end`.

The app rarely needs to change: new MangaVibe versions update the web app, and the Android app just
shows it.

## Build

Needs JDK 17 and the Android SDK (Android Studio has both): open this folder in Android Studio, or run

```
./gradlew assembleRelease
```

The APK is `app/build/outputs/apk/release/app-release.apk`. Without a signing key (below) it is signed
with your debug key: fine for your own phone.

Releases get the app from `.github/workflows/android.yml`, attached to the GitHub release as `MangaVibe.apk`.
It is only rebuilt when `android/` changed since the last release with an APK; otherwise that APK is
copied over. To rebuild anyway: Actions → Android app → Run workflow, with the release tag.

## Signing key (once, for releases)

Android only installs an update over the app when both are signed with the same key. So all releases
are signed with one key, kept in the repository's secrets. **Keep a backup of the key file and its
password**: if it's lost, users have to uninstall the app (and set it up again) to get a new version.

1. Make the key (keytool comes with any JDK, e.g. inside Android Studio's `jbr/bin`):

   ```
   keytool -genkeypair -v -keystore mangavibe-release.jks -alias mangavibe -keyalg RSA -keysize 4096 -validity 36500
   ```

2. Add four repository secrets (GitHub → Settings → Secrets and variables → Actions), or with `gh`:

   ```
   base64 -w0 mangavibe-release.jks | gh secret set ANDROID_KEYSTORE_BASE64
   gh secret set ANDROID_KEYSTORE_PASSWORD   # the store password you picked
   gh secret set ANDROID_KEY_ALIAS --body mangavibe
   gh secret set ANDROID_KEY_PASSWORD        # same as the store password unless you set another
   ```

To sign a local build with it, set `MANGAVIBE_KEYSTORE` (the .jks path), `MANGAVIBE_KEYSTORE_PASSWORD`,
`MANGAVIBE_KEY_ALIAS` and `MANGAVIBE_KEY_PASSWORD` before running Gradle.
