# GitMatrix firmware plan (v3: reviews + answered questions)

> v3 decisions: sounds via pause-A2DP → `machine.I2S` → resume; watchdog reboot + quarantine for hung plugins; **security matches Art Matrix** (`CERT_NONE`, open LAN UI, optional token); plugin repo set in a firmware config file only; WPA2 setup AP with a default password; fallback AP after 1 min; firmware lives in this repo (to be renamed `gitmatrix`); notification quiet mode/schedule.

GitMatrix = the Pimoroni 53×7 "GitHub Contribution Widget" (RP2350, 8 MB PSRAM, 16 MB flash,
CYW43 WiFi+BT, I2S amp, rotary encoder + push button, status LEDs, light sensor), running a
firmware modelled closely on [pimoroni/artmatrix](https://github.com/pimoroni/artmatrix).

## 0. Verified facts (probed on the live device / sources)

| Fact | Evidence | Consequence |
|---|---|---|
| User filesystem is **13.6 MB** (3,328 × 4 KB blocks) | `os.statvfs('/')` on device | No custom build needed for storage; phew, web UI, sounds, plugins all live on the FS (88 KB in the UF2 is only the shipped image) |
| ~8.1 MB free heap (PSRAM) | `gc.mem_free()` | Room for offscreen surfaces, RAM-staged installs, decoded images |
| `picovector.image.load()` accepts **bytes** | loaded a 120×7 PNG from `bytes` | Notification PNGs decode from RAM; no temp files / flash wear |
| `network.WLAN.IF_AP` + `IF_STA`, `machine.WDT`, `reset_cause` present | `dir()` on device | AP+STA and watchdog available (coexistence still needs a spike) |
| `bt_audio` has **no PCM playback** and no track metadata | firmware symbols + `bt_audio_visualiser.py` | Sounds use the fallback: pause A2DP sink → play via `machine.I2S` → resume (spike 0c) |
| Speaker plays **left channel only** | earlier test | Built-in sounds mono; mixer must write both channels |
| Display driver lacks `set_hold`/`is_busy`/second surface/blend | firmware strings | Emulate in Python with two 53×7 offscreen images (1.5 KB each) |
| artmatrix `fetch.py` uses `tls.CERT_NONE` | `fetch.py:284` | Copied as-is (decision: match Art Matrix) |
| 12 artmatrix plugins use `rom_font` | grep of artmatrix-plugins | Provide a `rom_font` alias |

## 1. What we copy from Art Matrix (and how it differs)

| Art Matrix | GitMatrix |
|---|---|
| `firmware/main.py`: one asyncio loop with `draw()`, `watch_connection()`, `flush_config()`, phew `server.run()` | Same + `input_task()`, `notify_task()`, `audio_task()`, later `mqtt_task()` |
| Plugins `/lib/plugins/<name>/__init__.py`, module-level setup, `update()` per frame drawing into global `screen` | Identical contract |
| `package.json` (`name`, `version`, `hash`, `description`, `author`, `category`, `requires_internet`, `urls`) | Same + optional `settings`, `input`, `min_firmware`, `tags` |
| `config.html` + `placeholder`, `process_config(form)`, `plugin_config(name, defaults)` | Kept + auto-generated forms from a `settings` schema (incl. secrets) |
| `fetch.AsyncFetch` non-blocking HTTP + DNS cache | Copied as-is (`CERT_NONE`, like Art Matrix) |
| Index `raw.githubusercontent.com/pimoroni/artmatrix-plugins/.../index.json`, cached, 15-min refresh | Same; repo/branch from a firmware config file `/gitmatrix_config.py` (default `martinwoodward/gitmatrix-plugins@main`), not exposed in the web UI |
| Install: stage in `/.install`, rename | Versioned dirs + activation record (power-cut safe; §2.6) |
| Import errors disable; 5 `update()` failures skip; `set_hold` swallows plugin `display.update()` | Same + watchdog-scratch crash marker + boot-loop safe mode |
| `config.py` + `migrate_config()` + coalesced writes | Copied |
| "Art Matrix Setup" AP + captive DNS | "GitMatrix" AP + captive DNS |
| `mic` object | `audio` object fed from Bluetooth FFT, all `mic` method names, aliased as `mic` |
| `rom_font`, `artmatrix` module | Aliases: `rom_font` → `font`, `contribution_widget` → `gitmatrix` |

**Positioning:** plugins are **trusted, cooperative code** (as on Art Matrix), not sandboxed. One
MicroPython interpreter means a plugin *can* import `machine`/`bt_audio` and a tight infinite loop
can only be stopped by the watchdog. The goal is: a well-meaning plugin that throws, leaks, is slow
or hangs is contained/quarantined and the device self-recovers. Docs and the install UI say so.

## 2. Architecture

```
boot.py ─► gitmatrix (FS module, frozen later) sets builtins: screen, display(proxy), font/rom_font,
           color/shape/brush/..., audio/mic, gitmatrix
main.py
 ├─ crash/boot-loop check (watchdog scratch regs) → safe mode if needed
 ├─ config load + migrate
 ├─ bt_audio.init(name="GitMatrix")  (always, first)
 ├─ wifi: STA from config, else AP "GitMatrix" + captive DNS (AP off once STA up)
 ├─ install reconciliation (§2.6), load_plugins()
 └─ asyncio loop
     ├─ draw()             frame loop: PLUGIN | NOTIFICATION | MENU (+ SETUP overlay); feeds WDT
     ├─ input_task()       encoder → host event queue (sequence-numbered)
     ├─ notify_task()      queue, asset prefetch, sound, preemption
     ├─ audio_task()       link keys (debounced), FFT → audio API buffers, BT state
     ├─ watch_connection() reconnect, NTP, index refresh (bounded timeouts)
     ├─ flush_config()     coalesced config/state/secret writes (streaming-aware)
     ├─ phew server        web UI + REST API v1 (auth, Origin/Host checks)
     └─ mqtt_task()        (phase 6) async MQTT + HA discovery
```

### 2.1 Display host
- Each plugin draws into its own state-carrying offscreen `image(53, 7)` bound as `builtins.screen` around its calls (artmatrix `bind()`); the host composites into the real framebuffer. Transitions: slide (like the current carousel) or crossfade.
- Keep artmatrix `direct_framebuffer` detection for plugins that draw to the framebuffer directly → hard cut.
- `display` is a proxy: `update()` is a no-op inside plugin calls (reported once); `set_brightness` reserved to host.
- Status LEDs show WiFi/BT state (no room for icons on 7 rows). Optional auto-brightness from `LIGHT_SENSE`.

### 2.2 Input & menu
- **Button press opens the menu** (snappy_menu animation): `Plugins`, `Brightness`, `Volume`, `Alerts`, `WiFi`, `Bluetooth`, `< Back`.
  - Plugins: turn to scroll installed plugin names, press to show it.
  - Alerts: `Sound on` / `Sound off` / `Quiet hours` (uses the schedule set in the web UI) — §2.10.
  - Brightness/Volume: turn adjusts with live bar, press saves.
  - WiFi: scrolls `http://gitmatrix.local  192.168.1.42  <SSID>`; in AP mode `Join WiFi "GitMatrix" then http://192.168.4.1` + scrolls the AP password.
  - Bluetooth: `GitMatrix  connected/streaming/not paired`; submenu `Pairable`, `Forget devices`, `< Back`.
  - 30 s inactivity returns. Plugins paused (and their tasks suspended, §2.3) while the menu is open.
- Turning in plugin mode = previous/next plugin. A plugin with `"input": true` instead reads `gitmatrix.input.events()` (host-owned, sequence-numbered snapshot, so the menu and HA see the same events). Button is reserved.
- Boot: hold button → safe mode (no plugins). Hold 10 s → WiFi reset to AP setup.

### 2.3 Plugin runtime & fault containment
- **All hooks guarded**: import, `update()`, `process_config()`, settings callbacks, optional `on_show()`, `on_hide()`, `on_notify(data)`, `on_input(evt)`. Errors recorded for the web UI; 5 consecutive failures → quarantine; `MemoryError` → `gc.collect()` + skip frame.
- **Crash marker**: before each plugin hook, write plugin index + hook id into an RP2350 watchdog/POWMAN **scratch register** (survives watchdog reset, zero flash wear); clear on return. After a WDT reset with the marker set → quarantine that plugin and tell the user.
- **Boot-loop guard**: scratch counter incremented early in boot, cleared after 60 s healthy; 3 → safe mode.
- **Watchdog** 8 s, fed by `draw()`; host blocking operations (DNS, NTP, flash, scans) have bounded timeouts and feed it before/after.
- **Slow plugins**: track `update()` time; persistently over budget → flagged "slow" in UI, optionally auto-skipped. `time.sleep` inside plugin hooks is warned (not enforced).
- **Resource ownership**: `gitmatrix.create_task()`, `gitmatrix.fetch()` and timers are tracked per plugin and cancelled/closed on hide (optional), quarantine or unload. Plain `asyncio.create_task` documented as unsupported.
- **Forced plugins** (notifications/API) still respect quarantine.

### 2.4 Plugin API (Art Matrix compatible subset + GitMatrix additions)
- Graphics: `screen` (53×7), `color`, `shape`, `brush`, `vec2`, `mat3`, `font.<name>`/`rom_font.<name>`, `image.load` (path or bytes), filters, `text`/`sprite`/`math` helpers.
- Network: `fetch.AsyncFetch` (Art Matrix behaviour, `CERT_NONE`), `connection` state, `requires_internet`.
- Settings: `plugin_config(name, defaults)` (unchanged) + `gitmatrix.settings(name)` (schema-typed, includes secrets).
- State: `gitmatrix.state(name)` dict-like at `/data/<name>/state.json`; `save()` debounced (≥30 s, deferred while streaming up to a max), atomic (temp + rename); survives plugin updates; optional delete on uninstall.
- Audio (read-only): `audio.streaming()`, `connected()`, `level()`, `spectrum()` (16 bands), `bass()`, `tone()`, `flux()`, `intensity()`, `active()`, `beat()`, `kick()`, `clap()`, `bpm()`, `phase()`, `volume()` — computed once per frame from `bt_audio.fft_into()` into pre-allocated buffers, matching `mic` value ranges. `waveform()` returns a synthesised/empty buffer (documented approximate). No track metadata (bt_audio has no AVRCP).
- Notifications: `gitmatrix.notification` (dict/None) + `on_notify(data)`.
- Helpers: `scrolling.Scroll`, `gitmatrix.show_message(text)`. `qr_screen` not available (7 rows).
- **Compatibility doc**: supported / aliased / unsupported Art Matrix APIs, validated by porting 4–5 representative plugins (audio-reactive, network, config page, generative).

### 2.5 Settings schema (package.json)
```json
"settings": [
  {"key": "username", "type": "text", "label": "GitHub user", "default": "octocat"},
  {"key": "url", "type": "url", "label": "Feed URL"},
  {"key": "token", "type": "secret", "label": "API token"},
  {"key": "refresh", "type": "number", "min": 60, "default": 300},
  {"key": "colour", "type": "color", "default": "#39d353"},
  {"key": "mode", "type": "select", "options": ["graph", "count"]},
  {"key": "animate", "type": "bool", "default": true}
]
```
- Auto-rendered by the web UI; `config.html` overrides (Art Matrix compatible).
- Secrets in `/secrets/<plugin>.json`, never sent back to the browser ("•••• set", Replace/Clear), excluded from exports, `/api` and MQTT. Not encrypted at rest — USB access can read them (documented).

### 2.6 Plugin store
- `PLUGIN_REPO` / `PLUGIN_BRANCH` live in `/gitmatrix_config.py` on the device (edit via mpremote/Thonny). Not exposed in the web UI or API.
- Repo layout mirrors artmatrix-plugins: `<plugin>/__init__.py`, `package.json`, optional `config.html`, `thumbnail.png` (53×7), `ci/generate_packages.py` builds `index.json` with descriptions, categories, tags, versions and sizes (Art Matrix format).
- Discover: device serves cached `index.json`; browser does client-side search/filter; thumbnails load directly from raw.githubusercontent.com.
- Downloads over HTTPS with `CERT_NONE`, exactly as Art Matrix (accepted risk; revisit if the repo ever goes third-party).
- **Transactional install**: free-space check → download + verify into RAM (staged to `/.install/<name>@<ver>` if large) → `/lib/plugins/<name>@<ver>/` → atomically update `/lib/plugins/active.json`. Boot reconciliation removes incomplete versions and falls back to last-known-good. User files live in `/data/<name>` not the plugin dir.

### 2.7 Networking, provisioning & web UI
- `wifi.py` + phew (on FS), mDNS hostname `gitmatrix` (configurable).
- No WiFi configured → AP `GitMatrix` at 192.168.4.1 + captive DNS. AP is **WPA2** with a default password **`monalisa`** (user asked for `mona`; WPA2 requires 8–63 chars; changeable in `/gitmatrix_config.py`), shown on the display and in the menu's WiFi screen. AP turned off once STA connects. If the configured network fails for **1 min**, the AP starts as well while STA keeps retrying; it stops once STA connects.
- Pages: Home (now showing, reorder/enable/disable, delay, Show now), Discover, Plugin settings, Notifications (test sender), Settings (brightness, volume, hostname, timezone, optional API token, alert sound on/off + quiet-hours schedule), Sounds (built-ins, upload/delete custom), WiFi (scan → pick → password → save → reboot), Bluetooth, System (usage, version, reboot, logs, plugin errors/quarantine).
- **Scan while streaming** briefly starves A2DP → UI warns "audio may stutter" (or defer until paused).

### 2.8 Security (matches Art Matrix)
- Web UI and API are **open on the LAN** by default, like Art Matrix.
- Optional **API token** (Settings): when set, `/api/v1/*` and the notify webhook require `Authorization: Bearer <token>`; the UI keeps working from the browser via a cookie set on entry.
- Setup AP is WPA2 with a default password shown on the display (§2.7).
- Plugin/index downloads use `CERT_NONE` (as Art Matrix). Secrets are write-only in the UI but stored unencrypted.
- Plugins are trusted code; the plugin repo is only changeable on the filesystem.

### 2.9 Bluetooth speaker & audio stability
- `bt_audio.init(name="GitMatrix")` at boot regardless of WiFi/plugin; link keys restored; pairable while unpaired; pairable toggle in menu/web.
- **Baseline audio in phase 1**; every later phase gated on measured `underruns()` + worst-case loop latency (idle, web browsing, index refresh, install, notification, scan, MQTT).
- Flash writes/GC: coalesced and **deferred while streaming** (max delay), installs staged in RAM and committed in one burst, `gc.collect()` between frames, allocation-free frame loop.
- AP disabled when STA connected; notification assets prefetched before sound starts.

### 2.10 Notifications
`POST /api/v1/notify` (open, or Bearer token if one is set):
```json
{
  "text": "Build failed on main",
  "image": "<base64 PNG>"  | "image_url": "https://…",
  "sound": "chime" | "sound_url": "https://…/x.wav" | "sound_data": "<base64 WAV ≤ 32 KB>",
  "plugin": "github_actions", "data": { … },
  "duration": 10, "repeat": 1, "priority": "normal|high", "ttl": 300,
  "color": "#ff0000", "font": "sins", "speed": 30, "id": "optional-dedupe-key"
}
```
- Limits checked **before** buffering/decoding: body ≤ 64 KB; PNG ≤ 32 KB and ≤ 2048×64 px decoded; inline sound ≤ 32 KB (larger via `sound_url` ≤ 256 KB, or uploaded once as a named custom sound); duration ≤ 60 s; repeat ≤ 5. Response `202 Accepted` + id; 400/413/429 errors.
- Bounded queue (8), priority with aging (no starvation), `ttl` expiry, `id` dedupe/replace.
- **Button**: first press during a notification dismisses it; the next press opens the menu. Notifications don't interrupt the open menu (they wait; high priority blinks a status LED).
- **Preemption**: interrupts plugins; rotation timer, current plugin and payload saved and restored on exit.
- **Image rules**: w>53 & h≤7 → horizontal scroll; w==53 & h>7 → vertical scroll; ≤53×7 → centred; both larger → **scale down to 7 rows** (nearest-neighbour, aspect kept), then apply the scroll rules (horizontal if still >53 wide).
- **Text + image together**: image ≤7 px wide = icon fixed at the left, text scrolls in the remaining 45-46 columns; otherwise image is shown first (with its scroll rules), then the text.
- **Plugin-forcing**: switch to any installed (even disabled, never quarantined) plugin, set `gitmatrix.notification = data`, call `on_notify(data)`, show for `duration`, then restore.
- **Sounds**: built-ins (confirmed) in `/sounds/` (`chime`, `ping`, `alert`, `success`, `error`, `doorbell`, `coin`), 16-bit mono 22.05 kHz WAV; playback is cancellable (dismiss).
- **Playback (decision: fallback)**: if A2DP is streaming, pause the sink (AVRCP pause if available, else stop feeding I2S / `bt_audio.deinit()` — spike 0c decides), play the WAV through `machine.I2S` (mono duplicated to both channels, since only left is audible), then resume. Nothing streaming → just play. If the spike shows A2DP can't be paused cleanly, sounds play only when idle and the notification shows silently.
- **Custom sounds**: upload/delete named WAVs via web UI and `POST/DELETE /api/v1/sounds/{name}` (≤256 KB each, 2 MB total), usable as `"sound": "<name>"`.
- **Alert sound control**: `Sound on/off` and **quiet hours** (start/end time, days) in web UI + device `Alerts` menu + API/MQTT. When muted, notifications still show but play no sound; a user option **"High priority bypasses quiet hours"** (web UI + API/MQTT, default off) lets `priority: high` notifications still play sound.

### 2.11 REST API v1 (Home Assistant & scripts)
- `GET /api/v1/status` — plugin, mode, wifi, bt (connected/streaming), volume, brightness, encoder (position, last event seq), uptime, firmware, mem/flash.
- `GET /api/v1/plugins`; `POST /api/v1/plugins/{name}/show` `{data, duration}`; `POST /api/v1/plugins/next|prev`; `POST /api/v1/plugins/{name}/enable|disable`; `GET/POST /api/v1/plugins/{name}/settings` (secrets write-only).
- `GET/POST /api/v1/brightness`, `/api/v1/volume`; `POST /api/v1/sound`; `POST/DELETE /api/v1/notify`.
- `GET /api/v1/events?since=<seq>` long-poll for encoder/button/plugin/notification events.
- Phase 6: **async** MQTT client (e.g. `mqtt_as`-style, not blocking `umqtt.simple`) with HA discovery: `light`, `number` (volume), `select` (plugin), `sensor` (encoder), `event` (button/rotation), `notify`, `button` (next/prev), `binary_sensor` (streaming).

## 3. Repos
- **This repo** (`martinwoodward/ghcontrib`, to be renamed `gitmatrix` and later made public) gets the firmware mirroring artmatrix, alongside the archived original firmware: `modules/python/{gitmatrix/,phew/,fetch.py,wifi.py,plugin_config.py,scrolling.py,timezones.py}`, `firmware/{main.py,lib/,html/,defaults/,sounds/,assets/}`, `ci/`, `tools/deploy.py` (mpremote sync). First releases = **filesystem bundle on the existing 1.29 UF2**; `board/` + UF2 CI later once Pimoroni sources are available.
- `martinwoodward/gitmatrix-plugins` (new, **public**): plugins + `ci/generate_packages.py` + manifest-check workflow.
- Original firmware stays in `firmware/` + `device/` as reference; new code under `gitmatrix/` (or top-level once renamed).

## 4. Phases (each gated on audio/latency measurements)
0. **Spikes**: (a) A2DP streaming + STA + AP + phew + scan + TLS fetch, measure underruns; (b) watchdog/POWMAN scratch registers survive WDT reset; (c) **sound fallback**: pause/resume A2DP sink (or deinit/init) and play WAV via `machine.I2S` on the speaker pins, measure resume time and reconnect behaviour; (d) flash-write stall length while streaming; (e) LittleFS rename atomicity assumptions.
1. **Core runtime + baseline BT**: boot, config, display host, plugin loader + fault containment, crash marker/safe mode, encoder events, snappy menu, BT speaker "GitMatrix", `audio`/`mic` API, port existing widgets (Graph, Clock, Stars…) + demos as built-in plugins.
2. **Networking + security**: wifi, AP provisioning + captive portal, scan/save/reboot, mDNS, WPA2 setup AP + fallback AP, optional API token, web UI skeleton.
3. **Plugin store**: repo from `/gitmatrix_config.py`, index cache, discover/search, transactional install/update/remove, settings schema + secrets, state API.
4. **Notifications**: API, queue, limits, text/PNG renderer + scroll/scale rules, plugin-forcing, sounds (pause-A2DP fallback), custom sounds, quiet hours.
5. **REST API v1 complete** (status, events, plugins, sound, brightness/volume).
6. **Home Assistant**: async MQTT + discovery.
7. **Docs**: porting guide, compatibility table, plugin template, examples.

## 5. Testing
- CPython host harness with stubbed picovector/`screen` for unit tests: loader fault containment, settings schema + secret redaction, notification validation/limits/scroll rules, install reconciliation, config migration.
- On-device smoke + soak via `mpremote`: boot, AP, install, notify, 1-hour streaming soak logging `underruns()`, power-cut-during-install.
- ruff with artmatrix config.
