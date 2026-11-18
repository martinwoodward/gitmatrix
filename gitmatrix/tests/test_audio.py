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


def test_housekeeping_defers_pairing_and_config_while_streaming(rig, bt):
    # phase 0: a flash write during A2DP can hang the board, so even a new
    # pairing waits until playback stops
    from gitmatrix import _boot

    set_plugins(rig.root, ["clock"])
    rt = rig.boot()
    bt.keys, bt.keys_dirty, bt.streaming_now = b"k", True, True
    rt.config.set("delay", 9)
    _boot.housekeeping(rt, rig.clock.advance(10))
    assert not (rig.root / "bt_link_keys.bin").exists()
    assert rt.config.dirty
    bt.streaming_now = False
    _boot.housekeeping(rt, rig.clock.advance(10))
    assert (rig.root / "bt_link_keys.bin").read_bytes() == b"k"
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


def test_bands_cover_60hz_to_16khz_weighted_to_midrange():
    hz = _audio.BAND_HZ
    assert len(hz) == _audio.BANDS + 1
    assert round(hz[0]) == 60 and hz[-1] == 16000
    assert all(lo < hi for lo, hi in zip(hz, hz[1:]))
    mid = [i for i in range(_audio.BANDS) if hz[i] >= 250 and hz[i + 1] <= 4000]
    assert len(mid) == 12 and hz[1] == 250
    for rate in (44100, 48000):
        edges = list(_audio._band_edges(rate))
        assert all(hi - lo >= 1 for lo, hi in zip(edges, edges[1:]))
        assert edges[0] * rate / 1024 < 70
        assert abs(edges[-1] * rate / 1024 - 16000) < rate / 1024


def tone(freq, rate=44100):
    bins = [50] * 512
    bins[int(freq * 1024 / rate + 0.5)] = 9000
    return bins


def test_tones_land_in_their_band(bt, clock):
    for rate in (44100, 48000):
        for freq, band in ((60, 0), (1100, 7), (15000, 15)):
            bt.rate = rate
            a = Audio(bt)
            bt.streaming_now = True
            feed(bt, clock, a, tone(freq, rate), 10)
            spec = list(a.spectrum())
            assert spec.index(max(spec)) == band, (rate, freq, spec)
            lo, hi = a.band_range(band)
            assert lo <= freq < hi and a.sample_rate() == rate


def test_sample_rate_is_read_at_stream_start_then_every_5s(bt, clock, monkeypatch):
    calls = []
    real = bt.sample_rate
    monkeypatch.setattr(bt, "sample_rate", lambda: calls.append(1) or real())
    a = Audio(bt)
    bt.streaming_now = True
    feed(bt, clock, a, tone(1100), 251)         # just over 5 s of FFT frames
    assert len(calls) == 2
    bt.rate = 48000
    feed(bt, clock, a, tone(1100, 48000), 251)
    assert a.sample_rate() == 48000 and len(calls) == 3
    bt.streaming_now = False
    feed(bt, clock, a, tone(1100), 1)
    bt.streaming_now = True
    feed(bt, clock, a, tone(1100), 1)
    assert len(calls) == 4


def test_treble_bursts_clap_and_bass_kicks(bt, clock):
    a = Audio(bt)
    bt.streaming_now = True
    quiet = [100] * 512
    feed(bt, clock, a, quiet, 40)
    a.kick(), a.clap()
    feed(bt, clock, a, [100] * 70 + [9000] * 300, 1)  # broadband crack above 3 kHz
    assert a.clap() and not a.kick()
    feed(bt, clock, a, quiet, 20)
    feed(bt, clock, a, tone(100), 1)
    assert a.kick() and not a.clap()
