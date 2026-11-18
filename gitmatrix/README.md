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
  firmware/sounds/*.wav            -> /sounds/  built-in alert sounds (custom ones go to /data/sounds/)
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
      _events    event bus: ring of seq-numbered events + latest state (long-poll, MQTT later)
      _notify    notification validation, queue, quiet hours, sound policy, renderer
      _sounds    built-in/custom sounds, uploads, I2S playback
      _png       PNG size checks, fit to 7 rows, decode
      _config    /config.py load / migrate / save-in-place
      _persist   crash marker + boot counter (machine.mem_backup()[0..1]), watchdog reboot
      _wifi      STA / setup AP / fallback AP state machine, mDNS name, scans, captive DNS
      _web       phew routes: JSON API under /api/v1, token auth, static pages, captive redirect
      _log       in-RAM log ring (shown on the System page; never written to flash)
      glow       shared drawing helpers for built-in plugins
  modules/python/phew/             -> /lib/phew/  vendored phew (patched for 1.29 asyncio + an existing loop)
  modules/python/wifi.py, timezones.py, plugin_config.py, scrolling.py, fetch.py
                                   -> /lib/  Art Matrix compatibility
  tools/deploy.py                  mpremote sync of the bundle
  tools/make_sounds.py             regenerates firmware/sounds/ (procedural, no samples)
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

API (JSON, `/api/v1/`; full reference with curl examples in
[docs/gitmatrix/api.md](../docs/gitmatrix/api.md); the System page's **Developer** section
can send calls and watch events):

| Endpoint | |
|---|---|
| `GET status` | plugin, mode, wifi, bluetooth, brightness, volume, encoder, events seq, memory, flash, notifications, uptime |
| `GET events?since=&timeout=&types=` | long-poll for knob, plugin, mode, notification, Bluetooth, WiFi, brightness and volume events (max 2 waiting, 30 s) |
| `GET/POST brightness`, `volume` | `{"value": 0-100}` |
| `GET plugins`, `GET plugins/<name>` | list / one plugin |
| `POST plugins/next`, `prev`, `order` | rotate, reorder (`{"order": [...]}`) |
| `POST plugins/<name>/show` | switch to it; with `{data, duration}` show it for a while then go back (202) |
| `POST plugins/<name>/enable`, `disable`, `unquarantine` | |
| `POST/DELETE sound` | play a sound now (`{sound \| sound_url \| sound_data, priority, force}`) under the alert settings; stop it |
| `GET/POST settings` | delay, hostname, time zone, API token (write-only), alert sound and quiet hours |
| `GET/POST wifi`, `POST wifi/forget` | status; save `{ssid, password}` and reboot |
| `POST wifi/scan` (202, 429 within 15 s of the last), `GET wifi/networks` | rescan, results |
| `GET bluetooth`, `POST bluetooth/forget`, `bluetooth/pairable` | |
| `GET system`, `GET system/logs`, `POST system/reboot` | |
| `GET store`, `POST store/refresh` | plugin catalogue (index + installed/update state); refetch the index |
| `GET/POST store/install` | `{name, pause_audio?, queue?}` → 202 + progress; GET polls it |
| `POST plugins/<name>/remove` | `{keep_data, pause_audio?}` |
| `GET/POST plugins/<name>/settings` | schema + values; secrets are write-only (`"set": true`) |
| `GET/POST /config?plugin=<name>` | the plugin's own Art Matrix-style `config.html` |
| `GET/POST/DELETE notify` | status; send a notification (202 + id); dismiss (`?id=` for one) |
| `GET sounds`, `GET/POST/DELETE sounds/<name>`, `POST sounds/<name>/play` | list; upload a WAV (raw `audio/wav` body or `{"data": base64}`), delete, play |

## Notifications and sounds

Send a notification from anything on the LAN (add `-H 'Authorization: Bearer <token>'`
if a token is set):

```sh
curl -X POST http://gitmatrix.local/api/v1/notify -H 'Content-Type: application/json' \
  -d '{"text": "Build failed on main", "sound": "error", "priority": "high", "color": "#ff4040"}'
```

It interrupts the plugin on screen, then everything goes back to how it was. The
**Notifications** page sends them too and shows the queue.

| Field | |
|---|---|
| `text` | up to 500 characters; scrolls if it doesn't fit |
| `image` / `image_url` | a PNG, base64 (≤ 32 KB) or a URL, up to 2048×64. 53×7 or smaller is centred; wider than 53 scrolls sideways; 53 wide and taller than 7 scrolls up; bigger both ways is scaled down to 7 rows first. An image ≤ 7 wide with `text` is an icon at the left |
| `plugin` + `data` | show an installed plugin instead and pass it `data` ([plugin API](../docs/gitmatrix/plugin-api.md#notifications)) |
| `sound` / `sound_url` / `sound_data` | a built-in or uploaded sound name, a WAV URL (≤ 256 KB) or base64 WAV (≤ 32 KB) |
| `priority` | `normal` or `high` (jumps the queue) |
| `duration`, `repeat` | seconds on screen (default automatic, max 60); passes (max 5) |
| `ttl`, `id` | how long it may wait in the queue (default 600 s); same `id` replaces it |
| `color`, `font`, `speed` | `#rrggbb`, a font name, scroll pixels per second (5-100) |

Limits are checked before anything is read or decoded: body ≤ 64 KB (413), queue of 8
(429), bad fields (400). A knob press dismisses a notification; press again to open the
menu. Notifications wait while the menu is open (a red pixel blinks for high-priority
ones).

**Sounds**: `chime`, `ping`, `alert`, `success`, `error`, `doorbell`, `coin` are built
in (generated by `tools/make_sounds.py`). Upload your own on the **Sounds** page or with
`curl -X POST --data-binary @bell.wav -H 'Content-Type: audio/wav'
http://gitmatrix.local/api/v1/sounds/bell` (16-bit PCM WAV, ≤ 256 KB each, 2 MB in
total), then use `"sound": "bell"`.

**Alert sound** (Notifications page, Menu → Alerts, `settings` API): sound on/off, quiet
hours (from/to and days) and whether high priority rings during quiet hours. Muted
notifications still show. While a phone streams music to the speaker, notifications
are silent unless you turn on *High priority sounds play over Bluetooth audio*: the
alert then replaces the music for a moment and the music comes back on its own.

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
- Reboots always go through the watchdog (`gitmatrix.reboot()`): `machine.reset()` can
  leave USB dead until a power cycle on this board.
