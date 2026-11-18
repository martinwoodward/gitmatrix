# My Plugin: a dot that bounces along the display, leaving a short trail.
#
# A starting point for GitMatrix plugins. Copy this folder, rename it (the folder
# name must be a Python identifier), set NAME to match and edit package.json.
# See docs/gitmatrix/plugin-api.md for the full API.

import gitmatrix

NAME = "my_plugin"

BLACK = color.rgb(0, 0, 0)

x = 0.0
direction = 1
pen = None
trail = None


def _rgb(value):
    """'#rrggbb' (a `color` setting) -> (r, g, b)."""
    try:
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    except (TypeError, ValueError, IndexError):
        return 255, 255, 255


def _load_settings():
    global pen, trail
    r, g, b = _rgb(gitmatrix.settings(NAME).get("colour"))
    pen = color.rgb(r, g, b)
    trail = color.rgb(r // 4, g // 4, b // 4)


def on_show():
    # Called each time the plugin comes on screen (rotation, leaving the menu).
    if pen is None:
        _load_settings()


def on_settings():
    # The settings changed in the web UI. Without this hook the plugin is reloaded.
    _load_settings()


def on_input(event):
    # Only called when package.json has "input": true. Turning the knob then
    # changes the speed instead of stepping plugins; a press still opens the menu.
    st = gitmatrix.state(NAME)
    st["boost"] = clamp(st.get("boost", 0) + event.value, -19, 100)
    st.save()  # throttled, and deferred while Bluetooth audio streams


def update():
    # Called every frame (~60 fps) while on screen. Draw into `screen` (53x7).
    global x, direction
    if pen is None:
        _load_settings()

    st = gitmatrix.state(NAME)
    speed = max(1, gitmatrix.settings(NAME).get("speed", 20) + st.get("boost", 0))
    x += direction * speed / 60
    if x <= 0 or x >= screen.width - 1:
        direction = -direction
        x = clamp(x, 0, screen.width - 1)
        st["bounces"] = st.get("bounces", 0) + 1
        st.save()

    screen.pen = BLACK
    screen.clear()
    screen.pen = trail
    tail = int(x) - direction * 6
    screen.rectangle(min(tail, int(x)), 3, 7, 1)
    screen.pen = pen
    screen.rectangle(int(x), 2, 1, 3)
