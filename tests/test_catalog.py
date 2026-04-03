from __future__ import annotations

from pathlib import Path

from gamebooks_client.catalog import SqlDumpCatalog, SqliteCatalog, compare_dump_to_catalog, import_dump_to_sqlite


def test_catalog_reads_books_from_dump(tmp_path: Path) -> None:
    dump_path = tmp_path / "sample.sql"
    dump_path.write_text(
        "\n".join(
            [
                "INSERT INTO `File_Types` VALUES (1,'Article'),(4,'Play Aid');",
                "INSERT INTO `Material_Types` VALUES (1,'Gamebook','Gamebooks',1,NULL);",
                "INSERT INTO `Items` VALUES (1,'Alpha Book',NULL,'Thanks note',1),(2,'Beta Book','Errata',NULL,1);",
                "INSERT INTO `Items_Descriptions` VALUES (1,'User','Alpha description'),(2,'User','Beta description');",
                "INSERT INTO `People` VALUES (1,'Jane','Doe','',NULL,NULL);",
                "INSERT INTO `Roles` VALUES (1,'Author',NULL,NULL);",
                "INSERT INTO `Items_AltTitles` VALUES (1,'Alpha Alt',NULL,1);",
                "INSERT INTO `Items_Creators` VALUES (1,1,1,1);",
                "INSERT INTO `Files` VALUES (100,'Alpha Cover','/gallery/alpha.jpg','Cover image',4),(101,'Alpha Notes','/docs/alpha.pdf','Notes',1);",
                "INSERT INTO `Items_Files` VALUES (1,100),(1,101);",
                "INSERT INTO `Series` VALUES (10,'Starter Series','Series description',1);",
                "INSERT INTO `Series_AltTitles` VALUES (10,'Starter Saga',NULL,1);",
                "INSERT INTO `Series_Files` VALUES (10,100);",
                "INSERT INTO `Series_Bibliography` VALUES (10,1),(10,2);",
            ]
        ),
        encoding="utf-8",
    )

    catalog = SqlDumpCatalog(dump_path)

    books = catalog.list_books(limit=10)
    assert [book.title for book in books] == ["Alpha Book", "Beta Book"]
    assert books[0].material_type_name == "Gamebook"
    assert books[0].description == "Alpha description"
    assert books[0].alt_titles == ["Alpha Alt"]
    assert books[0].creators[0].name == "Jane Doe"
    assert books[1].errata == "Errata"
    files = catalog.get_book_files(1)
    assert [file.file_id for file in files] == [100, 101]
    assert [file.file_type_name for file in files] == ["Play Aid", "Article"]
    assert files[0].url == "https://gamebooks.org/gallery/alpha.jpg"
    assert [file.file_id for file in catalog.get_book_files(1, images_only=True)] == [100]
    series = catalog.get_series(10)
    assert series is not None
    assert series.alt_titles == ["Starter Saga"]
    assert [file.file_id for file in series.files] == [100]


def test_catalog_search_and_series_lookup(tmp_path: Path) -> None:
    dump_path = tmp_path / "sample.sql"
    dump_path.write_text(
        "\n".join(
            [
                "INSERT INTO `Items` VALUES (10,'Skystalker',NULL,NULL,1),(11,'Star Crystal, The',NULL,NULL,1),(12,'Robot World',NULL,NULL,1);",
                "INSERT INTO `Series` VALUES (5,'Falcon',NULL,1);",
                "INSERT INTO `Series_AltTitles` VALUES (5,'Falcon Saga',NULL,1);",
                "INSERT INTO `Series_Bibliography` VALUES (5,10),(5,11);",
            ]
        ),
        encoding="utf-8",
    )

    catalog = SqlDumpCatalog(dump_path)

    search_results = catalog.search_books("star", limit=10)
    assert [book.item_id for book in search_results] == [11]

    series = catalog.get_series(5)
    assert series is not None
    assert series.title == "Falcon"
    assert [series.series_id for series in catalog.search_series("saga", limit=10)] == [5]

    entries = catalog.get_series_books(5)
    assert [(entry.item_id, entry.title) for entry in entries] == [(10, "Skystalker"), (11, "Star Crystal, The")]


def test_catalog_works_with_repo_dump() -> None:
    dump_path = Path(__file__).resolve().parents[1] / "database" / "gamebooks.sql"
    catalog = SqlDumpCatalog(dump_path)

    first_books = catalog.list_books(limit=5)
    assert len(first_books) == 5
    assert first_books[0].item_id == 1
    assert first_books[0].title == "Dick Tracy"

    lone_wolf_like = catalog.search_books("lone wolf", limit=10)
    assert lone_wolf_like


def test_import_dump_to_sqlite_and_query(tmp_path: Path) -> None:
    dump_path = tmp_path / "sample.sql"
    sqlite_path = tmp_path / "catalog.sqlite"
    dump_path.write_text(
        "\n".join(
            [
                "INSERT INTO `File_Types` VALUES (4,'Play Aid');",
                "INSERT INTO `Material_Types` VALUES (1,'Gamebook','Gamebooks',1,NULL);",
                "INSERT INTO `Items` VALUES (1,'Alpha Book',NULL,'Thanks note',1),(2,'Beta Book','Errata',NULL,1);",
                "INSERT INTO `Items_Descriptions` VALUES (1,'User','Alpha description'),(2,'User','Beta description');",
                "INSERT INTO `People` VALUES (1,'Jane','Doe','',NULL,NULL);",
                "INSERT INTO `Roles` VALUES (1,'Author',NULL,NULL);",
                "INSERT INTO `Items_AltTitles` VALUES (1,'Alpha Alt',NULL,1);",
                "INSERT INTO `Items_Creators` VALUES (1,1,1,1);",
                "INSERT INTO `Files` VALUES (100,'Alpha Cover','/gallery/alpha.jpg','Cover image',4);",
                "INSERT INTO `Items_Files` VALUES (1,100);",
                "INSERT INTO `Series` VALUES (10,'Starter Series','Series description',1);",
                "INSERT INTO `Series_AltTitles` VALUES (10,'Starter Saga',NULL,1);",
                "INSERT INTO `Series_Files` VALUES (10,100);",
                "INSERT INTO `Series_Bibliography` VALUES (10,1),(10,2);",
            ]
        ),
        encoding="utf-8",
    )

    payload = import_dump_to_sqlite(dump_path, sqlite_path)
    assert payload["book_count"] == 2

    catalog = SqliteCatalog(sqlite_path)
    books = catalog.list_books(limit=10)
    assert [book.title for book in books] == ["Alpha Book", "Beta Book"]

    status = catalog.get_status()
    assert status["backend"] == "sqlite"
    assert status["book_count"] == 2
    assert status["file_count"] == 1
    assert status["alt_title_count"] == 1
    assert status["creator_count"] == 1
    assert status["series_alt_title_count"] == 1
    assert status["series_file_count"] == 1

    entries = catalog.get_series_books(10)
    assert [(entry.item_id, entry.title) for entry in entries] == [(1, "Alpha Book"), (2, "Beta Book")]
    files = catalog.get_book_files(1)
    assert [(file.file_id, file.path) for file in files] == [(100, "/gallery/alpha.jpg")]
    assert catalog.search_books("Jane", limit=10)[0].item_id == 1
    assert catalog.search_books("Alpha Alt", limit=10)[0].item_id == 1
    series = catalog.get_series(10)
    assert series is not None
    assert series.alt_titles == ["Starter Saga"]
    assert [file.file_id for file in series.files] == [100]
    assert catalog.search_series("saga", limit=10)[0].series_id == 10


def test_catalog_item_payload_includes_files(tmp_path: Path) -> None:
    dump_path = tmp_path / "sample.sql"
    dump_path.write_text(
        "\n".join(
            [
                "INSERT INTO `Items` VALUES (1,'Alpha Book',NULL,NULL,1);",
                "INSERT INTO `File_Types` VALUES (4,'Play Aid');",
                "INSERT INTO `Files` VALUES (100,'Alpha Cover','/gallery/alpha.jpg','Cover image',4);",
                "INSERT INTO `Items_Files` VALUES (1,100);",
            ]
        ),
        encoding="utf-8",
    )

    catalog = SqlDumpCatalog(dump_path)
    payload = catalog.get_book_payload(1)

    assert payload is not None
    assert payload["title"] == "Alpha Book"
    assert payload["files"][0]["path"] == "/gallery/alpha.jpg"
    assert payload["files"][0]["url"] == "https://gamebooks.org/gallery/alpha.jpg"


def test_compare_dump_to_catalog(tmp_path: Path) -> None:
    dump_path = tmp_path / "sample.sql"
    sqlite_path = tmp_path / "catalog.sqlite"
    dump_path.write_text("INSERT INTO `Items` VALUES (1,'Alpha Book',NULL,NULL,1);", encoding="utf-8")

    before_import = compare_dump_to_catalog(dump_path, sqlite_path)
    assert before_import["catalog_exists"] is False

    import_dump_to_sqlite(dump_path, sqlite_path)
    after_import = compare_dump_to_catalog(dump_path, sqlite_path)
    assert after_import["catalog_exists"] is True
    assert after_import["matches_imported_dump"] is True