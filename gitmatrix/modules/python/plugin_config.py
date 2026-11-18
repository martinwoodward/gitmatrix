# Art Matrix compatible plugin_config(): values saved from a plugin's config.html
# form, typed by the defaults. A bool is True when its key was submitted (an
# unticked checkbox sends nothing); a tuple default reads "#rrggbb" as (r, g, b).

import json


def _root():
    try:
        from gitmatrix import _runtime
        return _runtime.ROOT
    except ImportError:
        return ""


def _coerce(default, value):
    if value is None or value == "":
        return default
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, tuple):
        text = str(value).lstrip("#")
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    return value


def plugin_config(name, defaults):
    try:
        with open(f"{_root()}/lib/plugins/{name}/config.json") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return dict(defaults)
    if not isinstance(saved, dict):
        return dict(defaults)

    out = {}
    for key, default in defaults.items():
        if isinstance(default, bool):
            out[key] = key in saved
            continue
        try:
            out[key] = _coerce(default, saved.get(key))
        except (TypeError, ValueError):
            out[key] = default
    return out
