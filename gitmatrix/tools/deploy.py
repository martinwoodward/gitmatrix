#!/usr/bin/env python3
"""Copy the GitMatrix filesystem bundle onto a widget running the stock 1.29 UF2.

    python3 gitmatrix/tools/deploy.py              # sync everything
    python3 gitmatrix/tools/deploy.py --dry-run    # just print the plan
    python3 gitmatrix/tools/deploy.py --port /dev/cu.usbmodem1101 --reset

User data (/config.py, /gitmatrix_config.py, /data, /secrets, /bt_link_keys.bin,
/contrib_data.json) and store-installed plugins are never touched. Stop Bluetooth
playback first: a flash write while the speaker streams can hang the board.
Needs `mpremote` (pip install mpremote).
"""

import argparse
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent

# (source relative to gitmatrix/, destination on the device)
MAPPING = [
    ("modules/python", "/lib"),
    ("firmware/lib/plugins", "/lib/plugins"),
    ("firmware/defaults", "/defaults"),
    ("firmware/html", "/html"),
    ("firmware/sounds", "/sounds"),
    ("firmware/main.py", "/main.py"),
]

SKIP = {"__pycache__", ".DS_Store"}

# Stops the running app's watchdog so it can't reset the board mid-copy
# (machine.WDT can't be stopped from Python, but the hardware enable bit can).
STOP_WATCHDOG = "import machine; machine.mem32[0x400D8000] &= ~(1 << 30)"


def plan():
    """[(local file, device path)] for everything in the bundle."""
    out = []
    for src, dst in MAPPING:
        path = HERE / src
        if path.is_file():
            out.append((path, dst))
            continue
        for f in sorted(path.rglob("*")):
            if f.is_file() and not SKIP.intersection(f.parts):
                out.append((f, dst + "/" + f.relative_to(path).as_posix()))
    return out


def device_dirs(files):
    dirs = set()
    for _, dst in files:
        parts = dst.strip("/").split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/" + "/".join(parts[:i]))
    return sorted(dirs, key=lambda d: (d.count("/"), d))


def commands(files, port=None, reset=False):
    mkdirs = "import os\nfor d in %r:\n try:\n  os.mkdir(d)\n except OSError:\n  pass" % device_dirs(files)
    cmd = ["mpremote"] + (["connect", port] if port else [])
    cmd += ["exec", STOP_WATCHDOG, "+", "exec", mkdirs]
    for src, dst in files:
        cmd += ["+", "fs", "cp", str(src), ":" + dst]
    if reset:
        # machine.reset() can wedge USB on this firmware; reboot via the watchdog
        cmd += ["+", "exec", "import machine\nmachine.WDT(timeout=100)\nwhile True:\n pass"]
    return cmd


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="serial port (default: mpremote auto-detect)")
    p.add_argument("--dry-run", action="store_true", help="print what would be copied")
    p.add_argument("--reset", action="store_true", help="reboot (via watchdog) when done")
    args = p.parse_args(argv)

    files = plan()
    for src, dst in files:
        print(f"{src.relative_to(HERE)} -> {dst}")
    cmd = commands(files, args.port, args.reset)
    if args.dry_run:
        print(f"\n{len(files)} files; would run: {' '.join(cmd[:6])} ...")
        return 0
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
