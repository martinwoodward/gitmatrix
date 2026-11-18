# Phase 0 spike: warm-reset reliability harness. Installed temporarily as /main.py.
# State lives in /wdt_state.json (flash) so it survives the power cycle needed after a hang.
# Each boot: if the previous cycle expected a WDT reset but we came back via power-on,
# that cycle hung (needed a manual replug) -> log it and move to the next condition.
# The host must NOT hold the serial port open while this runs.
import json, machine, os, time

STATE = "/wdt_state.json"
CONDS = ["A_clean", "B_bt_init", "C_wifi_sta", "D_bt_init_deinit", "E_bt_full_wdsel"]
CYCLES = 6
PSM_WDSEL = 0x40018008


def save(s):
    with open(STATE + ".tmp", "w") as f:
        json.dump(s, f)
    os.rename(STATE + ".tmp", STATE)


try:
    with open(STATE) as f:
        s = json.load(f)
except OSError:
    s = {"ci": 0, "n": 0, "pending": False, "log": []}

cause = machine.reset_cause()
if s["pending"]:
    c = CONDS[s["ci"]]
    if cause == 3:
        s["log"].append([c, s["n"], "ok"])
    else:
        s["log"].append([c, s["n"], "HANG(cause=%d)" % cause])
        s["ci"] += 1
        s["n"] = 0
    s["pending"] = False
if s["ci"] < len(CONDS) and s["n"] >= CYCLES:
    s["ci"] += 1
    s["n"] = 0

if s["ci"] >= len(CONDS):
    s["done"] = True
    save(s)
    print("WDT harness done", s["log"])
else:
    c = CONDS[s["ci"]]
    print("WDT harness", c, s["n"], "- Ctrl-C within 3 s to abort")
    time.sleep(3)
    if c.startswith("B") or c.startswith("D") or c.startswith("E"):
        import bt_audio
        bt_audio.init(name="Widget")
        time.sleep(2)
        if c.startswith("D"):
            bt_audio.deinit()
            time.sleep(0.5)
    elif c.startswith("C"):
        import network
        w = network.WLAN(network.STA_IF)
        w.active(True)
        try:
            w.scan()
        except Exception:
            pass
    s["n"] += 1
    s["pending"] = True
    save(s)
    wdt = machine.WDT(timeout=300)
    if c.startswith("E"):
        machine.mem32[PSM_WDSEL] = 0x01FFFFFF  # also reset ROSC/XOSC
    while True:
        pass
