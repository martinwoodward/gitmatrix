# GitMatrix

Plugin firmware for Pimoroni's **GitHub Contribution Widget**: a 53×7 RGB LED matrix
(one pixel per day for a year of contributions) with a rotary encoder, a speaker, WiFi
and Bluetooth, built on an RP2350.

GitMatrix is modelled on Pimoroni's [Art Matrix](https://github.com/pimoroni/artmatrix),
so Art Matrix plugins are easy to port and the environment will feel familiar. On top of
that it adds:

- **Plugins from a store**: browse, search and install plugins from
  [martinwoodward/gitmatrix-plugins](https://github.com/martinwoodward/gitmatrix-plugins)
  in the device's web UI. They download straight onto the widget.
- **Safe plugins**: a plugin that crashes or hangs is stopped and quarantined; the
  display, Bluetooth audio and web server keep running.
- **Always a Bluetooth speaker** called **GitMatrix**, whatever plugin is showing.
  Plugins can animate to the music through a spectrum and beat API.
- **Notifications**: send text, a scrolling PNG or a sound to a webhook on the device, or
  have it show a plugin with a JSON payload. They interrupt the current plugin, then it
  carries on.
- **The knob**: turn to change plugin; press for a snappy menu with Brightness, Volume,
  WiFi, Bluetooth and Alerts.
- **Easy WiFi setup**: with no network saved, the widget starts an access point called
  **GitMatrix** with a captive portal where you can scan for networks and join one.
- **REST API and MQTT** with Home Assistant discovery: control plugins, brightness and
  volume, send notifications, play sounds and react to the knob.

## Getting started

1. Flash the stock firmware,
   [`firmware/contribution_widget-micropython-1.29-with-filesystem.uf2`](firmware/contribution_widget-micropython-1.29-with-filesystem.uf2)
   (hold BOOT while plugging in, then copy the UF2 to the drive that appears).
2. Install GitMatrix over USB:
   ```sh
   pip install mpremote
   python3 gitmatrix/tools/deploy.py --reset
   ```
3. Join the **GitMatrix** WiFi network (password `monalisa`), pick your network in the
   page that opens and save. The widget joins it without rebooting.
4. Press the knob and choose **WiFi** to see the address, then open
   `http://gitmatrix.local` to install plugins and change settings.
5. Pair your phone or computer with the **GitMatrix** Bluetooth speaker.

Send a notification:

```sh
curl -X POST http://gitmatrix.local/api/v1/notify -H 'Content-Type: application/json' \
  -d '{"text": "Build failed on main", "sound": "error", "priority": "high"}'
```

## Documentation

| | |
|---|---|
| [gitmatrix/README.md](gitmatrix/README.md) | Deploying, WiFi and the web UI, notifications and sounds, the plugin store, MQTT, development |
| [Plugin API](docs/gitmatrix/plugin-api.md) | Writing plugins: hooks, settings and secrets, state, audio, notifications |
| [Porting from Art Matrix](docs/gitmatrix/porting.md) | Every difference, with a worked example |
| [Plugin template](gitmatrix/templates/plugin/README.md) | A starter plugin to copy |
| [REST API, events and MQTT](docs/gitmatrix/api.md) | The `/api/v1/` reference and Home Assistant topics |
| [Design](docs/gitmatrix/plan.md) | Architecture and how each part was built |
| [On-device results](docs/gitmatrix/ondevice.md) | What has been tested on real hardware, and known issues |
| [The widget](docs/widget.md) | Hardware, the stock firmware, and the original contribution graph apps |

## Repository layout

| Path | |
|---|---|
| `gitmatrix/` | The GitMatrix firmware: runtime, web UI, built-in plugins and sounds, tests and tools |
| `docs/` | Documentation |
| `firmware/` | Stock MicroPython 1.29 firmware for the widget and its starter filesystem |
| `device/` | The original contribution graph app carousel (not GitMatrix) |
| `tools/` | Helpers for the stock firmware: UF2 unpacking and framebuffer snapshots |

## Development

```sh
cd gitmatrix
python3 -m pytest
ruff check .
```

## License

GitMatrix is released under the [MIT License](LICENSE), the same license Pimoroni uses
for Art Matrix's bundled code and its other MicroPython projects. Vendored and
third-party components keep their own licenses, for example
[phew](gitmatrix/modules/python/phew/LICENSE). The firmware images in `firmware/` are
Pimoroni's.
