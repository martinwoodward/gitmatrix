# GitMatrix settings. Edited by the menu (and later the web UI); safe to edit by
# hand. If this file breaks, it is kept as config.py.bad and replaced with the
# defaults.

# Plugins in rotation order, and the ones switched off. New plugins found on
# disk are added switched off.
plugins = ["graph", "clock", "spectrum", "lava", "fire", "rainbow"]
disabled_plugins = []

# Plugins stopped for crashing or hanging, and why. Pick one in the menu to retry.
quarantined = {}

# Seconds each plugin shows before the next (0 = stay put)
delay = 30

# 'slide' or 'cut'
transition = "slide"

# Display brightness 0-100, speaker volume 0-127
brightness = 60
volume = 64

# Notification sounds. Quiet hours ("HH:MM" local time; a window past midnight
# belongs to the day it starts on) mute alerts on the listed days (0 = Monday).
# Muted notifications still show, silently.
alert_sound = True
quiet_hours_enabled = False
quiet_hours = ("22:00", "07:00")
quiet_days = [0, 1, 2, 3, 4, 5, 6]
quiet_bypass_high = False
# While Bluetooth audio is playing, notifications are silent unless this is on:
# then high-priority sounds play over the stream (it drops out for a moment)
alert_interrupt_audio = False

# Reboot if the firmware stops responding for 8 s (and stop the plugin that hung)
watchdog = True

# WiFi network to join (set from the web UI's WiFi page). With no network the
# 'GitMatrix' access point starts so you can set one up.
wifi_ssid = None
wifi_password = None
# Name on the network: http://gitmatrix.local
wifi_hostname = "gitmatrix"

# Time zone for clocks (see timezones.py for the keys)
time_zone = "uk"

# Set to require `Authorization: Bearer <token>` on /api/v1 (the web pages keep working)
api_token = None

# Let new phones find and pair with the speaker
bt_pairable = True

# MQTT / Home Assistant (set from the web Settings page). Off by default.
# Changes apply straight away, no reboot. TLS doesn't check certificates.
mqtt_enabled = False
mqtt_host = None
mqtt_port = None            # None: 1883, or 8883 with TLS
mqtt_user = None
mqtt_password = None
mqtt_tls = False
mqtt_discovery = True       # announce entities to Home Assistant
mqtt_discovery_prefix = None    # None: "homeassistant"
mqtt_base_topic = None      # None: "gitmatrix/<network name>"
