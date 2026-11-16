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
| `main.py` | Boot app. A menu you scroll with the encoder (Graph, Brightness, Volume, WiFi, Apps, Update, Test); it opens on Graph. Press the encoder button to open an item and press again to go back. **Graph:** draws `/contrib_data.json` as 53 weeks × 7 days, brighter green = more contributions; turn the wheel to show the total. **Brightness:** turn to adjust, press to save to `/settings.json` (applied at boot). **WiFi:** connects with `ezwifi` and scrolls the network, IP address and signal strength. **Update:** downloads `https://github.com/<GITHUB_USERNAME>.contribs` into `/contrib_data.json` and shows the graph. **Apps:** lists the `.py` files on the device; press to launch one. Apps launch via `/launch.txt` and a reset, and pressing the button while an app runs resets back to the menu. Volume and Test show "Soon!". |
| `gol.py` | Conway's Game of Life |
| `fast_fire.py` | Fire effect (viper-optimised) |
| `spiral.py`, `rainbow.py`, `rainbow_wheel.py`, `balls_demo.py` | Graphics demos |
| `contrib_data.json` | Sample contribution graph (schema `v2`: `weeks[53].contribution_days[7]` with `count`/`level`, `colors_full`, `months`, `total_contributions`). Same format as `https://github.com/<user>.contribs`; the **Update** menu item replaces it with your own data. |
| `user_data.json` | Sample GitHub `/users/<login>` API response (@Gadgetoid) |
| `rom/fonts/*.ppf` | Copy of the ROMFS fonts, available on the device as `rom_font.<name>` |
| `secrets.example.py` | Template for `secrets.py` (see below) |

> The **Update** menu item fetches your contribution data from
> `https://github.com/<GITHUB_USERNAME>.contribs` over WiFi. Data isn't refreshed
> automatically yet.

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
# edit WIFI_SSID, WIFI_PASSWORD, GITHUB_USERNAME
mpremote fs cp device/secrets.py :secrets.py
```

`ezwifi` reads `WIFI_SSID`/`WIFI_PASSWORD` from `secrets.py`:

```python
import ezwifi
ezwifi.connect()
```

### 3. Deploy apps

```sh
mpremote fs cp device/gol.py :gol.py     # one file
cd device && mpremote fs cp *.py *.json : # everything
mpremote reset                            # reboot into main.py
```

### 4. Run an app without installing it

Apps rely on globals (`screen`, `display`, `color`, `rom_font`, `vec2`, `shape`,
`image`, `mat3`...) that are set up when `contribution_widget` is imported:

```sh
mpremote exec "import contribution_widget" run device/gol.py
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
