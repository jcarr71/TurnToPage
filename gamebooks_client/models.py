from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class GamebookBook:
    title: str
    url: str
    item_id: Optional[int]


@dataclass(frozen=True)
class GamebookSearchResult:
    title: str
    url: str
    result_type: str
    item_id: Optional[int]

    @property
    def is_series(self) -> bool:
        return self.result_type == "Series"

    @property
    def is_item(self) -> bool:
        return self.result_type == "Item"


@dataclass(frozen=True)
class GamebookRelatedLink:
    label: str
    url: str


@dataclass(frozen=True)
class GamebookEdition:
    title: str
    metadata: Dict[str, str] = field(default_factory=dict)
    cover_image_url: Optional[str] = None


@dataclass(frozen=True)
class GamebookSeriesItem:
    title: str
    url: str
    item_id: Optional[int]


@dataclass(frozen=True)
class GamebookSeriesDetails:
    title: str
    url: str
    gamebooks: List[GamebookSeriesItem] = field(default_factory=list)
    collections: List[GamebookSeriesItem] = field(default_factory=list)

    @property
    def items(self) -> List[GamebookSeriesItem]:
        return [*self.gamebooks, *self.collections]

    @property
    def total_count(self) -> int:
        return len(self.gamebooks)

    @property
    def collection_count(self) -> int:
        return len(self.collections)

    @property
    def item_ids(self) -> List[int]:
        return [item.item_id for item in self.gamebooks if item.item_id is not None]


@dataclass(frozen=True)
class GamebookItemDetails:
    title: str
    url: str
    show_url: str
    editions_url: str
    image_url: Optional[str]
    image_urls: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
    related_links: List[GamebookRelatedLink] = field(default_factory=list)
    description: Optional[str] = None
    series_title: Optional[str] = None
    series_id: Optional[int] = None
    series_number: Optional[int] = None
    authors: List[str] = field(default_factory=list)
    illustrators: List[str] = field(default_factory=list)
    pub_date: Optional[str] = None
    isbns: List[str] = field(default_factory=list)
    length_pages: Optional[int] = None
    number_of_endings: Optional[int] = None
    editions_count: int = 0
    editions: List[GamebookEdition] = field(default_factory=list)

    @property
    def series(self) -> Optional[str]:
        return self.series_title or self.metadata.get("Series")

    @property
    def authors_raw(self) -> Optional[str]:
        return self.metadata.get("Authors")

    @property
    def illustrators_raw(self) -> Optional[str]:
        return self.metadata.get("Illustrators")

    @property
    def date(self) -> Optional[str]:
        return self.pub_date or self.metadata.get("Date")

    @property
    def isbn(self) -> Optional[str]:
        if self.isbns:
            return ", ".join(self.isbns)
        return self.metadata.get("ISBN")

    @property
    def length(self) -> Optional[str]:
        if self.length_pages is not None:
            return str(self.length_pages)
        return self.metadata.get("Length")
