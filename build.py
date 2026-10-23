"""Build the MangaVibe Docker image on your PC, with the current git commit built in.

    python build.py          build mangavibe:latest
    python build.py --run    build, then (re)start the local container from docker-compose.yml
    python build.py --save   build, then write mangavibe.tar, to import elsewhere with `docker load`
    python build.py --push   build, then push ivokolarov/mangavibe:latest to Docker Hub (Intel + ARM)
    python build.py --push --tag 1.1   also push it as ivokolarov/mangavibe:1.1

--push needs `docker login` once.

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
HUB_REPO = "ivokolarov/mangavibe"
PLATFORMS = "linux/amd64,linux/arm64"
BUILDER = "mangavibe-multi"


def run(*cmd, env=None):
    print(">", " ".join(cmd), flush=True)
    if subprocess.run(cmd, cwd=HERE, env=env).returncode != 0:
        sys.exit(f"Failed: {' '.join(cmd)}")


def git(*args):
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser(description="Build the MangaVibe Docker image.")
    ap.add_argument("--run", action="store_true", help="(re)start the local container with docker compose")
    ap.add_argument("--save", action="store_true", help="write mangavibe.tar to import elsewhere")
    ap.add_argument("--push", action="store_true", help=f"push {HUB_REPO}:latest to Docker Hub (Intel + ARM)")
    ap.add_argument("--tag", help="with --push: also push this version tag, e.g. 1.1")
    args = ap.parse_args()
    if args.tag and not args.push:
        ap.error("--tag only works with --push")

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
        print(f"Wrote {HERE / 'mangavibe.tar'}. Import it with `docker load -i mangavibe.tar`.")
    if args.push:
        push(sha, args.tag)
    print(f"Built {IMAGE} at version {sha[:7]}.")


def push(sha, tag):
    # The default builder can't build for several platforms at once, so use our own.
    if subprocess.run(["docker", "buildx", "inspect", BUILDER], cwd=HERE, capture_output=True).returncode != 0:
        run("docker", "buildx", "create", "--name", BUILDER)
    tags = ["-t", f"{HUB_REPO}:latest"] + (["-t", f"{HUB_REPO}:{tag}"] if tag else [])
    run("docker", "buildx", "build", "--builder", BUILDER, "--platform", PLATFORMS,
        "--build-arg", f"GIT_SHA={sha}", *tags, "--push", ".")
    print(f"Pushed {HUB_REPO}:latest" + (f" and :{tag}" if tag else "") + " to Docker Hub.")


if __name__ == "__main__":
    main()
