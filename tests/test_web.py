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
    def post(self, url, data=None, headers=None, timeout=None):
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
    assert skipped(links[0][0])  # YouTube не подключается
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


@pytest.mark.parametrize("url", ["https://mixkit.co/free-stock-video/x/", "https://www.shutterstock.com/video/1",
                                 "https://youtu.be/abc"])
def test_skipped_sites(url):
    assert skipped(url)


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
    assert r.license.name == "CC BY 4.0" and r.license.status == licenses.ATTRIBUTION
    assert parse_page(PAGE.format(lic="by-nc"), "https://example.org/clips/7").license.status == licenses.REJECTED


def test_generic_page_needs_file_and_license():
    assert parse_page(PAGE.replace("creativecommons.org/licenses/{lic}/4.0/", "example.org/terms"),
                      "https://example.org/clips/7") is None
    assert parse_page(PAGE.format(lic="by").replace("wire-binding.mp4", "player"), "https://example.org/x") is None


def test_web_search_opens_pages(cache, clock):
    ddg = ('<a class="result__a" href="https://www.youtube.com/watch?v=1">YouTube</a>'
           '<a class="result__a" href="https://example.org/clips/7">Wire binding</a>')
    src = WebSource("", cache, session=PostSession(FakeResponse(text=ddg)), now=clock)
    src.pages.session = FakeSession(FakeResponse(text=PAGE.format(lic="by")))
    results = src.search("wire binding machine", SearchFilters())
    assert [r.source_id for r in results] == ["https://example.org/clips/7"]
    call = src.session.calls[0]
    assert call["post"] and call["params"]["q"] == "wire binding machine free stock video"
    assert len(src.pages.session.calls) == 1  # YouTube не открывался


def test_web_search_blocked_by_ddg(cache, clock):
    src = WebSource("", cache, session=PostSession(FakeResponse(202, fixture_text("ddg_anomaly.html"))), now=clock)
    with pytest.raises(RateLimitError, match="Brave"):
        src.search("wire binding", SearchFilters())
    with pytest.raises(RateLimitError):  # 10 минут не беспокоим
        src.search("comb binding", SearchFilters())
    assert len(src.session.calls) == 1


def test_web_search_brave(cache, clock):
    data = '{"web": {"results": [{"url": "https://example.org/clips/7", "title": "Wire binding"}]}}'
    src = WebSource("bkey", cache, session=FakeSession(FakeResponse(text=data)), now=clock)
    src.pages.session = FakeSession(FakeResponse(text=PAGE.format(lic="by")))
    assert len(src.search("wire binding", SearchFilters(), lang="de")) == 1
    call = src.session.calls[0]
    assert call["headers"]["X-Subscription-Token"] == "bkey"
    assert call["params"]["q"] == "wire binding kostenlos Video"
    assert src.engine == "Brave"


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


def test_brave_stays_within_free_credit(cache, clock):
    from app.sources.web import BRAVE_MONTHLY
    src = WebSource("bkey", cache, session=FakeSession(), now=clock)
    monthly = [count for count, window, _ in src.limits if window >= 28 * 86400]
    assert monthly == [BRAVE_MONTHLY] and BRAVE_MONTHLY < 1000  # $5 бесплатно = 1000 запросов
