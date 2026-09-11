"""Tags schreiben und wieder einlesen - der Rundlauf muss verlustfrei sein."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_testlib  # noqa: E402

from ipodfs.library import Library, apply_plans  # noqa: E402
from ipodfs.syncer import SyncTarget, remote_path_for  # noqa: E402
from ipodfs.tags import read_artwork, read_track, tile_key, write_tags  # noqa: E402


@pytest.fixture
def mp3(tmp_path):
    path = tmp_path / "song.mp3"
    make_testlib.make(str(path), "California Gurls", "Katy Perry feat. Snoop Dogg",
                      "Teenage Dream", "2/4", cover=make_testlib.COVER_A)
    return str(path)


def test_rundlauf_aller_felder(mp3):
    write_tags(mp3, {
        "title": "Neuer Titel", "artist": "A", "albumartist": "B", "album": "C",
        "genre": "Pop", "year": "2010", "track": 3, "track_total": 12,
        "disc": 2, "disc_total": 2, "compilation": True,
        "sort_artist": "A,", "sort_albumartist": "B,", "sort_album": "C,",
    })
    t = read_track(mp3)
    assert (t.title, t.artist, t.albumartist, t.album) == ("Neuer Titel", "A", "B", "C")
    assert (t.track, t.track_total, t.disc, t.disc_total) == (3, 12, 2, 2)
    assert t.compilation is True
    assert (t.genre, t.year) == ("Pop", "2010")
    assert t.sort_albumartist == "B,"


def test_nur_gesamtzahl_aendern_laesst_tracknummer_stehen(mp3):
    """Der Klassiker: 'track_total' setzen darf 'track' nicht loeschen."""
    assert read_track(mp3).track == 2
    write_tags(mp3, {"track_total": 9})
    t = read_track(mp3)
    assert t.track == 2, "die Titelnummer darf nicht verschwinden"
    assert t.track_total == 9


def test_nur_disc_gesamtzahl_aendern(mp3):
    write_tags(mp3, {"disc": 2})
    write_tags(mp3, {"disc_total": 3})
    t = read_track(mp3)
    assert (t.disc, t.disc_total) == (2, 3)


def test_cover_wird_ersetzt_und_bleibt_einzeln(mp3):
    write_tags(mp3, {}, artwork=(make_testlib.COVER_B, "image/png"))
    daten, mime = read_artwork(mp3)
    assert daten == make_testlib.COVER_B
    assert mime == "image/png"
    assert read_track(mp3).art_count == 1


def test_leerer_wert_entfernt_das_feld(mp3):
    write_tags(mp3, {"genre": ""})
    assert read_track(mp3).genre == ""


def test_tile_key_unterscheidet_unsichtbares():
    assert tile_key("", "Adele", "21") != tile_key("", "Adele", "21 ")
    assert tile_key("Adele", "Adele feat. X", "21") == tile_key("Adele", "Adele", "21")


def test_tracknummern_ueberleben_die_reparatur(tmp_path):
    """Ende zu Ende: nach dem Aufraeumen muessen die Dateinamen nummeriert sein."""
    root = tmp_path / "musik"
    make_testlib.main.__globals__["sys"].argv = ["make_testlib", str(root)]
    make_testlib.main()

    lib = Library()
    lib.rescan([str(root)])
    apply_plans(lib.plans, lib.options)
    lib.replan()

    target = SyncTarget(base="/iPodFS")
    pfade = {remote_path_for(t, target) for t in lib.tracks}

    assert "/iPodFS/Katy Perry/Teenage Dream/02 California Gurls.mp3" in pfade
    assert "/iPodFS/Pink Floyd/The Wall/2-01 Hey You.mp3" in pfade
    assert all(t.track for t in lib.tracks), "keine Titelnummer darf verloren gehen"
