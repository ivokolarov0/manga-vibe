# MangaVibe

> [!WARNING]
> **This app is fully vibe coded.** Every line was written by an AI assistant (Claude), guided by
> conversation rather than written or reviewed line by line by a human developer. It has automated
> tests and has been checked in a browser, but it has not had a professional security audit.
> Use it at your own risk, keep it on your home network (see [Reading away from home](docs/away-from-home.md)),
> and keep backups of anything you care about. The app changes your manga files in one way only:
> **Delete series** removes a series' folder when you ask it to. Mount the manga folder read-only
> (`:ro`) if you never want that.

<p align="center">
  <img src="docs/media/desktop.webp" width="74%" alt="MangaVibe on a computer: the library grid, a genre filter, a series page with its books in order and the files behind them, and the Needs attention lists">
  &nbsp;
  <img src="docs/media/mobile.webp" width="23%" alt="MangaVibe on a phone: opening a series and reading it with swipes, taps, pinch zoom, the page slider and vertical mode">
</p>
<p align="center"><sub>The series and art shown are made up for the demo.</sub></p>

MangaVibe is a self-hosted manga server that runs anywhere Docker does: a NAS, a home server or your
PC. Point it at your manga folder, and read on your phone, tablet or computer. Nothing is uploaded
anywhere, and your files are never renamed or changed.

## Install

> [!CAUTION]
> **Never make MangaVibe reachable from the internet.** It has **no login**: anyone who finds it can
> read, download and change everything in it. To read away from home, use a VPN:
> see [Reading away from home](docs/away-from-home.md).

MangaVibe runs in **Docker**. You set it up once (about 10 minutes); after that it updates itself.
Pick your system:

| | |
|---|---|
| 🪟 **[Windows](docs/install/windows.md)** | Docker Desktop and one command in PowerShell |
| 🍎 **[Mac](docs/install/mac.md)** | Docker Desktop and one command in Terminal |
| 🐧 **[Linux](docs/install/linux.md)** | Docker and one command |
| 🗄️ **[Synology NAS](docs/install/synology.md)** | Container Manager, step by step with clicks |
| 🐳 **[Other servers](docs/install/docker.md)** | Unraid, Portainer, Docker Compose, environment variables |

Then read on your phone: **[Phone and Android app](docs/phone.md)**.

## Features

### Your library, sorted out
- **Each folder is a series**, and the books in it are put in order even with messy file names:
  `Vol 3`, `ch03v2`, `c10-18`, `15a`, Japanese titles, extras and duplicates.
- **Reads** `.cbz/.zip`, `.cbr/.rar`, `.cb7/.7z`, image `.epub`, `.pdf` and folders of images, without
  unpacking them. Pages can be JPEG, PNG, WebP, GIF, AVIF, JPEG XL, BMP or TIFF. Archives inside
  archives and files with the wrong extension work too.
- **Several library folders**, each scanned every few hours and on start. New files show up by
  themselves; a removed file disappears.
- **Covers and details** (titles in three languages, description, genres, tags, authors, status,
  score, links) from **AniList** and **MangaUpdates**, matched automatically with a match score.
  If it picked the wrong series, **Fix match** lets you search or paste a link.
- **Edit anything by hand**, upload your own poster, and your edits are kept when the details are
  refreshed.

### Finding something to read
- **Search** titles, alternative names and authors.
- **Filter** by genre and tag (include or exclude), reading status, format, favorites, first
  letter, and complete or incomplete collections (finished series with volumes missing); **sort** by title, date added, last updated, last read, score, year or unread count.
- **Continue**: everything you're in the middle of, to pick up where you left off.
- **Reading status** worked out from your progress (reading, completed, …), or set by hand, plus
  favorites. Mark a book read or unread, or everything before it.

### A reader made for phones
- **Pages or vertical** (webtoons). Pages turn like every digital reader: the next one is on the
  right (→, a tap on the right, a swipe to the left). Set per series or per library folder.
- **Swipe or tap** to turn pages (or the volume buttons, in the Android app). Tap zones can be
  one-handed, and the buttons move to your reading hand; pinch and double-tap to zoom, with a lock
  to keep the page fitted.
- **Fit** to screen, width or height, or original size. Pages are resized for your screen and the
  next ones preloaded, so turning is instant.
- **Progress saved on every page**, and the next book offered at the end.
- **On a computer:** click or use the arrow keys, scroll to zoom, and move the mouse to the bottom
  edge for the controls.

### Easy on the eyes, and for readers who can't see the page
- **Page look:** brightness, contrast, and inverted (white lines on black) or warm colours.
- **Read aloud:** with a plugin that has text for the page (like Manga Accessibility or Manga
  Translate), MangaVibe reads it with your device's voice. Pick the speed and voice, and let it turn
  the pages by itself to listen to a whole chapter.
- **Screen readers** (TalkBack, NVDA, VoiceOver) get the same text.

### Looking after your collection
- **Needs attention** in Settings lists what to fix: series that couldn't be matched, series
  matched to the same entry, files that can't be read (with what's inside them), duplicates side
  by side, and missing chapters.
- **Fix a book's place by hand** (Files tab): its volume, chapter, a chapter range, or mark it as an
  extra or hidden. Only MangaVibe's database changes, never the file.

### Your way
- **Dark or light theme**, card size, endless scrolling or numbered pages, and the title language.
- **An Android app**: fullscreen, the screen kept on, volume buttons and the phone's voice. See
  [Phone and Android app](docs/phone.md).
- **Plugins** add features: tracker sync, translation, accessibility. See [Plugins](docs/using-plugins.md).
- **Your own styles** in `custom.css` in the data folder (created on first start, with examples).
- **Updates itself** (see below).

## Organizing your manga

Each folder directly inside your library is one series. Files inside it can be named almost any way
you like:

```
/manga
├── One Piece/
│   ├── One Piece v01.cbz
│   ├── One Piece v02.cbz
│   └── One Piece ch1050-1060.cbz
├── Berserk/
│   └── Berserk Vol.01/          ← a folder of images is one book
└── Solo Leveling/
    └── Solo Leveling c001.epub
```

## Updating

MangaVibe checks for a new version once a day and shows a message. Tap **Update now**, and it
installs the new version and restarts by itself. You don't have to touch Docker. If a new version
doesn't start 3 times in a row, MangaVibe goes back to the previous one by itself.

Once in a while, a version needs a new Docker image, and the message tells you so. Your library,
progress and settings are kept. The steps are at the end of each install guide:
[Windows](docs/install/windows.md#when-a-version-needs-a-new-image) ·
[Mac](docs/install/mac.md#when-a-version-needs-a-new-image) ·
[Linux](docs/install/linux.md#when-a-version-needs-a-new-image) ·
[Synology](docs/install/synology.md#when-a-version-needs-a-new-image) ·
[Docker Compose and others](docs/install/docker.md#when-a-version-needs-a-new-image)

## More

- **[Reading away from home](docs/away-from-home.md)**: why not to open a port, and how to use a VPN instead
- **[Plugins](docs/using-plugins.md)**: the available plugins, and how to install, update and remove them
- **[Troubleshooting](docs/troubleshooting.md)**: logs, permissions, more library folders
- **[Q&A](docs/qa.md)**: why it's built the way it is, and the requests that are declined
- **[For developers](docs/developers.md)**: running it from source, tests, releases, the code layout,
  and [writing a plugin](docs/PLUGINS.md)

## Legal

MangaVibe is a reader for manga **you own**, on your own server. It doesn't include, download or
share any manga: it only shows the files you put in your library, to you. Covers and details come
from AniList and MangaUpdates, the way any tracker app shows them.

- Don't use it to share or publish manga or translated pages.
- Please support the creators: buy the manga you read.

Plugins are made by others and run their own code; [Plugins](docs/using-plugins.md) says what to
watch for. This project is provided as is, without any warranty.

**Licence:** [AGPL-3.0](LICENSE). In short: you may use, change and share MangaVibe; if you share a
changed version, or run one for other people, you must share its source code under the same licence.
