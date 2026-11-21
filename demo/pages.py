"""The docs pages of the Pages site: each Markdown file of the repo, where it's shown and under which
group of the sidebar. demo/build.py writes each one as <path>/index.html (demo/docs.html) with the
Markdown next to it as page.md; demo/docs.html draws it. A page's links to another file in this list
stay on the site; links to any other file of the repo go to GitHub."""

# (file in the repo, path on the site, title in the sidebar, group)
PAGES = [
    ("README.md", "docs/", "Home", "Start"),
    ("docs/install/windows.md", "docs/install-windows/", "Windows", "Install"),
    ("docs/install/mac.md", "docs/install-mac/", "Mac", "Install"),
    ("docs/install/linux.md", "docs/install-linux/", "Linux", "Install"),
    ("docs/install/synology.md", "docs/install-synology/", "Synology NAS", "Install"),
    ("docs/install/docker.md", "docs/install-docker/", "Other servers", "Install"),
    ("docs/phone.md", "docs/phone/", "Phone and Android app", "Using MangaVibe"),
    ("docs/away-from-home.md", "docs/away-from-home/", "Reading away from home", "Using MangaVibe"),
    ("docs/using-plugins.md", "docs/plugins/", "Plugins", "Using MangaVibe"),
    ("docs/troubleshooting.md", "docs/troubleshooting/", "Troubleshooting", "Using MangaVibe"),
    ("docs/qa.md", "docs/qa/", "Q&A", "Using MangaVibe"),
    ("docs/PLUGIN-REFERENCE.md", "plugins/", "Plugin reference", "Writing plugins"),
    ("docs/PLUGINS.md", "docs/plugin-guide/", "The full plugin guide", "Writing plugins"),
    ("docs/developers.md", "docs/developers/", "For developers", "Writing plugins"),
]
