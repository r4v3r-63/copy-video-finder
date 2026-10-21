"""Ориентация полезного изображения, а не контейнера с чёрными полями."""

from io import BytesIO

from PIL import Image, UnidentifiedImageError

UNKNOWN = ""
LANDSCAPE = "горизонтальное"
PORTRAIT = "не горизонтальное"
UNVERIFIED_TEXT = "Горизонтальность содержимого не подтверждена"
CONFIRM_TEXT = "Содержимое горизонтальное — проверено"


def image_orientation(data: bytes) -> str:
    """Убираем почти чёрные поля; логотип в углу не должен расширять полезный кадр.

    Тёмный/пустой кадр не подтверждает ориентацию. Обложка даёт только повод
    отклонить результат; подтвердить весь ролик по одной картинке нельзя.
    """
    try:
        with Image.open(BytesIO(data)) as original:
            if original.width * original.height > 40_000_000:
                return UNKNOWN
            original.thumbnail((320, 320))
            image = original.convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return UNKNOWN
    width, height = image.size
    if min(width, height) < 16:
        return UNKNOWN
    pixels = image.load()
    # До 8 % светлых точек допускают небольшой логотип и шум сжатия в поле.
    columns = [x for x in range(width)
               if sum(max(pixels[x, y]) > 28 for y in range(height)) > height * 0.08]
    rows = [y for y in range(height)
            if sum(max(pixels[x, y]) > 28 for x in range(width)) > width * 0.08]
    if not columns or not rows:
        return UNKNOWN
    left, right = columns[0], columns[-1]
    top, bottom = rows[0], rows[-1]
    # Одинокий логотип отделён от изображения широким чёрным промежутком.
    groups = []
    for x in columns:
        if not groups or x - groups[-1][-1] > 2:
            groups.append([x])
        else:
            groups[-1].append(x)
    widest = max(groups, key=len)
    if len(widest) > len(columns) * 0.8:
        left, right = widest[0], widest[-1]
    useful_width, useful_height = right - left + 1, bottom - top + 1
    if useful_width < 16 or useful_height < 16:
        return UNKNOWN
    return LANDSCAPE if useful_width > useful_height * 1.1 else PORTRAIT


def frames_orientation(frames: list[tuple[float, bytes]]) -> str:
    """Три читаемых кадра подтверждают горизонтальность; одного вертикального достаточно для отказа."""
    states = [image_orientation(data) for _, data in frames]
    if PORTRAIT in states:
        return PORTRAIT
    return LANDSCAPE if len(states) >= 3 and all(s == LANDSCAPE for s in states) else UNKNOWN
