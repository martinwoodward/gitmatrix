# The event bus behind /api/v1/events (and, later, MQTT).

import asyncio

import pytest

from gitmatrix import _events
from gitmatrix._events import Bus


@pytest.fixture
def fast(monkeypatch):
    async def tick(_ms):
        await asyncio.sleep(0)

    monkeypatch.setattr(_events, "sleep_ms", tick)


def seqs(out):
    return [e["seq"] for e in out["events"]]


def test_seq_since_and_dropped():
    bus = Bus(size=4)
    for i in range(3):
        bus.emit("volume", {"value": i}, 0)
    out = bus.read(None, now=10)
    assert seqs(out) == [1, 2, 3] and out["next"] == 3 and out["dropped"] == 0 and not out["reset"]
    assert out["events"][0] == {"seq": 1, "type": "volume", "value": 0, "age_ms": 10}
    assert seqs(bus.read(2)) == [3]
    assert bus.read(3)["events"] == []
    for i in range(5):
        bus.emit("volume", {"value": i}, 0)
    # seq 4 fell off the ring before a client at 3 got it
    out = bus.read(3)
    assert seqs(out) == [5, 6, 7, 8] and out["dropped"] == 1
    assert bus.read(0)["dropped"] == 4
    assert bus.read(None)["dropped"] == 0


def test_since_from_a_previous_boot_resets():
    bus = Bus()
    bus.emit("plugin", {"name": "a"})
    out = bus.read(500)
    assert out["reset"] and seqs(out) == [1] and out["dropped"] == 0 and out["boot"] == bus.boot
    assert bus.pending(500)


def test_change_only_emits_on_change_and_types_filter():
    bus = Bus()
    assert bus.change("volume", 50)["previous"] is None
    assert bus.change("volume", 50) is None
    assert bus.change("volume", 60) == {"seq": 2, "type": "volume", "t": bus.ring[2]["t"], "previous": 50, "value": 60}
    bus.emit("plugin", {"name": "x"})
    assert [e["type"] for e in bus.read(0, ("plugin",))["events"]] == ["plugin"]
    assert bus.pending(2, ("plugin",)) and not bus.pending(3, ("plugin",)) and not bus.pending(2, ("wifi",))
    assert bus.state == {"volume": 60}


def test_listeners_see_everything_and_cannot_break_emit():
    bus = Bus()
    seen = []
    bus.subscribe(seen.append)
    bus.subscribe(lambda e: 1 / 0)
    bus.emit("wifi", {"connected": True})
    assert seen[0]["type"] == "wifi" and bus.seq == 1
    bus.unsubscribe(seen.append)
    bus.emit("wifi")
    assert len(seen) == 1


def test_parse_types():
    assert _events.parse_types(None) is None
    assert _events.parse_types("encoder, plugin") == ("encoder", "plugin")
    with pytest.raises(ValueError):
        _events.parse_types("encoder,nope")


def test_wait_wakes_on_event_and_times_out(fast):
    bus = Bus()

    async def go():
        task = asyncio.create_task(bus.wait(0, None, 30000))
        await asyncio.sleep(0)
        assert bus.waiters == 1
        bus.emit("volume", {"value": 1})
        assert await task
        assert bus.waiters == 0
        assert not await bus.wait(1, None, 200)     # nothing new: times out
        assert not await bus.wait(1, ("plugin",), 200)

    asyncio.run(go())
