"""Preview this checkout (main or any git worktree) in the browser: `python preview.py`

- Uses the .venv of this folder, or the one in the main checkout (worktrees have none).
- Each worktree gets its own port (stable, from the folder path) and its own dev-data,
  so several worktrees can run side by side without sharing a database.
- Reuses the main checkout's dev-library (read-only), so there is no wait for sample manga.
- Prints a LAN address too, to open the preview on a phone on the same Wi-Fi.

Options: --port N, --no-browser. Stop with Ctrl+C.
"""
import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main_checkout() -> Path:
    """The main working tree (first entry of `git worktree list`), or this folder."""
    try:
        out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=HERE,
                             capture_output=True, text=True, check=True).stdout
        return Path(out.splitlines()[0].split(" ", 1)[1])
    except Exception:
        return HERE


def venv_python(folder: Path) -> Path | None:
    for p in (folder / ".venv" / "Scripts" / "python.exe", folder / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    return None


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def pick_port() -> int:
    # main checkout keeps the usual 8090; worktrees get 8100-8199 from their path
    start = 8090 if HERE == main_checkout() else 8100 + zlib.crc32(str(HERE).lower().encode()) % 100
    for port in range(start, start + 50):
        if port_free(port):
            return port
    raise SystemExit("No free port found")


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return None


def branch() -> str:
    try:
        return subprocess.run(["git", "branch", "--show-current"], cwd=HERE,
                              capture_output=True, text=True).stdout.strip() or "?"
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    main_dir = main_checkout()
    python = venv_python(HERE) or venv_python(main_dir)
    if not python:
        raise SystemExit(f"No .venv found in {HERE} or {main_dir}.\n"
                         f"Create one: python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt")

    port = args.port or pick_port()
    env = dict(os.environ, PORT=str(port), PYTHONUNBUFFERED="1")
    env.setdefault("DATA_DIR", str(HERE / "dev-data"))
    if "LIBRARY_PATHS" not in env and not (HERE / "dev-library").exists() and (main_dir / "dev-library").exists():
        env["LIBRARY_PATHS"] = str(main_dir / "dev-library")

    url = f"http://localhost:{port}"
    ip = lan_ip()
    print(f"\n  Branch:  {branch()}\n  Folder:  {HERE}\n  Local:   {url}"
          + (f"\n  Phone:   http://{ip}:{port}" if ip else "")
          + f"\n  Library: {env.get('LIBRARY_PATHS', HERE / 'dev-library')}\n", flush=True)

    proc = subprocess.Popen([str(python), str(HERE / "dev.py")], cwd=HERE, env=env)

    def open_when_ready():
        for _ in range(120):
            if proc.poll() is not None:
                return
            try:
                urllib.request.urlopen(url + "/api/health", timeout=1)
                print(f"  Ready: {url}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return
            except Exception:
                time.sleep(0.5)

    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        sys.exit(proc.wait())
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
