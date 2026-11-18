# Spectrum analyser for the Bluetooth speaker, using the audio API. Holds the
# display while music plays (package.json "hold": "audio"); shows a hint otherwise.

import gitmatrix
from gitmatrix import _compat
from scrolling import Scroll

BANDS = 16
peaks = [0.0] * BANDS
bar_pens = []
peak_pen = None
dim_pen = None
kick_at = 0
hint = None


def on_show():
    global peak_pen, dim_pen
    if not bar_pens:
        for i in range(BANDS):
            bar_pens.append(color.oklch(190, 230, (150 - i * 9) % 256))
        peak_pen = color.oklch(250, 60, 60)
        dim_pen = color.oklch(120, 120, 150)


def update():
    global kick_at
    if not bar_pens:
        on_show()
    audio = gitmatrix.audio
    w, h = screen.width, screen.height
    screen.pen = color.rgb(0, 0, 0)
    screen.clear()
    if not audio.active():
        global hint
        if hint is None:
            hint = Scroll("Play music to " + audio.name, speed=15)
        screen.pen = dim_pen
        hint.update()
        hint.render()
        return
    now = _compat.ticks_ms()
    if audio.kick():
        kick_at = now
    flash = max(0, 255 - _compat.ticks_diff(now, kick_at) // 2)
    if flash:
        screen.pen = color.oklch(60, 120, 330, flash // 3)
        screen.clear()
    spec = audio.spectrum()
    bar_w = w // BANDS
    x0 = (w - bar_w * BANDS) // 2
    for i in range(BANDS):
        v = max(0.0, min(1.0, spec[i]))
        peaks[i] = max(v, peaks[i] - 0.02)
        bh = int(v * h + 0.5)
        x = x0 + i * bar_w
        if bh:
            screen.pen = bar_pens[i]
            screen.rectangle(x, h - bh, bar_w - 1, bh)
        py = h - 1 - min(h - 1, int(peaks[i] * h))
        screen.pen = peak_pen
        screen.rectangle(x, py, bar_w - 1, 1)
