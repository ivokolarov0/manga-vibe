---
name: preview
description: Start MangaVibe from the current checkout or worktree and open it in the browser, to check the work done there. Use when the user asks to preview, run, start, open or show the app, or to see/check what a branch or worktree changed.
---

# Preview the app

Run from the root of the current checkout (works the same in the main checkout and in any git worktree):

```
python preview.py
```

Run it with the Bash tool and `run_in_background: true` (the server keeps running). Then read the
first lines of its output to get the URLs and tell the user, in one short line each:

- **Local** – `http://localhost:<port>` (it opens in the browser by itself)
- **Phone** – the LAN address, for testing on a phone on the same Wi-Fi

What the script does, so you don't have to:

- finds a Python: this folder's `.venv`, else the main checkout's `.venv` (worktrees have none)
- picks a port: 8090 for the main checkout, a fixed 8100–8199 port per worktree, next free one if taken
- keeps a separate `dev-data/` (database) per worktree, and reuses the main checkout's
  `dev-library/` sample manga

Options: `--port N`, `--no-browser` (use this when you only need to check it yourself, e.g. with a
headless browser screenshot). To point at real manga: set `LIBRARY_PATHS` before running.

To stop it: stop the background task, or the user presses Ctrl+C if they ran `! python preview.py`.
