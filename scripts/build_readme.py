"""Собирает README.md — базу медиатеки — из тегов аудиофайлов в ~/Music.

Запуск из корня репозитория:  python3 scripts/build_readme.py
Нужен ffprobe (brew install ffmpeg). Другая папка с музыкой: MUSIC=/path python3 scripts/build_readme.py

Медиатека разложена как ЖАНР/Артист/Альбом. Альбом = папка с аудиофайлами;
папки CD1/CD2 сводятся к родительской. Название, артист и год берутся из тегов
первого файла (для образа с CUE — из CUE), формат — из самого файла.
"""
import glob, json, os, re, subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC = os.environ.get("MUSIC", os.path.expanduser("~/Music"))
SKIP_TOP = {"Music", "CUE test - Shelly Manne 2-3-4"}  # служебная папка Apple Music и тестовая копия альбома
AUDIO = re.compile(r"\.(flac|dsf|dff|wav|aiff?|ape|mp3|m4a|iso)$", re.I)
DISC = re.compile(r"^(cd|disc|disk)[ ._-]*\d", re.I)
BOX = "Blue Note 75th"  # бокс из 75 альбомов: показываем отдельным свёрнутым списком

SECTIONS = [("JAZZ", "Джаз"), ("Nu Jazz:Funk", "Ню-джаз"), ("ELECTRO", "Электроника"),
            ("AMBIENT", "Эмбиент и даунтемпо"), ("ROCK", "Рок и авторская песня"), ("BLUES", "Блюз")]
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
          "Buddha-Bar": "Ravin"}
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
    "One From The Heart": ("One from the Heart", None),
    "Your Queen Is A Reptile": ("Your Queen Is a Reptile", None),
    "Brawlers": ("Orphans: Brawlers, Bawlers & Bastards", "2006"),  # три диска Orphans — одной строкой
}
DROP = {"Spirit Of Eden", "DJ-Kicks: Moodymann", "Bawlers", "Bastards"}
FMT_ORDER = ["DSD256", "DSD128", "DSD64", "24/192", "24/96", "24/88.2", "24/48", "24/44.1", "16/44.1", "SACD", "MP3"]


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
                         capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
    except ValueError:
        return {}, {}
    tags = {k.lower(): v for k, v in d.get("format", {}).get("tags", {}).items()}
    return tags, next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})


def cue_tags(folder):
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith((".cue", ".cue.off")) and not f.startswith("._"):
            head = open(os.path.join(folder, f), "rb").read().decode("utf-8", "replace").split("TRACK", 1)[0]
            get = lambda k: (re.search(rf'^\s*{k}\s+"?(.+?)"?\s*$', head, re.M) or [None, ""])[1]
            return {"album": get("TITLE"), "artist": get("PERFORMER"), "date": get("REM DATE")}
    return {}


def fmt(codec, sr, bits):
    if codec == "iso":
        return "SACD"
    if codec.startswith("dsd"):  # ffprobe отдаёт частоту DSD, делённую на 8
        return {"352800": "DSD64", "705600": "DSD128", "1411200": "DSD256"}.get(sr, "DSD")
    if codec == "mp3":
        return "MP3"
    return f"{bits or 16}/{int(sr) / 1000:g}"


def clean(title):
    prev = None
    while prev != title:
        prev = title
        for rx in JUNK:
            title = re.sub(rx, "", title, flags=re.I)
    return title.strip()


def scan():
    """→ {жанр: [(артист, альбом, год, формат)]}"""
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
            artist, album, year, f = os.path.basename(os.path.dirname(unit)), m[2], m[1], "SACD"
        else:
            tags, st = probe(first)
            if not tags.get("album"):
                tags = {**cue_tags(os.path.dirname(first)), **{k: v for k, v in tags.items() if v}}
            artist = tags.get("album_artist") or tags.get("albumartist") or tags.get("artist") or ""
            album, year = clean(tags.get("album", "")), (tags.get("date") or tags.get("year") or "")[:4]
            f = fmt(st.get("codec_name", ""), st.get("sample_rate", "0"), st.get("bits_per_raw_sample"))
        if album in DROP:
            continue
        artist = ARTIST.get(artist.strip(), artist.strip())
        album, fix = ALBUM.get(album, (album, None))
        genre = top if top in rows else GENRE_BY_ARTIST.get(artist)
        if genre:
            rows[genre].append((artist, album, fix or year, f))
        else:
            print(f"! жанр не определён, пропущено: {os.path.relpath(unit, MUSIC)}")
    return rows


def box():
    """Состав бокса Blue Note из имён папок «CDnn - Артист - Альбом (год)»."""
    d = (glob.glob(os.path.join(MUSIC, "JAZZ", "*", f"*{BOX}*")) or [""])[0]
    items = [re.match(r"CD(\d+) - (.+?) - (.+) \((\d{4})\)$", x) for x in sorted(os.listdir(d))] if d else []
    return [(int(m[1]), m[2], m[3], m[4]) for m in items if m]


def render(rows, bn):
    total = sum(len(v) for v in rows.values()) + len(bn)
    fmts = {}
    for r in (x for v in rows.values() for x in v):
        fmts[r[3]] = fmts.get(r[3], 0) + 1
    if bn:
        fmts["24/192"] = fmts.get("24/192", 0) + len(bn)
    out = ["# Медиатека\n",
           f"{total} альбомов для офлайн-прослушивания. Плеер — Cayin N3Ultra, наушники — Moondrop Blessing.",
           "Хард-боп Blue Note, необычная электроника, ню-джаз и Tom Waits.\n",
           "## Как устроено\n",
           "Файлы лежат по схеме `ЖАНР/Артист/Альбом`. Шесть жанровых папок — шесть разделов ниже, в том же порядке.",
           "В каждой таблице: артист, альбом, год выпуска и формат файла.\n",
           "| Жанр | Папка | Альбомов | Артистов |\n|---|---|---:|---:|"]
    for key, title in SECTIONS:
        n = len(rows[key]) + (len(bn) if key == "JAZZ" else 0)
        artists = {r[0] for r in rows[key]} | ({b[1] for b in bn} if key == "JAZZ" else set())
        out.append(f"| [{title}](#{title.lower().replace(' ', '-')}) | `{key}/` | {n} | {len(artists)} |")
    out += [f"| **Всего** | | **{total}** | |\n",
            "### Формат\n",
            "| Обозначение | Что значит |\n|---|---|",
            "| `24/192`, `24/96` … | 24 бита, частота в кГц — хайрез |",
            "| `16/44.1` | 16 бит, 44.1 кГц — качество CD |",
            "| `DSD128`, `DSD256` | DSD: частота в 128 и 256 раз выше 44.1 кГц |",
            "| `SACD` | образ диска Super Audio CD |",
            "| `MP3` | сжатый файл |\n",
            "По форматам: " + " · ".join(f"`{f}` — {fmts[f]}" for f in FMT_ORDER if f in fmts) + ".\n"]
    for key, title in SECTIONS:
        out += [f"\n## {title}\n", f"Папка `{key}/`\n"]
        if key == "JAZZ" and bn:
            out.append(f"**Blue Note 75th Anniversary Collection** — {len(bn)} альбомов "
                       f"{min(b[3] for b in bn)}–{max(b[3] for b in bn)}, 24/192.\n")
            out.append("<details>\n<summary>Состав бокса</summary>\n\n| # | Артист | Альбом | Год |\n|---:|---|---|---|")
            out += [f"| {n} | {a} | {t} | {y} |" for n, a, t, y in bn]
            out.append("\n</details>\n")
        out.append("| Артист | Альбом | Год | Формат |\n|---|---|---|---|")
        out += [f"| {a} | {t} | {y} | {f} |" for a, t, y, f in sorted(rows[key], key=lambda r: (r[0].lower(), r[2]))]
    out.append("\n---\n\nЧто хочу добавить — [wishlist.md](wishlist.md).")
    out.append("База собирается из тегов файлов: `python3 scripts/build_readme.py`.")
    return "\n".join(out) + "\n", total


def selfcheck():
    assert fmt("flac", "192000", "24") == "24/192" and fmt("flac", "44100", None) == "16/44.1"
    assert fmt("dsd_lsbf_planar", "705600", None) == "DSD128" and fmt("iso", "", "") == "SACD"
    assert clean("Rain Dogs (2023 Remaster)") == "Rain Dogs"
    assert clean("Mezzanine (20th Anniversary Deluxe Edition) (Deluxe)") == "Mezzanine"
    assert clean("The Last Ship (Expanded Edition) CD2") == "The Last Ship (Expanded Edition)"
    assert clean("The Slow Light (24bit-88.2kHz Resolution)") == "The Slow Light"


if __name__ == "__main__":
    selfcheck()
    text, total = render(scan(), box())
    open(os.path.join(REPO, "README.md"), "w", encoding="utf-8").write(text)
    print(f"README.md: {total} альбомов")
