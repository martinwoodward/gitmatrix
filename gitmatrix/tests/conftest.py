import builtins
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "modules", "python"))

import stubs  # noqa: E402

FAKE_BT = stubs.install()

import gitmatrix  # noqa: E402
from gitmatrix import _flash, _persist, _runtime, _sounds, _state  # noqa: E402
import phew.server  # noqa: E402, F401 - grabs the event loop before any asyncio.run()

gitmatrix.install_globals()

FIRMWARE = os.path.join(ROOT, "firmware")


def _forget_plugin_modules():
    for key in list(sys.modules):
        if key == "plugins" or key.startswith("plugins."):
            del sys.modules[key]


@pytest.fixture
def root(tmp_path):
    """A fresh device filesystem: defaults + built-in plugins, ROOT pointed at it."""
    shutil.copytree(os.path.join(FIRMWARE, "defaults"), tmp_path / "defaults")
    shutil.copytree(os.path.join(FIRMWARE, "lib"), tmp_path / "lib")
    shutil.copytree(os.path.join(FIRMWARE, "html"), tmp_path / "html")
    shutil.copytree(os.path.join(FIRMWARE, "sounds"), tmp_path / "sounds")
    _sounds._deferred.clear()  # noqa: SLF001
    old_root = _runtime.ROOT
    _runtime.ROOT = str(tmp_path)
    sys.path.insert(0, str(tmp_path / "lib"))
    _forget_plugin_modules()
    _state._states.clear()  # noqa: SLF001
    _state._settings.clear()  # noqa: SLF001
    store = _persist.MemoryStore()
    old_store = _persist.set_store(store)
    screen, display = builtins.screen, builtins.display
    builtins.screen = stubs.image()
    builtins.display = stubs.Display()
    FAKE_BT.__init__()
    _flash.set_bt(FAKE_BT)
    _flash._down, _flash._depth, _flash.on_resume = None, 0, None  # noqa: SLF001
    try:
        yield tmp_path
    finally:
        sys.path.remove(str(tmp_path / "lib"))
        _forget_plugin_modules()
        _persist.set_store(old_store)
        _runtime.ROOT = old_root
        _runtime.host = _runtime.loader = _runtime.config = _runtime.current = None
        _runtime.wifi = _runtime.reboot_at = _runtime.notifier = _runtime.events = None
        _runtime.wifi_restart_at = None
        _runtime.mqtt = None
        gitmatrix.notification = None
        _runtime.firmware = {}
        builtins.screen, builtins.display = screen, display


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """Never bind a real captive DNS socket; record the calls instead."""
    from gitmatrix import _wifi

    calls = []
    monkeypatch.setattr(_wifi, "_start_dns", calls.append)
    return calls


@pytest.fixture
def bt():
    return FAKE_BT


@pytest.fixture
def clock(monkeypatch):
    """Controllable ticks_ms for the runtime."""
    from gitmatrix import _compat

    class Clock:
        now = 1000

        def advance(self, ms):
            self.now += ms
            return self.now

    c = Clock()
    monkeypatch.setattr(_compat, "ticks_ms", lambda: c.now)
    return c


def make_plugin(root, name, code, manifest=None):
    """Write a plugin into the fake filesystem."""
    import json

    d = root / "lib" / "plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text(code)
    (d / "package.json").write_text(json.dumps(manifest or {"name": name.title()}))
    return d


def set_plugins(root, names, **extra):
    """Rewrite config.py so only `names` are installed and enabled."""
    lines = [f"plugins = {names!r}", "disabled_plugins = []", "quarantined = {}", "delay = 30",
             "transition = 'slide'", "brightness = 60", "volume = 64", "watchdog = True"]
    lines += [f"{k} = {v!r}" for k, v in extra.items()]
    (root / "config.py").write_text("\n".join(lines) + "\n")


@pytest.fixture
def rig(root, clock):
    """boot() a runtime over the fake filesystem; call rig.boot() after adding plugins."""
    from gitmatrix import _boot
    from gitmatrix._input import Input

    class Rig:
        rt = None
        input = Input()
        net = None              # set to stubs.FakeNetwork() before boot() for WiFi

        def boot(self):
            self.rt = _boot.boot(self.input, network_=self.net)
            return self.rt

        def frames(self, n=1, ms=16):
            for _ in range(n):
                self.rt.host.frame(clock.advance(ms))

        def turn(self, d):
            self.input.push("turn", d)
            self.frames()

        def press(self):
            self.input.push("press")
            self.input.push("release")
            self.frames()

    r = Rig()
    r.root = root
    r.clock = clock
    return r
