# Phase 0 spike: warm-reset reliability harness. Installed temporarily as /main.py.
# State lives in /wdt_state.json (flash) so it survives the power cycle needed after a hang.
# Each boot: if the previous cycle expected a warm reset but we came back via power-on,
# that cycle hung (needed a manual replug) -> log it and move to the next condition.
# Stops after MAX_HANGS hangs or when all conditions are done, then restores the stock
# /main.py itself so the widget is left as it was. The host must NOT hold the serial port open.
import json, machine, os, time

STATE = "/wdt_state.json"
STOCK_MAIN = "import bt_audio_visualiser  # noqa: F401\n"
# R = machine.reset(), others = WDT timeout. Ordered most-informative first.
CONDS = ["R_reset_clean", "A_wdt_clean", "B_wdt_bt_init", "D_wdt_bt_init_deinit",
         "C_wdt_wifi_sta", "E_wdt_bt_wdsel"]
CYCLES = 5
MAX_HANGS = 2
MARK = 0xC0FFEE01
PSM_WDSEL = 0x40018008


def save(s):
    with open(STATE + ".tmp", "w") as f:
        json.dump(s, f)
    os.rename(STATE + ".tmp", STATE)


try:
    with open(STATE) as f:
        s = json.load(f)
except OSError:
    s = {"ci": 0, "n": 0, "pending": False, "hangs": 0, "log": []}

cause = machine.reset_cause()
mb = machine.mem_backup()
marker = mb[0]
mb[0] = 0
if s["pending"]:
    c = CONDS[s["ci"]]
    if cause == 3:
        s["log"].append([c, s["n"], "ok", marker == MARK])
    else:
        s["log"].append([c, s["n"], "HANG(cause=%d)" % cause, marker == MARK])
        s["hangs"] += 1
        s["ci"] += 1
        s["n"] = 0
    s["pending"] = False
if s["ci"] < len(CONDS) and s["n"] >= CYCLES:
    s["ci"] += 1
    s["n"] = 0

if s["ci"] >= len(CONDS) or s["hangs"] >= MAX_HANGS:
    s["done"] = True
    save(s)
    with open("/main.py", "w") as f:
        f.write(STOCK_MAIN)
    print("WDT harness done", s["log"])
else:
    c = CONDS[s["ci"]]
    print("WDT harness", c, s["n"], "- Ctrl-C within 3 s to abort")
    time.sleep(3)
    if c.startswith(("B", "D", "E")):
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
    mb[0] = MARK
    if c.startswith("R"):
        machine.reset()
    wdt = machine.WDT(timeout=300)
    if c.startswith("E"):
        machine.mem32[PSM_WDSEL] = 0x01FFFFFF  # also reset ROSC/XOSC
    while True:
        pass
