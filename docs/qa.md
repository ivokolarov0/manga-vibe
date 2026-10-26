# Q&A

[← Back to the README](../README.md)

**Why another manga server?**
I tried the popular options and didn't like them. I wanted an easy way to get metadata, and it
turned out I had to run an extra Docker container and install a browser extension that needed
access to all domains (it didn't work when I limited it to specific ones). After all that,
identify still didn't work. So I said f*ck it, I'll vibe code my own: extensible, with metadata
and match % built in from the start. I didn't feel like making a mobile app, so the browser is enough
(well, that changed: turns out I'm too lazy to open the browser, so now there's an
[Android app](phone.md#android-app)).

**Can MangaVibe change my files?**
In one way only: **Delete series** (the ⋯ menu on a series page) deletes a series' folder, after
showing what it will remove. I added it because I wanted to get rid of series I don't care about
without leaving the app. It can only delete a series folder directly inside a library folder,
nothing above it. The code was written almost entirely with Claude and may have gaps, so if you
never want the app to touch your files, mount the manga folder read-only (`:ro`). Deleting is then
turned off.

**Why is there no user login?**
Too much of a headache, and it means handling sensitive information I don't want to be responsible
for. Also, someone will want Google/Apple login, then 2FA, then passkeys, then "forgot password",
which means a mailing service or an SMTP server... In short, tedious, and I don't feel like it.
At most there will be a PIN page or a simple password.

**Why only manga and not comics?**
I'm making it for myself, and I mostly read manga. That said, you can already read comics with it:
a series matched as a western comic reads left to right by itself, and a library folder of comics
can be set to left to right in Settings → Library folders. If you want more comic-specific features,
there's a plugin system, so you can vibe code a plugin for them.

**Why is there no option to rename or optimize files?**
Again, I don't want the app to have access to your files. Rename and optimize them outside the
app; I don't want to be responsible if something important disappears.

**Do you accept feature requests and changes?**
Some things are never accepted: see [Requests that are declined](#requests-that-are-declined) below.
For the rest, not sure. Small things like "this icon isn't centered" or "there's no hover here": no.
This was mainly meant as a personal project. If people like it and want to add features, pull
requests are welcome, but I work 9 to 6 every day, so I don't know how much time I'll have to review
them. I'm not saying no for now, but it may be easier to fork it and change it yourself without
waiting for me.

**You're a developer and you vibe coded everything. Why?**
I enjoy having more free time. With my work hours and the "free" time I have, the whole project
would probably have taken me a few months. What you see here took 2–3 hours to vibe code a working
base version and another 6–10 hours to polish it and add features. If you're one of those people
who hate everything AI... this is not the right place. Go to the art section. ;)

**Why is there no executable file?**
Docker runs it anywhere, from a NAS to your desktop, packs everything it needs and keeps it away
from your files. Making installers for every OS sounds like a job, and I already have one of those.

**If you don't plan to accept pull requests, why make it public?**
It makes updating easier, and if someone wants a quick and dirty solution, here you go.

## Requests that are declined

> [!IMPORTANT]
> Issues and pull requests asking for any of these are closed without discussion. You are free to
> code a solution for yourself (in a fork or a plugin), but please don't ask for it here.

- **Connecting to pirate sites to read their contents.** MangaVibe reads the files you own, nothing else.
- **Crawling or scraping pirate sites**, including downloaders and "find the raws" features.
- **Changing library files**: renaming, converting, optimizing, tagging or moving them. Deleting a
  whole series is the one exception (see above).
- **More mobile apps**: an iPhone app, a Play Store listing, or a native reader. The
  [Android app](phone.md#android-app) is a window onto the web app, and that's as far as it goes.
- **Tying it to an existing mobile app**, such as making it a source or extension for another reader app.
- **An executable or installer** (`.exe`, `.dmg`, …). MangaVibe runs in Docker. (The Android app's
  `.apk` is the one exception, and it only shows the server running in Docker.)
- **A public access option**: opening it to the internet, share links, or anything else that makes it
  reachable from outside your home. See [Reading away from home](away-from-home.md).
- **Support for devices other than the ones in the install guides** (Windows, Mac, Linux, Synology
  NAS and other Docker servers). For example, no running the server on a phone.
