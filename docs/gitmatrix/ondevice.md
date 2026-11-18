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
| Audio | "GitMatrix" A2DP sink, audio-reactive plugins, underruns, deinit/pause path, install-while-streaming, no flash writes while streaming | **pending**: held until the user gives the all clear for sound |

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

- **Hangs still use the watchdog.** A plugin hanging for 8 s causes a warm reset, which can hang
  the chip until it is power cycled. Phase 0 makes this unavoidable in hardware. Hangs should be
  rare, but if one happens the device may need a replug.
- `bt_audio.set_pairable` doesn't exist on 1.29: the pairable toggle is config-only.
- Rejoining after a soft reset sometimes needs a second or third attempt (up to ~50 s).
- The first index refresh straight after joining WiFi can time out once (it retries later).
- Plugin settings silently ignore unknown keys (the API returns 200).
- Still to run: audio checks, menu/encoder by hand, safe mode with the knob held, fallback AP,
  captive portal on phones, power cut mid-install, 30-minute streaming soak.
