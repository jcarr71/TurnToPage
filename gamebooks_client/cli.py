from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .api import GamebooksApi
from .models import GamebookBook


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI for querying gamebooks.org")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search books by title")
    search_parser.add_argument("query", help="Search text")

    item_parser = subparsers.add_parser("item", help="Fetch item details by numeric ID")
    item_parser.add_argument("item_id", type=int, help="Item ID from gamebooks.org")
    item_parser.add_argument("--title", default="", help="Optional fallback title")

    series_parser = subparsers.add_parser("series", help="Fetch series details by ID")
    series_parser.add_argument("series_id", type=int, help="Series ID from gamebooks.org")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

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
    finally:
        api.close()


if __name__ == "__main__":
    main()
