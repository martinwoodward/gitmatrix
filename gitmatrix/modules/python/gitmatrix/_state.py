# gitmatrix.state() and gitmatrix.settings(): per-plugin persistence.
#
# state    -> /data/<name>/state.json, dict-like, debounced atomic saves.
# settings -> package.json "settings" schema, values in /data/<name>/settings.json,
#             secrets in /secrets/<name>.json (never mixed with normal values).

import json

from gitmatrix import _compat, _runtime

SAVE_INTERVAL_MS = 30000     # minimum gap between flash writes per plugin
MAX_DEFER_MS = 300000        # write anyway after this long even while streaming

_states = {}
_settings = {}


def _read_json(path, default):
    try:
        with open(path) as f:
            value = json.load(f)
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    folder = path[:path.rfind("/")]
    if folder:
        _compat.makedirs(folder)
    _compat.write_atomic(path, json.dumps(value))


class State:
    """A dict persisted to /data/<name>/state.json.

    Assignments mark it dirty; the host flushes it at most every 30 s (and not
    while audio is streaming, up to 5 minutes). Call save(now=True) to force it.
    Mutating nested values in place needs an explicit save().
    """

    def __init__(self, name):
        self.name = name
        self.path = _runtime.data_dir(name) + "/state.json"
        self._data = _read_json(self.path, {})
        self._dirty = False
        self._dirty_since = 0
        self._last_write = _compat.ticks_ms() - SAVE_INTERVAL_MS

    # dict protocol
    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        if key not in self._data or self._data[key] != value or isinstance(value, (list, dict)):
            self._data[key] = value
            self.save()

    def __delitem__(self, key):
        del self._data[key]
        self.save()

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f"State({self.name!r}, {self._data!r})"

    def get(self, key, default=None):
        return self._data.get(key, default)

    def setdefault(self, key, default=None):
        if key not in self._data:
            self[key] = default
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def update(self, *args, **kwargs):
        self._data.update(*args, **kwargs)
        self.save()

    def pop(self, key, *default):
        value = self._data.pop(key, *default)
        self.save()
        return value

    def clear(self):
        self._data.clear()
        self.save()

    @property
    def dirty(self):
        return self._dirty

    def save(self, now=False):
        if not self._dirty:
            self._dirty = True
            self._dirty_since = _compat.ticks_ms()
        if now:
            self.flush(force=True)

    def flush(self, force=False, streaming=False):
        if not self._dirty:
            return False
        now = _compat.ticks_ms()
        if not force:
            if _compat.ticks_diff(now, self._last_write) < SAVE_INTERVAL_MS:
                return False
            if streaming and _compat.ticks_diff(now, self._dirty_since) < MAX_DEFER_MS:
                return False
        try:
            _write_json(self.path, self._data)
        except OSError as e:
            print(f"state: could not save {self.name}: {_compat.error_text(e)}")
            return False
        self._dirty = False
        self._last_write = now
        return True


def state(name=None):
    name = name or _runtime.current_name()
    if not name:
        raise ValueError("state() needs a plugin name outside a plugin hook")
    s = _states.get(name)
    if s is None:
        s = _states[name] = State(name)
    return s


def flush_all(force=False, streaming=False):
    wrote = 0
    for s in _states.values():
        if s.flush(force=force, streaming=streaming):
            wrote += 1
    return wrote


def forget(name, delete=False):
    """Drop a cached state, optionally deleting it (uninstall)."""
    _settings.pop(name, None)
    s = _states.pop(name, None)
    if s is not None and not delete:
        s.flush(force=True)
    if delete:
        _compat.remove_tree(_runtime.data_dir(name))


# ---- settings ---------------------------------------------------------------

_TRUE = ("1", "true", "yes", "on")


def coerce(field, value):
    kind = field.get("type", "string")
    if value is None:
        return None
    try:
        if kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in _TRUE
            return bool(value)
        if kind == "int":
            value = int(value)
        elif kind in ("float", "number"):
            value = float(value)
        elif kind == "choice":
            choices = [c[0] if isinstance(c, (list, tuple)) else c for c in field.get("choices", [])]
            return value if value in choices else field.get("default")
        else:
            value = str(value)
    except (TypeError, ValueError):
        return field.get("default")
    if kind in ("int", "float", "number"):
        if "min" in field and value < field["min"]:
            value = field["min"]
        if "max" in field and value > field["max"]:
            value = field["max"]
    return value


def manifest(name):
    return _read_json(_runtime.plugins_dir() + "/" + name + "/package.json", {})


class Settings:
    """Typed, schema-driven plugin settings. Read like a dict; secrets included."""

    def __init__(self, name, schema=None):
        self.name = name
        self.schema = schema if schema is not None else manifest(name).get("settings", [])
        self.fields = {f["key"]: f for f in self.schema if isinstance(f, dict) and "key" in f}
        self.values_path = _runtime.data_dir(name) + "/settings.json"
        self.secrets_path = _runtime.secrets_path(name)
        self.reload()

    def reload(self):
        stored = _read_json(self.values_path, {})
        secrets = _read_json(self.secrets_path, {})
        self._values = {}
        for key, field in self.fields.items():
            source = secrets if field.get("type") == "secret" else stored
            if key in source:
                self._values[key] = coerce(field, source[key]) if field.get("type") != "secret" else source[key]
            else:
                self._values[key] = field.get("default")

    def __getitem__(self, key):
        return self._values[key]

    def __contains__(self, key):
        return key in self._values

    def __getattr__(self, key):
        values = self.__dict__.get("_values")
        if values is not None and key in values:
            return values[key]
        raise AttributeError(key)

    def get(self, key, default=None):
        value = self._values.get(key)
        return default if value is None else value

    def items(self):
        return self._values.items()

    def is_secret(self, key):
        return self.fields.get(key, {}).get("type") == "secret"

    def redacted(self):
        """For UIs/APIs: secrets show only whether they are set."""
        return {k: (bool(v) if self.is_secret(k) else v) for k, v in self._values.items()}

    def update(self, values):
        """Validate and persist new values (unknown keys ignored, "" clears a secret)."""
        stored = _read_json(self.values_path, {})
        secrets = _read_json(self.secrets_path, {})
        secrets_changed = False
        for key, value in values.items():
            field = self.fields.get(key)
            if field is None:
                continue
            if field.get("type") == "secret":
                if value:
                    secrets[key] = str(value)
                else:
                    secrets.pop(key, None)
                secrets_changed = True
            else:
                stored[key] = coerce(field, value)
        _write_json(self.values_path, stored)
        if secrets_changed:
            _write_json(self.secrets_path, secrets)
        self.reload()


def settings(name=None):
    name = name or _runtime.current_name()
    if not name:
        raise ValueError("settings() needs a plugin name outside a plugin hook")
    s = _settings.get(name)
    if s is None:
        s = _settings[name] = Settings(name)
    return s
