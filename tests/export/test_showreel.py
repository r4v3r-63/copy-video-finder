"""Шоурил для экрана: отбор роликов, склейка FFmpeg, список авторов."""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.models import LicenseInfo, VideoResult
from app.export import showreel
from app.storage import catalog as cat

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def video(i, status="Разрешено", name="Pixabay Content License", duration=10.0):
    return VideoResult(content_orientation="горизонтальное",
                       source="Pixabay", source_id=str(i), title=f"Ролик {i}", description="", preview_url="",
                       duration=duration, width=1920, height=1080, has_audio=None, page_url=f"https://pixabay.com/v/{i}",
                       author=f"Автор {i}", license=LicenseInfo(name, status=status))


def add(store, tmp_path, i, pid, match=cat.EXACT, confirmed=True, file=True, **kw):
    vid = store.select(video(i, **kw), pid, match)
    store.confirm_license(vid, confirmed)
    if file:
        path = tmp_path / f"{i}.mp4"
        path.write_bytes(b"x")
        store.set_downloaded(vid, str(path))
    return vid


def test_entries_suitable_and_ordered(tmp_path):
    store = cat.Catalog(tmp_path / "c.sqlite")
    a = add(store, tmp_path, 1, "lam_roll", cat.PARTIAL)
    b = add(store, tmp_path, 2, "lam_roll", cat.EXACT)
    c = add(store, tmp_path, 3, "guillotine")
    add(store, tmp_path, 4, "lam_roll", confirmed=False)  # лицензия не подтверждена
    add(store, tmp_path, 5, "lam_roll", file=False)  # не скачан
    add(store, tmp_path, 6, "lam_roll", status="Требует проверки", name="Royalty Free")
    rejected = add(store, tmp_path, 7, "lam_roll")
    store.set_status(rejected, cat.REJECTED)
    store.link(b, "guillotine", cat.EXACT)  # ролик двух услуг — один раз
    assert [e.id for e in showreel.showreel_entries(store, ["guillotine", "lam_roll"])] == [b, c, a]
    assert [e.id for e in showreel.showreel_entries(store, ["lam_roll"])] == [b, a]
    store.close()


def test_command_scales_fades_and_mutes():
    cmd = showreel.build_command([Path("a.mp4"), Path("b.mp4")], [8.0, 3.0], Path("out.mp4"), Path("ffmpeg"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in graph and "pad=1920:1080" in graph
    assert "fade=t=out:st=7.50" in graph and "fade=t=out:st=2.50" in graph
    assert graph.endswith("[v0][v1]concat=n=2:v=1:a=0[out]")
    assert "-an" in cmd and cmd.count("-t") == 2
    no_fade = showreel.build_command([Path("a.mp4")], [5.0], Path("o.mp4"), Path("ffmpeg"), fade=0)
    assert "fade" not in no_fade[no_fade.index("-filter_complex") + 1]


def test_credits_mention_attribution():
    store_entries = [cat.Entry(id=1, result=video(1)),
                     cat.Entry(id=2, result=video(2, status="Разрешено с атрибуцией", name="CC BY-SA 4.0"))]
    text = showreel.credits_text("Ламинация", store_entries)
    assert "Шоурил «Ламинация»" in text and "Автор 2" in text and "https://pixabay.com/v/2" in text
    assert "указать авторов" in text and "BY-SA" in text
    assert "указать авторов" not in showreel.credits_text("X", store_entries[:1])


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="нет FFmpeg")
def test_showreel_made(tmp_path):
    """Горизонтальный 4 с и вертикальный 1,5 с по 3 с → около 4,5 с, 1920×1080, без звука."""
    store = cat.Catalog(tmp_path / "c.sqlite")
    files = []
    for i, (size, seconds) in enumerate([("640x360", 4), ("360x640", 1.5), ("320x240", 0.5)]):
        vid = add(store, tmp_path, i, "lam_roll", file=False)
        path = tmp_path / f"src{i}.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25", "-f", "lavfi",
                        "-i", "sine=frequency=440", "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(path)], check=True)
        store.set_downloaded(vid, str(path))
        files.append(path)
    entries = showreel.showreel_entries(store, ["lam_roll"])
    shares = []
    dest = tmp_path / "reel.mp4"
    used = showreel.make_showreel(entries, dest, Path(FFMPEG), Path(FFPROBE), seconds=3, progress=shares.append)
    assert len(used) == 2  # ролик 0,5 с короче минимума — пропущен
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(dest)], capture_output=True, text=True).stdout
    import json
    info = json.loads(out)
    assert [s["codec_type"] for s in info["streams"]] == ["video"]
    assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (1920, 1080)
    assert 4.2 <= float(info["format"]["duration"]) <= 4.8
    assert shares and shares[-1] <= 1.0
    assert not list(tmp_path.glob("*.making.mp4"))
    store.close()


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="нет FFmpeg")
def test_showreel_cancelled(tmp_path):
    import threading
    store = cat.Catalog(tmp_path / "c.sqlite")
    vid = add(store, tmp_path, 1, "lam_roll", file=False)
    path = tmp_path / "src.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-t", "3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    store.set_downloaded(vid, str(path))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(showreel.Cancelled):
        showreel.make_showreel(showreel.showreel_entries(store, ["lam_roll"]), tmp_path / "r.mp4", Path(FFMPEG),
                               Path(FFPROBE), cancel=cancel)
    assert not list(tmp_path.glob("r*.mp4"))
    store.close()


def test_dialog_lists_ready_services(tmp_path):
    from app.core import processes
    from app.ui.dialogs.showreel_dialog import ShowreelDialog
    store = cat.Catalog(tmp_path / "c.sqlite")
    a = add(store, tmp_path, 1, "lam_roll")
    dialog = ShowreelDialog(store, processes.load_catalog())
    items = [dialog.scope.itemText(i) for i in range(dialog.scope.count())]
    assert items[0] == "Все услуги (1)" and any("Рулонное ламинирование" in t for t in items)
    assert [e.id for e in dialog.entries()] == [a] and dialog.ok.isEnabled()
    dialog.fade.setChecked(False)
    assert dialog.fade_seconds() == 0
    store.close()
