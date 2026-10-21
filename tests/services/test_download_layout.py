"""Готовые MP4 отдельно от состояния очереди, в том числе после отмены и ошибки."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from app.core.models import VideoFile
from app.services.downloader import Cancelled, DownloadJob, publish_file, run_job
from app.services.stock_download import existing_download, run_stock_job
from app.storage.download_files import metadata_file, working_file
from tests.fakes import ROOT, StreamResponse, StreamSession
from tests.services.test_downloader import FILES, video


def test_output_only_mp4_and_license_and_metadata_are_kept_elsewhere(tmp_path):
    folder = tmp_path / "Видео"
    job = DownloadJob(video(FILES), FILES[1], None, "Проверка", folder, flat_folder=True, check_content=False)
    file, _ = run_stock_job(job, StreamSession(StreamResponse(b"fixture")))
    assert list(folder.iterdir()) == [file] and file.suffix == ".mp4"
    assert metadata_file(file, ".txt").is_file() and metadata_file(file).is_file()
    assert existing_download(job) == file


def test_old_adjacent_metadata_moves_and_still_prevents_repeated_download(tmp_path):
    folder = tmp_path / "Видео"
    job = DownloadJob(video(FILES), FILES[1], None, "Проверка", folder, flat_folder=True, check_content=False)
    file, _ = run_stock_job(job, StreamSession(StreamResponse(b"fixture")))
    metadata_file(file).replace(file.with_suffix(".json"))
    metadata_file(file, ".txt").replace(file.with_suffix(".txt"))
    assert run_stock_job(job, StreamSession())[0] == file
    assert list(folder.iterdir()) == [file]
    assert json.loads(metadata_file(file).read_text(encoding="utf-8"))["license_confirmed"] is False


def test_cancelled_publish_preserves_old_video_and_leaves_no_temporary_file(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"new")
    folder = tmp_path / "Видео"
    folder.mkdir()
    destination = folder / "video.mp4"
    destination.write_bytes(b"old")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        publish_file(source, destination, cancel)
    assert destination.read_bytes() == b"old" and list(folder.iterdir()) == [destination]


def test_interrupted_download_keeps_resume_state_outside_video_folder(tmp_path):
    folder = tmp_path / "Видео"
    job = DownloadJob(video(FILES), FILES[1], None, "Проверка", folder, flat_folder=True, check_content=False)
    session = StreamSession(StreamResponse(b"abcdefgh", headers={"ETag": '"fixture-v1"'}))
    with pytest.raises(Cancelled):
        run_stock_job(job, session, progress=lambda *_: job.cancel.set())
    assert not list(folder.iterdir())
    partial = list(working_file(folder / "unused.mp4").parent.glob("*.part"))
    assert len(partial) == 1 and partial[0].read_bytes() == b"abcd"
    job.cancel.clear()
    resumed = StreamSession(StreamResponse(b"efgh", 206, headers={"Content-Range": "bytes 4-7/8"}))
    output, _ = run_stock_job(job, resumed)
    assert resumed.calls[0]["headers"]["Range"] == "bytes=4-"
    assert output.read_bytes() == b"abcdefgh" and list(folder.iterdir()) == [output]


def test_main_download_reuses_migrated_adjacent_license(tmp_path):
    job = DownloadJob(video(FILES), FILES[1], None, "Проверка", tmp_path / "Видео", check_content=False)
    output, _ = run_job(job, StreamSession(StreamResponse(b"fixture")))
    metadata_file(output, ".txt").replace(output.with_suffix(".txt"))
    assert run_job(job, StreamSession()) == (output, "уже был скачан")
    assert list(output.parent.iterdir()) == [output] and metadata_file(output, ".txt").is_file()


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="нет FFmpeg")
def test_mkv_is_rewrapped_as_real_mp4_without_reencoding(tmp_path):
    source = tmp_path / "source.mkv"
    ffmpeg, ffprobe = Path(shutil.which("ffmpeg")), Path(shutil.which("ffprobe"))
    subprocess.run([str(ffmpeg), "-v", "error", "-i", str(ROOT / "tests/fixtures/site_player/landscape.mp4"),
                    "-c", "copy", str(source)], check=True)
    file = VideoFile("HD", "https://example.org/video", 320, 180, mime="video/x-matroska", size=source.stat().st_size)
    folder = tmp_path / "Видео"
    job = DownloadJob(video([file]), file, None, "Проверка", folder,
                      convert=False, flat_folder=True, check_content=False)
    output, _ = run_stock_job(job, StreamSession(StreamResponse(source.read_bytes())), ffmpeg=ffmpeg, ffprobe=ffprobe)
    assert list(folder.iterdir()) == [output] and output.suffix == ".mp4"
    assert not working_file(folder / output.name).exists()
    info = subprocess.run([str(ffprobe), "-v", "error", "-show_format", "-of", "json", str(output)],
                          check=True, capture_output=True, text=True)
    assert "mp4" in json.loads(info.stdout)["format"]["format_name"]

    def packets(path):
        data = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_packets",
                               "-show_data_hash", "sha256", "-of", "json", str(path)],
                              check=True, capture_output=True, text=True)
        return [packet["data_hash"] for packet in json.loads(data.stdout)["packets"]]

    assert packets(source) == packets(output)
    assert existing_download(job) == output
