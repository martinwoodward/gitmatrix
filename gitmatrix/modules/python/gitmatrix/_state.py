# gitmatrix.state() and gitmatrix.settings(): per-plugin persistence.
#
# state    -> /data/<name>/state.json, dict-like, debounced atomic saves.
# settings -> package.json "settings" schema, values in /data/<name>/settings.json,
#             secrets in /secrets/<name>.json (never mixed with normal values).

import json

from gitmatrix import _compat, _flash, _runtime

SAVE_INTERVAL_MS = 30000     # minimum gap between flash writes per plugin

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

    Assignments mark it dirty; the host flushes it at most every 30 s, and never
    while Bluetooth audio is streaming (a flash write then can hang the board):
    it waits until playback stops. save(now=True) skips the 30 s gap, not that.
    Mutating nested values in place needs an explicit save().
    """

    def __init__(self, name):
        self.name = name
        self.path = _runtime.data_dir(name) + "/state.json"
        self._data = _read_json(self.path, {})
        self._dirty = False
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
        self._dirty = True
        if now:
            self.flush(force=True)

    def flush(self, force=False, streaming=False):
        if not self._dirty:
            return False
        now = _compat.ticks_ms()
        if streaming or not _flash.can_write():
            return False
        if not force and _compat.ticks_diff(now, self._last_write) < SAVE_INTERVAL_MS:
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
        _compat.remove_tree(_runtime.secrets_path(name))


# ---- settings ---------------------------------------------------------------
#
# Field types: text/string, url, secret, number/float, int, color, select/choice
# and bool, each with "key", "label" and "default"; number/int take "min"/"max",
# select takes "options" (values, [value, label] pairs or {"value", "label"}).

_TRUE = ("1", "true", "yes", "on")
TEXT_MAX = 512
TYPES = ("text", "string", "url", "secret", "number", "float", "int", "color", "select", "choice", "bool")


def options(field):
    """The values a select field allows, as [(value, label)]."""
    out = []
    for o in field.get("options", field.get("choices", [])):
        if isinstance(o, dict):
            out.append((o.get("value"), str(o.get("label", o.get("value")))))
        elif isinstance(o, (list, tuple)) and o:
            out.append((o[0], str(o[1]) if len(o) > 1 else str(o[0])))
        else:
            out.append((o, str(o)))
    return out


def _color(value):
    text = str(value).strip().lower()
    if not text.startswith("#"):
        text = "#" + text
    if len(text) != 7:
        raise ValueError("colour must be #rrggbb")
    int(text[1:], 16)
    return text


def check(field, value):
    """Validate and normalise one value; raises ValueError saying what's wrong."""
    kind = field.get("type", "string")
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in _TRUE
        return bool(value)
    if kind in ("int", "float", "number"):
        if isinstance(value, bool):
            raise ValueError("must be a number")
        number = float(value)
        if number != number:  # NaN
            raise ValueError("must be a number")
        if kind == "int" or (number == int(number) and isinstance(field.get("default"), int)):
            number = int(number)
        if "min" in field and number < field["min"]:
            number = field["min"]
        if "max" in field and number > field["max"]:
            number = field["max"]
        return number
    if kind in ("select", "choice"):
        for v, _label in options(field):
            if v == value or str(v) == str(value):
                return v
        raise ValueError("not one of the options")
    if kind == "color":
        return _color(value)
    text = str(value)
    if len(text) > TEXT_MAX:
        raise ValueError(f"longer than {TEXT_MAX} characters")
    if kind == "url" and text and not (text.startswith("http://") or text.startswith("https://")):  # noqa: PIE810 - MicroPython takes no tuple
        raise ValueError("must start with http:// or https://")
    return text


def coerce(field, value):
    """check(), falling back to the field's default for a bad value."""
    if value is None:
        return None
    try:
        return check(field, value)
    except (TypeError, ValueError):
        return field.get("default")


def public_schema(schema):
    """The schema as the web UI needs it: typed, labelled, options listed."""
    out = []
    for f in schema:
        if not isinstance(f, dict) or not isinstance(f.get("key"), str):
            continue
        kind = f.get("type", "string")
        item = {
            "key": f["key"],
            "type": kind if kind in TYPES else "text",
            "label": str(f.get("label", f["key"])),
        }
        if kind != "secret":
            item["default"] = f.get("default")
        for extra in ("min", "max", "step", "help", "placeholder"):
            if extra in f:
                item[extra] = f[extra]
        if kind in ("select", "choice"):
            item["options"] = [{"value": v, "label": label} for v, label in options(f)]
        out.append(item)
    return out


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

    def update(self, values, strict=False):
        """Validate and persist new values ("" or None clears a secret). Unknown keys
        are ignored, or with strict (the web API) rejected; strict raises ValueError
        naming the first bad field, saving nothing."""
        if strict:
            unknown = sorted(k for k in values if k not in self.fields)
            if unknown:
                raise ValueError("unknown setting(s): " + ", ".join(unknown))
        stored = _read_json(self.values_path, {})
        secrets = _read_json(self.secrets_path, {})
        secrets_changed = False
        for key, value in values.items():
            field = self.fields.get(key)
            if field is None:
                continue
            if field.get("type") == "secret":
                if value:
                    if strict and len(str(value)) > TEXT_MAX:
                        raise ValueError(f"{key}: longer than {TEXT_MAX} characters")
                    secrets[key] = str(value)
                else:
                    secrets.pop(key, None)
                secrets_changed = True
            elif strict:
                try:
                    stored[key] = None if value is None else check(field, value)
                except (TypeError, ValueError) as e:
                    raise ValueError(f"{field.get('label', key)}: {e}") from None
                if stored[key] is None:
                    stored.pop(key)
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
