"""
Erzeugt eine kleine Test-Sammlung mit genau den Problemen, die iPodFS loest.

    python tools/make_testlib.py [zielordner]

Die MP3s sind winzige Stille-Dateien - es geht nur um die Tags.
"""

import os
import sys
import struct
import zlib

from mutagen.id3 import ID3, APIC, TALB, TCMP, TIT2, TPE1, TPE2, TRCK

# Ein gueltiger, stiller MPEG1-Layer3-Frame (128 kbit/s, 44,1 kHz, stereo).
SILENT_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413
SILENT_MP3 = SILENT_FRAME * 40


def _png(r: int, g: int, b: int, size: int = 8) -> bytes:
    """Winziges einfarbiges PNG - dient als Cover."""
    raw = b"".join(b"\x00" + bytes([r, g, b]) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


COVER_A = _png(220, 60, 90)
COVER_B = _png(60, 120, 220)


def make(path, title, artist, album, track, album_artist=None, cover=None, compilation=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(SILENT_MP3)

    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TALB(encoding=3, text=album))
    tags.add(TRCK(encoding=3, text=str(track)))
    if album_artist:
        tags.add(TPE2(encoding=3, text=album_artist))
    if compilation is not None:
        tags.add(TCMP(encoding=3, text="1" if compilation else "0"))
    if cover:
        tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=cover))
    tags.save(path, v2_version=3)
    print("  ", os.path.relpath(path))


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "test_music"

    print("Album 1 - klassischer feat.-Zerfall:")
    base = os.path.join(root, "Katy Perry", "Teenage Dream")
    make(f"{base}/01 Teenage Dream.mp3", "Teenage Dream", "Katy Perry", "Teenage Dream", 1, cover=COVER_A)
    make(f"{base}/02 California Gurls.mp3", "California Gurls", "Katy Perry feat. Snoop Dogg", "Teenage Dream", 2, cover=COVER_A)
    make(f"{base}/03 E.T..mp3", "E.T.", "Katy Perry (feat. Kanye West)", "Teenage Dream", 3, cover=COVER_B)
    make(f"{base}/04 Firework.mp3", "Firework", "Katy Perry", "Teenage Dream ", 4)

    print("Album 2 - Gastinterpret ohne feat.-Marker + Deluxe-Variante:")
    base = os.path.join(root, "Eminem", "Recovery")
    make(f"{base}/01 Not Afraid.mp3", "Not Afraid", "Eminem", "Recovery", 1, album_artist="Eminem", cover=COVER_A)
    make(f"{base}/02 Love The Way You Lie.mp3", "Love The Way You Lie", "Eminem & Rihanna", "Recovery (Deluxe Edition)", 2, album_artist="Eminem & Rihanna", cover=COVER_A)
    make(f"{base}/03 No Love.mp3", "No Love", "Eminem, Lil Wayne", "Recovery", 3, cover=COVER_A)

    print("Album 3 - falsches Kompilations-Flag und unsichtbare Zeichen:")
    base = os.path.join(root, "Daft Punk", "Discovery")
    make(f"{base}/01 One More Time.mp3", "One More Time", "Daft Punk", "Discovery", 1, album_artist="Daft Punk", cover=COVER_A, compilation=True)
    make(f"{base}/02 Aerodynamic.mp3", "Aerodynamic", "Daft Punk", "Discovery", 2, album_artist="Daft Punk", cover=COVER_A, compilation=False)

    print("Album 4 - echter Sampler, soll Sampler bleiben:")
    base = os.path.join(root, "Various", "Bravo Hits 70")
    make(f"{base}/01 Song A.mp3", "Song A", "Artist One", "Bravo Hits 70", 1, album_artist="Various Artists", cover=COVER_B, compilation=True)
    make(f"{base}/02 Song B.mp3", "Song B", "Artist Two", "Bravo Hits 70", 2, album_artist="Various Artists", cover=COVER_B, compilation=True)
    make(f"{base}/03 Song C.mp3", "Song C", "Artist Three", "Bravo Hits 70", 3, album_artist="Various Artists", cover=COVER_B, compilation=True)
    make(f"{base}/04 Song D.mp3", "Song D", "Artist Four", "Bravo Hits 70", 4, album_artist="Various Artists", cover=COVER_B, compilation=True)

    print("Album 5 - Doppel-CD, Disc-Nummer steckt im Albumnamen:")
    base = os.path.join(root, "Pink Floyd", "The Wall")
    make(f"{base}/1-01 In The Flesh.mp3", "In The Flesh?", "Pink Floyd", "The Wall (Disc 1)", 1, album_artist="Pink Floyd", cover=COVER_A)
    make(f"{base}/2-01 Hey You.mp3", "Hey You", "Pink Floyd", "The Wall (Disc 2)", 1, album_artist="Pink Floyd", cover=COVER_A)

    print("\nFertig:", os.path.abspath(root))


if __name__ == "__main__":
    main()
