"""Guard against circular imports inside the network_vault package.

Only imports that run when a module is first loaded (top-level statements) count:
an import inside a function runs later, after every module has loaded, which is the
usual way to break a cycle (e.g. engine -> alerts, operations -> create_app).
`from . import config` imports the config module, not the package __init__.
"""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "network_vault"


def _import_time_graph():
    modules = {p.stem for p in PKG.glob("*.py")}
    graph = {}
    for path in PKG.glob("*.py"):
        deps = set()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if not (isinstance(node, ast.ImportFrom) and node.level == 1):
                continue
            if node.module:
                deps.add(node.module.split(".")[0])
            else:
                deps.update(a.name if a.name in modules else "__init__" for a in node.names)
        graph[path.stem] = deps & (modules | {"__init__"})
    return graph


def _cycles(graph):
    found = set()

    def walk(start, node, path):
        for nxt in graph.get(node, ()):
            if nxt == start:
                found.add(" -> ".join(path + [start]))
            elif nxt not in path:
                walk(start, nxt, path + [nxt])

    for module in sorted(graph):
        walk(module, module, [module])
    return found


def test_graph_sees_the_package():
    graph = _import_time_graph()
    assert {"__init__", "engine", "util", "views", "models"} <= set(graph)


def test_no_import_time_cycles():
    cycles = _cycles(_import_time_graph())
    assert not cycles, (
        "Circular import at module load time - move one import inside the function "
        "that needs it:\n  " + "\n  ".join(sorted(cycles))
    )


def test_cycle_detector_catches_a_cycle():
    assert _cycles({"a": {"b"}, "b": {"a"}}) == {"a -> b -> a", "b -> a -> b"}
