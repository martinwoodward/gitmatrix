import json

from conftest import set_plugins

from gitmatrix import _audio
from gitmatrix._audio import Audio


def feed(bt, clock, audio, bins, frames=1, ms=20):
    for _ in range(frames):
        bt.push(bins)
        clock.advance(ms)
        audio.level()


def test_silent_when_not_streaming(bt, clock):
    a = Audio(bt)
    feed(bt, clock, a, [4000] * 240, 5)
    assert not a.active() and a.level() == 0 and a.streaming() is False
    assert a.name == "GitMatrix"
    assert list(a.waveform()) == [0] * _audio.WAVE


def test_music_drives_level_and_spectrum(bt, clock):
    a = Audio(bt)
    bt.streaming_now = True
    bass = [6000] * 12 + [200] * 228
    feed(bt, clock, a, bass, 30)
    assert a.active() and a.level() > 0.1
    bands = a.spectrum()
    assert len(bands) == _audio.BANDS and bands[0] > bands[-1]
    assert a.bass() > 0.3


def test_kicks_are_counted_and_tempo_found(bt, clock):
    a = Audio(bt)
    bt.streaming_now = True
    quiet = [100] * 240
    hit = [9000] * 12 + [100] * 228
    kicks = 0
    for beat in range(16):
        feed(bt, clock, a, hit, 1)
        feed(bt, clock, a, quiet, 24)  # 500 ms per beat = 120 bpm
        kicks += bool(a.kick())
        assert not a.kick()  # each hit is reported once
        if beat == 0:
            continue
    assert kicks >= 12
    assert 110 < a.bpm() < 130
    assert 0 <= a.phase() < 1


def test_volume_round_trip(bt):
    a = Audio(bt)
    a.set_volume(0.5)
    assert bt.vol == 64 and abs(a.volume() - 64 / 127) < 1e-6
    a.set_volume(7)
    assert bt.vol == 127


def test_driver_errors_are_swallowed(clock):
    class Broken:
        def streaming(self):
            raise OSError("gone")

    a = Audio(Broken())
    clock.advance(10)
    assert a.level() == 0 and not a.active()


def test_start_loads_link_keys_and_names_speaker(root, bt):
    (root / "bt_link_keys.bin").write_bytes(b"abc")
    assert _audio.start(bt)
    assert bt.name == "GitMatrix" and bt.loaded_keys == b"abc"


def test_link_keys_saved_when_dirty(root, bt):
    bt.keys = b"new"
    assert not _audio.save_link_keys(bt)
    bt.keys_dirty = True
    assert _audio.save_link_keys(bt)
    assert (root / "bt_link_keys.bin").read_bytes() == b"new"


def test_housekeeping_saves_new_pairing_but_defers_config(rig, bt):
    # pairing usually happens just before music starts: keys are saved straight away
    from gitmatrix import _boot

    set_plugins(rig.root, ["clock"])
    rt = rig.boot()
    bt.keys, bt.keys_dirty, bt.streaming_now = b"k", True, True
    rt.config.set("delay", 9)
    _boot.housekeeping(rt, rig.clock.advance(10))
    assert (rig.root / "bt_link_keys.bin").read_bytes() == b"k"
    assert rt.config.dirty
    bt.streaming_now = False
    _boot.housekeeping(rt, rig.clock.advance(10))
    assert not rt.config.dirty


def test_plugin_config(root):
    from plugin_config import plugin_config

    defaults = {"speed": 5, "scale": 1.5, "show": True, "colour": (0, 0, 0), "label": "hi"}
    assert plugin_config("demo", defaults) == defaults
    d = root / "lib" / "plugins" / "demo"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"speed": "9", "scale": "2", "colour": "#ff8000", "label": "yo"}))
    got = plugin_config("demo", defaults)
    assert got == {"speed": 9, "scale": 2.0, "show": False, "colour": (255, 128, 0), "label": "yo"}
    (d / "config.json").write_text("not json")
    assert plugin_config("demo", defaults) == defaults
