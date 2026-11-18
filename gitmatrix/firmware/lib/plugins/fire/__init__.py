# Doom fire at 53x7, written straight into this plugin's surface. The surface is
# 53x7 RGBA, one 0xAABBGGRR word per pixel.

import random

import micropython
from micropython import const

W = const(55)    # display + 1 column each side
H = const(11)    # display + 4 seed rows below
ROWS = const(7)
COLS = const(53)
SPAWNS = const(9)
DAMPING = const(800)  # ~0.98 / 5 in 12 bit fixed point

heat = bytearray(W * H * 4)
palette = bytearray(6 * 4)
_seeds = bytearray(SPAWNS)


# colours as 0xBBGGRR (alpha is added when drawing), coolest first
for _i, _c in enumerate((0x000000, 0x050510, 0x00103C, 0x0040C8, 0x10A0FF, 0xB4FFFF)):
    palette[_i * 4:_i * 4 + 4] = _c.to_bytes(4, "little")


@micropython.viper
def _p32(buf) -> ptr32:  # noqa: F821
    return ptr32(buf)  # noqa: F821


@micropython.viper
def _p8(buf) -> ptr8:  # noqa: F821
    return ptr8(buf)  # noqa: F821


heat_p = _p32(heat)
palette_p = _p32(palette)
seeds_p = _p8(_seeds)


@micropython.viper
def _step(h: ptr32, seeds: ptr8):  # noqa: F821
    for x in range(W):
        h[x + W * (H - 1)] = 0
        h[x + W * (H - 2)] = 0
    for i in range(SPAWNS):
        x = seeds[i] % (W - 4) + 2
        for dy in range(1, 3):
            row = W * (H - dy)
            h[x - 1 + row] += 65536
            h[x + row] += 65536
            h[x + 1 + row] += 65536
    for y in range(H - 2):
        for x in range(1, W - 1):
            v = h[x + W * y] + h[x + W * (y + 1)] + h[x + W * (y + 2)] + h[x - 1 + W * (y + 1)] + h[x + 1 + W * (y + 1)]
            h[x + W * y] = (v * DAMPING) >> 12


@micropython.viper
def _draw(h: ptr32, pal: ptr32, fb: ptr32):  # noqa: F821
    alpha = int(0xFF)
    alpha <<= 24
    for y in range(ROWS):
        for x in range(COLS):
            v = h[x + 1 + y * W]
            if v < 9000:
                i = 0
            elif v < 14000:
                i = 1
            elif v < 19000:
                i = 2
            elif v < 23000:
                i = 3
            elif v < 29000:
                i = 4
            else:
                i = 5
            fb[x + COLS * y] = pal[i] | alpha


def update():
    for i in range(SPAWNS):
        _seeds[i] = random.getrandbits(8)
    _step(heat_p, seeds_p)
    _draw(heat_p, palette_p, _p32(memoryview(screen)))
