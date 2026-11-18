# Porting Art Matrix plugins to GitMatrix

GitMatrix keeps the [Art Matrix](https://github.com/pimoroni/artmatrix) plugin model so that
plugins move between the two with as few changes as possible: same folder layout, same
`update()` loop drawing into `screen` with picovector, same `plugin_config()` and
`config.html`, same `mic` API. What changes comes from the hardware: a **53×7** display
instead of 128×128, **one rotary encoder** instead of four buttons, and **Bluetooth audio**
instead of a microphone.

This guide lists every difference and ends with a worked example. The full API is in
[plugin-api.md](plugin-api.md); a starting point for new plugins is
[`gitmatrix/templates/plugin/`](../../gitmatrix/templates/plugin/README.md).

## Checklist

1. **Redraw for 53×7.** Use `screen.width` / `screen.height`, never `128`. Small fonts only
   (`font.sins` is 5 px high). Two-dimensional scenes usually become a strip or a scroller.
2. **Replace buttons.** Drop `machine.Pin(BUTTON_*)`. Set `"input": true` in `package.json`
   and handle `on_input(event)`, or leave the knob to the runtime.
3. **Expect Bluetooth audio, not a mic.** `mic.*` works but reads whatever is streaming to
   the speaker, and is silent when nothing is. `mic.waveform()` is all zeros.
4. **Check `config.html`.** Add a **Save** button: GitMatrix doesn't implement Art Matrix's
   `data-autosave` script. Add a reset button if you want one.
5. **Don't write files yourself.** Use `gitmatrix.state()` (or `plugin_config()` /
   `process_config()`); direct flash writes while audio streams can hang the board.
6. **Remove what isn't there**: `sensor`, `BUTTON_*`, ambient LEDs, `/sd`, `qr_screen`.
7. **`package.json`**: keep the Art Matrix fields; optionally add `settings`, `input`,
   `hold`, `tags` and `min_firmware`, and a 53×7 `thumbnail.png`.
8. Run it under the host harness (see the template's test) and on a device.

## What stays the same

| | Art Matrix and GitMatrix |
| --- | --- |
| Plugin location | `/lib/plugins/<name>/__init__.py` (+ `package.json`, `config.html`, `thumbnail.png`); `<name>` is a Python identifier |
| Loading | every enabled plugin is imported at boot; top-level code runs once |
| Drawing | `update()` every frame, drawing into the global `screen` with picovector (`color`, `shape`, `brush`, `vec2`, `rect`, `mat3`, `image`, `font`…) |
| `display.update()` | ignored inside plugins; the host owns the frame |
| Rotation | the runtime rotates enabled plugins on a timer (`delay`) |
| Settings | `from plugin_config import plugin_config` with the same type rules (bool = key present, tuple = `#rrggbb`, int/float coerced) |
| Config page | `config.html` served at `/config?plugin=<name>`, `{{ plugin }}` and `{{ placeholder.X }}` (the plugin module), `process_config(form)` if defined, a `reset` field deletes the saved form, the plugin is reloaded after a save |
| Audio analysis | `mic.level()`, `tone()`, `flux()`, `intensity()`, `active()`, `bpm()`, `phase()`, `beat()`, `kick()`, `clap()`, `spectrum()` (16 bands, 0-1, same array each call) |
| Helpers | `clamp`, `rnd`, `frnd`, `free`, `file_exists`, `is_dir`, `UID` as builtins; `fetch.AsyncFetch` and `scrolling.Scroll` modules |
| Imports | `import artmatrix` gives the `gitmatrix` module; `rom_font` is an alias of `font` (as on Art Matrix) |
| Errors | an exception in `update()` skips the frame; 5 in a row stop the plugin |

## What's different

### Display: 128×128 → 53×7

| | Art Matrix | GitMatrix |
| --- | --- | --- |
| Size | 128×128 | 53×7 (`gitmatrix.WIDTH`, `gitmatrix.HEIGHT`) |
| `screen` | one of two host surfaces, rebound per plugin | a 53×7 image per plugin, rebound per plugin |
| Fonts | `font.*`, `load_font()`, `text.draw()` / `text.scroll()` helpers | `font.*` (`font.sins` fits; most others are taller than 7 rows). Use `scrolling.Scroll` for text |
| Transitions | the host blends surfaces; plugins that draw into a captured framebuffer get a hard cut | the host slides or cuts between per-plugin surfaces |

`text`, `SpriteSheet` / `AnimatedSprite` and `load_font` aren't part of GitMatrix's API.
The stock 1.29 firmware's frozen `contribution_widget` module (which GitMatrix imports)
ships Badgeware-style `text` and `sprite` helpers, so they may work on the device, but
they're untested at 53×7 and not stubbed in the host harness. Prefer `scrolling.Scroll`.

Art Matrix plugins often compute positions from `screen.width / 2` at import time, which
keeps working. Anything with a hard-coded `128` or a layout that needs height (grids,
clocks with two lines, images) needs a redesign: scroll it, or show one value at a time.

### Input: four buttons → one encoder

Art Matrix exposes `BUTTON_A`, `BUTTON_B`, `BUTTON_C` and `BUTTON_USER` as pin numbers.
The runtime only uses A and C (held at power-on, for Wi-Fi setup), so plugins read them
directly with `machine.Pin(BUTTON_A, Pin.IN, Pin.PULL_UP)`.

GitMatrix has a single rotary encoder with a push button, and the runtime owns it:

- **A press always opens the menu** (or dismisses a notification). Plugins never see presses.
- **Turns step plugins**, unless the plugin's `package.json` has `"input": true`. Then
  each turn calls `on_input(event)` with `event.kind == "turn"` and `event.value` = detents
  (positive clockwise), or poll `gitmatrix.input.turns()` in `update()`.
- `BUTTON_*` don't exist, so `machine.Pin(BUTTON_A, …)` raises `NameError` at import and
  the plugin is quarantined. Map each button action to turning the knob, or drop it.

### Audio: microphone → Bluetooth speaker

Art Matrix's `mic` analyses a microphone on the SENS header. GitMatrix is always a
Bluetooth speaker called **GitMatrix**, and `mic` (also available as `audio` and
`gitmatrix.audio`) analyses the FFT of whatever a phone or computer streams to it.

| | Art Matrix | GitMatrix |
| --- | --- | --- |
| Source | SENS-header mic (room sound) | the A2DP stream (only while music plays) |
| `level()` … `spectrum()` | ✓ | ✓, same ranges, computed from the 512-bin FFT |
| `waveform()` | 64 points, -1..1 | 64 zeros: `bt_audio` exposes no PCM to Python |
| Extras | | `bass()`, `fft()` (raw bins), `streaming()`, `connected()`, `volume()`, `set_volume()`, `name` |
| Silence | quiet room | nothing streaming: everything reads 0, `active()` is `False` |

A visualiser should draw something when `audio.streaming()` is `False`, and can set
`"hold": "audio"` so it stays on screen while music plays. Plugins can't play sound.

### Settings, `config.html` and secrets

`plugin_config()` and `config.html` work unchanged, with three differences:

- The saved form goes to **`/data/<name>/config.json`**, not `/lib/plugins/<name>/config.json`,
  so it survives updates. A `config.json` shipped in the plugin folder is still read as a fallback.
- **No autosave.** Art Matrix's config page script saves `<form data-autosave>` on every
  change; GitMatrix wraps your HTML in its own page without that script. Add
  `<button type="submit">Save</button>` (harmless on Art Matrix).
- **No automatic "Reset to defaults".** Art Matrix adds one for plugins without
  `process_config`. Add your own: `<button type="submit" name="reset" value="1">Reset to defaults</button>`.

If Bluetooth audio is streaming when the form is posted, GitMatrix asks before saving
(the speaker disconnects for the write).

GitMatrix also offers a **settings schema** in `package.json`: the web UI builds the form,
values are typed and validated, `secret` fields (API tokens) are stored apart in
`/secrets/<name>.json` and never sent back to the browser, and `on_settings()` is called
after a change instead of reloading the plugin. Read them with `gitmatrix.settings()`.
A plugin can use either mechanism or both; see [plugin-api.md](plugin-api.md#settings-schema).

### State and files

Art Matrix plugins write wherever they like (flash or `/sd`). On GitMatrix:

- A flash write while the speaker streams can hang the board, so all of GitMatrix's own
  writes wait or ask first. Use `gitmatrix.state()` for anything a plugin wants to keep:
  it's saved to `/data/<name>/state.json`, throttled, and deferred while audio plays.
- There is no SD card. `/lib/plugins/<name>/` is replaced on update; keep data in `/data`.

### Hooks and lifecycle

Art Matrix calls only `update()` and `process_config(form)`. GitMatrix adds optional
`on_show()`, `on_hide()`, `on_input(event)`, `on_settings()` and `on_notify(data)`; a
plugin without them behaves as on Art Matrix.

| | Art Matrix | GitMatrix |
| --- | --- | --- |
| 5 failing `update()`s in a row | skipped until reboot | quarantined (persisted, `!` in the menu; select it to retry) |
| Import error | logged, plugin not loaded | quarantined |
| Hang | device stops | the 8 s watchdog reboots; the plugin is quarantined on the next boot |
| `requires_internet` | plugin only rotates while online | shown on Discover; the plugin rotates regardless, so handle being offline |
| Background work | `asyncio.create_task` | `gitmatrix.create_task(coro)` (tracked, cancelled on quarantine or unload) |
| Notifications | | `on_notify(data)` and `gitmatrix.notification` |

### Not available on GitMatrix

| Art Matrix | Why / instead |
| --- | --- |
| `BUTTON_A`/`B`/`C`/`USER` | one encoder: `"input": true` + `on_input` |
| `sensor.read_u16()` | no SENS port |
| Ambient LEDs (`display.set_ambient`) | no rear LEDs |
| `/sd` | no SD card |
| `qr_screen` | 7 rows can't show a QR code |
| `display.set_hold`, second surface | the host composites |
| `mic` from a microphone | Bluetooth audio only (above) |
| Playing sound | the speaker belongs to the runtime |
| `OFF` / `X2` / `X4` builtins | Art Matrix hoists them from `image`; use `image.X4` etc. if your firmware has them |

`free()` exists but prints bytes rather than Art Matrix's KB with a delta.

## Worked example

An Art Matrix spectrum bars plugin, `bars`: 16 bars from the mic, button A cycles the
gain, colour and peak markers set from `config.html`.

### Before (Art Matrix, 128×128)

```python
from machine import Pin
from plugin_config import plugin_config

cfg = plugin_config("bars", {"colour": (57, 211, 83), "peaks": True})
PEN = color.rgb(*cfg["colour"])
PEAK = color.rgb(255, 255, 255)
BLACK = color.rgb(0, 0, 0)
BAR_W = 8                                # 16 bands x 8 px = 128
button = Pin(BUTTON_A, Pin.IN, Pin.PULL_UP)
gain = 1.0
was_down = False
peaks = [0.0] * 16


def update():
    global gain, was_down
    down = not button.value()
    if down and not was_down:
        gain = 0.5 if gain >= 2 else gain * 2
    was_down = down

    screen.pen = BLACK
    screen.clear()
    for i, v in enumerate(mic.spectrum()):
        h = int(min(1, v * gain) * screen.height)
        screen.pen = PEN
        screen.rectangle(i * BAR_W, screen.height - h, BAR_W - 1, h)
        peaks[i] = max(peaks[i] * 0.97, h)
        if cfg["peaks"]:
            screen.pen = PEAK
            screen.rectangle(i * BAR_W, screen.height - int(peaks[i]) - 1, BAR_W - 1, 1)
```

```html
<form method="post" action="/config?plugin={{ plugin }}" data-autosave>
  <input type="color" name="colour" value="{{ '#%02x%02x%02x' % placeholder.cfg['colour'] }}">
  <label><input type="checkbox" name="peaks" {{ "checked" if placeholder.cfg['peaks'] else "" }}> Peaks</label>
</form>
```

### After (GitMatrix, 53×7)

```python
import gitmatrix
from plugin_config import plugin_config

cfg = plugin_config("bars", {"colour": (57, 211, 83), "peaks": True})   # unchanged
PEN = color.rgb(*cfg["colour"])
PEAK = color.rgb(255, 255, 255)
DIM = color.rgb(20, 60, 25)
BLACK = color.rgb(0, 0, 0)
BAR_W = 3                                # 16 bands x 3 px = 48 of 53
X0 = (screen.width - 16 * BAR_W) // 2    # centre them
gain = 1.0
peaks = [0.0] * 16


def on_input(event):                     # needs "input": true in package.json
    global gain
    gain = clamp(gain * 1.25 ** event.value, 0.25, 8)


def update():
    screen.pen = BLACK
    screen.clear()
    if not gitmatrix.audio.streaming():  # nothing playing: draw a resting line
        screen.pen = DIM
        screen.rectangle(X0, screen.height - 1, 16 * BAR_W - 1, 1)
        return
    for i, v in enumerate(mic.spectrum()):
        h = int(min(1, v * gain) * screen.height)
        x = X0 + i * BAR_W
        screen.pen = PEN
        screen.rectangle(x, screen.height - h, BAR_W - 1, h)
        peaks[i] = max(peaks[i] * 0.97, h)
        if cfg["peaks"]:
            screen.pen = PEAK
            screen.rectangle(x, screen.height - int(peaks[i]) - 1, BAR_W - 1, 1)
```

```html
<form method="post" action="/config?plugin={{ plugin }}" data-autosave>
  <input type="color" name="colour" value="{{ '#%02x%02x%02x' % placeholder.cfg['colour'] }}">
  <label><input type="checkbox" name="peaks" {{ "checked" if placeholder.cfg['peaks'] else "" }}> Peaks</label>
  <button type="submit">Save</button>
  <button type="submit" name="reset" value="1">Reset to defaults</button>
</form>
```

```json
{
  "name": "Bars",
  "version": "1.1.0",
  "description": "Spectrum bars for whatever is playing",
  "author": "you",
  "category": "Visualisations",
  "requires_internet": false,
  "input": true,
  "hold": "audio"
}
```

What changed:

- `machine.Pin(BUTTON_A)` and the edge detection became `on_input()`; the knob now adjusts
  the gain smoothly. `"input": true` stops turns from stepping plugins (a press still
  opens the menu).
- `BAR_W` 8 → 3 and an `X0` offset; heights already used `screen.height`.
- An idle state for when nothing streams, and `"hold": "audio"` to stay on screen while
  music plays.
- `config.html` gained Save and Reset buttons; `plugin_config()` is untouched and now reads
  `/data/bars/config.json`.
- `mic.spectrum()` is unchanged.

Optionally, replace `plugin_config()` and `config.html` with a schema, which gives typed
values and a form for free:

```json
"settings": [
  {"key": "colour", "type": "color", "label": "Colour", "default": "#39d353"},
  {"key": "peaks", "type": "bool", "label": "Peaks", "default": true}
]
```

```python
def on_settings():                       # instead of being reloaded
    global PEN, show_peaks
    s = gitmatrix.settings()
    PEN = color.rgb(int(s.colour[1:3], 16), int(s.colour[3:5], 16), int(s.colour[5:7], 16))
    show_peaks = s.peaks
```

## Porting back to Art Matrix

A GitMatrix plugin runs on Art Matrix if it avoids the GitMatrix-only parts or guards them:

```python
try:
    import gitmatrix
except ImportError:          # Art Matrix
    gitmatrix = None
```

Hooks Art Matrix doesn't call (`on_show`, `on_input`, …) are simply ignored there, so
initialise in top-level code or lazily in `update()`. A settings schema isn't read by
Art Matrix, so use `plugin_config()` and `config.html` (with a Save button, which also
works there) for settings that must work on both. Draw relative to `screen.width` and
`screen.height` and the layout scales up.
