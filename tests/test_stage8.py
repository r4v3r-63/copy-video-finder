import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import catalog as cat
from app import licenses, similar
from app.export.playlist import write_m3u
from app.maintenance import check_links, hash_previews, link_check_due
from app.sources.base import LicenseInfo, NotFoundError, SourceError, VideoFile, VideoResult
from tests.fakes import Clock, FakeResponse


def png(draw=None, size=(160, 90), color=(200, 200, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    if draw:
        draw(ImageDraw.Draw(img), size[0] / 160, size[1] / 90)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def scene(d, sx, sy):  # фигуры — в долях размера: та же картинка при любом разрешении
    d.rectangle((10 * sx, 10 * sy, 60 * sx, 60 * sy), fill=(20, 20, 20))
    d.ellipse((90 * sx, 20 * sy, 150 * sx, 80 * sy), fill=(250, 250, 250))


def other_scene(d, sx, sy):
    d.rectangle((100 * sx, 5 * sy, 155 * sx, 85 * sy), fill=(10, 10, 10))
    d.line((0, 80 * sy, 160 * sx, 0), fill=(255, 255, 255), width=int(8 * sx))


def video(sid, source="Pixabay", preview=None):
    return VideoResult(source=source, source_id=sid, title=f"Ролик {sid}", description="", preview_url=preview,
                       duration=12.6, width=1920, height=1080, has_audio=None, page_url="",
                       files=[VideoFile("HD", "u")],
                       license=LicenseInfo("Pixabay Content License", status=licenses.ALLOWED))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = cat.Catalog(tmp_path / "c.sqlite", now=clock)
    yield s
    s.close()


# --- перцептивный хеш ---

def test_dhash_same_scene_resized_is_close_other_scene_far():
    a = similar.dhash(png(scene))
    b = similar.dhash(png(scene, size=(640, 360)))  # та же картинка в другом размере (другая площадка)
    c = similar.dhash(png(other_scene))
    assert len(a) == 16
    assert similar.distance(a, b) <= similar.DUPLICATE_DISTANCE
    assert similar.distance(a, c) > similar.DUPLICATE_DISTANCE


def test_find_duplicates_between_sources(store):
    for sid, source in (("1", "Pixabay"), ("2", "Coverr"), ("3", "Pixabay")):
        store.select(video(sid, source))
    e1, e2, e3 = sorted(store.entries(), key=lambda e: e.id)
    store.set_phash(e1.id, similar.dhash(png(scene)))
    store.set_phash(e2.id, similar.dhash(png(scene, size=(320, 180))))
    store.set_phash(e3.id, similar.dhash(png(other_scene)))
    pairs = similar.find_duplicates(store.entries())
    assert [(a.result.source_id, b.result.source_id) for a, b, _ in pairs] in ([("2", "1")], [("1", "2")])


class PreviewSession:
    def __init__(self, images):
        self.images = images

    def get(self, url, headers=None, timeout=None):
        if url not in self.images:
            return FakeResponse(404)
        r = FakeResponse(200)
        r.content = self.images[url]
        return r


def test_hash_previews_only_missing(store):
    store.select(video("1", preview="https://p/1.jpg"))
    store.select(video("2", preview="https://p/broken.jpg"))
    store.select(video("3"))  # без превью
    session = PreviewSession({"https://p/1.jpg": png(scene), "https://p/broken.jpg": b"not an image"})
    assert hash_previews(store, session) == 1
    assert hash_previews(store, session) == 0  # повторно не считаются
    assert store.find(("Pixabay", "1")).phash


# --- проверка ссылок ---

class Checker:
    name = "Pixabay"

    def __init__(self, gone=(), broken=()):
        self.gone, self.broken = set(gone), set(broken)

    def is_configured(self):
        return True

    def get_details(self, source_id):
        if source_id in self.gone:
            raise NotFoundError(self.name, "удалён")
        if source_id in self.broken:
            raise SourceError(self.name, "нет связи")
        return video(source_id)


def test_check_links_marks_only_confirmed_removals(store, clock, tmp_path):
    for sid in "1234":
        store.select(video(sid), "lam_pouch")
    store.select(video("9", source="Coverr"), "lam_pouch")  # площадка без проверки — пропуск
    counts = check_links(store, [Checker(gone={"2"}, broken={"3"})])
    assert counts == {"checked": 3, "removed": 1, "skipped": 2, "restored": 0}
    by_sid = {e.result.source_id: e for e in store.entries()}
    assert not by_sid["2"].available and by_sid["2"].link_checked_at == clock.t
    assert by_sid["3"].available and by_sid["3"].link_checked_at is None  # сбой сети — не трогаем
    # Удалённый и не скачанный ролик не засчитывается, скачанный — засчитывается.
    assert store.coverage() == {"lam_pouch": 4}
    store.set_downloaded(by_sid["2"].id, str(tmp_path / "нет-файла.mp4"))
    assert store.coverage() == {"lam_pouch": 4}  # путь есть, файла нет — не засчитывается
    (tmp_path / "2.mp4").write_bytes(b"x")
    store.set_downloaded(by_sid["2"].id, str(tmp_path / "2.mp4"))
    assert store.coverage() == {"lam_pouch": 5}
    counts = check_links(store, [Checker()])
    assert counts["restored"] == 1


def test_link_check_schedule(store, clock):
    assert link_check_due(store, 7, now=clock)
    store.set_meta("last_link_check_at", str(clock.t))
    assert not link_check_due(store, 7, now=clock)
    clock.t += 7 * 86400
    assert link_check_due(store, 7, now=clock)
    assert not link_check_due(store, 0, now=clock)  # 0 — проверка выключена


# --- поля этапа 8 в старом каталоге ---

def test_old_catalog_gets_new_columns(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.executescript("""CREATE TABLE videos (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
        title TEXT, description TEXT, preview_url TEXT, duration REAL, width INTEGER, height INTEGER, has_audio INTEGER,
        page_url TEXT, author TEXT, author_url TEXT, license_name TEXT, license_url TEXT, license_status TEXT,
        license_note TEXT, files TEXT, status TEXT NOT NULL DEFAULT '', license_confirmed INTEGER NOT NULL DEFAULT 0,
        comment TEXT NOT NULL DEFAULT '', downloaded_path TEXT NOT NULL DEFAULT '', added_at REAL, checked_at REAL,
        updated_at REAL, UNIQUE (source, source_id));
        INSERT INTO videos (source, source_id, title, status) VALUES ('Pixabay', '1', 'старый', 'выбрано');""")
    db.commit()
    db.close()
    store = cat.Catalog(tmp_path / "old.sqlite")
    e = store.find(("Pixabay", "1"))
    assert e.result.title == "старый" and e.available and e.phash == "" and e.loop is None
    store.set_loop(e.id, True)
    store.set_loop_hint(e.id, False)
    e = store.get(e.id)
    assert e.loop is True and e.loop_hint is False
    store.close()


# --- плейлист ---

def test_m3u_relative_paths_only_existing(store, tmp_path):
    folder = tmp_path / "Видео" / "Ламинация"
    folder.mkdir(parents=True)
    (folder / "1_lam.mp4").write_bytes(b"x")
    a = store.select(video("1"))
    store.set_downloaded(a, str(folder / "1_lam.mp4"))
    b = store.select(video("2"))
    store.set_downloaded(b, str(folder / "нет.mp4"))  # файл удалили
    store.select(video("3"))  # не скачан
    playlist = tmp_path / "Видео" / "плейлист.m3u8"
    assert write_m3u(playlist, sorted(store.entries(), key=lambda e: e.id)) == 1
    assert playlist.read_text(encoding="utf-8").splitlines() == ["#EXTM3U", "#EXTINF:13,Ролик 1", "Ламинация/1_lam.mp4"]


# --- подсказка зацикливания ---

FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(not FFMPEG, reason="нет FFmpeg")
def test_loop_hint(tmp_path):
    from app.downloader import loop_hint
    still = tmp_path / "still.mp4"  # один и тот же кадр от начала до конца — зацикливается
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x180:d=2", "-vf",
                    "drawbox=x=40:y=40:w=80:h=80:color=black:t=fill", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(still)], check=True)
    moving = tmp_path / "moving.mp4"  # в начале светлая левая половина, в конце — правая
    boxes = ("drawbox=x=0:y=0:w=160:h=180:color=white:t=fill:enable='lt(t,1)',"
             "drawbox=x=160:y=0:w=160:h=180:color=white:t=fill:enable='gte(t,2)'")
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=3", "-vf", boxes,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(moving)], check=True)
    assert loop_hint(still, Path(FFMPEG)) is True
    assert loop_hint(moving, Path(FFMPEG)) is False
    assert loop_hint(tmp_path / "нет.mp4", Path(FFMPEG)) is None


def test_search_history(store, clock):
    store.record_search("ламинация", [("P", "1")])
    clock.t += 10
    store.record_search("биговка", [])
    clock.t += 10
    store.record_search("ламинация", [("P", "1"), ("P", "2")])
    h = store.search_history()
    assert [x["query"] for x in h] == ["ламинация", "биговка"]
    assert (h[0]["times"], h[0]["found"], h[0]["new"]) == (2, 2, 1)


def test_user_dictionary_overrides_builtin(data_dir):
    import json

    from app import paths
    from app.query import Dictionary
    assert Dictionary.load().translate("пенокартон") == "foam board"
    paths.terms_file().write_text(json.dumps({"stopwords": [], "terms": {"пенокартон": "foamboard"}},
                                             ensure_ascii=False), encoding="utf-8")
    assert Dictionary.load().translate("пенокартон") == "foamboard"


def test_avif_thumbnail_loads():
    """Coverr отдаёт превью в AVIF, который Qt не читает: плитка должна получить картинку через Pillow."""
    import io

    from PIL import Image, features
    if not features.check("avif"):
        pytest.skip("Pillow без AVIF")
    from app.ui.main_window import load_image
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (99, 46, 230)).save(buf, "AVIF")
    image = load_image(buf.getvalue())
    assert (image.width(), image.height()) == (64, 36)
    assert load_image(b"not an image").isNull()


def test_frame_times():
    from app.frames import frame_times
    assert frame_times(100) == [10.0, 50.0, 90.0]
    assert frame_times(None) == [1.0, 5.0, 10.0]


@pytest.mark.skipif(not FFMPEG, reason="нет FFmpeg")
def test_frames_from_video(tmp_path):
    """Три кадра из ролика (начало, середина, конец) в PNG; ролик короче — лишний кадр пропускается."""
    from pathlib import Path

    from PIL import Image

    from app.frames import extract_frames
    src = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10", "-t", "4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    frames = extract_frames(str(src), 4.0, Path(FFMPEG))
    assert [at for at, _ in frames] == [0.4, 2.0, 3.6]
    with Image.open(io.BytesIO(frames[0][1])) as img:
        assert img.size == (320, 180)
    assert len(extract_frames(str(src), None, Path(FFMPEG))) == 1  # 1 с есть, 5 и 10 с — нет


@pytest.mark.skipif(sys.platform == "win32", reason="фальшивый FFmpeg — скрипт sh")
def test_frames_stop_kills_slow_ffmpeg(tmp_path, monkeypatch):
    """Закрытие окна во время получения кадров: FFmpeg прерывается, поток завершается до удаления окна."""
    import time

    from app.ui.frames_view import FramesStrip
    slow = tmp_path / "ffmpeg"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)
    monkeypatch.setattr("app.ui.frames_view.paths.ffmpeg_tool", lambda name: slow)
    strip = FramesStrip()
    strip.set_video(("Pixabay", "1"), "https://cdn/x.mp4", 12.0)
    strip.load()
    worker = strip.workers[0]
    time.sleep(0.3)
    started = time.monotonic()
    strip.stop()
    assert worker.isFinished() and time.monotonic() - started < 3


def test_settings_dialog_fits_small_screen(tmp_path, monkeypatch):
    """Окно настроек на экране высотой 768: форма прокручивается, кнопки остаются видны."""
    from PyQt6.QtWidgets import QScrollArea

    from app.settings import Settings
    from app.ui.settings_dialog import SettingsDialog
    dialog = SettingsDialog(Settings(), None)
    assert dialog.findChild(QScrollArea) is not None
    assert dialog.minimumSizeHint().height() < 700


def test_settings_dialog_width_fits_screen():
    """Вся форма видна по ширине: окно не шире экрана, и форма не шире видимой области."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QScrollArea

    from app.settings import Settings
    from app.ui.settings_dialog import SettingsDialog
    dialog = SettingsDialog(Settings(), None)
    scroll = dialog.findChild(QScrollArea)
    room = dialog.width() - scroll.verticalScrollBar().sizeHint().width() - 24
    screen = dialog.screen().availableGeometry().width()
    assert dialog.width() <= screen
    # Форма помещается, а на совсем узком экране (или крупном масштабе) её можно прокрутить вбок.
    assert scroll.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    if screen >= 1000:
        assert scroll.widget().minimumSizeHint().width() <= room
