from __future__ import annotations

from datetime import UTC, date, datetime
from functools import lru_cache
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import urlparse

import httpx

from alphapilot.core.config import Settings, get_settings

# The 深证信 WebAPI portal verifies plain OAuth2 client-credentials tokens
# (POST /api-cloud-platform/oauth2/token). Announcements come from the public
# www.cninfo.com.cn query endpoints, which need an orgId resolved per symbol.
TOKEN_PATH = "/api-cloud-platform/oauth2/token"
STOCK_PROFILE_PATH = "/api/stock/p_stock2101"
TOP_SEARCH_PATH = "/new/information/topSearch/query"
ANNOUNCEMENT_PATH = "/new/hisAnnouncement/query"
STATIC_PATH_PREFIX = "https://static.cninfo.com.cn/"
USER_AGENT = "AlphaPilotAI/0.2 (+local research tool)"


class CninfoError(RuntimeError):
    pass


class CninfoNotConfiguredError(CninfoError):
    """Raised when webapi credentials are required but absent from the environment."""


class CninfoClient:
    def __init__(self, settings: Settings, timeout_seconds: float = 20.0):
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        self._token_lock = Lock()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._org_cache: dict[str, str] = {}

    @property
    def configured(self) -> bool:
        return bool(self.settings.cninfo_access_key and self.settings.cninfo_access_secret)

    def _client(self, base_url: str) -> httpx.Client:
        parsed = urlparse(base_url)
        if parsed.scheme.lower() == "http" and parsed.netloc.lower() == "www.cninfo.com.cn":
            base_url = parsed._replace(scheme="https").geturl()
        elif parsed.scheme.lower() != "https":
            raise CninfoError("cninfo endpoints require HTTPS")
        return httpx.Client(
            base_url=base_url,
            timeout=self.timeout_seconds,
            verify=True,
            headers={"User-Agent": USER_AGENT},
        )

    def _access_token(self) -> str:
        if not self.configured:
            raise CninfoNotConfiguredError(
                "cninfo credentials are not configured; set ALPHAPILOT_CNINFO_ACCESS_KEY "
                "and ALPHAPILOT_CNINFO_ACCESS_SECRET in the local .env."
            )
        with self._token_lock:
            if self._token and monotonic() < self._token_expires_at - 60:
                return self._token
            with self._client(self.settings.cninfo_base_url) as client:
                response = client.post(
                    TOKEN_PATH,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.settings.cninfo_access_key,
                        "client_secret": self.settings.cninfo_access_secret,
                    },
                )
            if response.status_code != 200:
                raise CninfoError(f"cninfo token request failed: HTTP {response.status_code}")
            payload = response.json()
            token = payload.get("access_token")
            if not token:
                # OAuth responses may contain other credentials even when the
                # expected field is absent. Never copy the payload into errors.
                raise CninfoError("cninfo token response missing access_token")
            self._token = str(token)
            self._token_expires_at = monotonic() + float(payload.get("expires_in", 1800))
            return self._token

    def stock_profile(self, symbol: str) -> dict[str, Any]:
        """Company base information from webapi p_stock2101."""
        digits = "".join(character for character in symbol if character.isdigit())
        if len(digits) != 6:
            raise CninfoError(f"Unsupported A-share symbol for cninfo: {symbol}")
        token = self._access_token()
        with self._client(self.settings.cninfo_base_url) as client:
            response = client.get(
                STOCK_PROFILE_PATH, params={"scode": digits, "access_token": token}
            )
        if response.status_code != 200:
            raise CninfoError(f"cninfo profile failed: HTTP {response.status_code}")
        payload = response.json()
        if payload.get("resultcode") != 200:
            raise CninfoError(f"cninfo profile error: {payload.get('resultmsg')}")
        records = payload.get("records") or []
        if not records:
            raise CninfoError(f"cninfo has no profile for {symbol}")
        record = records[0]
        return {
            "symbol": digits,
            "name": record.get("SECNAME"),
            "org_name": record.get("ORGNAME"),
            "board": record.get("F005V"),
            "listed_date": record.get("F006D"),
            "status": record.get("F011V"),
            "isin": record.get("F013V"),
            "raw": record,
        }

    def resolve_org_id(self, symbol: str) -> str:
        digits = "".join(character for character in symbol if character.isdigit())
        if digits in self._org_cache:
            return self._org_cache[digits]
        with self._client(self.settings.cninfo_announcement_base_url) as client:
            response = client.post(
                TOP_SEARCH_PATH,
                data={"keyWord": digits, "maxNum": 10},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code != 200:
            raise CninfoError(f"cninfo topSearch failed: HTTP {response.status_code}")
        for row in response.json() or []:
            if row.get("code") == digits and row.get("orgId"):
                org_id = str(row["orgId"])
                self._org_cache[digits] = org_id
                return org_id
        raise CninfoError(f"cninfo could not resolve orgId for {symbol}")

    def announcements(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        page_size: int = 30,
    ) -> list[dict[str, Any]]:
        """Company announcements from the public cninfo query endpoint."""
        digits = "".join(character for character in symbol if character.isdigit())
        org_id = self.resolve_org_id(digits)
        column = "sse" if digits.startswith(("6", "9", "5")) else "szse"
        with self._client(self.settings.cninfo_announcement_base_url) as client:
            response = client.post(
                ANNOUNCEMENT_PATH,
                data={
                    "pageNum": 1,
                    "pageSize": page_size,
                    "column": column,
                    "tabName": "fulltext",
                    "stock": f"{digits},{org_id}",
                    "seDate": f"{start.isoformat()}~{end.isoformat()}",
                    "isHLtitle": "false",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code != 200:
            raise CninfoError(f"cninfo announcements failed: HTTP {response.status_code}")
        payload = response.json()
        results: list[dict[str, Any]] = []
        for item in payload.get("announcements") or []:
            timestamp = item.get("announcementTime")
            published_at = (
                datetime.fromtimestamp(timestamp / 1000, tz=UTC)
                if isinstance(timestamp, (int, float))
                else None
            )
            adjunct = str(item.get("adjunctUrl") or "")
            results.append(
                {
                    "symbol": digits,
                    "title": str(item.get("announcementTitle") or "").strip(),
                    "url": STATIC_PATH_PREFIX + adjunct if adjunct else "",
                    "category": item.get("announcementType"),
                    "published_at": published_at,
                    "source": "cninfo",
                }
            )
        return [item for item in results if item["title"] and item["url"]]


@lru_cache(maxsize=1)
def get_cninfo_client() -> CninfoClient:
    return CninfoClient(get_settings())
