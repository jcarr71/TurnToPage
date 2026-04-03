from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import shutil

from .api import GamebooksApi
from .catalog import compare_dump_to_catalog, import_dump_to_sqlite, open_catalog
from .collection import CollectionStore, VALID_STATUSES
from .models import GamebookBook


DEFAULT_DUMP_PATH = Path(__file__).resolve().parents[1] / "database" / "gamebooks.sql"
DEFAULT_SQLITE_PATH = Path(__file__).resolve().parents[1] / "database" / "gamebooks.sqlite"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TurnToPage - A Gamebook Collectors Tool")
    parser.add_argument(
        "--db-path",
        default="turntopage.db",
        help="Path to local SQLite collection database (default: turntopage.db)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search books by title")
    search_parser.add_argument("query", help="Search text")

    item_parser = subparsers.add_parser("item", help="Fetch item details by numeric ID")
    item_parser.add_argument("item_id", type=int, help="Item ID from gamebooks.org")
    item_parser.add_argument("--title", default="", help="Optional fallback title")

    series_parser = subparsers.add_parser("series", help="Fetch series details by ID")
    series_parser.add_argument("series_id", type=int, help="Series ID from gamebooks.org")

    mark_parser = subparsers.add_parser("mark", help="Mark an item as have/missing/want")
    mark_parser.add_argument("item_id", type=int, help="Item ID from gamebooks.org")
    mark_parser.add_argument("status", choices=sorted(VALID_STATUSES), help="Collection status")
    mark_parser.add_argument("--title", default="", help="Optional local title override")
    mark_parser.add_argument("--url", default="", help="Optional local URL override")

    unmark_parser = subparsers.add_parser("unmark", help="Remove item from local collection tracking")
    unmark_parser.add_argument("item_id", type=int, help="Item ID from gamebooks.org")

    collection_parser = subparsers.add_parser("collection", help="List locally tracked collection entries")
    collection_parser.add_argument(
        "--status",
        choices=sorted(VALID_STATUSES),
        help="Filter by status",
    )

    series_status_parser = subparsers.add_parser(
        "series-status",
        help="Show owned/missing status for each book in a series",
    )
    series_status_parser.add_argument("series_id", type=int, help="Series ID from gamebooks.org")

    catalog_books_parser = subparsers.add_parser("catalog-books", help="List books from the local SQL dump")
    catalog_books_parser.add_argument("--limit", type=int, default=20, help="Number of books to return")
    catalog_books_parser.add_argument("--offset", type=int, default=0, help="Number of books to skip")
    catalog_books_parser.add_argument("--database-path", default=str(DEFAULT_DUMP_PATH), help="Path to gamebooks.sql")
    catalog_books_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")

    catalog_search_parser = subparsers.add_parser("catalog-search", help="Search books in the local SQL dump")
    catalog_search_parser.add_argument("query", help="Search text")
    catalog_search_parser.add_argument("--limit", type=int, default=20, help="Number of books to return")
    catalog_search_parser.add_argument("--database-path", default=str(DEFAULT_DUMP_PATH), help="Path to gamebooks.sql")
    catalog_search_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")

    catalog_item_parser = subparsers.add_parser("catalog-item", help="Fetch a book from the local SQL dump by ID")
    catalog_item_parser.add_argument("item_id", type=int, help="Item ID from the SQL dump")
    catalog_item_parser.add_argument("--database-path", default=str(DEFAULT_DUMP_PATH), help="Path to gamebooks.sql")
    catalog_item_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")

    catalog_files_parser = subparsers.add_parser("catalog-files", help="Fetch linked files for a book from the local catalog")
    catalog_files_parser.add_argument("item_id", type=int, help="Item ID from the local catalog")
    catalog_files_parser.add_argument("--images-only", action="store_true", help="Only return image file links")
    catalog_files_parser.add_argument("--database-path", default=str(DEFAULT_DUMP_PATH), help="Path to gamebooks.sql")
    catalog_files_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")

    catalog_series_parser = subparsers.add_parser("catalog-series", help="Fetch series and its books from the local SQL dump")
    catalog_series_parser.add_argument("series_id", type=int, help="Series ID from the SQL dump")
    catalog_series_parser.add_argument("--limit", type=int, default=20, help="Number of books to return")
    catalog_series_parser.add_argument("--database-path", default=str(DEFAULT_DUMP_PATH), help="Path to gamebooks.sql")
    catalog_series_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")

    catalog_series_search_parser = subparsers.add_parser(
        "catalog-series-search",
        help="Search series titles and alternate titles from the local catalog",
    )
    catalog_series_search_parser.add_argument("query", help="Search text")
    catalog_series_search_parser.add_argument("--limit", type=int, default=20, help="Number of series to return")
    catalog_series_search_parser.add_argument("--database-path", default=str(DEFAULT_DUMP_PATH), help="Path to gamebooks.sql")
    catalog_series_search_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")

    catalog_import_parser = subparsers.add_parser("catalog-import", help="Import a SQL dump into the local SQLite catalog")
    catalog_import_parser.add_argument("--dump-path", default=str(DEFAULT_DUMP_PATH), help="Path to the source SQL dump")
    catalog_import_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")
    catalog_import_parser.add_argument(
        "--replace-master-dump",
        action="store_true",
        help="Copy the provided dump over database/gamebooks.sql before importing",
    )

    catalog_status_parser = subparsers.add_parser("catalog-status", help="Show whether the app is using the SQL dump or SQLite catalog")
    catalog_status_parser.add_argument("--database-path", default=str(DEFAULT_DUMP_PATH), help="Path to gamebooks.sql")
    catalog_status_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")

    catalog_check_parser = subparsers.add_parser("catalog-check-dump", help="Compare an incoming dump to the currently imported catalog")
    catalog_check_parser.add_argument("--dump-path", default=str(DEFAULT_DUMP_PATH), help="Path to the source SQL dump to compare")
    catalog_check_parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Path to gamebooks.sqlite")

    return parser


def _open_catalog(args):
    return open_catalog(sqlite_path=args.sqlite_path, dump_path=args.database_path)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    store = CollectionStore(args.db_path)

    api = GamebooksApi()
    try:
        if args.command == "search":
            results = api.search_books(args.query)
            print(json.dumps([asdict(result) for result in results], indent=2))
            return

        if args.command == "item":
            book = GamebookBook(
                title=args.title or f"Item {args.item_id}",
                url=f"https://gamebooks.org/Item/{args.item_id}",
                item_id=args.item_id,
            )
            details = api.fetch_item_details(book)
            print(json.dumps(asdict(details), indent=2))
            return

        if args.command == "series":
            details = api.fetch_series_details(f"https://gamebooks.org/Series/{args.series_id}")
            print(json.dumps(asdict(details), indent=2))
            return

        if args.command == "mark":
            title = args.title or f"Item {args.item_id}"
            url = args.url or f"https://gamebooks.org/Item/{args.item_id}"
            store.set_status(item_id=args.item_id, status=args.status, title=title, url=url)
            entry = store.get(args.item_id)
            print(json.dumps(asdict(entry) if entry else {}, indent=2))
            return

        if args.command == "unmark":
            removed = store.remove(args.item_id)
            print(json.dumps({"item_id": args.item_id, "removed": removed}, indent=2))
            return

        if args.command == "collection":
            entries = store.list_entries(args.status)
            print(json.dumps([asdict(entry) for entry in entries], indent=2))
            return

        if args.command == "series-status":
            details = api.fetch_series_details(f"https://gamebooks.org/Series/{args.series_id}")
            status_by_id = store.status_map(details.item_ids)

            books = []
            have_count = 0
            missing_count = 0
            unknown_count = 0

            for item in details.gamebooks:
                status = "unknown"
                if item.item_id is not None and item.item_id in status_by_id:
                    status = status_by_id[item.item_id]

                if status == "have":
                    have_count += 1
                elif status in {"missing", "want"}:
                    missing_count += 1
                else:
                    unknown_count += 1

                books.append(
                    {
                        "title": item.title,
                        "item_id": item.item_id,
                        "url": item.url,
                        "status": status,
                    }
                )

            response = {
                "series_title": details.title,
                "series_url": details.url,
                "total_gamebooks": details.total_count,
                "have_count": have_count,
                "missing_count": missing_count,
                "unknown_count": unknown_count,
                "books": books,
            }
            print(json.dumps(response, indent=2))
            return

        if args.command == "catalog-books":
            catalog = _open_catalog(args)
            print(json.dumps([asdict(book) for book in catalog.list_books(limit=args.limit, offset=args.offset)], indent=2))
            return

        if args.command == "catalog-search":
            catalog = _open_catalog(args)
            print(json.dumps([asdict(book) for book in catalog.search_books(args.query, limit=args.limit)], indent=2))
            return

        if args.command == "catalog-item":
            catalog = _open_catalog(args)
            print(json.dumps(catalog.get_book_payload(args.item_id), indent=2))
            return

        if args.command == "catalog-files":
            catalog = _open_catalog(args)
            print(json.dumps([asdict(file) for file in catalog.get_book_files(args.item_id, images_only=args.images_only)], indent=2))
            return

        if args.command == "catalog-series":
            catalog = _open_catalog(args)
            series = catalog.get_series(args.series_id)
            payload = {
                "series": asdict(series) if series is not None else None,
                "books": [asdict(entry) for entry in catalog.get_series_books(args.series_id, limit=args.limit)],
            }
            print(json.dumps(payload, indent=2))
            return

        if args.command == "catalog-series-search":
            catalog = _open_catalog(args)
            print(
                json.dumps(
                    [
                        {
                            "series_id": series.series_id,
                            "title": series.title,
                            "alt_titles": series.alt_titles,
                        }
                        for series in catalog.search_series(args.query, limit=args.limit)
                    ],
                    indent=2,
                )
            )
            return

        if args.command == "catalog-import":
            source_dump = Path(args.dump_path)
            if args.replace_master_dump:
                DEFAULT_DUMP_PATH.parent.mkdir(parents=True, exist_ok=True)
                if source_dump.resolve() != DEFAULT_DUMP_PATH.resolve():
                    shutil.copy2(source_dump, DEFAULT_DUMP_PATH)
                source_dump = DEFAULT_DUMP_PATH
            payload = import_dump_to_sqlite(source_dump, args.sqlite_path)
            print(json.dumps(payload, indent=2))
            return

        if args.command == "catalog-status":
            catalog = _open_catalog(args)
            print(json.dumps(catalog.get_status(), indent=2))
            return

        if args.command == "catalog-check-dump":
            print(json.dumps(compare_dump_to_catalog(args.dump_path, args.sqlite_path), indent=2))
            return
    finally:
        api.close()


if __name__ == "__main__":
    main()
