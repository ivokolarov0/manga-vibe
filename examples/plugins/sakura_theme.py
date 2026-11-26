"""Example plugin that only changes how MangaVibe looks. Copy into /data/plugins/.

A plugin's `css` is loaded after the built-in styles, so it wins without !important.
Turn it off in Settings -> Plugins to get the normal look back.

Two ways to change the look (docs/UI-KIT.md, "Restyling"):
- the design tokens (--accent, --radius…) change everything at once;
- a piece of the UI kit (buttons, chips, dialogs…) has custom properties of its own (--btn-radius,
  --chip-bg, --dialog-radius…), so one piece can change without the others.
"""
from app.plugins import Plugin


class SakuraTheme(Plugin):
    id = "sakura-theme"
    name = "Sakura theme"
    category = "appearance"
    version = "1.1"
    description = "Pink accent colour, rounder corners and pill-shaped buttons."
    permissions = ["Changes how the app looks"]
    css = """
    /* the tokens: everything that uses them changes */
    :root {
      --accent: #ff6fa8;
      --radius: 14px;
      --radius-panel: 18px;
    }

    /* the UI kit's pieces, one at a time */
    :root {
      --btn-radius: 999px;                                         /* pill-shaped buttons */
      --chip-bg: color-mix(in srgb, var(--accent) 16%, var(--surface2));  /* pink-tinted chips */
      --dialog-radius: 26px;                                       /* rounder dialogs and sheets */
    }

    .card .poster {
      box-shadow: 0 4px 14px rgba(255, 111, 168, 0.25);
    }
    """
