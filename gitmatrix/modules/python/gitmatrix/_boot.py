# Boot sequence and the main loop tasks. /main.py just calls run().

import builtins

import gitmatrix
from gitmatrix import _audio, _compat, _flash, _log, _persist, _runtime, _state, _store
from gitmatrix._config import Config, load_firmware_config
from gitmatrix._display import Compositor, DisplayProxy
from gitmatrix._host import Host
from gitmatrix._input import Input, PluginInput
from gitmatrix._loader import PluginLoader
from gitmatrix._wifi import TICK_MS as WIFI_TICK_MS
from gitmatrix._wifi import WiFi

try:
    import machine
except ImportError:
    machine = None

WATCHDOG_MS = 8000
HEALTHY_MS = 60000
BOOT_LOOP_LIMIT = 3
HOUSEKEEPING_MS = 2000
STORE_TICK_MS = 5000


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
        self.wifi = None
        self.store = None
        self.booted = 0
        self.healthy = False


def boot(input_=None, bt=None, network_=None):
    rt = Runtime()
    rt.booted = _compat.ticks_ms()
    boots = _persist.bump_boot()
    was_watchdog = watchdog_reset()

    rt.config = config = Config().load()
    _runtime.config = config
    if config.repaired:
        print(f"boot: config repaired ({config.repaired})")
    _runtime.firmware = load_firmware_config()
    _runtime.reboot_at = None

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

    # finish or undo a plugin install a power cut interrupted, before anything
    # loads - and before Bluetooth is up, so its flash writes can't meet a stream
    try:
        _store.reconcile()
    except Exception as e:  # noqa: BLE001 - a muddled store must not stop the display
        _log.log(f"store: reconcile failed {_compat.error_text(e)}")

    # The speaker is always on, whatever is (or isn't) running
    if bt is not None:
        gitmatrix.audio._bt = bt  # noqa: SLF001
    _audio.start(gitmatrix.audio._bt)  # noqa: SLF001
    _runtime.audio = gitmatrix.audio

    rt.watchdog = _Watchdog(config.get("watchdog", True))
    rt.store = _store.Store()
    rt.store.feed = rt.watchdog.feed
    rt.loader = loader = PluginLoader(config, rt.compositor, rt.watchdog.feed)
    loader.sync_config()
    crashed = loader.check_crash(was_watchdog)

    rt.host = host = Host(config, loader, rt.compositor, rt.input, gitmatrix.audio, safe)
    _flash.on_resume = host.apply_pairable
    if not safe:
        loader.load_all()
    host.start()
    if crashed:
        host.show_message(f"{crashed} hung and was stopped", 5000, 25)
    elif safe:
        print(f"boot: safe mode, {safe}")
    # after Bluetooth is up: WiFi shares the CYW43 radio with it
    rt.wifi = _runtime.wifi = WiFi(config, _runtime.firmware, network_)
    try:
        rt.wifi.start()
        # the event loop isn't running yet, so the blocking scan stalls nothing
        rt.wifi.prescan(rt.watchdog.feed)
    except Exception as e:  # noqa: BLE001 - no WiFi is bad, but no display and speaker is worse
        _log.log(f"wifi: start failed {_compat.error_text(e)}")
    if _flash.can_write():      # else housekeeping saves it once playback stops
        config.save()
    rt.watchdog.feed()
    return rt


def housekeeping(rt, now):
    """Flushes and timers. Called every couple of seconds. Background writes all
    wait while Bluetooth audio streams: a flash write then can hang the board."""
    if _flash.can_write():
        _audio.save_link_keys()
        _state.flush_all()
        if rt.config.dirty:
            rt.config.save()
        if rt.store is not None:
            rt.store.flush()
    if not rt.healthy and _compat.ticks_diff(now, rt.booted) > HEALTHY_MS:
        rt.healthy = True
        _persist.clear_boot()
    if _runtime.reboot_at is not None and _compat.ticks_diff(now, _runtime.reboot_at) >= 0:
        # asked for by the web UI (WiFi saved, Reboot button); never machine.reset()
        _runtime.reboot_at = None
        gitmatrix.reboot()


def shutdown(rt):
    """Back to the REPL (Ctrl-C / mpremote): stop the watchdog and save."""
    _persist.disable_watchdog()
    _persist.clear_all()
    try:
        # the sink stays stopped: mpremote writes to flash next (deploys)
        with _flash.pause(resume=False):
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


async def _wifi(rt):
    while True:
        await _sleep_ms(WIFI_TICK_MS)
        try:
            rt.wifi.tick(_compat.ticks_ms())
        except Exception as e:  # noqa: BLE001 - keep retrying; the display carries on
            _log.log(f"wifi: {_compat.error_text(e)}")


async def _store_task(rt):
    """Keep the plugin index fresh while online (Discover reads the cache)."""
    while True:
        await _sleep_ms(STORE_TICK_MS)
        store = rt.store
        try:
            if store is not None and rt.wifi.connected() and store.due(_compat.ticks_ms()):
                await store.refresh()
        except Exception as e:  # noqa: BLE001 - the store is optional
            _log.log(f"store: {_compat.error_text(e)}")


async def _main(rt):
    import asyncio

    asyncio.create_task(_housekeeping(rt))
    if rt.wifi is not None and rt.wifi.net is not None:
        asyncio.create_task(_wifi(rt))
        asyncio.create_task(_store_task(rt))
        try:
            from gitmatrix import _web

            await _web.start(rt)
        except Exception as e:  # noqa: BLE001 - the display and speaker must outlive the web UI
            _log.log(f"web: {_compat.error_text(e)}")
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
            with _flash.pause(resume=False):
                _state.flush_all(force=True)
        except Exception:  # noqa: BLE001, S110 - rebooting regardless
            pass
        _persist.reset(keep_boot=True)
