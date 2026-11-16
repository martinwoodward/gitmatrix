# ghcontrib

Code and firmware backup for the **GitHub Contribution Widget**, a prototype Pimoroni board
with a 53×7 RGB LED matrix: one pixel per day for a year of GitHub contributions
(53 weeks × 7 days).

Everything here was pulled from the device on 2026-11-16.

## Hardware

| | |
|---|---|
| MCU | RP2350 (rev A4, QFN60), Arm cores, 250 MHz |
| RAM | ~8 MB PSRAM available to MicroPython |
| Flash | 16 MB |
| Display | 53 × 7 RGB LED matrix (shift-register driven) |
| Input | Rotary encoder (GPIO 22 = A, GPIO 21 = B) with push button (`board.SWITCH`, GPIO 20, active low, needs pull-up) |
| Wireless | WiFi + Bluetooth (CYW43, pins `WL_*`) |
| Audio | I2S output with amplifier (`I2S_*`, `AMP_EN`) |
| Other | Light sensor (`LIGHT_SENSE`), logo/power/connection/text LEDs, Qw/ST I2C connector |
| USB | USB-C, enumerates as `MicroPython Board in FS mode` (VID `0x2E8A`, PID `0x0005`) |

Pin names are available on the device via `import board; dir(board)`.

## What's installed

### Firmware: `firmware/`

| File | Contents |
|---|---|
| `contribution_widget-micropython-1.27.uf2` | MicroPython 1.27.0 (branch `feature/basepri`, built 2026-03-13, Pico SDK 2.1.1, board `pimoroni_contribution_widget`). Flash range `0x10000000–0x10125a44`. |
| `romfs.uf2` | Read-only ROMFS at `0x10200000–0x10300000`, mounted at `/rom`. Contains the 37 `.ppf` fonts. |

The read/write user filesystem (`0x10300000–0x11000000`, 13 MB) was **not** dumped
because it contains `secrets.py`. Its non-secret contents are in `device/`.

Frozen modules in the firmware include `contribution_widget` (display driver and
graphics globals), `picovector`, `easing`, `encoder`, `ezwifi`, `requests`/`urequests`,
`ntptime`, `aioble`, `umqtt.simple`, `qrcode`, `ulab`, `adcfft`, the Pimoroni `breakout_*`
drivers, and `qwstpad`.

### Device filesystem: `device/`

| File | Purpose |
|---|---|
| `main.py` | Boot app. **Boots into a carousel of widget apps** that switches to the next app every `APP_INTERVAL_S` seconds with a slide transition. Turn the wheel to jump to the next or previous app. Press the encoder button for the menu: **Apps** (pick an app to jump to it), **Brightness** (turn to adjust, press to save to `/settings.json`), **WiFi** (shows network, IP and signal), **Update** (refreshes all data now), **< Back**. Volume and Test show "Soon!". The menu returns to the carousel after 30 s of no input. |
| `widgets.py` | The carousel apps. Each draws a blurred glow pass (drifting ambient blobs, haloed text) under a crisp pass. **Graph:** 53 weeks of contributions (brighter green = more) that sweep in, with each square gently throbbing at its own pace, random squares twinkling, a band of light passing over and today pulsing. **Clock:** large digits that roll when they change, a blinking colon, a comet marking the seconds and slowly drifting colour; shows `--:--` until NTP time syncs. **Stars / Followers / X / YouTube:** pixel icon that bounces in and a number that counts up, with sparkles; numbers are abbreviated (`20.3K`) when they don't fit or are approximate. |
| `datasource.py` | Background data fetching. Connects to WiFi without blocking and streams each request a chunk per frame so animations keep running. Values are cached in `/cache.json` (contributions in `/contrib_data.json`). See [Data sources](#data-sources). |
| `config.py` | Non-secret settings: app list and order, rotation interval, timezone, 12/24 hour clock, repo for stars, X handle, YouTube handle. |
| `backup/` | The original demo apps (`gol.py`, `fast_fire.py`, `spiral.py`, `rainbow.py`, `rainbow_wheel.py`, `balls_demo.py`). Not part of the menu any more, but can still be run with `mpremote` (see below). |
| `contrib_data.json` | Sample contribution graph (schema `v2`: `weeks[53].contribution_days[7]` with `count`/`level`, `colors_full`, `months`, `total_contributions`). Same format as `https://github.com/<user>.contribs`; the **Update** menu item replaces it with your own data. |
| `user_data.json` | Sample GitHub `/users/<login>` API response (@Gadgetoid) |
| `rom/fonts/*.ppf` | Copy of the ROMFS fonts, available on the device as `rom_font.<name>` |
| `secrets.example.py` | Template for `secrets.py` (see below) |

## Data sources

| App | Source | Refresh |
|---|---|---|
| Graph | `https://github.com/<GITHUB_USERNAME>.contribs` | 24 h |
| Clock | NTP (`ntptime`), shown at `TIMEZONE_OFFSET_HOURS` from UTC | 6 h |
| Stars | GitHub REST API `repos/<STARS_REPO>` | 1 h |
| Followers | GitHub REST API `users/<GITHUB_USERNAME>` | 1 h |
| X | [FxTwitter](https://github.com/FixTweet/FxTwitter) public API `api.fxtwitter.com/<X_HANDLE>` (X has no free official API) | 1 h |
| YouTube | YouTube Data API if `YOUTUBE_API_KEY` is set; otherwise the public `m.youtube.com/<YOUTUBE_HANDLE>` page (~1 MB streamed over ~10 s, abbreviated count like `711K`) | 6 h |

All sources fetch at boot. Failed fetches retry after 15 minutes, and the last good value
stays on screen in the meantime. Caveats:

- The timezone is a fixed offset, so change `TIMEZONE_OFFSET_HOURS` for daylight saving.
- Unauthenticated GitHub API calls are limited to 60/hour per IP; set `GITHUB_TOKEN` if you share the IP with other tools.
- FxTwitter is a third-party service and occasionally fails; the cached value is shown until it recovers.

## Setup

### 1. Tools

```sh
pip install mpremote   # or: brew install mpremote
brew install picotool  # only needed for firmware backup/restore
```

The board appears as `/dev/cu.usbmodem*` on macOS. `mpremote` finds it automatically.

### 2. WiFi and GitHub config

Create `secrets.py` from the template and copy it to the device:

```sh
cp device/secrets.example.py device/secrets.py   # git-ignored
# edit WIFI_SSID, WIFI_PASSWORD, GITHUB_USERNAME (and optional tokens)
mpremote fs cp device/secrets.py :secrets.py
```

Then edit `device/config.py` for your timezone, clock format, apps, rotation interval,
repo, X handle and YouTube channel, and copy it over with `mpremote fs cp device/config.py :`.

`ezwifi` reads `WIFI_SSID`/`WIFI_PASSWORD` from `secrets.py`:

```python
import ezwifi
ezwifi.connect()
```

### 3. Deploy apps

```sh
mpremote fs cp device/widgets.py :       # one file
cd device && mpremote fs cp *.py :        # everything
mpremote reset                            # reboot into main.py
```

### 4. Run an app without installing it

Apps rely on globals (`screen`, `display`, `color`, `rom_font`, `vec2`, `shape`,
`image`, `mat3`...) that are set up when `contribution_widget` is imported:

```sh
mpremote exec "import contribution_widget" run device/backup/gol.py
```

### 5. Preview frames on your computer

`tools/snap.py` runs drawing code on the device and saves the framebuffer as a PNG of
round LEDs, which is handy for checking visuals:

```sh
python3 tools/snap.py /tmp/frame.png "screen.pen = color.rgb(0,255,0); screen.text('Hi', vec2(0,0))"
```

## Drawing API, quick reference

```python
import contribution_widget  # sets up the globals

screen.pen = color.rgb(0, 0, 0)
screen.clear()
screen.font = rom_font.sins
screen.pen = color.oklch(200, 255, 120)   # lightness, chroma, hue[, alpha]
screen.text("Hello", vec2(2, 0))
screen.line(0, 6, 52, 6)
display.set_brightness(0.5)                # also get_brightness / adjust_brightness
display.update()
```

Rotary encoder:

```python
from encoder import Encoder
enc = Encoder(1, 0, (22, 21))   # PIO 1, SM 0, pins A/B
enc.delta()
```

## Restoring or reflashing firmware

1. Enter BOOTSEL: `mpremote exec "import machine; machine.bootloader()"`
   (or hold BOOT while plugging in).
2. Flash:
   ```sh
   picotool load -x firmware/contribution_widget-micropython-1.27.uf2
   picotool load firmware/romfs.uf2 && picotool reboot
   ```

Backups were taken with `picotool save -p` (program) and
`picotool save -r 0x10200000 0x10300000` (ROMFS).
