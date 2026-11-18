# A small in-memory log for the web UI's System page. Lines are printed (so the
# REPL/mpremote still sees them) and kept in a ring; nothing is written to flash,
# because a flash write stalls both cores and the Bluetooth audio with them.

from gitmatrix import _compat

MAX_LINES = 80

lines = []      # [(ticks_ms, text)]


def log(*items):
    text = " ".join(str(i) for i in items)
    print(text)
    lines.append((_compat.ticks_ms(), text))
    if len(lines) > MAX_LINES:
        del lines[0]


def phew_log(level, text):
    """Replacement for phew.logging.log: console + ring, never the log.txt file."""
    log(f"web {level}: {text}")


def recent(n=MAX_LINES):
    return lines[-n:]


def clear():
    del lines[:]
