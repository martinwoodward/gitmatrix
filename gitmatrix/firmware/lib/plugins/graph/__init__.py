# The GitHub contribution graph: the last 53 weeks, one column per week, one row
# per weekday. Data comes from /contrib_data.json (written by the sync service).

import json
import math
import random

from gitmatrix import _compat, _runtime
from gitmatrix.glow import OX, OY, W, Ambient, Canvas, blob, text

HUE = 100
TWINKLE_MS = 900
LEVEL_RGB = ((0, 6, 2), (0, 45, 14), (0, 100, 30), (0, 170, 50), (70, 255, 110))

canvas = None
ambient = None
cells = []
twinkles = {}
loaded_mtime = None
shown_at = 0
band_pen = None
no_data_pen = None


def _mtime(path):
    try:
        import os
        return os.stat(path)[8]
    except OSError:
        return None


def load():
    global cells, loaded_mtime
    path = _runtime.path("/contrib_data.json")
    loaded_mtime = _mtime(path)
    try:
        with open(path) as f:
            weeks = json.load(f)["weeks"][-W:]
        cells = [(x, d["weekday"], min(4, d["level"]),
                  random.uniform(0, 6.28), random.uniform(0.0012, 0.003))
                 for x, w in enumerate(weeks) for d in w["contribution_days"]]
    except (OSError, ValueError, KeyError, TypeError):
        cells = []


def on_show():
    global canvas, ambient, band_pen, no_data_pen, shown_at
    if canvas is None:
        canvas = Canvas()
        ambient = Ambient(HUE, count=4, alpha=25)
        band_pen = color.oklch(200, 200, HUE, 40)
        no_data_pen = color.oklch(180, 200, 25)
    if not cells or _mtime(_runtime.path("/contrib_data.json")) != loaded_mtime:
        load()
    shown_at = _compat.ticks_ms()


def _draw_cells(c, t, glow):
    cols = min(W, t // 12 + 1)
    n = len(cells)
    for i in range(n):
        x, y, level, phase, speed = cells[i]
        if x >= cols or (glow and level < 2 and i not in twinkles):
            continue
        if i == n - 1:
            c.pen = color.oklch(150 + int(90 * (0.5 + 0.5 * math.sin(t / 300))), 220, HUE)
        else:
            r, g, b = LEVEL_RGB[level]
            f = 0.72 + 0.28 * math.sin(t * speed + phase) if level else 1.0
            tw = twinkles.get(i)
            if tw is not None:
                k = math.sin(math.pi * (t - tw) / TWINKLE_MS)
                r, g, b = r + (180 - r) * k, g + (255 - g) * k, b + (200 - b) * k
                f = f + (1 - f) * k
            c.pen = color.rgb(int(r * f), int(g * f), int(b * f))
        c.rectangle(OX + x, OY + y, 1, 1)


def _update_twinkles(t):
    for i, start in list(twinkles.items()):
        if t - start > TWINKLE_MS or t < start:
            del twinkles[i]
    if cells and random.random() < 0.12:
        i = random.randrange(len(cells))
        if cells[i][2] > 0:
            twinkles[i] = t


def update():
    if canvas is None:
        on_show()
    t = _compat.ticks_diff(_compat.ticks_ms(), shown_at)
    c = canvas.clear()
    ambient.draw(c)
    blob(c, OX + ((t // 25) % (W + 30)) - 15, OY + 3, 5, band_pen)
    _draw_cells(c, t, True)
    canvas.blur(2)
    _update_twinkles(t)
    if cells:
        _draw_cells(c, t, False)
    else:
        text(c, "No data", 8, font.sins, no_data_pen)
    canvas.show()
