from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from .models import (
    GamebookBook,
    GamebookEdition,
    GamebookItemDetails,
    GamebookRelatedLink,
    GamebookSearchResult,
    GamebookSeriesDetails,
    GamebookSeriesItem,
)


class GamebooksApiError(Exception):
    pass


class GamebooksApi:
    BASE_URL = "https://gamebooks.org"

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._session = session or requests.Session()

    def search_books(self, query: str) -> List[GamebookSearchResult]:
        response = self._session.get(
            f"{self.BASE_URL}/Search",
            params={"SearchQuery": query, "SearchType": "Title"},
            headers={"Accept": "text/html"},
            timeout=20,
        )
        if response.status_code != 200:
            raise GamebooksApiError(f"Search failed with HTTP {response.status_code}.")

        soup = BeautifulSoup(response.content, "html.parser")
        results: List[GamebookSearchResult] = []
        results.extend(self._extract_search_results(soup, "Series", "Series"))
        results.extend(self._extract_search_results(soup, "Item", "Item"))
        return results

    def fetch_item_details(self, book: GamebookBook) -> GamebookItemDetails:
        if book.item_id is None:
            raise GamebooksApiError("This item does not have a numeric ID to fetch details.")

        editions_url = f"{self.BASE_URL}/Item/{book.item_id}/Editions"
        show_url = f"{self.BASE_URL}/Item/{book.item_id}"
        response = self._session.get(
            editions_url,
            headers={"Accept": "text/html"},
            timeout=20,
        )
        if response.status_code != 200:
            raise GamebooksApiError(f"Item details failed with HTTP {response.status_code}.")

        soup = BeautifulSoup(response.content, "html.parser")
        title = (soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else "") or book.title
        image_urls = self._find_image_urls(soup)
        metadata: Dict[str, str] = {}
        metadata.update(self._extract_section_metadata(soup, "Item-Level Details"))
        metadata.update(self._extract_first_edition_metadata(soup))
        related_links = self._extract_related_links(soup)
        description = metadata.get("User Summary") or self._extract_description(soup)

        series_info = self._extract_series_info(soup)
        authors_raw = metadata.get("Authors") or metadata.get("Author")
        illustrators_raw = metadata.get("Illustrators") or metadata.get("Illustrator")
        pub_date = metadata.get("Date") or metadata.get("Publication Date")

        isbn_raw: Optional[str] = None
        for key, value in metadata.items():
            if "isbn" in key.lower():
                isbn_raw = value
                break

        authors = self._split_names(authors_raw or "")
        illustrators = self._split_names(illustrators_raw or "")
        isbns = self._extract_isbns(isbn_raw) if isbn_raw is not None else []
        length_pages = self._parse_int_from_string(metadata.get("Length"))
        number_of_endings = self._parse_int_from_string(metadata.get("Number of Endings"))

        editions = self._extract_editions(soup)

        return GamebookItemDetails(
            title=title,
            url=show_url,
            show_url=show_url,
            editions_url=editions_url,
            image_url=image_urls[0] if image_urls else None,
            image_urls=image_urls,
            metadata=metadata,
            related_links=related_links,
            description=description,
            series_title=series_info.get("title"),
            series_id=series_info.get("id"),
            series_number=series_info.get("number"),
            authors=authors,
            illustrators=illustrators,
            pub_date=pub_date,
            isbns=isbns,
            length_pages=length_pages,
            number_of_endings=number_of_endings,
            editions_count=len(editions),
            editions=editions,
        )

    def fetch_series_details(self, series_url: str) -> GamebookSeriesDetails:
        response = self._session.get(
            series_url,
            headers={"Accept": "text/html"},
            timeout=20,
        )
        if response.status_code != 200:
            raise GamebooksApiError(f"Series details failed with HTTP {response.status_code}.")

        soup = BeautifulSoup(response.content, "html.parser")
        h1 = soup.select_one("h1")
        if h1:
            title = h1.get_text(strip=True)
        else:
            path_segments = [segment for segment in urlparse(series_url).path.split("/") if segment]
            title = path_segments[-1] if path_segments else "Series"

        gamebooks = self._extract_series_section_items(soup, "Gamebooks")
        collections = self._extract_series_section_items(soup, "Collections")

        if not gamebooks and not collections:
            fallback = self._extract_series_items_fallback(soup)
            if fallback:
                gamebooks = fallback
                collections = []

        return GamebookSeriesDetails(
            title=title,
            url=series_url,
            gamebooks=gamebooks,
            collections=collections,
        )

    def close(self) -> None:
        self._session.close()

    def _extract_search_results(
        self,
        soup: BeautifulSoup,
        section_title: str,
        result_type: str,
    ) -> List[GamebookSearchResult]:
        section = self._find_section(soup, section_title)
        if section is None:
            return []

        out: List[GamebookSearchResult] = []
        for anchor in section.select("a.col-md-10"):
            title = anchor.get_text(strip=True)
            href = anchor.get("href")
            if not title or not href:
                continue
            if result_type == "Item" and not href.startswith("/Item/"):
                continue
            if result_type == "Series" and not href.startswith("/Series/"):
                continue

            item_id = self._extract_trailing_id(href)
            out.append(
                GamebookSearchResult(
                    title=title,
                    url=urljoin(self.BASE_URL, href),
                    result_type=result_type,
                    item_id=item_id,
                )
            )
        return out

    def _find_section(self, soup: BeautifulSoup, heading_text: str) -> Optional[Tag]:
        for heading in soup.select("h2, h3, h4, h5"):
            if heading.get_text(strip=True) == heading_text:
                return heading.parent if isinstance(heading.parent, Tag) else heading
        return None

    def _find_heading(self, soup: BeautifulSoup, heading_text: str) -> Optional[Tag]:
        for heading in soup.select("h2, h3, h4, h5"):
            if heading.get_text(strip=True) == heading_text:
                return heading
        return None

    def _extract_section_metadata(self, soup: BeautifulSoup, heading_text: str) -> Dict[str, str]:
        heading = self._find_heading(soup, heading_text)
        if heading is None:
            return {}

        metadata: Dict[str, str] = {}
        for row in self._rows_until_next_heading(heading):
            header_el = row.select_one("th")
            value_el = row.select_one("td")
            if not header_el or not value_el:
                continue
            key = header_el.get_text(strip=True).replace(":", "")
            value = value_el.get_text(strip=True)
            if key and value:
                metadata.setdefault(key, value)
        return metadata

    def _extract_first_edition_metadata(self, soup: BeautifulSoup) -> Dict[str, str]:
        by_edition = self._find_heading(soup, "By Edition")
        edition_heading: Optional[Tag] = None
        if by_edition is not None:
            edition_heading = self._next_heading_after(by_edition)
            while edition_heading is not None and edition_heading.get_text(strip=True) == "Item-Level Details":
                edition_heading = self._next_heading_after(edition_heading)
        else:
            for heading in soup.select("h2, h3, h4, h5"):
                text = heading.get_text(strip=True).lower()
                if "edition" in text or "printing" in text or "original" in text:
                    edition_heading = heading
                    break

        if edition_heading is None:
            return {}

        metadata: Dict[str, str] = {}
        for row in self._rows_until_next_heading(edition_heading):
            header_el = row.select_one("th")
            value_el = row.select_one("td")
            if not header_el or not value_el:
                continue
            key = header_el.get_text(strip=True).replace(":", "")
            value = value_el.get_text(strip=True)
            if key and value:
                metadata.setdefault(key, value)

        expected_keys = {
            "Authors",
            "Illustrators",
            "ISBN",
            "Date",
            "Length",
            "Printing",
            "Special Thanks",
            "Cover Price",
            "Description",
            "Number of Endings",
        }
        return metadata if any(key in metadata for key in expected_keys) else {}

    def _extract_series_section_items(self, soup: BeautifulSoup, section_title: str) -> List[GamebookSeriesItem]:
        heading = self._find_heading(soup, section_title)
        if heading is None:
            return []

        items: List[GamebookSeriesItem] = []
        seen_ids: set[int] = set()

        for anchor in self._anchors_until_next_heading(
            heading,
            lambda a: (a.get("href") or "").startswith("/Item/"),
        ):
            href = anchor.get("href")
            title = anchor.get_text(strip=True)
            if not href or not title:
                continue

            item_id = self._extract_trailing_id(href)
            if item_id is not None and item_id in seen_ids:
                continue

            if item_id is not None:
                seen_ids.add(item_id)

            items.append(
                GamebookSeriesItem(
                    title=title,
                    url=self._resolve_link(href),
                    item_id=item_id,
                )
            )

        return items

    def _extract_series_items_fallback(self, soup: BeautifulSoup) -> List[GamebookSeriesItem]:
        items: List[GamebookSeriesItem] = []
        seen_ids: set[int] = set()

        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            if not href:
                continue

            parsed = urlparse(self._resolve_link(href))
            segments = [segment for segment in parsed.path.split("/") if segment]
            if "Item" not in segments:
                continue

            index = segments.index("Item")
            if index + 1 >= len(segments):
                continue

            item_id = self._parse_int_from_string(segments[index + 1])
            if item_id is None or item_id in seen_ids:
                continue

            title = anchor.get_text(strip=True)
            if not title:
                continue

            seen_ids.add(item_id)
            items.append(GamebookSeriesItem(title=title, url=self._resolve_link(href), item_id=item_id))

        return items

    def _rows_until_next_heading(self, heading: Tag):
        heading_level = self._heading_level(heading)
        sibling = heading.next_sibling
        while sibling is not None:
            if isinstance(sibling, Tag):
                sibling_level = self._heading_level(sibling)
                if sibling_level is not None and heading_level is not None and sibling_level <= heading_level:
                    break
                for row in sibling.select("table tr"):
                    yield row
            sibling = sibling.next_sibling

    def _anchors_until_next_heading(self, heading: Tag, match):
        heading_level = self._heading_level(heading)
        sibling = heading.next_sibling
        while sibling is not None:
            if isinstance(sibling, Tag):
                sibling_level = self._heading_level(sibling)
                if sibling_level is not None and heading_level is not None and sibling_level <= heading_level:
                    break
                for anchor in sibling.select("a[href]"):
                    if match(anchor):
                        yield anchor
            sibling = sibling.next_sibling

    def _next_heading_after(self, heading: Tag) -> Optional[Tag]:
        heading_level = self._heading_level(heading)
        sibling = heading.next_sibling
        while sibling is not None:
            if isinstance(sibling, Tag):
                sibling_level = self._heading_level(sibling)
                if sibling_level is not None and heading_level is not None and sibling_level <= heading_level:
                    return sibling
            sibling = sibling.next_sibling
        return None

    def _heading_level(self, element: Tag) -> Optional[int]:
        name = element.name.lower() if element.name else ""
        if not name.startswith("h"):
            return None
        try:
            return int(name[1:])
        except ValueError:
            return None

    def _find_image_urls(self, soup: BeautifulSoup) -> List[str]:
        urls: List[str] = []
        for anchor in soup.select('a[href^="/gallery/"]'):
            href = anchor.get("href")
            if not href:
                continue
            full = urljoin(self.BASE_URL, href)
            if full not in urls:
                urls.append(full)
        return urls

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        for paragraph in soup.select("div.col-md-9 p, div.col-md-12 p"):
            text = paragraph.get_text(strip=True)
            if text and not text.startswith("Please log in"):
                return text
        return None

    def _extract_related_links(self, soup: BeautifulSoup) -> List[GamebookRelatedLink]:
        related: List[GamebookRelatedLink] = []
        heading = self._find_heading(soup, "Related Documents")
        if heading is None:
            return related

        for anchor in self._anchors_until_next_heading(heading, lambda a: bool(a.get("href"))):
            href = anchor.get("href")
            if not href:
                continue

            label = anchor.get_text(strip=True) or self._label_for_href(href)
            if not label:
                continue

            related.append(GamebookRelatedLink(label=label, url=self._resolve_link(href)))

        return related

    def _extract_series_info(self, soup: BeautifulSoup) -> Dict[str, Optional[int | str]]:
        title: Optional[str] = None
        series_id: Optional[int] = None
        number: Optional[int] = None

        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            if not href:
                continue

            parsed = urlparse(self._resolve_link(href))
            segments = [segment for segment in parsed.path.split("/") if segment]
            if "Series" not in segments:
                continue

            index = segments.index("Series")
            if index + 1 >= len(segments):
                continue

            maybe_id = self._parse_int_from_string(segments[index + 1])
            if maybe_id is None:
                continue

            series_id = series_id or maybe_id

            anchor_text = anchor.get_text(strip=True)
            if anchor_text and title is None:
                title = anchor_text

            parts: List[str] = []
            if isinstance(anchor.parent, Tag):
                parts.append(anchor.parent.get_text(" ", strip=True))
                if isinstance(anchor.parent.parent, Tag):
                    parts.append(anchor.parent.parent.get_text(" ", strip=True))
                if isinstance(anchor.parent.next_sibling, Tag):
                    parts.append(anchor.parent.next_sibling.get_text(" ", strip=True))
            parts.append(anchor_text)
            if isinstance(anchor.next_sibling, Tag):
                parts.append(anchor.next_sibling.get_text(" ", strip=True))

            surrounding = re.sub(r"\s+", " ", " ".join(part for part in parts if part))

            match = re.search(r"(?:#|No\.?|Book)\s*(\d+)", surrounding, flags=re.IGNORECASE)
            if match:
                number = number or int(match.group(1))
            else:
                match2 = re.search(r"\bno\.?\s*(\d+)\b", surrounding, flags=re.IGNORECASE)
                if match2:
                    number = number or int(match2.group(1))

            if title is not None and number is not None:
                break

        return {"title": title, "id": series_id, "number": number}

    def _split_names(self, raw: str) -> List[str]:
        if not raw.strip():
            return []

        normalized = raw.replace("&", ",").replace(" and ", ",")
        parts = re.split(r"[;,\n]", normalized)
        return [part.strip() for part in parts if part.strip()]

    def _extract_isbns(self, raw: str) -> List[str]:
        if not raw.strip():
            return []

        candidates = [part.strip() for part in re.split(r"[;,\n/]", raw) if part.strip()]
        results: List[str] = []
        for candidate in candidates:
            match = re.search(r"[0-9Xx\- ]{9,}", candidate)
            if match:
                results.append(match.group(0).strip())
        return results

    def _parse_int_from_string(self, raw: Optional[str]) -> Optional[int]:
        if raw is None:
            return None
        match = re.search(r"(\d+)", raw)
        if not match:
            return None
        return int(match.group(1))

    def _extract_editions(self, soup: BeautifulSoup) -> List[GamebookEdition]:
        editions: List[GamebookEdition] = []

        for heading in soup.select("h2, h3, h4, h5"):
            title = heading.get_text(strip=True)
            lower = title.lower()
            if lower == "by edition":
                continue
            if not any(word in lower for word in ("edition", "printing", "original", "reissue")):
                continue

            metadata: Dict[str, str] = {}
            for row in self._rows_until_next_heading(heading):
                header_el = row.select_one("th")
                value_el = row.select_one("td")
                if not header_el or not value_el:
                    continue

                key = header_el.get_text(strip=True).replace(":", "")
                value = value_el.get_text(strip=True)
                if key and value:
                    metadata.setdefault(key, value)

            cover_url: Optional[str] = None
            heading_level = self._heading_level(heading)
            sibling = heading.next_sibling
            while sibling is not None:
                if isinstance(sibling, Tag):
                    sibling_level = self._heading_level(sibling)
                    if sibling_level is not None and heading_level is not None and sibling_level <= heading_level:
                        break

                    gallery_anchor = sibling.select_one('a[href^="/gallery/"]')
                    if gallery_anchor and gallery_anchor.get("href"):
                        cover_url = self._resolve_link(gallery_anchor.get("href"))
                        break

                    img = sibling.select_one("img")
                    if img and img.get("src"):
                        cover_url = self._resolve_link(img.get("src"))
                        break

                sibling = sibling.next_sibling

            editions.append(GamebookEdition(title=title, metadata=metadata, cover_image_url=cover_url))

        return editions

    def _resolve_link(self, href: str) -> str:
        return href if href.startswith("http") else urljoin(self.BASE_URL, href)

    def _label_for_href(self, href: str) -> str:
        parsed = urlparse(href)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not segments:
            return ""
        return segments[-1].replace("-", " ")

    def _extract_trailing_id(self, href: str) -> Optional[int]:
        parsed = urlparse(href)
        parts = [segment for segment in parsed.path.split("/") if segment]
        if not parts:
            return None
        match = re.search(r"(\d+)$", parts[-1])
        if not match:
            return None
        return int(match.group(1))
