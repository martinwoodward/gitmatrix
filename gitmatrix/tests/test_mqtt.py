# The MQTT client and Home Assistant discovery, against a fake broker on 127.0.0.1.

import asyncio
import json

import pytest
import stubs
from conftest import make_plugin, set_plugins
from test_web import call, ok

from gitmatrix import _mqtt, _runtime, _web

GOOD = "def update():\n    pass\n"
BASE = "gitmatrix/gitmatrix"
NODE = "gitmatrix_0102030405060708"


class Broker:
    """Just enough MQTT 3.1.1 broker: CONNACK, SUBACK, PINGRESP, retained messages."""

    def __init__(self, connack=0):
        self.connack = connack
        self.connects = []
        self.messages = []          # (topic, payload, retain)
        self.retained = {}
        self.subs = []
        self.clients = []
        self.disconnects = 0
        self.closed = 0

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    def stop(self):
        self.server.close()
        for w in self.clients:
            w.close()

    async def _handle(self, reader, writer):
        self.clients.append(writer)
        try:
            while True:
                first, body = await _mqtt.read_packet(reader)
                kind = first >> 4
                if kind == 1:
                    self.connects.append(parse_connect(body))
                    writer.write(bytes((0x20, 2, 0, self.connack)))
                    if self.connack:
                        await writer.drain()
                        return
                elif kind == 3:
                    topic, payload, retain = _mqtt.parse_publish(first, body)
                    self.messages.append((topic, payload, retain))
                    if retain and payload:
                        self.retained[topic] = payload
                    elif retain:
                        self.retained.pop(topic, None)
                elif kind == 8:
                    i = 2
                    while i < len(body):
                        n = int.from_bytes(body[i:i + 2], "big")
                        self.subs.append(body[i + 2:i + 2 + n].decode())
                        i += 3 + n
                    writer.write(bytes((0x90, 3)) + body[:2] + b"\x00")
                elif kind == 12:
                    writer.write(b"\xd0\x00")
                elif kind == 14:
                    self.disconnects += 1
                    return
                await writer.drain()
        except (EOFError, ConnectionError):
            pass
        finally:
            self.closed += 1
            if writer in self.clients:
                self.clients.remove(writer)
            writer.close()

    def send(self, topic, payload, retain=False):
        for w in self.clients:
            w.write(_mqtt.publish_packet(topic, payload, retain))

    def drop(self):
        for w in list(self.clients):
            w.close()

    def published(self, topic):
        return [p for t, p, _ in self.messages if t == topic]

    def results(self):
        return [json.loads(p) for p in self.published(BASE + "/result")]


def parse_connect(body):
    def text(i):
        n = int.from_bytes(body[i:i + 2], "big")
        return body[i + 2:i + 2 + n].decode(), i + 2 + n

    proto, i = text(0)
    level, flags = body[i], body[i + 1]
    keepalive = int.from_bytes(body[i + 2:i + 4], "big")
    out = {"proto": proto, "level": level, "flags": flags, "keepalive": keepalive}
    out["client_id"], i = text(i + 4)
    if flags & 0x04:
        out["will_topic"], i = text(i)
        out["will"], i = text(i)
        out["will_retain"] = bool(flags & 0x20)
    if flags & 0x80:
        out["user"], i = text(i)
    if flags & 0x40:
        out["password"], i = text(i)
    return out


async def until(cond, timeout=3.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting")


@pytest.fixture
def dev(rig, monkeypatch):
    """Booted runtime on WiFi, routes registered, fast MQTT back-off."""
    for name in ("one", "two", "three"):
        make_plugin(rig.root, name, GOOD, {"name": name.title()})
    set_plugins(rig.root, ["one", "two", "three"], wifi_ssid="home", wifi_password="secret123")
    rig.net = stubs.FakeNetwork()
    rt = rig.boot()
    rig.net.sta.up = True
    rt.wifi.tick(rig.clock.advance(500))
    _web.setup(rt)
    spawned = []
    monkeypatch.setattr(_web, "spawn", spawned.append)
    rig.spawned = spawned
    monkeypatch.setattr(_mqtt, "BACKOFF_MIN_MS", 20)
    monkeypatch.setattr(_mqtt, "WAIT_MS", 20)
    yield rig
    for coro in spawned:
        coro.close()


def enable(rig, broker, **extra):
    cfg = rig.rt.config
    cfg.set("mqtt_enabled", True)
    cfg.set("mqtt_host", "127.0.0.1")
    cfg.set("mqtt_port", broker.port)
    for k, v in extra.items():
        cfg.set("mqtt_" + k, v)


def start_client(rig):
    client = _runtime.mqtt = rig.rt.mqtt = _mqtt.Mqtt(rig.rt.config, rig.rt.events)
    return client, asyncio.create_task(client.run())


async def online(broker, client):
    await until(lambda: client.state == _mqtt.CONNECTED and broker.retained.get(BASE + "/availability") == b"online"
                and broker.retained.get(BASE + "/plugin") is not None)


# ---- packets ------------------------------------------------------------------

def test_packet_encoding():
    assert [_mqtt._varint(n) for n in (0, 127, 128, 16383, 16384)] == [  # noqa: SLF001
        b"\x00", b"\x7f", b"\x80\x01", b"\xff\x7f", b"\x80\x80\x01"]
    pkt = _mqtt.connect_packet("cid", 60, "u", "p", ("t/a", "offline", True))
    assert pkt[0] == 0x10 and pkt[1] == len(pkt) - 2
    c = parse_connect(pkt[2:])
    assert c == {"proto": "MQTT", "level": 4, "flags": 0x02 | 0x04 | 0x20 | 0x80 | 0x40, "keepalive": 60,
                 "client_id": "cid", "will_topic": "t/a", "will": "offline", "will_retain": True, "user": "u", "password": "p"}
    assert parse_connect(_mqtt.connect_packet("x")[2:])["flags"] == 0x02
    pub = _mqtt.publish_packet("a/b", "hi", retain=True)
    assert pub == b"\x31\x07\x00\x03a/bhi"
    assert _mqtt.parse_publish(pub[0], pub[2:]) == ("a/b", b"hi", True)
    assert _mqtt.subscribe_packet(1, ["x"]) == b"\x82\x06\x00\x01\x00\x01x\x00"


def test_read_packet_caps_size():
    class Reader:
        def __init__(self, data):
            self.data = data

        async def readexactly(self, n):
            if len(self.data) < n:
                raise EOFError
            out, self.data = self.data[:n], self.data[n:]
            return out

    big = _mqtt.publish_packet("t", b"x" * 300)
    r = Reader(big + _mqtt.publish_packet("t", b"ok"))
    assert asyncio.run(_mqtt.read_packet(r, limit=100)) == (0x30, None)      # drained, dropped
    first, body = asyncio.run(_mqtt.read_packet(r, limit=100))
    assert _mqtt.parse_publish(first, body) == ("t", b"ok", False)
    with pytest.raises(EOFError):
        asyncio.run(_mqtt.read_packet(r))


def test_topic_validation_and_tls_context():
    assert _mqtt.valid_topic("home/gitmatrix")
    for bad in ("", "/x", "x/", "a//b", "a/+", "a/#", "a b", "x" * 65):
        assert not _mqtt.valid_topic(bad)
    import ssl
    ctx = _mqtt._tls_context()  # noqa: SLF001
    assert ctx.verify_mode == ssl.CERT_NONE and not ctx.check_hostname


# ---- the client ---------------------------------------------------------------

def test_off_by_default_never_connects(dev):
    async def scenario():
        broker = await Broker().start()
        assert _mqtt.settings(dev.rt.config) is None
        client, task = start_client(dev)
        await asyncio.sleep(0.1)
        assert client.state == _mqtt.DISABLED and broker.connects == []
        task.cancel()
        broker.stop()

    asyncio.run(scenario())


def test_connect_discovery_and_state(dev):
    async def scenario():
        broker = await Broker().start()
        enable(dev, broker, user="ha", password="pw")
        client, task = start_client(dev)
        await online(broker, client)
        c = broker.connects[0]
        assert c["client_id"] == NODE and c["user"] == "ha" and c["password"] == "pw"
        assert (c["will_topic"], c["will"], c["will_retain"]) == (BASE + "/availability", "offline", True)
        assert c["keepalive"] == 60
        assert set(broker.subs) == {BASE + "/" + t for t in _mqtt.COMMANDS} | {"homeassistant/status"}
        r = broker.retained
        assert r[BASE + "/plugin"] == b"one" and r[BASE + "/brightness"] == b"60" and r[BASE + "/volume"] == b"50"
        assert r[BASE + "/alert_sound"] == b"ON" and r[BASE + "/encoder/position"] == b"0"
        assert json.loads(r[BASE + "/wifi"]) == {"connected": True, "ip": "192.168.1.42"}
        assert json.loads(r[BASE + "/notification"]) == {"showing": False, "id": None}
        select = json.loads(r[f"homeassistant/select/{NODE}/plugin/config"])
        assert select["options"] == ["one", "two", "three"] and select["unique_id"] == NODE + "_plugin"
        assert select["command_topic"] == BASE + "/plugin/set" and select["availability_topic"] == BASE + "/availability"
        assert select["device"]["identifiers"] == [NODE] and select["device"]["configuration_url"] == "http://192.168.1.42"
        event = json.loads(r[f"homeassistant/event/{NODE}/knob/config"])
        assert event["event_types"] == ["turn_cw", "turn_ccw", "press", "long_press"]
        trig = json.loads(r[f"homeassistant/device_automation/{NODE}/knob_press/config"])
        assert trig["type"] == "button_short_press" and trig["payload"] == "press" and trig["topic"] == BASE + "/encoder/action"
        for comp, obj in (("number", "brightness"), ("number", "volume"), ("notify", "notify"), ("button", "next"),
                          ("button", "dismiss"), ("select", "sound"), ("switch", "alert_sound"), ("sensor", "ip"),
                          ("binary_sensor", "bt_connected"), ("binary_sensor", "bt_streaming"), ("binary_sensor", "notification")):
            assert f"homeassistant/{comp}/{NODE}/{obj}/config" in r, (comp, obj)
        assert "chime" in json.loads(r[f"homeassistant/select/{NODE}/sound/config"])["options"]
        s = ok("GET", "/api/v1/mqtt")["status"]
        assert s["connected"] and s["state"] == "connected" and s["node_id"] == NODE and s["connects"] == 1
        task.cancel()
        broker.stop()

    asyncio.run(scenario())


def test_bus_changes_and_knob_events(dev):
    async def scenario():
        broker = await Broker().start()
        enable(dev, broker)
        client, task = start_client(dev)
        await online(broker, client)
        dev.turn(1)
        dev.turn(-1)
        await until(lambda: len(broker.published(BASE + "/encoder")) == 2)
        events = [json.loads(p) for p in broker.published(BASE + "/encoder")]
        assert [e["event_type"] for e in events] == ["turn_cw", "turn_ccw"]
        assert broker.published(BASE + "/encoder/action") == [b"turn_cw", b"turn_ccw"]
        assert broker.retained[BASE + "/encoder/position"] == b"0"
        ok("POST", "/api/v1/plugins/three/show")
        dev.frames()
        await until(lambda: broker.retained[BASE + "/plugin"] == b"three")
        ok("POST", "/api/v1/brightness", {"value": 25})
        await until(lambda: broker.retained[BASE + "/brightness"] == b"25")
        # the plugin list changing re-announces the select's options
        ok("POST", "/api/v1/plugins/order", {"order": ["three", "one", "two"] + dev.rt.config.get("plugins")[3:]})
        client._kick()  # noqa: SLF001
        await until(lambda: json.loads(broker.retained[f"homeassistant/select/{NODE}/plugin/config"])["options"][0] == "three")
        task.cancel()
        broker.stop()

    asyncio.run(scenario())


def test_knob_queue_is_bounded(dev):
    client = _mqtt.Mqtt(dev.rt.config, dev.rt.events)
    client.state = _mqtt.CONNECTED
    for _ in range(_mqtt.MAX_ACTIONS + 5):
        dev.turn(1)
    assert len(client._actions) == _mqtt.MAX_ACTIONS and client.dropped == 5  # noqa: SLF001
    client.state = _mqtt.BACKOFF
    dev.turn(1)
    assert len(client._actions) == _mqtt.MAX_ACTIONS  # noqa: SLF001 - nothing recorded while offline


def test_commands(dev, bt):
    async def scenario():
        broker = await Broker().start()
        enable(dev, broker)
        client, task = start_client(dev)
        await online(broker, client)
        rt = dev.rt

        async def command(topic, payload, retain=False):
            n = len(broker.results())
            broker.send(BASE + "/" + topic, payload, retain)
            await until(lambda: len(broker.results()) > n)
            return broker.results()[-1]

        assert (await command("plugin/set", "two")) == {"ok": True, "command": "plugin/set", "now_showing": "two"}
        assert rt.host.current.name == "two"
        bad = await command("plugin/set", "nope")
        assert not bad["ok"] and "nope" in bad["error"]
        await command("plugin/next", "PRESS")
        assert rt.host.current.name == "three"
        await command("plugin/prev", "")
        assert rt.host.current.name == "two"
        await command("brightness/set", "30")
        assert rt.config.get("brightness") == 30
        await until(lambda: broker.retained[BASE + "/brightness"] == b"30")
        await command("volume/set", "100")
        assert rt.config.get("volume") == 127 and bt.vol == 127
        assert not (await command("volume/set", "101"))["ok"]
        res = await command("notify", json.dumps({"text": "Build passed", "id": "ci"}))
        assert res["ok"] and res["id"] == "ci"
        res = await command("notify", "Plain words")
        assert res["ok"] and rt.notifier.queue.items[-1].text == "Plain words"
        assert not (await command("notify", "{bad json"))["ok"]
        assert not (await command("notify", json.dumps({"nope": 1})))["ok"]
        assert (await command("notify/dismiss", "PRESS"))["dismissed"] >= 2
        res = await command("sound", "chime")
        assert res == {"ok": True, "command": "sound", "played": True, "result": "play"}
        assert len(dev.spawned) == 1
        assert not (await command("sound", "nonesuch"))["ok"]
        assert not (await command("sound", json.dumps({"sound": "chime", "bogus": 1})))["ok"]
        await command("alert_sound/set", "OFF")
        assert rt.config.get("alert_sound") is False
        await until(lambda: broker.retained[BASE + "/alert_sound"] == b"OFF")
        # a retained command is ignored (it would re-run on every connect)
        n = client.commands
        broker.send(BASE + "/plugin/set", "one", retain=True)
        await command("plugin/next", "")
        assert client.commands == n + 1 and rt.host.current.name == "three"
        task.cancel()
        broker.stop()

    asyncio.run(scenario())


def test_home_assistant_restart_resends_discovery(dev):
    async def scenario():
        broker = await Broker().start()
        enable(dev, broker)
        client, task = start_client(dev)
        await online(broker, client)
        topic = f"homeassistant/select/{NODE}/plugin/config"
        assert len(broker.published(topic)) == 1
        broker.send("homeassistant/status", "online")
        await until(lambda: len(broker.published(topic)) == 2)
        task.cancel()
        broker.stop()

    asyncio.run(scenario())


def test_reconnects_after_broker_drops(dev):
    async def scenario():
        broker = await Broker().start()
        enable(dev, broker)
        client, task = start_client(dev)
        await online(broker, client)
        broker.drop()
        await until(lambda: client.connects == 2 and client.state == _mqtt.CONNECTED)
        assert len(broker.connects) == 2
        task.cancel()
        broker.stop()

    asyncio.run(scenario())


def test_refused_login_backs_off(dev):
    async def scenario():
        broker = await Broker(connack=4).start()
        enable(dev, broker, user="ha", password="wrong")
        client, task = start_client(dev)
        await until(lambda: len(broker.connects) >= 2)
        assert client.last_error == "bad user name or password" and client.connects == 0
        assert client.backoff_ms >= 2 * _mqtt.BACKOFF_MIN_MS
        task.cancel()
        broker.stop()

    asyncio.run(scenario())


def test_waits_for_wifi_and_for_audio(dev, bt):
    async def scenario():
        broker = await Broker().start()
        enable(dev, broker)
        dev.net.sta.up = False
        dev.rt.wifi.tick(dev.clock.advance(500))
        assert not dev.rt.wifi.connected()
        client, task = start_client(dev)
        await asyncio.sleep(0.1)
        assert client.state == _mqtt.WAITING and client.reason == "no WiFi" and broker.connects == []
        task.cancel()
        broker.stop()

    asyncio.run(scenario())
    s = _mqtt.settings(dev.rt.config)
    client = _mqtt.Mqtt(dev.rt.config)
    dev.net.sta.up = True
    dev.rt.wifi.tick(dev.clock.advance(500))
    bt.streaming_now = True
    assert client._blocked(s) is None  # noqa: SLF001 - a plain connection to an IP is fine
    assert client._blocked(dict(s, tls=True)) == "waiting for audio to stop"  # noqa: SLF001
    assert client._blocked(dict(s, host="broker.lan")) == "waiting for audio to stop"  # noqa: SLF001
    bt.streaming_now = False
    assert client._blocked(dict(s, tls=True)) is None  # noqa: SLF001


# ---- settings via the web API -----------------------------------------------------

def test_api_settings_are_validated_and_password_is_write_only(dev):
    s = ok("GET", "/api/v1/mqtt")
    assert s["enabled"] is False and s["port"] is None and s["default_port"] == 1883 and s["password_set"] is False
    assert s["base_topic"] is None and s["default_base_topic"] == s["topic"] == BASE and s["discovery_prefix"] == "homeassistant"
    assert s["status"]["state"] == "unavailable"
    s = ok("POST", "/api/v1/mqtt", {"host": "192.168.1.5", "user": "ha", "password": "s3cret", "tls": True})
    assert s["password_set"] and "s3cret" not in json.dumps(s) and s["default_port"] == 8883
    assert dev.rt.config.get("mqtt_password") == "s3cret"
    assert "s3cret" not in json.dumps(ok("GET", "/api/v1/settings"))
    for body in ({"port": 70000}, {"host": "has space"}, {"base_topic": "a/#"}, {"bogus": 1},
                 {"discovery_prefix": "/x"}, {"enabled": True, "host": ""}):
        assert call("POST", "/api/v1/mqtt", body).status == 400, body
    # all or nothing: a bad field leaves the earlier good ones alone
    assert call("POST", "/api/v1/mqtt", {"user": "other", "port": 0}).status == 400
    assert dev.rt.config.get("mqtt_user") == "ha"
    s = ok("POST", "/api/v1/mqtt", {"password": ""})
    assert not s["password_set"] and dev.rt.config.get("mqtt_password") is None


def test_api_save_deferred_while_streaming(dev, bt):
    bt.streaming_now = True
    ok("POST", "/api/v1/mqtt", {"host": "10.0.0.2"})
    assert dev.rt.config.dirty
    assert "10.0.0.2" not in (dev.root / "config.py").read_text()
    bt.streaming_now = False
    ok("POST", "/api/v1/mqtt", {"enabled": False})
    assert "mqtt_host = '10.0.0.2'" in (dev.root / "config.py").read_text()


def test_settings_apply_in_place_without_reboot(dev):
    async def scenario():
        broker = await Broker().start()
        client, task = start_client(dev)
        await asyncio.sleep(0.05)
        assert client.state == _mqtt.DISABLED
        ok("POST", "/api/v1/mqtt", {"enabled": True, "host": "127.0.0.1", "port": broker.port})
        await online(broker, client)
        old = f"homeassistant/select/{NODE}/plugin/config"
        assert old in broker.retained
        # a new base topic: the old connection says goodbye and removes its entities
        ok("POST", "/api/v1/mqtt", {"base_topic": "home/matrix"})
        await until(lambda: broker.retained.get("home/matrix/availability") == b"online")
        assert broker.disconnects == 1 and client.connects == 2
        assert ("gitmatrix/gitmatrix/availability", b"offline", True) in broker.messages
        assert json.loads(broker.retained[old])["command_topic"] == "home/matrix/plugin/set"
        # discovery off: entities removed, client stays connected
        ok("POST", "/api/v1/mqtt", {"discovery": False})
        await until(lambda: old not in broker.retained and client.connects == 3)
        # switched off: offline + DISCONNECT, then idle
        ok("POST", "/api/v1/mqtt", {"enabled": False})
        await until(lambda: client.state == _mqtt.DISABLED and broker.disconnects == 3)
        assert broker.retained["home/matrix/availability"] == b"offline"
        assert _runtime.reboot_at is None       # never a reboot
        task.cancel()
        broker.stop()

    asyncio.run(scenario())
