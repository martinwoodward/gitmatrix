# Hang watch: stop a plugin hook that runs too long, without a warm reset.
#
# The hardware watchdog resets the chip, and on this board a warm reset hangs
# it about a third of the time until it's unplugged (docs/gitmatrix/phase0.md).
# So a soft machine.Timer checks how long the current hook has been running
# and, past HANG_MS, schedules a KeyboardInterrupt on the main thread
# (micropython.schedule(micropython.kbd_intr, None), MicroPython 1.29).
# PluginLoader.guard() catches it, quarantines the plugin and carries on.
#
# Soft timer callbacks run between bytecodes, so this stops Python loops and
# time.sleep(); a viper/native loop never yields and still meets the hardware
# watchdog (WATCHDOG_MS), which stays as the last resort.

from gitmatrix import _compat

HANG_MS = 5000        # well under the 8 s hardware watchdog
CHECK_MS = 500
REPEAT_MS = 1000      # interrupt again if the plugin swallowed the first one

try:
    import micropython
except ImportError:
    micropython = None


def _kbd_interrupt():
    micropython.schedule(micropython.kbd_intr, None)


class Watch:
    def __init__(self, interrupt=None):
        self.interrupt = interrupt
        self.started = None
        self.hook = None
        self.tripped = False
        self.last_trip = None

    def enter(self, hook, now=None):
        """A hook starts. Returns a token for leave() (hooks can nest)."""
        prev = (self.started, self.hook)
        self.started = _compat.ticks_ms() if now is None else now
        self.hook = hook
        return prev

    def leave(self, token):
        self.started, self.hook = token
        self.last_trip = None

    def check(self, now=None):
        """Timer tick. Interrupts the main thread if a hook has overrun."""
        started = self.started
        if started is None or self.interrupt is None:
            return False
        now = _compat.ticks_ms() if now is None else now
        if _compat.ticks_diff(now, started) < HANG_MS:
            return False
        if self.last_trip is not None and _compat.ticks_diff(now, self.last_trip) < REPEAT_MS:
            return False
        self.tripped = True
        self.last_trip = now
        try:
            self.interrupt()
        except ValueError:
            # this build can't schedule a KeyboardInterrupt: the hardware watchdog it is
            print("hang: kbd_intr scheduling unsupported, watch off")
            self.interrupt = None
            self.tripped = False
        except Exception as e:  # noqa: BLE001 - e.g. queue full: try again next tick
            print(f"hang: interrupt failed {_compat.error_text(e)}")
        return True

    def consume(self):
        """True (once) if the last KeyboardInterrupt was ours, not Ctrl-C."""
        tripped = self.tripped
        self.tripped = False
        return tripped


watch = Watch()
_timer = None


def start():
    """Arm the hang watch on the device. False if this firmware can't do it
    (then a hang still meets the hardware watchdog)."""
    global _timer
    stop()
    if micropython is None:
        return False
    try:
        import machine

        if not hasattr(micropython, "kbd_intr"):
            return False
        watch.interrupt = _kbd_interrupt
        _timer = machine.Timer(-1)
        _timer.init(mode=machine.Timer.PERIODIC, period=CHECK_MS, callback=lambda _t: watch.check())
        return True
    except Exception as e:  # noqa: BLE001 - fall back to the hardware watchdog
        print(f"hang: watch unavailable ({_compat.error_text(e)})")
        watch.interrupt = None
        return False


def stop():
    global _timer
    if _timer is not None:
        try:
            _timer.deinit()
        except Exception:  # noqa: BLE001, S110
            pass
        _timer = None
    watch.started = None
    watch.tripped = False
