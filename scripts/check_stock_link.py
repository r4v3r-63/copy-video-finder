"""Локальная проверка вкладки загрузки, без ключей и внешних видеостоков."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.diagnostics.stock_link import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(ROOT / "tests" / "fixtures"))
