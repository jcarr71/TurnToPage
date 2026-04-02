# TurnToPage - A Gamebook Collectors Tool

TurnToPage is a desktop and CLI app for searching gamebooks.org, tracking your personal collection, and building a local catalog snapshot for faster offline-friendly workflows.

## Project Status

TurnToPage is not production-ready yet.

Development is currently waiting on explicit clearance/permission for data usage and large-scale catalog acquisition. Until that clearance is granted, treat crawling and catalog snapshot features as local development/testing workflows only.

## Features

- Tkinter desktop GUI with tabs for Dashboard, Search, Collection, and Series progress
- Local collection tracking in SQLite (`have`, `want`, `missing`)
- Rich item and series parsing from gamebooks.org
- Series-focused completion tools (missing-only view, bulk updates, progress and suggestions)
- Saved filter presets for search workflows
- Resumable crawler that builds a local catalog database
- CLI for search, detail lookup, collection tracking, and crawling

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest -q
```

## Run The App

GUI:

```bash
python launch_gui.py
```

CLI entry points (after install):

```bash
turntopage --help
turntopage-gui
```

Or without installing scripts:

```bash
python -m gamebooks_client.cli --help
```

## CLI Commands

Basic lookup:

```bash
python -m gamebooks_client.cli search "lone wolf"
python -m gamebooks_client.cli item 456
python -m gamebooks_client.cli series 789
```

Collection tracking:

```bash
python -m gamebooks_client.cli mark 456 have --title "Flight from the Dark"
python -m gamebooks_client.cli mark 457 missing
python -m gamebooks_client.cli unmark 457
python -m gamebooks_client.cli collection
python -m gamebooks_client.cli collection --status missing
python -m gamebooks_client.cli series-status 789
```

Custom collection DB path:

```bash
python -m gamebooks_client.cli --db-path my_collection.db collection
```

## Build A Local Catalog Snapshot

The crawler writes to `turntopage_catalog.db` by default and stores checkpoint state so interrupted crawls can resume.

```bash
python -m gamebooks_client.cli crawl --scope both
python -m gamebooks_client.cli crawl --scope items --start-item 1 --max-item-id 5000
python -m gamebooks_client.cli crawl --scope series --start-series 1 --max-series-id 2000
```

Useful options:

- `--catalog-db-path`: set custom catalog DB file
- `--delay-seconds`: request pacing (default `1.0`)
- `--max-miss-streak`: stop threshold for consecutive 404s
- `--no-resume`: ignore saved checkpoints and start from provided IDs

## Packaging (Windows EXE)

Build from the existing PyInstaller spec:

```bash
python -m PyInstaller TurnToPage.spec
```

Output executable:

- `dist/TurnToPage.exe`

## Optional Cover Images

Cover rendering in the GUI uses Pillow when installed.

```bash
pip install "Pillow>=10.0"
```

## Project Layout

- `gamebooks_client/api.py`: HTTP and parsing layer
- `gamebooks_client/collection.py`: local collection store
- `gamebooks_client/catalog.py`: local catalog store
- `gamebooks_client/crawler.py`: resumable crawler logic
- `gamebooks_client/gui.py`: desktop interface
- `gamebooks_client/cli.py`: command-line interface
