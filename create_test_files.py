"""
Create test MP3 files with metadata to demonstrate album splitting issues.
These are minimal valid MP3 files (silence) with ID3 tags.
"""

import os
import struct
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TRCK, TCON

# Minimal valid MP3 frame (silence, 1 frame)
# MPEG1 Layer3, 128kbps, 44100Hz, stereo
SYNC_WORD = b'\xff\xfb'
HEADER_REST = b'\x90\x00'
PADDING = b'\x00' * 413  # Pad to frame size

SILENT_MP3_FRAME = SYNC_WORD + HEADER_REST + PADDING
# Repeat a few frames to make it a valid file
SILENT_MP3 = SILENT_MP3_FRAME * 50


def create_test_mp3(filepath, title, artist, album, track_num=None, genre=None):
    """Create a minimal MP3 file with the given metadata."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # Write raw MP3 data
    with open(filepath, 'wb') as f:
        f.write(SILENT_MP3)

    # Add ID3 tags
    from mutagen.id3 import ID3
    try:
        tags = ID3(filepath)
    except:
        tags = ID3()

    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TALB(encoding=3, text=album))
    if track_num:
        tags.add(TRCK(encoding=3, text=track_num))
    if genre:
        tags.add(TCON(encoding=3, text=genre))

    tags.save(filepath)
    print(f"  Created: {os.path.basename(filepath)}")


def main():
    test_dir = os.path.join(os.path.dirname(__file__), "test_music")

    print("Creating test MP3 files with album splitting issues...\n")

    # ── Album 1: Katy Perry - Teenage Dream (HAS ISSUES) ──
    print("Album: Teenage Dream (with feat. splitting issue)")
    create_test_mp3(
        os.path.join(test_dir, "01 Teenage Dream.mp3"),
        "Teenage Dream", "Katy Perry", "Teenage Dream", "1/5", "Pop"
    )
    create_test_mp3(
        os.path.join(test_dir, "02 California Gurls.mp3"),
        "California Gurls", "Katy Perry feat. Snoop Dogg", "Teenage Dream", "2/5", "Pop"
    )
    create_test_mp3(
        os.path.join(test_dir, "03 Firework.mp3"),
        "Firework", "Katy Perry", "Teenage Dream", "3/5", "Pop"
    )
    create_test_mp3(
        os.path.join(test_dir, "04 E.T.mp3"),
        "E.T.", "Katy Perry ft. Kanye West", "Teenage Dream", "4/5", "Pop"
    )
    create_test_mp3(
        os.path.join(test_dir, "05 Last Friday Night.mp3"),
        "Last Friday Night", "Katy Perry", "Teenage Dream", "5/5", "Pop"
    )

    # ── Album 2: Eminem - Recovery (HAS ISSUES) ──
    print("\nAlbum: Recovery (with feat. splitting issue)")
    create_test_mp3(
        os.path.join(test_dir, "01 Not Afraid.mp3"),
        "Not Afraid", "Eminem", "Recovery", "1/4", "Hip-Hop"
    )
    create_test_mp3(
        os.path.join(test_dir, "02 Love The Way You Lie.mp3"),
        "Love The Way You Lie", "Eminem feat. Rihanna", "Recovery", "2/4", "Hip-Hop"
    )
    create_test_mp3(
        os.path.join(test_dir, "03 No Love.mp3"),
        "No Love", "Eminem feat. Lil Wayne", "Recovery", "3/4", "Hip-Hop"
    )
    create_test_mp3(
        os.path.join(test_dir, "04 Space Bound.mp3"),
        "Space Bound", "Eminem", "Recovery", "4/4", "Hip-Hop"
    )

    # ── Album 3: Adele - 21 (NO ISSUES - control group) ──
    print("\nAlbum: 21 (no issues - all same artist)")
    create_test_mp3(
        os.path.join(test_dir, "01 Rolling in the Deep.mp3"),
        "Rolling in the Deep", "Adele", "21", "1/3", "Pop"
    )
    create_test_mp3(
        os.path.join(test_dir, "02 Rumour Has It.mp3"),
        "Rumour Has It", "Adele", "21", "2/3", "Pop"
    )
    create_test_mp3(
        os.path.join(test_dir, "03 Turning Tables.mp3"),
        "Turning Tables", "Adele", "21", "3/3", "Pop"
    )

    # ── Album 4: Ed Sheeran (HAS ISSUES) ──
    print("\nAlbum: ÷ Divide (with featuring issue)")
    create_test_mp3(
        os.path.join(test_dir, "01 Shape of You.mp3"),
        "Shape of You", "Ed Sheeran", "÷ (Divide)", "1/3", "Pop"
    )
    create_test_mp3(
        os.path.join(test_dir, "02 Perfect Duet.mp3"),
        "Perfect Duet", "Ed Sheeran featuring Beyoncé", "÷ (Divide)", "2/3", "Pop"
    )
    create_test_mp3(
        os.path.join(test_dir, "03 Galway Girl.mp3"),
        "Galway Girl", "Ed Sheeran", "÷ (Divide)", "3/3", "Pop"
    )

    print(f"\n✅ Done! Created test files in: {test_dir}")
    print(f"   Open the iPod Sync Tool and scan this folder to test.")


if __name__ == "__main__":
    main()
