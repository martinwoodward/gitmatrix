# Phase 0 spike: which scratch storage survives soft reset / WDT reset / machine.reset / power cycle.
# Usage (mpremote run): set MODE below via the first line rewrite, or call from REPL:
#   import scratch_regs; scratch_regs.write(tag); then trigger reset; then scratch_regs.read()
import machine

WD = 0x400D8000          # watchdog: SCRATCH0..7 at +0x0C..+0x28
PM = 0x40100000          # POWMAN:   SCRATCH0..7 at +0xB0..+0xCC (writes need 0x5AFE in [31:16])
PM_PW = 0x5AFE0000


def write(tag):
    for i in range(8):
        if i != 4:  # SCRATCH4 = SDK/ROM reboot magic, never touch
            machine.mem32[WD + 0x0C + 4 * i] = (tag << 8) | i
        machine.mem32[PM + 0xB0 + 4 * i] = PM_PW | ((tag << 8) | 0x80 | i)


def read():
    wd = [machine.mem32[WD + 0x0C + 4 * i] & 0xFFFFFFFF for i in range(8)]
    pm = [machine.mem32[PM + 0xB0 + 4 * i] & 0xFFFFFFFF for i in range(8)]
    print("reset_cause", machine.reset_cause())
    print("WD", [hex(v) for v in wd])
    print("PM", [hex(v) for v in pm])
    return wd, pm


def wdt_reset():
    machine.WDT(timeout=1000)
    while True:
        pass
