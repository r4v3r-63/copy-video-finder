import shutil
import subprocess
from datetime import datetime

import pytest

from app import licenses
from app.downloader import (
    DownloadError,
    DownloadJob,
    choose_file,
    extension,
    fetch,
    file_stem,
    run_job,
    safe_name,
    target_folder,
)
from app.processes import Process
from app.sources.base import LicenseInfo, VideoFile, VideoResult
from tests.fakes import StreamResponse, StreamSession

PROC = Process(id="lam_roll", name="Рулонная ламинация листовой продукции", category="Ламинация",
               phrases_ru=[], phrases_en=[])


def video(files, source="Pixabay", status=licenses.ALLOWED, lic_name="Pixabay Content License"):
    return VideoResult(source=source, source_id="125", title="laminator, office", description="",
                       preview_url=None, duration=12, width=1920, height=1080, has_audio=True,
                       page_url="https://pixabay.com/videos/id-125/", files=files, author="Anna",
                       author_url="https://pixabay.com/users/anna-1/",
                       license=LicenseInfo(lic_name, "https://lic", status=status))


FILES = [VideoFile("4K", "https://cdn/x_4k.mp4", 3840, 2160, "video/mp4"),
         VideoFile("Full HD", "https://cdn/x_fhd.mp4", 1920, 1080, "video/mp4"),
         VideoFile("HD", "https://cdn/x_hd.mp4", 1280, 720, "video/mp4")]


def test_choose_file_full_hd_by_default_4k_optional():
    assert choose_file(video(FILES)).quality == "Full HD"
    assert choose_file(video(FILES), allow_4k=True).quality == "4K"
    portrait = [VideoFile("Full HD", "u1", 1080, 1920), VideoFile("4K", "u2", 2160, 3840)]
    assert choose_file(video(portrait)).url == "u1"
    assert choose_file(video([VideoFile("4K", "only", 3840, 2160)])).url == "only"
    assert choose_file(video([])) is None


def test_choose_prefers_mp4_at_same_quality():
    files = [VideoFile("SD", "https://u/a.webm", 640, 480, "video/webm"),
             VideoFile("SD", "https://u/a.mp4", 640, 480, "video/mp4")]
    assert choose_file(video(files)).url.endswith(".mp4")


def test_names_and_folders(tmp_path):
    assert safe_name('Ш/Ф печать: "латекс"?') == "Ш Ф печать латекс"
    assert safe_name("  ...  ") == "без названия"
    assert target_folder(tmp_path, PROC, "") == tmp_path / "Ламинация" / "Рулонная ламинация листовой продукции"
    assert target_folder(tmp_path, None, "люверсы на баннер") == tmp_path / "Без услуги" / "люверсы на баннер"
    v = video(FILES, source="Wikimedia Commons")
    assert file_stem(PROC, v) == "lam_roll_wikimediacommons_125"
    assert file_stem(None, v) == "zapros_wikimediacommons_125"


def test_extension():
    assert extension(VideoFile("SD", "https://u/a.webm?x=1")) == ".webm"
    assert extension(VideoFile("SD", "https://u/File.ogg")) == ".ogv"
    assert extension(VideoFile("SD", "https://u/noext", mime="video/quicktime")) == ".mov"
    assert extension(VideoFile("SD", "https://u/noext")) == ".mp4"


def test_fetch_resumes_after_break(tmp_path):
    data = b"0123456789abcdef"
    session = StreamSession(StreamResponse(data, cut_after=8), StreamResponse(data[8:], 206))
    dest = tmp_path / "v.mp4"
    fetch("https://cdn/v.mp4", dest, session, pause=0)
    assert dest.read_bytes() == data
    assert "Range" not in session.calls[0]["headers"]
    assert session.calls[1]["headers"]["Range"] == "bytes=8-"
    assert not (tmp_path / "v.mp4.part").exists()


def test_fetch_restarts_if_server_ignores_range(tmp_path):
    data = b"abcdefgh"
    session = StreamSession(StreamResponse(data, cut_after=4), StreamResponse(data, 200))
    dest = tmp_path / "v.mp4"
    fetch("u", dest, session, pause=0)
    assert dest.read_bytes() == data


def test_fetch_errors(tmp_path):
    with pytest.raises(DownloadError, match="удалён"):
        fetch("u", tmp_path / "a.mp4", StreamSession(StreamResponse(b"", 404)), pause=0)
    session = StreamSession(*[StreamResponse(b"abcdefgh", cut_after=0) for _ in range(3)])
    with pytest.raises(DownloadError, match="Нет связи"):
        fetch("u", tmp_path / "b.mp4", session, pause=0)
    assert len(session.calls) == 3


def test_run_job_writes_video_and_license(tmp_path):
    v = video(FILES)
    job = DownloadJob(result=v, file=FILES[1], process=PROC, query_text="", base_dir=tmp_path)
    session = StreamSession(StreamResponse(b"videodata"))
    path, note = run_job(job, session, now=lambda: datetime(2026, 10, 19, 12, 0))
    assert path == tmp_path / "Ламинация" / "Рулонная ламинация листовой продукции" / "lam_roll_pixabay_125.mp4"
    assert path.read_bytes() == b"videodata"
    assert session.calls[0]["url"] == "https://cdn/x_fhd.mp4"  # только ссылка из API
    text = path.with_suffix(".txt").read_text(encoding="utf-8")
    assert "Лицензия: Pixabay Content License" in text
    assert "Условия лицензии: https://lic" in text
    assert "Страница ролика: https://pixabay.com/videos/id-125/" in text
    assert "Автор: Anna (https://pixabay.com/users/anna-1/)" in text
    assert "проверить права на музыку" in text
    assert "2026-10-19 12:00" in text
    assert "Атрибуция" not in text
    # Повторное скачивание не нужно.
    assert run_job(job, StreamSession()) == (path, "уже был скачан")


def test_attribution_in_license_file(tmp_path):
    v = video(FILES, source="Wikimedia Commons", status=licenses.ATTRIBUTION, lic_name="CC BY-SA 4.0")
    v.license.note = licenses.SHARE_ALIKE_NOTE
    job = DownloadJob(result=v, file=FILES[2], process=None, query_text="ламинатор", base_dir=tmp_path)
    path, _ = run_job(job, StreamSession(StreamResponse(b"x")))
    text = path.with_suffix(".txt").read_text(encoding="utf-8")
    assert "Атрибуция (показывать в титре): Anna — laminator, office — CC BY-SA 4.0 — https://pixabay.com/videos/id-125/" in text
    assert licenses.SHARE_ALIKE_NOTE in text


def test_webm_without_ffmpeg_kept(tmp_path):
    f = VideoFile("SD", "https://upload/x.webm", 640, 480, "video/webm")
    job = DownloadJob(result=video([f]), file=f, process=PROC, query_text="", base_dir=tmp_path)
    path, note = run_job(job, StreamSession(StreamResponse(b"webm")), ffmpeg=None)
    assert path.suffix == ".webm" and "FFmpeg не найден" in note


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="нет FFmpeg")
def test_webm_converted_to_mp4_and_checked(tmp_path):
    from pathlib import Path
    src = tmp_path / "src.webm"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "2",
                    "-c:v", "libvpx", str(src)], check=True)
    f = VideoFile("SD", "https://upload/x.webm", 320, 240, "video/webm")
    job = DownloadJob(result=video([f]), file=f, process=PROC, query_text="", base_dir=tmp_path / "out")
    path, note = run_job(job, StreamSession(StreamResponse(src.read_bytes())), ffmpeg=Path(FFMPEG),
                         ffprobe=Path(FFPROBE))
    assert path.suffix == ".mp4" and note == ""
    assert not path.with_suffix(".webm").exists()
    text = path.with_suffix(".txt").read_text(encoding="utf-8")
    assert "Проверка файла: 320×240, 2.0 с" in text


@pytest.mark.skipif(not FFPROBE, reason="нет FFmpeg")
def test_broken_file_reported(tmp_path):
    from pathlib import Path
    job = DownloadJob(result=video(FILES), file=FILES[2], process=PROC, query_text="", base_dir=tmp_path)
    _, note = run_job(job, StreamSession(StreamResponse(b"not a video")), ffprobe=Path(FFPROBE))
    assert "повреждён" in note


def test_short_429_waited_long_429_reported(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr("app.downloader.time.sleep", slept.append)
    session = StreamSession(StreamResponse(b"", 429, headers={"Retry-After": "10"}), StreamResponse(b"ok"))
    fetch("u", tmp_path / "a.mp4", session)
    assert (tmp_path / "a.mp4").read_bytes() == b"ok" and slept == [10.0]
    session = StreamSession(StreamResponse(b"", 429, headers={"Retry-After": "600"}))
    with pytest.raises(DownloadError, match="Повторите через 10 мин"):
        fetch("u", tmp_path / "b.mp4", session)
    assert len(session.calls) == 1


def test_file_name_starts_with_catalog_id(tmp_path):
    v = video(FILES)
    assert file_stem(PROC, v, 42) == "42_lam_roll_pixabay_125"
    job = DownloadJob(result=v, file=FILES[1], process=PROC, query_text="", base_dir=tmp_path, catalog_id=42)
    path, _ = run_job(job, StreamSession(StreamResponse(b"x")))
    assert path.name == "42_lam_roll_pixabay_125.mp4"
    assert "Номер в каталоге: 42" in path.with_suffix(".txt").read_text(encoding="utf-8")


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="нет FFmpeg")
def test_prepared_for_screens_trimmed_and_muted(tmp_path):
    """Подготовка к показу: MP4 со звуком 4 с → первые 2 с без звука, пометка в файле лицензии."""
    from pathlib import Path
    src = tmp_path / "src.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    f = VideoFile("SD", "https://cdn/x.mp4", 320, 240, "video/mp4")
    v = video([f])
    v.has_audio = True
    job = DownloadJob(result=v, file=f, process=PROC, query_text="", base_dir=tmp_path / "out", trim=2, mute=True)
    path, note = run_job(job, StreamSession(StreamResponse(src.read_bytes())), ffmpeg=Path(FFMPEG),
                         ffprobe=Path(FFPROBE))
    assert path.suffix == ".mp4" and note == "" and job.prepared
    assert not list(path.parent.glob("*.download*"))  # исходник удалён
    streams = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    assert "audio" not in streams
    duration = float([line for line in streams.splitlines() if line.strip() and line[0].isdigit()][-1])
    assert 1.5 <= duration <= 2.5
    text = path.with_suffix(".txt").read_text(encoding="utf-8")
    assert "Подготовлен к показу: обрезан до 2 с, звук убран." in text
    assert "проверить права на музыку" not in text


def test_no_preparation_keeps_mp4_as_is(tmp_path):
    job = DownloadJob(result=video(FILES), file=FILES[1], process=PROC, query_text="", base_dir=tmp_path)
    path, _ = run_job(job, StreamSession(StreamResponse(b"videodata")))
    assert path.read_bytes() == b"videodata" and not job.prepared
