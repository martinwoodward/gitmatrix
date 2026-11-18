# The GitHub Contribution Widget: hardware, stock firmware and original apps

Reference for the **GitHub Contribution Widget**, a prototype Pimoroni board
with a 53×7 RGB LED matrix: one pixel per day for a year of GitHub contributions
(53 weeks × 7 days).

The **authoritative firmware** is
[`firmware/contribution_widget-micropython-1.29-with-filesystem.uf2`](../firmware/contribution_widget-micropython-1.29-with-filesystem.uf2)
(MicroPython 1.29, received 2026-11-17). GitMatrix targets it ([README](../README.md)). The apps in `device/`
were originally pulled from a device running the previous 1.27 build on 2026-11-16 and
have been ported to 1.29 (see
[Changes from the 1.27 firmware](#changes-from-the-127-firmware)); they no longer run on 1.27.

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

`contribution_widget-micropython-1.29-with-filesystem.uf2` (3.8 MB, SHA-256
`986c29d7…69e714`) is a single image containing firmware, fonts and a starter filesystem:

| Flash range | Size | Contents |
|---|---|---|
| `0x10000000–0x10154a00` | 1.3 MB | MicroPython `v1.29.0-4.g2429603c55.dirty`, Pico SDK 2.3.0, built 2026-09-15 (MinSizeRel), board `pimoroni_contribution_widget`, RP2350 ARM Secure |
| `0x10200000–0x10266700` | 409 KB | Read-only ROMFS, mounted at `/rom`. The same 37 `.ppf` fonts as before (byte-identical to `device/rom/fonts/`) |
| `0x10300000–0x10316000` | 88 KB | LittleFS v2.1 user filesystem (4 KB blocks, formatted with a block count of 22). Unpacked into [`firmware/filesystem/`](../firmware/filesystem) |

Flashing this UF2 **overwrites the start of the user filesystem**, so back up the
device (and `secrets.py`) first. To unpack it yourself:

```sh
pip install littlefs-python   # optional, needed for the user filesystem
python3 tools/uf2_extract.py firmware/contribution_widget-micropython-1.29-with-filesystem.uf2 /tmp/uf2
```

Frozen Python modules: `contribution_widget` (`__init__`, `filesystem`, `math`, `memory`,
`sprite`, `text`), `board`, `boot`, `_boot`, `_boot_fat`, `pimoroni`, `ezwifi`, `easing`,
`qwstpad`, `version`, `rp2`, `asyncio`, `aioble`, `umqtt.simple`, `requests`/`urequests`,
`urllib.urequest`, `ntptime`, `mip`, `ssl`, `datetime`, `webrepl`, `neopixel`, `onewire`,
`ds18x20`, `dht`. Native modules include `picovector` (`screen`, `image`, `color`,
`shape`, `brush`, `font`, `vec2`, `mat3`), `encoder`, `bt_audio`, `ulab`, `qrcode`,
`adcfft`, `bluetooth` and the Pimoroni `breakout_*` drivers.

#### Starter filesystem (`firmware/filesystem/`)

| File | Purpose |
|---|---|
| `main.py` | Boots straight into `bt_audio_visualiser` |
| `bt_audio_visualiser.py` | Bluetooth A2DP speaker ("Widget") with Winamp-style visualiser modes. Turn to change mode, hold the button and turn for volume. Core 1 runs I2S and a 512-bin FFT |
| `bt_audio_spectrum.py` | Simpler A2DP spectrum analyser, encoder is volume |
| `snappy_menu.py` | Encoder-driven scrolling text menu with snap and peek animation |
| `calendar.py` | Month pager driven by the encoder |
| `hotlava.py` | Lava-lamp blobs |
| `train-capacity.py` | Scrolling train showing carriage occupancy |
| `gol.py`, `fast_fire.py`, `spiral.py`, `rainbow.py`, `rainbow_wheel.py`, `balls_demo.py` | The original demos, identical to `device/backup/` |

#### Changes from the 1.27 firmware

- MicroPython 1.27.0 (`feature/basepri`, SDK 2.1.1) → 1.29.0 + 4 local commits (SDK 2.3.0).
- **`rom_font` is gone; use `font`** (e.g. `font.sins`). The apps in `device/`
  have been updated. Avoid naming local variables `font` in functions that also read the
  global.
- New `bt_audio` module: A2DP sink over BTstack with SBC decode, I2S output, volume and
  FFT data (`init`, `set_volume`, `fft_into`, `streaming`, `link_keys`...).
- PicoVector additions: `brush` (`pattern`, `gradient`, `fractal`, `lighten`, `darken`),
  image filters (`blur`, `bloom`, `pixelate`, `vignette`, `crt`, `glitch`, `oilpaint`...),
  palettes (`gameboy`, `c64`, `cga`, `synthwave`...), colour helpers (`mix`, `saturate`,
  `luminance`, `to_oklch`), `vector_font`/`load_font`, `screen.antialias`, `hspan`/`vspan`.

#### Previous firmware (`firmware/previous/`)

| File | Contents |
|---|---|
| `contribution_widget-micropython-1.27.uf2` | MicroPython 1.27.0 (branch `feature/basepri`, built 2026-03-13, Pico SDK 2.1.1). Flash range `0x10000000–0x10125a44`. What the device shipped with. |
| `romfs.uf2` | ROMFS at `0x10200000–0x10300000` from the same device. |

### Public sources

The exact build commit (`2429603c55`) isn't public, but the pieces come from:

| Component | Source |
|---|---|
| MicroPython core | [micropython/micropython v1.29.0](https://github.com/micropython/micropython/releases/tag/v1.29.0), with Pimoroni patches from [pimoroni/micropython](https://github.com/pimoroni/micropython) (`bw-1.29.0` branches) |
| Pico SDK | [raspberrypi/pico-sdk 2.3.0](https://github.com/raspberrypi/pico-sdk/releases/tag/2.3.0) (includes BTstack and the CYW43 driver) |
| Board layout and `contribution_widget` Python modules | Modelled on the Badgeware boards: [pimoroni/blinky2350](https://github.com/pimoroni/blinky2350) (`modules/common/badgeware/` → `contribution_widget/`), [tufty2350](https://github.com/pimoroni/tufty2350), [badger2350](https://github.com/pimoroni/badger2350). Docs at [pimoroni/badgeware-docs](https://github.com/pimoroni/badgeware-docs) |
| Fonts (ROMFS) | [pimoroni/blinky2350 `romfs/fonts`](https://github.com/pimoroni/blinky2350/tree/main/romfs/fonts) (all 37 files byte-identical) |
| Graphics | [pimoroni/picovector](https://github.com/pimoroni/picovector) and [pimoroni/picovector-micropython](https://github.com/pimoroni/picovector-micropython) |
| `breakout_*`, `encoder`, `qrcode`, `adcfft`, `pimoroni` | [pimoroni/pimoroni-pico](https://github.com/pimoroni/pimoroni-pico) |
| `ezwifi` | [pimoroni/presto `modules/py_frozen/ezwifi.py`](https://github.com/pimoroni/presto/blob/main/modules/py_frozen/ezwifi.py) |
| `ulab` | [v923z/micropython-ulab](https://github.com/v923z/micropython-ulab) |
| `aioble`, `umqtt`, `requests`, `datetime`, `mip`... | [micropython/micropython-lib](https://github.com/micropython/micropython-lib) |
| Demo apps | Adapted from Pimoroni examples, e.g. [`balls_demo.py`](https://github.com/pimoroni/explorer/blob/main/examples/balls_demo.py) and [`rainbow_wheel.py`](https://github.com/pimoroni/pimoroni-pico/blob/main/micropython/examples/pico_display/rainbow_wheel.py). `bt_audio_*`, `snappy_menu`, `calendar`, `hotlava`, `train-capacity` and `contribution_widget` itself have no public source yet. |

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
| `rom/fonts/*.ppf` | Copy of the ROMFS fonts, available on the device as `font.<name>` (`rom_font.<name>` on 1.27) |
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

Apps rely on globals (`screen`, `display`, `color`, `font`, `brush`, `vec2`, `shape`,
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
screen.font = font.sins                    # rom_font.sins on 1.27
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
2. Flash the current firmware (firmware, fonts and starter filesystem in one go):
   ```sh
   picotool load -x firmware/contribution_widget-micropython-1.29-with-filesystem.uf2
   ```
   Or roll back to the previous build:
   ```sh
   picotool load -x firmware/previous/contribution_widget-micropython-1.27.uf2
   picotool load firmware/previous/romfs.uf2 && picotool reboot
   ```

The 1.27 backups were taken with `picotool save -p` (program) and
`picotool save -r 0x10200000 0x10300000` (ROMFS).
