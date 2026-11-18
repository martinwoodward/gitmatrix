# GitMatrix plugin API

GitMatrix plugins follow the [Art Matrix](https://github.com/pimoroni/artmatrix) plugin
model, so most Art Matrix plugins port with only small changes. This page covers what
is supported, what is aliased, and what GitMatrix adds. To move an existing Art Matrix
plugin across, follow the [porting guide](porting.md); to start a new one, copy the
[plugin template](../../gitmatrix/templates/plugin/README.md). For the design, see
[plan.md](plan.md), and for all the docs, the [index](README.md).

## Anatomy of a plugin

```
/lib/plugins/<name>/
    __init__.py      # the plugin module
    package.json     # manifest (optional but recommended; required in the store)
    config.html      # optional Art Matrix-style settings page
    thumbnail.png    # 53x7 preview for the Discover page (store only)
```

Data a plugin saves lives outside its folder, so it survives updates:
`/data/<name>/` (state, settings, `config.json`) and `/secrets/<name>.json` (secret settings).

`<name>` must be a valid Python identifier. A plugin found on disk that isn't listed
in `config.py` is added switched off, and you can turn it on from **Menu → Plugins**.

### Hooks

Every hook is optional and called with no arguments, except `on_input` and `on_notify`.

| Hook | When |
| --- | --- |
| module import | first load (top-level code runs once) |
| `update()` | every frame while the plugin is on screen (≈60 fps, draw into `screen`) |
| `on_show()` | the plugin is about to be shown (including returning from the menu) |
| `on_hide()` | the plugin leaves the screen (rotation, menu opened) |
| `on_input(event)` | only for `"input": true` plugins: an encoder turn (`event.kind == "turn"`, `event.value` = detents ±) |
| `process_config(form)` | Art Matrix style: the plugin's `config.html` form was posted (a dict of strings); save what you need |
| `on_settings()` | the schema settings changed in the web UI (otherwise the plugin is reloaded) |
| `on_notify(data)` | a notification forced this plugin on screen (after `on_show()`); `data` is the request's JSON `data` (or `None`). See [Notifications](#notifications) |

Plugins draw with picovector into `screen`, a 53×7 image owned by the plugin.
`display.update()` inside a plugin does nothing: the runtime composites, handles
transitions and the menu, and presents the frame.

### `package.json`

The Art Matrix fields, plus GitMatrix's optional `settings`, `input`, `hold`,
`min_firmware` and `tags`. In the plugin repo, `ci/generate_packages.py` fills in
`hash` and `urls`.

```json
{
  "name": "JSON Feed",
  "version": "1.0.0",
  "description": "Scrolls a value from any JSON URL",
  "author": "martinwoodward",
  "category": "Information",
  "requires_internet": true,
  "tags": ["json", "api"],
  "min_firmware": "0.1.0",
  "input": false,
  "hold": false,
  "settings": [
    {"key": "url", "type": "url", "label": "Feed URL", "default": "https://api.github.com/zen"},
    {"key": "token", "type": "secret", "label": "Bearer token"},
    {"key": "refresh", "type": "int", "label": "Refresh (s)", "min": 30, "max": 86400, "default": 300},
    {"key": "colour", "type": "color", "label": "Colour", "default": "#39d353"},
    {"key": "mode", "type": "select", "label": "Mode", "options": ["scroll", ["static", "Static"]], "default": "scroll"},
    {"key": "animate", "type": "bool", "label": "Animate", "default": true}
  ],
  "hash": "3f2a9c01b7de",
  "urls": [["plugins/json_feed/__init__.py", "__init__.py"]]
}
```

| Field | Meaning |
| --- | --- |
| `name` | title shown in the menu, web UI and Discover (defaults to the directory name) |
| `version`, `description`, `author` | shown on Discover; bump `version` for each release |
| `category` | `Information`, `Visualisations`, `Scenes`, `Interactive` or `3D` (the Discover filter) |
| `requires_internet` | shown on Discover |
| `hash`, `urls` | written by `ci/generate_packages.py`: the package hash the device verifies, and `[repo path, path in the plugin]` for every file |
| `tags` | GitMatrix: extra search words |
| `min_firmware` | GitMatrix: oldest firmware that can run it (`"0.1.0"`, compared with `gitmatrix.VERSION`); older devices can't install it |
| `input` | `true`: encoder turns go to the plugin instead of stepping plugins. A press still opens the menu. |
| `hold` | `true`: never auto-rotate away. `"audio"`: stay on screen while music plays. |
| `settings` | GitMatrix: the settings schema below |

### Settings schema

Each field has `key`, `type`, `label` and optional `default`, `help` and `placeholder`.
The web UI's plugin page (`/plugin.html?name=<plugin>`) renders a form from it.

| `type` | Input | Extra keys |
| --- | --- | --- |
| `text` (`string`) | text box, ≤ 512 characters | |
| `url` | must start with `http://` or `https://` | |
| `secret` | password box; never sent back to the browser or API | |
| `number` (`float`), `int` | number box, clamped to the range | `min`, `max`, `step` |
| `color` | colour picker, stored as `#rrggbb` | |
| `select` (`choice`) | drop-down | `options`: values, `[value, label]` pairs or `{"value", "label"}` |
| `bool` | checkbox | |

- Values are saved to `/data/<name>/settings.json`; `secret` values to `/secrets/<name>.json`.
- Secrets are write-only: `GET /api/v1/plugins/<name>/settings` returns `"set": true/false`
  for each secret and leaves them out of `values`. Posting `""` clears a secret; leaving it
  out keeps it. They're stored unencrypted, so anyone with USB access can read them.
- In the plugin, `gitmatrix.settings()` returns the typed values, secrets included.
- After a save the plugin's `on_settings()` is called, or the plugin is reloaded if it has none.

### `config.html` (Art Matrix style)

A plugin can ship its own `config.html` instead of, or as well as, a schema. It's served at
`/config?plugin=<name>` inside the GitMatrix page frame. `{{ plugin }}` is the plugin's
name and `{{ placeholder.X }}` is attribute `X` of the plugin module. The form posts back
to the same URL. With `process_config(form)` the plugin handles the form, otherwise it's
saved to `/data/<name>/config.json` for `plugin_config()`. A `reset` field deletes it.

As on Art Matrix, a `<form data-autosave>` saves on every change (no Save button needed),
and plugins without `process_config` get a "Reset to defaults" button under the form.
GitMatrix's autosave waits for changes to settle (0.6 s) so a slider drag makes one write,
skips rewriting an unchanged `config.json`, and asks before saving while audio streams.

### Flash writes and Bluetooth audio

Writing to flash while the speaker streams can hang the board, so GitMatrix never does.
`state.save()` waits until playback stops. `settings.update()` and other file writes
through GitMatrix raise `FlashBusy` (an `OSError`) while it streams, so check
`gitmatrix.audio.streaming()` first, and don't write files directly from plugin code. Saving settings, `config.html` forms, installs and removals from the web UI ask
first: the speaker disconnects for the write and your phone or computer needs to reconnect.
Installs can instead wait until playback stops.

## Compatibility with Art Matrix

### Supported as-is

| API | Notes |
| --- | --- |
| picovector globals (`image`, `color`, `brush`, `shape`, `vec2`, `rect`, `mat3`, …) | from the stock 1.29 firmware |
| `screen` | 53×7, per-plugin surface |
| `display.update()` | no-op inside plugins; the host presents the frame |
| `plugin_config(name, defaults)` | reads `/data/<name>/config.json` (else `/lib/plugins/<name>/config.json`), coerced to the default's type (bool = key present; tuple = `#rrggbb`) |
| `config.html` + `process_config(form)` | served at `/config?plugin=<name>`; `<form data-autosave>` and the automatic "Reset to defaults" button as on Art Matrix |
| `fetch.AsyncFetch` | vendored from Art Matrix (TLS with `CERT_NONE`, as Art Matrix) |
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
| `requires_internet` | shown on Discover only; the plugin rotates even when offline (Art Matrix skips it) |
| `sensor`, `/sd`, `qr_screen`, `display.set_ambient` | not present |
| Plugin install path | GitMatrix installs to the same `/lib/plugins/<name>/`, but writes Art Matrix plugins' `config.json` to `/data/<name>/` so it survives updates |

## GitMatrix additions

```python
import gitmatrix

st = gitmatrix.state()          # dict-like, saved to /data/<plugin>/state.json
st["best"] = max(st.get("best", 0), score)
st.save()                       # throttled (≥30 s apart, waits until music stops streaming)
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
gitmatrix.reboot()              # saves, then reboots via the watchdog (never machine.reset());
                                # warm resets can hang the board until replugged, so avoid it
gitmatrix.WIDTH, gitmatrix.HEIGHT   # 53, 7
```

`gitmatrix.state()` and `gitmatrix.settings()` work out the calling plugin automatically
inside hooks. Pass a name to use them anywhere else.

### Notifications

A notification can force a plugin on screen and hand it data:

```sh
curl -X POST http://gitmatrix.local/api/v1/notify -H 'Content-Type: application/json' \
  -d '{"plugin": "my_plugin", "data": {"status": "failed", "repo": "octo/app"}, "duration": 15}'
```

Any installed plugin can be forced, even one that is switched off (it is loaded for the
notification and unloaded again afterwards); a quarantined plugin can't (409). The
runtime saves what was on screen, calls `on_show()`, sets `gitmatrix.notification` to
`data`, calls `on_notify(data)`, and keeps calling `update()` for `duration` seconds
(default 10, max 60). Then `on_hide()` runs, `gitmatrix.notification` goes back to
`None` and the previous plugin, its rotation timer and payload are restored. A knob press
dismisses it early.

```python
import gitmatrix

alert = None

def on_notify(data):
    global alert
    alert = data            # the same object as gitmatrix.notification

def update():
    if gitmatrix.notification:      # shown by a notification
        ...                         # draw from gitmatrix.notification["status"]
    else:
        ...                         # normal rotation
```

`data` is whatever JSON the sender gave (any type), so check it before use. Errors in
`on_notify` count towards quarantine like any other hook.

## Fault containment

Plugins are trusted, cooperative code, not sandboxed. The runtime contains their failures:

- **Import error** → the plugin is quarantined at once. It's marked `!` in Menu → Plugins and selecting it retries.
- **Exception in a hook** → logged and the frame is skipped. After **5 in a row** the plugin is quarantined
  and the next plugin shown ("<name> stopped"). Quarantine survives reboots (`config.quarantined`).
- **Hang** (no frame for 8 s) → the hardware watchdog reboots the device. A scratch-register marker
  records which plugin and hook was running, so on the next boot that plugin is quarantined and a message is shown.
  Warm resets occasionally hang this board until it's unplugged ([phase0.md](phase0.md)), so the watchdog is
  a last resort: if the widget stays dark, replug it.
- **Boot loop** (3 boots without reaching 60 s healthy uptime), or the knob **held at power-on** → safe mode:
  no plugins load, and the menu works. Choosing a plugin from the menu leaves safe mode.
- `update()` slower than 50 ms is logged once as a warning.
- The Bluetooth speaker belongs to the runtime and keeps playing whatever a plugin does.
