"""Der Sync-Motor gegen ein simuliertes Geraet."""

import posixpath

import pytest

from fake_device import FakeLink
from ipodfs.library import Library, apply_plans
from ipodfs.syncer import SyncTarget, build_plan, load_manifest, remote_path_for, run_plan, safe_component
from ipodfs.tags import Track


@pytest.fixture
def testlib(tmp_path):
    """Eine kleine Sammlung mit echten MP3-Dateien anlegen."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "tools"))
    import make_testlib

    root = tmp_path / "musik"
    make_testlib.main.__globals__["sys"].argv = ["make_testlib", str(root)]
    make_testlib.main()
    return root


@pytest.fixture
def device(tmp_path):
    return FakeLink(tmp_path / "ipod")


# ------------------------------------------------------------------- Pfade
def test_safe_component_entschaerft_sonderzeichen():
    assert safe_component("AC/DC: Back?") == "AC_DC_ Back_"
    assert safe_component("  ") == "Unbenannt"
    assert len(safe_component("x" * 200)) == 80


def test_zielpfad_folgt_dem_album_interpreten():
    track = Track(path="/a.mp3", ext=".mp3", title="California Gurls",
                  artist="Katy Perry feat. Snoop Dogg", albumartist="Katy Perry",
                  album="Teenage Dream", track=2)
    target = SyncTarget(base="/iPodFS", layout="albumartist_album")
    assert remote_path_for(track, target) == "/iPodFS/Katy Perry/Teenage Dream/02 California Gurls.mp3"


def test_layout_interpret_nutzt_den_gastnamen():
    track = Track(path="/a.mp3", ext=".mp3", title="California Gurls",
                  artist="Katy Perry feat. Snoop Dogg", albumartist="Katy Perry",
                  album="Teenage Dream", track=2)
    pfad = remote_path_for(track, SyncTarget(base="/M", layout="artist_album"))
    assert pfad.startswith("/M/Katy Perry feat. Snoop Dogg/")


def test_doppel_cd_bekommt_praefix():
    track = Track(path="/a.mp3", ext=".mp3", title="Hey You", artist="Pink Floyd",
                  albumartist="Pink Floyd", album="The Wall", track=1, disc=2, disc_total=2)
    assert remote_path_for(track, SyncTarget(base="/M")).endswith("/2-01 Hey You.mp3")


# -------------------------------------------------------------- Sync-Ablauf
def test_kompletter_sync_und_wiederholung(testlib, device):
    lib = Library()
    lib.rescan([str(testlib)])
    apply_plans(lib.plans, lib.options)
    lib.replan()

    target = SyncTarget(root="media", base="/iPodFS")

    plan = build_plan(device, lib.tracks, target)
    assert len(plan.uploads) == 15
    assert plan.upload_bytes > 0

    result = run_plan(device, plan)
    assert result["uploaded"] == 15
    assert result["errors"] == []

    # Alle Songs desselben Albums liegen in genau einem Ordner - das ist der Kern.
    dateien = [f for f in device.tree() if f.endswith(".mp3")]
    katy = sorted({posixpath.dirname(f) for f in dateien if "Katy Perry" in f})
    assert katy == ["/iPodFS/Katy Perry/Teenage Dream"], katy

    # Zweiter Lauf: nichts zu tun.
    plan2 = build_plan(device, lib.tracks, target)
    assert plan2.uploads == []
    assert len([a for a in plan2.actions if a.kind == "skip"]) == 15


def test_geaenderter_tag_loest_neuen_upload_aus(testlib, device):
    lib = Library()
    lib.rescan([str(testlib)])
    target = SyncTarget(base="/iPodFS")
    run_plan(device, build_plan(device, lib.tracks, target))

    before = len(device.uploads)
    apply_plans(lib.plans, lib.options)
    lib.replan()

    plan = build_plan(device, lib.tracks, target)
    assert plan.uploads, "nach dem Reparieren muessen die Dateien erneut hoch"
    run_plan(device, plan)
    assert len(device.uploads) > before


def test_verwaiste_dateien_werden_nur_auf_wunsch_geloescht(testlib, device):
    lib = Library()
    lib.rescan([str(testlib)])
    target = SyncTarget(base="/iPodFS")
    run_plan(device, build_plan(device, lib.tracks, target))

    lib.tracks = lib.tracks[:10]          # fuenf Songs aus der Sammlung entfernen
    lib.replan()

    ohne = build_plan(device, lib.tracks, target)
    assert ohne.deletions == []

    mit = build_plan(device, lib.tracks, target, delete_orphans=True)
    assert len(mit.deletions) == 5
    run_plan(device, mit)
    assert len([f for f in device.tree() if f.endswith(".mp3")]) == 10
    assert len(load_manifest(device, target)) == 10


def test_voller_ipod_bricht_sauber_ab(testlib, tmp_path):
    device = FakeLink(tmp_path / "ipod", free_bytes=20_000)
    lib = Library()
    lib.rescan([str(testlib)])
    target = SyncTarget(base="/iPodFS")
    result = run_plan(device, build_plan(device, lib.tracks, target))
    assert result["errors"], "der volle Speicher muss gemeldet werden"
    assert any("Speicherplatz" in e for e in result["errors"])


def test_namenskollision_wird_durchnummeriert(device):
    tracks = [
        Track(path=f"/x{i}.mp3", ext=".mp3", size=10, title="Intro",
              artist="X", albumartist="X", album="Y")
        for i in range(3)
    ]
    plan = build_plan(device, tracks, SyncTarget(base="/M"))
    ziele = sorted(a.dst for a in plan.uploads)
    assert len(set(ziele)) == 3
    assert any("(2)" in z for z in ziele)
    assert plan.warnings
