"""Точка входа для PyInstaller: запускает программу как пакет app."""

import os
import sys

# У оконной сборки нет консоли; CLR и другие библиотеки ожидают файловые потоки.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from app.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
