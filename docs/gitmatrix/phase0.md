# GitMatrix Phase 0 spikes (interim report, 2026-11-18)

Status: spikes 1, 2 (a/b/d), 3, 5 done; **spike 4 found a hard hang**: a large flash write
while A2DP is streaming locks the device. Warm-reset characterisation and the 2c/flash-threshold
follow-ups are pending. **Pending (device hung, waiting for a replug):** 16/32 KB threshold test,
I2S-while-streaming, warm-reset harness, and the final device cleanup (the hung run may have left
`/wifi_tmp.json` and `/_*.tmp` on the device).

## Key findings / rules for the plan

1. **Never write flash while `bt_audio.streaming()`.** A 64 KB write hung the whole device
   (no USB, needs a power cycle) in 2 of 2 attempts. Writes are safe after `bt_audio.deinit()`.
   Interim rule: defer flash writes (state, config, WiFi save, plugin install) until not streaming,
   or pause the sink (`deinit` -> write -> `init`) and accept that the source has to reconnect.
2. Notification sound: `machine.I2S` + AMP_EN (GP11) works, including while `bt_audio` is
   initialised but idle. While streaming it's untested.
3. WiFi + A2DP coexist. Only the STA association itself caused underruns (73 during an ~8.7 s
   connect). Scan, HTTP, TLS, an asyncio server and AP/STA+AP caused none.
4. `os.rename` over an existing file is atomic enough for temp+rename saves.
5. Crash marker: `machine.mem_backup()` survives soft and WDT resets and is cleared on power-up.

> **Confounder:** BambuStudio (3D-printer slicer) was running on the Mac throughout and probes/opens
> new serial ports. That matches the `failed to access ... in use by another program` errors and
> may explain some or all of the "hangs". After quitting it, a plain replug enumerated in 0.6 s and
> `bt_audio.init()` + 30 s of BT running with an active REPL was fine. **All hang numbers below were
> measured with BambuStudio running and must be re-measured before drawing conclusions.**

## Spike 1: scratch registers (crash marker + boot-loop counter)

Script: `gitmatrix/spikes/scratch_regs.py`.

| Storage | Soft reset (Ctrl-D) | WDT timeout reset | Power cycle |
|---|---|---|---|
| Watchdog SCRATCH0-3, 5-7 (`0x400D800C..`) | kept | kept | **cleared** |
| Watchdog SCRATCH4 | kept (SDK/ROM reboot magic; written by the SDK as `0x6ab73121` on WDT reset, never use) | overwritten | cleared |
| `machine.mem_backup()` | same as watchdog SCRATCH0-3 (it's a 4-byte-wide memoryview at `0x400D800C`; `mem_backup(1)` = 3 words at `0x400D8020`, i.e. SCRATCH5-7) | kept | cleared |
| POWMAN SCRATCH0-7 (`0x401000B0..`, write `0x5AFE<<16 \| value`, so only 16 bits each) | kept | kept | **cleared** |

- `machine.reset_cause()`: 1 = power-on/soft reset, 3 = WDT/`machine.reset()`.
- Recommendation: crash marker in `mem_backup()[0]` / `[1]` (full 32-bit) or POWMAN SCRATCH0-3 (16-bit).
  Avoid WD SCRATCH4-7 and POWMAN BOOT0-3 (RP2350 bootrom vectored-boot inputs) and WD SCRATCH2-3
  (bootrom params for `reboot` API). On cold boot all are zero, which matches "no crash recorded".

## Spike 2: notification sound via `machine.I2S`

Scripts: `gitmatrix/spikes/i2s_tone.py`, `bt_i2s_handoff.py`. 1 kHz sine, 22.05 kHz 16-bit stereo,
`I2S(0, sck=9, ws=10, sd=8)`, AMP_EN GP11 high while playing. Measured with the Mac mic (16 kHz, mono).

| Case | Result | Mic RMS (idle ~5) |
|---|---|---|
| 2a: `bt_audio` never initialised | plays; `I2S()` open 1 ms | ~1200 |
| 2b: after `bt_audio.init()` (135 ms) then `deinit()` (1 ms) | plays; `init()` again afterwards 135 ms | ~650 |
| 2d: `bt_audio` initialised, no source connected | `I2S(0)` and `I2S(1)` both open; tone plays; `underruns` 0 | ~1400 |
| 2d: while A2DP streaming | **not tested** (the streaming run hung earlier in spike 4) | - |
| 2c: re-init then source reconnect | macOS did **not** reconnect by itself; the user reconnected manually (~31 s, then streaming in 0.6 s). Re-init took 135 ms normally, 1134 ms once | - |

Viable approach: if not streaming, play directly via `machine.I2S` (no need to deinit `bt_audio`).
If streaming, `deinit()` -> play -> `init()` works on the device side, but the A2DP source may not
come back without user action (the Mac didn't; phones untested). The plan should either mix the
chime into the A2DP path (needs firmware support) or skip/defer chimes while streaming.

## Spike 3: A2DP + WiFi coexistence

Scripts: `gitmatrix/spikes/coexist.py` (device) + `host_coexist.py` (Mac streams a 440 Hz WAV
via `afplay`, hammers the device server). 44.1 kHz SBC, ring 1024 frames, `ring_level` sampled
every 10 ms.

| Phase | Duration | Underruns | Min ring level |
|---|---|---|---|
| idle, streaming | 8 s | 0 | 0 (ring still filling at start) |
| STA connect (DHCP) | 9.2 s (assoc 8.7 s) | **+73** | 0 |
| STA idle | 8 s | 0 | 512 |
| `scan()` (4 nets) | 0.7 s | 0 | 512 |
| HTTP GET x3 | 0.3 s (70-135 ms each) | 0 | 768 |
| TLS GET x3 (`CERT_NONE`) | 2.0 s (~680 ms each) | 0 | 768 |
| asyncio HTTP server, 64 requests in 10 s | 10 s | 0* | 512 |
| AP up (STA+AP) / STA+AP idle / STA off / AP idle / AP off | 0.2-8 s | 0 | 1024 |
| WiFi off idle | 8 s | 0 | 768 |

\* `underruns()` went down by 73 in this phase, so the counter appears to reset (likely at a
stream restart). Treat `ring_level` as the reliable signal. Without streaming: TLS fetch ~490 ms,
HTTP ~50 ms, server 86 hits/10 s.

Recommendation: connect STA before starting audio, or accept a ~1 s glitch on (re)connect.
Everything else can run while streaming.

## Spike 4: flash writes (stall and hang)

Scripts: `gitmatrix/spikes/fs_spike.py`, `flash_stream.py` + `flash_host.py`.

Timing without BT (5 reps each): 1 KB write avg 65 ms (max 110), 8 KB 168 ms, 64 KB 1.01 s;
`rename` 4 ms avg (one 72 ms outlier).

| bt_audio state | Writes tried | Result |
|---|---|---|
| not initialised | 1/8/64 KB x5 + rename | OK |
| initialised, no source (2 runs) | 1/8/64 KB + rename | OK, 0 underruns |
| connected, not streaming | create empty, 4 KB, 64 KB | OK (64 KB 1.02 s) |
| **streaming** | create empty 3 ms, 256 B 4 ms, 4 KB 131 ms, rename 4 ms, 8 KB 181 ms | OK, 0 underruns |
| **streaming** | **64 KB** | **device hung: no REPL, no USB; replug needed (2/2)** |
| streaming, then `bt_audio.deinit()` (0 ms) first | 64 KB 1.06 s, rename 3 ms, `init` 1.1 s | OK |

The first hang happened during a 1 KB -> 8 KB -> 64 KB sequence, the second at 64 KB after smaller
writes succeeded. Single samples for 4-8 KB mean "small writes are safe" is **not proven**, and the
size threshold (16/32 KB?) is unknown. Likely cause: a long flash program/erase sequence locks out
core 1 (I2S/SBC) or XIP for too long. That's a firmware-level issue to raise with Pimoroni.

## Spike 5: LittleFS rename

Writing `/_cfg.tmp`, then `os.rename('/_cfg.tmp', '/_cfg.json')` over an existing file: the new
contents are visible, the temp file is gone, 3.4 ms. Temp+rename is fine for config saves (power-loss
atomicity is not tested).

## Warm reset is unreliable on this firmware (plan risk)

| Reset method | Attempts | Came back on USB |
|---|---|---|
| `machine.reset()` with scratch untouched | 1 | 1 |
| `machine.reset()` after writing WD/POWMAN scratch or `mem_backup` | 3 | **0** (needed replug) |
| `machine.reset()` after writing then clearing `mem_backup` | 1 | **0** |
| WDT timeout (`machine.WDT(timeout=300..1000)` + busy loop) | 4 | 3 (4th hung) |

- A hang = USB never enumerates again until a physical replug. macOS logs
  `createDevice: failed to create device (0xe00002bc)` repeatedly, then
  `persistent enumeration failures` and stops trying on that port until a cable change.
- `main.py` on the device is the stock `import bt_audio_visualiser`. The display shows black when
  nothing is streaming, so "blank display" alone does not mean hung.
- **After the last WDT hang, the device stopped cold-booting reliably too.** It still enters BOOTSEL.
  The firmware + ROMFS regions are byte-identical to `firmware/contribution_widget-micropython-1.29-with-filesystem.uf2`
  (checked from a full `picotool save -a` backup, kept in the session folder, not in git).
  During button-assisted replugs MicroPython did enumerate twice ("Board in FS mode"), so cold
  boot works sometimes.
- Recovery: BOOTSEL + `picotool save -a` backup; `picotool reboot` did not help; after quitting
  BambuStudio a plain replug booted normally. Reflash was **not** needed.

### Device changes before it stopped booting (complete list)
1. Copied `/scratch_regs.py` (1 KB) to the device FS.
2. Wrote watchdog SCRATCH0-3,5-7, POWMAN SCRATCH0-7 and `mem_backup()[0..1]` (RAM-only registers; a power cycle clears them, verified).
3. ~9 `machine.reset()` / WDT resets, 4 of which needed replugs.
No other files were written; `main.py`, `/bt_link_keys.bin` untouched.

### Consequence for the plan (§2.3)
Until a warm reset is proven reliable, watchdog-based recovery can leave the widget hung until a
manual power cycle. Options: (a) find a reset recipe that's 100% reliable (e.g. deinit CYW43/BT first,
reset oscillators via PSM WDSEL, close USB CDC first), then use it; (b) treat WDT as last resort
and show "power cycle me" on screen in safe mode; (c) ask Pimoroni to fix warm boot in the board firmware.
`gitmatrix/spikes/wdt_harness.py` is a ready-made, self-driving harness for (a) (not yet run).

## Recommendations (from results so far)

Pending tests may refine these; they won't loosen rule 1.

1. **Single flash writer, gated on streaming.** Every flash write (state saves, config, WiFi
   credentials, plugin install, link keys) goes through one writer task:
   - not streaming: write now, using temp + `os.rename`;
   - streaming: queue it and flush when `bt_audio.streaming()` goes false. Use a coalescing
     queue for state (last-write-wins), and debounce saves of 5 s or more;
   - user-initiated writes that can't wait (plugin install, WiFi save from the setup portal):
     pause the sink (`bt_audio.deinit()` -> write -> `init()`, about 0.1-1.1 s) and show
     "audio paused". The source may have to reconnect manually.
   Enforce this in the runtime, not by convention. `open(..., 'w')` outside the writer should
   be a lint/review failure.
2. **Notification sounds.** When not streaming, play directly with `machine.I2S(0, sck=9, ws=10, sd=8)`
   plus AMP_EN GP11. No `bt_audio` deinit is needed; open takes about 1 ms. Use the left channel
   only, or put the same samples in both channels. While streaming, skip or defer the chime and
   flash the display instead, unless the pending I2S-while-streaming test shows a clean mix.
   Ask Pimoroni for a `bt_audio` PCM-mix/"play" API.
3. **Crash marker / boot-loop counter.** Use `machine.mem_backup()[0]` (marker) and `[1]`
   (counter): they're 32-bit, survive soft and WDT resets and are zero on power-up. Never touch
   WD SCRATCH2-7 or POWMAN BOOT0-3. If a 16-bit spare is needed, POWMAN SCRATCH0-3 is an
   alternative.
4. **Reset policy.** Don't rely on WDT/`machine.reset()` coming back until the harness has
   re-measured it without BambuStudio. Until then:
   - prefer a soft reset (`machine.soft_reset()`) for "restart the app" paths;
   - keep the WDT as a last resort, with a generous timeout (8 s);
   - on a boot-loop or crash marker, enter safe mode with "unplug & replug" on screen.
5. **WiFi.** Connect STA before starting `bt_audio` where possible, or accept a short dropout
   during association (about 70 underruns over 8 s). Scans, HTTP/TLS fetches and the
   setup-portal AP are safe while streaming. Monitor `ring_level()`, not `underruns()`,
   which resets.
6. **Raise with Pimoroni:**
   - flash-write hang while A2DP streams (64 KB; likely core 1/XIP starvation during
     erase/program);
   - unreliable warm boot;
   - no PCM playback/mix API in `bt_audio`.
7. **Dev hygiene.** Close BambuStudio (and anything else that probes serial ports) when working
   with the widget. It caused "port in use" errors and is a suspect for the enumeration
   failures.

## Resume here (paused for app restart)

State at pause:
- Widget probably hung from the last 64 KB-while-streaming test and needs one replug.
- Device FS was clean before that run. The hung write may have left `/_a.tmp`-`/_c.tmp`.
- Mac output is "MacBook Pro Speakers".
- After the restart, the app should have Bluetooth permission, so use `blueutil --connect 28-cd-c1-19-6a-19` instead of asking the user to reconnect.

Remaining steps, in order:
1. Replug the widget (1 replug). Remove `/_*.tmp` and any `/wifi_tmp.json`; confirm `main.py` and `/bt_link_keys.bin` are intact.
2. Threshold test (informational only): 16 KB and 32 KB writes while streaming. Use `flash_stream.py`, adding 16k/32k steps before 64k.
3. I2S playback while A2DP is streaming: does it glitch, hang, or mix? Record with the mic.
4. Warm-reset harness (`gitmatrix/spikes/wdt_harness.py`), approved with a 2-replug budget and BambuStudio closed:
   - Back up `/main.py` and install the harness as `/main.py`.
   - Run it, then restore the stock `/main.py` (`import bt_audio_visualiser`).
5. Final device cleanup (temp files, WiFi creds, harness state `/wdt_state.json`). Restore the Mac speakers.
6. Add a Recommendations section here, commit, and send the final summary to the coordinator.
