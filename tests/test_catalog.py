from __future__ import annotations

from gamebooks_client.catalog import CatalogStore
from gamebooks_client.models import GamebookItemDetails, GamebookSeriesDetails, GamebookSeriesItem


def test_catalog_store_upsert_item_and_series(tmp_path) -> None:
    db_path = tmp_path / "catalog.db"
    store = CatalogStore(str(db_path))

    item_details = GamebookItemDetails(
        title="Book Alpha",
        url="https://gamebooks.org/Item/100",
        show_url="https://gamebooks.org/Item/100",
        editions_url="https://gamebooks.org/Item/100/Editions",
        image_url=None,
        series_title="Series Prime",
        series_id=10,
        series_number=1,
    )
    store.upsert_item(100, item_details)

    series_details = GamebookSeriesDetails(
        title="Series Prime",
        url="https://gamebooks.org/Series/10",
        gamebooks=[
            GamebookSeriesItem(title="Book Alpha", url="https://gamebooks.org/Item/100", item_id=100),
            GamebookSeriesItem(title="Book Beta", url="https://gamebooks.org/Item/101", item_id=101),
        ],
        collections=[
            GamebookSeriesItem(title="Omnibus", url="https://gamebooks.org/Item/500", item_id=500),
        ],
    )
    store.upsert_series(10, series_details)

    assert store.count_items() == 1
    assert store.count_series() == 1

    gamebooks = store.list_series_items(10, kind="gamebook")
    collections = store.list_series_items(10, kind="collection")

    assert len(gamebooks) == 2
    assert gamebooks[0]["title"] == "Book Alpha"
    assert len(collections) == 1
    assert collections[0]["title"] == "Omnibus"


def test_catalog_store_crawl_state_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "catalog.db"
    store = CatalogStore(str(db_path))

    next_id, miss_streak = store.load_state("items", default_next_id=1)
    assert next_id == 1
    assert miss_streak == 0

    store.save_state("items", next_id=250, miss_streak=12)
    next_id2, miss_streak2 = store.load_state("items", default_next_id=1)

    assert next_id2 == 250
    assert miss_streak2 == 12
