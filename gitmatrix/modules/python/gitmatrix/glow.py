# Helpers for the soft "glow" look of the built-in plugins: draw a blurred pass
# then a crisp pass into a canvas with a margin (so the blur doesn't clip at the
# edges), then blit the middle onto `screen`.

import random

W, H = 53, 7
OX, OY = 10, 7
CW, CH = W + OX * 2, H + OY * 2

_circle = None


def blob(c, x, y, r, pen):
    global _circle
    if _circle is None:
        _circle = shape.circle(0, 0, 1.0)
    c.pen = pen
    _circle.transform = mat3().translate(x, y).scale(r)
    c.shape(_circle)


def text_size(c, s, face):
    c.font = face
    return c.measure_text(s)


def text(c, s, x, face, pen, y_off=0):
    """Text at display x (0..52), vertically centred on the 7 rows."""
    c.font = face
    th = c.measure_text(s)[1]
    c.pen = pen
    c.text(s, vec2(OX + x, OY + (H - th) // 2 + y_off))


class Canvas:
    def __init__(self):
        self.image = image(CW, CH)
        self.black = color.rgb(0, 0, 0)

    def clear(self):
        self.image.pen = self.black
        self.image.clear()
        return self.image

    def blur(self, amount=2):
        self.image.blur(amount)

    def show(self):
        screen.blit(self.image, vec2(-OX, -OY))


class Ambient:
    """Slow drifting soft blobs that sit behind everything in the glow pass."""

    def __init__(self, hue, count=5, alpha=45, spread=20):
        self.blobs = []
        self.pens = []
        for _ in range(count):
            self.blobs.append([random.uniform(0, CW), random.uniform(OY, OY + H),
                               random.uniform(3, 6), random.uniform(-1, 1) * 0.12,
                               random.uniform(-1, 1) * 0.04])
            self.pens.append(color.oklch(140, 200, int(hue + random.uniform(-spread, spread)) % 256, alpha))

    def draw(self, c):
        for i, b in enumerate(self.blobs):
            b[0] += b[3]
            b[1] += b[4]
            if not 0 <= b[0] <= CW:
                b[3] = -b[3]
            if not OY - 2 <= b[1] <= OY + H + 2:
                b[4] = -b[4]
            blob(c, b[0], b[1], b[2], self.pens[i])


def ease_out_back(t):
    c1 = 1.70158
    c3 = c1 + 1
    t -= 1
    return 1 + c3 * t * t * t + c1 * t * t
