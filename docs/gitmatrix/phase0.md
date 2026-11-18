# GitMatrix Phase 0 spikes (interim report, 2026-11-18)

Status: **spike 1 done; spikes 2-5 not started**. The device stopped enumerating during reset
testing and has been **recovered without reflashing** (FS, `main.py`, `/bt_link_keys.bin` intact).
Reset experiments are paused until the coordinator approves more.

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
