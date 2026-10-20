"""Videezy и поиск по интернету — на сохранённых страницах, без сети."""

import pytest

from app import licenses
from app.cache import Cache
from app.sources.base import RateLimitError, SearchFilters, VideoFile
from app.sources.videezy import FREE_LICENSE, VideezySource, license_of, parse_search, parse_video
from app.sources.web import WebSource, ddg_blocked, known_site, parse_ddg, parse_page, skipped
from tests.fakes import Clock, FakeResponse, FakeSession, fixture_text

VIDEO_PATH = "industry/48837-old-printing-machine-working"


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(tmp_path, clock):
    c = Cache(tmp_path / "cache.sqlite", now=clock)
    yield c
    c.close()


class PostSession(FakeSession):
    def post(self, url, data=None, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(data or {}), "headers": dict(headers or {}), "post": True})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def test_videezy_search_page():
    cards = parse_search(fixture_text("videezy_search.html"))
    paths = [p for p, _ in cards]
    assert VIDEO_PATH in paths
    assert all(thumb.startswith("https://static.videezy.com/") for _, thumb in cards)
    assert len(paths) == len(set(paths))


def test_videezy_video_page():
    r = parse_video(fixture_text("videezy_video.html"), VIDEO_PATH)
    assert r.source == "Videezy" and r.source_id == VIDEO_PATH
    assert r.title == "Old Printing Machine Working"
    assert "printing machine" in r.description and "print shop" in r.description  # описание и теги
    assert r.page_url == "https://www.videezy.com/" + VIDEO_PATH
    assert r.preview_video.endswith(".mp4") and r.preview_url.endswith(".jpg")
    assert r.author == "topntp"
    assert r.license.name == FREE_LICENSE and r.license.status == licenses.ATTRIBUTION
    assert "Videezy.com" in r.license.note
    assert r.files[0].page and r.files[0].url == r.page_url  # ссылку на файл даст страница


@pytest.mark.parametrize("marks, status", [
    ("data-license-type=\"standard\" var resourceType = 'free';", licenses.ATTRIBUTION),
    ("data-license-type=\"standard\" var resourceType = 'pro';", licenses.REJECTED),
    ("data-license-type=\"editorial\" var resourceType = 'free';", licenses.REJECTED),
])
def test_videezy_license(marks, status):
    assert license_of(marks).status == status


def test_videezy_resolves_file_from_page():
    page = FakeResponse(text=fixture_text("videezy_video.html"))
    page.raise_for_status = lambda: None
    file_url = "https://static.videezy.com/system/protected/files/000/048/837/old_printing_machine_14.mov"
    session = FakeSession(page, FakeResponse(302, "", {"Location": file_url}))
    session.get = _get_with_kwargs(session)
    src = VideezySource.__new__(VideezySource)
    file = VideoFile("оригинал с сайта", "https://www.videezy.com/" + VIDEO_PATH, page=True)
    assert src.resolve_download(file, session) == file_url
    assert session.calls[1]["url"].startswith("https://www.videezy.com/download/48837?download_auth_hash=")


def test_videezy_no_file_when_redirected_back():
    page = FakeResponse(text=fixture_text("videezy_video.html"))
    page.raise_for_status = lambda: None
    url = "https://www.videezy.com/" + VIDEO_PATH
    session = FakeSession(page, FakeResponse(302, "", {"Location": url}))
    session.get = _get_with_kwargs(session)
    src = VideezySource.__new__(VideezySource)
    from app.sources.base import NotFoundError
    with pytest.raises(NotFoundError):
        src.resolve_download(VideoFile("x", url, page=True), session)


def _get_with_kwargs(session):
    original = FakeSession.get

    def get(url, params=None, headers=None, timeout=None, allow_redirects=True):
        return original(session, url, params, headers, timeout)
    return get


def test_ddg_results_and_block():
    links = parse_ddg(fixture_text("ddg_results.html"))
    assert links and all(u.startswith("http") for u, _ in links)
    assert not skipped(links[0][0])  # YouTube ищется (решение пользователя 20.10.2026)
    assert ddg_blocked(fixture_text("ddg_anomaly.html"), 202)
    assert not ddg_blocked(fixture_text("ddg_results.html"), 200)


@pytest.mark.parametrize("url, expected", [
    ("https://www.pexels.com/video/printing-machine-3195441/", ("Pexels", "3195441")),
    ("https://pixabay.com/videos/printer-printing-office-34594/", ("Pixabay", "34594")),
    ("https://vimeo.com/48686230", ("Vimeo", "48686230")),
    ("https://commons.wikimedia.org/wiki/File:Paper_Cutter.webm", ("Wikimedia Commons", "File:Paper_Cutter.webm")),
    ("https://archive.org/details/bookbinding_film", ("Internet Archive", "bookbinding_film")),
    ("https://www.videezy.com/" + VIDEO_PATH, ("Videezy", VIDEO_PATH)),
    ("https://example.org/video/1", None),
])
def test_known_sites(url, expected):
    assert known_site(url) == expected


@pytest.mark.parametrize("url, skip", [("https://www.shutterstock.com/video/1", True),
                                       ("https://mixkit.co/free-stock-video/x/", False),
                                       ("https://youtu.be/abc", False)])
def test_skipped_sites(url, skip):
    assert skipped(url) is skip


PAGE = """<html><head><title>Bookbinding</title>
<meta property="og:title" content="Wire binding machine at work">
<meta property="og:image" content="/thumb.jpg">
<meta property="og:video" content="https://media.example.org/wire-binding.mp4">
</head><body><a rel="license" href="https://creativecommons.org/licenses/{lic}/4.0/">CC</a></body></html>"""


def test_generic_page_with_open_license():
    r = parse_page(PAGE.format(lic="by"), "https://example.org/clips/7")
    assert r.source == "example.org" and r.source_id == "https://example.org/clips/7"
    assert r.title == "Wire binding machine at work"
    assert r.files[0].url == "https://media.example.org/wire-binding.mp4"  # ссылка со страницы, не придумана
    assert r.preview_url == "https://example.org/thumb.jpg"
    # rel="license" — лицензия страницы, а не ролика: только ручная проверка
    assert r.license.name == "CC BY 4.0" and r.license.status == licenses.REVIEW
    assert parse_page(PAGE.format(lic="by-nc"), "https://example.org/clips/7").license.status == licenses.REJECTED


def test_generic_page_without_license_or_file():
    """Любой ролик со страницы: без лицензии — «Требует проверки», без файла — смотреть на сайте."""
    r = parse_page(PAGE.replace("creativecommons.org/licenses/{lic}/4.0/", "example.org/terms"),
                   "https://example.org/clips/7")
    assert r.license.status == licenses.REVIEW and r.license.name == "Не указана" and r.files
    r = parse_page(PAGE.format(lic="by").replace("wire-binding.mp4", "player"), "https://example.org/x")
    assert r is not None and r.files == []  # og:video есть, но не файл — страница с плеером
    assert parse_page("<html><head><title>Статья про печать</title></head></html>", "https://example.org/a") is None


def web(cache, clock, ddg=None, tavily_key="", tavily=None, page=None):
    src = WebSource(cache, now=clock, tavily_key=tavily_key)
    sessions = {"DuckDuckGo": ddg, "Tavily": tavily}
    for engine in src.engines:
        engine.session = sessions.get(engine.name) or PostSession(FakeResponse(500, ""))
    src.pages.session = page or FakeSession(FakeResponse(text=PAGE.format(lic="by")))
    return src


def test_web_search_opens_pages(cache, clock):
    ddg = ('<a class="result__a" href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">YouTube</a>'
           '<a class="result__a" href="https://example.org/clips/7">Wire binding</a>')
    src = web(cache, clock, ddg=PostSession(FakeResponse(text=ddg)))
    results = src.search("wire binding machine", SearchFilters())
    assert [r.source_id for r in results] == ["https://example.org/clips/7"]  # oEmbed YouTube не ответил
    call = src.engines[0].session.calls[0]
    assert call["post"] and call["params"]["q"] == "wire binding machine video"
    assert src.pages.session.calls[0]["url"] == "https://www.youtube.com/oembed"


def test_ddg_blocked_rests_ten_minutes(cache, clock):
    src = web(cache, clock, ddg=PostSession(FakeResponse(202, fixture_text("ddg_anomaly.html"))))
    with pytest.raises(RateLimitError, match="временно недоступен"):
        src.search("wire binding", SearchFilters())
    with pytest.raises(RateLimitError):
        src.search("comb binding", SearchFilters())
    assert len(src.engines[0].session.calls) == 1  # блокировку не обходим: 10 минут не беспокоим


def test_tavily_blocked_falls_back_to_ddg(cache, clock):
    ddg = '<a class="result__a" href="https://example.org/clips/7">Wire binding</a>'
    src = web(cache, clock, tavily_key="tvly-key", tavily=JsonSession(FakeResponse(432, "")),
              ddg=PostSession(FakeResponse(text=ddg)))
    assert [r.source_id for r in src.search("wire binding", SearchFilters())] == ["https://example.org/clips/7"]


class JsonSession(PostSession):
    def post(self, url, data=None, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(json or data or {}), "headers": dict(headers or {})})
        return self.responses[0]


def test_tavily_first_when_key_given(cache, clock):
    data = '{"results": [{"url": "https://example.org/clips/7", "title": "Wire binding"}]}'
    src = web(cache, clock, tavily_key="tvly-key", tavily=JsonSession(FakeResponse(text=data)))
    assert [e.name for e in src.engines] == ["Tavily", "DuckDuckGo"]
    assert len(src.search("wire binding", SearchFilters(), lang="de")) == 1
    call = src.engines[0].session.calls[0]
    assert call["headers"]["Authorization"] == "Bearer tvly-key"
    assert call["params"]["query"] == "wire binding Video"  # обычный поиск по всему интернету
    assert "include_domains" not in call["params"]


def test_free_limits_stay_free(cache, clock):
    from app.sources.web import BRAVE_MONTHLY, TAVILY_MONTHLY, BraveEngine, TavilyEngine
    for engine, cap, free in ((TavilyEngine("k", cache, now=clock), TAVILY_MONTHLY, 1000),
                              (BraveEngine("k", cache, now=clock), BRAVE_MONTHLY, 1000)):
        monthly = [count for count, window, _ in engine.limits if window >= 28 * 86400]
        assert monthly == [cap] and cap < free
    assert [e.name for e in WebSource(cache, now=clock).engines] == ["DuckDuckGo"]  # без ключей


def test_downloader_takes_file_from_page(tmp_path):
    from datetime import datetime

    from app.downloader import DownloadJob, run_job
    from tests.fakes import StreamResponse, StreamSession
    r = parse_video(fixture_text("videezy_video.html"), VIDEO_PATH)
    url = "https://static.videezy.com/system/protected/files/000/048/837/old_printing_machine_14.mov"
    job = DownloadJob(result=r, file=r.files[0], process=None, query_text="", base_dir=tmp_path, convert=False,
                      resolver=lambda f, s: url)
    session = StreamSession(StreamResponse(b"movdata"))
    path, _ = run_job(job, session, now=lambda: datetime(2026, 10, 19))
    assert path.suffix == ".mov" and path.read_bytes() == b"movdata"
    assert session.calls[0]["url"] == url



def test_stock_listing_pages_not_opened(cache, clock):
    src = web(cache, clock)
    for url in ("https://www.pexels.com/search/binding%20machine", "https://pixabay.com/images/search/bind",
                "https://vimeo.com/ondemand/12gbw1", "https://archive.org/download/x/y.pdf",
                "https://www.pexels.com/video/laser-engraving-34240702/",  # без ключа Pexels не открыть
                "https://commons.wikimedia.org/wiki/File:Laser_engraving_machine.jpg"):
        assert src.open(url) is None
    assert src.pages.session.calls == []


def test_video_own_terms_not_replaced_by_page_license():
    """У ролика «все права защищены», CC BY в подвале — для текста: ролик не со свободной лицензией."""
    ld = ('<script type="application/ld+json">{"@type": "VideoObject", "name": "Wire binding", '
          '"contentUrl": "https://media.example.org/wire-binding.mp4", "license": "%s"}</script>')
    footer = '<footer>Text: <a href="https://creativecommons.org/licenses/by/4.0/">CC BY</a></footer>'
    for own in ("All rights reserved", "https://example.org/terms"):
        page = f"<html><head>{ld % own}</head><body>{footer}</body></html>"
        r = parse_page(page, "https://example.org/clips/8")
        assert r.license.status == licenses.REVIEW and "CC" not in r.license.name
    # без лицензии у ролика ссылка из подвала — только «требует проверки»
    page = f"<html><head>{ld.replace(', \"license\": \"%s\"', '')}</head><body>{footer}</body></html>"
    r = parse_page(page, "https://example.org/clips/8")
    assert r.license.status == licenses.REVIEW and "не для ролика" in r.license.note
    # лицензия у самого ролика — принимается
    page = f"<html><head>{ld % 'https://creativecommons.org/publicdomain/zero/1.0/'}</head></html>"
    assert parse_page(page, "https://example.org/clips/8").license.status == licenses.ALLOWED


def test_youtube_by_oembed(cache, clock):
    """YouTube — по официальному oEmbed: название, автор, превью; файла нет, лицензия — на проверку."""
    oembed = ('{"title": "Laser printer printing", "author_name": "Print Shop", '
              '"author_url": "https://www.youtube.com/@shop", "thumbnail_url": "https://i.ytimg.com/vi/x/hq.jpg"}')
    src = web(cache, clock, page=FakeSession(FakeResponse(text=oembed)))
    r = src.open("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5")
    assert r.source == "YouTube" and r.source_id == "dQw4w9WgXcQ" and r.title == "Laser printer printing"
    assert r.files == [] and r.license.status == licenses.REVIEW and r.author == "Print Shop"
    assert r.page_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert src.open("https://www.youtube.com/shorts/dQw4w9WgXcQ") is None  # вертикальные — нет
    assert src.open("https://www.youtube.com/@shop") is None


def test_tavily_quota_message(cache, clock):
    ddg = '<a class="result__a" href="https://example.org/clips/7">Wire binding</a>'
    src = web(cache, clock, tavily_key="tvly-key", tavily=JsonSession(FakeResponse(432, "")),
              ddg=PostSession(FakeResponse(text=ddg)))
    with pytest.raises(RateLimitError, match="закончились бесплатные"):
        src.engines[0].find("wire binding")
    assert len(src.search("wire binding", SearchFilters())) == 1  # без Tavily — через DuckDuckGo
    assert len(src.engines[0].session.calls) == 1  # сутки Tavily не спрашиваем
