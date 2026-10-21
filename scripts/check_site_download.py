"""Проверка загрузки по странице и приватной сессии на локальных фикстурах."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.site_download_check import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(ROOT / "tests" / "fixtures"))
