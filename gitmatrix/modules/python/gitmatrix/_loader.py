# Plugin loader and fault containment.
#
# Plugins are trusted, cooperative code (not sandboxed), but one that raises,
# runs out of memory or hangs must only take itself out:
#   * every hook runs inside guard(): exceptions are recorded, 5 in a row (or
#     any import failure) quarantines the plugin and persists that in config.
#   * before each hook a crash marker naming the plugin and hook goes into a
#     scratch register; if the watchdog fires mid-hook, the next boot reads it
#     and quarantines that plugin (check_crash()).

import json
import os
import sys

from gitmatrix import _compat, _persist, _runtime

MAX_FAILURES = 5
SLOW_UPDATE_MS = 50
IS_MICROPYTHON = sys.implementation.name == "micropython"


class Plugin:
    def __init__(self, name, index, manifest):
        self.name = name
        self.index = index
        self.manifest = manifest
        self.title = manifest.get("name", name) if isinstance(manifest.get("name"), str) else name
        self.input = bool(manifest.get("input", False))
        self.module = None
        self.surface = None
        self.failures = 0          # consecutive
        self.error_count = 0       # total
        self.last_error = None
        self.tasks = []
        self.update_ms = 0.0       # moving average
        self.slow = False
        self.shown = False
        self.markers = {hook: _persist.marker(index, name, hook) for hook in _persist.HOOKS}
        self.hooks = {}

    def hook(self, name):
        fn = self.hooks.get(name, False)
        if fn is False:
            fn = getattr(self.module, name, None) if self.module is not None else None
            if not callable(fn):
                fn = None
            self.hooks[name] = fn
        return fn

    def __repr__(self):
        return f"Plugin({self.name!r})"


def read_manifest(name):
    try:
        with open(_runtime.plugins_dir() + "/" + name + "/package.json") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _module_key(name):
    return _runtime.plugins_dir() + "/" + name if IS_MICROPYTHON else "plugins." + name


def _import(name):
    key = _module_key(name)
    if IS_MICROPYTHON:
        # Path imports, as Art Matrix does: no plugins/__init__.py needed
        return __import__(key)
    __import__(key)
    return sys.modules[key]


class PluginLoader:
    def __init__(self, config, compositor, feed=None):
        self.config = config
        self.compositor = compositor
        self.feed = feed or (lambda: None)
        self.plugins = {}          # name -> Plugin, for every enabled plugin we tried
        self.errors = []           # recent (name, hook, error text) for the UI/logs
        _runtime.loader = self

    # ---- discovery ------------------------------------------------------------

    def on_disk(self):
        try:
            entries = os.listdir(_runtime.plugins_dir())
        except OSError:
            return []
        return [n for n in sorted(entries) if _compat.valid_name(n)
                and _compat.exists(_runtime.plugins_dir() + "/" + n + "/__init__.py")]

    def sync_config(self):
        """Art Matrix rules: drop vanished plugins, add new ones switched off."""
        disk = self.on_disk()
        cfg = self.config
        plugins = [p for p in cfg.get("plugins", []) if p in disk]
        disabled = [p for p in cfg.get("disabled_plugins", []) if p in disk]
        added = [p for p in disk if p not in plugins]
        plugins += added
        disabled += [p for p in added if p not in disabled]
        quarantined = {k: v for k, v in cfg.get("quarantined", {}).items() if k in disk}
        cfg.set("plugins", plugins)
        cfg.set("disabled_plugins", disabled)
        cfg.set("quarantined", quarantined)
        return added

    def enabled(self):
        cfg = self.config
        q = cfg.get("quarantined", {})
        disabled = cfg.get("disabled_plugins", [])
        return [p for p in cfg.get("plugins", []) if p not in disabled and p not in q]

    def active(self):
        """Loaded, healthy plugins in rotation order."""
        out = []
        for name in self.enabled():
            p = self.plugins.get(name)
            if p is not None and p.module is not None:
                out.append(p)
        return out

    # ---- loading --------------------------------------------------------------

    def load_all(self):
        for name in self.enabled():
            if name not in self.plugins or self.plugins[name].module is None:
                self.load(name)
            self.feed()
        return self.active()

    def load(self, name):
        if name in self.config.get("quarantined", {}):
            return None
        plugins = self.config.get("plugins", [])
        index = plugins.index(name) if name in plugins else 0xFFF
        plugin = Plugin(name, index, read_manifest(name))
        plugin.surface = self.compositor.new_surface()
        self.plugins[name] = plugin
        ok, module = self.guard(plugin, "import", _import, name)
        if not ok:
            self._drop_module(name)
            self.quarantine(plugin, f"import failed: {plugin.last_error}")
            return None
        plugin.module = module
        return plugin

    def unload(self, name):
        plugin = self.plugins.pop(name, None)
        if plugin is not None:
            self.cancel_tasks(plugin)
        self._drop_module(name)
        _compat.collect()

    def _drop_module(self, name):
        key = _module_key(name)
        for k in list(sys.modules):
            if k == key or k.startswith(key + "."):
                del sys.modules[k]

    # ---- guarded calls --------------------------------------------------------

    def guard(self, plugin, hook, fn, *args):
        """Run fn(*args) as `plugin`'s `hook`. Returns (ok, result)."""
        _persist.mark(plugin.markers[hook])
        _runtime.current = plugin
        self.compositor.bind(plugin.surface)
        try:
            result = fn(*args)
        except MemoryError as e:
            _compat.collect()
            self.record_failure(plugin, hook, e)
            return False, None
        except Exception as e:  # noqa: BLE001 - containment is the point
            self.record_failure(plugin, hook, e)
            return False, None
        except SystemExit as e:
            self.record_failure(plugin, hook, e)
            return False, None
        finally:
            self.compositor.unbind()
            _runtime.current = None
            _persist.clear_mark()
        plugin.failures = 0
        return True, result

    def call(self, plugin, hook, *args):
        """Call an optional hook. Missing hooks count as success."""
        if plugin.module is None:
            return False, None
        fn = plugin.hook(hook)
        if fn is None:
            return True, None
        ok, result = self.guard(plugin, hook, fn, *args)
        if not ok and plugin.failures >= MAX_FAILURES:
            self.quarantine(plugin, f"{hook} failed {MAX_FAILURES} times: {plugin.last_error}")
        return ok, result

    def update(self, plugin):
        t = _compat.ticks_ms()
        ok, _ = self.call(plugin, "update")
        if ok:
            ms = _compat.ticks_diff(_compat.ticks_ms(), t)
            plugin.update_ms += (ms - plugin.update_ms) * 0.05
            if plugin.update_ms > SLOW_UPDATE_MS and not plugin.slow:
                plugin.slow = True
                print(f"plugin {plugin.name} is slow ({int(plugin.update_ms)} ms/frame)")
        return ok

    def record_failure(self, plugin, hook, e):
        plugin.failures += 1
        plugin.error_count += 1
        plugin.last_error = _compat.error_text(e)
        self.errors.append((plugin.name, hook, plugin.last_error))
        if len(self.errors) > 20:
            self.errors.pop(0)
        if plugin.failures <= MAX_FAILURES:
            print(f"plugin {plugin.name} {hook} failed ({plugin.failures}/{MAX_FAILURES}):")
            _compat.print_exception(e)

    # ---- quarantine -----------------------------------------------------------

    def quarantine(self, plugin_or_name, reason):
        name = plugin_or_name if isinstance(plugin_or_name, str) else plugin_or_name.name
        print(f"plugin {name} quarantined: {reason}")
        q = dict(self.config.get("quarantined", {}))
        q[name] = reason
        self.config.set("quarantined", q)
        plugin = self.plugins.get(name)
        if plugin is not None:
            self.cancel_tasks(plugin)
            plugin.module = None
            plugin.hooks = {}
        self._drop_module(name)
        _compat.collect()
        if _runtime.host is not None:
            _runtime.host.plugin_removed(name)

    def unquarantine(self, name):
        q = dict(self.config.get("quarantined", {}))
        if q.pop(name, None) is None:
            return False
        self.config.set("quarantined", q)
        disabled = self.config.get("disabled_plugins", [])
        if name in disabled:
            self.config.set("disabled_plugins", [p for p in disabled if p != name])
        self.plugins.pop(name, None)
        return True

    def check_crash(self, watchdog_reset):
        """At boot: quarantine the plugin that was mid-hook when the watchdog fired."""
        found = _persist.read_mark()
        _persist.clear_mark()
        if found is None or not watchdog_reset:
            return None
        index, crc, hook = found
        plugins = self.config.get("plugins", [])
        name = plugins[index] if index < len(plugins) else None
        if name is None or _persist.crc8(name) != crc:
            # the list changed since; fall back to the checksum
            matches = [p for p in plugins if _persist.crc8(p) == crc]
            name = matches[0] if len(matches) == 1 else None
        if name is None:
            return None
        self.quarantine(name, f"hung in {hook} (watchdog reset)")
        return name

    # ---- tasks ----------------------------------------------------------------

    def track(self, plugin, task):
        plugin.tasks = [t for t in plugin.tasks if not _done(t)]
        plugin.tasks.append(task)

    def cancel_tasks(self, plugin):
        for t in plugin.tasks:
            try:
                t.cancel()
            except Exception:  # noqa: BLE001, S110 - already finished
                pass
        plugin.tasks = []


def _done(task):
    done = getattr(task, "done", None)
    try:
        return bool(done()) if done else False
    except Exception:  # noqa: BLE001
        return False
