# TurnToPage - A Gamebook Collectors Tool

TurnToPage is a desktop application for searching gamebooks.org, tracking your personal collection, and working against a local catalog imported from the maintained SQL dump. A companion CLI is still included for catalog maintenance, scripted lookups, and collection operations.

## Current Status

- The desktop GUI is the primary supported app entry point.
- The GUI is the primary user interface for day-to-day searching, tracking, and series review.
- The CLI remains available for lookup, collection management, and catalog maintenance.
- The local catalog workflow is based on `database/gamebooks.sql` plus an imported `database/gamebooks.sqlite` working copy.
- There is one supported Windows packaging path: `TurnToPage.spec` builds `dist/TurnToPage.exe` from `launch_gui.py`.

## Features

- Tkinter desktop GUI with Dashboard, Search, Collection, and Series Gap Report tabs
- Local collection tracking in SQLite with `have`, `want`, and `missing` states
- Rich item and series parsing from gamebooks.org
- Series-focused completion tools, saved filter presets, bulk updates, and undo support
- Read-only local catalog access backed by `database/gamebooks.sql` or imported `database/gamebooks.sqlite`
- SQLite import workflow for faster local catalog queries and dump comparisons
- Pytest coverage for parsing, catalog import/search, and collection behaviors

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest -q
```

## Run The App

Primary GUI entry point:

```bash
python launch_gui.py
```

Installed GUI script:

```bash
turntopage-gui
```

## GUI Overview

- Dashboard: collection totals, recent activity, quick actions, series progress, and finish-next suggestions
- Search: live lookup against gamebooks.org with item details and collection actions
- Collection: browse, filter, and bulk-update tracked books in your local database
- Series Gap Report: review series completion and identify what is still missing

The GUI is the recommended way to use the project unless you are importing a fresh catalog dump, automating lookups, or doing scripted maintenance.

## CLI And Maintenance

Installed CLI entry point:

```bash
turntopage --help
```

Or without installing scripts:

```bash
python -m gamebooks_client.cli --help
```

Use the CLI for catalog import, scripted queries, or collection updates outside the GUI.

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

Catalog workflow:

```bash
python -m gamebooks_client.cli catalog-import
python -m gamebooks_client.cli catalog-status
python -m gamebooks_client.cli catalog-check-dump --dump-path path\to\new_dump.sql
python -m gamebooks_client.cli catalog-books --limit 10
python -m gamebooks_client.cli catalog-search "lone wolf"
python -m gamebooks_client.cli catalog-item 11
python -m gamebooks_client.cli catalog-files 11 --images-only
python -m gamebooks_client.cli catalog-series-search "fighting fantasy"
python -m gamebooks_client.cli catalog-series 111 --limit 10
```

The catalog workflow has two layers:

- `database/gamebooks.sql` is the received source dump from the site maintainer.
- `database/gamebooks.sqlite` is the app's local working catalog, rebuilt from the dump.

Run `python -m gamebooks_client.cli catalog-import` after receiving a new dump. If the maintainer sends a dump file at another path, you can refresh from it with:

```bash
python -m gamebooks_client.cli catalog-import --dump-path path\to\new_dump.sql --replace-master-dump
```

Catalog read commands prefer SQLite when `database/gamebooks.sqlite` exists, and fall back to parsing `database/gamebooks.sql` if it does not.

The dump contains file metadata and paths, not embedded image binaries. Those links come from `Files` plus `Items_Files`, and the app imports and exposes them through `catalog-item` and `catalog-files`.

Catalog search checks primary titles, alternate titles, and imported creator names/roles. File records also include full `https://gamebooks.org/...` URLs derived from the relative dump paths.

Series metadata also carries alternate titles and linked files from `Series_AltTitles` and `Series_Files`, so `catalog-series` returns richer series payloads after import.

## Packaging (Windows EXE)

Build from the single supported PyInstaller spec:

```bash
python -m PyInstaller -y TurnToPage.spec
```

Output executable:

- `dist/TurnToPage.exe`

Generated packaging output under `build/` and `dist/` is ignored and can be removed/rebuilt at any time.

## Optional Cover Images

Cover rendering in the GUI uses Pillow when installed.

```bash
pip install "Pillow>=10.0"
```

## Project Layout

- `gamebooks_client/api.py`: HTTP and parsing layer
- `gamebooks_client/catalog.py`: local SQL dump and SQLite catalog access
- `gamebooks_client/collection.py`: local collection store
- `gamebooks_client/gui.py`: desktop interface
- `gamebooks_client/cli.py`: command-line interface
