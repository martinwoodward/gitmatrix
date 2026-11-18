# The one gate every flash write goes through.
#
# Phase 0 found that any flash write while bt_audio is streaming A2DP can hang
# the RP2350 (a 64 KB write hung it 2 runs out of 2, USB gone until a replug).
# Writes are fine when nothing is streaming, and after bt_audio.deinit(). So:
#
#   * background writers (state, config, link keys, the index cache) ask
#     can_write() and defer until playback stops - however long that takes;
#   * user-initiated writes (WiFi save, plugin install/update/remove, settings)
#     run inside `with pause():`, which stops the sink if it's streaming
#     (deinit), lets the burst through, then brings it back (link keys, init,
#     volume, pairable). The phone does NOT reconnect on its own, so the web UI
#     warns first (AUDIO_WARNING) or queues the action until playback stops;
#   * _compat's write helpers call check(), so a stray write while streaming
#     raises FlashBusy (an OSError) instead of hanging the board.

from gitmatrix import _compat, _log, _runtime

try:
    import bt_audio
except ImportError:
    bt_audio = None

NAME = "GitMatrix"
KEYS_FILE = "/bt_link_keys.bin"
AUDIO_WARNING = "Bluetooth audio will disconnect; reconnect your device"


class FlashBusy(OSError):
    pass


_bt = bt_audio
_depth = 0          # nested pause() blocks
_down = None        # (link keys, volume) while the sink is stopped by pause()
pauses = 0          # times the sink was stopped, for the System page and tests
on_resume = None    # re-applies settings the driver forgets (pairable); set by boot


def set_bt(bt):
    global _bt
    _bt = bt


def sink_down():
    return _down is not None


def streaming():
    """A2DP audio is playing right now (and so flash must not be written)."""
    if _down is not None or _bt is None:
        return False
    try:
        return bool(_bt.streaming())
    except Exception:  # noqa: BLE001 - a driver hiccup reads as "not streaming"
        return False


def can_write():
    return not streaming()


def check():
    """Called before every flash write. Raises FlashBusy while streaming."""
    if streaming():
        raise FlashBusy("flash busy: Bluetooth audio is streaming")


def drop_keys():
    """Forget the link keys pause() is holding, so the sink comes back unpaired."""
    global _down
    if _down is not None:
        _down = (b"", _down[1])


def _call(name, *args):
    fn = getattr(_bt, name, None)
    return fn(*args) if fn is not None else None


def _stop():
    global _down, pauses
    keys = b""
    dirty = False
    volume = None
    try:
        keys = _call("link_keys") or b""
        dirty = bool(_call("link_keys_dirty"))
        volume = _call("volume")
    except Exception as e:  # noqa: BLE001 - keep going: the write matters more
        _log.log(f"flash: reading link keys failed {_compat.error_text(e)}")
    _call("deinit")
    _down = (bytes(keys), volume)
    pauses += 1
    _log.log("flash: Bluetooth audio paused for a flash write")
    if dirty and keys:
        # the sink is down, so this is the safest moment to keep a new pairing
        try:
            _compat.write_atomic(_runtime.path(KEYS_FILE), bytes(keys))
        except OSError as e:
            _log.log(f"flash: could not save link keys {_compat.error_text(e)}")


def _start():
    global _down
    keys, volume = _down
    _down = None
    try:
        _call("load_link_keys", keys)
        _bt.init(name=NAME)
        if volume is not None:
            _call("set_volume", volume)
        if on_resume is not None:
            on_resume()
    except Exception as e:  # noqa: BLE001 - no speaker is bad, a crash is worse
        _log.log(f"flash: Bluetooth restart failed {_compat.error_text(e)}")
    _log.log("flash: Bluetooth audio back on; reconnect your device")


class pause:  # noqa: N801 - used as `with _flash.pause():`
    """Let a burst of flash writes through, stopping the Bluetooth sink first if
    it's streaming. resume=False leaves it stopped (reboot, REPL)."""

    def __init__(self, resume=True):
        self.resume = resume
        self.stopped = False

    def __enter__(self):
        global _depth
        if _bt is not None and _down is None and streaming():
            _stop()
            self.stopped = True
        _depth += 1
        return self

    def __exit__(self, *exc):
        global _depth
        _depth -= 1
        if _depth == 0 and _down is not None and self.resume:
            _start()
        return False


_compat.guard = check
