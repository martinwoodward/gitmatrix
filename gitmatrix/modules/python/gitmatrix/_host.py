# The host: owns the frame. Each frame it reads input, runs the menu or the
# current plugin(s), composites and presents. Plugins only ever run inside
# loader.guard(), so a broken one can't take the host down with it.

from gitmatrix import _audio, _compat, _input, _menu, _runtime

PLUGIN = 0
MENU = 1

TRANSITION_MS = 700
FRAME_MS = 16


class Host:
    def __init__(self, config, loader, compositor, input_, audio=None, safe_mode=None):
        self.config = config
        self.loader = loader
        self.compositor = compositor
        self.input = input_
        self.audio = audio
        self.safe_mode = safe_mode      # None, or the reason we are in safe mode
        self.mode = PLUGIN
        self.menu = _menu.Menu(self)
        self.seq = input_.seq
        self.current = None             # Plugin
        self.entered = 0
        self.prev = None                # Plugin sliding out
        self.trans_start = 0
        self.direction = 1
        self.message = None             # (text, until, hue)
        self.message_since = 0
        self.surface = compositor.new_surface()   # host's own drawing surface
        self.frames = 0
        _runtime.host = self

    # ---- rotation ---------------------------------------------------------------

    def plugins(self):
        return [] if self.safe_mode else self.loader.active()

    def start(self, now=None):
        now = _compat.ticks_ms() if now is None else now
        self.apply_brightness()
        self.apply_volume()
        self.apply_pairable()
        plugins = self.plugins()
        if plugins:
            self._enter(plugins[0], now)

    def _enter(self, plugin, now, direction=0):
        old = self.current
        if old is plugin:
            return
        if old is not None and old.module is not None:
            self.loader.call(old, "on_hide")
            old.shown = False
        self.prev = old if (direction and old is not None and old.module is not None and plugin is not None) else None
        self.direction = direction or 1
        self.trans_start = now
        self.current = plugin
        self.entered = now
        if plugin is not None:
            self._reset_plugin_input(plugin)
            ok, _ = self.loader.call(plugin, "on_show")
            plugin.shown = ok
            print(f"plugin: {plugin.name}")

    def _reset_plugin_input(self, plugin):
        # a plugin only sees turns made while it is on screen
        api = _plugin_input()
        if api is not None:
            api.reset(plugin.name)

    def step(self, delta, now):
        plugins = self.plugins()
        if not plugins:
            self._enter(None, now)
            return
        if self.current in plugins:
            i = plugins.index(self.current)
        else:
            i = -1 if delta > 0 else 0
        nxt = plugins[(i + (1 if delta > 0 else -1)) % len(plugins)]
        if nxt is not self.current:
            self._enter(nxt, now, 1 if delta > 0 else -1)

    def show(self, name, now=None):
        """Switch to a plugin by name, loading or un-quarantining it if needed."""
        now = _compat.ticks_ms() if now is None else now
        if self.safe_mode:
            self.safe_mode = None
            self.loader.load_all()
        if name in self.config.get("quarantined", {}):
            self.loader.unquarantine(name)
        disabled = self.config.get("disabled_plugins", [])
        if name in disabled:
            self.config.set("disabled_plugins", [p for p in disabled if p != name])
        plugin = self.loader.plugins.get(name)
        if plugin is None or plugin.module is None:
            plugin = self.loader.load(name)
        if plugin is None:
            self.show_message(f"{name} failed to load", 3000, 25)
            return False
        self._enter(plugin, now)
        self.prev = None
        return True

    def plugin_removed(self, name):
        """The loader quarantined a plugin; move on if it was on screen."""
        if self.prev is not None and self.prev.name == name:
            self.prev = None
        if self.current is not None and self.current.name == name:
            now = _compat.ticks_ms()
            self.current = None
            plugins = self.plugins()
            if plugins:
                self._enter(plugins[0], now)
            self.show_message(f"{name} stopped", 2500, 25)

    # ---- messages ---------------------------------------------------------------

    def show_message(self, text, ms=3000, hue=240, now=None):
        now = _compat.ticks_ms() if now is None else now
        self.message = (text, _compat.ticks_add(now, ms), hue)
        self.message_since = now

    def _draw_message(self, now):
        text, until, hue = self.message
        if until is not None and _compat.ticks_diff(now, until) > 0:
            self.message = None
            return False
        _menu.blank(self.surface)
        _menu.draw_marquee(self.surface, text, _compat.ticks_diff(now, self.message_since), color.oklch(200, 150, hue))
        self.compositor.present(self.surface)
        return True

    # ---- frame ------------------------------------------------------------------

    def frame(self, now=None):
        now = _compat.ticks_ms() if now is None else now
        self.frames += 1
        self.input.poll()
        events = self.input.since(self.seq)
        self.seq = self.input.seq

        if self.mode == MENU:
            if self.menu.frame(self.compositor.fb, events, now):
                self.compositor.present()
                return
            events = []

        delta = 0
        for e in events:
            if e.kind == _input.PRESS:
                self.open_menu(now)
                self.menu.frame(self.compositor.fb, [], now)
                self.compositor.present()
                return
            if e.kind == _input.TURN:
                if self.current is not None and self.current.input:
                    self.loader.call(self.current, "on_input", e)
                else:
                    delta += e.value

        if delta and not self.message:
            self.step(delta, now)
        elif self.prev is None and self._rotation_due(now):
            self.step(1, now)

        if self.message is not None and self._draw_message(now):
            return

        if self.current is None:
            self._idle_screen(now)
            return

        current = self.current
        self.loader.update(current)
        prev = self.prev
        if prev is not None:
            p = _compat.ticks_diff(now, self.trans_start) / TRANSITION_MS
            if p >= 1 or prev.module is None or self.config.get("transition", "slide") != "slide":
                self.prev = None
            else:
                self.loader.update(prev)
                if self.current is not None and self.current.module is not None:
                    self.compositor.slide(prev.surface, current.surface, p, self.direction)
                    self.compositor.present()
                    return
        if self.current is current and current.module is not None:
            self.compositor.present(current.surface)

    def _rotation_due(self, now):
        delay = self.config.get("delay", 30)
        if not delay or self.current is None:
            return False
        # manifest "hold": true pins the plugin; "audio" pins it while music plays
        hold = self.current.manifest.get("hold", False)
        if hold is True or (hold == "audio" and self.audio is not None and self.audio.active()):
            return False
        return len(self.plugins()) > 1 and _compat.ticks_diff(now, self.entered) > delay * 1000

    def _idle_screen(self, now):
        if self.safe_mode:
            text, hue = f"Safe mode ({self.safe_mode}) - press for menu", 25
        else:
            text, hue = "No plugins - press for menu", 240
        _menu.blank(self.surface)
        _menu.draw_marquee(self.surface, text, _compat.ticks_diff(now, self.entered), color.oklch(200, 150, hue))
        self.compositor.present(self.surface)

    # ---- menu -------------------------------------------------------------------

    def open_menu(self, now):
        if self.current is not None and self.current.module is not None:
            self.loader.call(self.current, "on_hide")
            self.current.shown = False
        self.prev = None
        self.mode = MENU
        self.menu.open(now)

    def menu_closed(self):
        self.mode = PLUGIN
        now = _compat.ticks_ms()
        self.entered = now
        if self.current is not None and self.current.module is not None and not self.current.shown:
            ok, _ = self.loader.call(self.current, "on_show")
            self.current.shown = ok
        elif self.current is None and self.plugins():
            self._enter(self.plugins()[0], now)

    def menu_plugins(self):
        q = self.config.get("quarantined", {})
        out = []
        for name in self.config.get("plugins", []):
            disabled = name in self.config.get("disabled_plugins", [])
            if disabled and name not in q:
                continue
            plugin = self.loader.plugins.get(name)
            title = plugin.title if plugin is not None else name.replace("_", " ").capitalize()
            out.append((name, title, name in q))
        return out

    def menu_current_plugin(self):
        return self.current.name if self.current is not None else None

    def menu_show_plugin(self, name):
        self.show(name)

    def menu_get_brightness(self):
        return self.config.get("brightness", 60) / 100

    def menu_set_brightness(self, level, save):
        self.config.values["brightness"] = int(level * 100 + 0.5)
        self.apply_brightness()
        if save:
            self.config.touch()

    def apply_brightness(self):
        try:
            self.compositor.driver.set_brightness(max(0.0, min(1.0, self.config.get("brightness", 60) / 100)))
        except AttributeError:
            pass

    def menu_get_volume(self):
        return self.config.get("volume", 64) / 127

    def menu_set_volume(self, level, save):
        self.config.values["volume"] = int(level * 127 + 0.5)
        self.apply_volume()
        if save:
            self.config.touch()

    def apply_volume(self):
        if self.audio is not None:
            self.audio.set_volume(self.config.get("volume", 64) / 127)

    def menu_wifi_status(self):
        wifi = _runtime.wifi
        if wifi is None:
            return "WiFi unavailable"
        return wifi.status_text()

    def menu_bt_status(self):
        if self.audio is None:
            return "Bluetooth unavailable"
        if self.audio.streaming():
            return f"Playing - {_audio.NAME}"
        if self.audio.connected():
            return f"Connected - {_audio.NAME}"
        return f"Pair with '{_audio.NAME}'"

    def menu_forget_bt(self):
        _audio.forget_devices()

    def menu_get_pairable(self):
        return bool(self.config.get("bt_pairable", True))

    def menu_set_pairable(self, on):
        self.config.set("bt_pairable", bool(on))
        return self.apply_pairable()

    def apply_pairable(self):
        return _audio.set_pairable(self.config.get("bt_pairable", True))


def _plugin_input():
    import gitmatrix
    return getattr(gitmatrix, "input", None)
