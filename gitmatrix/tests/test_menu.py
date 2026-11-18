# Menu and input: the knob press always gets you back to the menu.

from conftest import make_plugin, set_plugins

from gitmatrix import _host, _menu

GOOD = "frames = 0\ndef update():\n    global frames\n    frames += 1\n"
TURNER = "got = []\ndef on_input(e):\n    got.append(e.value)\ndef update():\n    pass\n"


def boot_two(rig, **extra):
    make_plugin(rig.root, "one", GOOD)
    make_plugin(rig.root, "two", GOOD)
    set_plugins(rig.root, ["one", "two"], **extra)
    return rig.boot()


def top(rt):
    return rt.host.menu.stack[-1]


def choose(rig, label):
    """Turn the snappy menu until `label` is selected, then press."""
    s = top(rig.rt).snappy
    for _ in range(s.n + 1):
        rig.frames(90)  # let the snap finish (~67 frames)
        if s.items[s.selected()] == label:
            break
        rig.turn(1)
    assert s.items[s.selected()] == label
    rig.press()


def test_press_opens_menu_and_back_closes(rig):
    rt = boot_two(rig)
    rig.press()
    assert rt.host.mode == _host.MENU
    assert top(rt).snappy.items == list(_menu.MAIN_ITEMS)
    assert list(_menu.MAIN_ITEMS) == ["Plugins", "Brightness", "Volume", "Alerts", "WiFi", "Bluetooth", "< Back"]
    choose(rig, "< Back")
    assert rt.host.mode == _host.PLUGIN


def test_plugins_stay_paused_in_menu(rig):
    rt = boot_two(rig)
    mod = rt.loader.plugins["one"].module
    rig.frames(3)
    n = mod.frames
    rig.press()
    rig.frames(20)
    assert mod.frames == n


def test_turn_snaps_to_next_item(rig):
    rt = boot_two(rig)
    rig.press()
    s = top(rt).snappy
    rig.frames(30)
    assert s.items[s.selected()] == "Plugins"
    # like the original snappy menu, a positive delta slides to the previous item
    rig.turn(-1)
    assert s.items[s.selected()] == "Brightness"  # committed before the slide ends
    rig.frames(90)
    assert s.items[s.current] == "Brightness"
    rig.turn(1)
    rig.frames(90)
    assert s.items[s.current] == "Plugins"


def test_menu_times_out(rig):
    rt = boot_two(rig)
    rig.press()
    rig.frames(int(_menu.IDLE_MS / 16) + 5)
    assert rt.host.mode == _host.PLUGIN


def test_plugins_list_switches_plugin(rig):
    rt = boot_two(rig)
    assert rt.host.current.name == "one"
    rig.press()
    choose(rig, "Plugins")
    lst = top(rt)
    assert isinstance(lst, _menu.ListScreen)
    assert lst.labels[-1] == "< Back" and lst.idx == 0
    rig.turn(1)
    rig.press()
    assert rt.host.mode == _host.PLUGIN
    assert rt.host.current.name == "two"


def test_plugins_list_marks_quarantined(rig):
    rt = boot_two(rig, quarantined={"two": "bad"})
    rig.press()
    choose(rig, "Plugins")
    assert any(label.startswith("! ") for label in top(rt).labels)


def test_brightness_slider_saves_to_config(rig):
    rt = boot_two(rig)
    rig.press()
    choose(rig, "Brightness")
    assert isinstance(top(rt), _menu.SliderScreen)
    rt.config.save(force=True)
    rig.turn(1)
    rig.turn(1)
    assert abs(rt.compositor.driver.brightness - 0.70) < 0.01
    assert not rt.config.dirty  # previewed only
    rig.press()
    assert rt.config.brightness == 70 and rt.config.dirty


def test_volume_slider_sets_bt_volume(rig, bt):
    rt = boot_two(rig)
    rig.press()
    choose(rig, "Volume")
    rig.turn(-1)
    rig.press()
    assert rt.config.volume == bt.vol < 64


def test_alerts_toggle(rig):
    rt = boot_two(rig)
    rig.press()
    choose(rig, "Alerts")
    choose(rig, "Sound on")
    assert rt.config.alert_sound is False
    assert "Sound off" in top(rt).snappy.items


def test_bluetooth_forget(rig, bt):
    (rig.root / "bt_link_keys.bin").write_bytes(b"k")
    rt = boot_two(rig)
    rig.press()
    choose(rig, "Bluetooth")
    choose(rig, "Forget all")
    assert not (rig.root / "bt_link_keys.bin").exists()
    assert bt.loaded_keys == b""
    assert isinstance(top(rt), _menu.MessageScreen)


def test_wifi_screen_shows_setup_ap_then_address(rig):
    import stubs

    rig.net = stubs.FakeNetwork()
    rt = boot_two(rig)
    rig.press()
    choose(rig, "WiFi")
    screen = top(rt)
    assert isinstance(screen, _menu.MessageScreen)
    text = screen.text()
    assert "'GitMatrix'" in text and "'monalisa'" in text and "http://192.168.4.1" in text
    rig.frames(30)  # long text scrolls rather than failing to fit
    rig.press()
    assert isinstance(top(rt), _menu.SnappyScreen)
    # once joined, the same screen shows where to point a browser (live)
    rt.wifi.config.set("wifi_ssid", "home")
    rt.wifi.start(rig.clock.now)
    rig.net.sta.up = True
    rt.wifi.tick(rig.clock.advance(500))
    assert screen.text() == "home - visit http://192.168.1.42 or http://gitmatrix.local"


def test_wifi_screen_without_radio(rig):
    rt = boot_two(rig)
    rig.press()
    choose(rig, "WiFi")
    assert top(rt).text() == "WiFi unavailable"


def test_bluetooth_pairable_toggle(rig, bt):
    rt = boot_two(rig)
    assert bt.pairable is True
    rig.press()
    choose(rig, "Bluetooth")
    choose(rig, "Pairable on")
    assert bt.pairable is False and rt.config.get("bt_pairable") is False
    assert top(rt).snappy.items[2] == "Pairable off"
    choose(rig, "Pairable off")
    assert bt.pairable is True


def test_turn_steps_plugins_with_slide(rig):
    rt = boot_two(rig)
    rig.turn(1)
    assert rt.host.current.name == "two" and rt.host.prev.name == "one"
    rig.frames(60)
    assert rt.host.prev is None
    rig.turn(-1)
    assert rt.host.current.name == "one"


def test_auto_rotation(rig):
    rt = boot_two(rig, delay=1)
    rig.frames(70)
    assert rt.host.current.name == "two"


def test_input_plugin_gets_turns_not_rotation(rig):
    make_plugin(rig.root, "knob", TURNER, {"name": "Knob", "input": True})
    make_plugin(rig.root, "other", GOOD)
    set_plugins(rig.root, ["knob", "other"])
    rt = rig.boot()
    rig.turn(2)
    rig.turn(-1)
    assert rt.host.current.name == "knob"
    assert rt.loader.plugins["knob"].module.got == [2, -1]
    rig.press()  # the button still opens the menu
    assert rt.host.mode == _host.MENU


def test_plugin_input_api_sees_only_its_own_turns(rig):
    code = "import gitmatrix\nseen = []\ndef update():\n    seen.append(gitmatrix.input.turns())\n"
    make_plugin(rig.root, "poller", code, {"input": True})
    set_plugins(rig.root, ["poller"])
    rt = rig.boot()
    rig.frames(2)
    rig.input.push("turn", 3)
    rig.frames(1)
    rig.frames(1)
    assert rt.loader.plugins["poller"].module.seen[-2:] == [3, 0]


def test_audio_hold_pins_spectrum_while_playing(rig, bt):
    set_plugins(rig.root, ["spectrum", "clock"], delay=1)
    rt = rig.boot()
    bt.streaming_now = True
    for _ in range(80):
        bt.push([3000] * 240)
        rig.frames(1)
    assert rt.host.current.name == "spectrum"
    bt.streaming_now = False
    for _ in range(20):
        bt.push([0] * 240)
        rig.frames(1)
    assert rt.host.current.name == "clock"


def test_input_debounce_and_long_press(clock):
    from gitmatrix._input import Input

    class Btn:
        level = 1

        def value(self):
            return self.level

    b = Btn()
    inp = Input(None, b)
    b.level = 0
    clock.advance(50)
    inp.poll()
    b.level = 1
    clock.advance(5)
    inp.poll()   # bounce ignored
    clock.advance(1100)
    b.level = 0
    inp.poll()
    kinds = [e.kind for e in inp.since(0)]
    assert kinds == ["press", "long_press"]
    b.level = 1
    clock.advance(50)
    inp.poll()
    assert inp.since(2)[-1].kind == "release"


def test_input_ring_drops_oldest(clock):
    from gitmatrix._input import RING, Input

    inp = Input()
    for i in range(RING + 5):
        inp.push("turn", i)
    events = inp.since(0)
    assert len(events) == RING and events[0].value == 5
