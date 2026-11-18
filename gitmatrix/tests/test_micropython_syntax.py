# Code CPython accepts but MicroPython 1.29 rejects (at import time, or at runtime).
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = sorted(p for d in ("modules/python", "firmware") for p in (ROOT / d).rglob("*.py"))


def unsupported(tree):
    for node in ast.walk(tree):
        # [*a, b], (*a, b), {*a}: "*x must be assignment target" / "invalid syntax"
        # (a, *b = x is fine: that's an assignment target, ctx Store)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not isinstance(getattr(node, "ctx", None), ast.Store):
            if any(isinstance(e, ast.Starred) for e in node.elts):
                yield node.lineno, "starred item in a display"
        # {**a, ...}
        if isinstance(node, ast.Dict) and None in node.keys:
            yield node.lineno, "** in a dict display"
        # contribution_widget's globals are in the builtins override dict: reading
        # builtins.screen raises AttributeError on the device (writing is fine)
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name) and node.value.id == "builtins"):
            yield node.lineno, f"reads builtins.{node.attr}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_source_compiles_on_micropython(path):
    found = list(unsupported(ast.parse(path.read_text(), str(path))))
    assert not found, f"{path.name}: {found}"
