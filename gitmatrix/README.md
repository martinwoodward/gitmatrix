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
  firmware/defaults/gitmatrix_config.py -> /defaults/  (copied once to /gitmatrix_config.py:
                                      setup AP SSID/password, plugin repo; never in the web UI)
  firmware/html/                   -> /html/  static web UI (pages + static/app.js, app.css)
  firmware/lib/plugins/<name>/     -> /lib/plugins/  built-in plugins
  modules/python/gitmatrix/        -> /lib/gitmatrix/  the runtime
      _boot      boot sequence, safe mode, watchdog, main loop
      _loader    plugin import + fault containment / quarantine
      _host      frame loop, rotation, transitions, messages, menu glue
      _menu      snappy menu and its screens
      _display   compositor + display/screen proxies
      _input     encoder + button events
      _audio     bt_audio speaker + mic-compatible analysis
      _state     per-plugin state and typed settings (schema, secrets in /secrets)
      _store     plugin store: index cache, transactional install/update/remove, boot reconcile
      _flash     the single flash writer: no writes while Bluetooth audio streams
      _config    /config.py load / migrate / save-in-place
      _persist   crash marker + boot counter (machine.mem_backup()[0..1]), soft-reset reboot
      _wifi      STA / setup AP / fallback AP state machine, mDNS name, scans, captive DNS
      _ntp       sets the RTC from NTP while online (non-blocking UDP)
      _web       phew routes: JSON API under /api/v1, token auth, static pages, captive redirect
      _log       in-RAM log ring (shown on the System page; never written to flash)
      glow       shared drawing helpers for built-in plugins
  modules/python/phew/             -> /lib/phew/  vendored phew (patched for 1.29 asyncio + an existing loop)
  modules/python/wifi.py, timezones.py, plugin_config.py, scrolling.py, fetch.py
                                   -> /lib/  Art Matrix compatibility
  tools/deploy.py                  mpremote sync of the bundle
  tests/                           CPython harness (stubs for the device modules)
```

## Deploy

Flash the stock `contribution_widget-micropython-1.29` UF2, then:

```sh
python3 gitmatrix/tools/deploy.py --dry-run   # what will be copied
python3 gitmatrix/tools/deploy.py --reset
```

User data (`/config.py`, `/gitmatrix_config.py`, `/data`, `/secrets`, `/bt_link_keys.bin`)
and store-installed plugins (`/lib/plugins/active.json`) are left alone. Stop any
Bluetooth playback before deploying: a flash write while the speaker streams can hang the
board.

## WiFi and the web UI

- No network saved: the device starts a WPA2 access point **GitMatrix** (password
  **`monalisa`**, change it in `/gitmatrix_config.py`) at `http://192.168.4.1`, with a
  captive portal that lands on the WiFi page. Scan, pick a network, enter the password,
  **Save & reboot**.
- Network saved: it joins it as `gitmatrix.local` (hostname in Settings). The AP is off
  while connected. If the network can't be reached for 1 minute the setup AP comes back
  as well, while it keeps retrying; it goes away again once connected.
- Knob press → **WiFi** shows the address to visit (or the AP name, password and URL).
- The UI and API are open on the LAN, like Art Matrix. Set an **API token** in Settings
  and `/api/v1/*` then needs `Authorization: Bearer <token>`; pages served by the device
  set an `HttpOnly` cookie so the browser UI keeps working.
- Scanning while a phone is streaming to the speaker can make the audio stutter. The
  CYW43 scan can't run in the background, so setup mode scans once at boot (before
  anything else runs) and **Scan** in the UI pauses the display for a second or two;
  rescans are limited to one per 15 s.

API (JSON, `/api/v1/`; phases 5/6 extend it):

| Endpoint | |
|---|---|
| `GET status` | plugin, wifi, bluetooth, brightness, volume, encoder, uptime |
| `GET/POST brightness`, `volume` | `{"value": 0-100}` |
| `GET plugins`, `GET plugins/<name>` | list / one plugin |
| `POST plugins/next`, `prev`, `order` | rotate, reorder (`{"order": [...]}`) |
| `POST plugins/<name>/show`, `enable`, `disable`, `unquarantine` | |
| `GET/POST settings` | delay, hostname, time zone, API token (write-only) |
| `GET/POST wifi`, `POST wifi/forget` | status; save `{ssid, password}` and reboot |
| `POST wifi/scan` (202, 429 within 15 s of the last), `GET wifi/networks` | rescan, results |
| `GET bluetooth`, `POST bluetooth/forget`, `bluetooth/pairable` | |
| `GET system`, `GET system/logs`, `POST system/reboot` | |
| `GET store`, `POST store/refresh` | plugin catalogue (index + installed/update state); refetch the index |
| `GET/POST store/install` | `{name, pause_audio?, queue?}` → 202 + progress; GET polls it |
| `POST plugins/<name>/remove` | `{keep_data, pause_audio?}` |
| `GET/POST plugins/<name>/settings` | schema + values; secrets are write-only (`"set": true`) |
| `GET/POST /config?plugin=<name>` | the plugin's own Art Matrix-style `config.html` |

Notifications and Sounds pages are placeholders until phase 4.

## Plugin store

**Discover** lists the plugins in `martinwoodward/gitmatrix-plugins` (set `PLUGIN_REPO` /
`PLUGIN_BRANCH` in `/gitmatrix_config.py` to use another repo). The device fetches and
caches its `index.json` every 15 minutes; search and filtering happen in the browser.
Installing downloads and verifies the package, then swaps it into `/lib/plugins/<name>/`
in one burst. A power cut mid-install is repaired at boot, and a failed update puts the
old version back. Each plugin's page (click it on Home) has its settings, its
`config.html` link and **Remove** (keep or delete its data).

**Bluetooth audio**: flash writes can't happen while the speaker streams. Background
saves (state, settings, config, the index cache) wait until playback stops. Installs,
removals and settings saves ask first: the speaker disconnects for the write, and your
phone or computer has to reconnect. Installs can wait for playback to stop instead.

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
- Reboots are soft resets (`gitmatrix.reboot()` stops the watchdog, then calls
  `machine.soft_reset()`): warm resets (`machine.reset()`, the watchdog) can hang the
  whole chip until a power cycle on this board. See `docs/gitmatrix/ondevice.md`.
