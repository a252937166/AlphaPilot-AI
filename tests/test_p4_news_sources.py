from __future__ import annotations

from typing import Any

import pytest

from alphapilot.cninfo import client as cninfo_client
from alphapilot.cninfo.client import CninfoClient, CninfoError
from alphapilot.core.config import Settings
from alphapilot.data.provenance import AUDITED_NEWS_SOURCES


def test_cninfo_transport_requires_https_and_enables_certificate_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class FakeHttpClient:
        pass

    def fake_http_client(**kwargs: Any) -> FakeHttpClient:
        observed.update(kwargs)
        return FakeHttpClient()

    monkeypatch.setattr(cninfo_client.httpx, "Client", fake_http_client)
    client = CninfoClient(Settings(_env_file=None))

    created = client._client("https://www.cninfo.com.cn")

    assert isinstance(created, FakeHttpClient)
    assert observed["base_url"] == "https://www.cninfo.com.cn"
    assert observed["verify"] is True
    client._client("http://www.cninfo.com.cn")
    assert observed["base_url"] == "https://www.cninfo.com.cn"
    with pytest.raises(CninfoError, match="require HTTPS"):
        client._client("http://example.test")


def test_cninfo_defaults_and_static_urls_are_https_only() -> None:
    settings = Settings(_env_file=None)

    assert settings.cninfo_base_url.startswith("https://")
    assert settings.cninfo_announcement_base_url == "http://www.cninfo.com.cn"
    assert cninfo_client.STATIC_PATH_PREFIX.startswith("https://")


def test_audited_news_sources_are_fine_grained_and_exclude_unapproved_feeds() -> None:
    assert frozenset({"akshare_ths", "cninfo", "sina_company_news"}) == (AUDITED_NEWS_SOURCES)
    assert "akshare_non_eastmoney" not in AUDITED_NEWS_SOURCES
    assert "akshare_cls" not in AUDITED_NEWS_SOURCES
    assert "akshare_caixin" not in AUDITED_NEWS_SOURCES
    assert "futu_snapshot" not in AUDITED_NEWS_SOURCES
