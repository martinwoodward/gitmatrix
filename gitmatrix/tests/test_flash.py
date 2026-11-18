# The single flash writer: background writes defer while Bluetooth audio
# streams, user writes pause the sink (deinit -> write -> init) and come back.
import pytest
from conftest import set_plugins

import gitmatrix
from gitmatrix import _audio, _compat, _flash


def test_writes_are_refused_while_streaming(root, bt):
    bt.streaming_now = True
    with pytest.raises(_flash.FlashBusy):
        _compat.write_atomic(str(root / "x.txt"), "x")
    with pytest.raises(_flash.FlashBusy):
        _compat.makedirs(str(root / "new" / "dir"))
    assert not (root / "x.txt").exists() and not (root / "new").exists()
    bt.streaming_now = False
    _compat.write_atomic(str(root / "x.txt"), "x")
    assert (root / "x.txt").read_text() == "x"


def test_pause_stops_the_sink_for_the_burst_and_restores_it(root, bt):
    calls = []
    _flash.on_resume = lambda: calls.append("pairable")
    bt.streaming_now, bt.keys, bt.keys_dirty, bt.vol = True, b"keys", True, 99
    with _flash.pause() as p:
        assert p.stopped and bt.deinits == 1 and _flash.sink_down()
        _compat.write_atomic(str(root / "x.txt"), "x")
        with _flash.pause():            # nested: no second deinit/init
            _compat.write_atomic(str(root / "y.txt"), "y")
        assert bt.inits == 0
    assert bt.inits == 1 and bt.name == "GitMatrix" and not _flash.sink_down()
    assert bt.loaded_keys == b"keys" and bt.vol == 99 and calls == ["pairable"]
    # the pairing was saved while the sink was down
    assert (root / "bt_link_keys.bin").read_bytes() == b"keys"
    assert (root / "x.txt").exists() and (root / "y.txt").exists()


def test_pause_is_free_when_not_streaming(root, bt):
    with _flash.pause() as p:
        _compat.write_atomic(str(root / "x.txt"), "x")
    assert not p.stopped and bt.deinits == 0 and bt.inits == 0


def test_pause_without_resume_leaves_the_sink_stopped(root, bt):
    bt.streaming_now = True
    with _flash.pause(resume=False):
        pass
    assert bt.deinits == 1 and bt.inits == 0 and _flash.sink_down()


def test_reboot_pauses_audio_to_flush(rig, bt):
    set_plugins(rig.root, ["clock"])
    rt = rig.boot()
    s = gitmatrix.state("clock")
    s["n"] = 1
    rt.config.set("delay", 7)
    bt.streaming_now = True
    with pytest.raises(SystemExit):
        gitmatrix.reboot()
    assert bt.deinits == 1
    assert (rig.root / "data" / "clock" / "state.json").exists()
    assert "delay = 7" in (rig.root / "config.py").read_text()


def test_forget_devices_while_streaming_comes_back_unpaired(root, bt):
    (root / "bt_link_keys.bin").write_bytes(b"old")
    bt.streaming_now, bt.keys = True, b"old"
    assert _audio.forget_devices()
    assert not (root / "bt_link_keys.bin").exists()
    assert bt.deinits == 1 and bt.inits == 1 and bt.loaded_keys == b""


def test_store_index_cache_waits_for_playback(rig, bt):
    from gitmatrix import _boot

    set_plugins(rig.root, ["clock"])
    rt = rig.boot()
    rt.store.pending_cache = {"repo": "a/b@main", "index": {"plugins": {}}}
    bt.streaming_now = True
    _boot.housekeeping(rt, rig.clock.advance(10))
    assert not (rig.root / "index-cache.json").exists()
    bt.streaming_now = False
    _boot.housekeeping(rt, rig.clock.advance(10))
    assert (rig.root / "index-cache.json").exists() and rt.store.pending_cache is None
