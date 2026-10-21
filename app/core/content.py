"""Исключение рисованных роликов по метаданным источника."""

import re

from app.core.models import VideoResult

DRAWN = re.compile(
    r"\b(?:anime|manga|cartoons?|animation|animated|аниме|манга|мульт\w*|анимаци\w*)\b",
    re.IGNORECASE,
)


def is_drawn(result: VideoResult) -> bool:
    """Тип API надёжнее тегов; отсутствие признаков не подтверждает реальную съёмку."""
    return result.video_type == "animation" or bool(DRAWN.search(f"{result.title} {result.description}"))
