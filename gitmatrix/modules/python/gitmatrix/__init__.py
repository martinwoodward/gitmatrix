# GitMatrix plugin API.
#
# Importing this sets up the globals plugins expect (Art Matrix style): the
# picovector names, `screen`, `display`, `font` (+ `rom_font`), `audio`/`mic`,
# `clamp`/`rnd`/`frnd`, `file_exists`/`is_dir`, `UID` and `gitmatrix` itself.

import builtins
import random
import sys

try:
    # the widget's frozen module: puts picovector, screen, display, font in builtins
    import contribution_widget  # noqa: F401
except ImportError:
    pass

from gitmatrix import _audio, _compat, _runtime, _state

VERSION = "0.1.0"
WIDTH = 53
HEIGHT = 7

# The JSON `data` of the notification that forced this plugin on screen (else None).
notification = None

# gitmatrix.input: encoder turns for plugins with "input": true. Set at boot.
input = None  # noqa: A001

audio = _audio.Audio()
mic = audio


def state(name=None):
    """Dict-like persistent state for a plugin (defaults to the calling plugin)."""
    return _state.state(name)


def settings(name=None):
    """Typed settings from the plugin's package.json `settings` schema."""
    return _state.settings(name)


def plugin_name():
    return _runtime.current_name()


def create_task(coro, name=None):
    """asyncio.create_task(), but owned by the plugin: errors count against it and
    the task is cancelled if the plugin is quarantined or unloaded."""
    import asyncio

    loader = _runtime.loader
    plugin = _runtime.current
    if plugin is None and name is not None and loader is not None:
        plugin = loader.plugins.get(name)
    if plugin is None or loader is None:
        return asyncio.create_task(coro)

    async def guarded():
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - contained like any other hook
            loader.record_failure(plugin, "task", e)

    task = asyncio.create_task(guarded())
    loader.track(plugin, task)
    return task


def show_message(text, ms=3000):
    if _runtime.host is not None:
        _runtime.host.show_message(text, ms)


def reboot():
    """Save everything, then soft-reset. Warm resets (machine.reset(), the
    watchdog) can hang this board until a power cycle; never use them."""
    from gitmatrix import _flash, _persist

    # stop the Bluetooth sink if it's playing: a flash write then can hang the board
    with _flash.pause(resume=False):
        _state.flush_all(force=True)
        if _runtime.config is not None:
            _runtime.config.save()
    _persist.reset()


# ---- Art Matrix helper globals ------------------------------------------------

def clamp(v, vmin, vmax):
    return max(vmin, min(v, vmax))


def rnd(v1, v2=None):
    if v2:
        return random.randint(v1, v2)
    return random.randint(0, v1)


def frnd(v1, v2=None):
    if v2:
        return random.uniform(v1, v2)
    return random.uniform(0, v1)


def free(message=""):
    try:
        import gc
        gc.collect()
        print(f"{message} free: {gc.mem_free()}")
    except (ImportError, AttributeError):
        pass


def _uid():
    try:
        import binascii

        import machine
        return binascii.hexlify(machine.unique_id()).decode("ascii")
    except (ImportError, AttributeError):
        return "000000000000"


def install_globals():
    b = builtins
    if hasattr(b, "font") and not hasattr(b, "rom_font"):
        b.rom_font = b.font
    b.audio = audio
    b.mic = audio
    b.clamp = clamp
    b.rnd = rnd
    b.frnd = frnd
    b.free = free
    b.file_exists = _compat.exists
    b.is_dir = _compat.is_dir
    if not hasattr(b, "UID"):
        b.UID = _uid()
    b.gitmatrix = sys.modules[__name__]
    if "artmatrix" not in sys.modules:
        sys.modules["artmatrix"] = sys.modules[__name__]


install_globals()
