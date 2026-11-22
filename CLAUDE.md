# Notes for AI agents working on MangaVibe

## Settings cards and lists

- Settings cards and lists (plugin pages too) must never show a divider under their last row.
- Don't add `border-bottom` to rows and then remove it with `:last-child` or `:last-of-type`. That
  breaks as soon as a hidden row, an Alpine `<template>`, a wrapper or an empty element follows the
  last row, and the line comes back (#126, #245).
- Put dividers *between* rows instead: a `border-top` on each row that has a row before it
  (`.row ~ .row` or `.row + .row`). See `.setting-row` in `app/web/static/css/settings.css`.
- When you add or change a settings row or card, check that the card's bottom edge has no extra line,
  in Firefox too. `tests/test_web_parts.py` fails on the old `:last-child` pattern.
