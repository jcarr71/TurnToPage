from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

VALID_STATUSES = {"have", "missing", "want"}


@dataclass(frozen=True)
class CollectionEntry:
    item_id: int
    status: str
    title: str
    url: str
    updated_at: str
    series_title: str = ""
    series_id: Optional[int] = None


class CollectionStore:
    def __init__(self, db_path: str = "turntopage.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collection_entries (
                    item_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    series_id INTEGER,
                    series_title TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK(status IN ('have', 'missing', 'want'))
                )
                """
            )
            # Migrate older databases that lack the series columns.
            for _col_sql in (
                "ALTER TABLE collection_entries ADD COLUMN series_id INTEGER",
                "ALTER TABLE collection_entries ADD COLUMN series_title TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    conn.execute(_col_sql)
                except sqlite3.OperationalError:
                    pass  # column already exists

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS filter_presets (
                    name TEXT PRIMARY KEY,
                    text_filter TEXT NOT NULL DEFAULT '',
                    status_filter TEXT NOT NULL DEFAULT 'all',
                    sort TEXT NOT NULL DEFAULT 'title-asc'
                )
                """
            )

    def set_status(
        self,
        item_id: int,
        status: str,
        title: Optional[str] = None,
        url: Optional[str] = None,
        series_id: Optional[int] = None,
        series_title: Optional[str] = None,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Expected one of: {', '.join(sorted(VALID_STATUSES))}.")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO collection_entries (item_id, status, title, url, series_id, series_title)
                VALUES (?, ?, COALESCE(?, ''), COALESCE(?, ''), ?, COALESCE(?, ''))
                ON CONFLICT(item_id) DO UPDATE SET
                    status = excluded.status,
                    title = CASE
                        WHEN excluded.title != '' THEN excluded.title
                        ELSE collection_entries.title
                    END,
                    url = CASE
                        WHEN excluded.url != '' THEN excluded.url
                        ELSE collection_entries.url
                    END,
                    series_id = CASE
                        WHEN excluded.series_id IS NOT NULL THEN excluded.series_id
                        ELSE collection_entries.series_id
                    END,
                    series_title = CASE
                        WHEN excluded.series_title != '' THEN excluded.series_title
                        ELSE collection_entries.series_title
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (item_id, status, title, url, series_id, series_title),
            )

    def remove(self, item_id: int) -> bool:
        with self._connect() as conn:
            result = conn.execute("DELETE FROM collection_entries WHERE item_id = ?", (item_id,))
            return result.rowcount > 0

    def get(self, item_id: int) -> Optional[CollectionEntry]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT item_id, status, title, url, updated_at
                FROM collection_entries
                WHERE item_id = ?
                """,
                (item_id,),
            ).fetchone()

        if row is None:
            return None

        return CollectionEntry(
            item_id=int(row["item_id"]),
            status=str(row["status"]),
            title=str(row["title"]),
            url=str(row["url"]),
            updated_at=str(row["updated_at"]),
        )

    def list_entries(self, status: Optional[str] = None) -> List[CollectionEntry]:
        query = """
            SELECT item_id, status, title, url, updated_at, series_title, series_id
            FROM collection_entries
        """
        params: tuple[object, ...] = ()
        if status is not None:
            if status not in VALID_STATUSES:
                raise ValueError(
                    f"Invalid status '{status}'. Expected one of: {', '.join(sorted(VALID_STATUSES))}."
                )
            query += " WHERE status = ?"
            params = (status,)

        query += " ORDER BY title COLLATE NOCASE ASC, item_id ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            CollectionEntry(
                item_id=int(row["item_id"]),
                status=str(row["status"]),
                title=str(row["title"]),
                url=str(row["url"]),
                updated_at=str(row["updated_at"]),
                series_title=str(row["series_title"]),
                series_id=int(row["series_id"]) if row["series_id"] is not None else None,
            )
            for row in rows
        ]

    def summary_counts(self) -> Dict[str, int]:
        counts = {"have": 0, "want": 0, "missing": 0, "total": 0}

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS entry_count
                FROM collection_entries
                GROUP BY status
                """
            ).fetchall()

        for row in rows:
            status = str(row["status"])
            entry_count = int(row["entry_count"])
            if status in counts:
                counts[status] = entry_count
                counts["total"] += entry_count

        return counts

    def recent_entries(self, limit: int = 10) -> List[CollectionEntry]:
        if limit <= 0:
            return []

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT item_id, status, title, url, updated_at
                FROM collection_entries
                ORDER BY updated_at DESC, item_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            CollectionEntry(
                item_id=int(row["item_id"]),
                status=str(row["status"]),
                title=str(row["title"]),
                url=str(row["url"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def status_map(self, item_ids: List[int]) -> Dict[int, str]:
        if not item_ids:
            return {}

        placeholders = ",".join("?" for _ in item_ids)
        query = f"SELECT item_id, status FROM collection_entries WHERE item_id IN ({placeholders})"

        with self._connect() as conn:
            rows = conn.execute(query, tuple(item_ids)).fetchall()

        return {int(row["item_id"]): str(row["status"]) for row in rows}

    def series_progress(self) -> List[Dict[str, object]]:
        """Return per-series completion stats for all series with ≥2 tracked entries."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT series_id,
                       series_title,
                       SUM(CASE WHEN status = 'have' THEN 1 ELSE 0 END) AS have_count,
                       SUM(CASE WHEN status IN ('missing', 'want') THEN 1 ELSE 0 END) AS missing_count,
                       COUNT(*) AS total_count
                FROM collection_entries
                WHERE series_id IS NOT NULL
                GROUP BY series_id, series_title
                HAVING total_count >= 2
                ORDER BY CAST(have_count AS REAL) / total_count DESC,
                         have_count DESC
                LIMIT 12
                """
            ).fetchall()
        return [
            {
                "series_id": int(row["series_id"]),
                "series_title": str(row["series_title"]),
                "have": int(row["have_count"]),
                "missing": int(row["missing_count"]),
                "total": int(row["total_count"]),
                "pct": (
                    int(100 * int(row["have_count"]) / int(row["total_count"]))
                    if int(row["total_count"]) > 0
                    else 0
                ),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Filter presets
    # ------------------------------------------------------------------

    def save_preset(self, name: str, text_filter: str, status_filter: str, sort: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Preset name must not be empty.")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO filter_presets (name, text_filter, status_filter, sort)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    text_filter   = excluded.text_filter,
                    status_filter = excluded.status_filter,
                    sort          = excluded.sort
                """,
                (name, text_filter, status_filter, sort),
            )

    def list_presets(self) -> List[Dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, text_filter, status_filter, sort FROM filter_presets ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [
            {
                "name": str(row["name"]),
                "text_filter": str(row["text_filter"]),
                "status_filter": str(row["status_filter"]),
                "sort": str(row["sort"]),
            }
            for row in rows
        ]

    def delete_preset(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM filter_presets WHERE name = ?", (name,))

    # ------------------------------------------------------------------
    # Recommendations / suggestions
    # ------------------------------------------------------------------

    def suggestions(self, limit: int = 8) -> List[Dict[str, object]]:
        """Series you've started but haven't finished, sorted fewest-still-needed first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT series_id,
                       series_title,
                       SUM(CASE WHEN status = 'have' THEN 1 ELSE 0 END)             AS have_count,
                       SUM(CASE WHEN status IN ('missing', 'want') THEN 1 ELSE 0 END) AS need_count,
                       COUNT(*) AS total_count
                FROM collection_entries
                WHERE series_id IS NOT NULL
                GROUP BY series_id, series_title
                HAVING have_count > 0 AND need_count > 0
                ORDER BY need_count ASC, have_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "series_id": int(row["series_id"]),
                "series_title": str(row["series_title"]),
                "have": int(row["have_count"]),
                "need": int(row["need_count"]),
                "total": int(row["total_count"]),
            }
            for row in rows
        ]
