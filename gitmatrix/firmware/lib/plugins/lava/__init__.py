# Lava lamp: blobs heat up at the bottom-middle, rise, cool and sink.

import math
import random

import gitmatrix

COUNT = 5
SIZE = 4
DAMPING = 0.80
DEFINITION = 0.5

blobs = []
base = None
hue = None


class Blob:
    def __init__(self, w, h, hue):
        self.r = random.uniform(0.5, 2) * SIZE
        self.x = random.uniform(0, w)
        self.y = random.uniform(-3, h + 3)
        self.pen = color.oklch(random.uniform(10, 200), random.uniform(200, 240),
                               random.uniform(hue, hue + 30) % 256, random.randint(150, 220))
        self.temperature = (self.x / w) * 100
        self.dx = 0
        self.inertia = random.uniform(0.5, 1)
        self.dy = (random.uniform(-1, 1) + 0.2) / 100
        self.circle = shape.circle(0, 0, 1.0)

    def update(self, w, h):
        self.temperature += math.sin((self.x / w + 0.5) * math.pi) / 40 * self.inertia
        self.temperature = max(0, min(100, self.temperature))
        self.dx = (self.dx + (self.temperature - 50) / 10000) * DAMPING
        self.x += self.dx
        self.y += self.dy
        if self.x < 0 or self.x >= w or self.y < 0 or self.y >= h:
            self.dy *= -1
        self.x = max(0, min(w, self.x))
        self.y = max(2, min(h - 2, self.y))
        sx = 0.5 + math.sin(self.x / (w - 1) * math.pi) ** 2
        self.circle.transform = mat3().translate(self.x, self.y).scale(self.r * sx, self.r)

    def draw(self):
        screen.pen = self.pen
        screen.shape(self.circle)


def on_show():
    global base, hue
    h = gitmatrix.settings().get("hue", 0)
    if h != hue or not blobs:
        hue = h
        blobs[:] = [Blob(screen.width, screen.height, hue) for _ in range(COUNT)]
        base = color.oklch(12, 16, hue)


def update():
    if not blobs:
        on_show()
    screen.alpha = 255
    screen.pen = base
    screen.clear()
    for b in blobs:
        b.update(screen.width, screen.height)
        b.draw()
    screen.blur(1)
    screen.alpha = int(255 * DEFINITION)
    for b in blobs:
        b.draw()
    screen.alpha = 255
