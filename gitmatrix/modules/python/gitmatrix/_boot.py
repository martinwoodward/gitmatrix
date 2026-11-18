# Boot sequence and the main loop tasks. /main.py just calls run().

import builtins

import gitmatrix
from gitmatrix import _audio, _compat, _persist, _runtime, _state
from gitmatrix._config import Config
from gitmatrix._display import Compositor, DisplayProxy
from gitmatrix._host import Host
from gitmatrix._input import Input, PluginInput
from gitmatrix._loader import PluginLoader

try:
    import machine
except ImportError:
    machine = None

WATCHDOG_MS = 8000
HEALTHY_MS = 60000
BOOT_LOOP_LIMIT = 3
HOUSEKEEPING_MS = 2000


class _Watchdog:
    def __init__(self, enabled):
        self.wdt = None
        if enabled and machine is not None and hasattr(machine, "WDT"):
            self.wdt = machine.WDT(timeout=WATCHDOG_MS)

    def feed(self):
        if self.wdt is not None:
            self.wdt.feed()


def watchdog_reset():
    if machine is None:
        return False
    try:
        return machine.reset_cause() == machine.WDT_RESET
    except AttributeError:
        return False


class Runtime:
    """Everything boot() wires together, kept for main.py, tests and the REPL."""

    def __init__(self):
        self.config = None
        self.compositor = None
        self.input = None
        self.loader = None
        self.host = None
        self.watchdog = None
        self.booted = 0
        self.healthy = False


def boot(input_=None, bt=None):
    rt = Runtime()
    rt.booted = _compat.ticks_ms()
    boots = _persist.bump_boot()
    was_watchdog = watchdog_reset()

    rt.config = config = Config().load()
    _runtime.config = config
    if config.repaired:
        print(f"boot: config repaired ({config.repaired})")

    real_screen = builtins.screen
    real_display = builtins.display
    if isinstance(real_display, DisplayProxy):
        real_display = real_display._real  # noqa: SLF001 - re-running boot from the REPL
    rt.compositor = Compositor(real_screen, real_display)
    builtins.display = DisplayProxy(real_display)

    rt.input = input_ if input_ is not None else Input.from_hardware()
    gitmatrix.input = PluginInput(rt.input)

    safe = None
    if rt.input.held():
        safe = "button held"
    elif boots >= BOOT_LOOP_LIMIT:
        safe = f"{boots} failed boots"

    # The speaker is always on, whatever is (or isn't) running
    if bt is not None:
        gitmatrix.audio._bt = bt  # noqa: SLF001
    _audio.start(gitmatrix.audio._bt)  # noqa: SLF001
    _runtime.audio = gitmatrix.audio

    rt.watchdog = _Watchdog(config.get("watchdog", True))
    rt.loader = loader = PluginLoader(config, rt.compositor, rt.watchdog.feed)
    loader.sync_config()
    crashed = loader.check_crash(was_watchdog)

    rt.host = host = Host(config, loader, rt.compositor, rt.input, gitmatrix.audio, safe)
    if not safe:
        loader.load_all()
    host.start()
    if crashed:
        host.show_message(f"{crashed} hung and was stopped", 5000, 25)
    elif safe:
        print(f"boot: safe mode, {safe}")
    config.save()
    rt.watchdog.feed()
    return rt


def housekeeping(rt, now):
    """Flushes and timers. Called every couple of seconds."""
    streaming = gitmatrix.audio.streaming()
    _audio.save_link_keys()
    _state.flush_all(streaming=streaming)
    if rt.config.dirty and not streaming:
        rt.config.save()
    if not rt.healthy and _compat.ticks_diff(now, rt.booted) > HEALTHY_MS:
        rt.healthy = True
        _persist.clear_boot()


def shutdown(rt):
    """Back to the REPL (Ctrl-C / mpremote): stop the watchdog and save."""
    _persist.disable_watchdog()
    _persist.clear_all()
    try:
        _state.flush_all(force=True)
        if rt is not None and rt.config is not None:
            rt.config.save()
    except OSError:
        pass
    if rt is not None and rt.compositor is not None:
        builtins.display = rt.compositor.driver
        builtins.screen = rt.compositor.fb


async def _sleep_ms(ms):
    import asyncio

    if hasattr(asyncio, "sleep_ms"):
        await asyncio.sleep_ms(ms)
    else:
        await asyncio.sleep(ms / 1000)


async def _draw(rt):
    while True:
        t = _compat.ticks_ms()
        rt.host.frame(t)
        rt.watchdog.feed()
        spent = _compat.ticks_diff(_compat.ticks_ms(), t)
        await _sleep_ms(max(1, 16 - spent))


async def _housekeeping(rt):
    while True:
        await _sleep_ms(HOUSEKEEPING_MS)
        try:
            housekeeping(rt, _compat.ticks_ms())
        except Exception as e:  # noqa: BLE001 - a full flash must not stop the display
            print(f"housekeeping: {_compat.error_text(e)}")


async def _main(rt):
    import asyncio

    asyncio.create_task(_housekeeping(rt))
    await _draw(rt)


def run():
    import asyncio

    rt = None
    try:
        rt = boot()
        asyncio.run(_main(rt))
    except KeyboardInterrupt:
        shutdown(rt)
        print("gitmatrix: stopped, watchdog off")
    except Exception as e:  # noqa: BLE001 - a host bug: reboot; repeated failures land in safe mode
        _compat.print_exception(e)
        try:
            _state.flush_all(force=True)
        except OSError:
            pass
        _persist.reset(1000, keep_boot=True)
