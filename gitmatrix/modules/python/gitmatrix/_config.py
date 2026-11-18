# /config.py handling: load, repair, migrate and save, in the Art Matrix style
# (a Python file of simple assignments so it is easy to edit by hand).

import os

from gitmatrix import _compat, _runtime

CONFIG_PATH = "/config.py"
DEFAULTS_PATH = "/defaults/config.py"
FIRMWARE_PATH = "/gitmatrix_config.py"
FIRMWARE_DEFAULTS_PATH = "/defaults/gitmatrix_config.py"


def _exec_file(path):
    ns = {}
    with open(path) as f:
        src = f.read()
    exec(src, ns)
    return {k: v for k, v in ns.items() if not k.startswith("_") and isinstance(v, _SIMPLE)}


_SIMPLE = (bool, int, float, str, list, dict, tuple, type(None))


def _copy(src, dst):
    with open(src) as f:
        data = f.read()
    _compat.write_atomic(dst, data)


class Config:
    def __init__(self, path=None, defaults_path=None):
        self.path = path or _runtime.path(CONFIG_PATH)
        self.defaults_path = defaults_path or _runtime.path(DEFAULTS_PATH)
        self.values = {}
        self.defaults = {}
        self.dirty = False
        self.repaired = None

    def load(self):
        try:
            self.defaults = _exec_file(self.defaults_path)
        except (OSError, SyntaxError) as e:
            print(f"config: no defaults ({_compat.error_text(e)})")
            self.defaults = {}

        if not _compat.exists(self.path):
            self._reset()
        else:
            try:
                self.values = _exec_file(self.path)
            except Exception as e:  # noqa: BLE001 - any broken config must not stop boot
                self.repaired = _compat.error_text(e)
                print(f"config: {self.path} is broken ({self.repaired}), restoring defaults")
                try:
                    os.rename(self.path, self.path + ".bad")
                except OSError:
                    pass
                self._reset()
        self.migrate()
        return self

    def _reset(self):
        if _compat.exists(self.defaults_path):
            _copy(self.defaults_path, self.path)
        self.values = dict(self.defaults)
        for k, v in self.values.items():
            self.values[k] = _clone(v)

    def migrate(self):
        """Add keys that newer defaults introduced, keeping the user's values."""
        added = []
        for key, value in self.defaults.items():
            if key not in self.values:
                self.values[key] = _clone(value)
                added.append(key)
        if added:
            self.dirty = True
        return added

    def __getattr__(self, name):
        values = self.__dict__.get("values")
        if values is not None and name in values:
            return values[name]
        raise AttributeError(name)

    def get(self, name, default=None):
        return self.values.get(name, default)

    def set(self, name, value):
        if self.values.get(name) != value:
            self.values[name] = value
            self.dirty = True

    def touch(self):
        """Mark dirty after mutating a list/dict value in place."""
        self.dirty = True

    def save(self, force=False):
        """Rewrite each `key = value` line in place, keeping comments, then append new keys."""
        if not (self.dirty or force):
            return False
        lines = []
        if _compat.exists(self.path):
            with open(self.path) as f:
                lines = f.read().split("\n")
        written = set()
        out = []
        depth = 0  # skipping the continuation lines of a multi-line value
        for line in lines:
            if depth > 0:
                depth += _bracket_depth(line)
                continue
            key = _assigned_key(line)
            if key is None:
                out.append(line)
                continue
            depth = max(0, _bracket_depth(line))
            if key in self.values and key not in written:
                out.append(f"{key} = {self.values[key]!r}")
                written.add(key)
        while out and out[-1] == "":
            out.pop()
        for key, value in self.values.items():
            if key not in written:
                out.append(f"{key} = {value!r}")
        _compat.write_atomic(self.path, "\n".join(out) + "\n")
        self.dirty = False
        return True


def load_firmware_config(path=None, defaults_path=None):
    """/gitmatrix_config.py: firmware settings that the web UI never shows
    (AP_SSID, AP_PASSWORD, PLUGIN_REPO, PLUGIN_BRANCH). Created from the defaults
    when missing; missing keys fall back to the defaults."""
    path = path or _runtime.path(FIRMWARE_PATH)
    defaults_path = defaults_path or _runtime.path(FIRMWARE_DEFAULTS_PATH)
    values = {}
    try:
        values = _exec_file(defaults_path)
    except (OSError, SyntaxError) as e:
        print(f"config: no firmware defaults ({_compat.error_text(e)})")
    if not _compat.exists(path):
        if _compat.exists(defaults_path):
            try:
                _copy(defaults_path, path)
            except OSError as e:
                print(f"config: could not create {path} ({_compat.error_text(e)})")
        return values
    try:
        values.update(_exec_file(path))
    except Exception as e:  # noqa: BLE001 - a typo here must not stop boot
        print(f"config: {path} is broken ({_compat.error_text(e)}), using defaults")
    return values


def _assigned_key(line):
    if not line or line[0] in " \t#":
        return None
    eq = line.find("=")
    if eq <= 0:
        return None
    key = line[:eq].strip()
    return key if _compat.valid_name(key) else None


def _bracket_depth(line):
    depth = 0
    quote = None
    for ch in line:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
    return depth


def _clone(value):
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value
