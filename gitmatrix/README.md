# GitMatrix firmware

An Art Matrix-style plugin runtime for the 53×7 GitHub Contribution Widget (RP2350,
MicroPython 1.29). It is always a Bluetooth speaker called **GitMatrix**, and a knob
press always opens the snappy menu. See [docs/gitmatrix/plan.md](../docs/gitmatrix/plan.md)
for the design and [docs/gitmatrix/plugin-api.md](../docs/gitmatrix/plugin-api.md) for plugins.

## Layout

```
gitmatrix/
  firmware/main.py                 -> /main.py   (just runs gitmatrix._boot)
  firmware/defaults/config.py      -> /defaults/config.py (copied to /config.py, then migrated)
  firmware/lib/plugins/<name>/     -> /lib/plugins/  built-in plugins
  modules/python/gitmatrix/        -> /lib/gitmatrix/  the runtime
      _boot      boot sequence, safe mode, watchdog, main loop
      _loader    plugin import + fault containment / quarantine
      _host      frame loop, rotation, transitions, messages, menu glue
      _menu      snappy menu and its screens
      _display   compositor + display/screen proxies
      _input     encoder + button events
      _audio     bt_audio speaker + mic-compatible analysis
      _state     per-plugin state and typed settings
      _config    /config.py load / migrate / save-in-place
      _persist   crash marker + boot counter (machine.mem_backup()[0..1]), watchdog reboot
      glow       shared drawing helpers for built-in plugins
  modules/python/plugin_config.py, scrolling.py   -> /lib/  Art Matrix compatibility
  tools/deploy.py                  mpremote sync of the bundle
  tests/                           CPython harness (stubs for the device modules)
```

## Deploy

Flash the stock `contribution_widget-micropython-1.29` UF2, then:

```sh
python3 gitmatrix/tools/deploy.py --dry-run   # what will be copied
python3 gitmatrix/tools/deploy.py --reset
```

User data (`/config.py`, `/data`, `/secrets`, `/bt_link_keys.bin`) is left alone.

## Develop

```sh
cd gitmatrix
python3 -m pytest          # host tests (pytest ≥ 8)
ruff check .
```

The tests run the real runtime under CPython against the stubs in `tests/stubs`.
`rig` (in `conftest.py`) boots the whole thing and lets a test turn and press the knob
and step frames on a fake clock.

## Safe mode and recovery

- Hold the knob while powering on, or crash 3 times before reaching 60 s of uptime: the
  device boots with no plugins, and the menu still works.
- A plugin that hangs trips the 8 s watchdog. It's quarantined on the next boot.
- Reboots always go through the watchdog (`gitmatrix.reboot()`): `machine.reset()` can
  leave USB dead until a power cycle on this board.
