# GitMatrix plugin API

GitMatrix plugins follow the [Art Matrix](https://github.com/pimoroni/artmatrix) plugin
model, so most Art Matrix plugins port with only small changes. This page covers what
is supported, what is aliased, and what GitMatrix adds. For the design, see [plan.md](plan.md).

## Anatomy of a plugin

```
/lib/plugins/<name>/
    __init__.py      # the plugin module
    package.json     # manifest (optional but recommended)
    config.json      # Art Matrix-style plugin_config() values (optional)
```

`<name>` must be a valid Python identifier. A plugin found on disk that isn't listed
in `config.py` is added switched off, and you can turn it on from **Menu → Plugins**.

### Hooks

Every hook is optional and called with no arguments, except `on_input`.

| Hook | When |
| --- | --- |
| module import | first load (top-level code runs once) |
| `update()` | every frame while the plugin is on screen (≈60 fps, draw into `screen`) |
| `on_show()` | the plugin is about to be shown (including returning from the menu) |
| `on_hide()` | the plugin leaves the screen (rotation, menu opened) |
| `on_input(event)` | only for `"input": true` plugins: an encoder turn (`event.kind == "turn"`, `event.value` = detents ±) |
| `process_config()` | reserved: called after the web UI saves settings (later phase) |
| `on_notify(n)` | reserved: alerts (phase 2) |

Plugins draw with picovector into `screen`, a 53×7 image owned by the plugin.
`display.update()` inside a plugin does nothing: the runtime composites, handles
transitions and the menu, and presents the frame.

### `package.json`

```json
{
  "name": "Spectrum",
  "description": "Bars that dance to whatever is playing",
  "input": false,
  "hold": "audio",
  "settings": [
    {"key": "hue", "type": "int", "min": 0, "max": 360, "default": 200, "label": "Colour"},
    {"key": "mode", "type": "choice", "choices": ["bars", ["dots", "Dots"]], "default": "bars"},
    {"key": "token", "type": "secret", "label": "API token"}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `name` | label shown in the Plugins menu (defaults to the directory name) |
| `input` | `true`: encoder turns go to the plugin instead of stepping plugins. A press still opens the menu. |
| `hold` | `true`: never auto-rotate away. `"audio"`: stay on screen while music plays. |
| `settings` | typed settings schema; types `string`, `int`, `number`/`float`, `bool`, `choice`, `secret` with optional `min`, `max`, `default` |

## Compatibility with Art Matrix

### Supported as-is

| API | Notes |
| --- | --- |
| picovector globals (`image`, `color`, `brush`, `shape`, `vec2`, `rect`, `mat3`, …) | from the stock 1.29 firmware |
| `screen` | 53×7, per-plugin surface |
| `display.update()` | no-op inside plugins; the host presents the frame |
| `plugin_config(name, defaults)` | reads `/lib/plugins/<name>/config.json`, coerced to the default's type (bool = key present; tuple = `#rrggbb`) |
| `scrolling.Scroll` | `update(progress)`, `render(offset)`, `reset()`, `set_text()` |
| `mic.level()`, `tone()`, `flux()`, `intensity()`, `active()`, `bpm()`, `phase()`, `beat()`, `kick()`, `clap()`, `spectrum()` (16 bands) | driven by the Bluetooth speaker's FFT rather than a microphone |
| `clamp`, `rnd`, `frnd`, `free`, `file_exists`, `is_dir`, `UID` | builtins |
| `create_task(coro)` | via `gitmatrix.create_task`; errors count against the plugin, and tasks are cancelled on quarantine |

### Aliased

| Art Matrix | GitMatrix |
| --- | --- |
| `rom_font` | `font` (the 1.29 firmware's global, e.g. `font.sins`) |
| `import artmatrix` | `gitmatrix` (the same module) |
| `mic` | `audio` (the same object) |
| `import contribution_widget` | already imported by `gitmatrix` and harmless to repeat |

### Unsupported or different

| API | Status |
| --- | --- |
| `mic.waveform()` | returns zeros: `bt_audio` exposes no PCM to Python |
| Microphone input | none: the audio source is whatever is streaming to the "GitMatrix" speaker |
| Audio playback from plugins | not available (`bt_audio` has no PCM output) |
| Art Matrix buttons (`BUTTON_*`), LEDs, light sensor | not present: use the encoder (`"input": true`) instead |
| Larger display / `set_hold` / second surface | the display is 53×7; the host handles double buffering |
| Web UI `config.html` | planned for a later phase; `plugin_config()` already reads `config.json` |

## GitMatrix additions

```python
import gitmatrix

st = gitmatrix.state()          # dict-like, saved to /data/<plugin>/state.json
st["best"] = max(st.get("best", 0), score)
st.save()                       # throttled (≥30 s apart, deferred while music streams)
st.save(now=True)               # write immediately

cfg = gitmatrix.settings()      # typed values from the package.json schema
cfg.hue, cfg["mode"], cfg.get("token")   # secrets live in /secrets/<plugin>.json
cfg.update({"hue": 120})        # validated, clamped, persisted

gitmatrix.audio.bass()          # 0-1 low-end energy
gitmatrix.audio.fft()           # the raw 512 FFT bins
gitmatrix.audio.streaming()     # music is playing to the speaker
gitmatrix.audio.connected()
gitmatrix.audio.volume()        # 0-1 (the knob menu sets this)
gitmatrix.audio.name            # "GitMatrix"

gitmatrix.input.turns()         # input plugins: detents since the last call
gitmatrix.input.events()        # input plugins: new events since the last call

gitmatrix.show_message("Saved!")
gitmatrix.reboot()              # saves, then reboots via the watchdog (never machine.reset())
gitmatrix.WIDTH, gitmatrix.HEIGHT   # 53, 7
```

`gitmatrix.state()` and `gitmatrix.settings()` work out the calling plugin automatically
inside hooks. Pass a name to use them anywhere else.

## Fault containment

Plugins are trusted, cooperative code, not sandboxed. The runtime contains their failures:

- **Import error** → the plugin is quarantined at once. It's marked `!` in Menu → Plugins and selecting it retries.
- **Exception in a hook** → logged and the frame is skipped. After **5 in a row** the plugin is quarantined
  and the next plugin shown ("<name> stopped"). Quarantine survives reboots (`config.quarantined`).
- **Hang** (no frame for 8 s) → the hardware watchdog reboots the device. A scratch-register marker
  records which plugin and hook was running, so on the next boot that plugin is quarantined and a message is shown.
- **Boot loop** (3 boots without reaching 60 s healthy uptime), or the knob **held at power-on** → safe mode:
  no plugins load, and the menu works. Choosing a plugin from the menu leaves safe mode.
- `update()` slower than 50 ms is logged once as a warning.
- The Bluetooth speaker belongs to the runtime and keeps playing whatever a plugin does.
