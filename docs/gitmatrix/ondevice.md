# GitMatrix on-device verification

Results of running phases 1–3 on the real widget: Pimoroni 53×7 GitHub Contribution Widget,
RP2350, MicroPython 1.29 (`firmware/contribution_widget-micropython-1.29-with-filesystem.uf2`).
GitMatrix was deployed with `tools/deploy.py` (no `--reset`), then soft-reset into `main.py`.
The display was checked through a webcam, and the web UI and API from a Mac on the same LAN.

## 1. `machine.soft_reset()` reliability

Phase 0 found that **warm resets hang the whole chip about 1 in 3 times** until it is power
cycled. That covers `machine.reset()` (3/4 OK), a watchdog reset (1/2 OK) and `picotool reboot`.
So soft reset was measured as the replacement for `gitmatrix.reboot()`'s watchdog spin.

| Starting state | Cycles | Hangs | Notes |
|---|---|---|---|
| Nothing initialised | ~10 | 0 | USB CDC stays up; back at the REPL / `main.py` in ~2 s |
| After `bt_audio.init()` (not streaming) | ~10 | 0 | `bt_audio.init()` runs again cleanly afterwards in 129–135 ms |
| After WiFi STA up | ~8 | 0 | The CYW43 link **stays associated** across the soft reset (see below) |
| Bluetooth + STA up | ~8 | 0 | |
| GitMatrix running (phew sockets, STA, Bluetooth, asyncio), via Save & reboot / `POST /api/v1/system/reboot` | 5 | 0 | Back serving the web UI in ~9 s |

Over all of these:
- `machine.reset_cause()` stays at its power-on value.
- `machine.mem_backup()` (the crash marker and boot counter) survives.
- No replug was needed.

Not measured: a soft reset **while A2DP is streaming**.

Findings that shaped the code:
- **The watchdog survives a soft reset.** `_persist.reset()` stops it (the WATCHDOG_CTRL enable
  bit) before calling `machine.soft_reset()`. `boot()` arms it again.
- **WiFi interfaces survive a soft reset.** The STA stays connected with its old netif, so the
  new hostname wasn't registered with mDNS. `_wifi.start()` now deactivates any active STA/AP
  first, so a reconnect usually takes 3–17 s. Once it took 3 attempts, ~50 s.
- A hang still trips the 8 s watchdog, which is a warm reset and so can hang the chip until a
  power cycle. That's the remaining risk; see §4.

Conclusion: soft reset is reliable here. `gitmatrix.reboot()` now uses it (4ae00b8).

## 2. Checklist

| Area | Check | Result |
|---|---|---|
| Boot | `main.py` → `gitmatrix.run()` boots and shows a plugin | PASS (after the fixes below) |
| Boot | Safe mode from the boot counter (3+ failed boots) | PASS: "safe mode, 4 failed boots", no plugins loaded, web UI and WiFi up |
| Boot | Boot counter cleared after 60 s healthy | PASS |
| Boot | Safe mode with the knob held at power-on | not run (needs a person and a replug) |
| Boot | `boot()` failing every time stops instead of looping | PASS (fixed: it soft-reset forever) |
| Crash | Hook marker in `mem_backup()[0]` while a hook runs; cleared after | PASS (index, crc8 and hook all decode) |
| Crash | Marker left over at a **non**-watchdog boot is cleared, nothing quarantined | PASS |
| Crash | Watchdog reset → hung plugin quarantined | not run (needs a deliberate watchdog trip) |
| Crash | A hook that raises 5 times → quarantined, next plugin shown | PASS |
| Display | Blit clipping at x = -60…60 | PASS: partial columns correct, off-screen blits draw nothing and don't raise |
| Display | Frame time per plugin (16 ms budget) | PASS: averages 2.5–5.6 ms, max 11–15 ms. One 54 ms spike in spectrum (GC) |
| Display | Fire (viper) | PASS: 2.5 ms per frame |
| Display | Slide transitions | PASS: max 11–15 ms per frame |
| Display | Clock | PASS after the fix: the RTC was never set, so it showed `--:--` |
| Menu | Menu and encoder direction/detent | not run: needs a person at the knob |
| WiFi | Setup AP visible and WPA2 (`SEC_WPA2` exists) | PASS (seen at -41 dBm from the Mac) |
| WiFi | STA joins and the AP drops | PASS |
| WiFi | Fallback AP while the STA retries | not run |
| WiFi | mDNS `gitmatrix.local` | PASS after the fix |
| WiFi | Save & reboot joins the saved network (via soft reset) | PASS (the new hostname answers, the old one doesn't) |
| WiFi | Web rescan | PASS: 202 in 0.27 s; 3 networks after ~7 s |
| WiFi | Captive portal on phones | not run: the Mac must stay on the LAN |
| WiFi | `bt_audio.set_pairable` | missing on 1.29: `pairable_supported` is false, so the toggle only changes config |
| Web | All pages (`/`, settings, wifi, bluetooth, discover, plugin, system, sounds, notifications, static) | PASS: 200 in under 0.9 s from flash |
| Web | API (plugins, store, wifi, bluetooth, system, status, reboot) | PASS |
| Memory | Free RAM with everything loaded | ~7.9 MB. Flash: ~12.8 MB free of 13.6 MB |
| Store | `hashlib.sha1` and package hashes | PASS after the fix (no `hexdigest()` on 1.29) |
| Store | Index fetch from raw.githubusercontent.com (`CERT_NONE`) | PASS. One `ETIMEDOUT` on the first refresh right after joining; later ones fine |
| Store | Install the three examples from `gitmatrix-plugins` | PASS. On-device hashes match the index |
| Store | Run: matrix_rain, json_feed, github_contributions | PASS (camera: rain; feed scrolls a value; "Check token" without a token) |
| Store | Update (stale hash → update_available → install) | PASS. Settings are kept across the update |
| Store | Remove keeps data; reinstall | PASS |
| Store | Directory rename install/rollback on littlefs, `statvfs` | PASS |
| Store | Settings and secrets | PASS after the fix. Secrets show `set: true` and are never echoed; values are clamped; unknown keys are ignored |
| Store | `/config?plugin=` `config.html` and `process_config` | PASS |
| Store | Thumbnails from raw.githubusercontent.com | PASS: 53×7 PNGs |
| Store | Power cut mid-install | not run (replug budget) |
| Audio | "GitMatrix" A2DP sink, audio-reactive plugins, underruns, deinit/pause path, install-while-streaming, no flash writes while streaming | PASS in round 2 (§5) |

## 3. Bugs found and fixed

| Commit | Bug |
|---|---|
| 93e5d18 | MicroPython 1.29 rejects `*`/`**` unpacking in list/dict displays. Added a syntax guard test |
| 4ae00b8 | Reboot via `machine.soft_reset()` (watchdog stopped first) instead of a watchdog spin. `deploy.py --reset` soft-resets |
| 203da1c | `builtins.screen` raises AttributeError on 1.29: firmware globals are only reachable by bare name. Boot crashed in a loop |
| 7c55237 | When `boot()` itself kept failing, `run()` soft-reset forever. It now stops at the REPL |
| 66b640a | WiFi interfaces left up by a soft reset kept the old mDNS registration. `start()` now restarts them |
| 52c2f8e | 1.29's `hashlib` has no `hexdigest()`, so every install failed |
| 8a2ebd0 | The crash-marker hook table said `settings`, not `on_settings`, so saving settings raised `KeyError` |
| 48c5e9b | Nothing set the RTC, so the clock showed `--:--`. Added non-blocking NTP |

## 4. Open issues

- ~~Hangs still use the watchdog.~~ The soft-timer hang watch now stops a hung hook after 5 s
  and quarantines the plugin in place (verified on the device in round 2, §5). Only viper/native
  loops it can't interrupt still meet the 8 s watchdog (a warm reset, which may need a replug).
  That case was not run on purpose.
- ~~`bt_audio.set_pairable` doesn't exist on 1.29: the pairable toggle is config-only.~~ The toggle
  is now hidden and the API answers 501 where it is missing.
- Rejoining after a soft reset sometimes needs a second or third attempt (up to ~50 s). WiFi
  changes no longer reboot (they restart WiFi in place), so this now only follows System → Reboot.
- ~~The first index refresh straight after joining WiFi can time out once.~~ It now retries
  after 10 s (twice) before backing off to a minute.
- ~~Plugin settings silently ignore unknown keys.~~ Now 400.
- The full list of what still needs running on the device is [plan.md §5.0](plan.md#50-consolidated-on-device-checklist-everything-not-yet-verified-on-hardware).
- Still to run: menu/encoder by hand, safe mode with the knob held, the 10 s knob WiFi reset,
  button dismiss / second-press menu, fallback AP, captive portal on phones, power cut
  mid-install, the `INTERRUPT_MODE = "restart"` fallback, Home Assistant, a 1-hour soak.
- The spectrum plugin's bands cover ~86 Hz–10 kHz with even spacing. The user wants them
  focused on 60 Hz–16 kHz: one bass band (60–250 Hz), most bands in the midrange
  (250 Hz–4 kHz) and a few for 4–16 kHz. Kick detection would then need reworking. Being done
  separately.
- A steady tone or a sustained chord makes the spectrum look frozen (the bars hold still).
  That's expected rather than a stall: a frequency sweep moves the bars normally.

## 5. Round 2: integration build (reboot-free, phases 4–7)

The integration branch was deployed with `deploy.py`. Audio was kept quiet: device volume 15
and the Mac's source at 0.15. The Mac streamed A2DP straight to the "GitMatrix" output without
changing its default output.

| Area | Check | Result |
|---|---|---|
| Boot | Phase 4–7 modules import on 1.29; `/status` fields all filled in | PASS |
| Hang watch | `micropython.schedule(kbd_intr)` accepted ("hang watch on" in the log) | PASS |
| Hang watch | Busy loop in `update`, `time.sleep(30)` in `update`, a hang in `on_show`, `on_notify`, `on_settings` and `process_config` | PASS: each is stopped after 5 s, "…hung and was stopped" scrolls, the plugin is quarantined, the rotation continues and there's no reset. `/config` save answers 400 "hung in process_config" |
| Hang watch | Viper busy loop meets the 8 s watchdog | not run (a warm reset can hang the chip) |
| Sounds | All 7 built-ins play | PASS after the fix (all failed with `NameError: ptr16`) |
| Sounds | Upload, play, delete a custom WAV; delete while it plays | PASS after the fix (deleting the playing sound hung the chip and cost a replug) |
| Sounds | Upload while streaming → queued, written after playback stops | PASS |
| Sounds | `POST /sound` results: `off`, `quiet`, `streaming`, `interrupt`, `busy`, `force` | PASS |
| Bluetooth | Mac connects to "GitMatrix", streams; spectrum reacts | PASS (`underruns` now in `GET /bluetooth`) |
| Bluetooth | Notification while streaming: silent by default (`last_result` `streaming`) | PASS |
| Bluetooth | High priority with "play over Bluetooth audio" on | PASS, 18/18: the sound plays, the stream comes back by itself, 0 underruns |
| Bluetooth | Remove/install/settings while streaming: 409 without `pause_audio`; `queue: true` install downloads, then writes once playback stops | PASS |
| Bluetooth | `pause_audio: true`: sink dropped, write ~0.3 s, back on, Mac reconnects with `blueutil --connect`; volume kept | PASS |
| Bluetooth | 30-minute soak while changing brightness, plugins, alerts, token, quiet hours and MQTT settings | PASS: 31 min, 0 underruns, 0 pauses, free RAM flat (~7.7 MB). Changes were saved after playback stopped and survived a restart |
| Notifications | PNGs: 20×5 centred, 200×7 scrolls sideways, 53×30 scrolls up, 300×60 scaled, 7×7 icon + text, 2048×64 with no RAM change, 2049 wide → clean error | PASS (camera) |
| Notifications | 70 KB body → 413; failed `image_url` (404, server down) drops the note with `last_error` | PASS |
| Notifications | Forced plugin show with `duration` and `data`, then back to the rotation; duration 61 → 400 | PASS |
| Notifications | Quiet hours around the current time: normal is quiet, `force` and outside the window are not; high is quiet with the bypass off | PASS |
| REST | `/events` long-polls: 3rd concurrent → 429, other requests still served; 500 polls with no leak | PASS |
| REST | Token: 401 without/wrong, 200 right; HttpOnly SameSite=Strict cookie; clearing works | PASS |
| REST | All 9 pages 200; titles capitalised | PASS |
| MQTT | Local mosquitto: HA discovery and retained state, every command and its result, wrong password backoff, retained `plugin/set` ignored on reconnect, base topic change, disable | PASS |
| MQTT | Home Assistant itself | not run |
| WiFi | Re-save the same network with a new hostname: rejoins in place in ~1 s; new name answers, old one gone once the Mac's cache expires; reverted | PASS |
| WiFi | Forget: setup AP "GitMatrix" back in place (seen from the Mac); credentials restored over USB and rejoined | PASS |
| Menu | Encoder, menu, knob holds, button dismiss | waiting for a person at the knob |

Bugs fixed in round 2:

| Commit | Bug |
|---|---|
| 364cd82 | `viper = micropython.viper` then `@viper` compiles as plain Python on 1.29, so every sound failed with `NameError: ptr16`. Uses the literal decorator; syntax guard added |
| d485d1c | `GET /bluetooth` didn't report underruns |
| 7b1573b | The shipped default volume was too loud; now 25% (was 50%) |
| 9626514 | Deleting a custom sound while it played removed the file under `play()` and hung the chip. Now 409 |
| 4637aa4 | `/config` ran `process_config` (and the plugin import) outside the hang watch, so a hang there would trip the watchdog |
