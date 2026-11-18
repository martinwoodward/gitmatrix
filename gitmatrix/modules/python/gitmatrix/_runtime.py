# Shared runtime references. Kept in one tiny module so the public API, loader,
# host and menu can find each other without circular imports.

# Filesystem root. "" on the device; the test harness points it at a temp dir.
ROOT = ""

host = None        # the running Host
loader = None      # the PluginLoader
config = None      # the Config
audio = None       # the Audio analyser
current = None     # Plugin whose hook is running right now (or None for the host)
wifi = None        # the WiFi manager (gitmatrix._wifi.WiFi)
firmware = {}      # /gitmatrix_config.py values (AP_PASSWORD, PLUGIN_REPO, ...)
store = None      # the plugin Store (gitmatrix._store.Store)
notifier = None    # the Notifier (gitmatrix._notify.Notifier)
events = None      # the event Bus (gitmatrix._events.Bus)
mqtt = None        # the MQTT client (gitmatrix._mqtt.Mqtt), when WiFi exists
reboot_at = None   # ticks_ms when housekeeping should reboot (after a web request)


def path(p):
    return ROOT + p


def plugins_dir():
    return path("/lib/plugins")


def data_dir(name):
    return path("/data/" + name)


def secrets_path(name):
    return path("/secrets/" + name + ".json")


def current_name():
    return current.name if current is not None else None
