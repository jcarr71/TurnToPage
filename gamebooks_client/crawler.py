from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .api import GamebooksApi, GamebooksApiError
from .catalog import CatalogStore
from .models import GamebookBook


@dataclass(frozen=True)
class CrawlSummary:
    scope: str
    started_from_id: int
    last_id_checked: int
    success_count: int
    miss_count: int
    error_count: int
    stop_reason: str


def crawl_catalog(
    api: GamebooksApi,
    catalog: CatalogStore,
    *,
    scope: str,
    start_item: int,
    start_series: int,
    max_item_id: Optional[int],
    max_series_id: Optional[int],
    delay_seconds: float,
    max_miss_streak: int,
    resume: bool,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Dict[str, object]]:
    summaries: Dict[str, Dict[str, object]] = {}

    if scope in {"both", "items"}:
        item_summary = _crawl_items(
            api=api,
            catalog=catalog,
            start_id=start_item,
            max_id=max_item_id,
            delay_seconds=delay_seconds,
            max_miss_streak=max_miss_streak,
            resume=resume,
            progress_cb=progress_cb,
        )
        summaries["items"] = item_summary.__dict__

    if scope in {"both", "series"}:
        series_summary = _crawl_series(
            api=api,
            catalog=catalog,
            start_id=start_series,
            max_id=max_series_id,
            delay_seconds=delay_seconds,
            max_miss_streak=max_miss_streak,
            resume=resume,
            progress_cb=progress_cb,
        )
        summaries["series"] = series_summary.__dict__

    summaries["catalog"] = {
        "items_total": catalog.count_items(),
        "series_total": catalog.count_series(),
    }
    return summaries


def _crawl_items(
    *,
    api: GamebooksApi,
    catalog: CatalogStore,
    start_id: int,
    max_id: Optional[int],
    delay_seconds: float,
    max_miss_streak: int,
    resume: bool,
    progress_cb: Optional[Callable[[str], None]],
) -> CrawlSummary:
    scope = "items"
    next_id, miss_streak = catalog.load_state(scope, start_id) if resume else (start_id, 0)
    started_from = next_id
    success_count = 0
    miss_count = 0
    error_count = 0
    stop_reason = ""
    last_id_checked = next_id - 1

    _progress(progress_cb, f"Starting item crawl at ID {next_id}")

    while True:
        if max_id is not None and next_id > max_id:
            stop_reason = f"Reached max-item-id {max_id}"
            break

        item_url = f"https://gamebooks.org/Item/{next_id}"
        try:
            details = api.fetch_item_details(
                GamebookBook(title=f"Item {next_id}", url=item_url, item_id=next_id)
            )
            catalog.upsert_item(next_id, details)
            success_count += 1
            miss_streak = 0
            if success_count % 25 == 0:
                _progress(progress_cb, f"Items: saved {success_count} (latest ID: {next_id})")
        except GamebooksApiError as exc:
            if _is_not_found_error(exc):
                miss_streak += 1
                miss_count += 1
            else:
                error_count += 1
                _progress(progress_cb, f"Item {next_id} failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            error_count += 1
            _progress(progress_cb, f"Item {next_id} unexpected error: {exc}")

        last_id_checked = next_id
        next_id += 1
        catalog.save_state(scope, next_id, miss_streak)

        if miss_streak >= max_miss_streak:
            stop_reason = f"Miss streak reached {max_miss_streak}"
            break

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    _progress(progress_cb, f"Item crawl finished: {success_count} found, {miss_count} misses, {error_count} errors")
    return CrawlSummary(
        scope=scope,
        started_from_id=started_from,
        last_id_checked=last_id_checked,
        success_count=success_count,
        miss_count=miss_count,
        error_count=error_count,
        stop_reason=stop_reason,
    )


def _crawl_series(
    *,
    api: GamebooksApi,
    catalog: CatalogStore,
    start_id: int,
    max_id: Optional[int],
    delay_seconds: float,
    max_miss_streak: int,
    resume: bool,
    progress_cb: Optional[Callable[[str], None]],
) -> CrawlSummary:
    scope = "series"
    next_id, miss_streak = catalog.load_state(scope, start_id) if resume else (start_id, 0)
    started_from = next_id
    success_count = 0
    miss_count = 0
    error_count = 0
    stop_reason = ""
    last_id_checked = next_id - 1

    _progress(progress_cb, f"Starting series crawl at ID {next_id}")

    while True:
        if max_id is not None and next_id > max_id:
            stop_reason = f"Reached max-series-id {max_id}"
            break

        series_url = f"https://gamebooks.org/Series/{next_id}"
        try:
            details = api.fetch_series_details(series_url)
            catalog.upsert_series(next_id, details)
            success_count += 1
            miss_streak = 0
            if success_count % 25 == 0:
                _progress(progress_cb, f"Series: saved {success_count} (latest ID: {next_id})")
        except GamebooksApiError as exc:
            if _is_not_found_error(exc):
                miss_streak += 1
                miss_count += 1
            else:
                error_count += 1
                _progress(progress_cb, f"Series {next_id} failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            error_count += 1
            _progress(progress_cb, f"Series {next_id} unexpected error: {exc}")

        last_id_checked = next_id
        next_id += 1
        catalog.save_state(scope, next_id, miss_streak)

        if miss_streak >= max_miss_streak:
            stop_reason = f"Miss streak reached {max_miss_streak}"
            break

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    _progress(progress_cb, f"Series crawl finished: {success_count} found, {miss_count} misses, {error_count} errors")
    return CrawlSummary(
        scope=scope,
        started_from_id=started_from,
        last_id_checked=last_id_checked,
        success_count=success_count,
        miss_count=miss_count,
        error_count=error_count,
        stop_reason=stop_reason,
    )


def _is_not_found_error(exc: GamebooksApiError) -> bool:
    return "HTTP 404" in str(exc)


def _progress(progress_cb: Optional[Callable[[str], None]], message: str) -> None:
    if progress_cb is not None:
        progress_cb(message)
