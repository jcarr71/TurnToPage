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
```

## Move To New Repo

1. Create a new empty GitHub repository.
2. Copy this folder (`python_gamebook_tracker`) into the new repository root.
3. Commit all files and push.
4. In CI, run `pip install -r requirements.txt` and `pytest`.

## Suggested Next Steps

1. Add live integration tests behind an opt-in flag or environment variable.
2. Add retries and backoff for transient HTTP failures.
3. Add persistence (SQLite/PostgreSQL) once schema settles.
