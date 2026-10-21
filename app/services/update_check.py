"""Режимы сквозной проверки реального автообновления в собранном EXE, без сети."""

import json
import os
from pathlib import Path

from app import __version__, paths
from app.services import updater


def install(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    script = updater.write_script(Path(config["new"]), paths.app_dir(), os.getpid(),
                                  paths.data_dir() / "update.log", __version__,
                                  restart_args=("--check-updater-target",), show_errors=False)
    updater.launch(script)
    return 0  # Процесс должен действительно завершиться и освободить свой EXE.


def target() -> int:
    paths.atomic_write_text(paths.data_dir() / "update-check.json", json.dumps({
        "version": __version__, "sha256": updater.sha256_of(paths.app_dir() / updater.EXE_NAME), "pid": os.getpid()}))
    return 0
