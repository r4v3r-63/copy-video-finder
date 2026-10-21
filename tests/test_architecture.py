"""Границы слоёв: модуль импортирует только свой и нижние слои (docs/ARCHITECTURE.md)."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
# Снизу вверх. app/__init__.py и app/paths.py — основание, их можно импортировать отовсюду.
LAYERS = ["core", "storage", "sources", "services", "export", "workers", "ui", "main"]
# Qt — только в потоках и интерфейсе; отчёт PDF собирается средствами Qt (QTextDocument → QPdfWriter).
QT_ALLOWED = {"workers", "ui", "main"}
QT_EXCEPTIONS = {"export/report.py"}


def layer_of(module: str) -> int:
    parts = module.split(".")
    if parts[0] != "app" or len(parts) == 1 or parts[1] in ("paths", "__version__"):
        return -1
    return LAYERS.index(parts[1])


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "app":  # from app import paths, __version__
                found += [f"app.{alias.name}" for alias in node.names]
            else:
                found.append(node.module)
    return found


def modules():
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP)
        if len(rel.parts) == 1 and rel.stem != "main":
            continue  # основание: __init__.py, paths.py
        yield rel, LAYERS.index(rel.parts[0] if len(rel.parts) > 1 else rel.stem), path


def test_every_module_belongs_to_a_layer():
    loose = [p.name for p in APP.glob("*.py") if p.stem not in ("__init__", "paths", "main")]
    assert loose == [], "новый модуль положите в папку своего слоя"


def test_layers_import_only_lower_layers():
    wrong = []
    for rel, own, path in modules():
        for module in imports(path):
            if module.startswith("app.") and layer_of(module) > own:
                # Список площадок собирается из настроек — единственная связь sources → storage.
                if rel.as_posix() == "sources/registry.py" and module == "app.storage.settings":
                    continue
                wrong.append(f"{rel.as_posix()} → {module}")
    assert wrong == []


def test_storage_does_not_depend_on_sources():
    wrong = [f"{rel.as_posix()} → {m}" for rel, _, path in modules() if rel.parts[0] == "storage"
             for m in imports(path) if m.startswith("app.sources")]
    assert wrong == []


def test_qt_only_in_workers_and_ui():
    wrong = [rel.as_posix() for rel, own, path in modules()
             if LAYERS[own] not in QT_ALLOWED and rel.as_posix() not in QT_EXCEPTIONS
             and any(m.split(".")[0] == "PyQt6" for m in imports(path))]
    assert wrong == []
