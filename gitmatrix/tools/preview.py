#!/usr/bin/env python3
"""Record an animated GIF preview of a GitMatrix plugin, for the store's Discover page.

The plugin runs under the real runtime on the CPython host harness (tests/stubs) with
a simulated clock, the widget's ROM pixel fonts and canned network responses, and
each presented frame is drawn as a 53x7 grid of LED dots.

    python3 tools/preview.py path/to/my_plugin                 # writes my_plugin/preview.gif
    python3 tools/preview.py path/to/my_plugin -o out.gif --scale 6

An optional `preview/preview.json` in the plugin folder sets up the recording:

    {
      "seconds": 6,                      # length of the loop (1-15)
      "skip": 0,                         # seconds to run before recording starts
      "seed": 1,                         # random.seed() for repeatable output
      "settings": {"login": "octocat"},  # plugin settings (secrets included) to apply
      "fetch": {"api.github.com/graphql": "graphql.json"}
    }

`fetch` maps "host/path" (no query string), or just "host", to a file in `preview/`
that AsyncFetch returns with HTTP 200. Anything else fails with HTTP 404, so a
preview never touches the network. Needs Pillow.
"""

import argparse
import builtins
import io
import json
import os
import random
import shutil
import struct
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
GITMATRIX = HERE.parent
FONTS = GITMATRIX.parent / "device" / "rom" / "fonts"
sys.path.insert(0, str(GITMATRIX / "tests"))
sys.path.insert(0, str(GITMATRIX / "modules" / "python"))

W, H = 53, 7
FRAME_MS = 16
GIF_FRAME_MS = 50
SECONDS_MAX = 15
BACKGROUND = (8, 9, 11)
OFF_LED = (24, 27, 32)


# ---- the widget's ROM pixel fonts (.ppf) -----------------------------------------------

class PixelFont:
    """A ROM .ppf font: "ppf!", u16 0, u16 0, u16 glyph count, u16 cell width, u16 cell
    height, 32-byte name, then (u32 codepoint, u16 width) per glyph and a row-major
    1bpp bitmap per glyph. Spacing matches the device: advance is width + 1, a
    zero-width glyph (space) advances 3, and positions truncate toward zero."""

    def __init__(self, path):
        data = Path(path).read_bytes()
        if data[:4] != b"ppf!":
            raise ValueError(f"{path}: not a ppf font")
        count, self.cell_width, self.height = struct.unpack(">HHH", data[8:14])
        self.name = data[14:46].rstrip(b"\0").decode()
        self.row_bytes = (self.cell_width + 7) // 8
        table, size = 46, self.height * self.row_bytes
        bitmaps = table + count * 6
        self.glyphs = {}
        for i in range(count):
            cp, width = struct.unpack(">IH", data[table + i * 6:table + i * 6 + 6])
            self.glyphs[cp] = (width, data[bitmaps + i * size:bitmaps + (i + 1) * size])
        self.advance = self.glyphs.get(ord("n"), (self.cell_width - 1, b""))[0] + 1

    def glyph(self, ch):
        return self.glyphs.get(ord(ch)) or self.glyphs.get(ord("?")) or (0, b"")

    def measure(self, text):
        return sum(self.glyph(ch)[0] + 1 if self.glyph(ch)[0] else 3 for ch in text)

    def draw(self, img, text, x, y, word):
        px = img.words
        x, y = int(x), int(y)
        for ch in text:
            width, bits = self.glyph(ch)
            for row in range(self.height):
                ty = y + row
                if not 0 <= ty < img.height:
                    continue
                line = int.from_bytes(bits[row * self.row_bytes:(row + 1) * self.row_bytes], "big")
                if not line:
                    continue
                top = self.row_bytes * 8 - 1
                for col in range(self.cell_width):
                    tx = x + col
                    if line >> (top - col) & 1 and 0 <= tx < img.width:
                        px[tx + ty * img.width] = word
            x += width + 1 if width else 3


def load_fonts():
    return types.SimpleNamespace(**{p.stem: PixelFont(p) for p in sorted(FONTS.glob("*.ppf"))})


def install_fonts(stubs):
    fonts = load_fonts()
    stubs.font = fonts
    builtins.font = fonts
    builtins.pixel_font = PixelFont
    plain_text, plain_measure = stubs.image.text, stubs.image.measure_text

    def text(self, text, pos, size=None):
        if not isinstance(self.font, PixelFont):
            return plain_text(self, text, pos, size)
        self.texts.append((text, pos.x, pos.y))
        word = self.pen.word() if isinstance(self.pen, stubs.Pen) else 0xFFFFFFFF
        self.font.draw(self, str(text), pos.x, pos.y, word)
        return None

    def measure_text(self, text, size=None):
        if not isinstance(self.font, PixelFont):
            return plain_measure(self, text, size)
        return (float(self.font.measure(str(text))), float(self.font.height))

    stubs.image.text = text
    stubs.image.measure_text = measure_text


# ---- canned network ---------------------------------------------------------------------

def preview_fetch(responses):
    """An AsyncFetch that answers from `responses` ({"host/path" or "host": bytes})."""
    sys.modules.setdefault("tls", types.ModuleType("tls"))  # never used: nothing connects
    import fetch

    class PreviewFetch(fetch.AsyncFetch):
        def _fetch_to_stream(self):
            yield  # one update() of latency, like a real request
            path = self._path.split("?", 1)[0].strip("/")
            body = responses.get(f"{self._host}/{path}", responses.get(self._host))
            self._response_headers = {}
            if body is None:
                print(f"preview: no fixture for {self._host}/{path}, answering 404", file=sys.stderr)
                self._status_code, body = 404, b""
            else:
                self._status_code = 200
            if self._file:
                with open(self._file, "wb") as f:
                    f.write(body)
            else:
                self._buffer = bytearray(body)
                self._buffer_len = len(body)

        # CPython's json and StringIO can't read the memoryview MicroPython hands them
        def to_json(self):
            return json.load(self.stream) if self._file else json.loads(bytes(self.body))

        @property
        def stream(self):
            return open(self._file) if self._file else io.StringIO(bytes(self.body).decode())

    return PreviewFetch


# ---- recording ----------------------------------------------------------------------------

def read_spec(plugin):
    path = plugin / "preview" / "preview.json"
    spec = json.loads(path.read_text()) if path.is_file() else {}
    if not isinstance(spec, dict):
        raise ValueError(f"{path}: not an object")
    seconds = spec.get("seconds", 6)
    if not isinstance(seconds, (int, float)) or not 1 <= seconds <= SECONDS_MAX:
        raise ValueError(f"{path}: seconds must be 1-{SECONDS_MAX}")
    responses = {}
    for key, name in (spec.get("fetch") or {}).items():
        file = (plugin / "preview" / name).resolve()
        if (plugin / "preview").resolve() not in file.parents or not file.is_file():
            raise ValueError(f"{path}: fetch file {name!r} isn't in preview/")
        responses[key.split("?", 1)[0].strip("/")] = file.read_bytes()
    return {
        "seconds": seconds,
        "skip": spec.get("skip", 0),
        "seed": spec.get("seed", 1),
        "settings": spec.get("settings") or {},
        "responses": responses,
    }


def record(plugin, spec):
    """Run the plugin and return [(frame words, duration ms)] for the loop."""
    import stubs

    fake_bt = stubs.install()
    install_fonts(stubs)
    fake_fetch = preview_fetch(spec["responses"])
    import fetch

    fetch.AsyncFetch = fake_fetch

    clock = types.SimpleNamespace(now=1000)
    time.ticks_ms = lambda: clock.now
    time.ticks_diff = lambda a, b: a - b
    time.ticks_add = lambda a, b: a + b

    import gitmatrix
    from gitmatrix import _boot, _compat, _flash, _persist, _runtime
    from gitmatrix._input import Input

    gitmatrix.install_globals()
    _compat.ticks_ms = time.ticks_ms
    random.seed(spec["seed"])

    name = plugin.name
    root = Path(tempfile.mkdtemp(prefix="gitmatrix-preview-"))
    try:
        firmware = GITMATRIX / "firmware"
        for part in ("defaults", "lib", "html", "sounds"):
            shutil.copytree(firmware / part, root / part)
        shutil.copytree(plugin, root / "lib" / "plugins" / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("preview", "preview.gif", "__pycache__"))
        (root / "config.py").write_text("\n".join([
            f"plugins = {[name]!r}", "disabled_plugins = []", "quarantined = {}", "delay = 3600",
            "transition = 'slide'", "brightness = 100", "volume = 64", "watchdog = False"]) + "\n")
        _runtime.ROOT = str(root)
        sys.path.insert(0, str(root / "lib"))
        _persist.set_store(_persist.MemoryStore())
        _flash.set_bt(fake_bt)
        write_settings(root, name, spec["settings"])

        frames = []
        fb = builtins.screen = stubs.image()

        class Capture(stubs.Display):
            def update(self, *args):
                frames.append((clock.now, bytes(fb)))
                return super().update(*args)

        builtins.display = Capture()
        rt = _boot.boot(Input())
        plugin_obj = rt.loader.plugins.get(name)
        if plugin_obj is None:
            raise RuntimeError(f"{name} didn't load")
        start = clock.now + int(spec["skip"] * 1000)
        end = start + int(spec["seconds"] * 1000)
        while clock.now < end:
            clock.now += FRAME_MS
            rt.host.frame(clock.now)
        if name in rt.config.quarantined or plugin_obj.error_count:
            raise RuntimeError(f"{name} raised while recording (see the log above)")
        return sample(frames, start, end)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def write_settings(root, name, values):
    if not values:
        return
    manifest = json.loads((root / "lib" / "plugins" / name / "package.json").read_text())
    secret = {f.get("key") for f in manifest.get("settings", []) if isinstance(f, dict) and f.get("type") == "secret"}
    plain = {k: v for k, v in values.items() if k not in secret}
    secrets = {k: v for k, v in values.items() if k in secret}
    (root / "data" / name).mkdir(parents=True, exist_ok=True)
    (root / "data" / name / "settings.json").write_text(json.dumps(plain))
    if secrets:
        (root / "secrets").mkdir(exist_ok=True)
        (root / "secrets" / f"{name}.json").write_text(json.dumps(secrets))


def sample(frames, start, end):
    """The frame on screen at each GIF_FRAME_MS step, with repeats merged."""
    out = []
    i, shown = 0, bytes(W * H * 4)
    for t in range(start, end, GIF_FRAME_MS):
        while i < len(frames) and frames[i][0] <= t:
            shown = frames[i][1]
            i += 1
        if out and out[-1][0] == shown:
            out[-1][1] += GIF_FRAME_MS
        else:
            out.append([shown, GIF_FRAME_MS])
    return [(f, ms) for f, ms in out]


# ---- drawing -----------------------------------------------------------------------------

def led_colour(word):
    """An LED's light added to the unlit LED's grey, so dim pixels keep their tint."""
    return tuple(min(255, base + (word >> shift & 255)) for base, shift in zip(OFF_LED, (0, 8, 16), strict=True))


def dot_mask(scale):
    from PIL import Image

    dot = Image.new("1", (scale, scale), 0)
    centre, radius = (scale - 1) / 2, scale * 0.4
    dot.putdata([int((x - centre) ** 2 + (y - centre) ** 2 <= radius * radius) for y in range(scale) for x in range(scale)])
    return dot


def to_gif(frames, out, scale):
    from PIL import Image

    words = [struct.unpack(f"<{W * H}I", f) for f, _ in frames]
    colours = sorted({led_colour(w) for frame in words for w in frame})
    if len(colours) > 255:
        strip = Image.new("RGB", (len(colours), 1))
        strip.putdata(colours)
        pal = strip.quantize(255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        lookup = dict(zip(colours, pal.getdata(), strict=True))
        palette = pal.getpalette()[:255 * 3]
        index = {c: lookup[c] + 1 for c in colours}
    else:
        palette = [v for c in colours for v in c]
        index = {c: i + 1 for i, c in enumerate(colours)}
    palette = list(BACKGROUND) + palette
    palette += [0] * (768 - len(palette))

    dot = dot_mask(scale)
    images = []
    for frame in words:
        img = Image.new("P", (W * scale, H * scale), 0)
        img.putpalette(palette)
        for y in range(H):
            for x in range(W):
                img.paste(index[led_colour(frame[x + y * W])], (x * scale, y * scale), dot)
        images.append(img)
    images[0].save(out, save_all=True, append_images=images[1:], duration=[ms for _, ms in frames],
                   loop=0, optimize=True, disposal=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("plugin", type=Path, help="plugin folder (with __init__.py and package.json)")
    ap.add_argument("-o", "--out", type=Path, help="output GIF (default: <plugin>/preview.gif)")
    ap.add_argument("--scale", type=int, default=8, help="pixels per LED (default 8)")
    args = ap.parse_args(argv)
    plugin = args.plugin.resolve()
    if not (plugin / "__init__.py").is_file() or not (plugin / "package.json").is_file():
        ap.error(f"{plugin} isn't a plugin folder")
    spec = read_spec(plugin)
    frames = record(plugin, spec)
    out = args.out or plugin / "preview.gif"
    to_gif(frames, out, max(2, args.scale))
    print(f"{plugin.name}: {len(frames)} frames, {os.path.getsize(out)} bytes -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
