"""Ручное добавление по сохранённым ответам, без сетевых запросов."""

import threading

import pytest

from app.core import licenses, processes
from app.services import manual
from app.services.manual import normalize_url, resolve_link
from app.sources.cache import Cache
from app.sources.pixabay import PixabaySource
from app.sources.web import WebSource, parse_page
from app.storage.catalog import EXACT, PARTIAL, REJECTED, Catalog
from app.ui.dialogs.add_link_dialog import AddLinkDialog
from app.workers.manual import LinkWorker
from tests.fakes import FakeResponse, FakeSession, fixture_text

URL = "https://example.org/video/lamination"


@pytest.fixture
def web(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite")
    source = WebSource(cache)
    source.pages.session = FakeSession(FakeResponse(text=fixture_text("manual_video.html")))
    yield source
    cache.close()


@pytest.fixture
def store(tmp_path):
    catalog = Catalog(tmp_path / "catalog.sqlite")
    yield catalog
    catalog.close()


def ready_dialog(web, store, url=URL):
    dialog = AddLinkDialog(store, processes.load_catalog(), web, "lam_pouch")
    dialog.url.setText(url)
    dialog.worker = LinkWorker(web, url, dialog)
    dialog.worker.run()
    dialog._loaded()
    return dialog


@pytest.mark.parametrize("url", ["", "example.org/video", "file:///tmp/a.mp4", "javascript:alert(1)",
                                 "https://u:p@example.org/video", "https://example.org:wrong/a",
                                 "https://example.org/a\nb", "https://example.org/a\\b"])
def test_invalid_url_does_not_make_requests(web, url):
    with pytest.raises(ValueError):
        resolve_link(web, url)
    assert not web.pages.session.calls


def test_page_uses_only_supplied_file_and_video_license(web):
    result = resolve_link(web, " HTTPS://EXAMPLE.ORG/video/lamination#player ")
    assert result.page_url == URL
    assert result.title == "Ламинирование визиток" and result.author == "Копицентр"
    assert result.duration == 25
    assert result.files[0].url == "https://media.example.org/lamination.mp4"
    assert result.license.status == licenses.ATTRIBUTION
    assert resolve_link(web, URL).key == result.key
    assert len(web.pages.session.calls) == 1  # повторное открытие — из общего кэша


def test_missing_file_and_license_can_be_saved_as_page_link(web, store):
    web.pages.session = FakeSession(FakeResponse(text='<meta property="og:type" content="video.other">'
                                                     '<meta property="og:title" content="Ламинирование">'))
    dialog = ready_dialog(web, store)
    try:
        assert dialog.save.isEnabled()
        assert not dialog.result.files
        assert dialog.result.license.status == licenses.REVIEW
        dialog.confirmed.setChecked(True)
        dialog.horizontal.setChecked(True)
        dialog._save()
        entry = store.get(dialog.video_id)
        assert entry.result.page_url == URL and not entry.counts_for_coverage
    finally:
        dialog.close()


@pytest.mark.parametrize("kind", ["by-nc", "by-nd"])
def test_nonfree_license_stays_rejected_even_after_confirmation(web, store, kind):
    page = fixture_text("manual_video.html").replace("licenses/by/", f"licenses/{kind}/")
    web.pages.session = FakeSession(FakeResponse(text=page))
    dialog = ready_dialog(web, store)
    try:
        dialog.confirmed.setChecked(True)
        dialog.horizontal.setChecked(True)
        dialog._save()
        entry = store.get(dialog.video_id)
        assert entry.result.license.status == licenses.REJECTED
        assert not entry.counts_for_coverage
    finally:
        dialog.close()


def test_page_license_does_not_confirm_video_license(web):
    page = fixture_text("manual_video.html").replace('"license": "https://creativecommons.org/licenses/by/4.0/",', "")
    page += '<a rel="license" href="https://creativecommons.org/licenses/by/4.0/">CC</a>'
    web.pages.session = FakeSession(FakeResponse(text=page))
    assert resolve_link(web, URL).license.status == licenses.REVIEW


def test_article_is_not_added_as_video(web, store):
    web.pages.session = FakeSession(FakeResponse(text="<title>Поиск роликов</title>"))
    dialog = ready_dialog(web, store)
    try:
        assert not dialog.save.isEnabled()
        assert "не удалось распознать" in dialog.info.text()
        dialog._save()
        assert not store.entries()
    finally:
        dialog.close()


def test_youtube_aliases_have_same_identity_and_no_download(web):
    web.pages.session = FakeSession(FakeResponse(text='{"title":"Печать", "author_name":"Автор"}'))
    short = resolve_link(web, "https://youtu.be/AbCdEf12345?t=10")
    full = resolve_link(web, "https://www.youtube.com/watch?v=AbCdEf12345&list=123")
    assert short.key == full.key == ("YouTube", "AbCdEf12345")
    assert short.files == [] and short.license.status == licenses.REVIEW


def test_known_platform_uses_api_and_preserves_its_identity(web):
    pixabay = PixabaySource("test-key", web.cache,
                            session=FakeSession(FakeResponse(text=fixture_text("pixabay_search.json"))))
    web.delegates["Pixabay"] = pixabay
    result = resolve_link(web, "https://pixabay.com/videos/printing-123/")
    assert result.source == "Pixabay"
    assert pixabay.session.calls and not web.pages.session.calls


def test_missing_key_keeps_known_link_without_claiming_free_license(web):
    result = resolve_link(web, "https://pixabay.com/videos/printing-123/")
    assert result.key == ("Pixabay", "123")
    assert result.license.status == licenses.REVIEW and not result.files
    assert not web.pages.session.calls
    vk = resolve_link(web, "https://vkvideo.ru/video-123_456?ref=share")
    assert vk.key == ("VK Видео", "https://vk.com/video-123_456")


def test_fake_platform_domain_is_read_as_ordinary_page(web):
    result = resolve_link(web, "https://example.org/pixabay.com/videos/printing-123/")
    assert result.source == "example.org"
    assert web.pages.session.calls


@pytest.mark.parametrize("url", ["https://youtube.com/shorts/AbCdEf12345", "https://pixabay.com/videos/",
                                 "https://commons.wikimedia.org/wiki/File:Print.webm", "https://shutterstock.com/video/1"])
def test_listings_and_excluded_sites_are_not_opened(web, url):
    with pytest.raises(ValueError):
        resolve_link(web, url)
    assert not web.pages.session.calls


def test_save_requires_explicit_license_confirmation_and_keeps_selected_group(web, store):
    dialog = ready_dialog(web, store)
    try:
        dialog.title.setText("Моё название")
        dialog.match.setCurrentText(PARTIAL)
        assert not dialog.confirmed.isChecked()
        dialog._save()
        entry = store.get(dialog.video_id)
        assert entry.result.title == "Моё название"
        assert entry.links == {"lam_pouch": PARTIAL}
        assert not entry.license_confirmed and not entry.counts_for_coverage
    finally:
        dialog.close()


def test_confirmed_free_video_counts_for_selected_group(web, store):
    dialog = ready_dialog(web, store)
    try:
        dialog.confirmed.setChecked(True)
        dialog.horizontal.setChecked(True)
        dialog._save()
        assert store.get(dialog.video_id).counts_for_coverage
        assert store.coverage() == {"lam_pouch": 1}
    finally:
        dialog.close()


@pytest.mark.parametrize("width", [720, 1080])
def test_known_vertical_or_square_video_is_rejected(web, width):
    page = fixture_text("manual_video.html").replace('"width": 1920', f'"width": {width}')
    web.pages.session = FakeSession(FakeResponse(text=page))
    with pytest.raises(ValueError, match="горизонтальными"):
        resolve_link(web, URL)


def test_duplicate_page_from_yandex_keeps_existing_source_identity(web, store):
    result = resolve_link(web, URL)
    result.source = "Название площадки из Яндекса"
    result.source_id = URL + "#player"
    video_id = store.select(result)
    dialog = ready_dialog(web, store)
    try:
        dialog._save()
        assert dialog.video_id == video_id and len(store.entries()) == 1
    finally:
        dialog.close()


def test_duplicate_opens_existing_without_changing_user_decisions(web, store):
    result = resolve_link(web, URL)
    video_id = store.select(result, "guillotine", EXACT)
    store.set_status(video_id, REJECTED)
    store.set_title(video_id, "Правка пользователя")
    store.set_comment(video_id, "Не подходит")
    dialog = ready_dialog(web, store)
    try:
        assert "уже в каталоге" in dialog.info.text()
        assert dialog.save.text() == "Открыть запись"
        dialog._save()
        assert dialog.video_id == video_id and len(store.entries()) == 1
        entry = store.get(video_id)
        assert entry.status == REJECTED and entry.comment == "Не подходит"
        assert entry.result.title == "Правка пользователя"
        assert entry.license_confirmed and entry.links == {"guillotine": EXACT}
    finally:
        dialog.close()


def test_changed_url_invalidates_loaded_result(web, store):
    dialog = ready_dialog(web, store)
    try:
        dialog.url.setText("https://example.org/another")
        assert dialog.result is None and not dialog.save.isEnabled()
        dialog._save()
        assert not store.entries()
    finally:
        dialog.close()


def test_rate_limit_is_reported_without_saving(web, store):
    web.pages.session = FakeSession(FakeResponse(429))
    worker = LinkWorker(web, URL)
    worker.run()
    assert worker.result is None and "лимит" in worker.error
    assert not store.entries()


def test_stopping_dialog_cancels_and_waits_for_background_request(web, store, monkeypatch):
    started = threading.Event()

    def slow_resolve(source, url):
        started.set()
        assert source.stop.wait(5)
        return parse_page(fixture_text("manual_video.html"), url)

    monkeypatch.setattr(manual, "resolve_link", slow_resolve)
    dialog = AddLinkDialog(store, processes.load_catalog(), web)
    dialog.url.setText(URL)
    dialog._load()
    try:
        assert started.wait(5)
        dialog.stop()
        assert not dialog.worker.isRunning()
        assert dialog.worker.result is None and not store.entries()
    finally:
        dialog.stop()
        dialog.close()


def test_url_normalization_keeps_video_query():
    assert normalize_url(" https://EXAMPLE.org/watch?id=4#video ") == "https://example.org/watch?id=4"
