"""Проверка встроенного просмотра локального видео без обращений к видеосайтам."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ui.site_player_check import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(ROOT / "tests" / "fixtures" / "site_player"))
