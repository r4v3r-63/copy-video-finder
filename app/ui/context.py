"""Общее состояние окна: настройки, каталог, кэш, справочник услуг и словарь.

Один объект на всё окно: вкладки и действия видят одно и то же, а новый справочник
(`set_categories`) сразу доходит до всех.
"""

from pathlib import Path

from app import paths
from app.core import processes, query
from app.core.models import SearchFilters
from app.services.search import print_stems
from app.sources.base import Source
from app.sources.cache import Cache
from app.sources.registry import all_sources, make_sources
from app.storage import catalog as cat
from app.storage.settings import Settings


class AppContext:
    def __init__(self, settings: Settings, categories: list[processes.Category], cache: Cache | None = None,
                 store: cat.Catalog | None = None):
        self.settings = settings
        self.catalog = store or cat.Catalog()
        self.cache = cache or Cache()
        self.dictionary = query.Dictionary.load()
        self.set_categories(categories)

    def set_categories(self, categories: list[processes.Category]):
        """Новый справочник услуг: подсказки, фразы поиска, слова полиграфии."""
        self.categories = categories
        self.processes = processes.all_processes(categories)
        self.printing = print_stems(self.processes)

    def process(self, process_id: str | None) -> processes.Process | None:
        return next((p for p in self.processes if p.id == process_id), None)

    def filters(self) -> SearchFilters:
        return SearchFilters(min_duration=self.settings.min_duration, max_duration=self.settings.max_duration)

    def download_dir(self) -> Path:
        return Path(self.settings.download_dir) if self.settings.download_dir else paths.default_download_dir()

    def sources(self) -> tuple[list[Source], list[str]]:
        """Площадки для поиска и названия пропущенных из-за отсутствия ключа."""
        return make_sources(self.settings, self.cache)

    def all_sources(self) -> list[Source]:
        return all_sources(self.settings, self.cache)
