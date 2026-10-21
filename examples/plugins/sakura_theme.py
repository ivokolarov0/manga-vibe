"""Example plugin that only changes how MangaVibe looks. Copy into /data/plugins/.

A plugin's `css` is loaded after the built-in styles, so it wins without !important.
Turn it off in Settings -> Plugins to get the normal look back.
"""
from app.plugins import Plugin


class SakuraTheme(Plugin):
    id = "sakura-theme"
    name = "Sakura theme"
    version = "1.0"
    description = "Pink accent colour and rounder corners."
    css = """
    :root {
      --accent: #ff6fa8;
      --radius: 14px;
      --radius-panel: 18px;
    }

    .card .poster {
      box-shadow: 0 4px 14px rgba(255, 111, 168, 0.25);
    }
    """
