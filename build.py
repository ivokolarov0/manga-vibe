"""Build the MangaVibe Docker image on your PC, with the current git commit built in.

    python build.py          build mangavibe:latest
    python build.py --run    build, then (re)start the local container from docker-compose.yml
    python build.py --save   build, then write mangavibe.tar for the Synology (docs/SYNOLOGY.md, steps 2-3)

The commit (GIT_SHA) tells the app which version is built in, so auto-update doesn't offer the
same version again. Uncommitted changes are built too, but the app can't know about them.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
IMAGE = "mangavibe:latest"


def run(*cmd, env=None):
    print(">", " ".join(cmd), flush=True)
    if subprocess.run(cmd, cwd=HERE, env=env).returncode != 0:
        sys.exit(f"Failed: {' '.join(cmd)}")


def git(*args):
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser(description="Build the MangaVibe Docker image.")
    ap.add_argument("--run", action="store_true", help="(re)start the local container with docker compose")
    ap.add_argument("--save", action="store_true", help="write mangavibe.tar for the Synology")
    args = ap.parse_args()

    sha = git("rev-parse", "HEAD")
    if not sha:
        sys.exit("This is not a git checkout, so the version is unknown.")
    if git("status", "--porcelain", "--untracked-files=no"):
        print(f"Note: there are uncommitted changes; they are built in, but the version shows as {sha[:7]}.")

    run("docker", "build", "--build-arg", f"GIT_SHA={sha}", "-t", IMAGE, ".")
    if args.run:
        # the image is already built; recreate the container so it uses it
        run("docker", "compose", "up", "-d", "--force-recreate", "--no-build", env={**os.environ, "GIT_SHA": sha})
        print("Running: http://localhost:8090")
    if args.save:
        run("docker", "save", IMAGE, "-o", "mangavibe.tar")
        print(f"Wrote {HERE / 'mangavibe.tar'}. Upload it to the NAS (docs/SYNOLOGY.md, step 2).")
    print(f"Built {IMAGE} at version {sha[:7]}.")


if __name__ == "__main__":
    main()
