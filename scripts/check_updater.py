"""Проверка замены работающего Windows EXE и автоматического перезапуска без GitHub."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402
from app.services.updater import process_running, sha256_of  # noqa: E402


def main(bundle: Path):
    with tempfile.TemporaryDirectory(prefix="copy-update-", dir=os.environ.get("RUNNER_TEMP")) as temporary:
        app = Path(temporary) / "Программа 100% ! & (копия)"
        process = None
        try:
            app = Path(temporary) / "Программа 100% ! & (копия)"
            app.mkdir()
            # Для режимов без интерфейса достаточно EXE и _internal; внешние браузер/FFmpeg не используются.
            shutil.copytree(bundle / "_internal", app / "_internal")
            shutil.copy2(bundle / "CopyVideoFinder.exe", app / "CopyVideoFinder.exe")
            data = app / "Данные"
            new = data / "update" / "new" / "CopyVideoFinder"
            new.mkdir(parents=True)
            shutil.copytree(app / "_internal", new / "_internal")
            shutil.copy2(bundle / "CopyVideoFinder.exe", new / "CopyVideoFinder.exe")
            expected = sha256_of(new / "CopyVideoFinder.exe")
            # Старый EXE остаётся исполняемым, но его байты должны быть заменены.
            with (app / "CopyVideoFinder.exe").open("ab") as stream:
                stream.write(b"old-update-check")
            (app / "_internal" / "update-marker.txt").write_text("old")
            (new / "_internal" / "update-marker.txt").write_text("new")
            (data / "settings.json").write_text('{"keep": true}', encoding="utf-8")
            (data / "Видео").mkdir()
            (data / "Видео" / "keep.mp4").write_bytes(b"keep-video")
            config = data / "check-config.json"
            config.write_text(json.dumps({"new": str(new)}), encoding="utf-8")
            environment = {**os.environ, "COPYVF_DATA_DIR": str(data)}
            process = subprocess.Popen([str(app / "CopyVideoFinder.exe"), "--check-updater", str(config)],
                                       env=environment)
            assert process.wait(timeout=45) == 0, "Старый EXE не подготовил обновление"
            report = data / "update-check.json"
            deadline = time.monotonic() + 90
            while not report.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
            assert report.is_file(), (data / "update.log").read_text(encoding="utf-8-sig", errors="replace")
            result = json.loads(report.read_text())
            assert result["version"] == __version__ and result["sha256"] == expected
            assert sha256_of(app / "CopyVideoFinder.exe") == expected
            assert (app / "_internal" / "update-marker.txt").read_text() == "new"
            assert json.loads((data / "settings.json").read_text())["keep"]
            assert (data / "Видео" / "keep.mp4").read_bytes() == b"keep-video"
            state = json.loads((data / "update-result.json").read_text(encoding="utf-8-sig"))
            assert state["state"] == "installed"
            # Дождаться закрытия всех дескрипторов перед удалением тестовой папки.
            deadline = time.monotonic() + 15
            while any(process_running(pid) for pid in (state["installer_pid"], result["pid"])):
                assert time.monotonic() < deadline, "Проверочные процессы не завершились"
                time.sleep(0.2)
        except Exception as exc:
            for name in ("update.log", "app.log", "update/installer-start.log", "update-result.json"):
                log = app / "Данные" / name
                if log.is_file():
                    detail = log.read_text(encoding="utf-8-sig", errors="replace")[-6000:]
                    detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                    print(f"::error::{name}: {detail}", flush=True)
            print(f"::error::{type(exc).__name__}: {str(exc)[:1000]}", flush=True)
            if process is not None and process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
            raise
    print("Обновление реального EXE, перезапуск, кириллица/символы в пути и сохранность Данных проверены.")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
