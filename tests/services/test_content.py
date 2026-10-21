"""Рисованные ролики исключаются до выбора и формирования очереди."""

import json
from dataclasses import replace

import pytest

from app.core.models import SearchFilters
from app.core.stock_link import ListingFilters, limit_downloads, parse_link
from app.services.search import rejection_reason
from app.sources.pixabay import parse_hit
from tests.fakes import fixture_text

CASES = json.loads(fixture_text("stock_link/drawn.json"))


@pytest.mark.parametrize("hit", CASES, ids=lambda hit: str(hit["id"]))
def test_drawn_metadata_is_excluded_from_both_searches(hit):
    result = parse_hit(hit)
    assert result.video_type == hit["type"]
    assert ListingFilters().allows(result) is not hit["excluded"]
    assert (rejection_reason(result, SearchFilters()) == "аниме или анимация") is hit["excluded"]
    # Другие источники могут передавать признаки только в описании.
    assert ListingFilters().allows(replace(result, title="Ролик", description=result.title)) is not hit["excluded"]


@pytest.mark.parametrize("requested", ["all", "animation", "film"])
def test_pixabay_requests_film_even_if_link_requests_animation(requested):
    link = parse_link(f"https://pixabay.com/videos/search/?video_type={requested}")
    assert link.params["video_type"] == "film"
    assert any("заменён" in note for note in link.warnings) is (requested != "film")


def test_drawn_results_do_not_consume_download_count():
    results = [parse_hit(hit) for hit in CASES]
    filtered = [result for result in results if ListingFilters().allows(result)]
    queue = limit_downloads(filtered, 2, batch_enabled=True)
    assert [result.source_id for result in queue] == ["310", "311"]
    assert parse_link("https://pixabay.com/videos/search/").params["video_type"] == "film"
