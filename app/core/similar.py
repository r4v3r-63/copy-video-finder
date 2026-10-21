"""Похожие картинки: перцептивный хеш dHash (поиск дублей по превью, подсказка зацикливания).

Свой dHash на Pillow вместо библиотеки imagehash: та тянет numpy и scipy и раздувает сборку.
"""

import io

from PIL import Image

DUPLICATE_DISTANCE = 6  # из 64 бит: до 6 различий — почти наверняка тот же кадр
LOOP_DISTANCE = 10  # первый и последний кадр похожи — ролик можно зациклить без заметного скачка


def dhash(data: bytes, size: int = 8) -> str:
    """64-битный разностный хеш картинки в виде 16 шестнадцатеричных цифр."""
    with Image.open(io.BytesIO(data)) as img:
        small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        px = small.tobytes()  # по байту на пиксель в оттенках серого
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:0{size * size // 4}x}"


def distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def find_duplicates(entries, max_distance: int = DUPLICATE_DISTANCE) -> list[tuple]:
    """Пары роликов каталога с похожими превью: (ролик, ролик, расстояние), ближайшие первыми."""
    hashed = [e for e in entries if e.phash]
    pairs = []
    for i, a in enumerate(hashed):
        for b in hashed[i + 1:]:
            if a.result.key != b.result.key:
                d = distance(a.phash, b.phash)
                if d <= max_distance:
                    pairs.append((a, b, d))
    return sorted(pairs, key=lambda p: p[2])
