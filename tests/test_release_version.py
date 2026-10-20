"""Выбор выпуска на локальной истории git, без GitHub и сетевых запросов."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_version import code_version, release_version


@pytest.fixture
def history(tmp_path):
    repository = tmp_path / "История версий"
    repository.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Тест", "-c", "user.email=test@example.org", *args],
            cwd=repository, check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()

    git("init")
    (repository / "app").mkdir()

    def commit(version, message="Обновление"):
        (repository / "app" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        git("add", ".")
        git("commit", "--allow-empty", "-m", message)
        return git("rev-parse", "HEAD")

    return repository, git, commit


@pytest.mark.parametrize(("old", "new", "expected"), [
    ("0.24.0", "0.24.0", ""),
    ("0.24.0", "0.23.9", ""),
    ("0.24.0", "0.24.1", "0.24.1"),
    ("0.9.0", "0.10.0", "0.10.0"),
    ("0.24.0", "1.0.0", "1.0.0"),
])
def test_only_version_increase_starts_release(history, old, new, expected):
    repository, _, commit = history
    before = commit(old)
    after = commit(new)
    assert release_version(before, after, repository) == expected


def test_push_with_bump_followed_by_ordinary_commit_releases_tested_tip(history):
    repository, _, commit = history
    before = commit("0.24.0")
    commit("0.25.0", "Повысить версию")
    after = commit("0.25.0", "Дополнить документацию")
    assert release_version(before, after, repository) == "0.25.0"


@pytest.mark.parametrize("tag", ["v0.25.0", "v0.26.0", "v1.0.0"])
def test_existing_or_newer_release_is_never_republished(history, tag):
    repository, git, commit = history
    before = commit("0.24.0")
    after = commit("0.25.0")
    git("tag", "-a", tag, "-m", "Выпуск")
    assert release_version(before, after, repository) == ""


def test_old_and_non_release_tags_do_not_block_bump(history):
    repository, git, commit = history
    before = commit("0.24.0")
    git("tag", "v0.24.0")
    git("tag", "v1.0.0-test")
    after = commit("0.25.0")
    assert release_version(before, after, repository) == "0.25.0"


def test_force_push_does_not_release(history):
    repository, git, commit = history
    base = commit("0.23.0")
    before = commit("0.24.0")
    git("checkout", "--detach", base)
    after = commit("0.25.0")
    assert release_version(before, after, repository) == ""


def test_new_branch_does_not_release(history):
    repository, _, commit = history
    after = commit("0.25.0")
    assert release_version("0" * 40, after, repository) == ""


@pytest.mark.parametrize("source", [
    '__version__ = "01.2.3"', '__version__ = "1.2.3-rc1"', '__version__ = "1.2"',
    '__version__ = 123', '__version__ = str("1.2.3")', 'OTHER = "1.2.3"',
])
def test_invalid_version_fails_before_release(source):
    with pytest.raises(ValueError):
        code_version(source)


def test_version_is_read_without_executing_module(tmp_path):
    marker = tmp_path / "Не создавать"
    source = f'from pathlib import Path\nPath({str(marker)!r}).touch()\n__version__ = "0.25.0"\n'
    assert code_version(source) == "0.25.0"
    assert not marker.exists()


def test_cli_writes_github_output(history, tmp_path):
    repository, _, commit = history
    before = commit("0.24.0")
    after = commit("0.25.0")
    output = tmp_path / "github-output.txt"
    script = Path(__file__).resolve().parents[1] / "scripts" / "release_version.py"
    subprocess.run(
        [sys.executable, str(script)], cwd=repository, check=True, capture_output=True,
        env={**os.environ, "BEFORE_SHA": before, "BUILD_SHA": after, "GITHUB_OUTPUT": str(output),
             "PYTHONIOENCODING": "utf-8"},
    )
    assert output.read_text(encoding="utf-8") == "version=0.25.0\n"
