"""Die Regeln, an denen die Albumreparatur haengt."""

import pytest

from ipodfs.normalize import (
    FixOptions, clean_text, fold, plan_album, plan_library,
    split_featuring, strip_album_decorations,
)
from ipodfs.tags import Track


def track(**kwargs) -> Track:
    base = dict(path=f"/x/{kwargs.get('title', 'song')}.mp3", ext=".mp3", size=1000, mtime=1.0)
    base.update(kwargs)
    return Track(**base)


# --------------------------------------------------------------- feat.-Regeln
@pytest.mark.parametrize("value,main,guests", [
    ("Katy Perry feat. Snoop Dogg", "Katy Perry", ["Snoop Dogg"]),
    ("Katy Perry ft Snoop Dogg", "Katy Perry", ["Snoop Dogg"]),
    ("Katy Perry (feat. Kanye West)", "Katy Perry", ["Kanye West"]),
    ("Eminem [ft. Rihanna]", "Eminem", ["Rihanna"]),
    ("Jay-Z featuring Alicia Keys & Nas", "Jay-Z", ["Alicia Keys", "Nas"]),
    ("Simon & Garfunkel", "Simon & Garfunkel", []),
    ("Earth, Wind & Fire", "Earth, Wind & Fire", []),
    ("AC/DC", "AC/DC", []),
])
def test_split_featuring(value, main, guests):
    assert split_featuring(value) == (main, guests)


def test_clean_text_entfernt_unsichtbares():
    assert clean_text("Daft\xa0Punk") == "Daft Punk"
    assert clean_text("Teenage  Dream ") == "Teenage Dream"
    assert clean_text("Don’t Stop") == "Don't Stop"


def test_fold_ignoriert_akzente_und_zeichen():
    assert fold("Beyoncé!") == fold("beyonce")


@pytest.mark.parametrize("value,clean,disc", [
    ("Teenage Dream (Deluxe Edition)", "Teenage Dream", None),
    ("The Wall (Disc 2)", "The Wall", 2),
    ("Abbey Road [Remastered 2009]", "Abbey Road", None),
    ("Thriller - Special Edition", "Thriller", None),
    ("Greatest Hits", "Greatest Hits", None),
])
def test_strip_album_decorations(value, clean, disc):
    assert strip_album_decorations(value) == (clean, disc)


# ------------------------------------------------------------- Albumdiagnose
def test_feat_zerfall_wird_zusammengefuehrt():
    tracks = [
        track(title="Teenage Dream", artist="Katy Perry", album="Teenage Dream", track=1),
        track(title="California Gurls", artist="Katy Perry feat. Snoop Dogg", album="Teenage Dream", track=2),
        track(title="E.T.", artist="Katy Perry (feat. Kanye West)", album="Teenage Dream", track=3),
    ]
    plan = plan_album(tracks, FixOptions())
    assert plan.tiles_before == 3
    assert plan.tiles_after == 1
    assert plan.album_artist == "Katy Perry"
    assert "feat_split" in plan.issues
    assert not plan.is_compilation


def test_gastinterpret_ohne_marker():
    tracks = [
        track(title="A", artist="Eminem", album="Recovery", track=1),
        track(title="B", artist="Eminem & Rihanna", album="Recovery", track=2),
        track(title="C", artist="Eminem, Lil Wayne", album="Recovery", track=3),
    ]
    plan = plan_album(tracks, FixOptions())
    assert plan.album_artist == "Eminem"
    assert plan.tiles_after == 1


def test_echter_sampler_bleibt_sampler():
    tracks = [track(title=f"S{i}", artist=f"Artist {i}", album="Bravo Hits 70",
                    albumartist="Various Artists", track=i) for i in range(1, 6)]
    plan = plan_album(tracks, FixOptions())
    assert plan.album_artist == "Various Artists"
    assert plan.is_compilation
    assert plan.tiles_after == 1


def test_band_mit_ampersand_wird_nicht_zerlegt():
    tracks = [
        track(title="A", artist="Simon & Garfunkel", album="Bookends", track=1),
        track(title="B", artist="Simon & Garfunkel", album="Bookends", track=2),
    ]
    plan = plan_album(tracks, FixOptions())
    assert plan.album_artist == "Simon & Garfunkel"


def test_nachgestelltes_leerzeichen_spaltet_und_wird_repariert():
    tracks = [
        track(title="A", artist="Adele", album="21", albumartist="Adele", track=1),
        track(title="B", artist="Adele", album="21 ", albumartist="Adele", track=2),
    ]
    plan = plan_album(tracks, FixOptions())
    assert plan.tiles_before == 2, "iOS sieht '21 ' und '21' als zwei Alben"
    assert plan.tiles_after == 1
    assert plan.album_variants == ["21", "21 "]


def test_deluxe_variante_wird_zusammengelegt():
    tracks = [
        track(title="A", artist="Adele", album="21", albumartist="Adele", track=1),
        track(title="B", artist="Adele", album="21 (Deluxe Edition)", albumartist="Adele", track=2),
    ]
    assert plan_album(tracks, FixOptions()).tiles_after == 1
    ohne = plan_album(tracks, FixOptions(unify_album_names=False))
    assert ohne.tiles_after == 2, "abgeschaltet muss die Variante erhalten bleiben"


def test_disc_nummer_wandert_aus_dem_albumnamen():
    tracks = [
        track(title="A", artist="Pink Floyd", album="The Wall (Disc 1)", albumartist="Pink Floyd", track=1),
        track(title="B", artist="Pink Floyd", album="The Wall (Disc 2)", albumartist="Pink Floyd", track=1),
    ]
    plan = plan_album(tracks, FixOptions())
    assert plan.album == "The Wall"
    discs = {c.fields["disc"][1] for c in plan.changes if "disc" in c.fields}
    assert discs == {1, 2}


def test_cover_wird_vereinheitlicht():
    tracks = [
        track(title="A", artist="X", album="Y", albumartist="X", art_hash="aaa", art_bytes=900, art_count=1),
        track(title="B", artist="X", album="Y", albumartist="X", art_hash="bbb", art_bytes=100, art_count=1),
        track(title="C", artist="X", album="Y", albumartist="X"),
    ]
    plan = plan_album(tracks, FixOptions())
    assert "artwork_mismatch" in plan.issues
    quellen = {c.artwork_source for c in plan.changes if c.set_artwork}
    assert quellen == {tracks[0].path}, "das groesste Bild gewinnt"


def test_sauberes_album_bleibt_unangetastet():
    tracks = [
        track(title="A", artist="Daft Punk", album="Discovery", albumartist="Daft Punk",
              track=1, track_total=2, sort_artist="Daft Punk", sort_albumartist="Daft Punk",
              sort_album="Discovery", art_hash="z", art_bytes=10, art_count=1),
        track(title="B", artist="Daft Punk", album="Discovery", albumartist="Daft Punk",
              track=2, track_total=2, sort_artist="Daft Punk", sort_albumartist="Daft Punk",
              sort_album="Discovery", art_hash="z", art_bytes=10, art_count=1),
    ]
    plan = plan_album(tracks, FixOptions())
    assert not plan.needs_fix
    assert plan.issues == []


def test_kompilations_flag_wird_geglaettet():
    tracks = [
        track(title="A", artist="Daft Punk", album="Discovery", albumartist="Daft Punk", compilation=True),
        track(title="B", artist="Daft Punk", album="Discovery", albumartist="Daft Punk", compilation=False),
    ]
    plan = plan_album(tracks, FixOptions())
    assert "compilation_flag" in plan.issues
    assert all(c.fields.get("compilation", (None, False))[1] is False
               for c in plan.changes if "compilation" in c.fields)


def test_sortiertags_stellen_artikel_um():
    tracks = [track(title="A", artist="The Beatles", album="The White Album",
                    albumartist="The Beatles", track=1)]
    plan = plan_album(tracks, FixOptions())
    felder = plan.changes[0].fields
    assert felder["sort_albumartist"][1] == "Beatles, The"
    assert felder["sort_album"][1] == "White Album, The"


def test_feat_in_den_titel_verschieben():
    tracks = [
        track(title="California Gurls", artist="Katy Perry feat. Snoop Dogg",
              album="Teenage Dream", track=2),
    ]
    plan = plan_album(tracks, FixOptions(move_feat_to_title=True))
    felder = plan.changes[0].fields
    assert felder["artist"][1] == "Katy Perry"
    assert felder["title"][1] == "California Gurls (feat. Snoop Dogg)"


def test_plan_library_sortiert_nach_ersparnis():
    tracks = [
        track(title="A", artist="X", album="Sauber", albumartist="X", track=1),
        track(title="B", artist="Y", album="Kaputt", track=1),
        track(title="C", artist="Y feat. Z", album="Kaputt", track=2),
    ]
    plans = plan_library(tracks)
    assert plans[0].album == "Kaputt", "das Album mit dem groessten Gewinn zuerst"
