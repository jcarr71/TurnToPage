from __future__ import annotations

from typing import Optional

import requests

from .api import GamebooksApiError


class GamebooksSession:
    BASE_URL = "https://gamebooks.org"

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._session = session or requests.Session()
        self._signed_in_username: Optional[str] = None

    @property
    def is_signed_in(self) -> bool:
        return self._signed_in_username is not None

    @property
    def signed_in_username(self) -> Optional[str]:
        return self._signed_in_username

    def sign_in(self, username: str, password: str) -> None:
        response = self._session.post(
            f"{self.BASE_URL}/login",
            headers={
                "Accept": "text/html",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"user": username, "pass": password},
            timeout=20,
        )

        body = response.text
        looks_logged_in = "Please log in" not in body or bool(self._session.cookies)
        if not looks_logged_in:
            raise GamebooksApiError("Login failed. Check your username and password.")

        self._signed_in_username = username

    def sign_out(self) -> None:
        self._signed_in_username = None
        self._session.cookies.clear()

    def get_page(self, url: str) -> str:
        response = self._session.get(url, headers={"Accept": "text/html"}, timeout=20)
        if response.status_code != 200:
            raise GamebooksApiError(f"Request failed with HTTP {response.status_code}.")
        return response.text

    def close(self) -> None:
        self._session.close()
