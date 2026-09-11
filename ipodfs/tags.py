"""
Einheitlicher Tag-Layer ueber mutagen.

Alle Formate werden auf ein gemeinsames Dict abgebildet, damit der Rest der
App nicht wissen muss, ob gerade ID3, MP4-Atome oder Vorbis-Comments im Spiel
sind. Geschrieben wird bei MP3 bewusst als ID3v2.3 - Apple-Geraete (und der
iPod touch 4 ganz besonders) kommen mit v2.4 gelegentlich durcheinander.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    ID3, ID3NoHeaderError, APIC, TALB, TCMP, TCON, TDRC, TIT2, TPE1, TPE2,
    TPOS, TRCK, TSO2, TSOA, TSOP, TYER,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".m4b", ".mp4", ".aac", ".alac",
    ".flac", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".wma",
}

#: Formate, die ein iPod touch mit der Standard-Musik-App nativ abspielt.
IOS_NATIVE_EXTENSIONS = {".mp3", ".m4a", ".aac", ".alac", ".wav", ".aiff", ".aif", ".m4b"}

# Felder, die normalize/library kennen
TEXT_FIELDS = (
    "title", "artist", "albumartist", "album", "genre", "year",
    "sort_artist", "sort_albumartist", "sort_album",
)
NUM_FIELDS = ("track", "track_total", "disc", "disc_total")


@dataclass
class Track:
    """Ein Song auf der Festplatte, formatunabhaengig."""

    path: str
    ext: str = ""
    size: int = 0
    mtime: float = 0.0
    duration: float = 0.0
    bitrate: int = 0

    title: str = ""
    artist: str = ""
    albumartist: str = ""
    album: str = ""
    genre: str = ""
    year: str = ""

    track: Optional[int] = None
    track_total: Optional[int] = None
    disc: Optional[int] = None
    disc_total: Optional[int] = None

    compilation: bool = False
    sort_artist: str = ""
    sort_albumartist: str = ""
    sort_album: str = ""

    art_hash: str = ""
    art_bytes: int = 0
    art_count: int = 0

    error: str = ""
    extra: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- helpers
    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def raw_album_artist(self) -> str:
        """Der Wert, den iOS zum Gruppieren heranzieht - unveraendert."""
        return self.albumartist if self.albumartist.strip() else self.artist

    @property
    def effective_album_artist(self) -> str:
        """Dasselbe, aber aufgeraeumt - fuer Anzeige und Ordnernamen."""
        return self.raw_album_artist.strip()

    @property
    def grouping_key(self) -> tuple:
        """
        Exakt der Schluessel, nach dem die iOS-Musik-App Cover-Flow-Kacheln
        bildet: (Album-Interpret, Album). Unterschiedliche Schluessel =
        unterschiedliche Kacheln, auch wenn das Cover identisch ist.

        Bewusst ohne strip(): "21" und "21 " sind fuer den iPod zwei Alben,
        und genau diese unsichtbaren Dubletten sollen sichtbar werden.
        """
        return tile_key(self.albumartist, self.artist, self.album)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["filename"] = self.filename
        d["effective_album_artist"] = self.effective_album_artist
        return d


def tile_key(album_artist: str, artist: str, album: str) -> tuple:
    """Der Gruppierungsschluessel der iOS-Musik-App aus rohen Tag-Werten."""
    grouping = album_artist if (album_artist or "").strip() else (artist or "")
    return (grouping.casefold(), (album or "").casefold())


def _raw(value: Any) -> str:
    """
    Tag-Wert unveraendert uebernehmen - nur echte Steuerzeichen fliegen raus.

    Bewusst *ohne* strip(): ein nachgestelltes Leerzeichen in "Teenage Dream "
    ist genau die Art von unsichtbarem Unterschied, an der Alben zerfallen.
    Sichtbar machen kann das nur, wer den Rohwert behaelt.
    """
    text = str(value)
    return "".join(ch for ch in text if ch == "\t" or ch >= " ")


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else None


def _split_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    """'3/12' -> (3, 12)."""
    if value is None:
        return None, None
    text = str(value)
    if "/" in text:
        a, _, b = text.partition("/")
        return _int_or_none(a), _int_or_none(b)
    return _int_or_none(text), None


def _art_fingerprint(data: Optional[bytes]) -> tuple[str, int]:
    if not data:
        return "", 0
    return hashlib.sha1(data).hexdigest()[:16], len(data)


# --------------------------------------------------------------------- lesen
def read_track(path: str) -> Track:
    """Metadaten einer Datei lesen. Wirft nicht - Fehler landen in .error."""
    ext = os.path.splitext(path)[1].lower()
    try:
        stat = os.stat(path)
        track = Track(path=path, ext=ext, size=stat.st_size, mtime=stat.st_mtime)
    except OSError as exc:
        return Track(path=path, ext=ext, error=str(exc))

    try:
        audio = mutagen.File(path)
    except Exception as exc:  # kaputte Datei - trotzdem anzeigen
        track.error = f"nicht lesbar: {exc}"
        track.title = os.path.splitext(track.filename)[0]
        return track

    if audio is None:
        track.error = "kein unterstuetztes Audioformat"
        track.title = os.path.splitext(track.filename)[0]
        return track

    if audio.info is not None:
        track.duration = float(getattr(audio.info, "length", 0.0) or 0.0)
        track.bitrate = int(getattr(audio.info, "bitrate", 0) or 0)

    if isinstance(audio, MP3) or isinstance(getattr(audio, "tags", None), ID3):
        _read_id3(audio, track)
    elif isinstance(audio, MP4):
        _read_mp4(audio, track)
    else:
        _read_vorbis(audio, track)

    if not track.title:
        track.title = os.path.splitext(track.filename)[0]
    return track


def _read_id3(audio, track: Track) -> None:
    tags = audio.tags
    if tags is None:
        return

    def text(frame_id: str) -> str:
        frame = tags.get(frame_id)
        if frame is None or not getattr(frame, "text", None):
            return ""
        return _raw(frame.text[0])

    track.title = text("TIT2")
    track.artist = text("TPE1")
    track.albumartist = text("TPE2")
    track.album = text("TALB")
    track.genre = text("TCON")
    track.year = text("TDRC") or text("TYER")
    track.sort_artist = text("TSOP")
    track.sort_albumartist = text("TSO2")
    track.sort_album = text("TSOA")
    track.track, track.track_total = _split_pair(text("TRCK"))
    track.disc, track.disc_total = _split_pair(text("TPOS"))

    cmp_frame = tags.get("TCMP")
    if cmp_frame is not None and getattr(cmp_frame, "text", None):
        track.compilation = str(cmp_frame.text[0]).strip() in ("1", "true", "True")

    pics = [tags[k] for k in tags.keys() if k.startswith("APIC")]
    track.art_count = len(pics)
    if pics:
        front = next((p for p in pics if getattr(p, "type", 3) == 3), pics[0])
        track.art_hash, track.art_bytes = _art_fingerprint(front.data)


def _read_mp4(audio: MP4, track: Track) -> None:
    tags = audio.tags or {}

    def text(key: str) -> str:
        value = tags.get(key)
        if not value:
            return ""
        return _raw(value[0])

    track.title = text("\xa9nam")
    track.artist = text("\xa9ART")
    track.albumartist = text("aART")
    track.album = text("\xa9alb")
    track.genre = text("\xa9gen")
    track.year = text("\xa9day")
    track.sort_artist = text("soar")
    track.sort_albumartist = text("soaa")
    track.sort_album = text("soal")
    track.compilation = bool(tags.get("cpil", [False])[0])

    trkn = tags.get("trkn")
    if trkn:
        track.track, track.track_total = (trkn[0] + (0, 0))[:2]
        track.track = track.track or None
        track.track_total = track.track_total or None
    disk = tags.get("disk")
    if disk:
        track.disc, track.disc_total = (disk[0] + (0, 0))[:2]
        track.disc = track.disc or None
        track.disc_total = track.disc_total or None

    covers = tags.get("covr") or []
    track.art_count = len(covers)
    if covers:
        track.art_hash, track.art_bytes = _art_fingerprint(bytes(covers[0]))


def _read_vorbis(audio, track: Track) -> None:
    tags = audio.tags
    if tags is None:
        return

    def text(*keys: str) -> str:
        for key in keys:
            try:
                value = tags.get(key)
            except Exception:
                value = None
            if value:
                return _raw(value[0])
        return ""

    track.title = text("title")
    track.artist = text("artist")
    track.albumartist = text("albumartist", "album artist")
    track.album = text("album")
    track.genre = text("genre")
    track.year = text("date", "year")
    track.sort_artist = text("artistsort")
    track.sort_albumartist = text("albumartistsort")
    track.sort_album = text("albumsort")
    track.compilation = text("compilation") in ("1", "true", "yes")
    track.track, track.track_total = _split_pair(text("tracknumber"))
    if track.track_total is None:
        track.track_total = _int_or_none(text("tracktotal", "totaltracks"))
    track.disc, track.disc_total = _split_pair(text("discnumber"))
    if track.disc_total is None:
        track.disc_total = _int_or_none(text("disctotal", "totaldiscs"))

    pictures = list(getattr(audio, "pictures", []) or [])
    track.art_count = len(pictures)
    if pictures:
        track.art_hash, track.art_bytes = _art_fingerprint(pictures[0].data)


def read_artwork(path: str) -> tuple[Optional[bytes], str]:
    """Eingebettetes Cover einer Datei holen -> (bytes, mime)."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return None, ""
    if audio is None:
        return None, ""

    tags = getattr(audio, "tags", None)
    if isinstance(tags, ID3):
        pics = [tags[k] for k in tags.keys() if k.startswith("APIC")]
        if pics:
            front = next((p for p in pics if getattr(p, "type", 3) == 3), pics[0])
            return front.data, front.mime or "image/jpeg"
    elif isinstance(audio, MP4):
        covers = (audio.tags or {}).get("covr") or []
        if covers:
            fmt = covers[0].imageformat
            mime = "image/png" if fmt == MP4Cover.FORMAT_PNG else "image/jpeg"
            return bytes(covers[0]), mime
    else:
        pictures = list(getattr(audio, "pictures", []) or [])
        if pictures:
            return pictures[0].data, pictures[0].mime or "image/jpeg"
    return None, ""


# -------------------------------------------------------------------- schreiben
def write_tags(path: str, changes: dict[str, Any], artwork: Optional[tuple[bytes, str]] = None) -> None:
    """
    Geaenderte Felder zurueckschreiben.

    ``changes`` enthaelt nur die Felder, die wirklich anders sind (Keys wie in
    :class:`Track`). ``artwork`` ersetzt - falls gesetzt - saemtliche
    eingebetteten Bilder durch genau ein Frontcover.
    """
    ext = os.path.splitext(path)[1].lower()
    if not changes and artwork is None:
        return

    if ext == ".mp3":
        _write_id3(path, changes, artwork)
    elif ext in (".m4a", ".m4b", ".mp4", ".aac", ".alac"):
        _write_mp4(path, changes, artwork)
    elif ext in (".flac",):
        _write_flac(path, changes, artwork)
    elif ext in (".ogg", ".oga", ".opus"):
        _write_vorbis(path, changes)
    else:
        raise ValueError(f"Schreiben fuer {ext} nicht unterstuetzt")


def _pair(number: Optional[int], total: Optional[int]) -> str:
    if not number:
        return ""
    return f"{number}/{total}" if total else str(number)


def _merge_pair(changes: dict, current: Any, num_key: str, total_key: str) -> str:
    """
    "3/12" neu zusammensetzen, ohne die Haelfte zu verlieren, die gleich bleibt.

    Aendert sich nur ``track_total``, darf ``track`` nicht verschwinden - sonst
    ist die Titelnummer weg und die Songs stehen auf dem iPod in Zufallsfolge.
    """
    cur_number, cur_total = _split_pair(current)
    number = changes.get(num_key, cur_number) if num_key in changes else cur_number
    total = changes.get(total_key, cur_total) if total_key in changes else cur_total
    return _pair(number, total)


def _write_id3(path: str, changes: dict, artwork) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    simple = {
        "title": TIT2, "artist": TPE1, "albumartist": TPE2, "album": TALB,
        "genre": TCON, "sort_artist": TSOP, "sort_albumartist": TSO2,
        "sort_album": TSOA,
    }
    for key, frame_cls in simple.items():
        if key in changes:
            value = (changes[key] or "").strip()
            if value:
                tags.setall(frame_cls.__name__, [frame_cls(encoding=3, text=value)])
            else:
                tags.delall(frame_cls.__name__)

    if "year" in changes:
        value = (changes["year"] or "").strip()
        tags.delall("TYER")
        if value:
            tags.setall("TDRC", [TDRC(encoding=3, text=value)])
        else:
            tags.delall("TDRC")

    def _current(frame_id: str) -> str:
        frame = tags.get(frame_id)
        return str(frame.text[0]) if frame is not None and getattr(frame, "text", None) else ""

    if "track" in changes or "track_total" in changes:
        value = _merge_pair(changes, _current("TRCK"), "track", "track_total")
        tags.setall("TRCK", [TRCK(encoding=3, text=value)]) if value else tags.delall("TRCK")
    if "disc" in changes or "disc_total" in changes:
        value = _merge_pair(changes, _current("TPOS"), "disc", "disc_total")
        tags.setall("TPOS", [TPOS(encoding=3, text=value)]) if value else tags.delall("TPOS")

    if "compilation" in changes:
        tags.setall("TCMP", [TCMP(encoding=3, text="1" if changes["compilation"] else "0")])

    if artwork is not None:
        data, mime = artwork
        tags.delall("APIC")
        if data:
            tags.add(APIC(encoding=3, mime=mime or "image/jpeg", type=3, desc="Cover", data=data))

    # v2.3 + ID3v1 mitschreiben: das verdaut auch die alte iOS-6-Musik-App.
    tags.save(path, v2_version=3, v1=2)


def _write_mp4(path: str, changes: dict, artwork) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    simple = {
        "title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART",
        "album": "\xa9alb", "genre": "\xa9gen", "year": "\xa9day",
        "sort_artist": "soar", "sort_albumartist": "soaa", "sort_album": "soal",
    }
    for key, atom in simple.items():
        if key in changes:
            value = (changes[key] or "").strip()
            if value:
                tags[atom] = [value]
            else:
                tags.pop(atom, None)

    def _pair_atom(atom: str, num_key: str, total_key: str) -> None:
        current = tags.get(atom)
        cur_number, cur_total = (current[0] + (0, 0))[:2] if current else (0, 0)
        number = changes.get(num_key, cur_number) if num_key in changes else cur_number
        total = changes.get(total_key, cur_total) if total_key in changes else cur_total
        tags[atom] = [(number or 0, total or 0)]

    if "track" in changes or "track_total" in changes:
        _pair_atom("trkn", "track", "track_total")
    if "disc" in changes or "disc_total" in changes:
        _pair_atom("disk", "disc", "disc_total")
    if "compilation" in changes:
        tags["cpil"] = bool(changes["compilation"])

    if artwork is not None:
        data, mime = artwork
        if data:
            fmt = MP4Cover.FORMAT_PNG if "png" in (mime or "") else MP4Cover.FORMAT_JPEG
            tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        else:
            tags.pop("covr", None)

    audio.save()


def _vorbis_map(changes: dict) -> dict[str, str]:
    simple = {
        "title": "title", "artist": "artist", "albumartist": "albumartist",
        "album": "album", "genre": "genre", "year": "date",
        "sort_artist": "artistsort", "sort_albumartist": "albumartistsort",
        "sort_album": "albumsort",
    }
    out: dict[str, str] = {}
    for key, name in simple.items():
        if key in changes:
            out[name] = (changes[key] or "").strip()
    if "track" in changes:
        out["tracknumber"] = str(changes["track"] or "")
    if "track_total" in changes:
        out["tracktotal"] = str(changes["track_total"] or "")
    if "disc" in changes:
        out["discnumber"] = str(changes["disc"] or "")
    if "disc_total" in changes:
        out["disctotal"] = str(changes["disc_total"] or "")
    if "compilation" in changes:
        out["compilation"] = "1" if changes["compilation"] else "0"
    return out


def _write_flac(path: str, changes: dict, artwork) -> None:
    audio = FLAC(path)
    for name, value in _vorbis_map(changes).items():
        if value:
            audio[name] = value
        else:
            audio.pop(name, None)
    if artwork is not None:
        audio.clear_pictures()
        data, mime = artwork
        if data:
            pic = Picture()
            pic.type = 3
            pic.mime = mime or "image/jpeg"
            pic.data = data
            audio.add_picture(pic)
    audio.save()


def _write_vorbis(path: str, changes: dict) -> None:
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("Datei nicht lesbar")
    if audio.tags is None:
        audio.add_tags()
    for name, value in _vorbis_map(changes).items():
        if value:
            audio[name] = value
        else:
            audio.pop(name, None)
    audio.save()
