import sys

import pytest
import stubs

from gitmatrix import _persist


def test_default_store_prefers_mem_backup(monkeypatch):
    import array

    words = array.array("I", [0] * 2)
    monkeypatch.setattr(sys.modules["machine"], "mem_backup", lambda: words, raising=False)
    store = _persist._default_store()
    assert isinstance(store, _persist.BackupStore)
    old = _persist.set_store(store)
    try:
        _persist.mark(_persist.marker(1, "fire", "on_show"))
        _persist.bump_boot()
        assert words[0] >> 24 == _persist.MAGIC and words[1] == _persist.BOOT_MAGIC | 1
    finally:
        _persist.set_store(old)


def test_scratch_store_uses_only_scratch0_and_1():
    mem = stubs.Mem32()
    old = _persist.set_store(_persist.ScratchStore(mem))
    try:
        _persist.mark(_persist.marker(3, "clock", "update"))
        _persist.bump_boot()
        base = _persist.WATCHDOG_SCRATCH0
        assert set(mem) == {base, base + 4}
        assert _persist.read_mark() == (3, _persist.crc8("clock"), "update")
        assert _persist.boot_count() == 1
        _persist.clear_all()
        assert _persist.read_mark() is None and _persist.boot_count() == 0
    finally:
        _persist.set_store(old)


def test_garbage_in_scratch_is_not_a_marker(root):
    _persist.store.write(_persist.MARKER_REG, 0x12345678)
    assert _persist.read_mark() is None
    _persist.store.write(_persist.BOOT_REG, 0xDEADBEEF)
    assert _persist.boot_count() == 0


def test_reset_uses_watchdog_never_machine_reset(root):
    machine = sys.modules["machine"]
    machine.WDT.created.clear()
    _persist.bump_boot()
    _persist.mark(_persist.marker(0, "x", "update"))
    with pytest.raises(SystemExit):
        _persist.reset(250)
    assert machine.WDT.created[-1].timeout == 250
    assert _persist.read_mark() is None and _persist.boot_count() == 0


def test_crash_reboot_keeps_boot_count(root):
    _persist.bump_boot()
    with pytest.raises(SystemExit):
        _persist.reset(keep_boot=True)
    assert _persist.boot_count() == 1


def test_gitmatrix_reboot_saves_first(rig):
    import gitmatrix

    rt = rig.boot()
    st = gitmatrix.state("demo")
    st["x"] = 1
    rt.config.set("delay", 5)
    with pytest.raises(SystemExit):
        gitmatrix.reboot()
    assert (rig.root / "data" / "demo" / "state.json").exists()
    assert "delay = 5" in (rig.root / "config.py").read_text()
