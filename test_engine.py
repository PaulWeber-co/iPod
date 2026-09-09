"""Quick test to verify the metadata engine works correctly."""
import metadata_engine as engine

tracks = engine.scan_directory('test_music')
print(f"Songs gefunden: {len(tracks)}")

issues = engine.detect_album_issues(tracks)
print(f"Probleme erkannt: {len(issues)}")

for issue in issues:
    print(f"\n  Album: {issue['album']}")
    print(f"  Typ: {issue['type']}")
    print(f"  Hauptkuenstler: {issue['main_artist']}")
    print(f"  Varianten: {issue['artist_variants']}")
    print(f"  Betroffene Songs: {len(issue['tracks'])}")

# Also check which albums are OK
album_groups = engine.group_by_album(tracks)
issue_keys = {i['album'].strip().lower() for i in issues}
ok_albums = [
    name for key, data in album_groups.items() 
    if key not in issue_keys
    for name in [data['name']]
]
print(f"\nAlben ohne Probleme: {ok_albums}")
