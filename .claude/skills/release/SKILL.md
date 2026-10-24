---
name: release
description: Publish a new MangaVibe version as a GitHub release. Finds the last release, looks at what was merged since, picks the next version (major, minor or patch), and publishes it after the user confirms. Use when the user asks to release, publish, ship or push out a new version, or asks what the next version number should be.
---

# Release a new version

A GitHub release is what running apps update to (`app/updater.py`), and publishing it builds the
Docker image (`.github/workflows/image.yml`). The `releases` branch is no longer used: never push or
merge into it.

## 1. Check where we are

```
git fetch origin --tags
gh release view --json tagName,publishedAt,targetCommitish
git log --first-parent --oneline <last tag>..origin/main
```

- `gh release view` with no tag shows the latest release (drafts and pre-releases are left out).
- No commits since the last tag: tell the user there is nothing to release, and stop.
- Release what is on `origin/main`, not the local checkout. If the local `main` has commits that
  aren't pushed, or there are open PRs the user may have meant to include, say so before going on.

## 2. Read what changed

For each merged PR since the last tag (merge commits name them: `Merge pull request #N`):

```
gh pr view N --json title,body,labels
```

Also check whether this version needs a new Docker image (the app then can't update by itself, and
users have to pull the image):

```
git diff --stat <last tag> origin/main -- Dockerfile docker/entrypoint.sh
```

## 3. Pick the version

Versions are `vMAJOR.MINOR.PATCH`. Bump one part from the last release and reset the ones after it.
Take the biggest bump that any change needs:

- **Major**: users lose something or have to do more than tap Update / pull the image: a feature or
  setting removed, an environment variable, port or volume renamed or removed, data that doesn't
  carry over, a database change that older versions can't read when they go back.
- **Minor**: something new or changed that users can see: a feature, a setting, a UI change, a new
  metadata source, a supported file format.
- **Patch**: only fixes, speed, security updates of packages, and changes users don't see (tests,
  docs, CI, refactors).

A new Docker image alone doesn't make it major (the app says so and the user pulls it), but it must
be written in the release notes (step 5).

## 4. Ask the user

Show, short:
- last version and date, and the proposed version
- one line per change (the PR title in plain words), and which change decided the bump
- whether it needs a new Docker image

Then ask with AskUserQuestion: the proposed version first, marked (Recommended), and the other two
bumps as options. The user can also type a version, or ask for a pre-release (step 5). Don't publish
before the user answers.

## 5. Publish

Release the exact commit that was reviewed, not whatever `main` is by then:

```
gh release create vX.Y.Z --target <origin/main sha> --title "<title>" --generate-notes [--notes "<notes>"]
```

- `--title`: a short summary of the biggest change, in plain words, at most 60 characters, without
  the version number (the app shows it next to the tag, e.g. "v1.2.0 · Reader tap zones").
- `--notes` is added above the generated list of PRs. Use it when the version needs a new Docker
  image: "This version needs a new Docker image: pull `ivokolarov/mangavibe:latest` and recreate the
  container. Your library, progress and settings are kept." Leave it out otherwise.
- Pre-release (to test it first, e.g. "release it as a test"): add `--prerelease` and use a version
  like `v1.2.0-rc.1`. It builds an image tagged `1.2.0-rc.1` but not `latest`, and running apps
  don't get it. Later the real release is `v1.2.0`.

## 6. Watch the image build

```
gh run list --workflow image.yml --limit 1
gh run watch <id> --exit-status
```

Run `gh run watch` with `run_in_background: true` (it takes a few minutes). Then tell the user: the
version and its link, whether the image build passed, and that running apps pick it up at their next
daily check (or right away with Settings → Updates → Check now). If the build failed, show the
failing step from `gh run view <id> --log-failed`; the release itself still stands, and running apps
can still update to it unless it needs a new image.
