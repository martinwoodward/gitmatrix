import pytest

from conftest import make_plugin, set_plugins

from gitmatrix import _boot, _persist


def test_boot_starts_bt_speaker_as_gitmatrix(rig, bt):
    rig.boot()
    assert bt.name == "GitMatrix"


def test_boot_loads_saved_link_keys_before_init(rig, bt):
    (rig.root / "bt_link_keys.bin").write_bytes(b"keys")
    rig.boot()
    assert bt.loaded_keys == b"keys"


def test_button_held_at_boot_is_safe_mode(rig):
    make_plugin(rig.root, "p", "def update():\n    pass\n")
    set_plugins(rig.root, ["p"])
    rig.input._down = True
    rt = rig.boot()
    assert rt.host.safe_mode == "button held"
    assert rt.loader.plugins == {}
    rig.input._down = False
    rig.frames(3)


def test_boot_loop_goes_to_safe_mode_and_clears_when_healthy(rig):
    for _ in range(_boot.BOOT_LOOP_LIMIT - 1):
        _persist.bump_boot()
    rt = rig.boot()
    assert rt.host.safe_mode
    _persist.clear_boot()
    rt = rig.boot()
    assert not rt.host.safe_mode
    _boot.housekeeping(rt, rig.clock.advance(_boot.HEALTHY_MS + 1))
    assert _persist.boot_count() == 0


def test_safe_mode_menu_can_start_a_plugin(rig):
    make_plugin(rig.root, "p", "def update():\n    pass\n")
    set_plugins(rig.root, ["p"])
    rig.input._down = True
    rt = rig.boot()
    rig.input._down = False
    rt.host.show("p")
    assert rt.host.safe_mode is None
    assert rt.host.current.name == "p"


def test_watchdog_started_and_fed(rig):
    import sys

    machine = sys.modules["machine"]
    machine.WDT.created.clear()
    rt = rig.boot()
    rig.frames(5)
    rt.watchdog.feed()
    assert machine.WDT.created and machine.WDT.created[-1].timeout == _boot.WATCHDOG_MS


def test_housekeeping_saves_link_keys_and_state(rig, bt):
    import gitmatrix

    rt = rig.boot()
    bt.keys, bt.keys_dirty = b"new", True
    gitmatrix.state("x")["a"] = 1
    rig.clock.advance(31000)
    _boot.housekeeping(rt, rig.clock.now)
    assert (rig.root / "bt_link_keys.bin").read_bytes() == b"new"
    assert (rig.root / "data" / "x" / "state.json").exists()


def test_shutdown_restores_real_display(rig):
    import builtins

    rt = rig.boot()
    real = rt.compositor.driver
    _boot.shutdown(rt)
    assert builtins.display is real


def test_failing_boot_soft_resets_then_stops_instead_of_looping(root, monkeypatch):
    def broken_boot():
        _persist.bump_boot()
        raise AttributeError("no screen")

    monkeypatch.setattr(_boot, "boot", broken_boot)
    for _ in range(_boot.BOOT_LOOP_LIMIT - 1):
        with pytest.raises(SystemExit, match="soft reboot"):
            _boot.run()
    _boot.run()  # returns to the REPL
    assert _persist.boot_count() == _boot.BOOT_LOOP_LIMIT
