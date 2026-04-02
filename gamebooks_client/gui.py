from __future__ import annotations

import csv
import json
import re
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Dict, List, Optional, Tuple

from .api import GamebooksApi, GamebooksApiError
from .collection import CollectionEntry, CollectionStore, VALID_STATUSES
from .models import (
    GamebookBook,
    GamebookItemDetails,
    GamebookSearchResult,
    GamebookSeriesDetails,
    GamebookSeriesItem,
)

try:
    import io
    import urllib.request
    from PIL import Image, ImageTk  # type: ignore[import]

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Palette & typography
# ---------------------------------------------------------------------------

_STATUS_FG: Dict[str, str] = {
    "have":    "#1b7a3e",
    "want":    "#0d5fa8",
    "missing": "#b71c1c",
    "unknown": "#808080",
}
_STATUS_LABEL: Dict[str, str] = {
    "have":    "✓  Have",
    "want":    "★  Want",
    "missing": "✗  Missing",
    "unknown": "",
}

_FONT  = ("Segoe UI", 10)
_FONTB = ("Segoe UI", 10, "bold")
_FONTH = ("Segoe UI", 12, "bold")
_PAD   = {"padx": 4, "pady": 3}


@dataclass(frozen=True)
class UndoAction:
    label: str
    changes: List[Tuple[int, Optional[CollectionEntry]]]


# ---------------------------------------------------------------------------
# Thread helper — run work() off the GUI thread, callback on GUI thread
# ---------------------------------------------------------------------------

def _async(widget: tk.Misc, work: Callable, done: Callable) -> None:
    def _run() -> None:
        try:
            result = work()
            widget.after(0, lambda: done(result, None))
        except Exception as exc:  # noqa: BLE001
            widget.after(0, lambda: done(None, exc))

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Dashboard Tab
# ---------------------------------------------------------------------------

class DashboardTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        api: GamebooksApi,
        store: CollectionStore,
        set_status: Callable[[str], None],
        register_undo: Callable[[str, List[Tuple[int, Optional[CollectionEntry]]]], None],
        get_activity_log: Callable[[], List[str]],
        open_search: Callable[[], None],
        open_collection: Callable[[], None],
        open_series: Callable[[], None],
        open_series_by_id: Optional[Callable[[int], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._store = store
        self._set_status = set_status
        self._register_undo = register_undo
        self._get_activity_log = get_activity_log
        self._open_search = open_search
        self._open_collection = open_collection
        self._open_series = open_series
        self._open_series_by_id = open_series_by_id
        self._recent_entries = []
        self._series_progress_data: List[Dict] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(header, text="Collection Dashboard", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="Your collection at a glance, plus quick ways to continue tracking.",
            font=_FONT,
            foreground="#555555",
        ).pack(anchor="w", pady=(2, 0))

        cards = ttk.Frame(self)
        cards.pack(fill="x", padx=10, pady=(0, 8))
        cards.columnconfigure((0, 1, 2, 3), weight=1)

        self._total_var = tk.StringVar()
        self._have_var = tk.StringVar()
        self._want_var = tk.StringVar()
        self._missing_var = tk.StringVar()
        self._status_summary_var = tk.StringVar()

        self._create_stat_card(cards, 0, "Tracked", self._total_var, "#404040")
        self._create_stat_card(cards, 1, "Have", self._have_var, _STATUS_FG["have"])
        self._create_stat_card(cards, 2, "Want", self._want_var, _STATUS_FG["want"])
        self._create_stat_card(cards, 3, "Missing", self._missing_var, _STATUS_FG["missing"])

        summary = ttk.LabelFrame(self, text="Overview")
        summary.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(summary, textvariable=self._status_summary_var, font=_FONT, wraplength=820).pack(
            anchor="w", padx=10, pady=10
        )

        actions = ttk.LabelFrame(self, text="Quick Actions")
        actions.pack(fill="x", padx=10, pady=(0, 8))
        action_row = ttk.Frame(actions)
        action_row.pack(fill="x", padx=10, pady=10)
        ttk.Button(action_row, text="Search Books", command=self._open_search).pack(side="left", padx=(0, 6))
        ttk.Button(action_row, text="Browse Collection", command=self._open_collection).pack(side="left", padx=6)
        ttk.Button(action_row, text="Open Series Gap Report", command=self._open_series).pack(side="left", padx=6)
        ttk.Button(action_row, text="Refresh Dashboard", command=self.refresh).pack(side="right")

        activity = ttk.LabelFrame(self, text="Activity Log (This Session)")
        activity.pack(fill="x", padx=10, pady=(0, 8))
        activity_row = ttk.Frame(activity)
        activity_row.pack(fill="x", padx=10, pady=8)
        self._activity_listbox = tk.Listbox(activity_row, height=4, activestyle="none", font=_FONT)
        self._activity_listbox.pack(side="left", fill="x", expand=True)
        activity_scroll = ttk.Scrollbar(activity_row, orient="vertical", command=self._activity_listbox.yview)
        self._activity_listbox.configure(yscrollcommand=activity_scroll.set)
        activity_scroll.pack(side="left", fill="y")

        # Series Progress panel
        sp_frame = ttk.LabelFrame(self, text="Series Progress — Closest to Complete")
        sp_frame.pack(fill="x", padx=10, pady=(0, 8))
        sp_cols = ("series", "have", "total", "pct")
        self._sp_tree = ttk.Treeview(
            sp_frame, columns=sp_cols, show="headings", selectmode="browse", height=5
        )
        self._sp_tree.heading("series", text="Series")
        self._sp_tree.heading("have",   text="Have")
        self._sp_tree.heading("total",  text="Tracked")
        self._sp_tree.heading("pct",    text="Complete %")
        self._sp_tree.column("series", width=360, stretch=True)
        self._sp_tree.column("have",   width=60,  anchor="center", stretch=False)
        self._sp_tree.column("total",  width=80,  anchor="center", stretch=False)
        self._sp_tree.column("pct",    width=160, anchor="center", stretch=False)
        sp_vsb = ttk.Scrollbar(sp_frame, orient="vertical", command=self._sp_tree.yview)
        self._sp_tree.configure(yscrollcommand=sp_vsb.set)
        self._sp_tree.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=6)
        sp_vsb.pack(side="left", fill="y", pady=6, padx=(0, 8))
        self._sp_tree.bind("<Double-1>", lambda _e: self._open_series_from_progress())
        self._sp_empty_label = ttk.Label(
            sp_frame,
            text="Mark books in the Series Gap Report to see progress here.",
            font=_FONT,
            foreground="#888888",
        )

        # Suggestions panel — series you've started but not finished
        sug_frame = ttk.LabelFrame(self, text="Suggested — Series to Finish (fewest still needed)")
        sug_frame.pack(fill="x", padx=10, pady=(0, 8))
        sug_cols = ("series", "have", "need")
        self._sug_tree = ttk.Treeview(
            sug_frame, columns=sug_cols, show="headings", selectmode="browse", height=4
        )
        self._sug_tree.heading("series", text="Series")
        self._sug_tree.heading("have",   text="Have")
        self._sug_tree.heading("need",   text="Still Need")
        self._sug_tree.column("series", width=400, stretch=True)
        self._sug_tree.column("have",   width=70,  anchor="center", stretch=False)
        self._sug_tree.column("need",   width=100, anchor="center", stretch=False)
        sug_vsb = ttk.Scrollbar(sug_frame, orient="vertical", command=self._sug_tree.yview)
        self._sug_tree.configure(yscrollcommand=sug_vsb.set)
        self._sug_tree.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=6)
        sug_vsb.pack(side="left", fill="y", pady=6, padx=(0, 8))
        self._sug_tree.bind("<Double-1>", lambda _e: self._open_series_from_suggestions())
        self._sug_empty_label = ttk.Label(
            sug_frame,
            text="Mark some books in a series as 'have' and others as 'missing' to see suggestions here.",
            font=_FONT,
            foreground="#888888",
        )
        self._suggestions_data: List[Dict] = []

        recent = ttk.LabelFrame(self, text="Recently Changed")
        recent.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        cols = ("title", "status", "updated")
        self._tree = ttk.Treeview(recent, columns=cols, show="headings", selectmode="browse")
        self._tree.heading("title", text="Title")
        self._tree.heading("status", text="Status")
        self._tree.heading("updated", text="Updated")
        self._tree.column("title", width=460, stretch=True)
        self._tree.column("status", width=120, anchor="center", stretch=False)
        self._tree.column("updated", width=180, anchor="center", stretch=False)
        for status, color in _STATUS_FG.items():
            self._tree.tag_configure(status, foreground=color)
        vsb = ttk.Scrollbar(recent, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        vsb.pack(side="left", fill="y", pady=10, padx=(0, 10))
        self._tree.bind("<Double-1>", lambda _e: self._open_selected_recent())

    def _create_stat_card(
        self,
        parent: ttk.Frame,
        column: int,
        title: str,
        value_var: tk.StringVar,
        foreground: str,
    ) -> None:
        card = ttk.LabelFrame(parent, text=title)
        card.grid(row=0, column=column, sticky="nsew", padx=4, pady=2)
        ttk.Label(card, textvariable=value_var, font=("Segoe UI", 22, "bold"), foreground=foreground).pack(
            anchor="center", padx=18, pady=(10, 2)
        )
        ttk.Label(card, text=title.lower(), font=_FONT, foreground="#666666").pack(anchor="center", pady=(0, 10))

    def refresh(self) -> None:
        counts = self._store.summary_counts()
        self._recent_entries = self._store.recent_entries(limit=12)

        self._total_var.set(str(counts["total"]))
        self._have_var.set(str(counts["have"]))
        self._want_var.set(str(counts["want"]))
        self._missing_var.set(str(counts["missing"]))

        if counts["total"] == 0:
            summary = "No books are tracked yet. Start with Search to find a title or series, then mark items as have, want, or missing."
        else:
            have_pct = int((counts["have"] / counts["total"]) * 100) if counts["total"] else 0
            summary = (
                f"You are tracking {counts['total']} books. "
                f"{counts['have']} owned, {counts['want']} wanted, and {counts['missing']} marked missing. "
                f"Current owned progress: {have_pct}%."
            )
        self._status_summary_var.set(summary)

        self._tree.delete(*self._tree.get_children())
        for entry in self._recent_entries:
            self._tree.insert(
                "",
                "end",
                values=(entry.title, _STATUS_LABEL.get(entry.status, entry.status), entry.updated_at),
                tags=(entry.status,),
            )

        self._activity_listbox.delete(0, "end")
        for line in self._get_activity_log()[-12:]:
            self._activity_listbox.insert("end", line)
        if self._activity_listbox.size() > 0:
            self._activity_listbox.yview_moveto(1.0)

        self._series_progress_data = self._store.series_progress()
        self._sp_tree.delete(*self._sp_tree.get_children())
        if self._series_progress_data:
            self._sp_empty_label.pack_forget()
            for row in self._series_progress_data:
                bar_filled = "█" * (row["pct"] // 10)
                bar_empty  = "░" * (10 - row["pct"] // 10)
                pct_display = f"{row['pct']}%  {bar_filled}{bar_empty}"
                self._sp_tree.insert(
                    "", "end",
                    values=(row["series_title"], row["have"], row["total"], pct_display),
                )
        else:
            self._sp_empty_label.pack(padx=10, pady=6)

        self._suggestions_data = self._store.suggestions()
        self._sug_tree.delete(*self._sug_tree.get_children())
        if self._suggestions_data:
            self._sug_empty_label.pack_forget()
            for row in self._suggestions_data:
                self._sug_tree.insert(
                    "", "end",
                    values=(row["series_title"], row["have"], row["need"]),
                )
        else:
            self._sug_empty_label.pack(padx=10, pady=6)

    def _open_series_from_suggestions(self) -> None:
        sel = self._sug_tree.selection()
        if not sel:
            return
        idx = self._sug_tree.index(sel[0])
        if idx >= len(self._suggestions_data):
            return
        row = self._suggestions_data[idx]
        if self._open_series_by_id:
            self._open_series_by_id(row["series_id"])

    def _open_series_from_progress(self) -> None:
        sel = self._sp_tree.selection()
        if not sel:
            return
        idx = self._sp_tree.index(sel[0])
        if idx >= len(self._series_progress_data):
            return
        row = self._series_progress_data[idx]
        if self._open_series_by_id:
            self._open_series_by_id(row["series_id"])

    def _open_selected_recent(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        index = self._tree.index(selection[0])
        if index >= len(self._recent_entries):
            return
        entry = self._recent_entries[index]
        DetailWindow(
            self,
            self._api,
            self._store,
            entry.item_id,
            entry.title,
            entry.url,
            self._set_status,
            self._register_undo,
            self._open_series,
            on_close=self.refresh,
        )


# ---------------------------------------------------------------------------
# Search Tab
# ---------------------------------------------------------------------------

class SearchTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        api: GamebooksApi,
        store: CollectionStore,
        set_status: Callable[[str], None],
        open_series: Callable[[int], None],
        register_undo: Callable[[str, List[Tuple[int, Optional[CollectionEntry]]]], None],
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._store = store
        self._set_status = set_status
        self._open_series = open_series
        self._register_undo = register_undo
        self._results: List[GamebookSearchResult] = []
        self._visible_results: List[GamebookSearchResult] = []
        self._series_book_counts: Dict[int, int] = {}
        self._search_token = 0
        self._build()

    # ── layout ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Search bar
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text="Search:", font=_FONTB).pack(side="left")
        self._query_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self._query_var, font=_FONT, width=44)
        entry.pack(side="left", padx=(6, 4))
        entry.bind("<Return>", lambda _e: self._do_search())
        entry.focus_set()
        ttk.Button(bar, text="Search", command=self._do_search).pack(side="left")

        filter_bar = ttk.Frame(self)
        filter_bar.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(filter_bar, text="Filter:", font=_FONTB).pack(side="left")
        self._text_filter_var = tk.StringVar()
        text_filter_entry = ttk.Entry(filter_bar, textvariable=self._text_filter_var, width=30)
        text_filter_entry.pack(side="left", padx=(6, 12))
        text_filter_entry.bind("<KeyRelease>", lambda _e: self._apply_filters())

        ttk.Label(filter_bar, text="Status:", font=_FONTB).pack(side="left")
        self._status_filter_var = tk.StringVar(value="all")
        status_filter = ttk.Combobox(
            filter_bar,
            textvariable=self._status_filter_var,
            values=["all", "have", "want", "missing", "unknown"],
            width=12,
            state="readonly",
        )
        status_filter.pack(side="left", padx=(6, 0))
        status_filter.bind("<<ComboboxSelected>>", lambda _e: self._apply_filters())

        ttk.Label(filter_bar, text="Sort:", font=_FONTB).pack(side="left", padx=(16, 4))
        self._sort_var = tk.StringVar(value="title-asc")
        sort_filter = ttk.Combobox(
            filter_bar,
            textvariable=self._sort_var,
            values=["title-asc", "title-desc", "id-asc", "id-desc", "books-desc", "status"],
            width=12,
            state="readonly",
        )
        sort_filter.pack(side="left", padx=(6, 0))
        sort_filter.bind("<<ComboboxSelected>>", lambda _e: self._apply_filters())

        # Presets row
        preset_bar = ttk.Frame(self)
        preset_bar.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(preset_bar, text="Preset:", font=_FONTB).pack(side="left")
        self._preset_var = tk.StringVar()
        self._preset_combo = ttk.Combobox(
            preset_bar,
            textvariable=self._preset_var,
            width=24,
            state="readonly",
        )
        self._preset_combo.pack(side="left", padx=(6, 4))
        ttk.Button(preset_bar, text="Load",   command=self._load_preset).pack(side="left", padx=2)
        ttk.Button(preset_bar, text="Save…",  command=self._save_preset).pack(side="left", padx=2)
        ttk.Button(preset_bar, text="Delete", command=self._delete_preset).pack(side="left", padx=2)
        self._refresh_preset_list()

        # Results pane
        pane = ttk.Frame(self)
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        cols = ("title", "type", "id", "books", "status")
        self._tree = ttk.Treeview(pane, columns=cols, show="headings", selectmode="extended")
        self._tree.heading("title",  text="Title")
        self._tree.heading("type",   text="Type")
        self._tree.heading("id",     text="ID")
        self._tree.heading("books",  text="Books")
        self._tree.heading("status", text="My Status")
        self._tree.column("title",  width=430, stretch=True)
        self._tree.column("type",   width=80,  anchor="center", stretch=False)
        self._tree.column("id",     width=60,  anchor="center", stretch=False)
        self._tree.column("books",  width=70,  anchor="center", stretch=False)
        self._tree.column("status", width=110, anchor="center", stretch=False)
        for s, fg in _STATUS_FG.items():
            self._tree.tag_configure(s, foreground=fg)

        vsb = ttk.Scrollbar(pane, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self._tree.bind("<Double-1>", lambda _e: self._on_double_click())

        # Action buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=6)
        self._btn_have    = ttk.Button(btn_frame, text="✓  Mark Have",    command=lambda: self._mark("have"))
        self._btn_want    = ttk.Button(btn_frame, text="★  Mark Want",    command=lambda: self._mark("want"))
        self._btn_missing = ttk.Button(btn_frame, text="✗  Mark Missing", command=lambda: self._mark("missing"))
        self._btn_unmark  = ttk.Button(btn_frame, text="Remove",          command=self._unmark)
        self._btn_details = ttk.Button(btn_frame, text="View Details",    command=self._view_details)
        self._btn_series  = ttk.Button(btn_frame, text="Series Report →", command=self._go_series)
        for btn in (self._btn_have, self._btn_want, self._btn_missing,
                    self._btn_unmark, self._btn_details, self._btn_series):
            btn.pack(side="left", padx=3)
            btn.state(["disabled"])

        self._confirm_bulk_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            btn_frame,
            text="Confirm bulk updates",
            variable=self._confirm_bulk_var,
        ).pack(side="right", padx=4)

    # ── preset helpers ───────────────────────────────────────────────────────

    def _refresh_preset_list(self) -> None:
        names = [p["name"] for p in self._store.list_presets()]
        self._preset_combo.configure(values=names)
        if names and self._preset_var.get() not in names:
            self._preset_var.set("")

    def _load_preset(self) -> None:
        name = self._preset_var.get()
        if not name:
            return
        for p in self._store.list_presets():
            if p["name"] == name:
                self._text_filter_var.set(p["text_filter"])
                self._status_filter_var.set(p["status_filter"])
                self._sort_var.set(p["sort"])
                self._apply_filters()
                self._set_status(f"Loaded preset '{name}'.")
                return

    def _save_preset(self) -> None:
        name = simpledialog.askstring(
            "Save Preset",
            "Enter a name for this filter preset:",
            parent=self,
            initialvalue=self._preset_var.get() or "",
        )
        if not name or not name.strip():
            return
        self._store.save_preset(
            name.strip(),
            self._text_filter_var.get(),
            self._status_filter_var.get(),
            self._sort_var.get(),
        )
        self._refresh_preset_list()
        self._preset_var.set(name.strip())
        self._set_status(f"Saved preset '{name.strip()}'.")

    def _delete_preset(self) -> None:
        name = self._preset_var.get()
        if not name:
            return
        self._store.delete_preset(name)
        self._refresh_preset_list()
        self._set_status(f"Deleted preset '{name}'.")

    # ── actions ─────────────────────────────────────────────────────────────

    def _do_search(self) -> None:
        q = self._query_var.get().strip()
        if not q:
            return
        self._search_token += 1
        token = self._search_token
        self._set_status(f'Searching for "{q}" …')
        self._tree.delete(*self._tree.get_children())
        self._results = []
        self._visible_results = []
        self._series_book_counts = {}
        _async(
            self,
            lambda: self._api.search_books(q),
            lambda results, err: self._on_search_done(token, results, err),
        )

    def _on_search_done(
        self,
        token: int,
        results: Optional[List[GamebookSearchResult]],
        err: Optional[Exception],
    ) -> None:
        if token != self._search_token:
            return
        if err:
            self._set_status(f"Search failed: {err}")
            messagebox.showerror("Search Error", str(err))
            return

        self._results = results or []
        series_ids: List[int] = []

        for r in self._results:
            if r.is_series and r.item_id is not None:
                series_ids.append(r.item_id)

        self._set_status(f"{len(self._results)} result(s) found.")
        self._apply_filters()
        if series_ids:
            _async(
                self,
                lambda: self._fetch_series_counts(series_ids),
                lambda counts, counts_err: self._on_series_counts_done(token, counts, counts_err),
            )

    def _fetch_series_counts(self, series_ids: List[int]) -> Dict[int, int]:
        counts: Dict[int, int] = {}
        for series_id in sorted(set(series_ids)):
            try:
                details = self._api.fetch_series_details(f"https://gamebooks.org/Series/{series_id}")
                counts[series_id] = details.total_count
            except GamebooksApiError:
                continue
        return counts

    def _on_series_counts_done(
        self,
        token: int,
        counts: Optional[Dict[int, int]],
        err: Optional[Exception],
    ) -> None:
        if token != self._search_token or err or not counts:
            return

        self._series_book_counts.update(counts)
        self._apply_filters()

    def _result_status(self, result: GamebookSearchResult) -> str:
        if result.item_id is None:
            return "unknown"
        return self._store.status_map([result.item_id]).get(result.item_id, "unknown")

    def _apply_filters(self) -> None:
        text_query = self._text_filter_var.get().strip().lower()
        status_query = self._status_filter_var.get().strip().lower()
        sort_query = self._sort_var.get().strip().lower()

        selected_item_id: Optional[int] = None
        current = self._selected()
        if current is not None:
            selected_item_id = current.item_id

        tracked = self._store.status_map([r.item_id for r in self._results if r.item_id is not None])
        filtered: List[GamebookSearchResult] = []
        for result in self._results:
            status = tracked.get(result.item_id, "unknown") if result.item_id is not None else "unknown"
            if status_query and status_query != "all" and status != status_query:
                continue
            if text_query and text_query not in result.title.lower():
                continue
            filtered.append(result)

        status_rank = {"have": 0, "want": 1, "missing": 2, "unknown": 3}
        if sort_query == "title-desc":
            filtered.sort(key=lambda r: r.title.lower(), reverse=True)
        elif sort_query == "id-asc":
            filtered.sort(key=lambda r: (r.item_id is None, r.item_id or 0, r.title.lower()))
        elif sort_query == "id-desc":
            filtered.sort(key=lambda r: (r.item_id or -1), reverse=True)
        elif sort_query == "books-desc":
            filtered.sort(
                key=lambda r: (
                    self._series_book_counts.get(r.item_id or -1, -1) if r.is_series else -1,
                    r.title.lower(),
                ),
                reverse=True,
            )
        elif sort_query == "status":
            filtered.sort(
                key=lambda r: (
                    status_rank.get(tracked.get(r.item_id, "unknown") if r.item_id is not None else "unknown", 99),
                    r.title.lower(),
                )
            )
        else:
            filtered.sort(key=lambda r: r.title.lower())

        self._visible_results = filtered
        self._tree.delete(*self._tree.get_children())

        selected_iid: Optional[str] = None
        for result in self._visible_results:
            status = tracked.get(result.item_id, "unknown") if result.item_id is not None else "unknown"
            books_display = ""
            if result.is_series and result.item_id is not None:
                books_display = str(self._series_book_counts.get(result.item_id, "…"))
            type_display = "Series" if result.is_series else "Book"
            iid = self._tree.insert(
                "",
                "end",
                values=(result.title, type_display, result.item_id or "", books_display, _STATUS_LABEL.get(status, "")),
                tags=(status,),
            )
            if selected_item_id is not None and result.item_id == selected_item_id:
                selected_iid = iid

        if selected_iid is not None:
            self._tree.selection_set(selected_iid)
            self._tree.focus(selected_iid)

    def _selected_items(self) -> List[GamebookSearchResult]:
        out = []
        for iid in self._tree.selection():
            idx = self._tree.index(iid)
            if idx < len(self._visible_results):
                out.append(self._visible_results[idx])
        return out

    def _selected(self) -> Optional[GamebookSearchResult]:
        """Return the single focused result, or None if multi-select or empty."""
        items = self._selected_items()
        return items[0] if len(items) == 1 else None

    def _on_select(self) -> None:
        items = self._selected_items()
        has_any  = bool(items)
        has_item = any(r.item_id is not None for r in items)
        single   = len(items) == 1
        r        = items[0] if single else None
        has_series = single and r is not None and r.is_series
        for btn in (self._btn_have, self._btn_want, self._btn_missing, self._btn_unmark):
            btn.state(["!disabled"] if has_item else ["disabled"])
        self._btn_details.state(["!disabled"] if (single and has_item) else ["disabled"])
        self._btn_series.state(["!disabled"] if has_series else ["disabled"])

    def _mark(self, status: str) -> None:
        items = self._selected_items()
        bookable = [r for r in items if r.item_id is not None and not r.is_series]
        series   = [r for r in items if r.item_id is not None and r.is_series]
        # Series can also be marked, include them if explicitly clicked
        targets  = [r for r in items if r.item_id is not None]
        if not targets:
            return
        if len(targets) > 1 and self._confirm_bulk_var.get():
            if not messagebox.askyesno(
                "Confirm Bulk Update",
                f"Apply status '{status}' to {len(targets)} selected item(s)?",
            ):
                self._set_status("Bulk update cancelled.")
                return
        changes: List[Tuple[int, Optional[CollectionEntry]]] = []
        for r in targets:
            changes.append((r.item_id, self._store.get(r.item_id)))
            self._store.set_status(item_id=r.item_id, status=status, title=r.title, url=r.url)
        self._register_undo(f"Mark {len(targets)} item(s) as {status}", changes)
        # Refresh visible rows in-place
        for iid in self._tree.selection():
            idx = self._tree.index(iid)
            if idx < len(self._visible_results):
                r = self._visible_results[idx]
                if r.item_id is not None:
                    row_values = list(self._tree.item(iid, "values"))
                    type_display = row_values[1] if len(row_values) >= 2 else ("Series" if r.is_series else "Book")
                    books_value  = row_values[3] if len(row_values) >= 4 else ""
                    self._tree.item(iid, values=(r.title, type_display, r.item_id, books_value, _STATUS_LABEL[status]), tags=(status,))
        self._set_status(f"Marked {len(targets)} item(s) as {status}.")

    def _unmark(self) -> None:
        items   = [r for r in self._selected_items() if r.item_id is not None]
        if not items:
            return
        if len(items) > 1 and self._confirm_bulk_var.get():
            if not messagebox.askyesno(
                "Confirm Bulk Remove",
                f"Remove {len(items)} selected item(s) from collection?",
            ):
                self._set_status("Bulk remove cancelled.")
                return
        changes: List[Tuple[int, Optional[CollectionEntry]]] = []
        for r in items:
            changes.append((r.item_id, self._store.get(r.item_id)))
            self._store.remove(r.item_id)
        self._register_undo(f"Remove {len(items)} item(s)", changes)
        for iid in self._tree.selection():
            idx = self._tree.index(iid)
            if idx < len(self._visible_results):
                r = self._visible_results[idx]
                if r.item_id is not None:
                    row_values = list(self._tree.item(iid, "values"))
                    type_display = row_values[1] if len(row_values) >= 2 else ("Series" if r.is_series else "Book")
                    books_value  = row_values[3] if len(row_values) >= 4 else ""
                    self._tree.item(iid, values=(r.title, type_display, r.item_id, books_value, ""), tags=("unknown",))
        self._set_status(f"Removed {len(items)} item(s) from collection.")

    def _on_double_click(self) -> None:
        r = self._selected()
        if r is None:
            return
        if r.is_series:
            self._go_series()
        else:
            self._view_details()

    def _view_details(self) -> None:
        r = self._selected()
        if r is None or r.item_id is None:
            return
        DetailWindow(
            self,
            self._api,
            self._store,
            r.item_id,
            r.title,
            r.url,
            self._set_status,
            self._register_undo,
            self._open_series,
        )

    def _go_series(self) -> None:
        r = self._selected()
        if r is None:
            return
        m = re.search(r"/Series/(\d+)", r.url)
        if m:
            self._open_series(int(m.group(1)))

    def quick_mark(self, status: str) -> bool:
        items = [r for r in self._selected_items() if r.item_id is not None]
        if not items:
            return False
        self._mark(status)
        return True

    def quick_unmark(self) -> bool:
        items = [r for r in self._selected_items() if r.item_id is not None]
        if not items:
            return False
        self._unmark()
        return True


# ---------------------------------------------------------------------------
# Collection Tab
# ---------------------------------------------------------------------------

class CollectionTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        store: CollectionStore,
        api: GamebooksApi,
        set_status: Callable[[str], None],
        register_undo: Callable[[str, List[Tuple[int, Optional[CollectionEntry]]]], None],
        open_series: Callable[[int], None],
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._api = api
        self._set_status = set_status
        self._register_undo = register_undo
        self._open_series = open_series
        self._all_entries: list = []
        self._entries: list = []
        self._iid_to_entry: Dict[str, CollectionEntry] = {}
        self._build()

    def _build(self) -> None:
        # Filter bar
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text="Show:", font=_FONTB).pack(side="left")
        self._filter_var = tk.StringVar(value="all")
        for label, value in (("All", "all"), ("Have", "have"), ("Want", "want"), ("Missing", "missing")):
            ttk.Radiobutton(
                bar, text=label, variable=self._filter_var, value=value, command=self.refresh
            ).pack(side="left", padx=4)

        ttk.Label(bar, text="Filter:", font=_FONTB).pack(side="left", padx=(16, 4))
        self._text_filter_var = tk.StringVar()
        text_filter_entry = ttk.Entry(bar, textvariable=self._text_filter_var, width=28)
        text_filter_entry.pack(side="left")
        text_filter_entry.bind("<KeyRelease>", lambda _e: self._apply_text_filter())

        ttk.Label(bar, text="Sort:", font=_FONTB).pack(side="left", padx=(12, 4))
        self._sort_var = tk.StringVar(value="updated-desc")
        sort_filter = ttk.Combobox(
            bar,
            textvariable=self._sort_var,
            values=["updated-desc", "updated-asc", "title-asc", "title-desc", "status"],
            width=12,
            state="readonly",
        )
        sort_filter.pack(side="left")
        sort_filter.bind("<<ComboboxSelected>>", lambda _e: self._apply_text_filter())
        ttk.Button(bar, text="⟳ Refresh", command=self.refresh).pack(side="right")

        self._group_series_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar,
            text="Group by series",
            variable=self._group_series_var,
            command=self._apply_text_filter,
        ).pack(side="right", padx=4)

        # Treeview
        pane = ttk.Frame(self)
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        cols = ("title", "status", "id", "updated")
        self._tree = ttk.Treeview(pane, columns=cols, show="headings", selectmode="browse")
        self._tree.heading("title",   text="Title")
        self._tree.heading("status",  text="Status")
        self._tree.heading("id",      text="ID")
        self._tree.heading("updated", text="Updated")
        self._tree.column("title",   width=420, stretch=True)
        self._tree.column("status",  width=100, anchor="center", stretch=False)
        self._tree.column("id",      width=60,  anchor="center", stretch=False)
        self._tree.column("updated", width=180, anchor="center", stretch=False)
        for s, fg in _STATUS_FG.items():
            self._tree.tag_configure(s, foreground=fg)
        self._tree.tag_configure("series_header", foreground="#888888", font=_FONTB)
        vsb = ttk.Scrollbar(pane, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self._tree.bind("<Double-1>", lambda _e: self._view_details())

        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=6)
        self._btn_export  = ttk.Button(btn_frame, text="Export CSV",      command=self._export_csv)
        self._btn_export_json = ttk.Button(btn_frame, text="Export JSON", command=self._export_json)
        self._btn_details = ttk.Button(btn_frame, text="View Details",    command=self._view_details)
        self._btn_series  = ttk.Button(btn_frame, text="Jump To Series",  command=self._jump_to_series)
        self._btn_have    = ttk.Button(btn_frame, text="✓  Have",         command=lambda: self._change("have"))
        self._btn_want    = ttk.Button(btn_frame, text="★  Want",         command=lambda: self._change("want"))
        self._btn_missing = ttk.Button(btn_frame, text="✗  Missing",      command=lambda: self._change("missing"))
        self._btn_remove  = ttk.Button(btn_frame, text="Remove",          command=self._remove)
        for btn in (self._btn_export, self._btn_export_json, self._btn_details, self._btn_series, self._btn_have, self._btn_want, self._btn_missing, self._btn_remove):
            btn.pack(side="left", padx=3)
        self._btn_export.state(["disabled"])
        self._btn_export_json.state(["disabled"])
        for btn in (self._btn_details, self._btn_series, self._btn_have, self._btn_want, self._btn_missing, self._btn_remove):
            btn.state(["disabled"])
        self._count_var = tk.StringVar()
        ttk.Label(btn_frame, textvariable=self._count_var, font=_FONT).pack(side="right", padx=8)

        self.refresh()

    def refresh(self) -> None:
        filt = self._filter_var.get()
        status_arg = None if filt == "all" else filt
        self._all_entries = self._store.list_entries(status_arg)
        self._apply_text_filter()

    def _apply_text_filter(self) -> None:
        text_query = self._text_filter_var.get().strip().lower()
        sort_query = self._sort_var.get().strip().lower()

        selected_entry_id: Optional[int] = None
        selected = self._selected()
        if selected is not None:
            selected_entry_id = selected.item_id

        if text_query:
            self._entries = [
                entry
                for entry in self._all_entries
                if text_query in entry.title.lower() or text_query in str(entry.item_id)
            ]
        else:
            self._entries = list(self._all_entries)

        status_rank = {"have": 0, "want": 1, "missing": 2, "unknown": 3}
        if sort_query == "updated-asc":
            self._entries.sort(key=lambda e: (e.updated_at, e.title.lower()))
        elif sort_query == "title-asc":
            self._entries.sort(key=lambda e: e.title.lower())
        elif sort_query == "title-desc":
            self._entries.sort(key=lambda e: e.title.lower(), reverse=True)
        elif sort_query == "status":
            self._entries.sort(key=lambda e: (status_rank.get(e.status, 99), e.title.lower()))
        else:
            self._entries.sort(key=lambda e: (e.updated_at, e.title.lower()), reverse=True)

        self._tree.delete(*self._tree.get_children())
        self._iid_to_entry = {}
        selected_iid: Optional[str] = None

        if self._group_series_var.get():
            # Sort by (series_title or "~No Series", title) so ungrouped falls last
            grouped = sorted(
                self._entries,
                key=lambda e: (
                    e.series_title.lower() if e.series_title else "\xff",
                    e.title.lower(),
                ),
            )
            current_series: Optional[str] = object()  # sentinel
            for e in grouped:
                series_label = e.series_title if e.series_title else "No Series"
                if series_label != current_series:
                    current_series = series_label
                    self._tree.insert(
                        "", "end",
                        values=(f"── {series_label} ──", "", "", ""),
                        tags=("series_header",),
                    )
                iid = self._tree.insert(
                    "", "end",
                    values=(e.title, _STATUS_LABEL.get(e.status, e.status), e.item_id, e.updated_at),
                    tags=(e.status,),
                )
                self._iid_to_entry[iid] = e
                if selected_entry_id is not None and e.item_id == selected_entry_id:
                    selected_iid = iid
        else:
            for e in self._entries:
                iid = self._tree.insert(
                    "", "end",
                    values=(e.title, _STATUS_LABEL.get(e.status, e.status), e.item_id, e.updated_at),
                    tags=(e.status,),
                )
                self._iid_to_entry[iid] = e
                if selected_entry_id is not None and e.item_id == selected_entry_id:
                    selected_iid = iid

        if selected_iid is not None:
            self._tree.selection_set(selected_iid)
            self._tree.focus(selected_iid)

        self._count_var.set(f"{len(self._entries)} item(s)")
        if self._entries:
            self._btn_export.state(["!disabled"])
            self._btn_export_json.state(["!disabled"])
        else:
            self._btn_export.state(["disabled"])
            self._btn_export_json.state(["disabled"])
        for btn in (self._btn_details, self._btn_series, self._btn_have, self._btn_want, self._btn_missing, self._btn_remove):
            btn.state(["disabled"])

    def _selected(self):
        sel = self._tree.selection()
        if not sel:
            return None
        return self._iid_to_entry.get(sel[0])

    def _on_select(self) -> None:
        e = self._selected()
        state = ["!disabled"] if e else ["disabled"]
        for btn in (self._btn_details, self._btn_series, self._btn_have, self._btn_want, self._btn_missing, self._btn_remove):
            btn.state(state)

    def _change(self, status: str) -> None:
        e = self._selected()
        if e is None:
            return
        previous = self._store.get(e.item_id)
        self._store.set_status(item_id=e.item_id, status=status, title=e.title, url=e.url)
        self._register_undo(f'Mark "{e.title}" as {status}', [(e.item_id, previous)])
        self._set_status(f'Marked "{e.title}" as {status}.')
        self.refresh()

    def _remove(self) -> None:
        e = self._selected()
        if e is None:
            return
        if messagebox.askyesno("Remove", f'Remove "{e.title}" from your collection?'):
            previous = self._store.get(e.item_id)
            self._store.remove(e.item_id)
            self._register_undo(f'Remove "{e.title}"', [(e.item_id, previous)])
            self._set_status(f'Removed "{e.title}".')
            self.refresh()

    def _view_details(self) -> None:
        e = self._selected()
        if e is None:
            return
        DetailWindow(
            self, self._api, self._store,
            e.item_id, e.title, e.url, self._set_status,
            self._register_undo,
            self._open_series,
            on_close=self.refresh,
        )

    def _jump_to_series(self) -> None:
        e = self._selected()
        if e is None:
            return

        try:
            details = self._api.fetch_item_details(
                GamebookBook(title=e.title, url=e.url, item_id=e.item_id)
            )
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Failed to find series for '{e.title}': {exc}")
            return

        if details.series_id is None:
            self._set_status(f"No series found for '{e.title}'.")
            return

        self._open_series(details.series_id)
        self._set_status(f"Opened series {details.series_id} for '{e.title}'.")

    def _export_csv(self) -> None:
        if not self._entries:
            self._set_status("No entries to export.")
            return

        output_path = filedialog.asksaveasfilename(
            title="Export Collection CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="turntopage_collection.csv",
        )
        if not output_path:
            self._set_status("CSV export cancelled.")
            return

        try:
            with open(output_path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["item_id", "title", "status", "url", "updated_at"])
                for entry in self._entries:
                    writer.writerow([entry.item_id, entry.title, entry.status, entry.url, entry.updated_at])
        except OSError as exc:
            self._set_status(f"CSV export failed: {exc}")
            return

        self._set_status(f"Exported {len(self._entries)} item(s) to CSV.")

    def _export_json(self) -> None:
        if not self._entries:
            self._set_status("No entries to export.")
            return

        output_path = filedialog.asksaveasfilename(
            title="Export Collection JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="turntopage_collection.json",
        )
        if not output_path:
            self._set_status("JSON export cancelled.")
            return

        payload = [
            {
                "item_id": entry.item_id,
                "title": entry.title,
                "status": entry.status,
                "url": entry.url,
                "updated_at": entry.updated_at,
            }
            for entry in self._entries
        ]

        try:
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            self._set_status(f"JSON export failed: {exc}")
            return

        self._set_status(f"Exported {len(self._entries)} item(s) to JSON.")

    def quick_mark(self, status: str) -> bool:
        e = self._selected()
        if e is None:
            return False
        self._change(status)
        return True

    def quick_unmark(self) -> bool:
        e = self._selected()
        if e is None:
            return False
        previous = self._store.get(e.item_id)
        self._store.remove(e.item_id)
        self._register_undo(f'Remove "{e.title}"', [(e.item_id, previous)])
        self._set_status(f'Removed "{e.title}".')
        self.refresh()
        return True


# ---------------------------------------------------------------------------
# Series Gap Report Tab
# ---------------------------------------------------------------------------

class SeriesTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        api: GamebooksApi,
        store: CollectionStore,
        set_status: Callable[[str], None],
        register_undo: Callable[[str, List[Tuple[int, Optional[CollectionEntry]]]], None],
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._store = store
        self._set_status = set_status
        self._register_undo = register_undo
        self._series_items: List[Dict] = []
        self._visible_series_items: List[Dict] = []
        self._iid_to_item: Dict[str, Dict] = {}
        self._series_title: str = ""
        self._loaded_series_id: Optional[int] = None
        self._loaded_series_title: str = ""
        self._build()

    def _build(self) -> None:
        # Input bar
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text="Series:", font=_FONTB).pack(side="left")
        self._query_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self._query_var, font=_FONT, width=44)
        entry.pack(side="left", padx=(6, 4))
        entry.bind("<Return>", lambda _e: self._do_load())
        ttk.Label(bar, text="(name or numeric ID)", foreground="#888888").pack(side="left", padx=2)
        ttk.Button(bar, text="Load", command=self._do_load).pack(side="left", padx=4)

        # Summary strip
        self._summary_var = tk.StringVar()
        ttk.Label(self, textvariable=self._summary_var, font=_FONT).pack(anchor="w", padx=10)

        # Treeview
        pane = ttk.Frame(self)
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        cols = ("title", "id", "section", "status")
        self._tree = ttk.Treeview(pane, columns=cols, show="headings", selectmode="extended")
        self._tree.heading("title",   text="Title")
        self._tree.heading("id",      text="ID")
        self._tree.heading("section", text="Type")
        self._tree.heading("status",  text="My Status")
        self._tree.column("title",   width=430, stretch=True)
        self._tree.column("id",      width=60,  anchor="center", stretch=False)
        self._tree.column("section", width=130, anchor="center", stretch=False)
        self._tree.column("status",  width=120, anchor="center", stretch=False)
        for s, fg in _STATUS_FG.items():
            self._tree.tag_configure(s, foreground=fg)
        vsb = ttk.Scrollbar(pane, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self._tree.bind("<Double-1>", lambda _e: self._view_details())

        # Action buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=6)
        self._btn_have    = ttk.Button(btn_frame, text="✓  Mark Have",    command=lambda: self._mark_selected("have"))
        self._btn_want    = ttk.Button(btn_frame, text="★  Mark Want",    command=lambda: self._mark_selected("want"))
        self._btn_missing = ttk.Button(btn_frame, text="✗  Mark Missing", command=lambda: self._mark_selected("missing"))
        self._btn_details = ttk.Button(btn_frame, text="View Details",    command=self._view_details)
        self._btn_unkn_miss = ttk.Button(btn_frame, text="? → Missing",   command=self._mark_unknown_as_missing)
        for btn in (self._btn_have, self._btn_want, self._btn_missing, self._btn_details, self._btn_unkn_miss):
            btn.pack(side="left", padx=3)
            btn.state(["disabled"])

        self._missing_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            btn_frame,
            text="Missing/Want only",
            variable=self._missing_only_var,
            command=self._refresh_series_tree,
        ).pack(side="right", padx=4)

        self._confirm_bulk_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            btn_frame,
            text="Confirm bulk updates",
            variable=self._confirm_bulk_var,
        ).pack(side="right", padx=4)

    def load_series_id(self, series_id: int) -> None:
        self._query_var.set(str(series_id))
        self._do_load()

    def _do_load(self) -> None:
        q = self._query_var.get().strip()
        if not q:
            return
        self._set_status("Loading series …")
        self._series_items = []
        self._visible_series_items = []
        self._iid_to_item = {}
        self._series_title = ""
        self._loaded_series_id = None
        self._loaded_series_title = ""
        self._tree.delete(*self._tree.get_children())
        self._summary_var.set("")
        if q.isdigit():
            _async(
                self,
                lambda: self._api.fetch_series_details(f"https://gamebooks.org/Series/{q}"),
                self._on_series_loaded,
            )
        else:
            _async(self, lambda: self._api.search_books(q), self._on_search_done)

    def _on_search_done(
        self,
        results: Optional[List[GamebookSearchResult]],
        err: Optional[Exception],
    ) -> None:
        if err:
            self._set_status(f"Search error: {err}")
            messagebox.showerror("Error", str(err))
            return
        series_list = [r for r in (results or []) if r.is_series]
        if not series_list:
            self._set_status("No series found.")
            messagebox.showinfo("No Results", "No matching series found.")
            return
        # Use the first match; if multiple, pick via a small popup
        if len(series_list) == 1:
            chosen = series_list[0]
        else:
            chosen = _pick_series(self, series_list)
            if chosen is None:
                self._set_status("Cancelled.")
                return
        m = re.search(r"/Series/(\d+)", chosen.url)
        if not m:
            self._set_status("Could not parse series URL.")
            return
        sid = int(m.group(1))
        _async(
            self,
            lambda: self._api.fetch_series_details(f"https://gamebooks.org/Series/{sid}"),
            self._on_series_loaded,
        )

    def _on_series_loaded(
        self,
        details: Optional[GamebookSeriesDetails],
        err: Optional[Exception],
    ) -> None:
        if err:
            self._set_status(f"Failed to load series: {err}")
            messagebox.showerror("Error", str(err))
            return
        self._series_title = details.title
        _m_sid = re.search(r"/Series/(\d+)", details.url)
        self._loaded_series_id = int(_m_sid.group(1)) if _m_sid else None
        self._loaded_series_title = details.title
        all_items = [*details.gamebooks, *details.collections]
        all_item_ids = [item.item_id for item in all_items if item.item_id is not None]
        status_by_id = self._store.status_map(all_item_ids)
        self._series_items = []

        for item in details.gamebooks:
            st = status_by_id.get(item.item_id, "unknown") if item.item_id is not None else "unknown"
            self._series_items.append(
                {
                    "title": item.title,
                    "item_id": item.item_id,
                    "url": item.url,
                    "status": st,
                    "section": "Core",
                }
            )

        for item in details.collections:
            st = status_by_id.get(item.item_id, "unknown") if item.item_id is not None else "unknown"
            self._series_items.append(
                {
                    "title": item.title,
                    "item_id": item.item_id,
                    "url": item.url,
                    "status": st,
                    "section": "Collection",
                }
            )

        self._refresh_series_tree()
        self._set_status(
            f'Loaded "{details.title}" — {len(details.gamebooks)} core and {len(details.collections)} collection entries.'
        )

    def _update_summary(
        self,
        have: int,
        missing: int,
        unknown: int,
        total: int,
        visible: Optional[int] = None,
    ) -> None:
        showing = ""
        if visible is not None and visible != total:
            showing = f"   ·   Showing: {visible}"
        core_count = sum(1 for item in self._series_items if item.get("section") == "Core")
        collection_count = sum(1 for item in self._series_items if item.get("section") == "Collection")
        self._summary_var.set(
            f"{self._series_title}   ·   Core: {core_count}   ·   Collections: {collection_count}   ·"
            f"   Have: {have}   ·   Missing/Want: {missing}   ·   Unknown: {unknown}{showing}"
        )

    def _refresh_series_tree(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._iid_to_item = {}

        if self._missing_only_var.get():
            self._visible_series_items = [
                item for item in self._series_items if item["status"] in ("missing", "want")
            ]
        else:
            self._visible_series_items = list(self._series_items)

        for item in self._visible_series_items:
            iid = self._tree.insert(
                "",
                "end",
                values=(
                    item["title"],
                    item["item_id"] or "",
                    item.get("section", "Core"),
                    _STATUS_LABEL.get(item["status"], ""),
                ),
                tags=(item["status"],),
            )
            self._iid_to_item[iid] = item

        have = sum(1 for item in self._series_items if item["status"] == "have")
        missing = sum(1 for item in self._series_items if item["status"] in ("missing", "want"))
        unknown = sum(1 for item in self._series_items if item["status"] == "unknown")
        self._update_summary(have, missing, unknown, len(self._series_items), len(self._visible_series_items))
        self._on_select()

    def _selected_items(self) -> List[Dict]:
        return [self._iid_to_item[iid] for iid in self._tree.selection() if iid in self._iid_to_item]

    def _on_select(self) -> None:
        items = self._selected_items()
        multi_ok  = ["!disabled"] if items else ["disabled"]
        single_ok = ["!disabled"] if len(items) == 1 and items[0]["item_id"] else ["disabled"]
        has_unknown = ["!disabled"] if any(i["status"] == "unknown" for i in self._series_items) else ["disabled"]
        for btn in (self._btn_have, self._btn_want, self._btn_missing):
            btn.state(multi_ok)
        self._btn_details.state(single_ok)
        self._btn_unkn_miss.state(has_unknown)

    def _mark_unknown_as_missing(self) -> None:
        unknowns = [i for i in self._series_items if i["status"] == "unknown" and i["item_id"] is not None]
        if not unknowns:
            return
        if not messagebox.askyesno(
            "Mark Unknown as Missing",
            f"Mark {len(unknowns)} unknown book(s) as missing?",
        ):
            return
        changes: List[Tuple[int, Optional[CollectionEntry]]] = []
        for item in unknowns:
            item_id = int(item["item_id"])
            changes.append((item_id, self._store.get(item_id)))
            self._store.set_status(
                item_id=item_id,
                status="missing",
                title=item["title"],
                url=item["url"],
                series_id=self._loaded_series_id,
                series_title=self._loaded_series_title,
            )
            item["status"] = "missing"
        self._register_undo(f"Marked {len(changes)} unknown(s) as missing", changes)
        self._refresh_series_tree()
        self._set_status(f"Marked {len(unknowns)} unknown book(s) as missing.")

    def _mark_selected(self, status: str) -> None:
        items = self._selected_items()
        if not items:
            return

        if len(items) > 1 and self._confirm_bulk_var.get():
            if not messagebox.askyesno(
                "Confirm Bulk Update",
                f"Apply status '{status}' to {len(items)} selected books?",
            ):
                self._set_status("Bulk update cancelled.")
                return

        changes: List[Tuple[int, Optional[CollectionEntry]]] = []
        for item in items:
            if item["item_id"] is not None:
                item_id = int(item["item_id"])
                changes.append((item_id, self._store.get(item_id)))
                self._store.set_status(
                    item_id=item_id,
                    status=status,
                    title=item["title"],
                    url=item["url"],
                    series_id=self._loaded_series_id,
                    series_title=self._loaded_series_title,
                )
                item["status"] = status
        if changes:
            self._register_undo(f"Bulk mark {len(changes)} item(s) as {status}", changes)
        self._refresh_series_tree()
        self._set_status(f"Marked {len(items)} book(s) as {status}.")

    def _view_details(self) -> None:
        items = self._selected_items()
        if len(items) != 1 or not items[0]["item_id"]:
            return
        it = items[0]
        DetailWindow(
            self,
            self._api,
            self._store,
            it["item_id"],
            it["title"],
            it["url"],
            self._set_status,
            self._register_undo,
            self._open_series,
        )

    def quick_mark(self, status: str) -> bool:
        items = self._selected_items()
        if not items:
            return False
        self._mark_selected(status)
        return True


# ---------------------------------------------------------------------------
# Series picker popup (when a text search returns multiple series)
# ---------------------------------------------------------------------------

def _pick_series(
    parent: tk.Misc,
    options: List[GamebookSearchResult],
) -> Optional[GamebookSearchResult]:
    """Blocking dialog that returns the chosen series or None."""
    result: List[Optional[GamebookSearchResult]] = [None]

    win = tk.Toplevel(parent)
    win.title("Select Series")
    win.geometry("480x300")
    win.grab_set()

    ttk.Label(win, text="Multiple series found — pick one:", font=_FONTB).pack(padx=10, pady=8, anchor="w")

    lb = tk.Listbox(win, font=_FONT, selectmode="browse", activestyle="dotbox")
    lb.pack(fill="both", expand=True, padx=10, pady=(0, 6))
    for o in options:
        lb.insert("end", o.title)
    lb.selection_set(0)

    def _ok() -> None:
        sel = lb.curselection()
        if sel:
            result[0] = options[sel[0]]
        win.destroy()

    lb.bind("<Double-1>", lambda _e: _ok())
    ttk.Button(win, text="OK", command=_ok).pack(pady=6)
    win.wait_window()
    return result[0]


# ---------------------------------------------------------------------------
# Book Detail Window
# ---------------------------------------------------------------------------

class DetailWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        api: GamebooksApi,
        store: CollectionStore,
        item_id: int,
        title: str,
        url: str,
        set_status: Callable[[str], None],
        register_undo: Callable[[str, List[Tuple[int, Optional[CollectionEntry]]]], None],
        open_series: Callable[[int], None],
        on_close: Optional[Callable] = None,
    ) -> None:
        super().__init__(parent)
        self.title(f"Details — {title}")
        self.geometry("660x540")
        self.resizable(True, True)
        self._api = api
        self._store = store
        self._item_id = item_id
        self._title = title
        self._url = url
        self._set_status = set_status
        self._register_undo = register_undo
        self._open_series = open_series
        self._on_close = on_close
        self._photo: object = None  # keep PIL reference alive
        self._loaded_series_id: Optional[int] = None
        self._loaded_series_url: Optional[str] = None
        self._series_items: List[GamebookSeriesItem] = []
        self._series_current_index: Optional[int] = None

        self._build_skeleton()
        self._load()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self) -> None:
        if self._on_close:
            self._on_close()
        self.destroy()

    # ── layout skeleton ──────────────────────────────────────────────────────

    def _build_skeleton(self) -> None:
        # Cover image panel (left)
        self._img_frame = ttk.Frame(self, width=210)
        self._img_frame.pack(side="left", fill="y", padx=(10, 4), pady=10)
        self._img_frame.pack_propagate(False)
        self._img_label = ttk.Label(self._img_frame, text="Loading cover…", anchor="center")
        self._img_label.pack(expand=True)

        # Info panel (right)
        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=(4, 10), pady=10)

        # Title
        self._title_var = tk.StringVar(value=self._title)
        ttk.Label(right, textvariable=self._title_var, font=_FONTH, wraplength=400).pack(anchor="w")

        # Clickable URL
        self._url_var = tk.StringVar(value=self._url)
        url_row = ttk.Frame(right)
        url_row.pack(fill="x")
        url_lbl = ttk.Label(
            url_row, textvariable=self._url_var,
            foreground="#0055cc", cursor="hand2", font=_FONT,
        )
        url_lbl.pack(side="left", anchor="w")
        url_lbl.bind("<Button-1>", lambda _e: webbrowser.open(self._url))
        ttk.Button(url_row, text="Copy Item URL", command=self._copy_item_url).pack(side="left", padx=(8, 0))

        self._series_context_row = ttk.Frame(right)
        self._series_context_row.pack(fill="x", pady=(6, 0))
        ttk.Label(self._series_context_row, text="Series:", font=_FONTB).pack(side="left")
        self._series_context_var = tk.StringVar(value="Loading…")
        self._series_context_label = ttk.Label(
            self._series_context_row,
            textvariable=self._series_context_var,
            font=_FONT,
            foreground="#555555",
        )
        self._series_context_label.pack(side="left", padx=(6, 8))
        self._series_context_button = ttk.Button(
            self._series_context_row,
            text="Open Series",
            command=self._jump_to_series,
        )
        self._series_context_button.pack(side="left")
        self._series_context_button.state(["disabled"])
        self._series_browser_button = ttk.Button(
            self._series_context_row,
            text="Open In Browser",
            command=self._open_series_in_browser,
        )
        self._series_browser_button.pack(side="left", padx=(6, 0))
        self._series_browser_button.state(["disabled"])
        self._series_copy_button = ttk.Button(
            self._series_context_row,
            text="Copy Series URL",
            command=self._copy_series_url,
        )
        self._series_copy_button.pack(side="left", padx=(6, 0))
        self._series_copy_button.state(["disabled"])
        self._series_prev_button = ttk.Button(
            self._series_context_row,
            text="← Prev",
            command=lambda: self._open_series_neighbor(-1),
        )
        self._series_prev_button.pack(side="left", padx=(8, 2))
        self._series_prev_button.state(["disabled"])
        self._series_next_button = ttk.Button(
            self._series_context_row,
            text="Next →",
            command=lambda: self._open_series_neighbor(1),
        )
        self._series_next_button.pack(side="left", padx=(2, 0))
        self._series_next_button.state(["disabled"])

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # Scrollable metadata area
        canvas = tk.Canvas(right, highlightthickness=0)
        vsb = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._meta_frame = ttk.Frame(canvas)
        self._meta_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._meta_frame, anchor="nw")
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # Status section
        status_row = ttk.Frame(right)
        status_row.pack(fill="x")
        ttk.Label(status_row, text="My Status:", font=_FONTB).pack(side="left")
        entry = self._store.get(self._item_id)
        self._status_var = tk.StringVar(value=entry.status if entry else "unknown")
        self._status_lbl = ttk.Label(status_row, textvariable=self._status_var, font=_FONTB)
        self._status_lbl.pack(side="left", padx=8)
        self._refresh_status_color()

        btn_row = ttk.Frame(right)
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="✓  Have",    command=lambda: self._mark("have")).pack(side="left", padx=3)
        ttk.Button(btn_row, text="★  Want",    command=lambda: self._mark("want")).pack(side="left", padx=3)
        ttk.Button(btn_row, text="✗  Missing", command=lambda: self._mark("missing")).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Remove",     command=self._unmark).pack(side="left", padx=3)

    # ── data loading ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        book = GamebookBook(title=self._title, url=self._url, item_id=self._item_id)
        _async(self, lambda: self._api.fetch_item_details(book), self._on_details_loaded)

    def _on_details_loaded(
        self,
        details: Optional[GamebookItemDetails],
        err: Optional[Exception],
    ) -> None:
        if err:
            ttk.Label(
                self._meta_frame, text=f"Could not load details:\n{err}",
                foreground="red", font=_FONT,
            ).grid(row=0, column=0, columnspan=2, padx=4, pady=4)
            return

        self._title_var.set(details.title)
        self._loaded_series_id = details.series_id
        self._loaded_series_url = None
        self._series_items = []
        self._series_current_index = None
        self._series_prev_button.state(["disabled"])
        self._series_next_button.state(["disabled"])
        self._series_browser_button.state(["disabled"])
        self._series_copy_button.state(["disabled"])

        if details.series_id is not None and details.series_title:
            series_text = details.series_title
            if details.series_number is not None:
                series_text = f"{series_text} (Book {details.series_number})"
            self._series_context_var.set(series_text)
            self._series_context_button.state(["!disabled"])
            self._loaded_series_url = f"https://gamebooks.org/Series/{details.series_id}"
            self._series_browser_button.state(["!disabled"])
            self._series_copy_button.state(["!disabled"])
        elif details.series_id is not None:
            self._series_context_var.set(f"Series #{details.series_id}")
            self._series_context_button.state(["!disabled"])
            self._loaded_series_url = f"https://gamebooks.org/Series/{details.series_id}"
            self._series_browser_button.state(["!disabled"])
            self._series_copy_button.state(["!disabled"])
        else:
            self._series_context_var.set("Standalone title")
            self._series_context_button.state(["disabled"])

        if details.series_id is not None:
            _async(
                self,
                lambda: self._api.fetch_series_details(f"https://gamebooks.org/Series/{details.series_id}"),
                self._on_series_navigation_loaded,
            )

        # Fetch cover image
        if details.image_url:
            if _PIL_AVAILABLE:
                _async(self, lambda: _fetch_pil_image(details.image_url), self._on_image_loaded)
            else:
                self._img_label.configure(text="Install Pillow\nfor cover images")
        else:
            self._img_label.configure(text="No cover image")

        # Build metadata grid
        fields: List[tuple[str, str]] = []
        if details.series_title:
            num = f" #{details.series_number}" if details.series_number else ""
            fields.append(("Series", f"{details.series_title}{num}"))
        if details.authors:
            fields.append(("Authors", ", ".join(details.authors)))
        if details.illustrators:
            fields.append(("Illustrators", ", ".join(details.illustrators)))
        if details.pub_date:
            fields.append(("Published", details.pub_date))
        if details.isbns:
            fields.append(("ISBN", ", ".join(details.isbns)))
        if details.length_pages:
            fields.append(("Pages", str(details.length_pages)))
        if details.number_of_endings:
            fields.append(("Endings", str(details.number_of_endings)))
        if details.description:
            fields.append(("Description", details.description))
        # Fall back to raw metadata dict for anything not captured above
        for k, v in details.metadata.items():
            if k not in {"Authors", "Illustrators", "ISBN", "Date", "Series"}:
                fields.append((k, v))
        if details.editions:
            ed_lines = []
            for ed in details.editions:
                meta_str = (
                    "  " + "  ·  ".join(f"{k}: {v}" for k, v in ed.metadata.items())
                    if ed.metadata else ""
                )
                ed_lines.append(f"{ed.title}{meta_str}")
            fields.append(("Editions", "\n".join(ed_lines)))

        for row, (key, value) in enumerate(fields):
            ttk.Label(
                self._meta_frame, text=f"{key}:", font=_FONTB, anchor="ne",
            ).grid(row=row, column=0, sticky="ne", padx=(4, 6), pady=2)
            ttk.Label(
                self._meta_frame, text=value, font=_FONT,
                wraplength=300, anchor="nw", justify="left",
            ).grid(row=row, column=1, sticky="nw", pady=2)

    def _on_image_loaded(self, photo: object, err: Optional[Exception]) -> None:
        if err or photo is None:
            self._img_label.configure(text="Cover unavailable")
            return
        self._photo = photo  # prevent GC
        self._img_label.configure(image=photo, text="")  # type: ignore[arg-type]

    # ── collection actions ────────────────────────────────────────────────────

    def _mark(self, status: str) -> None:
        previous = self._store.get(self._item_id)
        self._store.set_status(item_id=self._item_id, status=status, title=self._title, url=self._url)
        self._register_undo(f'Mark "{self._title}" as {status}', [(self._item_id, previous)])
        self._status_var.set(status)
        self._refresh_status_color()
        self._set_status(f'Marked "{self._title}" as {status}.')

    def _unmark(self) -> None:
        previous = self._store.get(self._item_id)
        self._store.remove(self._item_id)
        self._register_undo(f'Remove "{self._title}"', [(self._item_id, previous)])
        self._status_var.set("unknown")
        self._refresh_status_color()
        self._set_status(f'Removed "{self._title}" from collection.')

    def _refresh_status_color(self) -> None:
        color = _STATUS_FG.get(self._status_var.get(), "#333333")
        self._status_lbl.configure(foreground=color)

    def _on_series_navigation_loaded(
        self,
        details: Optional[GamebookSeriesDetails],
        err: Optional[Exception],
    ) -> None:
        if err or details is None:
            return

        self._series_items = [item for item in details.gamebooks if item.item_id is not None]
        self._series_current_index = None
        for idx, item in enumerate(self._series_items):
            if item.item_id == self._item_id:
                self._series_current_index = idx
                break

        if self._series_current_index is None:
            return

        position = self._series_current_index + 1
        total = len(self._series_items)
        base = self._series_context_var.get()
        self._series_context_var.set(f"{base}  ·  {position}/{total}")

        if self._series_current_index > 0:
            self._series_prev_button.state(["!disabled"])
        else:
            self._series_prev_button.state(["disabled"])

        if self._series_current_index < total - 1:
            self._series_next_button.state(["!disabled"])
        else:
            self._series_next_button.state(["disabled"])

    def _open_series_neighbor(self, offset: int) -> None:
        if self._series_current_index is None:
            return
        target_index = self._series_current_index + offset
        if target_index < 0 or target_index >= len(self._series_items):
            return

        target = self._series_items[target_index]
        if target.item_id is None:
            return

        DetailWindow(
            self.master,
            self._api,
            self._store,
            target.item_id,
            target.title,
            target.url,
            self._set_status,
            self._register_undo,
            self._open_series,
            on_close=self._on_close,
        )
        self.destroy()

    def _jump_to_series(self) -> None:
        if self._loaded_series_id is None:
            self._set_status(f"No series found for '{self._title}'.")
            return
        self._open_series(self._loaded_series_id)
        self._set_status(f"Opened series {self._loaded_series_id} for '{self._title}'.")

    def _open_series_in_browser(self) -> None:
        if not self._loaded_series_url:
            self._set_status(f"No series URL found for '{self._title}'.")
            return
        webbrowser.open(self._loaded_series_url)
        self._set_status("Opened series page in browser.")

    def _copy_item_url(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._url)
        self.update_idletasks()
        self._set_status("Copied item URL to clipboard.")

    def _copy_series_url(self) -> None:
        if not self._loaded_series_url:
            self._set_status(f"No series URL found for '{self._title}'.")
            return
        self.clipboard_clear()
        self.clipboard_append(self._loaded_series_url)
        self.update_idletasks()
        self._set_status("Copied series URL to clipboard.")


# ---------------------------------------------------------------------------
# PIL image fetch (runs in background thread)
# ---------------------------------------------------------------------------

def _fetch_pil_image(url: str) -> object:
    """Download *url* and return an ImageTk.PhotoImage (caller keeps reference)."""
    import io as _io
    import urllib.request as _req
    from PIL import Image, ImageTk  # type: ignore[import]

    with _req.urlopen(url, timeout=10) as resp:
        data = resp.read()
    img = Image.open(_io.BytesIO(data))
    img.thumbnail((200, 280), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self, db_path: str = "turntopage.db") -> None:
        super().__init__()
        self.title("TurnToPage — Gamebook Collector")
        self.geometry("960x640")
        self.minsize(720, 500)

        self._api   = GamebooksApi()
        self._store = CollectionStore(db_path)
        self._last_undo_action: Optional[UndoAction] = None
        self._notice_after_id: Optional[str] = None
        self._activity_log: List[str] = []

        self._setup_styles()
        self._build()

    def _setup_styles(self) -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook.Tab", font=_FONTB, padding=(12, 5))
        s.configure("TButton",       font=_FONT,  padding=(6, 3))
        s.configure("TLabel",        font=_FONT)
        s.configure("TEntry",        font=_FONT)
        s.configure("Treeview",      font=_FONT,  rowheight=24)
        s.configure("Treeview.Heading", font=_FONTB)

    def _build(self) -> None:
        # Menu bar
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Quit", command=self.quit, accelerator="Alt+F4")
        menu.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="gamebooks.org",
            command=lambda: webbrowser.open("https://gamebooks.org"),
        )
        menu.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menu)

        # Tabs
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True, padx=4, pady=(4, 0))

        self._dashboard_tab = DashboardTab(
            self._nb,
            self._api,
            self._store,
            self._set_status,
            self._record_undo,
            self._get_activity_log,
            self._open_search,
            self._open_collection,
            self._open_series_tab,
            self._open_series,
        )

        self._search_tab = SearchTab(
            self._nb, self._api, self._store, self._set_status, self._open_series, self._record_undo
        )
        self._collection_tab = CollectionTab(
            self._nb, self._store, self._api, self._set_status, self._record_undo, self._open_series
        )
        self._series_tab = SeriesTab(
            self._nb, self._api, self._store, self._set_status, self._record_undo
        )

        self._nb.add(self._dashboard_tab,  text="  Dashboard  ")
        self._nb.add(self._search_tab,     text="  Search  ")
        self._nb.add(self._collection_tab, text="  My Collection  ")
        self._nb.add(self._series_tab,     text="  Series Gap Report  ")

        # Refresh collection tab whenever it becomes visible
        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_notice_bar()

        self._build_status_legend()
        self._bind_shortcuts()

        # Status bar
        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(
            self, textvariable=self._status_var,
            relief="sunken", anchor="w", font=_FONT,
        ).pack(fill="x", side="bottom")

    def _build_notice_bar(self) -> None:
        self._notice_var = tk.StringVar(value="")
        self._notice_label = tk.Label(
            self,
            textvariable=self._notice_var,
            anchor="w",
            font=_FONT,
            bg="#edf4ff",
            fg="#0b3b73",
            padx=10,
            pady=4,
        )
        self._notice_label.pack(fill="x", side="bottom")
        self._notice_label.pack_forget()

    def _build_status_legend(self) -> None:
        legend = ttk.Frame(self)
        legend.pack(fill="x", side="bottom", padx=8, pady=(0, 2))
        ttk.Label(legend, text="Status:", font=_FONTB).pack(side="left", padx=(0, 6))
        ttk.Label(legend, text="Have", foreground=_STATUS_FG["have"], font=_FONTB).pack(side="left", padx=6)
        ttk.Label(legend, text="Want", foreground=_STATUS_FG["want"], font=_FONTB).pack(side="left", padx=6)
        ttk.Label(legend, text="Missing", foreground=_STATUS_FG["missing"], font=_FONTB).pack(side="left", padx=6)
        ttk.Label(legend, text="Unknown", foreground=_STATUS_FG["unknown"], font=_FONTB).pack(side="left", padx=6)
        self._undo_button = ttk.Button(legend, text="Undo Last Change", command=self._undo_last_change)
        self._undo_button.pack(side="right", padx=(8, 0))
        self._undo_button.state(["disabled"])
        ttk.Label(
            legend,
            text="Shortcuts: H=Have, W=Want, M=Missing, U=Unmark, Ctrl+Z=Undo",
            font=_FONT,
            foreground="#666666",
        ).pack(side="right")

    def _bind_shortcuts(self) -> None:
        self.bind_all("<KeyPress-h>", lambda _e: self._shortcut_mark("have"))
        self.bind_all("<KeyPress-H>", lambda _e: self._shortcut_mark("have"))
        self.bind_all("<KeyPress-w>", lambda _e: self._shortcut_mark("want"))
        self.bind_all("<KeyPress-W>", lambda _e: self._shortcut_mark("want"))
        self.bind_all("<KeyPress-m>", lambda _e: self._shortcut_mark("missing"))
        self.bind_all("<KeyPress-M>", lambda _e: self._shortcut_mark("missing"))
        self.bind_all("<KeyPress-u>", lambda _e: self._shortcut_unmark())
        self.bind_all("<KeyPress-U>", lambda _e: self._shortcut_unmark())
        self.bind_all("<Control-z>", lambda _e: self._undo_last_change())
        self.bind_all("<Control-Z>", lambda _e: self._undo_last_change())

    def _shortcut_mark(self, status: str) -> None:
        if not self._should_handle_shortcut():
            return
        tab = self._active_tab_name()
        applied = False
        if tab == "search":
            applied = self._search_tab.quick_mark(status)
        elif tab == "collection":
            applied = self._collection_tab.quick_mark(status)
            if applied:
                self._dashboard_tab.refresh()
        elif tab == "series":
            applied = self._series_tab.quick_mark(status)
            if applied:
                self._dashboard_tab.refresh()

        if applied:
            self._set_status(f"Shortcut applied: {status}")

    def _shortcut_unmark(self) -> None:
        if not self._should_handle_shortcut():
            return
        tab = self._active_tab_name()
        applied = False
        if tab == "search":
            applied = self._search_tab.quick_unmark()
        elif tab == "collection":
            applied = self._collection_tab.quick_unmark()
            if applied:
                self._dashboard_tab.refresh()

        if applied:
            self._set_status("Shortcut applied: unmark")

    def _active_tab_name(self) -> str:
        selected = self._nb.select()
        if selected == str(self._search_tab):
            return "search"
        if selected == str(self._collection_tab):
            return "collection"
        if selected == str(self._series_tab):
            return "series"
        if selected == str(self._dashboard_tab):
            return "dashboard"
        return "unknown"

    def _should_handle_shortcut(self) -> bool:
        focused = self.focus_get()
        if focused is None:
            return True
        focused_class = focused.winfo_class().lower()
        return focused_class not in {"entry", "tentry", "text", "spinbox"}

    def _record_undo(self, label: str, changes: List[Tuple[int, Optional[CollectionEntry]]]) -> None:
        if not changes:
            return
        self._last_undo_action = UndoAction(label=label, changes=changes)
        self._undo_button.state(["!disabled"])

    def _undo_last_change(self) -> None:
        if self._last_undo_action is None:
            return

        action = self._last_undo_action
        for item_id, previous in action.changes:
            if previous is None:
                self._store.remove(item_id)
            else:
                self._store.set_status(
                    item_id=item_id,
                    status=previous.status,
                    title=previous.title,
                    url=previous.url,
                )

        self._last_undo_action = None
        self._undo_button.state(["disabled"])
        self._dashboard_tab.refresh()
        self._collection_tab.refresh()
        self._series_tab._on_select()
        self._search_tab._apply_filters()
        self._set_status(f'Undid: {action.label}')

    def _on_tab_changed(self, _event: tk.Event) -> None:
        if self._nb.select() == str(self._dashboard_tab):
            self._dashboard_tab.refresh()
        if self._nb.select() == str(self._collection_tab):
            self._collection_tab.refresh()

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)
        self._show_notice(msg, self._infer_notice_level(msg))
        self._record_activity(msg)

    def _record_activity(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._activity_log.append(f"{stamp}  {msg}")
        if len(self._activity_log) > 200:
            self._activity_log = self._activity_log[-200:]

    def _get_activity_log(self) -> List[str]:
        return list(self._activity_log)

    def _infer_notice_level(self, msg: str) -> str:
        lowered = msg.lower()
        if any(token in lowered for token in ["failed", "error", "unexpected", "could not"]):
            return "error"
        if any(token in lowered for token in ["removed", "missing", "undo", "undid"]):
            return "warning"
        if any(token in lowered for token in ["marked", "loaded", "found", "ready", "saved", "refresh"]):
            return "success"
        return "info"

    def _show_notice(self, msg: str, level: str = "info", ttl_ms: int = 3500) -> None:
        palette = {
            "info": ("#edf4ff", "#0b3b73"),
            "success": ("#e9f8ef", "#1b6b3a"),
            "warning": ("#fff7e6", "#7a4a00"),
            "error": ("#fdecec", "#8b1f1f"),
        }
        bg, fg = palette.get(level, palette["info"])
        self._notice_var.set(msg)
        self._notice_label.configure(bg=bg, fg=fg)
        self._notice_label.pack(fill="x", side="bottom")

        if self._notice_after_id is not None:
            self.after_cancel(self._notice_after_id)
        self._notice_after_id = self.after(ttl_ms, self._hide_notice)

    def _hide_notice(self) -> None:
        self._notice_after_id = None
        self._notice_label.pack_forget()

    def _open_series(self, series_id: int) -> None:
        self._nb.select(self._series_tab)
        self._series_tab.load_series_id(series_id)

    def _open_search(self) -> None:
        self._nb.select(self._search_tab)

    def _open_collection(self) -> None:
        self._nb.select(self._collection_tab)
        self._collection_tab.refresh()

    def _open_series_tab(self) -> None:
        self._nb.select(self._series_tab)

    def run(self) -> None:
        self.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="TurnToPage — Gamebook Collector GUI")
    p.add_argument("--db-path", default="turntopage.db", help="Path to SQLite database")
    args = p.parse_args()
    App(db_path=args.db_path).run()
