import json

import pytest

import gitmatrix
from gitmatrix import _state
from gitmatrix._state import Settings, State

SCHEMA = [
    {"key": "count", "type": "int", "min": 1, "max": 10, "default": 3},
    {"key": "ratio", "type": "number", "default": 0.5},
    {"key": "on", "type": "bool", "default": False},
    {"key": "mode", "type": "choice", "choices": ["a", ["b", "Bee"]], "default": "a"},
    {"key": "token", "type": "secret"},
]


def test_state_round_trip(root, clock):
    s = State("demo")
    s["score"] = 5
    s.setdefault("level", 1)
    assert s.dirty
    clock.advance(31000)
    assert s.flush()
    assert json.loads((root / "data" / "demo" / "state.json").read_text()) == {"score": 5, "level": 1}
    assert State("demo")["score"] == 5


def test_state_writes_are_rate_limited(root, clock):
    s = State("demo")
    clock.advance(31000)
    s["a"] = 1
    assert s.flush()
    s["a"] = 2
    assert not s.flush()
    clock.advance(31000)
    assert s.flush()


def test_state_defers_while_streaming_but_not_forever(root, clock):
    s = State("demo")
    clock.advance(31000)
    s["a"] = 1
    assert not s.flush(streaming=True)
    clock.advance(_state.MAX_DEFER_MS + 1)
    assert s.flush(streaming=True)


def test_state_save_now(root):
    s = State("demo")
    s["a"] = 1
    s.save(now=True)
    assert (root / "data" / "demo" / "state.json").exists()


def test_corrupt_state_starts_empty(root):
    d = root / "data" / "demo"
    d.mkdir(parents=True)
    (d / "state.json").write_text("{nope")
    assert dict(State("demo").items()) == {}


def test_state_needs_a_plugin_outside_hooks(root):
    with pytest.raises(ValueError):
        gitmatrix.state()


def test_settings_defaults_coercion_and_secrets(root):
    s = Settings("demo", SCHEMA)
    assert s.count == 3 and s["on"] is False and s.get("token") is None
    s.update({"count": "42", "ratio": "0.25", "on": "yes", "mode": "b", "token": "hunter2", "bogus": 1})
    assert s.count == 10 and s.ratio == 0.25 and s.on is True and s.mode == "b"
    assert s.token == "hunter2"
    stored = json.loads((root / "data" / "demo" / "settings.json").read_text())
    assert "token" not in stored and "bogus" not in stored
    assert json.loads((root / "secrets" / "demo.json").read_text()) == {"token": "hunter2"}
    assert s.redacted()["token"] is True
    s.update({"token": ""})
    assert s.get("token") is None


def test_settings_bad_choice_falls_back(root):
    s = Settings("demo", SCHEMA)
    s.update({"mode": "zzz", "count": "abc"})
    assert s.mode == "a" and s.count == 3


def test_settings_from_manifest(root):
    s = gitmatrix.settings("clock")
    assert s.clock_24h is True and s.utc_offset == 0
