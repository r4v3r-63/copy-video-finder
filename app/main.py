"""Точка входа: журнал, защита от второго запуска, главное окно."""

import logging
import sys

from PyQt6.QtCore import QLibraryInfo, QLocale, QLockFile, QTranslator
from PyQt6.QtGui import QIcon
from PyQt6.QtNetwork import QNetworkProxyFactory
from PyQt6.QtWidgets import QApplication, QMessageBox

from app import paths
from app.core import processes
from app.storage.settings import Settings
from app.ui import theme


def setup_logging() -> None:
    logging.basicConfig(
        filename=paths.log_file(),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )

    def log_unhandled(exc_type, exc, tb):
        logging.getLogger("app").critical("Необработанная ошибка", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = log_unhandled


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Поиск видео Copy.ru")
    app.setWindowIcon(QIcon(str(paths.resources_dir() / "logo.svg")))
    settings = Settings.load()
    theme.load_fonts()
    theme.apply(app, settings.theme)
    QNetworkProxyFactory.setUseSystemConfiguration(True)
    # Стандартные кнопки и окна Qt («Подробнее…», выбор папки) — на русском.
    translator = QTranslator(app)
    if translator.load(QLocale(QLocale.Language.Russian), "qtbase", "_",
                       QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)

    lock = QLockFile(str(paths.lock_file()))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "Поиск видео Copy.ru", "Программа уже запущена.")
        return 0

    try:
        catalog = processes.load_catalog()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logging.getLogger("app").exception("Не удалось загрузить справочник услуг")
        QMessageBox.critical(None, "Поиск видео Copy.ru", f"Не удалось загрузить справочник услуг:\n{exc}")
        return 1

    from app.ui.main_window import MainWindow

    window = MainWindow(settings, catalog)
    window.show()
    logging.getLogger("app").info("Программа запущена")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
