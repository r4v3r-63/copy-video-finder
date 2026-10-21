"""Адаптеры выдачи по ссылке: только официальные API и их реальные границы пагинации."""

from dataclasses import dataclass

from app.core.models import VideoResult
from app.core.stock_link import ListingLink
from app.sources.archive import API as ARCHIVE_API
from app.sources.archive import FIELDS, ArchiveSource, parse_doc
from app.sources.base import SourceError
from app.sources.pixabay import PixabaySource, parse_hit


@dataclass
class ListingPage:
    items: list[VideoResult]
    total: int
    accessible: int
    per_page: int


class PixabayListing:
    per_page = 200  # документированный максимум размера страницы, не предел числа роликов

    def __init__(self, source: PixabaySource):
        self.source = source

    def page(self, link: ListingLink, page: int) -> ListingPage:
        data = self.source._get({**link.params, "page": page, "per_page": self.per_page})
        available = min(int(data.get("totalHits", 0)), 500)
        remaining = max(0, available - (page - 1) * self.per_page)
        items = [parse_hit(hit) for hit in data.get("hits", [])[:remaining]]
        return ListingPage(items, int(data.get("total", available)), available, self.per_page)


class ArchiveListing:
    per_page = 100

    def __init__(self, source: ArchiveSource):
        self.source = source

    def page(self, link: ListingLink, page: int) -> ListingPage:
        params = {**link.params, "q": f'({link.params["q"]}) AND mediatype:movies AND '
                  '(licenseurl:*creativecommons.org* OR licenseurl:*publicdomain*) AND NOT identifier:youtube-*',
                  "fl[]": list(FIELDS), "rows": self.per_page, "page": page, "output": "json"}
        data = self.source._fetch(ARCHIVE_API, params)
        response = data.get("response") or {}
        items = [parse_doc(doc) for doc in response.get("docs", []) if doc.get("identifier")
                 and not doc["identifier"].startswith("youtube-")]
        total = int(response.get("numFound", 0))
        return ListingPage(items, total, total, self.per_page)


def make_listing(link: ListingLink, sources):
    source = next((source for source in sources if source.name == link.source), None)
    if source is None:
        raise SourceError(link.source, f"{link.source}: источник не настроен.")
    if not source.is_configured():
        raise SourceError(link.source, source.missing_key_message())
    return PixabayListing(source) if link.source == "Pixabay" else ArchiveListing(source)
