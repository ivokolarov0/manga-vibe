"""The demo's made-up library: series, covers and pages drawn here, so nothing in the demo is anyone's
manga. `make_library(root)` writes the folders; SERIES holds the details demo/build.py sets through the
app's own API (the edit dialog's PUT /api/series/<id>/metadata)."""
import io
import random
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PAGE = (720, 1080)        # paged books
STRIP = (720, 2400)       # webtoon pages, read vertically
PAGES_PER_BOOK = 6


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an old Pillow without the scalable default font
        return ImageFont.load_default()


def _webp(img, quality=60):
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=4)
    return buf.getvalue()


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", size, top)
    d = ImageDraw.Draw(img)
    for y in range(0, h, 4):
        d.rectangle([0, y, w, y + 4], fill=_mix(top, bottom, y / h))
    return img


def _shapes(d, box, rng, colors, n=6):
    """Simple shapes in a panel: hills, a sun or moon, figures as circles on bodies."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    # ground
    d.polygon([(x0, y1), (x0, y0 + h * 0.7), (x0 + w * 0.4, y0 + h * 0.55), (x1, y0 + h * 0.72), (x1, y1)],
              fill=colors[1])
    # sky body
    r = min(w, h) * rng.uniform(0.08, 0.14)
    cx, cy = x0 + w * rng.uniform(0.15, 0.85), y0 + h * rng.uniform(0.15, 0.35)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colors[2])
    # figures
    for _ in range(rng.randint(1, 3)):
        fx = x0 + w * rng.uniform(0.15, 0.85)
        fy = y0 + h * rng.uniform(0.55, 0.7)
        s = min(w, h) * rng.uniform(0.06, 0.1)
        d.rounded_rectangle([fx - s * 0.7, fy, fx + s * 0.7, fy + s * 2.4], radius=int(s * 0.4), fill=colors[3])
        d.ellipse([fx - s * 0.6, fy - s * 1.2, fx + s * 0.6, fy], fill=colors[4])


def _bubble(d, box, rng, text):
    x0, y0, x1, y1 = box
    w = (x1 - x0) * rng.uniform(0.35, 0.5)
    h = w * 0.45
    bx = x0 + (x1 - x0 - w) * rng.uniform(0.05, 0.95)
    by = y0 + 14
    d.ellipse([bx, by, bx + w, by + h], fill="white", outline="black", width=3)
    d.polygon([(bx + w * 0.4, by + h * 0.9), (bx + w * 0.5, by + h * 1.3), (bx + w * 0.55, by + h * 0.9)],
              fill="white", outline="black")
    d.text((bx + w / 2, by + h / 2), text, fill="black", font=_font(int(h * 0.28)), anchor="mm")


LINES = ["Hey!", "Look!", "Wait…", "Huh?", "Let's go!", "Really?", "…!", "Over there!", "Hmm.", "Run!"]


def page_image(title, palette, book_label, n, rng, size=PAGE):
    """One page: a few panels with shapes and speech bubbles, and its number at the bottom."""
    w, h = size
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    m, gap = 28, 16
    if size == STRIP:  # webtoon: tall panels one under the other, lots of space between them
        rows = [(m, m + 40, w - m, 900), (m, 1150, w - m, 1900)]
        boxes = rows
    else:
        split = rng.uniform(0.38, 0.55)
        top = (m, m, w - m, int(h * split))
        lower = int(h * split) + gap
        if rng.random() < 0.5:
            mid = rng.uniform(0.4, 0.6) * w
            boxes = [top, (m, lower, int(mid) - gap // 2, h - m - 40), (int(mid) + gap // 2, lower, w - m, h - m - 40)]
        else:
            boxes = [top, (m, lower, w - m, h - m - 40)]
    for i, box in enumerate(boxes):
        sky = _mix(palette[0], (255, 255, 255), rng.uniform(0.2, 0.6))
        panel = _gradient((box[2] - box[0], box[3] - box[1]), sky, _mix(palette[0], (0, 0, 0), 0.15))
        img.paste(panel, box[:2])
        _shapes(d, box, rng, [sky, palette[1], palette[2], palette[3], palette[4]])
        d.rectangle(box, outline="black", width=4)
        if rng.random() < 0.8:
            _bubble(d, box, rng, rng.choice(LINES))
    title = title.encode("ascii", "ignore").decode() or title
    d.text((w / 2, h - 30), f"{title} - {book_label} - page {n + 1}", fill=(90, 90, 90), font=_font(20),
           anchor="mm")
    return img


def cover_image(title, palette, subtitle="", size=PAGE):
    """A poster: a sun or moon, hills, one or two figures on a gradient, and the title (ASCII only:
    the default font has no accents or Japanese)."""
    w, h = size
    rng = random.Random(title)  # each series its own picture, the same on every build
    img = _gradient(size, _mix(palette[0], (255, 255, 255), 0.15), _mix(palette[0], (0, 0, 0), 0.6))
    d = ImageDraw.Draw(img)
    for _ in range(rng.randint(6, 14)):  # stars or dust
        x, y, r = rng.uniform(0, w), rng.uniform(h * 0.2, h * 0.6), rng.uniform(2, 5)
        d.ellipse([x - r, y - r, x + r, y + r], fill=_mix(palette[2], (255, 255, 255), 0.5))
    r = w * rng.uniform(0.22, 0.34)
    cx, cy = w * rng.uniform(0.3, 0.7), h * rng.uniform(0.36, 0.46)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=palette[2])
    far = _mix(palette[1], palette[0], 0.45)
    d.polygon([(0, h), (0, h * 0.66), (w * 0.25, h * 0.58), (w * 0.6, h * 0.66), (w, h * 0.56), (w, h)], fill=far)
    d.polygon([(0, h), (0, h * rng.uniform(0.72, 0.8)), (w * 0.4, h * 0.68), (w * 0.75, h * 0.76),
               (w, h * rng.uniform(0.66, 0.74)), (w, h)], fill=palette[1])
    people = [w / 2] if rng.random() < 0.5 else [w * 0.38, w * 0.62]
    for i, fx in enumerate(people):
        s = w * (0.11 if len(people) == 1 else 0.09) * (1 - 0.1 * i)
        top = h * 0.5 + i * s * 0.4
        d.rounded_rectangle([fx - s * 0.8, top, fx + s * 0.8, top + s * 3], radius=int(s * 0.5),
                            fill=palette[3])
        d.ellipse([fx - s * 0.7, top - s * 1.4, fx + s * 0.7, top], fill=palette[4])
    title = title.encode("ascii", "ignore").decode() or title
    font = _font(int(w * 0.085))
    words, lines, line = title.split(), [], ""
    for word in words:  # wrap the title
        test = f"{line} {word}".strip()
        if d.textlength(test, font=font) > w * 0.85 and line:
            lines.append(line)
            line = word
        else:
            line = test
    lines.append(line)
    for i, text in enumerate(lines):
        d.text((w / 2, h * 0.06 + i * w * 0.1), text, fill="white", font=font, anchor="mt",
               stroke_width=3, stroke_fill="black")
    if subtitle:
        d.text((w / 2, h - 40), subtitle, fill="white", font=_font(int(w * 0.05)), anchor="mm",
               stroke_width=2, stroke_fill="black")
    return img


def write_book(path: Path, title, palette, label, rng, pages=PAGES_PER_BOOK, size=PAGE, cover=True):
    """A .cbz of drawn pages; the first one is a cover."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for n in range(pages):
            if n == 0 and cover:
                img = cover_image(title, palette, label, size=PAGE)
            else:
                img = page_image(title, palette, label, n, rng, size=size)
            z.writestr(f"{n + 1:03d}.webp", _webp(img))


# palettes: sky, ground, sun, bodies, heads
P = {
    "dawn": [(255, 170, 120), (120, 90, 140), (255, 230, 150), (70, 60, 110), (250, 210, 180)],
    "sea": [(90, 170, 220), (40, 90, 140), (255, 250, 210), (30, 50, 90), (240, 200, 170)],
    "forest": [(150, 210, 150), (60, 120, 70), (255, 240, 170), (50, 80, 50), (240, 210, 180)],
    "night": [(40, 50, 100), (20, 25, 50), (230, 230, 255), (90, 80, 140), (210, 200, 230)],
    "neon": [(230, 80, 160), (40, 20, 70), (90, 240, 240), (30, 30, 60), (250, 200, 220)],
    "sand": [(240, 200, 120), (190, 130, 70), (255, 255, 230), (110, 70, 40), (250, 220, 190)],
    "snow": [(200, 220, 240), (240, 245, 250), (255, 220, 120), (80, 100, 140), (250, 220, 200)],
    "ember": [(200, 70, 50), (70, 30, 30), (255, 200, 80), (40, 20, 20), (240, 190, 160)],
    "mint": [(150, 230, 210), (70, 150, 140), (255, 255, 220), (50, 90, 100), (250, 220, 200)],
    "plum": [(170, 110, 200), (80, 50, 110), (255, 220, 240), (50, 30, 70), (240, 210, 220)],
    "steel": [(140, 160, 180), (60, 70, 90), (230, 240, 250), (40, 45, 60), (220, 200, 190)],
    "candy": [(255, 190, 210), (240, 140, 170), (255, 255, 200), (180, 80, 120), (255, 225, 210)],
}

AUTHOR = [{"name": "Demo Studio", "role": "Story & Art"}]


def S(folder, palette, books, meta, library="Manga", strip=False, progress=None, favorite=False, broken=()):
    return {"folder": folder, "palette": P[palette], "books": books, "meta": meta, "library": library,
            "strip": strip, "progress": progress or {}, "favorite": favorite, "broken": list(broken)}


def vols(name, numbers):
    return [f"{name} v{n:02d}.cbz" for n in numbers]


def chs(name, numbers):
    return [f"{name} ch{n:03d}.cbz" for n in numbers]


# Every name, story and picture here is made up for the demo.
SERIES = [
    S("Starlight Courier", "night", vols("Starlight Courier", range(1, 6)), {
        "title_english": "Starlight Courier", "title_native": "星明かりの配達人",
        "description": "Mina delivers letters between floating islands on a bicycle that can ride the "
                       "wind. Every letter is a story, and one of them is addressed to a city nobody has "
                       "seen in a hundred years.",
        "publication_status": "FINISHED", "start_year": 2019, "volumes_total": 5, "average_score": 86,
        "genres": ["Adventure", "Fantasy", "Slice of Life"], "tags": ["Found Family", "Flying", "Journey"]},
        progress={1: "done", 2: "done", 3: 3}, favorite=True),
    S("Kitsune Cafe", "candy", chs("Kitsune Cafe", range(1, 13)), {
        "title_english": "Kitsune Café", "title_native": "きつねカフェ",
        "description": "A fox spirit opens a café in a quiet shopping street and has no idea how coffee "
                       "works. The regulars don't mind. A comedy about small wins and very bad latte art.",
        "publication_status": "RELEASING", "start_year": 2023, "average_score": 79,
        "genres": ["Comedy", "Slice of Life", "Supernatural"], "tags": ["Food", "Yokai", "Iyashikei"]},
        progress={1: "done", 2: "done", 3: "done", 4: 2}),
    S("Iron Bloom", "ember", vols("Iron Bloom", [1, 2, 4, 5]), {
        "title_english": "Iron Bloom", "title_native": "鉄の花",
        "description": "In a city of rust and rain, a mechanic finds a flower growing inside a broken "
                       "war machine. Volume 3 is missing from this shelf on purpose: see Needs attention.",
        "publication_status": "FINISHED", "start_year": 2016, "volumes_total": 5, "average_score": 82,
        "genres": ["Action", "Sci-Fi", "Drama"], "tags": ["Mecha", "Post-Apocalyptic", "Robots"]}),
    S("Moonlit Archive", "plum", [*chs("Moonlit Archive", range(1, 7)), "Moonlit Archive ch003v2.cbz"], {
        "title_english": "Moonlit Archive", "title_native": "月夜の書庫",
        "description": "A library that only opens at night lends out memories instead of books. Its new "
                       "librarian has to find out who keeps returning them changed.",
        "publication_status": "RELEASING", "start_year": 2022, "average_score": 88,
        "genres": ["Mystery", "Supernatural"], "tags": ["Library", "Memory", "Twist"]},
        progress={1: "done", 2: 4}),
    S("Paper Lanterns", "dawn", ["Paper Lanterns.cbz"], {
        "title_english": "Paper Lanterns", "title_native": "紙灯籠",
        "description": "One summer festival night, two old friends meet again. A one-shot.",
        "publication_status": "FINISHED", "start_year": 2021, "format": "ONE_SHOT", "average_score": 74,
        "genres": ["Drama", "Romance"], "tags": ["Festival", "Childhood Friends", "One-shot"]}),
    S("Cloud Garden", "mint", chs("Cloud Garden", range(1, 9)), {
        "title_english": "Cloud Garden", "title_native": "구름 정원", "country_of_origin": "KR",
        "description": "A gardener grows plants on the clouds above Seoul. A webtoon: it opens in "
                       "vertical mode.",
        "publication_status": "RELEASING", "start_year": 2024, "average_score": 81,
        "genres": ["Fantasy", "Romance"], "tags": ["Webtoon", "Full Color", "Gardening"]},
        strip=True, progress={1: "done", 2: 1}),
    S("Shogi Club Days", "forest", vols("Shogi Club Days", range(1, 4)), {
        "title_english": "Shogi Club Days", "title_native": "将棋部の日々",
        "description": "The school shogi club has two members and one board. Then a transfer student "
                       "who has never lost a game walks in.",
        "publication_status": "HIATUS", "start_year": 2018, "average_score": 77,
        "genres": ["Slice of Life", "Sports", "Comedy"], "tags": ["School Club", "Board Games"]}),
    S("Neon Ronin", "neon", vols("Neon Ronin", [1, 2, 3]), {
        "title_english": "Neon Ronin", "title_native": "ネオン浪人",
        "description": "A swordsman without a master takes odd jobs in a city that never turns its lights "
                       "off. One of the files of this series can't be opened, on purpose: see Needs "
                       "attention.",
        "publication_status": "RELEASING", "start_year": 2020, "average_score": 84,
        "genres": ["Action", "Sci-Fi"], "tags": ["Cyberpunk", "Samurai", "Anti-Hero"]},
        broken=["Neon Ronin v04.cbz"]),
    S("The Quiet Lighthouse", "sea", vols("The Quiet Lighthouse", range(1, 4)), {
        "title_english": "The Quiet Lighthouse", "title_native": "静かな灯台",
        "description": "A lighthouse keeper on a tiny island writes down everything the sea brings in. "
                       "Today it brought a girl who doesn't remember her name.",
        "publication_status": "FINISHED", "start_year": 2015, "volumes_total": 3, "average_score": 90,
        "genres": ["Drama", "Mystery", "Slice of Life"], "tags": ["Island", "Amnesia", "Seinen"]},
        progress={1: "done", 2: "done", 3: "done"}, favorite=True),
    S("Pixel Witch", "candy", chs("Pixel Witch", [1, 2, 3, 5, 6, 7]), {
        "title_english": "Pixel Witch", "title_native": "ピクセル魔女",
        "description": "A witch who learned magic from old video games casts spells in 8-bit. Her "
                       "spells work. Mostly. Chapter 4 is missing on purpose: see Needs attention.",
        "publication_status": "RELEASING", "start_year": 2024, "average_score": 72,
        "genres": ["Comedy", "Fantasy"], "tags": ["Magic", "Video Games", "Gag"]}),
    S("Deep Sea Post Office", "sea", vols("Deep Sea Post Office", range(1, 3)), {
        "title_english": "Deep Sea Post Office", "title_native": "深海郵便局",
        "description": "The only post office at the bottom of the ocean is run by an octopus and a very "
                       "patient diver.",
        "publication_status": "RELEASING", "start_year": 2022, "average_score": 76,
        "genres": ["Adventure", "Slice of Life", "Fantasy"], "tags": ["Ocean", "Animals", "Iyashikei"]}),
    S("Glass Tide", "steel", vols("Glass Tide", range(1, 5)), {
        "title_english": "Glass Tide", "title_native": "硝子の潮",
        "description": "Every night at 3:14 the sea outside town turns to glass for one minute. Four "
                       "students decide to walk on it.",
        "publication_status": "FINISHED", "start_year": 2017, "volumes_total": 4, "average_score": 85,
        "genres": ["Mystery", "Psychological", "Thriller"], "tags": ["Small Town", "Time Loop"]}),
    S("Snowfield Bakery", "snow", [*chs("Snowfield Bakery", range(1, 6)), "[Other Group] Snowfield Bakery ch003.cbz"], {
        "title_english": "Snowfield Bakery", "title_native": "雪原のパン屋",
        "description": "A bakery in a village that is snowed in six months a year. Bread, soup and a lot "
                       "of very warm people. Chapter 3 is here twice on purpose: see Needs attention.",
        "publication_status": "RELEASING", "start_year": 2023, "average_score": 80,
        "genres": ["Slice of Life", "Romance"], "tags": ["Food", "Countryside"]}),
    S("Desert Cartographer", "sand", vols("Desert Cartographer", range(1, 3)), {
        "title_english": "Desert Cartographer", "title_native": "砂漠の地図師",
        "description": "The dunes move every night, so the maps have to be drawn again every morning.",
        "publication_status": "RELEASING", "start_year": 2021, "average_score": 78,
        "genres": ["Adventure", "Fantasy"], "tags": ["Desert", "Maps", "Journey"]}),
    # a second library: Comics
    S("Captain Comet", "night", [f"Captain Comet #{n:03d}.cbz" for n in range(1, 5)], {
        "title_english": "Captain Comet",
        "description": "A retired superhero runs a repair shop for jetpacks. The villains keep coming in "
                       "as customers.",
        "publication_status": "RELEASING", "start_year": 2020, "average_score": 73,
        "genres": ["Action", "Comedy"], "tags": ["Superhero"]}, library="Comics"),
    S("Night Shift Detectives", "steel", [f"Night Shift Detectives #{n:03d}.cbz" for n in range(1, 4)], {
        "title_english": "Night Shift Detectives",
        "description": "Two detectives who only work between midnight and six in the morning.",
        "publication_status": "FINISHED", "start_year": 2014, "average_score": 79,
        "genres": ["Mystery", "Thriller"], "tags": ["Detective", "Noir"]}, library="Comics"),
]


def make_library(root: Path) -> dict[str, Path]:
    """Writes every series' folder under root/<library>/ and returns {library name: its folder}."""
    folders = {}
    for i, s in enumerate(SERIES):
        lib = root / s["library"]
        folders[s["library"]] = lib
        rng = random.Random(i)
        title = s["meta"]["title_english"]
        for name in s["books"]:
            label = Path(name).stem.replace(s["folder"], "").strip() or "One-shot"
            write_book(lib / s["folder"] / name, title, s["palette"], label, rng,
                       size=STRIP if s["strip"] else PAGE, cover=not s["strip"] or name == s["books"][0])
        for name in s["broken"]:
            (lib / s["folder"] / name).write_bytes(b"this file is not a comic book archive")
        # the series' own poster, uploaded through the app by demo/build.py
        cover = cover_image(title, s["palette"])
        cover.save(lib.parent / f"_poster_{i}.webp", "WEBP", quality=70)
    return folders
