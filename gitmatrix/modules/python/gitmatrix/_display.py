# Display host. Each plugin draws into its own offscreen image(53, 7), bound as
# builtins.screen while its hooks run, so pen/font/clip state persists between
# frames and `memoryview(screen)` writes land in the plugin's own buffer. The
# host composites the chosen surface (or a transition) into the framebuffer.
#
# builtins.display is a proxy: a plugin calling display.update() does nothing
# (the host decides when to present), like Art Matrix's set_hold.

import builtins

from gitmatrix import _runtime

WIDTH = 53
HEIGHT = 7
FRAME_MS = 16  # the frame loop's period


def ms_per_px(speed):
    """Scroll period for `speed` px/s, rounded to whole frames so every pixel
    shows for the same number of frames (a 2/3/2/3 frame dwell judders)."""
    step = max(1, int(speed)) * FRAME_MS
    return max(1, (1000 + step // 2) // step) * FRAME_MS


def scroll_px(elapsed_ms, speed):
    """Whole pixels scrolled after `elapsed_ms`. The half-frame offset keeps each
    step clear of frame boundaries, so loop jitter can't move it a frame."""
    return max(0, elapsed_ms + FRAME_MS // 2) // ms_per_px(speed)


def ease_in_out_cubic(t):
    if t < 0.5:
        return 4 * t * t * t
    t = 2 * t - 2
    return 0.5 * t * t * t + 1


def ease_in_quart(t):
    return t * t * t * t


class DisplayProxy:
    def __init__(self, real):
        self._real = real
        self.swallowed = 0

    def update(self, *args):
        if _runtime.current is not None:
            self.swallowed += 1
            return None
        return self._real.update(*args)

    def __getattr__(self, name):
        return getattr(self._real, name)


class Compositor:
    def __init__(self, framebuffer, driver):
        self.fb = framebuffer       # the real `screen` image
        self.driver = driver        # the real `display`
        self.black = color.rgb(0, 0, 0)
        self.white = color.rgb(255, 255, 255)

    def new_surface(self):
        s = image(WIDTH, HEIGHT)
        reset_surface(s)
        return s

    def bind(self, surface):
        builtins.screen = surface

    def unbind(self):
        builtins.screen = self.fb

    def clear(self):
        self.fb.pen = self.black
        self.fb.clear()

    def blit(self, surface, x=0):
        if -WIDTH < x < WIDTH:
            self.fb.blit(surface, vec2(x, 0))

    def present(self, surface=None):
        if surface is not None:
            self.clear()
            self.blit(surface)
        self.driver.update()

    def slide(self, old, new, p, direction=1):
        """Push `new` in over `old`; p 0..1, direction +1 from the right."""
        s = int(ease_in_out_cubic(min(1.0, max(0.0, p))) * WIDTH + 0.5)
        self.clear()
        if direction >= 0:
            self.blit(old, -s)
            self.blit(new, WIDTH - s)
        else:
            self.blit(old, s)
            self.blit(new, s - WIDTH)


def reset_surface(s):
    s.pen = color.rgb(0, 0, 0)
    s.clear()
    s.pen = color.rgb(255, 255, 255)
    try:
        s.font = font.sins
    except (NameError, AttributeError):
        pass
