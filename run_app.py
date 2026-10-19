"""Точка входа для PyInstaller: запускает программу как пакет app."""

import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
