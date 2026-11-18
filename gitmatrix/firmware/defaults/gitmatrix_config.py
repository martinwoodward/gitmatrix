# GitMatrix firmware settings. Copied to /gitmatrix_config.py on first boot; edit
# that copy with mpremote or Thonny. Not shown in the web UI.

# Setup access point, started when no WiFi network is configured (or the
# configured one has been unreachable for a minute). WPA2: 8-63 characters.
AP_SSID = "GitMatrix"
AP_PASSWORD = "monalisa"

# Where the plugin store finds plugins (phase 3)
PLUGIN_REPO = "martinwoodward/gitmatrix-plugins"
PLUGIN_BRANCH = "main"
