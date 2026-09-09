"""
Metadata Engine for iPod Sync Tool
Handles MP3 scanning, album issue detection, and metadata fixing.

The core problem: iPod groups songs by (Artist + Album). When artist names
vary due to "feat." / "ft." additions, songs from the same album get split
into separate albums in Cover Flow. The fix: set the "Album Artist" (TPE2)
ID3 tag to the main artist so iPod groups all tracks together.
"""

import os
import re
from collections import defaultdict, Counter
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TPE1, TPE2, TALB, TIT2, TRCK, TCON, APIC


# Regex patterns that indicate featured artists in the Artist string
FEAT_PATTERNS = [
    # Parenthesized / bracketed forms first (more specific)
    r'\s*[\(\[]\s*feat\.?\s+.*?[\)\]]',
    r'\s*[\(\[]\s*ft\.?\s+.*?[\)\]]',
    r'\s*[\(\[]\s*featuring\s+.*?[\)\]]',
    r'\s*[\(\[]\s*with\s+.*?[\)\]]',
    # Inline forms (at end of string)
    r'\s+feat\.?\s+.*$',
    r'\s+ft\.?\s+.*$',
    r'\s+featuring\s+.*$',
]


def extract_main_artist(artist_string):
    """
    Remove featured artist info to get the primary/main artist.
    Example: "Katy Perry feat. Snoop Dogg" -> "Katy Perry"
    Example: "Eminem (feat. Rihanna)"      -> "Eminem"
    """
    if not artist_string:
        return ''

    result = artist_string.strip()
    for pattern in FEAT_PATTERNS:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE).strip()

    # Clean up trailing punctuation that might be left over
    result = result.rstrip(' -,;')
    return result


def format_duration(seconds):
    """Format duration in seconds as 'm:ss'."""
    if not seconds or seconds < 0:
        return '0:00'
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def scan_directory(directory_path):
    """
    Recursively scan a directory for MP3 files and read their metadata.
    Returns a list of track dicts with all relevant metadata fields.
    """
    tracks = []

    for root, dirs, files in os.walk(directory_path):
        for filename in sorted(files):
            if not filename.lower().endswith('.mp3'):
                continue

            filepath = os.path.join(root, filename)
            track = _read_track(filepath)
            if track is not None:
                tracks.append(track)

    return tracks


def _read_track(filepath):
    """Read metadata from a single MP3 file. Returns None on failure."""
    try:
        audio = MP3(filepath)
    except Exception:
        return None

    tags = audio.tags

    def get_text(tag_name):
        if tags is None:
            return ''
        tag = tags.get(tag_name)
        if tag and tag.text:
            return str(tag.text[0])
        return ''

    basename = os.path.splitext(os.path.basename(filepath))[0]

    return {
        'filepath': filepath,
        'filename': os.path.basename(filepath),
        'title': get_text('TIT2') or basename,
        'artist': get_text('TPE1'),
        'album': get_text('TALB'),
        'album_artist': get_text('TPE2'),
        'track_num': get_text('TRCK'),
        'genre': get_text('TCON'),
        'duration': audio.info.length if audio.info else 0,
    }


def group_by_album(tracks):
    """
    Group tracks by album name (case-insensitive).
    Returns dict: lowercase_key -> { 'name': display_name, 'tracks': [...] }
    """
    albums = {}
    for track in tracks:
        album_name = track['album'].strip() or '(Kein Album)'
        key = album_name.lower()

        if key not in albums:
            albums[key] = {'name': album_name, 'tracks': []}
        albums[key]['tracks'].append(track)

    return albums


def detect_album_issues(tracks):
    """
    Detect albums that are split due to inconsistent artist metadata.
    Returns a list of issue dicts with album info and suggested fix.
    """
    albums = group_by_album(tracks)
    issues = []

    for key, album_data in albums.items():
        album_tracks = album_data['tracks']
        album_name = album_data['name']

        # Skip placeholder album
        if album_name == '(Kein Album)':
            continue

        # Collect unique artist names
        artists = set(
            t['artist'].strip() for t in album_tracks if t['artist'].strip()
        )

        if len(artists) <= 1:
            # Only one artist variant -> no splitting issue
            continue

        # Check if Album Artist is already set consistently
        album_artists = set(
            t['album_artist'].strip() for t in album_tracks
            if t['album_artist'].strip()
        )

        if len(album_artists) == 1:
            aa = album_artists.pop()
            # If it's set and not "Various Artists", iPod will group correctly
            if aa.lower() not in ('various artists', 'various', 'va', 'v.a.'):
                continue

        # Check if artist differences are due to feat/ft/featuring
        main_artists = set(extract_main_artist(a) for a in artists if a)

        if not main_artists:
            continue

        if len(main_artists) == 1:
            # All main artists are the same -> clear feat/ft splitting issue
            main_artist = main_artists.pop()
            issues.append({
                'album': album_name,
                'main_artist': main_artist,
                'artist_variants': sorted(artists),
                'tracks': album_tracks,
                'type': 'feat_split',
            })
        else:
            # Multiple different main artists - use the most common one
            counts = Counter(
                extract_main_artist(t['artist'])
                for t in album_tracks if t['artist']
            )
            most_common_artist, most_common_count = counts.most_common(1)[0]

            # Only flag if at least 40% of tracks share the same main artist
            if most_common_count >= len(album_tracks) * 0.4:
                issues.append({
                    'album': album_name,
                    'main_artist': most_common_artist,
                    'artist_variants': sorted(artists),
                    'tracks': album_tracks,
                    'type': 'mixed_artists',
                })

    return issues


def fix_album_artist(tracks, album_artist):
    """
    Set the Album Artist (TPE2) ID3 tag for a list of tracks.
    This is the key fix: iPod uses Album Artist for grouping in Cover Flow.
    Returns (number_fixed, list_of_errors).
    """
    fixed = 0
    errors = []

    for track in tracks:
        try:
            try:
                audio = ID3(track['filepath'])
            except Exception:
                # File might not have ID3 tags yet - create them
                audio = ID3()
                audio.save(track['filepath'])
                audio = ID3(track['filepath'])

            audio.add(TPE2(encoding=3, text=album_artist))
            audio.save()
            track['album_artist'] = album_artist
            fixed += 1
        except Exception as e:
            errors.append(f"{track['filename']}: {e}")

    return fixed, errors


def update_track(filepath, title=None, artist=None, album=None,
                 album_artist=None, track_num=None, genre=None):
    """
    Update metadata fields for a single track.
    Only specified (non-None) fields are updated.
    Returns (success: bool, error_message: str or None).
    """
    try:
        try:
            audio = ID3(filepath)
        except Exception:
            audio = ID3()
            audio.save(filepath)
            audio = ID3(filepath)

        if title is not None:
            audio.add(TIT2(encoding=3, text=title))
        if artist is not None:
            audio.add(TPE1(encoding=3, text=artist))
        if album is not None:
            audio.add(TALB(encoding=3, text=album))
        if album_artist is not None:
            audio.add(TPE2(encoding=3, text=album_artist))
        if track_num is not None:
            audio.add(TRCK(encoding=3, text=track_num))
        if genre is not None:
            audio.add(TCON(encoding=3, text=genre))

        audio.save()
        return True, None
    except Exception as e:
        return False, str(e)


def get_artwork_bytes(filepath):
    """
    Extract embedded cover artwork from an MP3 file.
    Returns (image_bytes, mime_type) or (None, None) if no artwork.
    """
    try:
        tags = ID3(filepath)
        for key in tags:
            if key.startswith('APIC'):
                return tags[key].data, tags[key].mime
        return None, None
    except Exception:
        return None, None
