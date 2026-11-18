# Fault containment: a broken plugin only ever takes itself out.

import builtins

import pytest
from conftest import make_plugin, set_plugins

from gitmatrix import _hang, _persist

GOOD = """
frames = 0
def update():
    global frames
    frames += 1
    screen.pen = color.rgb(0, 255, 0)
    screen.clear()
"""


def test_import_error_is_quarantined_and_others_run(rig):
    make_plugin(rig.root, "good", GOOD)
    make_plugin(rig.root, "broken", "import does_not_exist\n")
    set_plugins(rig.root, ["broken", "good"])
    rt = rig.boot()
    assert "broken" in rt.config.quarantined
    assert "import failed" in rt.config.quarantined["broken"]
    assert rt.host.current.name == "good"
    rig.frames(10)
    assert rt.loader.plugins["good"].module.frames == 10


def test_syntax_error_is_quarantined(rig):
    make_plugin(rig.root, "good", GOOD)
    make_plugin(rig.root, "typo", "def update(:\n")
    set_plugins(rig.root, ["typo", "good"])
    rt = rig.boot()
    assert "typo" in rt.config.quarantined


def test_update_failures_quarantine_after_five_in_a_row(rig):
    make_plugin(rig.root, "flaky", "n = 0\ndef update():\n    global n\n    n += 1\n    raise ValueError('boom')\n")
    make_plugin(rig.root, "good", GOOD)
    set_plugins(rig.root, ["flaky", "good"])
    rt = rig.boot()
    rig.frames(4)
    assert "flaky" not in rt.config.quarantined
    rig.frames(1)
    assert "ValueError: boom" in rt.config.quarantined["flaky"]
    assert rt.host.current.name == "good"
    assert "flaky stopped" in rt.host.message[0]
    rig.frames(200)  # past the message
    assert rt.loader.plugins["good"].module.frames > 0
    # persisted, so it stays out after a reboot
    rt.config.save()
    rt2 = rig.boot()
    assert "flaky" in rt2.config.quarantined
    assert [p.name for p in rt2.loader.active()] == ["good"]


def test_occasional_failures_are_tolerated(rig):
    make_plugin(rig.root, "wobbly", "n = 0\ndef update():\n    global n\n    n += 1\n    if n % 3 == 0:\n        raise ValueError\n")
    set_plugins(rig.root, ["wobbly"])
    rt = rig.boot()
    rig.frames(30)
    p = rt.loader.plugins["wobbly"]
    assert p.module is not None and p.error_count == 10


def test_memory_error_and_sys_exit_are_contained(rig):
    make_plugin(rig.root, "hog", "def update():\n    raise MemoryError\n")
    make_plugin(rig.root, "quitter", "import sys\ndef on_show():\n    sys.exit()\n")
    set_plugins(rig.root, ["hog", "quitter"])
    rt = rig.boot()
    rig.frames(5)
    assert "hog" in rt.config.quarantined
    assert rt.loader.plugins["quitter"].error_count >= 1


def test_display_update_inside_a_plugin_is_ignored(rig):
    make_plugin(rig.root, "eager", "def update():\n    display.update()\n    display.update()\n")
    set_plugins(rig.root, ["eager"])
    rt = rig.boot()
    real = rt.compositor.driver
    before = real.updates
    rig.frames(10)
    assert real.updates - before == 10  # one present per frame, plugin calls swallowed


def test_hung_plugin_is_quarantined_after_watchdog_reset(rig):
    import sys

    make_plugin(rig.root, "good", GOOD)
    make_plugin(rig.root, "hang", "def update():\n    pass\n")
    set_plugins(rig.root, ["good", "hang"])
    rt = rig.boot()
    # simulate the watchdog firing while "hang" was in update()
    _persist.mark(rt.loader.plugins["hang"].markers["update"])
    rt.config.save()
    sys.modules["machine"].cause = sys.modules["machine"].WDT_RESET
    try:
        rt2 = rig.boot()
    finally:
        sys.modules["machine"].cause = sys.modules["machine"].PWRON_RESET
    assert "watchdog" in rt2.config.quarantined["hang"]
    assert "update" in rt2.config.quarantined["hang"]
    assert rt2.host.message is not None


@pytest.fixture
def hang_watch(rig):
    """The hang watch as the device runs it, minus the timer: spin() advances
    the clock and ticks the watch, whose interrupt raises KeyboardInterrupt."""
    def interrupt():
        raise KeyboardInterrupt

    _hang.watch.interrupt = interrupt
    _hang.watch.tripped = False

    def spin():
        _hang.watch.check(rig.clock.advance(_hang.CHECK_MS))

    builtins.spin = spin
    try:
        yield _hang.watch
    finally:
        del builtins.spin
        _hang.stop()
        _hang.watch.interrupt = None


HANGS = "def update():\n    while True:\n        spin()\n"


def test_hung_update_is_stopped_and_quarantined_without_a_reset(rig, hang_watch):
    make_plugin(rig.root, "good", GOOD)
    make_plugin(rig.root, "hang", HANGS)
    set_plugins(rig.root, ["hang", "good"])
    rt = rig.boot()
    assert rt.host.current.name == "hang"
    rig.frames(1)                          # returns: no reset, no REPL
    assert "hung in update" in rt.config.quarantined["hang"]
    assert rt.host.message is not None and "hung" in rt.host.message[0]
    assert hang_watch.tripped is False and hang_watch.started is None
    rig.frames(400)                        # past the message and transition
    assert rt.host.current.name == "good"
    assert rt.loader.plugins["good"].module.frames >= 1


def test_plugin_that_swallows_the_interrupt_is_interrupted_again(rig, hang_watch):
    code = """def update():
    n = 0
    while True:
        try:
            while True:
                spin()
        except KeyboardInterrupt:
            n += 1
"""
    make_plugin(rig.root, "stubborn", code)
    set_plugins(rig.root, ["stubborn"])
    rig.boot()
    calls = []
    real = hang_watch.interrupt

    def bounded():
        calls.append(1)
        if len(calls) > 3:
            raise SystemExit("hardware watchdog")    # what the device falls back to
        real()

    hang_watch.interrupt = bounded
    rig.frames(1)
    assert len(calls) > 1


def test_hung_import_is_quarantined(rig, hang_watch):
    make_plugin(rig.root, "good", GOOD)
    make_plugin(rig.root, "slow", "while True:\n    spin()\n")
    set_plugins(rig.root, ["slow", "good"])
    rt = rig.boot()
    assert "hung in import" in rt.config.quarantined["slow"]
    assert rt.host.current.name == "good"


def test_ctrl_c_in_a_plugin_still_reaches_the_repl(rig, hang_watch):
    make_plugin(rig.root, "c", "def update():\n    raise KeyboardInterrupt\n")
    set_plugins(rig.root, ["c"])
    rig.boot()
    with pytest.raises(KeyboardInterrupt):
        rig.frames(1)
    assert rig.rt.config.quarantined == {}


def test_hang_watch_is_quiet_for_quick_hooks(rig, hang_watch):
    make_plugin(rig.root, "good", GOOD)
    set_plugins(rig.root, ["good"])
    rt = rig.boot()
    rig.frames(3)
    hang_watch.check(rig.clock.advance(_hang.HANG_MS * 2))   # between hooks
    assert hang_watch.tripped is False
    assert rt.config.quarantined == {}


def test_marker_ignored_without_watchdog_reset(rig):
    make_plugin(rig.root, "hang", "def update():\n    pass\n")
    set_plugins(rig.root, ["hang"])
    rt = rig.boot()
    _persist.mark(rt.loader.plugins["hang"].markers["update"])
    rt2 = rig.boot()
    assert rt2.config.quarantined == {}
    assert _persist.read_mark() is None


def test_marker_found_by_checksum_when_order_changed(rig):
    make_plugin(rig.root, "a", GOOD)
    make_plugin(rig.root, "b", GOOD)
    set_plugins(rig.root, ["a", "b"])
    rt = rig.boot()
    marker = rt.loader.plugins["b"].markers["on_show"]
    rt.config.set("plugins", ["b", "a"])
    assert rt.loader.check_crash(True) is None  # mark already cleared by boot
    _persist.mark(marker)
    assert rt.loader.check_crash(True) == "b"


def test_sync_config_adds_new_plugins_disabled_and_drops_vanished(rig):
    make_plugin(rig.root, "good", GOOD)
    set_plugins(rig.root, ["good", "gone"], quarantined={"gone": "x"})
    rt = rig.boot()
    assert "gone" not in rt.config.plugins
    assert rt.config.quarantined == {}
    assert "graph" in rt.config.plugins and "graph" in rt.config.disabled_plugins
    assert [p.name for p in rt.loader.active()] == ["good"]


def test_unquarantine_via_menu_show(rig):
    make_plugin(rig.root, "good", GOOD)
    make_plugin(rig.root, "fixed", GOOD)
    set_plugins(rig.root, ["good", "fixed"], quarantined={"fixed": "old crash"})
    rt = rig.boot()
    assert [p.name for p in rt.loader.active()] == ["good"]
    rt.host.show("fixed")
    assert rt.config.quarantined == {}
    assert rt.host.current.name == "fixed"


def test_runtime_current_and_screen_are_restored(rig):
    import builtins

    from gitmatrix import _runtime

    make_plugin(rig.root, "nosy", "import gitmatrix\nseen = []\ndef update():\n    seen.append((gitmatrix.plugin_name(), screen))\n")
    set_plugins(rig.root, ["nosy"])
    rt = rig.boot()
    rig.frames(2)
    p = rt.loader.plugins["nosy"]
    assert p.module.seen[0] == ("nosy", p.surface)
    assert _runtime.current is None
    assert builtins.screen is rt.compositor.fb
