"""Границы пакетов, Qt и отсутствие циклов; правила — docs/ARCHITECTURE.md."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
# Снизу вверх; main и diagnostics собирают приложение, рабочие слои их не импортируют.
LAYERS = ["core", "storage", "sources", "services", "export", "workers", "ui", "diagnostics", "main"]
FOUNDATION = {"app", "app.paths", "app.__version__"}
QT_ALLOWED = {"workers", "ui", "diagnostics", "main"}
QT_EXCEPTIONS = {"export/report.py"}
CORE_FORBIDDEN = {"requests", "httpx", "aiohttp", "socket", "http.client", "urllib.request",
                  "sqlite3", "subprocess", "PyQt6", "PySide6"}


def layer_of(module: str) -> int:
    if not module.startswith("app.") or module in FOUNDATION:
        return -1
    return LAYERS.index(module.split(".")[1])


def imports(source: str, module: str, *, package=False) -> set[str]:
    """Учитывать абсолютные/относительные импорты и from package import submodule."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = module if package else module.rpartition(".")[0]
                base = resolve_name("." * node.level + base, parent)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def modules():
    return {".".join(path.relative_to(APP.parent).with_suffix("").parts).removesuffix(".__init__"): path
            for path in sorted(APP.rglob("*.py"))}


def dependencies():
    return {module: imports(path.read_text(encoding="utf-8"), module, package=path.name == "__init__.py")
            for module, path in modules().items()}


def cycles(graph):
    found, visited, active = [], set(), []

    def visit(module):
        if module in active:
            found.append(active[active.index(module):] + [module])
            return
        if module in visited:
            return
        active.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        active.pop()
        visited.add(module)

    for module in sorted(graph):
        visit(module)
    return found


def test_every_module_belongs_to_a_layer():
    wrong = [module for module in modules()
             if module not in FOUNDATION and module.split(".")[1] not in LAYERS]
    assert wrong == [], "Модуль вне согласованной структуры: " + ", ".join(wrong)


def test_layers_import_only_lower_layers():
    wrong = [f"{module} → {dependency}" for module, items in dependencies().items() for dependency in items
             if dependency.startswith("app.") and layer_of(dependency) > layer_of(module)]
    assert wrong == [], "Импорты верхних слоёв: " + "; ".join(sorted(wrong))


def test_core_has_no_network_database_processes_or_qt():
    wrong = [f"{module} → {dependency}" for module, items in dependencies().items() if module.startswith("app.core")
             for dependency in items
             if any(dependency == prefix or dependency.startswith(prefix + ".") for prefix in CORE_FORBIDDEN)]
    assert wrong == [], "Внешние операции не относятся к предметной логике: " + "; ".join(sorted(wrong))


def test_qt_only_in_presentation_and_diagnostics():
    wrong = []
    items = dependencies()
    for module, path in modules().items():
        layer = module.split(".")[1] if "." in module else ""
        if layer in QT_ALLOWED or path.relative_to(APP).as_posix() in QT_EXCEPTIONS:
            continue
        if any(dependency.split(".")[0] in {"PyQt6", "PySide6"} for dependency in items[module]):
            wrong.append(module)
    assert wrong == [], "Qt вне интерфейса, потоков и диагностики: " + ", ".join(sorted(wrong))


def test_application_imports_have_no_cycles():
    items = dependencies()
    graph = {module: {dependency for dependency in deps if dependency in items} for module, deps in items.items()}
    wrong = cycles(graph)
    assert wrong == [], "Циклы импортов: " + "; ".join(" → ".join(cycle) for cycle in wrong)


@pytest.mark.parametrize(("source", "module", "package", "expected"), [
    ("from . import updater", "app.services.example", False, {"app.services", "app.services.updater"}),
    ("from ..ui import widgets", "app.services.example", False, {"app.ui", "app.ui.widgets"}),
    ("from .models import VideoResult", "app.core", True, {"app.core.models", "app.core.models.VideoResult"}),
    ("from app import paths", "app.main", False, {"app", "app.paths"}),
    ("import app.ui.theme", "app.main", False, {"app.ui.theme"}),
])
def test_import_analysis_cannot_be_bypassed_with_relative_or_package_imports(source, module, package, expected):
    assert imports(source, module, package=package) == expected


def test_cycle_analysis_finds_cycle_and_accepts_shared_dependency():
    assert cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}}) == [["a", "b", "c", "a"]]
    assert cycles({"a": {"c"}, "b": {"c"}, "c": set()}) == []
