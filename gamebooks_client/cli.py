from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .api import GamebooksApi
from .catalog import CatalogStore
from .collection import CollectionStore, VALID_STATUSES
from .crawler import crawl_catalog
from .models import GamebookBook


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

    crawl_parser = subparsers.add_parser(
        "crawl",
        help="Crawl gamebooks.org item/series pages into a local catalog database",
    )
    crawl_parser.add_argument(
        "--catalog-db-path",
        default="turntopage_catalog.db",
        help="Path to local SQLite catalog database (default: turntopage_catalog.db)",
    )
    crawl_parser.add_argument(
        "--scope",
        choices=["both", "items", "series"],
        default="both",
        help="Which IDs to crawl (default: both)",
    )
    crawl_parser.add_argument("--start-item", type=int, default=1, help="Starting item ID (default: 1)")
    crawl_parser.add_argument("--start-series", type=int, default=1, help="Starting series ID (default: 1)")
    crawl_parser.add_argument("--max-item-id", type=int, help="Optional maximum item ID to crawl")
    crawl_parser.add_argument("--max-series-id", type=int, help="Optional maximum series ID to crawl")
    crawl_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="Delay between requests in seconds (default: 1.0)",
    )
    crawl_parser.add_argument(
        "--max-miss-streak",
        type=int,
        default=500,
        help="Stop after this many consecutive 404s (default: 500)",
    )
    crawl_parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore saved crawl checkpoints and start from --start-item/--start-series",
    )

    return parser


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

        if args.command == "crawl":
            catalog = CatalogStore(args.catalog_db_path)

            def _progress(message: str) -> None:
                print(message)

            summary = crawl_catalog(
                api=api,
                catalog=catalog,
                scope=args.scope,
                start_item=args.start_item,
                start_series=args.start_series,
                max_item_id=args.max_item_id,
                max_series_id=args.max_series_id,
                delay_seconds=args.delay_seconds,
                max_miss_streak=args.max_miss_streak,
                resume=not args.no_resume,
                progress_cb=_progress,
            )
            print(json.dumps(summary, indent=2))
            return
    finally:
        api.close()


if __name__ == "__main__":
    main()
