"""Собирает README.md — базу медиатеки — из тегов аудиофайлов в ~/Music и заметок из notes.tsv.

Запуск из корня репозитория:  python3 scripts/build_readme.py
Нужен ffprobe (brew install ffmpeg). Другая папка с музыкой: MUSIC=/path python3 scripts/build_readme.py

Медиатека разложена как ЖАНР/Артист/Альбом. Альбом = папка с аудиофайлами;
папки CD1/CD2 сводятся к родительской. Название, артист и год берутся из тегов
первого файла (для образа с CUE — из CUE). Заметка к альбому — из notes.tsv
(артист, альбом, заметка через табуляцию); нет заметки — ячейка пустая.
"""
import csv, glob, json, os, re, subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC = os.environ.get("MUSIC", os.path.expanduser("~/Music"))
SKIP_TOP = {"Music", "CUE test - Shelly Manne 2-3-4"}  # служебная папка Apple Music и тестовая копия альбома
AUDIO = re.compile(r"\.(flac|dsf|dff|wav|aiff?|ape|mp3|m4a|iso)$", re.I)
DISC = re.compile(r"^(cd|disc|disk)[ ._-]*\d", re.I)
BOX = "Blue Note 75th"  # бокс из 75 альбомов: показываем отдельным свёрнутым списком

SECTIONS = [("JAZZ", "Джаз"), ("Nu Jazz:Funk", "Ню-джаз"), ("ELECTRO", "Электроника"),
            ("AMBIENT", "Эмбиент и даунтемпо"), ("ROCK", "Рок"), ("BLUES", "Блюз")]
# Альбомы, ещё не разложенные органайзером по жанрам: жанр по артисту
GENRE_BY_ARTIST = {"Ricardo Villalobos": "ELECTRO", "Skee Mask": "ELECTRO", "Sons of Kemet": "JAZZ",
                   "Yussef Kamaal": "Nu Jazz:Funk", "Nubya Garcia": "JAZZ", "Kamasi Washington": "JAZZ",
                   "Bill Evans Trio": "JAZZ"}
ARTIST = {"Boards Of Canada": "Boards of Canada", "Four 80 East": "Four80East",
          "Four80East and Cece Peniston": "Four80East", "Villalobos": "Ricardo Villalobos",
          "Sons Of Kemet": "Sons of Kemet", "N'to": "Various Artists", "VA": "Various Artists",
          "Keith Jarrett;Jan Garbarek, Palle Danielsson, Jon Christensen": "Keith Jarrett / Jan Garbarek",
          "Floating Points, Pharoah Sanders, The London Symphony Orchestra": "Floating Points, Pharoah Sanders & LSO",
          "Tom Waits And Crystal Gayle": "Tom Waits & Crystal Gayle", "Jean Michel Jarre": "Jean-Michel Jarre",
          "Buddha-Bar": "Ravin",
          # бокс Blue Note: сокращения из имён папок
          "Art Blakey & TJM": "Art Blakey & The Jazz Messengers",
          "Horace Silver And The Jazz Messengers": "Horace Silver"}
# Служебные хвосты в тегах альбома: ремастер, разрядность, номер диска, издание
JUNK = [r"\s*\[[^\]]*\]$", r",\s*\(.*$", r"\s*\((\d{4} )?Remaster(ed)?(, \d{4})?\)$", r"\s*\(24 ?bit[^)]*\)",
        r"\s*\(Reissue\)$", r"\s*\(Instrumental\)$", r"\s*\([^)]*Deluxe[^)]*\)", r"\s*\([^)]*Compilation[^)]*\)",
        r"\s*\(?CD\s?\d\)?$"]
# Чистое название → (как писать, год первого выпуска или None)
ALBUM = {
    "Tomorrow's Harvest": ("Tomorrow's Harvest", "2013"),
    "Ambient 1 - Music For Airports": ("Ambient 1: Music for Airports", None),
    "XXVIII by Ravin": ("Buddha-Bar XXVIII", None),
    "Live At The Regal": ("Live at the Regal", "1965"),
    "Moanin' In The Moonlight": ("Moanin' in the Moonlight", "1959"),
    "The Best Of Muddy Waters": ("The Best of Muddy Waters", "1958"),
    "King Of The Delta Blues Singers": ("King of the Delta Blues Singers", "1961"),
    "King Of The Delta Blues Singers Vol. II": ("King of the Delta Blues Singers, Vol. II", "1970"),
    "Oxygene": ("Oxygène", "1976"),
    "Hungry 5": ("Hungry 5: The Best of 5 Years", None),
    "Belonging": ("Belonging", "1974"),
    "Violator": ("Violator", "1990"),
    "Portrait In Jazz": ("Portrait in Jazz", "1960"),
    "One From The Heart": ("One from the Heart", None),
    "Your Queen Is A Reptile": ("Your Queen Is a Reptile", None),
    "Brawlers": ("Orphans: Brawlers, Bawlers & Bastards", "2006"),  # три диска Orphans — одной строкой
    # бокс Blue Note: сокращения из имён папок
    "HS And TJM": ("Horace Silver and the Jazz Messengers", None),
    "That's Where...": ("That's Where It's At", None),
    "WITBA": ("Who Is This Bitch, Anyway?", None),
}
DROP = {"Spirit Of Eden", "DJ-Kicks: Moodymann", "Bawlers", "Bastards"}


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
                         capture_output=True, text=True).stdout
    try:
        return {k.lower(): v for k, v in json.loads(out).get("format", {}).get("tags", {}).items()}
    except ValueError:
        return {}


def cue_tags(folder):
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith((".cue", ".cue.off")) and not f.startswith("._"):
            head = open(os.path.join(folder, f), "rb").read().decode("utf-8", "replace").split("TRACK", 1)[0]
            get = lambda k: (re.search(rf'^\s*{k}\s+"?(.+?)"?\s*$', head, re.M) or [None, ""])[1]
            return {"album": get("TITLE"), "artist": get("PERFORMER"), "date": get("REM DATE")}
    return {}


def clean(title):
    prev = None
    while prev != title:
        prev = title
        for rx in JUNK:
            title = re.sub(rx, "", title, flags=re.I)
    return title.strip()


def scan():
    """→ {жанр: [(артист, альбом, год)]}"""
    units = {}
    for root, dirs, files in os.walk(MUSIC):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        rel = os.path.relpath(root, MUSIC)
        if rel == "." or rel.split(os.sep)[0] in SKIP_TOP or BOX in rel:
            continue
        aud = sorted(f for f in files if AUDIO.search(f) and not f.startswith("._"))
        if aud:
            unit = os.path.dirname(root) if DISC.match(os.path.basename(root)) else root
            units.setdefault(unit, os.path.join(root, aud[0]))

    rows = {k: [] for k, _ in SECTIONS}
    for unit, first in sorted(units.items()):
        if not os.path.exists(first):  # органайзер переложил папку во время скана — запусти ещё раз
            print(f"! папка переехала во время скана: {os.path.relpath(unit, MUSIC)}")
            continue
        top = os.path.relpath(unit, MUSIC).split(os.sep)[0]
        if first.lower().endswith(".iso"):  # у образа SACD тегов нет: «ГГГГ - Альбом (...)» в папке артиста
            m = re.match(r"(\d{4}) - (.+?)(?: \([^()]*\))?$", os.path.basename(unit))  # последняя скобка — каталожный номер
            artist, album, year = os.path.basename(os.path.dirname(unit)), m[2], m[1]
        else:
            tags = probe(first)
            if not tags.get("album"):
                tags = {**cue_tags(os.path.dirname(first)), **{k: v for k, v in tags.items() if v}}
            artist = tags.get("album_artist") or tags.get("albumartist") or tags.get("artist") or ""
            album, year = clean(tags.get("album", "")), (tags.get("date") or tags.get("year") or "")[:4]
        if album in DROP:
            continue
        artist = ARTIST.get(artist.strip(), artist.strip())
        album, fix = ALBUM.get(album, (album, None))
        genre = top if top in rows else GENRE_BY_ARTIST.get(artist)
        if genre:
            rows[genre].append((artist, album, fix or year))
        else:
            print(f"! жанр не определён, пропущено: {os.path.relpath(unit, MUSIC)}")
    return rows


def box():
    """Состав бокса Blue Note из имён папок «CDnn - Артист - Альбом (год)» → [(№, артист, альбом, год)]."""
    d = (glob.glob(os.path.join(MUSIC, "JAZZ", "*", f"*{BOX}*")) or [""])[0]
    items = [re.match(r"CD(\d+) - (.+?) - (.+) \((\d{4})\)$", x) for x in sorted(os.listdir(d))] if d else []
    return [(int(m[1]), ARTIST.get(m[2], m[2]), ALBUM.get(m[3], (m[3],))[0], m[4]) for m in items if m]


def load_notes():
    with open(os.path.join(REPO, "notes.tsv"), encoding="utf-8", newline="") as f:
        return {(r["artist"], r["album"]): r["note"] for r in csv.DictReader(f, delimiter="\t")}


def render(rows, bn, notes):
    used = set()

    def note(artist, album):
        used.add((artist, album))
        return notes.get((artist, album), "")

    total = sum(len(v) for v in rows.values()) + len(bn)
    out = ["# Медиатека\n",
           f"{total} альбомов для офлайн-прослушивания. Плеер — Cayin N3Ultra, наушники — Moondrop Blessing.",
           "Хард-боп Blue Note, необычная электроника, ню-джаз и Tom Waits.\n",
           "## Как устроено\n",
           "Файлы лежат по схеме `ЖАНР/Артист/Альбом`. Шесть жанров — шесть разделов ниже, в том же порядке.",
           "В каждой таблице: артист, альбом, год выпуска и пара слов об альбоме.\n",
           "| Жанр | Альбомов | Артистов |\n|---|---:|---:|"]
    for key, title in SECTIONS:
        n = len(rows[key]) + (len(bn) if key == "JAZZ" else 0)
        artists = {r[0] for r in rows[key]} | ({b[1] for b in bn} if key == "JAZZ" else set())
        out.append(f"| [{title}](#{title.lower().replace(' ', '-')}) | {n} | {len(artists)} |")
    out.append(f"| **Всего** | **{total}** | |")
    for key, title in SECTIONS:
        out.append(f"\n## {title}\n")
        if key == "JAZZ" and bn:
            out.append(f"**Blue Note 75th Anniversary Collection** — {len(bn)} альбомов лейбла "
                       f"{min(b[3] for b in bn)}–{max(b[3] for b in bn)}.\n")
            out.append("<details>\n<summary>Состав бокса</summary>\n\n"
                       "| # | Артист | Альбом | Год | Коротко |\n|---:|---|---|---|---|")
            out += [f"| {n} | {a} | {t} | {y} | {note(a, t)} |" for n, a, t, y in bn]
            out.append("\n</details>\n")
        out.append("| Артист | Альбом | Год | Коротко |\n|---|---|---|---|")
        out += [f"| {a} | {t} | {y} | {note(a, t)} |" for a, t, y in sorted(rows[key], key=lambda r: (r[0].lower(), r[2]))]
    out.append("\n---\n\nЧто хочу добавить — [wishlist.md](wishlist.md).")
    out.append("База собирается из тегов файлов и [notes.tsv](notes.tsv): `python3 scripts/build_readme.py`.")
    missing = sorted(k for k in used if k not in notes)
    stale = sorted(k for k in notes if k not in used)
    return "\n".join(out) + "\n", total, missing, stale


def selfcheck():
    assert clean("Rain Dogs (2023 Remaster)") == "Rain Dogs"
    assert clean("Mezzanine (20th Anniversary Deluxe Edition) (Deluxe)") == "Mezzanine"
    assert clean("The Last Ship (Expanded Edition) CD2") == "The Last Ship (Expanded Edition)"
    assert clean("The Slow Light (24bit-88.2kHz Resolution)") == "The Slow Light"


if __name__ == "__main__":
    selfcheck()
    text, total, missing, stale = render(scan(), box(), load_notes())
    open(os.path.join(REPO, "README.md"), "w", encoding="utf-8").write(text)
    print(f"README.md: {total} альбомов, без заметки: {len(missing)}")
    for a, t in stale:
        print(f"! заметка ни к чему не привязана (переименован альбом?): {a} — {t}")
