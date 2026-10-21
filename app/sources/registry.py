"""Список площадок поиска: какие подключены и с какими ключами из настроек."""

from app.sources.archive import ArchiveSource
from app.sources.base import Source
from app.sources.cache import Cache
from app.sources.coverr import CoverrSource
from app.sources.europeana import EuropeanaSource
from app.sources.pexels import PexelsSource
from app.sources.pixabay import PixabaySource
from app.sources.videezy import VideezySource
from app.sources.vimeo import VimeoSource
from app.sources.vk import Tokens, VkSource
from app.sources.web import WebSource
from app.sources.yandex_video import YandexVideoSource
from app.storage.settings import Settings


def all_sources(settings: Settings, cache: Cache) -> list[Source]:
    sources = [
        PixabaySource(settings.key("pixabay_api_key"), cache),
        CoverrSource(settings.key("coverr_api_key"), cache),
        VimeoSource(settings.key("vimeo_token"), cache),
        # Wikimedia Commons не ищется (решение пользователя 20.10.2026); его страницы из интернета — тоже.
        EuropeanaSource(settings.key("europeana_api_key"), cache),
        ArchiveSource(cache),
        VideezySource(cache),
        PexelsSource(settings.key("pexels_api_key"), cache),
        YandexVideoSource(cache),  # Rutube, VK Видео, Дзен, Одноклассники, YouTube — одной выдачей
        VkSource(settings.vk_token, cache, app_id=settings.vk_app_id, refresh_token=settings.vk_refresh_token,
                 device_id=settings.vk_device_id, expires_at=settings.vk_token_expires,
                 on_tokens=lambda t: _save_vk_tokens(settings, t),
                 token_supplier=lambda: Tokens(settings.vk_token, settings.vk_refresh_token,
                                                settings.vk_device_id, settings.vk_token_expires)),
    ]
    # Поиск по интернету: найденные страницы известных площадок открываются их модулями.
    return sources + [WebSource(cache, delegates=sources, tavily_key=settings.key("tavily_api_key"),
                                brave_key=settings.key("brave_api_key"))]


def _save_vk_tokens(settings: Settings, tokens) -> None:
    """VK выдал новые ключи (продление) — сразу в настройки: прежний ключ обновления больше не годится."""
    settings.vk_token, settings.vk_refresh_token = tokens.access, tokens.refresh
    settings.vk_device_id, settings.vk_token_expires = tokens.device_id, tokens.expires_at
    settings.save()


def make_sources(settings: Settings, cache: Cache) -> tuple[list[Source], list[str]]:
    """Площадки для поиска и названия пропущенных из-за отсутствия ключа."""
    sources = all_sources(settings, cache)
    return [s for s in sources if s.is_configured()], [s.name for s in sources if not s.is_configured()]
