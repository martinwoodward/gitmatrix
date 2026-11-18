from gitmatrix import _boot
from gitmatrix._input import Input


def test_boot_and_run_frames(root, clock):
    rt = _boot.boot(Input())
    seen = set()
    for _ in range(6 * 31 * 60 + 200):
        rt.host.frame(clock.advance(16))
        seen.add(rt.host.current.name)
    errors = {p.name: p.last_error for p in rt.loader.plugins.values() if p.error_count}
    assert errors == {}
    assert seen == {"graph", "clock", "spectrum", "lava", "fire", "rainbow"}
    assert not rt.config.quarantined


def test_fire_and_spectrum_draw_pixels(rig, bt):
    from conftest import set_plugins

    set_plugins(rig.root, ["fire", "spectrum"], delay=0)
    rt = rig.boot()
    rig.frames(30)
    surf = rt.loader.plugins["fire"].surface
    assert any(w & 0xFFFFFF for w in memoryview(surf).cast("I"))
    rig.turn(1)
    bt.streaming_now = True
    for _ in range(60):
        bt.push([5000] * 40 + [800] * 200)
        rig.frames(1)
    surf = rt.loader.plugins["spectrum"].surface
    assert any(w & 0xFFFFFF for w in memoryview(surf).cast("I"))
    assert rt.loader.plugins["spectrum"].failures == 0


def test_scroll_moves(root):
    import builtins

    from scrolling import Scroll

    s = Scroll("hello world, this is long")
    builtins.screen.texts.clear()
    s.update(0)
    s.render()
    first = [x for _, x, _ in builtins.screen.texts]
    builtins.screen.texts.clear()
    s.update(0.25)
    s.render()
    later = [x for _, x, _ in builtins.screen.texts]
    assert first and later and first != later
    assert s.target is builtins.screen
