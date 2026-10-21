"""Меню «Экспорт каталога» и «Импорт реестра CSV»."""

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog, QWidget

from app import paths
from app.export import html_catalog, playlist, registry, report, summary
from app.services.downloader import safe_name
from app.storage import catalog as cat
from app.ui.context import AppContext
from app.ui.dialogs.showreel_dialog import ShowreelDialog
from app.workers.report import ReportWorker
from app.workers.showreel import ShowreelWorker


class ExportActions(QObject):
    catalog_changed = pyqtSignal()  # импорт изменил каталог
    message = pyqtSignal(str)

    def __init__(self, ctx: AppContext, parent: QWidget):
        super().__init__(parent)
        self.ctx = ctx
        self.window = parent
        self.report_worker: ReportWorker | None = None
        self.showreel_worker: ShowreelWorker | None = None

    def menu(self) -> list:
        return [("Реестр в Excel…", self.export_xlsx), ("Реестр в CSV…", self.export_csv),
                ("HTML-каталог…", self.export_html),
                ("Отчёт для руководства (PDF)…", self.export_report),
                ("Сводка покрытия…", self.export_summary),
                ("Плейлист M3U из скачанных…", self.export_playlist),
                ("Шоурил для экрана…", self.export_showreel)]

    def busy(self) -> bool:
        return any(worker and worker.isRunning() for worker in (self.showreel_worker, self.report_worker))

    def workers(self) -> list:
        return [self.report_worker, self.showreel_worker]

    def _save_path(self, title: str, name: str, file_filter: str) -> Path | None:
        folder = paths.data_dir() / "Экспорт"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        chosen, _ = QFileDialog.getSaveFileName(self.window, title, str(folder / f"{name} {stamp}"), file_filter)
        return Path(chosen) if chosen else None

    def _exported(self, path: Path, what: str):
        answer = QMessageBox.question(self.window, "Экспорт", f"{what}:\n{path}\n\nОткрыть?")
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _export_failed(self, exc: Exception):
        QMessageBox.warning(self.window, "Экспорт", f"Не удалось сохранить файл: {exc}")

    def export_xlsx(self):
        path = self._save_path("Реестр в Excel", "Реестр роликов", "Excel (*.xlsx)")
        if not path:
            return
        path = path.with_suffix(".xlsx")
        try:
            s = summary.build(self.ctx.catalog, self.ctx.categories)
            registry.write_xlsx(path, registry.rows(self.ctx.catalog, self.ctx.processes), summary.lines(s))
        except OSError as exc:
            return self._export_failed(exc)
        self._exported(path, "Реестр сохранён")

    def export_csv(self):
        path = self._save_path("Реестр в CSV", "Реестр роликов", "CSV (*.csv)")
        if not path:
            return
        path = path.with_suffix(".csv")
        try:
            registry.write_csv(path, registry.rows(self.ctx.catalog, self.ctx.processes))
        except OSError as exc:
            return self._export_failed(exc)
        self._exported(path, "Реестр сохранён")

    def export_html(self):
        path = self._save_path("HTML-каталог", "Каталог роликов", "HTML (*.html)")
        if not path:
            return
        path = path.with_suffix(".html")
        try:
            html_catalog.write(path, self.ctx.catalog, self.ctx.categories)
        except OSError as exc:
            return self._export_failed(exc)
        self._exported(path, "HTML-каталог сохранён")

    def export_report(self):
        """PDF для руководства: покрытие услуг и превью роликов (превью скачиваются в фоне)."""
        if self.report_worker and self.report_worker.isRunning():
            return
        path = self._save_path("Отчёт для руководства", "Отчёт о роликах", "PDF (*.pdf)")
        if not path:
            return
        path = path.with_suffix(".pdf")
        videos = report.service_videos(self.ctx.catalog, self.ctx.categories)
        entries = list({e.id: e for items in videos.values() for e in items}.values())
        self.message.emit(f"Отчёт: загружаются превью роликов ({len(entries)})…")
        self.report_worker = ReportWorker(entries, self.window)
        self.report_worker.done.connect(lambda thumbs: self._write_report(path, thumbs))
        self.report_worker.start()

    def _write_report(self, path: Path, thumbs: dict):
        try:
            report.write_pdf(path, report.report_html(self.ctx.catalog, self.ctx.categories, thumbs), thumbs)
        except OSError as exc:
            return self._export_failed(exc)
        self.message.emit("Отчёт сохранён.")
        self._exported(path, "Отчёт сохранён")

    def export_summary(self):
        path = self._save_path("Сводка покрытия", "Сводка покрытия", "HTML (*.html)")
        if not path:
            return
        path = path.with_suffix(".html")
        try:
            summary.write_html(path, summary.build(self.ctx.catalog, self.ctx.categories))
        except OSError as exc:
            return self._export_failed(exc)
        summary.mark_done(self.ctx.catalog)
        self._exported(path, "Сводка сохранена")

    def export_playlist(self):
        folder = self.ctx.download_dir()
        folder.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getSaveFileName(self.window, "Плейлист M3U", str(folder / "Плейлист.m3u8"),
                                                "Плейлист (*.m3u8 *.m3u)")
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() not in (".m3u", ".m3u8"):
            path = path.with_suffix(".m3u8")
        entries = sorted((e for e in self.ctx.catalog.entries() if e.status != cat.REJECTED), key=lambda e: e.id)
        try:
            count = playlist.write_m3u(path, entries)
        except OSError as exc:
            return self._export_failed(exc)
        if not count:
            path.unlink(missing_ok=True)
            QMessageBox.information(self.window, "Плейлист",
                                    "В каталоге нет скачанных роликов, файлы которых есть на диске.")
            return
        self._exported(path, f"Плейлист из {count} роликов сохранён")

    def export_showreel(self):
        """Склеить скачанные ролики услуги или категории в один MP4 для экрана (в фоне, с отменой)."""
        if self.showreel_worker and self.showreel_worker.isRunning():
            return
        ffmpeg = paths.ffmpeg_tool("ffmpeg")
        if not ffmpeg:
            QMessageBox.warning(self.window, "Шоурил", "Для шоурила нужен FFmpeg в папке программы.")
            return
        dialog = ShowreelDialog(self.ctx.catalog, self.ctx.categories, self.window)
        if not dialog.exec():
            return
        title, entries = dialog.title(), dialog.entries()
        folder = self.ctx.download_dir() / "Шоурил"
        folder.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getSaveFileName(self.window, "Шоурил для экрана",
                                                str(folder / f"{safe_name(title)}.mp4"), "Видео MP4 (*.mp4)")
        if not chosen:
            return
        path = Path(chosen).with_suffix(".mp4")
        progress = QProgressDialog(f"Собирается шоурил «{title}» из {len(entries)} роликов…", "Отмена", 0, 100,
                                   self.window)
        progress.setWindowTitle("Шоурил")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        worker = ShowreelWorker(title, entries, path, ffmpeg, paths.ffmpeg_tool("ffprobe"),
                                dialog.clip_seconds(), dialog.fade_seconds(), self.window)
        worker.progress.connect(progress.setValue)
        progress.canceled.connect(worker.cancel.set)
        worker.done.connect(lambda error, used: self._showreel_done(progress, path, error, used))
        self.showreel_worker = worker
        worker.start()

    def _showreel_done(self, progress: QProgressDialog, path: Path, error: str, used: list):
        progress.close()
        if error:
            QMessageBox.warning(self.window, "Шоурил", error)
            return
        self._exported(path, f"Шоурил из {len(used)} роликов сохранён, список авторов — рядом в .txt")

    def import_csv(self):
        chosen, _ = QFileDialog.getOpenFileName(self.window, "Импорт реестра CSV", str(paths.data_dir()),
                                                "CSV (*.csv)")
        if not chosen:
            return
        try:
            videos, links = registry.import_csv(Path(chosen), self.ctx.catalog, self.ctx.processes)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            QMessageBox.warning(self.window, "Импорт", f"Не удалось прочитать файл: {exc}")
            return
        self.catalog_changed.emit()
        QMessageBox.information(self.window, "Импорт", f"Загружено роликов: {videos}, привязок к услугам: {links}.\n"
                                "Дубли не создаются: ролики, которые уже были в каталоге, обновлены.")
