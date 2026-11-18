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

# Notification sounds (phase 2)
alert_sound = True
quiet_hours_enabled = False
quiet_hours = (22, 7)

# Reboot if the firmware stops responding for 8 s (and stop the plugin that hung)
watchdog = True
