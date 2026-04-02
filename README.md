# TurnToPage - A Gamebook Collectors Tool

TurnToPage is a Python app for pulling and parsing collector data from https://gamebooks.org.

## What This Includes

- Search parsing for `Series` and `Item` results
- Item details parsing for title, image links, metadata, related links, and structured fields
- Series details parsing for gamebooks and collection entries
- Session-aware HTTP client with cookie persistence
- Pytest fixture-based parsing tests
- CLI for quick migration-time validation

## Quick Start

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
pytest
```

## CLI Usage

```bash
python -m gamebooks_client.cli search "lone wolf"
python -m gamebooks_client.cli item 456
python -m gamebooks_client.cli series 789
python -m gamebooks_client.cli mark 456 have --title "Flight from the Dark"
python -m gamebooks_client.cli mark 457 missing
python -m gamebooks_client.cli collection
python -m gamebooks_client.cli series-status 789
```

## Local Collection Tracking

TurnToPage now stores your collection state in a local SQLite file so you can track:

- `have`: books you already own
- `missing`: books you still need to find
- `want`: wishlist items

All tracking commands accept `--db-path` if you want a custom database location.

Examples:

```bash
python -m gamebooks_client.cli --db-path my_collection.db mark 123 have
python -m gamebooks_client.cli mark 124 missing
python -m gamebooks_client.cli collection --status missing
python -m gamebooks_client.cli unmark 124
python -m gamebooks_client.cli series-status 789
```

`series-status` compares books in a gamebooks.org series with your local database and returns each title with `have`, `missing`, `want`, or `unknown`.

## Move To New Repo

1. Create a new empty GitHub repository.
2. Copy this folder (`python_gamebook_tracker`) into the new repository root.
3. Commit all files and push.
4. In CI, run `pip install -r requirements.txt` and `pytest`.

## Suggested Next Steps

1. Add live integration tests behind an opt-in flag or environment variable.
2. Add retries and backoff for transient HTTP failures.
3. Add persistence (SQLite/PostgreSQL) once schema settles.
