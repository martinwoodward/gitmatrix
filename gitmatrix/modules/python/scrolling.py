# Scrolling text, with the same interface as Art Matrix's scrolling.Scroll.
#
#   s = Scroll("Hello world")          # bitmap font, scrolls across `screen`
#   s.update(); s.render()             # each frame
#
# gap=None scrolls the text fully off before it comes back; an int repeats it
# with that many pixels between copies.

from gitmatrix import _compat


class Scroll:
    def __init__(self, text, font_face=None, font_size=None, target=None, speed=25, gap=None, align="middle"):
        self.text = text
        self.font_face = font_face or font.sins
        self.font_size = font_size
        self._target = target
        self.speed = speed
        self.gap = gap
        self.align = align
        self.t_start = None
        self.x = 0
        self._measure()

    @property
    def target(self):
        # resolved each time, so it follows the host rebinding `screen`
        return self._target if self._target is not None else screen

    def _vector(self):
        try:
            return isinstance(self.font_face, vector_font)
        except NameError:
            return False

    def _measure(self):
        t = self.target
        t.font = self.font_face
        if self._vector():
            if self.font_size is None:
                raise ValueError("Scroll: vector fonts need a font_size")
            self.tw, _ = t.measure_text(self.text, self.font_size)
            self.th = self.font_size
        else:
            self.tw, self.th = t.measure_text(self.text)
        self.distance = self.tw + (self.gap if isinstance(self.gap, int) else t.width)
        if isinstance(self.align, int):
            self.y = self.align
        elif self.align == "bottom":
            self.y = t.height - self.th
        elif self.align == "top":
            self.y = 0
        else:
            self.y = (t.height - self.th) // 2

    def set_text(self, text):
        self.text = text
        self._measure()

    def update(self, progress=None):
        """Advance; returns how many times the text has scrolled through (float)."""
        if self.t_start is None:
            self.t_start = _compat.ticks_ms()
        if progress is not None:
            moved = self.distance * progress
        else:
            moved = _compat.ticks_diff(_compat.ticks_ms(), self.t_start) * self.speed / 1000
            progress = moved / self.distance
        moved %= self.distance
        self.x = -moved if isinstance(self.gap, int) else self.target.width - moved
        return progress

    def _draw(self, t, x, y):
        if self._vector():
            t.text(self.text, vec2(x, y), self.font_size)
        else:
            t.text(self.text, vec2(x, y))

    def render(self, offset=None):
        t = self.target
        t.font = self.font_face
        ox = offset.x if offset is not None else 0
        oy = offset.y if offset is not None else 0
        x = self.x + ox
        self._draw(t, x, self.y + oy)
        if isinstance(self.gap, int):
            while x + self.tw < t.width:
                x += self.tw + self.gap
                self._draw(t, x, self.y + oy)

    def reset(self):
        self.t_start = None
