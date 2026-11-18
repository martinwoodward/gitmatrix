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
import time

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


def commands(files, port=None):
    mkdirs = "import os\nfor d in %r:\n try:\n  os.mkdir(d)\n except OSError:\n  pass" % device_dirs(files)
    cmd = ["mpremote"] + (["connect", port] if port else [])
    cmd += ["exec", STOP_WATCHDOG, "+", "exec", mkdirs]
    for src, dst in files:
        cmd += ["+", "fs", "cp", str(src), ":" + dst]
    return cmd


def soft_reset(port=None):
    """Ctrl-B (leave mpremote's raw REPL) + Ctrl-D: a soft reset that runs /main.py.
    Never machine.reset() or the watchdog: warm resets can hang this board until
    it's unplugged. Needs pyserial (installed with mpremote)."""
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("pyserial missing: press Ctrl-D in a REPL to start GitMatrix")
        return 1
    if port is None:
        ports = [p.device for p in list_ports.comports() if p.vid == 0x2E8A]
        if not ports:
            print("no widget found; press Ctrl-D in a REPL to start GitMatrix")
            return 1
        port = ports[0]
    with serial.Serial(port, 115200, timeout=1) as s:
        s.write(b"\x03\x02")
        time.sleep(0.2)
        s.write(b"\x04")
        time.sleep(0.5)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="serial port (default: mpremote auto-detect)")
    p.add_argument("--dry-run", action="store_true", help="print what would be copied")
    p.add_argument("--reset", action="store_true", help="soft-reset when done (starts GitMatrix)")
    args = p.parse_args(argv)

    files = plan()
    for src, dst in files:
        print(f"{src.relative_to(HERE)} -> {dst}")
    cmd = commands(files, args.port)
    if args.dry_run:
        print(f"\n{len(files)} files; would run: {' '.join(cmd[:6])} ...")
        return 0
    code = subprocess.run(cmd, check=False).returncode
    if code == 0 and args.reset:
        code = soft_reset(args.port)
    return code


if __name__ == "__main__":
    sys.exit(main())
