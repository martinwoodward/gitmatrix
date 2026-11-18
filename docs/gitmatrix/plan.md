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
- **Watchdog** 8 s, fed by `draw()`; host blocking operations (DNS, NTP, flash, scans) have bounded timeouts and feed it before/after (WiFi scans also keep it fed mid-call with a soft `machine.Timer`).
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
- **Transactional install**: free-space check → download + verify into RAM (staged to `/.install/<name>@<hash>` if large) → swap into `/lib/plugins/<name>/` (stable path, see §6) → atomically update `/lib/plugins/active.json`. Boot reconciliation removes incomplete versions and falls back to last-known-good. User files live in `/data/<name>` not the plugin dir.

### 2.7 Networking, provisioning & web UI
- `wifi.py` + phew (on FS), mDNS hostname `gitmatrix` (configurable).
- No WiFi configured → AP `GitMatrix` at 192.168.4.1 + captive DNS. AP is **WPA2** with a default password **`monalisa`** (user asked for `mona`; WPA2 requires 8–63 chars; changeable in `/gitmatrix_config.py`), shown on the display and in the menu's WiFi screen. AP turned off once STA connects. If the configured network fails for **1 min**, the AP starts as well while STA keeps retrying; it stops once STA connects.
- Pages: Home (now showing, reorder/enable/disable, delay, Show now), Discover, Plugin settings, Notifications (test sender), Settings (brightness, volume, hostname, timezone, optional API token, alert sound on/off + quiet-hours schedule), Sounds (built-ins, upload/delete custom), WiFi (scan → pick → password → save → reboot), Bluetooth, System (usage, version, reboot, logs, plugin errors/quarantine).
- **Scan while streaming** briefly starves A2DP → UI warns "audio may stutter" (or defer until paused).
- **Implemented (phase 2)**: `gitmatrix._wifi` (state machine ticked every 500 ms from the asyncio loop; `WLAN.scan()` can't yield (one C call, up to 10 s, no async/per-channel form; core1 belongs to `bt_audio` so no thread), so it's isolated: setup mode scans once at boot before the event loop starts, and web rescans are explicit, rate-limited to one per 15 s (429 otherwise) and keep the watchdog fed via a soft Timer while Python tasks pause; `wifi.py` is an Art Matrix-compatible shim over it), `gitmatrix._web` (phew routes, JSON API under `/api/v1/`, static pages from `/html`, captive 302 to `/wifi.html` in AP mode), `gitmatrix._log` (RAM ring, never written to flash). Saving WiFi schedules a reboot (`gitmatrix.reboot()`, a `machine.soft_reset()` with the watchdog stopped; see `ondevice.md`), never `machine.reset()`. Discover, Plugin settings, Notifications and Sounds pages are placeholders. Not yet done: *hold 10 s → WiFi reset* (§2.2); use Forget network in the UI, or clear `wifi_ssid` in `/config.py`.

### 2.8 Security (matches Art Matrix)
- Web UI and API are **open on the LAN** by default, like Art Matrix.
- Optional **API token** (Settings): when set, `/api/v1/*` and the notify webhook require `Authorization: Bearer <token>`; the UI keeps working from the browser via a cookie set on entry (`gm_token`, `HttpOnly; SameSite=Strict`, set when the device serves an HTML page; state-changing cookie requests must have a matching `Origin`). This stops cross-site requests and casual scripts, not someone already browsing the LAN UI.
- Setup AP is WPA2 with a default password shown on the display (§2.7).
- Plugin/index downloads use `CERT_NONE` (as Art Matrix). Secrets are write-only in the UI but stored unencrypted.
- Plugins are trusted code; the plugin repo is only changeable on the filesystem.

### 2.9 Bluetooth speaker & audio stability
- `bt_audio.init(name="GitMatrix")` at boot regardless of WiFi/plugin; link keys restored; pairable while unpaired; pairable toggle in menu/web.
- **Baseline audio in phase 1**; every later phase gated on measured `underruns()` + worst-case loop latency (idle, web browsing, index refresh, install, notification, scan, MQTT).
- Flash writes/GC: coalesced; **no flash writes while streaming** (§6.1: background writes wait, user writes pause the sink), installs staged in RAM and committed in one burst, `gc.collect()` between frames, allocation-free frame loop.
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
- [`martinwoodward/gitmatrix-plugins`](https://github.com/martinwoodward/gitmatrix-plugins) (**public**, created in phase 3): plugins + `ci/generate_packages.py` (Art Matrix's generator plus GitMatrix details and schema/thumbnail validation) + a workflow that validates on PRs and regenerates `index.json` on main. Examples: `github_contributions` (schema with a secret token), `json_feed` (url/secret/color), `matrix_rain` (Art Matrix-style `config.html`).
- Original firmware stays in `firmware/` + `device/` as reference; new code under `gitmatrix/` (or top-level once renamed).

## 4. Phases (each gated on audio/latency measurements)
0. **Spikes**: (a) A2DP streaming + STA + AP + phew + scan + TLS fetch, measure underruns; (b) watchdog/POWMAN scratch registers survive WDT reset; (c) **sound fallback**: pause/resume A2DP sink (or deinit/init) and play WAV via `machine.I2S` on the speaker pins, measure resume time and reconnect behaviour; (d) flash-write stall length while streaming; (e) LittleFS rename atomicity assumptions.
1. **Core runtime + baseline BT** ✅: boot, config, display host, plugin loader + fault containment, crash marker/safe mode, encoder events, snappy menu, BT speaker "GitMatrix", `audio`/`mic` API, port existing widgets (Graph, Clock, Stars…) + demos as built-in plugins.
2. **Networking + security** ✅ (host-tested; on-device checks below): wifi, AP provisioning + captive portal, scan/save/reboot, mDNS, WPA2 setup AP + fallback AP, optional API token, web UI skeleton.
3. **Plugin store** ✅ (host-tested; on-device checks in §5.2, design as built in §6): repo from `/gitmatrix_config.py`, index cache, discover/search, transactional install/update/remove, settings schema + secrets, state API, single flash writer (no writes while A2DP streams).
4. **Notifications**: API, queue, limits, text/PNG renderer + scroll/scale rules, plugin-forcing, sounds (pause-A2DP fallback), custom sounds, quiet hours.
5. **REST API v1 complete** (status, events, plugins, sound, brightness/volume).
6. **Home Assistant**: async MQTT + discovery.
7. **Docs**: porting guide, compatibility table, plugin template, examples.

## 5. Testing
- CPython host harness with stubbed picovector/`screen` for unit tests: loader fault containment, settings schema + secret redaction, notification validation/limits/scroll rules, install reconciliation, config migration.
- On-device smoke + soak via `mpremote`: boot, AP, install, notify, 1-hour streaming soak logging `underruns()`, power-cut-during-install.
- ruff with artmatrix config.

### 5.1 Phase 2 on-device checklist (run on device; results in `ondevice.md`)
- [x] `network.WLAN.SEC_WPA2` exists on 1.29 and the setup AP really is WPA2 with `monalisa`.
- [~] STA + AP coexist (fallback AP while STA retries), and the AP drops once STA connects. *(AP drops on join: yes. Fallback AP after a failed join not run.)*
- [x] `network.hostname()` set before `active(True)` makes `gitmatrix.local` resolve (mDNS).
- [x] `asyncio.start_server` works on 1.29, and tasks created in `boot()` (captive DNS) survive `asyncio.run`; the DNS socket survives AP restarts.
- [ ] *(not run: needs a phone on the setup AP)* Captive portal opens the WiFi page on iOS and Android.
- [~] *(web rescan is async: 202 in 0.27 s, 3 networks in ~7 s; stall under A2DP not run)* Scan duration and the display/web stall during a web rescan (expect ~1-3 s); audio underruns while A2DP streams; same for page loads.
- [ ] *(not run: needs a deliberate watchdog trip, which can hang the chip)* Soft `machine.Timer(-1)` callbacks run inside `WLAN.scan()`'s wait so the watchdog stays fed (try a long passive scan with a short WDT).
- [ ] *(not checked: the AP was seen, but the first-load list wasn't inspected)* The boot pre-scan in setup mode with the AP already up: networks are listed on first page load and the AP stays up.
- [x] Save & reboot → reboot (now a soft reset) → joins the new network.
- [x] *(it doesn't: `pairable_supported` is false)* `bt_audio.set_pairable` exists (else the toggle only changes config).
- [x] Free RAM with phew loaded, and page load speed from flash.

### 5.2 Phase 3 on-device checklist (run on device; results in `ondevice.md`)
- [x] `hashlib.sha1` exists on 1.29 (package hashes; without it installs fail with "no sha1").
- [x] `fetch.AsyncFetch` with `CERT_NONE` reaches raw.githubusercontent.com; index refresh doesn't visibly stall the display or cause underruns.
- [x] `os.rename` of a **directory** works between `/lib/plugins` and `/.install` on littlefs (the swap and the rollback both rely on it).
- [x] `os.statvfs("/")` gives a sensible free figure (the free-space check and the Discover page).
- [ ] *(audio tests pending)* `bt_audio.deinit()` exists; the pause path (`link_keys()` → `deinit()` → write → `load_link_keys()` → `init(name="GitMatrix")`) keeps the pairing, restores volume and pairable, and the phone/Mac can reconnect by hand. Check `load_link_keys(b"")` after Forget is accepted.
- [ ] *(audio tests pending)* Install, settings save, `/config` save and Remove while streaming: 409 without `pause_audio`, no hang with it; a queued install runs once playback stops.
- [ ] *(audio tests pending)* No flash write happens while streaming (state, config, link keys, index cache all wait): stream for 30+ minutes while changing brightness and switching plugins, then check they're saved after playback stops.
- [ ] *(not run: replug budget)* Power cut mid-install (pull USB during "writing to flash") → boot reconcile restores the old version or removes a fresh one; `/.install` empty afterwards.
- [~] *(the examples, a few KB each, install from the RAM stage; the flash stage isn't run)* Memory: a ~40 KB plugin installs from the RAM stage; a larger one stages on flash with the audio paused.
- [x] Thumbnails (53×7 PNG) load in the browser straight from raw.githubusercontent.com.
- [x] A ported Art Matrix plugin's `config.html` renders at `/config?plugin=<name>` and `process_config(form)` is called on save.
- [x] The three example plugins from `martinwoodward/gitmatrix-plugins` install, run and keep their settings across an update.

## 6. Phase 3 (plugin store) as built

Host-tested (`gitmatrix/tests/test_store.py`, `test_flash.py`); on-device checks in §5.2.

- **Repo**: `PLUGIN_REPO` / `PLUGIN_BRANCH` from `/gitmatrix_config.py` only (default `martinwoodward/gitmatrix-plugins@main`; invalid values fall back to the default). Not in the web UI or API.
- **Index**: `index.json` (Art Matrix format plus GitMatrix detail fields) fetched with `fetch.AsyncFetch` (`CERT_NONE`, as Art Matrix) every 15 min (retry after 1 min on failure, 15 s timeout, 64 KB cap), cached in `/index-cache.json` (`{repo, index}`, ignored if the repo changes). Discover filters client-side; thumbnails load from raw.githubusercontent.com.
- **Install/update** (`gitmatrix/_store.py`): fetch `package.json`, validate (`urls` must stay inside the plugin, `min_firmware`), check free space (size + 16 KB margin), download every file and verify the package hash (sha1 over sorted `path\0bytes`, 12 hex chars, the same as `ci/generate_packages.py`). Packages up to 48 KB stay in RAM; larger ones stage on flash in `/.install/<name>@<hash>`. Commit, with no awaits: journal → move the old copy to `/.install/<name>.old` → rename the staged copy to `/lib/plugins/<name>` → carry over files the user/plugin added (Art Matrix `config.json` etc.) → write `/lib/plugins/active.json` → delete the journal. If an update fails to import, the old copy is put back.
- **Deviation**: plugins live at the stable `/lib/plugins/<name>/` path, not `<name>@<ver>/`, because Art Matrix plugins hardcode their path; `active.json` holds `{name: {version, hash}}` and the old copy is kept until the new one is in place.
- **Boot reconcile** (before Bluetooth starts): a journal with a complete new copy → finish; else restore `<name>.old`; else remove the half-installed plugin. Drops `active.json` entries without a complete folder and empties `/.install`.
- **Remove**: unload, delete the folder and its `active.json` entry; `keep_data` (default true) keeps `/data/<name>` and `/secrets/<name>.json`.
- **Built-ins** (in `/lib/plugins` but not in `active.json`) keep working; the store refuses to install over or remove them (409).
- **Settings**: `package.json` `settings` schema (§2.5, plus `int`; `number` takes min/max/step) rendered by `plugin.html`; values in `/data/<name>/settings.json`, secrets in `/secrets/<name>.json`, never returned (the API sends `set: true/false`); "" clears a secret, omitting it keeps it. A plugin's own `config.html` is served at `/config?plugin=<name>` with `{{ plugin }}`/`{{ placeholder.X }}` and `process_config(form)` (else saved to `/data/<name>/config.json`, which `plugin_config` reads).
- **API**: `GET /api/v1/store` (catalogue: index + installed/update_available/builtin/compatible, repo, base_url, age, error, online, free, install progress); `POST /api/v1/store/refresh` (202, 429 rate-limited, 409 offline); `GET/POST /api/v1/store/install` (`{name, pause_audio?, queue?}` → 202 + progress; GET polls it); `POST /api/v1/plugins/{name}/remove` (`{keep_data, pause_audio?}`); `GET/POST /api/v1/plugins/{name}/settings` (`pause_audio?`); `/api/v1/plugins` items gain `version`, `installed`, `builtin`, `update_available`, `has_settings`, `has_config_html`.
- **Web UI**: Discover (search, category filter, cards with thumbnail/description/version/size/tags, Install/Update/Installed/Built in, progress), `plugin.html?name=` (details, auto settings form with secret "•••• set"/Clear, `config.html` link, Remove with keep-data), update badges on Home.

### 6.1 Flash writes and Bluetooth audio (phase 0 finding)

A flash write while A2DP streams can hang the board (a 64 KB write hung it 2/2 runs; 1 KB ≈ 65 ms, 8 KB ≈ 170 ms, 64 KB ≈ 1 s; a rename over a file is atomic, ≈ 4 ms). Writes are fine when not streaming and after `bt_audio.deinit()`, but the Mac doesn't reconnect by itself afterwards. So: **no flash writes while `bt_audio.streaming()`**.

- `gitmatrix/_flash.py` is the single gate. `_compat.write_atomic`/`makedirs`/`remove_tree` call `_flash.check()`, which raises `FlashBusy` while streaming, so anything that slips past the rules fails instead of hanging.
- **Background writes wait** (no max-defer): plugin state/settings flush, `config.py` saves (housekeeping, end of boot, `/api/v1/settings`), Bluetooth link keys, the index cache. Housekeeping writes them once playback stops.
- **User writes pause the sink**: `with _flash.pause():` reads link keys/volume, `bt_audio.deinit()`, writes in one burst, then `load_link_keys()` → `init(name="GitMatrix")` → volume → pairable (`on_resume`). Nested pauses share one deinit.
  - Install, Remove, plugin settings and `/config` saves answer 409 `{"error": "Bluetooth audio will disconnect; reconnect your device", "audio_streaming": true}` unless the request has `pause_audio` (or `queue` for installs, which downloads first and waits for playback to stop before writing). The UI asks: OK = stop the audio now, Cancel = wait (installs only).
  - WiFi save/forget, Bluetooth Forget, `gitmatrix.reboot()`, shutdown and the crash path pause implicitly (reboot/shutdown/crash without resuming); WiFi save/forget replies carry a `warning`.
- `tools/deploy.py` writes files over USB: stop playback first.
