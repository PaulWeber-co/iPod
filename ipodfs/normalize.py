"""
Das Herzstueck: Alben wieder zusammenfuehren.

Die iOS-Musik-App (und praktisch jeder andere Player) bildet eine Cover-Flow-
Kachel pro Kombination aus *Album-Interpret* und *Album*. Steht bei einem Song
"Katy Perry" und beim naechsten "Katy Perry feat. Snoop Dogg", entstehen zwei
Kacheln mit demselben Cover - genau das Problem, um das es hier geht.

Ursachen, die dieses Modul erkennt und behebt:

1. ``feat.`` / ``ft.`` / ``featuring`` im Interpret-Feld
2. Begleit-Interpreten per ``&`` / ``,`` / ``x`` / ``vs.`` ohne feat.-Marker
3. Album-Interpret (TPE2) fehlt, ist uneinheitlich oder faelschlich
   "Various Artists"
4. Album-Namen, die sich nur durch Zusaetze wie ``(Deluxe Edition)``,
   ``[Explicit]`` oder ``(Disc 1)`` unterscheiden
5. Unsichtbare Unterschiede: geschuetzte Leerzeichen, typografische
   Apostrophe, doppelte Leerzeichen, unterschiedliche Unicode-Normalform
6. Uneinheitliche oder mehrfach eingebettete Cover
7. Das Kompilations-Flag (TCMP/cpil), das einzelne Songs nach
   "Compilations" schiebt
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .tags import Track, tile_key

# --------------------------------------------------------------- Textputz
_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "​": "", "﻿": "",
}
_QUOTE_RE = re.compile("|".join(map(re.escape, _QUOTES)))


def clean_text(value: str) -> str:
    """Unsichtbare Unterschiede entfernen - sonst spaltet iOS stillschweigend."""
    if not value:
        return ""
    value = unicodedata.normalize("NFC", value)
    value = _QUOTE_RE.sub(lambda m: _QUOTES[m.group()], value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def fold(value: str) -> str:
    """Vergleichsform: ohne Akzente, ohne Satzzeichen, klein."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", clean_text(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip().casefold()


# ------------------------------------------------------------- feat.-Erkennung
_FEAT_WORDS = r"feat|ft|featuring|featg|with|w/|avec|con|mit|vs|versus|x"

#: In Klammern - immer eindeutig, immer sicher abtrennbar.
_FEAT_BRACKET_RE = re.compile(
    rf"\s*[\(\[\{{]\s*(?:{_FEAT_WORDS})\.?\s+(?P<guests>[^)\]\}}]+)[\)\]\}}]",
    re.IGNORECASE,
)
#: Freistehend am Ende - "Katy Perry feat. Snoop Dogg".
_FEAT_INLINE_RE = re.compile(
    rf"\s+(?:{_FEAT_WORDS})\.?\s+(?P<guests>.+)$",
    re.IGNORECASE,
)
#: Trenner fuer Gastlisten.
_GUEST_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|/|\band\b|\bund\b)\s*", re.IGNORECASE)

#: Trenner ohne feat.-Marker. Nur mit Kontext (Album-Mehrheit) verwendet,
#: damit "Simon & Garfunkel" oder "Earth, Wind & Fire" heil bleiben.
_SOFT_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|/|\bx\b|\bvs\.?\b|\band\b)\s*", re.IGNORECASE)


def split_featuring(artist: str) -> tuple[str, list[str]]:
    """
    ``"Katy Perry feat. Snoop Dogg"`` -> ``("Katy Perry", ["Snoop Dogg"])``

    Nur explizite feat.-Marker werden ausgewertet; blosse ``&``-Verbindungen
    bleiben unangetastet (dafuer gibt es die Album-Kontextanalyse).
    """
    text = clean_text(artist)
    if not text:
        return "", []

    guests: list[str] = []

    def _collect(match: re.Match) -> str:
        guests.extend(g.strip() for g in _GUEST_SPLIT_RE.split(match.group("guests")) if g.strip())
        return ""

    text = _FEAT_BRACKET_RE.sub(_collect, text)
    inline = _FEAT_INLINE_RE.search(text)
    if inline:
        # "with" / "x" / "mit" inline sind zu unsicher - nur im Klammerfall.
        marker = re.match(rf"\s+({_FEAT_WORDS})\.?\s", inline.group(0), re.IGNORECASE)
        if marker and marker.group(1).lower() in ("feat", "ft", "featuring", "featg"):
            _collect(inline)
            text = text[: inline.start()]

    main = clean_text(text).rstrip(" -,;&/+")
    return (main or clean_text(artist)), guests


# --------------------------------------------------------- Album-Namensputz
_ALBUM_DECORATIONS = [
    r"deluxe(?:\s+(?:edition|version))?", r"expanded(?:\s+edition)?",
    r"special\s+edition", r"anniversary\s+edition", r"collector'?s?\s+edition",
    r"remaster(?:ed)?(?:\s+\d{4})?", r"\d{4}\s+remaster(?:ed)?",
    r"explicit(?:\s+version)?", r"clean(?:\s+version)?",
    r"bonus\s+track\s+version", r"international\s+version",
    r"the\s+complete\s+edition", r"platinum\s+edition", r"gold\s+edition",
    r"single", r"ep", r"original\s+motion\s+picture\s+soundtrack",
]
_DECORATION_RE = re.compile(
    r"\s*[\(\[]\s*(?:" + "|".join(_ALBUM_DECORATIONS) + r")\s*[\)\]]",
    re.IGNORECASE,
)
_DASH_DECORATION_RE = re.compile(
    r"\s+-\s+(?:" + "|".join(_ALBUM_DECORATIONS) + r")\s*$",
    re.IGNORECASE,
)
_DISC_RE = re.compile(
    r"\s*[\(\[]?\s*(?:disc|disk|cd|volume|vol\.?|teil|part)\s*\.?\s*(\d{1,2})\s*[\)\]]?\s*$",
    re.IGNORECASE,
)


def strip_album_decorations(album: str) -> tuple[str, Optional[int]]:
    """
    ``"Teenage Dream (Deluxe Edition) [Disc 2]"`` -> ``("Teenage Dream", 2)``

    Der zweite Rueckgabewert ist die erkannte Disc-Nummer, damit sie in TPOS
    landen kann statt den Albumnamen zu spalten.
    """
    text = clean_text(album)
    if not text:
        return "", None

    disc: Optional[int] = None
    match = _DISC_RE.search(text)
    if match:
        disc = int(match.group(1))
        text = text[: match.start()].strip()

    previous = None
    while previous != text:
        previous = text
        text = _DECORATION_RE.sub("", text)
        text = _DASH_DECORATION_RE.sub("", text)

    text = clean_text(text).rstrip(" -,;([{").strip()
    return (text or clean_text(album)), disc


# ------------------------------------------------------------------- Optionen
@dataclass
class FixOptions:
    """Was der Reparaturlauf anfassen darf."""

    set_album_artist: bool = True          # TPE2 vereinheitlichen
    unify_album_names: bool = True         # (Deluxe Edition) & Co. zusammenfuehren
    clean_invisible: bool = True           # NBSP, typografische Zeichen, Doppelspaces
    unify_artwork: bool = True             # ein Cover pro Album, genau eines
    fix_compilation_flag: bool = True      # TCMP konsistent setzen
    write_sort_tags: bool = True           # TSO2/TSOP/TSOA fuer stabile Sortierung
    fill_disc_numbers: bool = True         # "(Disc 2)" -> TPOS
    move_feat_to_title: bool = False       # "feat. X" aus Interpret in Titel
    various_artists_threshold: int = 4     # ab so vielen echten Hauptinterpreten: Sampler

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "FixOptions":
        data = data or {}
        opts = cls()
        for key in opts.__dataclass_fields__:
            if key in data and data[key] is not None:
                setattr(opts, key, type(getattr(opts, key))(data[key]))
        return opts


VARIOUS_ARTISTS = "Various Artists"
_VA_ALIASES = {"various artists", "various", "va", "v a", "diverse", "verschiedene",
               "verschiedene interpreten", "sampler", "compilation", "unknown artist"}


# --------------------------------------------------------------- Album-Gruppen
@dataclass
class TrackChange:
    """Geplante Aenderung an genau einer Datei."""

    path: str
    filename: str
    fields: dict[str, tuple] = field(default_factory=dict)   # feld -> (alt, neu)
    set_artwork: bool = False
    artwork_source: str = ""

    @property
    def empty(self) -> bool:
        return not self.fields and not self.set_artwork

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "filename": self.filename,
            "fields": {k: {"old": v[0], "new": v[1]} for k, v in self.fields.items()},
            "set_artwork": self.set_artwork,
            "artwork_source": self.artwork_source,
        }


@dataclass
class AlbumPlan:
    """Ein Album samt Diagnose und Reparaturvorschlag."""

    key: str
    album: str
    album_artist: str
    tracks: list[Track]
    changes: list[TrackChange] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    tiles_before: int = 1
    tiles_after: int = 1
    artist_variants: list[str] = field(default_factory=list)
    album_variants: list[str] = field(default_factory=list)
    artwork_variants: int = 0
    is_compilation: bool = False

    @property
    def needs_fix(self) -> bool:
        return any(not c.empty for c in self.changes)

    def to_dict(self, include_tracks: bool = True) -> dict:
        data = {
            "key": self.key,
            "album": self.album,
            "album_artist": self.album_artist,
            "track_count": len(self.tracks),
            "duration": sum(t.duration for t in self.tracks),
            "issues": self.issues,
            "tiles_before": self.tiles_before,
            "tiles_after": self.tiles_after,
            "artist_variants": self.artist_variants,
            "album_variants": self.album_variants,
            "artwork_variants": self.artwork_variants,
            "is_compilation": self.is_compilation,
            "needs_fix": self.needs_fix,
            "changes": [c.to_dict() for c in self.changes if not c.empty],
        }
        if include_tracks:
            data["tracks"] = [t.to_dict() for t in self.tracks]
        return data


ISSUE_LABELS = {
    "feat_split": "Album zerfaellt durch feat./ft. im Interpret",
    "guest_split": "Album zerfaellt durch Gast-Interpreten (&, x, vs.)",
    "no_album_artist": "Album-Interpret (TPE2) fehlt",
    "album_artist_mismatch": "Album-Interpret uneinheitlich",
    "album_variants": "Albumname in mehreren Schreibweisen",
    "invisible_chars": "Unsichtbare Zeichenunterschiede",
    "artwork_mismatch": "Unterschiedliche oder fehlende Cover",
    "artwork_duplicate": "Mehrere Bilder pro Datei eingebettet",
    "compilation_flag": "Kompilations-Flag uneinheitlich",
    "disc_in_album_name": "Disc-Nummer steckt im Albumnamen",
    "missing_sort_tags": "Sortier-Tags fehlen",
}


def _album_group_key(track: Track) -> str:
    """
    Gruppierung fuer die *Analyse*: bewusst grosszuegiger als die iOS-Regel,
    damit auseinandergefallene Teile desselben Albums wieder zueinander finden.
    """
    album_clean, _ = strip_album_decorations(track.album)
    main, _ = split_featuring(track.artist)
    base = fold(album_clean)
    if not base:
        return "\x00noalbum\x00" + fold(track.effective_album_artist or main)
    # Album-Interpret bewusst NICHT in den Schluessel: sonst bliebe genau die
    # Spaltung bestehen, die wir reparieren wollen.
    return base


def group_albums(tracks: Iterable[Track]) -> dict[str, list[Track]]:
    groups: dict[str, list[Track]] = defaultdict(list)
    for track in tracks:
        groups[_album_group_key(track)].append(track)
    return dict(groups)


def _longest_common_artist(artists: list[str]) -> Optional[str]:
    """
    Ohne feat.-Marker: taucht ein Interpret als Praefix aller anderen auf,
    ist er der Hauptinterpret ("Katy Perry", "Katy Perry & Snoop Dogg").
    """
    if not artists:
        return None
    candidates = sorted({a for a in artists if a}, key=len)
    for candidate in candidates:
        folded = fold(candidate)
        if not folded:
            continue
        ok = True
        for other in artists:
            other_folded = fold(other)
            if other_folded == folded:
                continue
            if not other_folded.startswith(folded + " "):
                ok = False
                break
            rest = other_folded[len(folded):].strip()
            # Nach dem Praefix muss ein Trenner gestanden haben.
            if not _SOFT_SPLIT_RE.match(other[len(candidate):].lstrip() or " ") and rest == other_folded:
                ok = False
                break
        if ok:
            return candidate
    return None


def _pick_album_artist(tracks: list[Track], opts: FixOptions) -> tuple[str, list[str], bool]:
    """-> (Album-Interpret, erkannte Probleme, ist_kompilation)"""
    issues: list[str] = []

    existing = [clean_text(t.albumartist) for t in tracks if clean_text(t.albumartist)]
    existing_real = [a for a in existing if fold(a) not in _VA_ALIASES]
    raw_artists = [clean_text(t.artist) for t in tracks if clean_text(t.artist)]

    mains: list[str] = []
    has_feat = False
    for artist in raw_artists:
        main, guests = split_featuring(artist)
        if guests:
            has_feat = True
        mains.append(main)

    if has_feat and len({fold(m) for m in mains}) < len({fold(a) for a in raw_artists}):
        issues.append("feat_split")

    distinct_mains = {fold(m) for m in mains if m}
    if len({fold(a) for a in existing}) > 1:
        issues.append("album_artist_mismatch")
    if len(existing) < len(tracks):
        issues.append("no_album_artist")

    # 1. Vorhandener, einheitlicher, echter Album-Interpret gewinnt.
    if existing_real and len({fold(a) for a in existing_real}) == 1:
        return Counter(existing_real).most_common(1)[0][0], issues, False

    # 2. Ein einziger Hauptinterpret nach feat.-Abtrennung.
    if len(distinct_mains) == 1:
        return Counter(mains).most_common(1)[0][0], issues, False

    # 3. Gemeinsamer Praefix (", &, x, vs." ohne feat.-Marker).
    common = _longest_common_artist(mains)
    if common:
        issues.append("guest_split")
        return common, issues, False

    # 4. Deutliche Mehrheit -> dominanter Interpret, Rest sind Gaeste.
    counts = Counter(fold(m) for m in mains if m)
    if counts:
        top_folded, top_count = counts.most_common(1)[0]
        if len(counts) < opts.various_artists_threshold and top_count >= max(2, len(tracks) * 0.5):
            display = next(m for m in mains if fold(m) == top_folded)
            issues.append("guest_split")
            return display, issues, False

    # 5. Echter Sampler.
    if existing and all(fold(a) in _VA_ALIASES for a in existing) and len({fold(a) for a in existing}) == 1:
        issues = [i for i in issues if i != "album_artist_mismatch"]
    return VARIOUS_ARTISTS, issues, True


def _pick_album_name(tracks: list[Track], opts: FixOptions) -> tuple[str, list[str], list[str]]:
    """-> (Albumname, Varianten, Probleme)"""
    issues: list[str] = []
    originals = [t.album for t in tracks if t.album.strip()]
    raw = [clean_text(a) for a in originals]
    if not raw:
        return "", [], issues

    # Varianten bewusst unbereinigt zeigen: "Teenage Dream " und
    # "Teenage Dream" sehen gleich aus, sind fuer den iPod aber zwei Alben.
    variants = sorted(set(originals))
    if len({fold(a) for a in raw}) > 1:
        issues.append("album_variants")
    elif len(variants) > 1:
        issues.append("invisible_chars")

    if opts.unify_album_names:
        stripped = [strip_album_decorations(a)[0] for a in raw]
        if any(fold(s) != fold(a) for s, a in zip(stripped, raw)):
            issues.append("album_variants")
        pool = stripped
    else:
        pool = raw

    # Haeufigste Schreibweise gewinnt; bei Gleichstand die kuerzeste.
    counts = Counter(pool)
    best = max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
    return best, variants, issues


def _pick_artwork(tracks: list[Track]) -> tuple[str, int, list[str]]:
    """-> (Pfad der Quelldatei, Anzahl Cover-Varianten, Probleme)"""
    issues: list[str] = []
    with_art = [t for t in tracks if t.art_hash]
    hashes = {t.art_hash for t in with_art}

    if any(t.art_count > 1 for t in tracks):
        issues.append("artwork_duplicate")
    if not with_art:
        return "", 0, issues
    if len(hashes) > 1 or len(with_art) != len(tracks):
        issues.append("artwork_mismatch")

    # Groesstes Bild, bei Gleichstand das haeufigste.
    freq = Counter(t.art_hash for t in with_art)
    best = max(with_art, key=lambda t: (t.art_bytes, freq[t.art_hash]))
    return best.path, len(hashes), issues


def _sort_name(value: str) -> str:
    """'The Beatles' -> 'Beatles, The' (stabile Sortierung wie bei iTunes)."""
    text = clean_text(value)
    match = re.match(r"^(the|die|der|das|a|an|le|la|les|el|los)\s+(.+)$", text, re.IGNORECASE)
    if match:
        return f"{match.group(2)}, {match.group(1)}"
    return text


def plan_album(tracks: list[Track], opts: FixOptions, key: str = "") -> AlbumPlan:
    """Ein Album analysieren und alle noetigen Aenderungen zusammenstellen."""
    tracks = [t for t in tracks if not t.error]
    plan = AlbumPlan(key=key, album="", album_artist="", tracks=tracks)
    if not tracks:
        return plan

    tiles_before = {t.grouping_key for t in tracks}
    plan.tiles_before = len(tiles_before)

    album_artist, artist_issues, is_comp = _pick_album_artist(tracks, opts)
    album_name, album_variants, album_issues = _pick_album_name(tracks, opts)
    art_source, art_variants, art_issues = _pick_artwork(tracks)

    plan.album = album_name or "(Kein Album)"
    plan.album_artist = album_artist
    plan.is_compilation = is_comp
    plan.artwork_variants = art_variants
    plan.album_variants = album_variants
    plan.artist_variants = sorted({t.artist for t in tracks if t.artist.strip()})

    issues = list(dict.fromkeys(artist_issues + album_issues + art_issues))

    comp_flags = {t.compilation for t in tracks}
    if len(comp_flags) > 1 or (is_comp not in comp_flags):
        issues.append("compilation_flag")

    if opts.write_sort_tags and any(not t.sort_albumartist for t in tracks):
        issues.append("missing_sort_tags")

    disc_hints = {t.path: strip_album_decorations(t.album)[1] for t in tracks}
    if opts.fill_disc_numbers and any(v for v in disc_hints.values()):
        issues.append("disc_in_album_name")

    if opts.clean_invisible:
        for track in tracks:
            for value in (track.artist, track.album, track.title, track.albumartist):
                if value and clean_text(value) != value:
                    issues.append("invisible_chars")
                    break

    # Ein Album, das schon jetzt in genau einer Kachel landet, zerfaellt nicht -
    # dann sind "feat."-Hinweise & Co. erledigt und wuerden nur verwirren.
    if plan.tiles_before == 1:
        resolved = {"feat_split", "guest_split", "album_artist_mismatch",
                    "no_album_artist", "album_variants"}
        issues = [i for i in issues if i not in resolved]

    plan.issues = list(dict.fromkeys(issues))

    total_tracks = max([t.track or 0 for t in tracks] + [0]) or None
    disc_values = [d for d in disc_hints.values() if d] or [t.disc for t in tracks if t.disc]
    total_discs = max(disc_values) if disc_values else None

    for track in tracks:
        change = TrackChange(path=track.path, filename=track.filename)

        def propose(field_name: str, new_value) -> None:
            old_value = getattr(track, field_name)
            if isinstance(new_value, str):
                new_value = new_value.strip()
            if new_value in (None, "") and old_value in (None, ""):
                return
            if new_value != old_value:
                change.fields[field_name] = (old_value, new_value)

        if opts.clean_invisible:
            propose("title", clean_text(track.title))
            propose("artist", clean_text(track.artist))
            propose("genre", clean_text(track.genre))

        if opts.move_feat_to_title:
            main, guests = split_featuring(track.artist)
            if guests:
                propose("artist", main)
                title = clean_text(change.fields.get("title", (track.title, track.title))[1])
                if "feat" not in title.lower() and "ft." not in title.lower():
                    propose("title", f"{title} (feat. {', '.join(guests)})")

        if opts.set_album_artist:
            propose("albumartist", album_artist)

        if album_name:
            if opts.unify_album_names:
                propose("album", album_name)
            elif opts.clean_invisible:
                # Varianten bleiben bestehen, nur unsichtbarer Unrat fliegt raus.
                propose("album", clean_text(track.album))

        if opts.fill_disc_numbers:
            hint = disc_hints.get(track.path)
            if hint and track.disc != hint:
                propose("disc", hint)
            if total_discs and total_discs > 1:
                if not track.disc and not hint:
                    propose("disc", 1)
                propose("disc_total", total_discs)

        if total_tracks and track.track and track.track_total != total_tracks:
            propose("track_total", total_tracks)

        if opts.fix_compilation_flag and track.compilation != is_comp:
            change.fields["compilation"] = (track.compilation, is_comp)

        if opts.write_sort_tags:
            propose("sort_albumartist", _sort_name(album_artist))
            if album_name:
                propose("sort_album", _sort_name(album_name))
            artist_now = change.fields.get("artist", (track.artist, track.artist))[1]
            propose("sort_artist", _sort_name(artist_now))

        if opts.unify_artwork and art_source:
            needs_art = (
                track.art_hash != next((t.art_hash for t in tracks if t.path == art_source), "")
                or track.art_count != 1
            )
            if needs_art:
                change.set_artwork = True
                change.artwork_source = art_source

        plan.changes.append(change)

    plan.tiles_after = len(_tiles_after(tracks, plan))
    return plan


def _tiles_after(tracks: list[Track], plan: AlbumPlan) -> set:
    """Wie viele Cover-Kacheln bleiben uebrig, wenn der Plan ausgefuehrt wird?"""
    changes = {c.path: c for c in plan.changes}
    tiles = set()
    for track in tracks:
        fields = changes[track.path].fields if track.path in changes else {}

        def after(name: str):
            return fields.get(name, (getattr(track, name), getattr(track, name)))[1]

        tiles.add(tile_key(after("albumartist"), after("artist"), after("album")))
    return tiles


def plan_library(tracks: Iterable[Track], opts: Optional[FixOptions] = None) -> list[AlbumPlan]:
    """Die komplette Sammlung analysieren, sortiert nach Dringlichkeit."""
    opts = opts or FixOptions()
    plans = [plan_album(group, opts, key=key) for key, group in group_albums(tracks).items()]
    plans.sort(key=lambda p: (-(p.tiles_before - p.tiles_after), -len(p.issues), p.album.casefold()))
    return plans
