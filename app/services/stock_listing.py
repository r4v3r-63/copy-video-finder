"""Сбор выдачи с отменой, потоковой передачей страниц и устранением дублей."""

from app.sources.base import SourceError


def collect_listing(link, adapter, cancel, on_page):
    seen = set()
    page = link.page
    while not cancel.is_set():
        result = adapter.page(link, page)
        if cancel.is_set():
            return
        fresh = []
        for item in result.items:
            if item.key not in seen:
                seen.add(item.key)
                fresh.append(item)
        on_page(fresh, result.total, result.accessible)
        if not result.items or page * result.per_page >= result.accessible:
            return
        if not fresh:
            raise SourceError(link.source, "Площадка повторила страницу без новых роликов. Обход остановлен.")
        page += 1
