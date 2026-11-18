# The host: owns the frame. Each frame it reads input, runs the menu or the
# current plugin(s), composites and presents. Plugins only ever run inside
# loader.guard(), so a broken one can't take the host down with it.

from gitmatrix import _audio, _compat, _events, _input, _menu, _notify, _runtime

PLUGIN = 0
MENU = 1
NOTIFY = 2
MODE_NAMES = ("plugin", "menu", "notification")

TRANSITION_MS = 700
WIDTH_PX = 53
FRAME_MS = 16
PUBLISH_MS = 500        # how often Bluetooth/WiFi state is checked for the event bus


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
        self.notifier = None            # gitmatrix._notify.Notifier, set at boot
        self.note = None                # the notification on screen
        self.note_start = 0
        self.renderer = None
        self.forced = None              # Plugin forced on screen by a notification
        self.saved = None               # (plugin, ms it had been showing) to restore
        self._pub_plugin = False        # what the event bus was last told (False: nothing yet)
        self._pub_mode = -1
        self._pub_at = None
        self._pub_bt = None
        self._pub_wifi = None
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
        if self.forced is not None and self.forced.name == name:
            self.forced = None
            if self.note is not None:
                self.note.cancelled = True
        if self.saved is not None and self.saved[0] is not None and self.saved[0].name == name:
            self.saved = (None, 0)
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
        bus = _runtime.events
        if bus is not None:
            # observed, not consumed: the menu/plugins below still act on them
            for e in events:
                bus.input(e)
            self.publish(bus, now)

        if self.mode == MENU:
            if self.menu.frame(self.compositor.fb, events, now):
                self._signal_waiting(now)
                self.compositor.present()
                return
            events = []

        if self.mode == NOTIFY:
            if self._notify_frame(events, now):
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

        notifier = self.notifier
        if notifier is not None and notifier.pending is not None:
            note = notifier.take(now)
            if note is not None and self.begin_notification(note, now) and self._notify_frame((), now):
                return

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

    def publish(self, bus, now):
        """Tell the event bus about plugin/mode changes (made by the frame,
        the menu or the API) and, every PUBLISH_MS, Bluetooth/WiFi changes.
        Allocation-free unless something changed."""
        current = self.current
        if current is not self._pub_plugin:
            self._pub_plugin = current
            name = current.name if current is not None else None
            bus.change(_events.PLUGIN, name, {"name": name}, now)
        if self.mode != self._pub_mode:
            self._pub_mode = self.mode
            mode = MODE_NAMES[self.mode]
            bus.change(_events.MODE, mode, {"mode": mode}, now)
        if self._pub_at is not None and _compat.ticks_diff(now, self._pub_at) < PUBLISH_MS:
            return
        self._pub_at = now
        audio = self.audio
        connected = bool(audio is not None and audio.connected())
        streaming = bool(audio is not None and audio.streaming())
        bt = (connected << 1) | streaming
        if bt != self._pub_bt:
            self._pub_bt = bt
            value = {"connected": connected, "streaming": streaming}
            bus.change(_events.BLUETOOTH, value, value, now)
        wifi = _runtime.wifi
        ip = wifi.ip if wifi is not None and wifi.connected() else None
        if ip != self._pub_wifi:
            self._pub_wifi = ip
            value = {"connected": ip is not None, "ip": ip}
            bus.change(_events.WIFI, value, value, now)

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

    # ---- notifications ----------------------------------------------------------

    def begin_notification(self, note, now):
        """Interrupt whatever is showing (remembering it) and show `note`."""
        if self.note is None:
            current = self.current
            shown_for = _compat.ticks_diff(now, self.entered)
            if current is not None and current.module is not None and current.shown:
                self.loader.call(current, "on_hide")
                current.shown = False
            self.saved = (current, shown_for)
            self.prev = None
        else:
            self._release_forced()
        self.mode = NOTIFY
        self.note = note
        self.note_start = now
        self.renderer = None
        self.message = None
        if note.plugin is not None:
            if not self._force_plugin(note):
                self.end_notification(now, False)
                self.show_message(f"{note.plugin} can't be shown", 2500, 25, now)
                return False
        else:
            self.renderer = _notify.Renderer(note, self.surface)
        print(f"notify: {note.id}")
        return True

    def _force_plugin(self, note):
        name = note.plugin
        if name in self.config.get("quarantined", {}):
            return False
        plugin = self.loader.plugins.get(name)
        if plugin is None or plugin.module is None:
            plugin = self.loader.load(name)
        if plugin is None or plugin.module is None:
            return False
        import gitmatrix
        gitmatrix.notification = note.data
        self.forced = plugin
        self._reset_plugin_input(plugin)
        ok, _ = self.loader.call(plugin, "on_show")
        plugin.shown = ok
        if self.forced is plugin:
            self.loader.call(plugin, "on_notify", note.data)
        return self.forced is plugin

    def _release_forced(self):
        import gitmatrix
        plugin = self.forced
        self.forced = None
        gitmatrix.notification = None
        if plugin is None:
            return
        if plugin.module is not None and plugin.shown:
            self.loader.call(plugin, "on_hide")
            plugin.shown = False
        # a disabled plugin was only loaded for this; free it again
        if plugin.name not in self.loader.enabled() and plugin is not (self.saved or (None,))[0]:
            self.loader.unload(plugin.name)

    def end_notification(self, now, dismissed):
        """Finish the note on screen and put back what was showing."""
        note = self.note
        self._release_forced()
        self.note = None
        self.renderer = None
        if self.notifier is not None and note is not None:
            self.notifier.finished(note, dismissed, now)
        self.mode = PLUGIN
        saved, self.saved = self.saved, None
        plugin, shown_for = saved if saved is not None else (None, 0)
        if plugin is not None and plugin.module is not None and plugin in self.plugins():
            self.current = plugin
            self.entered = _compat.ticks_add(now, -shown_for)
            self._reset_plugin_input(plugin)
            ok, _ = self.loader.call(plugin, "on_show")
            plugin.shown = ok
        else:
            self.current = None
            plugins = self.plugins()
            if plugins:
                self._enter(plugins[0], now)
        self.prev = None

    def _notify_frame(self, events, now):
        """One frame of the notification. Returns False once it has ended (so
        the frame goes on to draw the restored plugin)."""
        for e in events:
            if e.kind == _input.PRESS:
                self.end_notification(now, True)
                return False
            if e.kind == _input.TURN and self.forced is not None and self.forced.input:
                self.loader.call(self.forced, "on_input", e)
        note = self.note
        notifier = self.notifier
        elapsed = _compat.ticks_diff(now, self.note_start)
        if note.cancelled:
            self.end_notification(now, True)
            return False
        if self.forced is not None:
            done = elapsed >= (note.duration or _notify.PLUGIN_MS)
        else:
            done = self.renderer.draw(self.surface, elapsed)
        if note.replaced:
            # a newer note with this id is being prepared: keep this one up until it's ready
            done = notifier is not None and notifier.pending is not None
        if done:
            nxt = None
            if notifier is not None and notifier.pending is not None:
                notifier.finished(note, False, now)     # its end event before the next one's start
                nxt = notifier.take(now)
            if nxt is None:
                self.end_notification(now, False)
                return False
            return self.begin_notification(nxt, now) and self._notify_frame((), now)
        if self.forced is not None:
            self.loader.update(self.forced)
            if self.forced is None or self.forced.module is None:
                return True
            self.compositor.present(self.forced.surface)
        else:
            if note.replaced and elapsed >= self.renderer.total_ms:
                self.renderer.draw(self.surface, elapsed % self.renderer.pass_ms)
            self.compositor.present(self.surface)
        return True

    def _signal_waiting(self, now):
        """A high-priority note waits while the menu is open: blink a red pixel
        in the top-right corner (the board has no spare status LED)."""
        n = self.notifier
        if n is None or not n.waiting_high() or (now // 250) % 2:
            return
        fb = self.compositor.fb
        fb.pen = color.rgb(255, 0, 0)
        fb.rectangle(WIDTH_PX - 1, 0, 1, 1)

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
            title = plugin.title if plugin is not None else _compat.cap_first(name.replace("_", " "))
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
        bus = _runtime.events
        if bus is not None:
            bus.change(_events.BRIGHTNESS, self.config.get("brightness", 60))

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
        bus = _runtime.events
        if bus is not None:
            bus.change(_events.VOLUME, volume_percent(self.config))

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


def volume_percent(config):
    """The 0-127 config volume as 0-100."""
    return int(config.get("volume", 64) * 100 / 127 + 0.5)
