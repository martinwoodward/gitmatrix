# Small shims so the runtime runs both on MicroPython and in the CPython test harness.

import os
import time

try:
    ticks_ms = time.ticks_ms
    ticks_us = time.ticks_us
    ticks_diff = time.ticks_diff
    ticks_add = time.ticks_add
except AttributeError:
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_us():
        return int(time.monotonic() * 1000000)

    def ticks_diff(a, b):
        return a - b

    def ticks_add(a, b):
        return a + b

try:
    from sys import print_exception
except ImportError:
    import traceback

    def print_exception(e):
        traceback.print_exception(type(e), e, e.__traceback__)

try:
    import gc

    collect = gc.collect
except (ImportError, AttributeError):
    def collect():
        pass


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def is_dir(path):
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


def _no_guard():
    pass


# Called before every flash write below; gitmatrix._flash installs its check,
# which raises FlashBusy (an OSError) while Bluetooth audio is streaming.
guard = _no_guard


def makedirs(path):
    """mkdir -p, MicroPython has no os.makedirs."""
    built = ""
    for part in path.split("/"):
        if not part:
            continue
        built = built + "/" + part if built or path.startswith("/") else part
        if not exists(built):
            guard()
            os.mkdir(built)


def write_atomic(path, data):
    """Write to a temp file then rename over the target, so a power cut leaves the
    old file or the new one, never half of either."""
    guard()
    tmp = path + ".tmp"
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with open(tmp, mode) as f:
        f.write(data)
    os.rename(tmp, path)


def remove_tree(path):
    if exists(path):
        guard()
    if is_dir(path):
        for entry in os.listdir(path):
            remove_tree(path + "/" + entry)
        os.rmdir(path)
    elif exists(path):
        os.remove(path)


def error_text(e):
    return f"{type(e).__name__}: {e}"


def valid_name(name):
    """Plugin names become module names, so they must be identifiers."""
    if not name or name[0].isdigit():
        return False
    for ch in name:
        if not (ch.isalpha() or ch.isdigit() or ch == "_"):
            return False
    return True
