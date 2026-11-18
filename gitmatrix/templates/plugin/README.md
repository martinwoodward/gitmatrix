# GitMatrix plugin template

A minimal, working GitMatrix plugin: a dot bouncing along the 53×7 display. It shows the
hooks most plugins need (`update`, `on_show`, `on_settings`, `on_input`), a settings
schema, persistent state and a store thumbnail. The host test harness loads it and runs it
(`gitmatrix/tests/test_template.py`), so it stays in step with the runtime.

```
my_plugin/
    __init__.py      # the plugin
    package.json     # manifest + settings schema
    thumbnail.png    # 53x7 preview for Discover (required by the plugins repo CI)
```

## Make it yours

1. Copy `my_plugin/` and rename the folder. The name must be a Python identifier
   (`[A-Za-z_][A-Za-z0-9_]*`); it's the install path (`/lib/plugins/<name>/`) and the
   key for `/data/<name>/` and `/secrets/<name>.json`.
2. Set `NAME` at the top of `__init__.py` to the folder name.
3. Edit `package.json`: `name`, `description` (≤ 60 characters on Discover), `author`,
   `category` (`Information`, `Visualisations`, `Scenes`, `Interactive` or `3D`),
   `requires_internet`, `tags` (≤ 10) and the `settings` schema. Remove `"input": true`
   if the knob should keep stepping plugins. Don't add `hash` or `urls`; the plugins
   repo CI writes them.
4. Replace `thumbnail.png` with a 53×7 PNG of your plugin.

## Try it on a device

Stop any Bluetooth audio, copy the folder to `/lib/plugins/<name>/` (for example
`mpremote cp -r my_plugin :/lib/plugins/`), then unplug and replug the widget. Prefer
a replug to `mpremote reset`: warm resets can occasionally hang this board until it is
power-cycled ([phase0.md](../../../docs/gitmatrix/phase0.md)). A plugin found on disk that
isn't in `config.py` is added switched off, so turn it on in **Menu → Plugins**.

To iterate without the device, copy it into a test like
[`tests/test_template.py`](../../tests/test_template.py) and run it under the host harness.

## Publish it

Open a pull request adding the folder at the top level of
[martinwoodward/gitmatrix-plugins](https://github.com/martinwoodward/gitmatrix-plugins).
Its CI validates the manifest, schema and thumbnail and regenerates `index.json`.

## More

- [Plugin API](../../../docs/gitmatrix/plugin-api.md): hooks, `package.json`, settings, state, audio, notifications.
- [Porting from Art Matrix](../../../docs/gitmatrix/porting.md): what changes on a 53×7 display with a rotary encoder.
