from pathlib import Path
from typing import Any

from gamebooks_client.api import GamebooksApi
from gamebooks_client.models import GamebookBook


class FakeResponse:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self.content = content.encode("utf-8")
        self.status_code = status_code


class FakeSession:
    def __init__(self, mapping: dict[str, FakeResponse]) -> None:
        self.mapping = mapping

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        if "/Search" in url:
            return self.mapping["search"]
        if "/Item/456/Editions" in url:
            return self.mapping["item"]
        if "/Series/789" in url:
            return self.mapping["series"]
        return FakeResponse("not found", 404)

    def close(self) -> None:
        return None


def _read_fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_search_books_parses_series_and_item_results() -> None:
    session = FakeSession(
        {
            "search": FakeResponse(_read_fixture("search_results.html")),
            "item": FakeResponse("", 404),
            "series": FakeResponse("", 404),
        }
    )
    api = GamebooksApi(session=session)

    results = api.search_books("example")

    assert results
    series = next((r for r in results if r.is_series), None)
    item = next((r for r in results if r.is_item), None)

    assert series is not None
    assert series.title == "Sample Series Title"
    assert series.item_id == 123

    assert item is not None
    assert item.title == "Sample Item Title"
    assert item.item_id == 456


def test_fetch_item_details_extracts_title_images_and_metadata() -> None:
    session = FakeSession(
        {
            "search": FakeResponse("", 404),
            "item": FakeResponse(_read_fixture("item_details.html")),
            "series": FakeResponse("", 404),
        }
    )
    api = GamebooksApi(session=session)
    book = GamebookBook(title="Old Title", url="https://gamebooks.org/Item/456", item_id=456)

    details = api.fetch_item_details(book)

    assert details.title == "The Example Book"
    assert details.image_urls
    assert details.image_urls[0] == "https://gamebooks.org/gallery/image1"
    assert details.metadata["Authors"] == "Jane Example"
    assert details.description == "This is an example description."


def test_fetch_item_details_extracts_structured_fields_and_editions() -> None:
    session = FakeSession(
        {
            "search": FakeResponse("", 404),
            "item": FakeResponse(_read_fixture("item_details_rich.html")),
            "series": FakeResponse("", 404),
        }
    )
    api = GamebooksApi(session=session)
    book = GamebookBook(title="Old Title", url="https://gamebooks.org/Item/456", item_id=456)

    details = api.fetch_item_details(book)

    assert details.series_title == "Great Series"
    assert details.series_id == 789
    assert details.series_number == 4
    assert details.authors == ["Jane Example", "John Writer"]
    assert details.illustrators == ["Alex Artist"]
    assert details.pub_date == "1992"
    assert details.isbns == ["0-1234-5678-9"]
    assert details.length_pages == 220
    assert details.number_of_endings == 7
    assert details.related_links[0].label == "Reviews"
    assert details.related_links[0].url == "https://gamebooks.org/Item/456/Reviews"
    assert details.editions_count >= 1
    assert details.editions[0].title == "First Edition"
    assert details.editions[0].cover_image_url == "https://gamebooks.org/gallery/edition-cover"


def test_fetch_series_details_extracts_gamebooks_and_collections() -> None:
    session = FakeSession(
        {
            "search": FakeResponse("", 404),
            "item": FakeResponse("", 404),
            "series": FakeResponse(_read_fixture("series_details.html")),
        }
    )
    api = GamebooksApi(session=session)

    details = api.fetch_series_details("https://gamebooks.org/Series/789")

    assert details.title == "Great Series"
    assert details.total_count == 2
    assert details.collection_count == 1
    assert details.item_ids == [111, 222]
    assert details.gamebooks[0].title == "Book One"
    assert details.collections[0].title == "Collector Volume"
