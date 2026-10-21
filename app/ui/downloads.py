"""Скачивание из интерфейса: очередь, состояние каждого ролика и запись в каталог."""

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.models import VideoResult
from app.services.downloader import DownloadJob, choose_file
from app.storage import catalog as cat
from app.ui.context import AppContext
from app.workers.download import DownloadQueue


class DownloadController(QObject):
    state_changed = pyqtSignal(object, str, str, bool)  # ключ ролика, текст, путь, идёт ли скачивание
    catalog_state = pyqtSignal(object, str, int)  # ключ ролика, отметка, номер в каталоге
    catalog_changed = pyqtSignal()
    message = pyqtSignal(str)

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.queue = DownloadQueue(self)
        self.queue.progress.connect(self._on_progress)
        self.queue.done.connect(self._on_done)
        self.queue.failed.connect(self._on_failed)
        self.state: dict[tuple[str, str], tuple[str, str, bool]] = {}  # ключ ролика → (текст, путь, идёт)

    def busy(self) -> bool:
        return any(active for _, _, active in self.state.values())

    def is_queued(self, key) -> bool:
        return self.state.get(key, ("", "", False))[2]

    def download_entries(self, entries, selected_process: str | None):
        """Скачать выбранные в каталоге ролики; отметки и подтверждения лицензии не меняются."""
        queued, skipped = 0, 0
        for entry in entries:
            result = entry.result
            file = choose_file(result, self.ctx.settings.allow_4k)
            if not file or self.is_queued(result.key):
                skipped += 1
                continue
            pid = selected_process if selected_process in entry.links else next(iter(entry.links), None)
            self.download(result, file, self.ctx.process(pid), "Из каталога", preserve_status=True)
            queued += 1
        self.message.emit(f"В очереди на скачивание: {queued}. Без файла или уже в очереди: {skipped}.")

    def download(self, result: VideoResult, file, process, query_text, preserve_status=False):
        if self.is_queued(result.key):
            return
        catalog = self.ctx.catalog
        # Скачанный ролик попадает в каталог; номер в каталоге — в имени файла.
        # Лицензию это не подтверждает: для этого кнопка «В каталог — лицензию проверил».
        entry = catalog.find(result.key)
        # Степень соответствия: уже выбранную в каталоге не перезаписываем, иначе — подсказка уровня поиска.
        match = (entry.links.get(process.id) if entry and process else None) or result.match_hint or cat.EXACT
        if preserve_status and entry:
            video_id = entry.id
        else:
            video_id = catalog.add(result)
            catalog.set_status(video_id, cat.SELECTED)
            if process:
                catalog.link(video_id, process.id, match)
        self.catalog_state.emit(result.key, entry.status if preserve_status and entry else cat.SELECTED, video_id)
        settings = self.ctx.settings
        source = next((s for s in self.ctx.all_sources() if s.name == result.source), None)
        job = DownloadJob(result=result, file=file, process=process,
                          query_text=query_text, base_dir=self.ctx.download_dir(),
                          convert=settings.convert_to_mp4, catalog_id=video_id,
                          trim=settings.trim_seconds, mute=settings.mute_audio,
                          resolver=getattr(source, "resolve_download", None))
        self.queue.add(job)
        self._set_state(result.key, "В очереди на скачивание…", "", True)

    def stop(self):
        self.queue.stop()

    def _set_state(self, key, text: str, path: str, busy: bool):
        self.state[key] = (text, path, busy)
        self.state_changed.emit(key, text, path, busy)

    def _job_key(self, job_id: int):
        return self.queue.jobs[job_id].result.key

    def _on_progress(self, job_id: int, done: int, total: int):
        if done < 0:
            text = "Конвертация в MP4…"
        elif total:
            text = f"Скачивается: {done * 100 // total}% ({done / 1e6:.1f} из {total / 1e6:.1f} МБ)"
        else:
            text = f"Скачивается: {done / 1e6:.1f} МБ"
        self._set_state(self._job_key(job_id), text, "", True)

    def _on_done(self, job_id: int, path: str, note: str):
        text = f"Скачано: {path}" + (f" ({note})" if note else "") + ". Рядом — файл с лицензией."
        job = self.queue.jobs[job_id]
        if job.catalog_id:
            self.ctx.catalog.set_downloaded(job.catalog_id, path)
            if job.loop_hint is not None:
                self.ctx.catalog.set_loop_hint(job.catalog_id, job.loop_hint)
        self._set_state(self._job_key(job_id), text, path, False)
        self.message.emit(f"Скачано: {Path(path).name}")
        self.catalog_changed.emit()

    def _on_failed(self, job_id: int, message: str):
        self._set_state(self._job_key(job_id), f"Не скачано: {message}", "", False)
