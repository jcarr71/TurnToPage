from __future__ import annotations

from dataclasses import asdict
from functools import cached_property
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urljoin
from typing import Dict, List, Optional, Tuple

from .models import CatalogBook, CatalogCreator, CatalogFile, CatalogSeries, CatalogSeriesEntry, GAMEBOOKS_BASE_URL


class CatalogError(Exception):
    pass


def compare_dump_to_catalog(dump_path: Path | str, sqlite_path: Path | str) -> Dict[str, object]:
    dump_file = Path(dump_path)
    sqlite_file = Path(sqlite_path)
    if not dump_file.exists():
        raise CatalogError(f"SQL dump not found: {dump_file}")

    incoming_sha = _compute_sha256(dump_file)
    payload: Dict[str, object] = {
        "dump_path": str(dump_file),
        "incoming_dump_sha256": incoming_sha,
        "sqlite_path": str(sqlite_file),
        "catalog_exists": sqlite_file.exists(),
    }

    if not sqlite_file.exists():
        payload["matches_imported_dump"] = False
        payload["reason"] = "sqlite catalog does not exist yet"
        return payload

    catalog = SqliteCatalog(sqlite_file)
    status = catalog.get_status()
    current_sha = status.get("source_dump_sha256")
    payload["current_imported_dump_sha256"] = current_sha
    payload["matches_imported_dump"] = current_sha == incoming_sha
    return payload


def import_dump_to_sqlite(dump_path: Path | str, sqlite_path: Path | str) -> Dict[str, object]:
    dump_file = Path(dump_path)
    sqlite_file = Path(sqlite_path)

    data = _load_dump_data(dump_file)
    sqlite_file.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(sqlite_file) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            DROP TABLE IF EXISTS item_files;
            DROP TABLE IF EXISTS files;
            DROP TABLE IF EXISTS series_files;
            DROP TABLE IF EXISTS series_books;
            DROP TABLE IF EXISTS series;
            DROP TABLE IF EXISTS books;
            DROP TABLE IF EXISTS import_metadata;
            """
        )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS import_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS books (
                item_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                errata TEXT,
                thanks TEXT,
                material_type_id INTEGER,
                material_type_name TEXT,
                alt_titles_json TEXT NOT NULL,
                creators_json TEXT NOT NULL,
                search_text TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS series (
                series_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                alt_titles_json TEXT NOT NULL,
                search_text TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS series_books (
                series_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (series_id, position),
                FOREIGN KEY (series_id) REFERENCES series(series_id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES books(item_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS files (
                file_id INTEGER PRIMARY KEY,
                name TEXT,
                path TEXT,
                url TEXT,
                description TEXT,
                file_type_id INTEGER,
                file_type_name TEXT
            );

            CREATE TABLE IF NOT EXISTS item_files (
                item_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (item_id, position),
                FOREIGN KEY (item_id) REFERENCES books(item_id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS series_files (
                series_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (series_id, position),
                FOREIGN KEY (series_id) REFERENCES series(series_id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
            );
            """
        )

        connection.executemany(
            """
            INSERT INTO books (
                item_id,
                title,
                description,
                errata,
                thanks,
                material_type_id,
                material_type_name,
                alt_titles_json,
                creators_json,
                search_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    book.item_id,
                    book.title,
                    book.description,
                    book.errata,
                    book.thanks,
                    book.material_type_id,
                    book.material_type_name,
                    json.dumps(book.alt_titles, ensure_ascii=False),
                    json.dumps([asdict(creator) for creator in book.creators], ensure_ascii=False),
                    _build_search_text(book),
                )
                for book in sorted(data["items"].values(), key=lambda item: item.item_id)
            ],
        )

        connection.executemany(
            "INSERT INTO series (series_id, title, description, alt_titles_json, search_text) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    series.series_id,
                    series.title,
                    series.description,
                    json.dumps(series.alt_titles, ensure_ascii=False),
                    _build_series_search_text(series),
                )
                for series in sorted(data["series"].values(), key=lambda item: item.series_id)
            ],
        )

        series_rows = []
        for series_id, item_ids in sorted(data["series_books"].items()):
            for position, item_id in enumerate(item_ids, start=1):
                series_rows.append((series_id, item_id, position))
        connection.executemany(
            "INSERT INTO series_books (series_id, item_id, position) VALUES (?, ?, ?)",
            series_rows,
        )

        connection.executemany(
            """
            INSERT INTO files (
                file_id,
                name,
                path,
                url,
                description,
                file_type_id,
                file_type_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    file.file_id,
                    file.name,
                    file.path,
                    file.url,
                    file.description,
                    file.file_type_id,
                    file.file_type_name,
                )
                for file in sorted(data["files"].values(), key=lambda item: item.file_id)
            ],
        )

        item_file_rows = []
        for item_id, file_ids in sorted(data["item_files"].items()):
            for position, file_id in enumerate(file_ids, start=1):
                item_file_rows.append((item_id, file_id, position))
        connection.executemany(
            "INSERT INTO item_files (item_id, file_id, position) VALUES (?, ?, ?)",
            item_file_rows,
        )

        series_file_rows = []
        for series_id, file_ids in sorted(data["series_files"].items()):
            for position, file_id in enumerate(file_ids, start=1):
                series_file_rows.append((series_id, file_id, position))
        connection.executemany(
            "INSERT INTO series_files (series_id, file_id, position) VALUES (?, ?, ?)",
            series_file_rows,
        )

        metadata_rows = {
            "source_dump_path": str(dump_file.resolve()),
            "source_dump_sha256": _compute_sha256(dump_file),
            "book_count": str(len(data["items"])),
            "series_count": str(len(data["series"])),
            "series_book_count": str(len(series_rows)),
            "file_count": str(len(data["files"])),
            "item_file_count": str(len(item_file_rows)),
            "series_file_count": str(len(series_file_rows)),
            "alt_title_count": str(sum(len(book.alt_titles) for book in data["items"].values())),
            "creator_count": str(sum(len(book.creators) for book in data["items"].values())),
            "series_alt_title_count": str(sum(len(series.alt_titles) for series in data["series"].values())),
        }
        connection.executemany(
            "INSERT INTO import_metadata (key, value) VALUES (?, ?)",
            metadata_rows.items(),
        )
        connection.commit()

    return {
        "sqlite_path": str(sqlite_file),
        "source_dump_path": metadata_rows["source_dump_path"],
        "source_dump_sha256": metadata_rows["source_dump_sha256"],
        "book_count": len(data["items"]),
        "series_count": len(data["series"]),
        "series_book_count": len(series_rows),
        "file_count": len(data["files"]),
        "item_file_count": len(item_file_rows),
        "series_file_count": len(series_file_rows),
        "alt_title_count": sum(len(book.alt_titles) for book in data["items"].values()),
        "creator_count": sum(len(book.creators) for book in data["items"].values()),
        "series_alt_title_count": sum(len(series.alt_titles) for series in data["series"].values()),
    }


class SqlDumpCatalog:
    def __init__(self, dump_path: Path | str) -> None:
        self._dump_path = Path(dump_path)

    @property
    def dump_path(self) -> Path:
        return self._dump_path

    @cached_property
    def _data(self) -> Dict[str, object]:
        return _load_dump_data(self._dump_path)

    def list_books(self, *, limit: int = 20, offset: int = 0) -> List[CatalogBook]:
        items = sorted(self._items.values(), key=lambda item: item.item_id)
        return items[offset : offset + limit]

    def search_books(self, query: str, *, limit: int = 20) -> List[CatalogBook]:
        needle = query.casefold().strip()
        if not needle:
            return []

        matches = [item for item in self._items.values() if needle in _build_search_text(item).casefold()]
        matches.sort(key=lambda item: (item.title.casefold(), item.item_id))
        return matches[:limit]

    def get_book(self, item_id: int) -> Optional[CatalogBook]:
        return self._items.get(item_id)

    def search_series(self, query: str, *, limit: int = 20) -> List[CatalogSeries]:
        needle = query.casefold().strip()
        if not needle:
            return []

        matches = [series for series in self._series.values() if needle in _build_series_search_text(series).casefold()]
        matches.sort(key=lambda series: (series.title.casefold(), series.series_id))
        return matches[:limit]

    def get_series(self, series_id: int) -> Optional[CatalogSeries]:
        return self._series.get(series_id)

    def get_series_books(self, series_id: int, *, limit: Optional[int] = None) -> List[CatalogSeriesEntry]:
        series = self.get_series(series_id)
        if series is None:
            return []

        item_ids = self._series_books.get(series_id, [])
        if limit is not None:
            item_ids = item_ids[:limit]

        entries: List[CatalogSeriesEntry] = []
        for item_id in item_ids:
            item = self._items.get(item_id)
            entries.append(
                CatalogSeriesEntry(
                    series_id=series_id,
                    item_id=item_id,
                    title=item.title if item is not None else f"Item {item_id}",
                )
            )
        return entries

    def get_book_payload(self, item_id: int) -> Optional[Dict[str, object]]:
        item = self.get_book(item_id)
        if item is None:
            return None
        return {
            **asdict(item),
            "files": [asdict(file) for file in self.get_book_files(item_id)],
        }

    def get_book_files(self, item_id: int, *, images_only: bool = False) -> List[CatalogFile]:
        file_ids = self._item_files.get(item_id, [])
        files = [self._files[file_id] for file_id in file_ids if file_id in self._files]
        if images_only:
            files = [file for file in files if file.is_image]
        return files

    def get_series_files(self, series_id: int, *, images_only: bool = False) -> List[CatalogFile]:
        file_ids = self._series_files.get(series_id, [])
        files = [self._files[file_id] for file_id in file_ids if file_id in self._files]
        if images_only:
            files = [file for file in files if file.is_image]
        return files

    def get_status(self) -> Dict[str, object]:
        return {
            "backend": "sql-dump",
            "dump_path": str(self._dump_path),
            "book_count": len(self._items),
            "series_count": len(self._series),
            "series_book_count": sum(len(item_ids) for item_ids in self._series_books.values()),
            "file_count": len(self._files),
            "item_file_count": sum(len(file_ids) for file_ids in self._item_files.values()),
            "series_file_count": sum(len(file_ids) for file_ids in self._series_files.values()),
            "alt_title_count": sum(len(book.alt_titles) for book in self._items.values()),
            "creator_count": sum(len(book.creators) for book in self._items.values()),
            "series_alt_title_count": sum(len(series.alt_titles) for series in self._series.values()),
        }

    @property
    def _items(self) -> Dict[int, CatalogBook]:
        return self._data["items"]  # type: ignore[return-value]

    @property
    def _series(self) -> Dict[int, CatalogSeries]:
        return self._data["series"]  # type: ignore[return-value]

    @property
    def _series_books(self) -> Dict[int, List[int]]:
        return self._data["series_books"]  # type: ignore[return-value]

    @property
    def _files(self) -> Dict[int, CatalogFile]:
        return self._data["files"]  # type: ignore[return-value]

    @property
    def _item_files(self) -> Dict[int, List[int]]:
        return self._data["item_files"]  # type: ignore[return-value]

    @property
    def _series_files(self) -> Dict[int, List[int]]:
        return self._data["series_files"]  # type: ignore[return-value]

    def _split_insert_line(self, line: str) -> Tuple[str, str]:
        prefix = "INSERT INTO `"
        table_end = line.find("`", len(prefix))
        if table_end == -1:
            raise CatalogError("Unable to parse insert table name.")
        values_marker = " VALUES "
        values_index = line.find(values_marker, table_end)
        if values_index == -1:
            raise CatalogError("Unable to parse insert values.")
        table_name = line[len(prefix):table_end]
        values_text = line[values_index + len(values_marker):]
        if values_text.endswith(";"):
            values_text = values_text[:-1]
        return table_name, values_text

    def _parse_values(self, values_text: str) -> List[Tuple[object, ...]]:
        rows: List[Tuple[object, ...]] = []
        current_row: List[object] = []
        current_value: List[str] = []
        in_string = False
        escape_next = False
        value_is_string = False

        def flush_value() -> None:
            nonlocal current_value, value_is_string
            token = "".join(current_value)
            if value_is_string:
                current_row.append(token)
            else:
                stripped = token.strip()
                if stripped == "NULL" or stripped == "":
                    current_row.append(None)
                else:
                    try:
                        current_row.append(int(stripped))
                    except ValueError:
                        current_row.append(stripped)
            current_value = []
            value_is_string = False

        for char in values_text:
            if in_string:
                if escape_next:
                    current_value.append(char)
                    escape_next = False
                elif char == "\\":
                    escape_next = True
                elif char == "'":
                    in_string = False
                else:
                    current_value.append(char)
                continue

            if char == "'":
                in_string = True
                value_is_string = True
            elif char == "(":
                current_row = []
                current_value = []
                value_is_string = False
            elif char == ",":
                flush_value()
            elif char == ")":
                flush_value()
                rows.append(tuple(current_row))
                current_row = []
            else:
                current_value.append(char)

        return rows

    def _as_int(self, value: object) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(str(value))
        except ValueError:
            return None

    def _as_str(self, value: object) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class SqliteCatalog:
    def __init__(self, sqlite_path: Path | str) -> None:
        self._sqlite_path = Path(sqlite_path)
        if not self._sqlite_path.exists():
            raise CatalogError(f"SQLite catalog not found: {self._sqlite_path}")

    def list_books(self, *, limit: int = 20, offset: int = 0) -> List[CatalogBook]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT item_id, title, material_type_id, material_type_name, description, errata, thanks,
                       alt_titles_json, creators_json
                FROM books
                ORDER BY item_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [self._row_to_book(row) for row in rows]

    def search_books(self, query: str, *, limit: int = 20) -> List[CatalogBook]:
        needle = f"%{query.strip()}%"
        if needle == "%%":
            return []

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT item_id, title, material_type_id, material_type_name, description, errata, thanks,
                       alt_titles_json, creators_json
                FROM books
                WHERE search_text LIKE ? COLLATE NOCASE
                ORDER BY title COLLATE NOCASE, item_id
                LIMIT ?
                """,
                (needle, limit),
            ).fetchall()
        return [self._row_to_book(row) for row in rows]

    def get_book(self, item_id: int) -> Optional[CatalogBook]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT item_id, title, material_type_id, material_type_name, description, errata, thanks,
                       alt_titles_json, creators_json
                FROM books
                WHERE item_id = ?
                """,
                (item_id,),
            ).fetchone()
        return None if row is None else self._row_to_book(row)

    def search_series(self, query: str, *, limit: int = 20) -> List[CatalogSeries]:
        raw_query = query.strip()
        if not raw_query:
            return []

        needle = f"%{raw_query}%"

        with self._connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(series)").fetchall()
            }
            where_clause = "title LIKE ? COLLATE NOCASE OR alt_titles_json LIKE ? COLLATE NOCASE"
            parameters: List[object] = [needle, needle]
            if "search_text" in columns:
                where_clause += " OR search_text LIKE ? COLLATE NOCASE"
                parameters.append(needle)
            parameters.append(limit)
            rows = connection.execute(
                f"""
                SELECT series_id, title, description, alt_titles_json
                FROM series
                WHERE {where_clause}
                ORDER BY title COLLATE NOCASE, series_id
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            CatalogSeries(
                series_id=row["series_id"],
                title=row["title"],
                description=row["description"],
                alt_titles=json.loads(row["alt_titles_json"] or "[]"),
                files=self.get_series_files(row["series_id"]),
            )
            for row in rows
        ]

    def get_series(self, series_id: int) -> Optional[CatalogSeries]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT series_id, title, description, alt_titles_json FROM series WHERE series_id = ?",
                (series_id,),
            ).fetchone()
        if row is None:
            return None
        return CatalogSeries(
            series_id=row["series_id"],
            title=row["title"],
            description=row["description"],
            alt_titles=json.loads(row["alt_titles_json"] or "[]"),
            files=self.get_series_files(row["series_id"]),
        )

    def get_series_books(self, series_id: int, *, limit: Optional[int] = None) -> List[CatalogSeriesEntry]:
        query = (
            """
            SELECT sb.series_id, sb.item_id, b.title
            FROM series_books sb
            JOIN books b ON b.item_id = sb.item_id
            WHERE sb.series_id = ?
            ORDER BY sb.position
            """
        )
        params: tuple[object, ...] = (series_id,)
        if limit is not None:
            query += " LIMIT ?"
            params = (series_id, limit)

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            CatalogSeriesEntry(series_id=row["series_id"], item_id=row["item_id"], title=row["title"])
            for row in rows
        ]

    def get_book_payload(self, item_id: int) -> Optional[Dict[str, object]]:
        item = self.get_book(item_id)
        if item is None:
            return None
        return {
            **asdict(item),
            "files": [asdict(file) for file in self.get_book_files(item_id)],
        }

    def get_book_files(self, item_id: int, *, images_only: bool = False) -> List[CatalogFile]:
        query = (
            """
            SELECT f.file_id, f.name, f.path, f.url, f.description, f.file_type_id, f.file_type_name
            FROM item_files ifl
            JOIN files f ON f.file_id = ifl.file_id
            WHERE ifl.item_id = ?
            ORDER BY ifl.position
            """
        )
        with self._connect() as connection:
            rows = connection.execute(query, (item_id,)).fetchall()
        files = [self._row_to_file(row) for row in rows]
        if images_only:
            files = [file for file in files if file.is_image]
        return files

    def get_series_files(self, series_id: int, *, images_only: bool = False) -> List[CatalogFile]:
        query = (
            """
            SELECT f.file_id, f.name, f.path, f.url, f.description, f.file_type_id, f.file_type_name
            FROM series_files sfl
            JOIN files f ON f.file_id = sfl.file_id
            WHERE sfl.series_id = ?
            ORDER BY sfl.position
            """
        )
        with self._connect() as connection:
            rows = connection.execute(query, (series_id,)).fetchall()
        files = [self._row_to_file(row) for row in rows]
        if images_only:
            files = [file for file in files if file.is_image]
        return files

    def get_status(self) -> Dict[str, object]:
        with self._connect() as connection:
            metadata = dict(connection.execute("SELECT key, value FROM import_metadata").fetchall())
            counts = {
                "book_count": int(connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]),
                "series_count": int(connection.execute("SELECT COUNT(*) FROM series").fetchone()[0]),
                "series_book_count": int(connection.execute("SELECT COUNT(*) FROM series_books").fetchone()[0]),
                "file_count": int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
                "item_file_count": int(connection.execute("SELECT COUNT(*) FROM item_files").fetchone()[0]),
                "series_file_count": int(connection.execute("SELECT COUNT(*) FROM series_files").fetchone()[0]),
                "alt_title_count": int(metadata.get("alt_title_count", 0)),
                "creator_count": int(metadata.get("creator_count", 0)),
                "series_alt_title_count": int(metadata.get("series_alt_title_count", 0)),
            }
        return {
            "backend": "sqlite",
            "sqlite_path": str(self._sqlite_path),
            **counts,
            "source_dump_path": metadata.get("source_dump_path"),
            "source_dump_sha256": metadata.get("source_dump_sha256"),
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._sqlite_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_book(self, row: sqlite3.Row) -> CatalogBook:
        return CatalogBook(
            item_id=row["item_id"],
            title=row["title"],
            material_type_id=row["material_type_id"],
            material_type_name=row["material_type_name"],
            description=row["description"],
            errata=row["errata"],
            thanks=row["thanks"],
            alt_titles=json.loads(row["alt_titles_json"] or "[]"),
            creators=[CatalogCreator(**creator) for creator in json.loads(row["creators_json"] or "[]")],
        )

    def _row_to_file(self, row: sqlite3.Row) -> CatalogFile:
        return CatalogFile(
            file_id=row["file_id"],
            name=row["name"],
            path=row["path"],
            url=row["url"],
            description=row["description"],
            file_type_id=row["file_type_id"],
            file_type_name=row["file_type_name"],
        )


def open_catalog(*, sqlite_path: Path | str, dump_path: Path | str):
    sqlite_file = Path(sqlite_path)
    if sqlite_file.exists():
        return SqliteCatalog(sqlite_file)
    return SqlDumpCatalog(dump_path)


def _load_dump_data(dump_path: Path) -> Dict[str, object]:
    if not dump_path.exists():
        raise CatalogError(f"SQL dump not found: {dump_path}")

    items: Dict[int, CatalogBook] = {}
    descriptions: Dict[int, str] = {}
    material_types: Dict[int, str] = {}
    file_types: Dict[int, str] = {}
    people: Dict[int, str] = {}
    roles: Dict[int, str] = {}
    alt_titles: Dict[int, List[str]] = {}
    creator_refs_by_item: Dict[int, List[Tuple[int, Optional[int]]]] = {}
    series: Dict[int, CatalogSeries] = {}
    series_alt_titles: Dict[int, List[str]] = {}
    series_books: Dict[int, List[int]] = {}
    files: Dict[int, CatalogFile] = {}
    item_files: Dict[int, List[int]] = {}
    series_files: Dict[int, List[int]] = {}
    parser = SqlDumpCatalog(dump_path)

    with dump_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line.startswith("INSERT INTO `"):
                continue

            table_name, values_text = parser._split_insert_line(line)
            if table_name == "Items":
                for row in parser._parse_values(values_text):
                    item_id = parser._as_int(row[0])
                    if item_id is None:
                        continue
                    items[item_id] = CatalogBook(
                        item_id=item_id,
                        title=parser._as_str(row[1]) or f"Item {item_id}",
                        errata=parser._as_str(row[2]),
                        thanks=parser._as_str(row[3]),
                        material_type_id=parser._as_int(row[4]),
                    )
            elif table_name == "Items_Descriptions":
                for row in parser._parse_values(values_text):
                    item_id = parser._as_int(row[0])
                    description = parser._as_str(row[2])
                    if item_id is not None and description and item_id not in descriptions:
                        descriptions[item_id] = description
            elif table_name == "Material_Types":
                for row in parser._parse_values(values_text):
                    material_type_id = parser._as_int(row[0])
                    if material_type_id is None:
                        continue
                    material_types[material_type_id] = parser._as_str(row[1]) or "Unknown"
            elif table_name == "File_Types":
                for row in parser._parse_values(values_text):
                    file_type_id = parser._as_int(row[0])
                    if file_type_id is None:
                        continue
                    file_types[file_type_id] = parser._as_str(row[1]) or "Unknown"
            elif table_name == "People":
                for row in parser._parse_values(values_text):
                    person_id = parser._as_int(row[0])
                    if person_id is None:
                        continue
                    parts = [parser._as_str(row[1]) or "", parser._as_str(row[2]) or "", parser._as_str(row[3]) or ""]
                    people[person_id] = " ".join(part for part in parts if part).strip()
            elif table_name == "Roles":
                for row in parser._parse_values(values_text):
                    role_id = parser._as_int(row[0])
                    if role_id is None:
                        continue
                    roles[role_id] = parser._as_str(row[1]) or "Unknown"
            elif table_name == "Files":
                for row in parser._parse_values(values_text):
                    file_id = parser._as_int(row[0])
                    if file_id is None:
                        continue
                    files[file_id] = CatalogFile(
                        file_id=file_id,
                        name=parser._as_str(row[1]),
                        path=parser._as_str(row[2]),
                        url=_build_file_url(parser._as_str(row[2])),
                        description=parser._as_str(row[3]),
                        file_type_id=parser._as_int(row[4]),
                    )
            elif table_name == "Items_AltTitles":
                for row in parser._parse_values(values_text):
                    item_id = parser._as_int(row[0])
                    alt_title = parser._as_str(row[1])
                    if item_id is None or not alt_title:
                        continue
                    alt_titles.setdefault(item_id, []).append(alt_title)
            elif table_name == "Items_Creators":
                for row in parser._parse_values(values_text):
                    item_id = parser._as_int(row[1])
                    person_id = parser._as_int(row[2])
                    role_id = parser._as_int(row[3])
                    if item_id is None or person_id is None:
                        continue
                    creator_refs_by_item.setdefault(item_id, []).append((person_id, role_id))
            elif table_name == "Series":
                for row in parser._parse_values(values_text):
                    series_id = parser._as_int(row[0])
                    if series_id is None:
                        continue
                    series[series_id] = CatalogSeries(
                        series_id=series_id,
                        title=parser._as_str(row[1]) or f"Series {series_id}",
                        description=parser._as_str(row[2]),
                    )
            elif table_name == "Series_AltTitles":
                for row in parser._parse_values(values_text):
                    series_id = parser._as_int(row[0])
                    alt_title = parser._as_str(row[1])
                    if series_id is None or not alt_title:
                        continue
                    series_alt_titles.setdefault(series_id, []).append(alt_title)
            elif table_name == "Series_Bibliography":
                for row in parser._parse_values(values_text):
                    series_id = parser._as_int(row[0])
                    item_id = parser._as_int(row[1])
                    if series_id is None or item_id is None:
                        continue
                    series_books.setdefault(series_id, []).append(item_id)
            elif table_name == "Series_Files":
                for row in parser._parse_values(values_text):
                    series_id = parser._as_int(row[0])
                    file_id = parser._as_int(row[1])
                    if series_id is None or file_id is None:
                        continue
                    series_files.setdefault(series_id, []).append(file_id)
            elif table_name == "Items_Files":
                for row in parser._parse_values(values_text):
                    item_id = parser._as_int(row[0])
                    file_id = parser._as_int(row[1])
                    if item_id is None or file_id is None:
                        continue
                    item_files.setdefault(item_id, []).append(file_id)

    hydrated_items: Dict[int, CatalogBook] = {}
    for item_id, item in items.items():
        creators = [
            CatalogCreator(
                person_id=person_id,
                name=people.get(person_id, f"Person {person_id}"),
                role_id=role_id,
                role_name=roles.get(role_id) if role_id is not None else None,
            )
            for person_id, role_id in creator_refs_by_item.get(item_id, [])
        ]
        hydrated_items[item_id] = CatalogBook(
            item_id=item.item_id,
            title=item.title,
            material_type_id=item.material_type_id,
            material_type_name=material_types.get(item.material_type_id or -1),
            description=descriptions.get(item_id),
            errata=item.errata,
            thanks=item.thanks,
            alt_titles=alt_titles.get(item_id, []),
            creators=creators,
        )

    hydrated_files: Dict[int, CatalogFile] = {}
    for file_id, file in files.items():
        hydrated_files[file_id] = CatalogFile(
            file_id=file.file_id,
            name=file.name,
            path=file.path,
            url=file.url,
            description=file.description,
            file_type_id=file.file_type_id,
            file_type_name=file_types.get(file.file_type_id or -1),
        )

    hydrated_series: Dict[int, CatalogSeries] = {}
    for series_id, series_item in series.items():
        hydrated_series[series_id] = CatalogSeries(
            series_id=series_item.series_id,
            title=series_item.title,
            description=series_item.description,
            alt_titles=series_alt_titles.get(series_id, []),
            files=[hydrated_files[file_id] for file_id in series_files.get(series_id, []) if file_id in hydrated_files],
        )

    return {
        "items": hydrated_items,
        "series": hydrated_series,
        "series_books": series_books,
        "files": hydrated_files,
        "item_files": item_files,
        "series_files": series_files,
    }


def _compute_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _build_file_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return urljoin(f"{GAMEBOOKS_BASE_URL}/", path.lstrip("/"))


def _build_search_text(book: CatalogBook) -> str:
    creator_names = [creator.name for creator in book.creators]
    creator_roles = [creator.role_name for creator in book.creators if creator.role_name]
    parts = [book.title, *(book.alt_titles or []), *creator_names, *creator_roles]
    return " | ".join(part for part in parts if part)


def _build_series_search_text(series: CatalogSeries) -> str:
    parts = [series.title, *(series.alt_titles or [])]
    return " | ".join(part for part in parts if part)
