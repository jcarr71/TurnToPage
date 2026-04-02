from gamebooks_client.collection import CollectionStore


def test_collection_store_set_get_list_and_remove(tmp_path) -> None:
    db_path = tmp_path / "collection.db"
    store = CollectionStore(str(db_path))

    store.set_status(item_id=101, status="have", title="Book One", url="https://gamebooks.org/Item/101")
    store.set_status(item_id=202, status="missing", title="Book Two", url="https://gamebooks.org/Item/202")

    one = store.get(101)
    assert one is not None
    assert one.status == "have"
    assert one.title == "Book One"

    all_entries = store.list_entries()
    assert len(all_entries) == 2

    missing_entries = store.list_entries("missing")
    assert len(missing_entries) == 1
    assert missing_entries[0].item_id == 202

    status_map = store.status_map([101, 202, 303])
    assert status_map == {101: "have", 202: "missing"}

    assert store.remove(101) is True
    assert store.get(101) is None
    assert store.remove(101) is False


def test_collection_store_preserves_title_when_status_updates_without_title(tmp_path) -> None:
    db_path = tmp_path / "collection.db"
    store = CollectionStore(str(db_path))

    store.set_status(item_id=555, status="missing", title="Known Title", url="https://gamebooks.org/Item/555")
    store.set_status(item_id=555, status="have")

    entry = store.get(555)
    assert entry is not None
    assert entry.status == "have"
    assert entry.title == "Known Title"
    assert entry.url == "https://gamebooks.org/Item/555"


def test_collection_store_summary_counts_and_recent_entries(tmp_path) -> None:
    db_path = tmp_path / "collection.db"
    store = CollectionStore(str(db_path))

    store.set_status(item_id=101, status="have", title="Alpha", url="https://gamebooks.org/Item/101")
    store.set_status(item_id=202, status="want", title="Beta", url="https://gamebooks.org/Item/202")
    store.set_status(item_id=303, status="missing", title="Gamma", url="https://gamebooks.org/Item/303")

    counts = store.summary_counts()
    assert counts == {"have": 1, "want": 1, "missing": 1, "total": 3}

    recent = store.recent_entries(limit=2)
    assert len(recent) == 2
    assert {entry.item_id for entry in recent}.issubset({101, 202, 303})


# ---------------------------------------------------------------------------
# Filter presets
# ---------------------------------------------------------------------------

def test_save_list_delete_preset(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    store.save_preset("Fantasy Filter", "dragon", "have", "title")
    store.save_preset("Want List", "", "want", "added")

    presets = store.list_presets()
    assert len(presets) == 2
    names = [p["name"] for p in presets]
    assert "Fantasy Filter" in names
    assert "Want List" in names

    # Verify field values
    ff = next(p for p in presets if p["name"] == "Fantasy Filter")
    assert ff["text_filter"] == "dragon"
    assert ff["status_filter"] == "have"
    assert ff["sort"] == "title"


def test_save_preset_upserts(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    store.save_preset("My Preset", "old text", "have", "title")
    store.save_preset("My Preset", "new text", "missing", "added")

    presets = store.list_presets()
    assert len(presets) == 1
    assert presets[0]["text_filter"] == "new text"
    assert presets[0]["status_filter"] == "missing"


def test_delete_preset(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    store.save_preset("Keep", "", "have", "title")
    store.save_preset("Remove", "", "missing", "title")

    store.delete_preset("Remove")

    names = [p["name"] for p in store.list_presets()]
    assert "Keep" in names
    assert "Remove" not in names


def test_delete_nonexistent_preset_is_no_op(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))
    store.delete_preset("does not exist")  # should not raise


# ---------------------------------------------------------------------------
# series_progress
# ---------------------------------------------------------------------------

def _add_series_entries(store: CollectionStore, series_id: int, series_title: str, statuses: list) -> None:
    for idx, status in enumerate(statuses, start=1):
        store.set_status(
            item_id=series_id * 100 + idx,
            status=status,
            title=f"{series_title} Book {idx}",
            url=f"https://gamebooks.org/Item/{series_id * 100 + idx}",
            series_id=series_id,
            series_title=series_title,
        )


def test_series_progress_returns_stats_for_tracked_series(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    # Series with 3 entries (≥2 required)
    _add_series_entries(store, 1, "Fighting Fantasy", ["have", "have", "missing"])

    results = store.series_progress()
    assert len(results) == 1
    row = results[0]
    assert row["series_id"] == 1
    assert row["series_title"] == "Fighting Fantasy"
    assert row["have"] == 2
    assert row["missing"] == 1
    assert row["total"] == 3
    assert row["pct"] == 66


def test_series_progress_excludes_series_with_fewer_than_2_entries(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    _add_series_entries(store, 1, "Lone Wolf", ["have"])  # only 1 entry

    results = store.series_progress()
    assert results == []


def test_series_progress_sorted_by_completion_desc(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    _add_series_entries(store, 1, "Series A", ["have", "missing"])          # 50%
    _add_series_entries(store, 2, "Series B", ["have", "have", "missing"])  # 66%

    results = store.series_progress()
    assert results[0]["series_id"] == 2  # Series B first (higher pct)
    assert results[1]["series_id"] == 1


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------

def test_suggestions_returns_started_incomplete_series(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    _add_series_entries(store, 10, "Grailquest", ["have", "missing", "missing"])

    results = store.suggestions()
    assert len(results) == 1
    row = results[0]
    assert row["series_id"] == 10
    assert row["have"] == 1
    assert row["need"] == 2


def test_suggestions_excludes_complete_series(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    _add_series_entries(store, 20, "Complete Series", ["have", "have"])

    results = store.suggestions()
    assert results == []


def test_suggestions_excludes_series_with_no_have(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    _add_series_entries(store, 30, "Unstarted Series", ["missing", "missing"])

    results = store.suggestions()
    assert results == []


def test_suggestions_sorted_fewest_needed_first(tmp_path) -> None:
    store = CollectionStore(str(tmp_path / "col.db"))

    _add_series_entries(store, 1, "Almost Done",  ["have", "have", "missing"])         # need=1
    _add_series_entries(store, 2, "Far From Done", ["have", "missing", "missing", "missing"])  # need=3

    results = store.suggestions()
    assert results[0]["series_id"] == 1  # Almost Done first
    assert results[1]["series_id"] == 2

