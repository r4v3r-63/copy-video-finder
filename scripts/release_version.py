"""Версия для автоматического выпуска после успешной проверки пуша в main."""

import ast
import os
import re
import subprocess
from pathlib import Path


def version_number(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError(f"Неверная версия выпуска: {value!r}")
    major, minor, patch = map(int, value.split("."))
    return major, minor, patch


def code_version(source: str) -> str:
    # Файл из git читается как данные: импортировать код для выбора версии не нужно.
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                version_number(value)
                return value
    raise ValueError("В app/__init__.py не найдена версия выпуска")


def release_version(before: str, after: str, repository: Path = Path(".")) -> str:
    """Сравнить весь пуш, включая несколько коммитов; опубликованные версии не повторять."""
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (before, after)):
        raise ValueError("Для выбора версии нужны SHA коммитов до и после пуша")
    if before == "0" * 40:
        return ""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repository, check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()

    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", before, after], cwd=repository, capture_output=True,
    )
    if ancestry.returncode == 1:
        # Перезапись истории не должна случайно публиковать прежнюю версию.
        return ""
    ancestry.check_returncode()
    old = code_version(git("show", f"{before}:app/__init__.py"))
    new = code_version(git("show", f"{after}:app/__init__.py"))
    if version_number(new) <= version_number(old):
        return ""
    for tag in git("tag", "--list", "v*").splitlines():
        try:
            published = version_number(tag[1:])
        except ValueError:
            continue
        if published >= version_number(new):
            return ""
    return new


def main() -> None:
    version = release_version(os.environ["BEFORE_SHA"], os.environ["BUILD_SHA"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"version={version}\n")
    print(f"Будет выпущена версия {version}" if version else "Новой версии для выпуска нет")


if __name__ == "__main__":
    main()
