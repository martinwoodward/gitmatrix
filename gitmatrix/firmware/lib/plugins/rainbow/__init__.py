# Scrolling rainbow stripes.

import gitmatrix
from gitmatrix import _compat

pens = []
STEPS = 64


def on_show():
    if not pens:
        for i in range(STEPS):
            pens.append(color.oklch(200, 255, i * 256 // STEPS))


def update():
    if not pens:
        on_show()
    cfg = gitmatrix.settings()
    speed = cfg.get("speed", 20)
    stripes = cfg.get("stripes", 0.5)
    offset = _compat.ticks_ms() * speed // 1000
    for x in range(screen.width):
        screen.pen = pens[int(x / screen.width * stripes * STEPS + offset) % STEPS]
        screen.rectangle(x, 0, 1, screen.height)
