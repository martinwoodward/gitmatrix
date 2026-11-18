# Crash marker and boot-loop counter, kept somewhere a watchdog reset doesn't clear.
#
# The default backend is machine.mem_backup()[0..1], which on this firmware is
# watchdog SCRATCH0/1: zero flash wear, survives soft and watchdog resets, cleared
# at power-on (verified in phase 0). Without mem_backup we poke SCRATCH0/1 via
# mem32. Don't use SCRATCH2-4 or POWMAN BOOT0-3 (bootrom/firmware). It sits behind
# set_store() so the backend can be swapped.
#
# Every deliberate reboot goes through reset(): machine.soft_reset() with the
# hardware watchdog stopped first. Warm resets (machine.reset(), a watchdog timeout,
# picotool reboot) hang this board about 1 in 3 until a power cycle; soft resets
# never have (docs/gitmatrix/ondevice.md). The watchdog is only a last resort.
#
# Marker layout, kept under 2**30 so MicroPython writes it without allocating:
#   bits 29..24 MAGIC, 23..16 crc8(plugin name), 15..4 plugin index, 3..0 hook

try:
    import machine
except ImportError:
    machine = None

WATCHDOG_BASE = 0x400D8000          # RP2350
WATCHDOG_CTRL = WATCHDOG_BASE + 0x00
WATCHDOG_ENABLE = 1 << 30
WATCHDOG_SCRATCH0 = WATCHDOG_BASE + 0x0C

MARKER_REG = 0   # mem_backup()[0] = SCRATCH0
BOOT_REG = 1     # mem_backup()[1] = SCRATCH1
MAGIC = 0x2A
BOOT_MAGIC = 0x2A00

HOOKS = ("import", "update", "on_show", "on_hide", "on_input", "process_config", "on_notify", "on_settings")
HOOK_IDS = {name: i for i, name in enumerate(HOOKS)}


class ScratchStore:
    def __init__(self, mem32, base=WATCHDOG_SCRATCH0):
        self.mem32 = mem32
        self.base = base

    def read(self, reg):
        return self.mem32[self.base + reg * 4] & 0xFFFFFFFF

    def write(self, reg, value):
        self.mem32[self.base + reg * 4] = value


class BackupStore:
    """machine.mem_backup(): a uint32 array over the watchdog scratch registers."""

    def __init__(self, words):
        self.words = words

    def read(self, reg):
        return self.words[reg] & 0xFFFFFFFF

    def write(self, reg, value):
        self.words[reg] = value


class MemoryStore:
    """For the host harness, or boards without the registers. Lost on reset."""

    def __init__(self):
        self.regs = [0] * 8

    def read(self, reg):
        return self.regs[reg]

    def write(self, reg, value):
        self.regs[reg] = value


def _default_store():
    if machine is None:
        return MemoryStore()
    if hasattr(machine, "mem_backup"):
        try:
            words = machine.mem_backup()
            if len(words) >= 2:
                return BackupStore(words)
        except (OSError, TypeError, ValueError):
            pass
    if hasattr(machine, "mem32"):
        return ScratchStore(machine.mem32)
    return MemoryStore()


store = _default_store()


def set_store(new_store):
    """Swap the backing store (tests, or another register bank). Returns the old one."""
    global store
    old, store = store, new_store
    return old


def crc8(text):
    crc = 0
    for ch in text:
        crc ^= ord(ch) & 0xFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def marker(index, name, hook):
    """The value mark() writes. Plugins precompute theirs so a hook call costs one store."""
    return (MAGIC << 24) | (crc8(name) << 16) | ((index & 0xFFF) << 4) | (HOOK_IDS[hook] & 0xF)


def mark(value):
    store.write(MARKER_REG, value)


def clear_mark():
    store.write(MARKER_REG, 0)


def read_mark():
    """(index, crc, hook name) of the hook that was running, or None."""
    value = store.read(MARKER_REG)
    if value >> 24 != MAGIC:
        return None
    hook = value & 0xF
    return (value >> 4) & 0xFFF, (value >> 16) & 0xFF, HOOKS[hook] if hook < len(HOOKS) else "?"


def boot_count():
    value = store.read(BOOT_REG)
    if value >> 8 != BOOT_MAGIC >> 8:
        return 0
    return value & 0xFF


def bump_boot():
    count = min(255, boot_count() + 1)
    store.write(BOOT_REG, BOOT_MAGIC | count)
    return count


def clear_boot():
    store.write(BOOT_REG, 0)


def disable_watchdog():
    """machine.WDT can't be stopped, but the hardware can. Used when the REPL takes
    over (Ctrl-C from mpremote) so a deploy isn't reset part way through."""
    if machine is not None and hasattr(machine, "mem32"):
        machine.mem32[WATCHDOG_CTRL] &= ~WATCHDOG_ENABLE
        return True
    return False


def clear_all():
    clear_mark()
    clear_boot()


def reset(keep_boot=False):
    """Restart via machine.soft_reset(), never a warm reset. The watchdog keeps
    running across a soft reset, so it's stopped first (boot arms it again).
    keep_boot leaves the boot counter alone, so repeated crash-reboots still reach
    safe mode (mem_backup survives a soft reset)."""
    clear_mark()
    if not keep_boot:
        clear_boot()
    if machine is None:
        raise SystemExit("reboot")
    disable_watchdog()
    machine.soft_reset()
