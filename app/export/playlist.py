"""Плейлист M3U (ТЗ, 10) из скачанных роликов — для VLC и ТВ-плееров.

Пути относительные (от папки плейлиста), чтобы папку с роликами и плейлистом
можно было скопировать на флешку или телевизор целиком.
"""

import os
from pathlib import Path


def write_m3u(path: Path, entries) -> int:
    """Записать плейлист из роликов, файлы которых есть на диске. Возвращает число роликов."""
    path = Path(path)
    lines = ["#EXTM3U"]
    count = 0
    for e in entries:
        file = Path(e.downloaded_path) if e.downloaded_path else None
        if not file or not file.exists():
            continue
        try:
            ref = os.path.relpath(file, path.parent)
        except ValueError:  # другой диск в Windows — относительный путь невозможен
            ref = str(file)
        duration = int(round(e.result.duration)) if e.result.duration else -1
        lines.append(f"#EXTINF:{duration},{e.result.title.replace(chr(10), ' ')}")
        lines.append(ref.replace("\\", "/"))
        count += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count
