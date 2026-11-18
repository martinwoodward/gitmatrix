# Optional MQTT client with Home Assistant discovery (phase 6). Off by default;
# set up on the web Settings page (/api/v1/mqtt). Changes apply in place - the
# client drops its connection and reconnects with the new settings, never a
# reboot (warm resets can hang this board).
#
# A minimal MQTT 3.1.1 client, QoS 0 only, on asyncio streams, so it never
# blocks the frame loop, Bluetooth audio or the web server:
#   - the event-bus listener only records what changed (a set of keys and a
#     short, bounded list of knob actions) and wakes the writer
#   - one task writes (state, discovery, encoder events, pings), another reads
#     (commands, PINGRESP); inbound packets are capped at MAX_PACKET
#   - failures back off 2 s .. 5 min; it waits for WiFi rather than ever asking
#     WiFi to reconnect (associating makes A2DP stutter)
#   - while A2DP streams it won't start a TLS handshake or a DNS lookup (both
#     block for a while); a plain connection to a broker IP goes ahead
#
# Topics (<base> defaults to gitmatrix/<hostname>):
#   <base>/availability                 online/offline (retained, offline is the LWT)
#   <base>/plugin, /mode, /brightness, /volume, /alert_sound,
#   <base>/bluetooth, /wifi, /notification, /encoder/position   state (retained)
#   <base>/encoder         {"event_type": turn_cw|turn_ccw|press|long_press, "position"}
#   <base>/encoder/action  the same event type, plain (device triggers)
#   <base>/plugin/set|next|prev, /brightness/set, /volume/set, /alert_sound/set,
#   <base>/notify, /notify/dismiss, /sound          commands
#   <base>/result          {"command", "ok", "error"?, ...} after each command

import json

import gitmatrix
from gitmatrix import _compat, _events, _flash, _log, _notify, _runtime, _sounds

KEEPALIVE_S = 60
PING_MS = 30000             # ping when we've sent nothing for this long
DEAD_MS = 90000             # 1.5x keepalive without a byte from the broker: reconnect
CONNECT_TIMEOUT_MS = 10000
WRITE_TIMEOUT_MS = 10000
IDLE_MS = 5000              # the writer wakes at least this often
WAIT_MS = 2000              # re-check WiFi / audio this often while waiting
BACKOFF_MIN_MS = 2000
BACKOFF_MAX_MS = 300000
MAX_PACKET = 64 * 1024      # inbound; the same as a REST request body
MAX_ACTIONS = 16            # knob events waiting to be sent
MAX_RESULTS = 4
DNS_TTL_S = 1800
MAX_HOST = 128
MAX_TOPIC = 64
MAX_SECRET = 128

DISABLED = "disabled"
WAITING = "waiting"         # for WiFi, settings or the end of a stream
CONNECTING = "connecting"
CONNECTED = "connected"
BACKOFF = "backoff"

CONNACK_ERRORS = {
    1: "broker refused the protocol version",
    2: "broker refused the client id",
    3: "broker unavailable",
    4: "bad user name or password",
    5: "not authorised",
}

# command topic (under <base>) -> handler name
COMMANDS = {
    "plugin/set": "_cmd_plugin",
    "plugin/next": "_cmd_next",
    "plugin/prev": "_cmd_prev",
    "brightness/set": "_cmd_brightness",
    "volume/set": "_cmd_volume",
    "alert_sound/set": "_cmd_alert_sound",
    "notify": "_cmd_notify",
    "notify/dismiss": "_cmd_dismiss",
    "sound": "_cmd_sound",
}

ENCODER_TYPES = ("turn_cw", "turn_ccw", "press", "long_press")
STATE_KEYS = (_events.PLUGIN, _events.MODE, _events.BRIGHTNESS, _events.VOLUME,
              _events.BLUETOOTH, _events.WIFI, _events.NOTIFICATION, "alert_sound", "position")


class MqttError(OSError):
    """A failure we describe ourselves (shown as is in the status)."""


# ---- packets ----------------------------------------------------------------

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _str(s):
    b = s.encode("utf-8") if isinstance(s, str) else bytes(s)
    return len(b).to_bytes(2, "big") + b


def _packet(kind, body):
    return bytes((kind,)) + _varint(len(body)) + body


def connect_packet(client_id, keepalive=KEEPALIVE_S, user=None, password=None, will=None):
    """will: (topic, payload, retain) or None."""
    flags = 0x02                    # clean session
    payload = _str(client_id)
    if will is not None:
        flags |= 0x04 | (0x20 if will[2] else 0)
        payload += _str(will[0]) + _str(will[1])
    if user:
        flags |= 0x80
        payload += _str(user)
        if password:
            flags |= 0x40
            payload += _str(password)
    body = _str("MQTT") + bytes((4, flags)) + keepalive.to_bytes(2, "big") + payload
    return _packet(0x10, body)


def publish_packet(topic, payload, retain=False):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return _packet(0x31 if retain else 0x30, _str(topic) + payload)


def subscribe_packet(pid, topics):
    body = pid.to_bytes(2, "big")
    for t in topics:
        body += _str(t) + b"\x00"
    return _packet(0x82, body)


PINGREQ = b"\xc0\x00"
DISCONNECT = b"\xe0\x00"


def parse_publish(first, body):
    """(topic, payload, retained) from a PUBLISH packet's fixed header byte and body."""
    n = int.from_bytes(body[:2], "big")
    topic = bytes(body[2:2 + n]).decode("utf-8")
    i = 2 + n
    if (first >> 1) & 3:
        i += 2                      # packet id (we subscribe at QoS 0, but be safe)
    return topic, bytes(body[i:]), bool(first & 1)


async def read_packet(reader, limit=MAX_PACKET):
    """(first byte, body) or (first byte, None) for a packet over `limit`
    (read and thrown away in small pieces). EOFError when the peer closes."""
    head = await reader.readexactly(1)
    first = head[0]
    length = 0
    shift = 0
    while True:
        b = (await reader.readexactly(1))[0]
        length |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
        if shift > 21:
            raise ValueError("bad packet length")
    if length > limit:
        while length:
            n = min(length, 512)
            await reader.readexactly(n)
            length -= n
        return first, None
    body = await reader.readexactly(length) if length else b""
    return first, body


# ---- helpers ----------------------------------------------------------------

async def _wait_for(aw, ms):
    import asyncio

    if hasattr(asyncio, "wait_for_ms"):
        return await asyncio.wait_for_ms(aw, ms)
    return await asyncio.wait_for(aw, ms / 1000)


def _timeout_error():
    import asyncio

    return asyncio.TimeoutError


def _now_s():
    import time

    return time.time()


def _is_ip(host):
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) < 256 for p in parts)


def _tls_context():
    try:
        import ssl
    except ImportError:
        import tls as ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if hasattr(ctx, "check_hostname"):
        ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE         # no certificate checks, as Art Matrix
    return ctx


def device_id():
    try:
        import machine

        return "".join(f"{b:02x}" for b in machine.unique_id())
    except (ImportError, AttributeError):
        return None


def hostname(config):
    from gitmatrix import _wifi

    name = config.get("wifi_hostname") or _wifi.DEFAULT_HOSTNAME
    return name if _wifi.valid_hostname(name) else _wifi.DEFAULT_HOSTNAME


def valid_topic(text):
    return (isinstance(text, str) and 0 < len(text) <= MAX_TOPIC and not text.startswith("/")
            and not text.endswith("/") and "//" not in text
            and not any(c in text for c in "+#\x00 "))


def settings(config):
    """The effective MQTT settings, or None when switched off / no broker."""
    if not config.get("mqtt_enabled", False) or not config.get("mqtt_host"):
        return None
    tls = bool(config.get("mqtt_tls", False))
    return {
        "host": config.get("mqtt_host"),
        "port": int(config.get("mqtt_port") or (8883 if tls else 1883)),
        "user": config.get("mqtt_user") or None,
        "password": config.get("mqtt_password") or None,
        "tls": tls,
        "discovery": bool(config.get("mqtt_discovery", True)),
        "prefix": config.get("mqtt_discovery_prefix") or "homeassistant",
        "base": config.get("mqtt_base_topic") or "gitmatrix/" + hostname(config),
    }


def _percent(value):
    v = int(float(value) + 0.5)
    if not 0 <= v <= 100:
        raise ValueError("value must be 0-100")
    return v


def _on(text):
    return text.strip().lower() in ("on", "1", "true", "yes")


# ---- the client -------------------------------------------------------------

class Mqtt:
    def __init__(self, config, bus=None):
        self.config = config
        self.bus = bus
        self.state = DISABLED
        self.reason = None          # why we're WAITING
        self.last_error = None
        self.broker = None          # "host:port" of the current/last attempt
        self.backoff_ms = 0
        self.retry_ms = None        # how long the current back-off is
        self.connected_at = None
        self.connects = 0
        self.published = 0
        self.received = 0
        self.commands = 0
        self.dropped = 0            # knob events that didn't fit in the queue
        self.node = None
        self.base = None
        self.prefix = None
        self._event = None
        self._restart = False
        self._writer = None
        self._rx_error = None
        self._rx_at = 0
        self._tx_at = 0
        self._dirty = set()
        self._actions = []
        self._results = []
        self._discover = False
        self._discovered = None     # (prefix, base, node) we published discovery under
        self._plugins = None        # plugin list in the last discovery
        self._alert = None
        self._dns = {}
        if bus is not None:
            bus.subscribe(self.on_event)

    # -- outside API ---------------------------------------------------------

    def reconfigure(self):
        """Settings changed: drop the connection and start again with them."""
        self._restart = True
        self._kick()

    def status(self):
        s = settings(self.config)
        return {
            "state": self.state,
            "connected": self.state == CONNECTED,
            "reason": self.reason,
            "broker": self.broker,
            "base_topic": s["base"] if s else self.config.get("mqtt_base_topic") or "gitmatrix/" + hostname(self.config),
            "node_id": self._node(),
            "last_error": self.last_error,
            "retry_in_s": self.retry_ms // 1000 if self.state == BACKOFF and self.retry_ms else None,
            "connected_for_s": (_compat.ticks_diff(_compat.ticks_ms(), self.connected_at) // 1000
                                if self.state == CONNECTED and self.connected_at is not None else None),
            "connects": self.connects,
            "published": self.published,
            "received": self.received,
            "commands": self.commands,
            "dropped": self.dropped,
        }

    def on_event(self, event):
        """Event-bus listener: runs inside the frame, so only note what changed."""
        if self.state != CONNECTED:
            return
        kind = event["type"]
        if kind == _events.ENCODER:
            action = event.get("action")
            if action == "turn":
                name = "turn_cw" if event.get("delta", 0) > 0 else "turn_ccw"
            elif action in ("press", "long_press"):
                name = action
            else:
                return
            actions = self._actions
            if len(actions) >= MAX_ACTIONS:
                actions.pop(0)
                self.dropped += 1
            actions.append(name)
            self._dirty.add("position")
        else:
            self._dirty.add(kind)
        self._kick()

    # -- the task ------------------------------------------------------------

    def _kick(self):
        if self._event is not None:
            self._event.set()

    async def _wait(self, ms):
        ev = self._event
        try:
            await _wait_for(ev.wait(), ms)
        except _timeout_error():
            pass
        ev.clear()

    def _node(self):
        if self.node is None:
            self.node = "gitmatrix_" + (device_id() or hostname(self.config))
        return self.node

    def _blocked(self, s):
        wifi = _runtime.wifi
        if wifi is None or not wifi.connected():
            return "no WiFi"
        if _flash.streaming() and (s["tls"] or (not _is_ip(s["host"]) and not self._cached(s["host"]))):
            return "waiting for audio to stop"
        return None

    async def run(self):
        import asyncio

        self._event = asyncio.Event()
        while True:
            self._restart = False
            s = settings(self.config)
            if s is None:
                self.state, self.reason, self.retry_ms = DISABLED, None, None
                self.backoff_ms = 0
                await self._wait(60000)
                continue
            reason = self._blocked(s)
            if reason is not None:
                self.state, self.reason = WAITING, reason
                await self._wait(WAIT_MS)
                continue
            self.reason = None
            try:
                await self._session(s)
            except Exception as e:  # noqa: BLE001 - any failure: back off and try again
                text = str(e) if isinstance(e, MqttError) else _compat.error_text(e) or type(e).__name__
                if isinstance(e, _timeout_error()):
                    text = "timed out"
                if self.state == CONNECTED:
                    _log.log(f"mqtt: disconnected ({text})")
                else:
                    _log.log(f"mqtt: {self.broker}: {text}")
                self.last_error = text
            self.state = BACKOFF
            if self._restart:
                self.backoff_ms = 0
                continue
            self.backoff_ms = min(BACKOFF_MAX_MS, max(BACKOFF_MIN_MS, self.backoff_ms * 2))
            self.retry_ms = left = self.backoff_ms
            while left > 0 and not self._restart:
                step = min(left, WAIT_MS)
                await self._wait(step)
                left -= step

    def _cached(self, host):
        hit = self._dns.get(host)
        return hit is not None and _now_s() - hit[1] < DNS_TTL_S

    def _resolve(self, host, port):
        """Broker address; a name is looked up (blocking) at most every DNS_TTL_S."""
        if _is_ip(host):
            return host
        if self._cached(host):
            return self._dns[host][0]
        import socket

        addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1]
        ip = addr[0] if isinstance(addr, tuple) else host
        self._dns = {host: (ip, _now_s())}
        return ip

    async def _session(self, s):
        import asyncio

        self.state = CONNECTING
        self.broker = f"{s['host']}:{s['port']}"
        self.base, self.prefix = s["base"], s["prefix"]
        node = self._node()
        ip = self._resolve(s["host"], s["port"])
        ctx = _tls_context() if s["tls"] else None
        reader, writer = await _wait_for(asyncio.open_connection(ip, s["port"], ssl=ctx), CONNECT_TIMEOUT_MS)
        self._writer = writer
        reader_task = None
        try:
            will = (self.base + "/availability", "offline", True)
            await self._send(connect_packet(node, KEEPALIVE_S, s["user"], s["password"], will))
            first, body = await _wait_for(read_packet(reader, 16), CONNECT_TIMEOUT_MS)
            if first >> 4 != 2 or body is None or len(body) < 2:
                raise MqttError("no CONNACK from the broker")
            if body[1]:
                raise MqttError(CONNACK_ERRORS.get(body[1], f"broker refused ({body[1]})"))
            self.state = CONNECTED
            self.connected_at = self._rx_at = _compat.ticks_ms()
            self.connects += 1
            self.last_error = None
            self.backoff_ms = 0
            self.retry_ms = None
            _log.log(f"mqtt: connected to {self.broker} as {self.base}")
            topics = [self.base + "/" + c for c in COMMANDS]
            if s["discovery"]:
                topics.append(self.prefix + "/status")
            await self._send(subscribe_packet(1, topics))
            await self._publish("availability", "online", True)
            self._rx_error = None
            reader_task = asyncio.create_task(self._read_loop(reader))
            self._discover = s["discovery"]
            if not s["discovery"] and self._discovered is not None:
                await self._remove_discovery()
            self._dirty.update(STATE_KEYS)
            while not self._restart:
                if self._rx_error is not None:
                    raise MqttError(self._rx_error)
                if _runtime.wifi is None or not _runtime.wifi.connected():
                    raise MqttError("WiFi dropped")
                await self._flush()
                now = _compat.ticks_ms()
                if _compat.ticks_diff(now, self._rx_at) > DEAD_MS:
                    raise MqttError("broker stopped answering")
                if _compat.ticks_diff(now, self._tx_at) > PING_MS:
                    await self._send(PINGREQ)
                if self._restart:
                    break
                await self._wait(IDLE_MS)
            await self._goodbye()
        finally:
            if reader_task is not None:
                reader_task.cancel()
            self._writer = None
            self._actions.clear()
            self._results.clear()
            self._dirty.clear()
            try:
                writer.close()
            except Exception:  # noqa: BLE001, S110 - already gone
                pass

    async def _goodbye(self):
        """Settings changed: tidy up on the old connection before leaving."""
        new = settings(self.config)
        if self._discovered is not None and new is not None and (
                not new["discovery"] or (new["prefix"], new["base"], new["host"], new["port"]) != (
                    self._discovered[0], self._discovered[1], self._discovered[3], self._discovered[4])):
            await self._remove_discovery()
        await self._publish("availability", "offline", True)
        await self._send(DISCONNECT)
        _log.log("mqtt: disconnected (settings changed)")

    async def _send(self, data):
        w = self._writer
        if w is None:
            raise MqttError("not connected")
        w.write(data)
        await _wait_for(w.drain(), WRITE_TIMEOUT_MS)
        self._tx_at = _compat.ticks_ms()

    async def _publish(self, sub, payload, retain=False):
        await self._send(publish_packet(self.base + "/" + sub, payload, retain))
        self.published += 1

    async def _read_loop(self, reader):
        try:
            while True:
                first, body = await read_packet(reader)
                self._rx_at = _compat.ticks_ms()
                kind = first >> 4
                if kind == 3 and body is not None:
                    self.received += 1
                    self._on_publish(*parse_publish(first, body))
                elif kind == 3:
                    self.received += 1
                    self._result("?", {"ok": False, "error": "message too big"})
                elif kind == 9 and body is not None and b"\x80" in bytes(body[2:]):
                    _log.log("mqtt: the broker refused a subscription")
        except Exception as e:  # noqa: BLE001 - tell the writer, which reconnects
            self._rx_error = "connection closed" if isinstance(e, EOFError) else (_compat.error_text(e) or type(e).__name__)
            self._kick()

    # -- inbound -------------------------------------------------------------

    def _on_publish(self, topic, payload, retained):
        if topic == self.prefix + "/status":
            if payload.strip() == b"online":
                # Home Assistant restarted: send everything again
                self._discover = settings(self.config) is not None and bool(self.config.get("mqtt_discovery", True))
                self._dirty.update(STATE_KEYS)
                self._kick()
            return
        base = self.base + "/"
        if not topic.startswith(base):
            return
        command = topic[len(base):]
        handler = COMMANDS.get(command)
        if handler is None or retained:
            return              # a retained command would re-run on every connect
        self.commands += 1
        try:
            text = payload.decode("utf-8")
        except UnicodeError:
            self._result(command, {"ok": False, "error": "payload must be UTF-8"})
            return
        try:
            out = getattr(self, handler)(text)
        except _notify.NotifyError as e:
            out = {"ok": False, "error": _compat.error_text(e), "status": e.status}
        except (ValueError, TypeError, KeyError) as e:
            out = {"ok": False, "error": _compat.error_text(e)}
        except Exception as e:  # noqa: BLE001 - a bad command must not drop the connection
            _log.log(f"mqtt: {command}: {_compat.error_text(e)}")
            out = {"ok": False, "error": _compat.error_text(e)}
        self._result(command, out)

    def _result(self, command, out):
        out = dict(out or {"ok": True})
        out.setdefault("ok", True)
        out["command"] = command
        if len(self._results) >= MAX_RESULTS:
            self._results.pop(0)
        self._results.append(out)
        self._kick()

    def _cmd_plugin(self, text):
        name = text.strip()
        if name not in self.config.get("plugins", []):
            raise ValueError(f"no plugin '{name}'")
        if not _runtime.host.show(name):
            raise ValueError(f"{name} failed to load")
        return {"now_showing": name}

    def _cmd_next(self, _text):
        _runtime.host.step(1, _compat.ticks_ms())

    def _cmd_prev(self, _text):
        _runtime.host.step(-1, _compat.ticks_ms())

    def _cmd_brightness(self, text):
        self.config.set("brightness", _percent(text))
        _runtime.host.apply_brightness()

    def _cmd_volume(self, text):
        self.config.set("volume", int(_percent(text) * 127 / 100 + 0.5))
        _runtime.host.apply_volume()

    def _cmd_alert_sound(self, text):
        self.config.set("alert_sound", _on(text))
        self._dirty.add("alert_sound")

    def _cmd_notify(self, text):
        n = _runtime.notifier
        if n is None:
            raise ValueError("notifications are not running")
        body = _json_object(text)
        note = n.submit(body if body is not None else {"text": text.strip()})
        return {"id": note.id, "queued": len(n.queue)}

    def _cmd_dismiss(self, text):
        n = _runtime.notifier
        if n is None:
            raise ValueError("notifications are not running")
        nid = text.strip()
        if nid.upper() == "PRESS":          # the Home Assistant button's payload
            nid = ""
        return {"dismissed": n.dismiss(nid or None)}

    def _cmd_sound(self, text):
        from gitmatrix import _web

        body = _json_object(text)
        out, status = _web.play_request(body if body is not None else {"sound": text.strip()})
        if status >= 400:
            raise ValueError(out.get("error", f"error {status}"))
        return out

    # -- outbound ------------------------------------------------------------

    def _value(self, key):
        bus_state = self.bus.state if self.bus is not None else {}
        cfg = self.config
        if key == _events.PLUGIN:
            host = _runtime.host
            if key in bus_state:
                return bus_state[key] or ""
            return host.current.name if host is not None and host.current is not None else ""
        if key == _events.MODE:
            return bus_state.get(key) or "plugin"
        if key == _events.BRIGHTNESS:
            return str(cfg.get("brightness", 60))
        if key == _events.VOLUME:
            return str(int(cfg.get("volume", 64) * 100 / 127 + 0.5))
        if key == "alert_sound":
            return "ON" if cfg.get("alert_sound", True) else "OFF"
        if key == "position":
            return str(self.bus.position if self.bus is not None else 0)
        if key == _events.BLUETOOTH:
            audio = _runtime.audio
            value = bus_state.get(key)
            if value is None:
                value = {"connected": bool(audio is not None and audio.connected()),
                         "streaming": bool(audio is not None and audio.streaming())}
            return json.dumps(value)
        if key == _events.WIFI:
            wifi = _runtime.wifi
            value = bus_state.get(key)
            if value is None:
                ip = wifi.ip if wifi is not None and wifi.connected() else None
                value = {"connected": ip is not None, "ip": ip}
            return json.dumps(value)
        if key == _events.NOTIFICATION:
            active = bus_state.get(key)
            return json.dumps({"showing": active is not None, "id": active})
        return None

    async def _flush(self):
        if self._discover:
            self._discover = False
            await self._publish_discovery()
        elif self._discovered is not None and self._options() != self._plugins:
            await self._publish_discovery(only="select")
        alert = self.config.get("alert_sound", True)
        if alert != self._alert:
            self._dirty.add("alert_sound")
        while self._actions:
            name = self._actions.pop(0)
            payload = json.dumps({"event_type": name, "position": self.bus.position if self.bus is not None else 0})
            await self._publish("encoder", payload)
            await self._publish("encoder/action", name)
        while self._results:
            await self._publish("result", json.dumps(self._results.pop(0)))
        if self._dirty:
            dirty, self._dirty = self._dirty, set()
            for key in STATE_KEYS:
                if key in dirty:
                    value = self._value(key)
                    if key == "alert_sound":
                        self._alert = alert
                    await self._publish("encoder/position" if key == "position" else key, value, True)

    # -- Home Assistant discovery ---------------------------------------------

    def _device(self):
        cfg = self.config
        wifi = _runtime.wifi
        name = hostname(cfg)
        device = {
            "identifiers": [self.node],
            "name": "GitMatrix" if name == "gitmatrix" else f"GitMatrix {name}",
            "manufacturer": "Pimoroni",
            "model": "GitHub Contribution Widget",
            "sw_version": gitmatrix.VERSION,
        }
        url = wifi.url() if wifi is not None and wifi.connected() else None
        if url:
            device["configuration_url"] = url
        return device

    def _entities(self):
        """(component, object id, config) for everything we expose."""
        b = self.base + "/"
        sounds = list(_sounds.BUILTIN) + [n for n in _sounds.custom_names() if n not in _sounds.BUILTIN]
        on_off = "{{ 'ON' if value_json.%s else 'OFF' }}"
        out = [
            ("select", "plugin", {"name": "Plugin", "icon": "mdi:view-carousel", "state_topic": b + "plugin",
                                  "command_topic": b + "plugin/set", "options": self._options()}),
            ("button", "next", {"name": "Next plugin", "icon": "mdi:skip-next", "command_topic": b + "plugin/next"}),
            ("button", "prev", {"name": "Previous plugin", "icon": "mdi:skip-previous", "command_topic": b + "plugin/prev"}),
            ("number", "brightness", {"name": "Brightness", "icon": "mdi:brightness-6", "min": 0, "max": 100, "step": 1,
                                      "unit_of_measurement": "%", "mode": "slider", "state_topic": b + "brightness",
                                      "command_topic": b + "brightness/set"}),
            ("number", "volume", {"name": "Volume", "icon": "mdi:volume-high", "min": 0, "max": 100, "step": 1,
                                  "unit_of_measurement": "%", "mode": "slider", "state_topic": b + "volume",
                                  "command_topic": b + "volume/set"}),
            ("notify", "notify", {"name": "Notify", "command_topic": b + "notify"}),
            ("button", "dismiss", {"name": "Dismiss notification", "icon": "mdi:bell-cancel", "command_topic": b + "notify/dismiss"}),
            ("select", "sound", {"name": "Play sound", "icon": "mdi:bell-ring", "command_topic": b + "sound", "options": sounds}),
            ("switch", "alert_sound", {"name": "Alert sounds", "icon": "mdi:bell", "entity_category": "config",
                                       "state_topic": b + "alert_sound", "command_topic": b + "alert_sound/set"}),
            ("event", "knob", {"name": "Knob", "icon": "mdi:knob", "state_topic": b + "encoder",
                               "event_types": list(ENCODER_TYPES)}),
            ("sensor", "knob_position", {"name": "Knob position", "icon": "mdi:knob", "state_topic": b + "encoder/position",
                                         "state_class": "measurement"}),
            ("binary_sensor", "notification", {"name": "Notification showing", "icon": "mdi:bell-badge",
                                               "state_topic": b + "notification", "value_template": on_off % "showing"}),
            ("binary_sensor", "bt_connected", {"name": "Bluetooth connected", "device_class": "connectivity",
                                               "entity_category": "diagnostic", "state_topic": b + "bluetooth",
                                               "value_template": on_off % "connected"}),
            ("binary_sensor", "bt_streaming", {"name": "Bluetooth audio playing", "icon": "mdi:music",
                                               "state_topic": b + "bluetooth", "value_template": on_off % "streaming"}),
            ("sensor", "ip", {"name": "IP address", "icon": "mdi:ip-network", "entity_category": "diagnostic",
                              "state_topic": b + "wifi", "value_template": "{{ value_json.ip }}"}),
            ("sensor", "mode", {"name": "Mode", "icon": "mdi:monitor", "entity_category": "diagnostic",
                                "state_topic": b + "mode"}),
        ]
        for action in ENCODER_TYPES:
            out.append(("device_automation", "knob_" + action, {
                "automation_type": "trigger", "topic": b + "encoder/action", "payload": action,
                "type": {"press": "button_short_press", "long_press": "button_long_press"}.get(action, action),
                "subtype": "knob"}))
        return out

    def _options(self):
        """The plugin select's options: the rotation (enabled plugins)."""
        off = self.config.get("disabled_plugins", [])
        return [p for p in self.config.get("plugins", []) if p not in off]

    def _topic(self, component, obj, node=None, prefix=None):
        return f"{prefix or self.prefix}/{component}/{node or self.node}/{obj}/config"

    async def _publish_discovery(self, only=None):
        device = self._device()
        origin = {"name": "GitMatrix", "sw_version": gitmatrix.VERSION}
        avail = self.base + "/availability"
        for component, obj, conf in self._entities():
            if only is not None and component != only:
                continue
            if component != "device_automation":
                conf["unique_id"] = f"{self.node}_{obj}"
                conf["availability_topic"] = avail
            conf["device"] = device
            conf["origin"] = origin
            await self._send(publish_packet(self._topic(component, obj), json.dumps(conf), True))
            self.published += 1
        s = settings(self.config)
        self._discovered = (self.prefix, self.base, self.node, s["host"] if s else None, s["port"] if s else None)
        self._plugins = self._options()

    async def _remove_discovery(self):
        prefix, _base, node = self._discovered[:3]
        for component, obj, _conf in self._entities():
            await self._send(publish_packet(self._topic(component, obj, node, prefix), b"", True))
            self.published += 1
        self._discovered = None


def _json_object(text):
    t = text.strip()
    if not t.startswith("{"):
        return None
    try:
        body = json.loads(t)
    except ValueError as e:
        raise _notify.NotifyError("bad JSON") from e
    return body if isinstance(body, dict) else None
