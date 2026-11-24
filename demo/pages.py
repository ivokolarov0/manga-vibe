"""The website on GitHub Pages, next to the demo at its root (demo/build.py writes it):

- the homepage, docs/ (demo/home.html): for people who want to use MangaVibe. Its install guides, the
  phone page, reading away from home and troubleshooting are the repo's Markdown files, drawn into it
  (ON_HOME), so the files stay the only source.
- the developer pages (DEV_PAGES): one Markdown file each, drawn by demo/docs.html.

A link in a doc to a file on the site stays on the site (README.md is the homepage); any other file of
the repo opens on GitHub. The pages the site used to have redirect to where their content is now
(REDIRECTS), so old links keep working."""

REPO = "https://github.com/ivokolarov0/manga-vibe/blob/main/"
HOME = "docs/"

# Markdown drawn into the homepage: (file in the repo, its id on the homepage)
ON_HOME = [
    ("README.md", ""),
    ("docs/install/windows.md", "install-windows"),
    ("docs/install/mac.md", "install-mac"),
    ("docs/install/linux.md", "install-linux"),
    ("docs/install/synology.md", "install-synology"),
    ("docs/install/docker.md", "install-docker"),
    ("docs/phone.md", "phone"),
    ("docs/away-from-home.md", "away-from-home"),
    ("docs/troubleshooting.md", "troubleshooting"),
]

# The developers' pages: (file in the repo, path on the site, title in the menu)
DEV_PAGES = [
    ("docs/developers.md", "docs/developers/", "How it's built"),
    ("docs/PLUGIN-REFERENCE.md", "plugins/", "Plugin reference"),
    ("docs/PLUGINS.md", "docs/plugin-guide/", "The full plugin guide"),
]

# Docs that are only on GitHub; the homepage links to them
ON_GITHUB = ["docs/using-plugins.md", "docs/qa.md"]

# Pages the site used to have, and where that is now (a path on the site, or a full address)
REDIRECTS = {
    "docs/install-windows/": "docs/#install-windows",
    "docs/install-mac/": "docs/#install-mac",
    "docs/install-linux/": "docs/#install-linux",
    "docs/install-synology/": "docs/#install-synology",
    "docs/install-docker/": "docs/#install-docker",
    "docs/phone/": "docs/#phone",
    "docs/away-from-home/": "docs/#away-from-home",
    "docs/troubleshooting/": "docs/#troubleshooting",
    "docs/plugins/": REPO + "docs/using-plugins.md",
    "docs/qa/": REPO + "docs/qa.md",
}


def links():
    """Where each doc on the site is, for the links between docs: file in the repo → path from the
    site's root (with #id for a part of the homepage)."""
    out = {src: HOME + (f"#{anchor}" if anchor else "") for src, anchor in ON_HOME}
    out.update({src: path for src, path, _ in DEV_PAGES})
    return out
