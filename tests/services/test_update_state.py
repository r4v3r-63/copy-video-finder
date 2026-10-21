"""Не закрывать программу без установщика и не уничтожать незавершённое обновление."""

import json

from app import paths
from app.services import updater


def state(value):
    paths.atomic_write_text(updater.result_file(), json.dumps(value))


def staged():
    folder = updater.update_dir()
    folder.mkdir(parents=True)
    file = folder / "new-exe"
    file.write_bytes(b"new")
    return file


def test_cleanup_preserves_pending_and_failed_installations(monkeypatch):
    file = staged()
    updater.cleanup()
    assert file.exists()
    state({"state": "failed", "installer_pid": 99})
    updater.cleanup()
    assert file.exists()
    monkeypatch.setattr(updater, "process_running", lambda pid: True)
    state({"state": "copying", "installer_pid": 99})
    assert updater.installation_in_progress()
    updater.cleanup()
    assert file.exists()


def test_cleanup_after_success_preserves_user_data(monkeypatch):
    file = staged()
    user = paths.data_dir() / "settings.json"
    user.write_text("keep")
    monkeypatch.setattr(updater, "process_running", lambda pid: False)
    state({"state": "installed", "installer_pid": 99})
    updater.cleanup()
    assert not file.exists() and user.read_text() == "keep"


def test_failure_reported_once_and_staging_kept():
    file = staged()
    state({"state": "failed", "message": "Нет прав на запись."})
    assert "Нет прав на запись" in updater.previous_failure()
    assert updater.previous_failure() == "" and file.exists()


def test_abandoned_installation_is_reported(monkeypatch):
    monkeypatch.setattr(updater, "process_running", lambda pid: False)
    state({"state": "copying", "installer_pid": 99})
    assert "прервана" in updater.previous_failure()


def test_failed_launch_does_not_signal_readiness(tmp_path, monkeypatch):
    import pytest

    script = tmp_path / "installer.ps1"
    script.write_text("exit 1", encoding="utf-8")

    class Failed:
        def poll(self):
            return 1

    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **kw: Failed())
    with pytest.raises(updater.UpdateError, match="остаётся открытой"):
        updater.launch(script)


def test_encoded_launch_requires_ready_marker(tmp_path, monkeypatch):
    import base64

    script = tmp_path / "Путь 100% ! &.ps1"
    script.write_text("# Проверка", encoding="utf-8-sig")
    calls = []

    class Running:
        pid = 12345

        def poll(self):
            return None

    def start(command, **kwargs):
        calls.append(command)
        script.with_suffix(".ready").write_text("12345", encoding="utf-8-sig")
        return Running()

    monkeypatch.setattr(updater.subprocess, "Popen", start)
    updater.launch(script)
    assert "-EncodedCommand" in calls[0] and "cmd.exe" not in calls[0]
    assert base64.b64decode(calls[0][-1]).decode("utf-16-le") == "# Проверка"
