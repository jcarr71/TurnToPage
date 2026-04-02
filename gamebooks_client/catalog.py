from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Tuple

from .models import GamebookItemDetails, GamebookSeriesDetails


class CatalogStore:
    def __init__(self, db_path: str = "turntopage_catalog.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_items (
                    item_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    series_id INTEGER,
                    series_title TEXT,
                    series_number INTEGER,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    raw_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_series (
                    series_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    total_gamebooks INTEGER NOT NULL DEFAULT 0,
                    total_collections INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    raw_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_series_items (
                    series_id INTEGER NOT NULL,
                    item_id INTEGER,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (series_id, kind, position),
                    CHECK(kind IN ('gamebook', 'collection')),
                    FOREIGN KEY(series_id) REFERENCES catalog_series(series_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_state (
                    scope TEXT PRIMARY KEY,
                    next_id INTEGER NOT NULL,
                    miss_streak INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_items_series_id ON catalog_items(series_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_series_items_series ON catalog_series_items(series_id)")

    def upsert_item(self, item_id: int, details: GamebookItemDetails) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog_items (
                    item_id, title, url, series_id, series_title, series_number, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    series_id = excluded.series_id,
                    series_title = excluded.series_title,
                    series_number = excluded.series_number,
                    raw_json = excluded.raw_json,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (
                    item_id,
                    details.title,
                    details.show_url or details.url,
                    details.series_id,
                    details.series_title,
                    details.series_number,
                    json.dumps(asdict(details), ensure_ascii=True),
                ),
            )

    def upsert_series(self, series_id: int, details: GamebookSeriesDetails) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog_series (
                    series_id, title, url, total_gamebooks, total_collections, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    total_gamebooks = excluded.total_gamebooks,
                    total_collections = excluded.total_collections,
                    raw_json = excluded.raw_json,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (
                    series_id,
                    details.title,
                    details.url,
                    details.total_count,
                    details.collection_count,
                    json.dumps(asdict(details), ensure_ascii=True),
                ),
            )

            conn.execute("DELETE FROM catalog_series_items WHERE series_id = ?", (series_id,))
            for pos, item in enumerate(details.gamebooks, start=1):
                conn.execute(
                    """
                    INSERT INTO catalog_series_items (series_id, item_id, title, url, kind, position)
                    VALUES (?, ?, ?, ?, 'gamebook', ?)
                    """,
                    (series_id, item.item_id, item.title, item.url, pos),
                )
            for pos, item in enumerate(details.collections, start=1):
                conn.execute(
                    """
                    INSERT INTO catalog_series_items (series_id, item_id, title, url, kind, position)
                    VALUES (?, ?, ?, ?, 'collection', ?)
                    """,
                    (series_id, item.item_id, item.title, item.url, pos),
                )

    def load_state(self, scope: str, default_next_id: int) -> Tuple[int, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT next_id, miss_streak FROM crawl_state WHERE scope = ?",
                (scope,),
            ).fetchone()
        if row is None:
            return default_next_id, 0
        return int(row["next_id"]), int(row["miss_streak"])

    def save_state(self, scope: str, next_id: int, miss_streak: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crawl_state (scope, next_id, miss_streak)
                VALUES (?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    next_id = excluded.next_id,
                    miss_streak = excluded.miss_streak,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (scope, next_id, miss_streak),
            )

    def count_items(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM catalog_items").fetchone()
        return int(row["c"]) if row is not None else 0

    def count_series(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM catalog_series").fetchone()
        return int(row["c"]) if row is not None else 0

    def list_series_items(self, series_id: int, kind: Optional[str] = None) -> List[sqlite3.Row]:
        query = """
            SELECT series_id, item_id, title, url, kind, position
            FROM catalog_series_items
            WHERE series_id = ?
        """
        params: tuple[object, ...] = (series_id,)
        if kind is not None:
            query += " AND kind = ?"
            params = (series_id, kind)
        query += " ORDER BY kind ASC, position ASC"
        with self._connect() as conn:
            return list(conn.execute(query, params).fetchall())
