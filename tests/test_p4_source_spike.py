from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import yaml
from scripts import run_p4_source_spike

from alphapilot.jobs import p4_source_spike
from alphapilot.jobs.registry import JOBS, run_job

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config/p4_source_spike_v2.yaml"


class _FakeResponse:
    def __init__(
        self,
        *,
        payload: object = None,
        text: str = "",
        status_code: int = 200,
    ) -> None:
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def json(self) -> object:
        return self._payload

    def read(self) -> None:
        return None


class _FakeHttpClient:
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.responder(method, url, kwargs)

    def close(self) -> None:
        self.closed = True


def _safe_snapshot() -> dict[str, Any]:
    return {
        "settings": {
            "trading_mode": "research",
            "live_trading_enabled": False,
            "paper_auto_trading_enabled": False,
            "futu_enable_trade": False,
            "futu_enable_account_mutation": False,
            "unlock_trade_permanently_blocked": True,
        },
        "trade_proposals": {"count": 1, "identity_sha256": "proposal-sha"},
        "broker_orders": {
            "count": 1,
            "identity_sha256": "order-sha",
            "non_simulate_count": 0,
        },
    }


def _source_result(source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "status": "usable_primary",
        "samples": [
            {
                "source": source_id,
                "symbol": "600519",
                "title": "测试事件",
                "url": f"https://example.test/{source_id}",
                "published_at": "2026-08-01T12:00:00+00:00",
                "available_time": "2026-08-01T12:00:01+00:00",
            }
        ],
        "failures": [],
    }


def _fake_ak_ths() -> pd.DataFrame:
    cast_requests = globals()["requests"]
    cast_requests.get("https://news.10jqka.com.cn/tapp/news/push/stock/")
    return pd.DataFrame(
        [
            {
                "标题": "同花顺测试新闻",
                "内容": "测试内容",
                "发布时间": "2026-08-01 10:00:00",
                "链接": "https://news.10jqka.com.cn/test",
            }
        ]
    )


def _fake_ak_cls() -> pd.DataFrame:
    make_request = globals()["make_request_with_retry_json"]
    make_request("https://www.cls.cn/nodeapi/telegraphList")
    return pd.DataFrame(
        [
            {
                "标题": "财联社测试快讯",
                "内容": "测试内容",
                "发布日期": "2026-08-01",
                "发布时间": "10:01:00",
            }
        ]
    )


def _fake_ak_cx() -> pd.DataFrame:
    cast_requests = globals()["requests"]
    cast_requests.get("https://cxdata.caixin.com/api/dataplus/sjtPc/jxNews")
    return pd.DataFrame(
        [
            {
                "tag": "市场",
                "summary": "财新测试摘要",
                "url": "https://cxdata.caixin.com/test",
            }
        ]
    )


def test_config_freezes_scope_safety_and_non_eastmoney_sources() -> None:
    config = p4_source_spike.load_source_spike_config(CONFIG_PATH)

    assert config.document["baseline_commit"].startswith("e288be6")
    assert config.document["scope_exclusions"]["eastmoney"]["status"] == (
        "not_probed_by_owner_directed_scope"
    )
    assert config.document["safety"]["required_futu_trade_enabled"] is False
    upstreams = [
        str(probe["upstream"])
        for probe in config.document["sources"]["akshare_non_eastmoney"]["function_probes"]
    ]
    assert all("eastmoney" not in upstream.lower() for upstream in upstreams)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("probe_date_shanghai",), "2026-08-03"),
        (("pre_registered_at",), "2026-08-03T00:00:00Z"),
        (("forbidden_upstreams",), ["eastmoney.com"]),
        (("scope_exclusions", "eastmoney", "reason"), "weakened"),
        (("network", "max_attempts_per_request"), 2),
        (("safety", "required_live_trading_enabled"), True),
        (("sources", "futu_auxiliary", "allowed_trade_methods"), ["place_order"]),
        (
            ("sources", "akshare_non_eastmoney", "function_probes", 0, "upstream"),
            "push2.eastmoney.com",
        ),
    ],
)
def test_config_rejects_any_frozen_contract_weakening(
    tmp_path: Path,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    document = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cursor = document
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        p4_source_spike.load_source_spike_config(candidate)


def test_request_budget_blocks_eastmoney_before_transport() -> None:
    client = _FakeHttpClient(lambda *_args: _FakeResponse())
    budget = p4_source_spike._RequestBudget(
        source_id="test",
        client=client,
        max_requests=1,
        min_interval_seconds=0,
        forbidden_hosts={"eastmoney.com"},
    )

    with pytest.raises(p4_source_spike.ProbeFailure, match="forbidden upstream"):
        budget.request("GET", "https://push2.eastmoney.com/api/test")

    assert client.calls == []
    assert budget.request_count == 0


def test_request_budget_records_parse_time_before_durable_available_time() -> None:
    client = _FakeHttpClient(lambda *_args: _FakeResponse(payload={"ok": True}))
    budget = p4_source_spike._RequestBudget(
        source_id="test",
        client=client,
        max_requests=1,
        min_interval_seconds=0,
        forbidden_hosts={"eastmoney.com"},
    )

    response, evidence = budget.request("GET", "https://example.test/news")
    p4_source_spike._decode_json(response, evidence)

    assert evidence["requested_at"] <= evidence["response_received_at"]
    assert evidence["parsed_at"] >= evidence["response_received_at"]
    assert str(evidence["parsed_at"]).endswith("+00:00")


def test_cninfo_probe_uses_bounded_two_step_contract() -> None:
    def responder(
        _method: str,
        url: str,
        kwargs: dict[str, object],
    ) -> _FakeResponse:
        data = kwargs["data"]
        assert isinstance(data, dict)
        if url.endswith("/topSearch/query"):
            symbol = str(data["keyWord"])
            return _FakeResponse(payload=[{"code": symbol, "orgId": f"org-{symbol}"}])
        stock = str(data.get("stock") or "")
        symbol = stock.split(",", 1)[0] or "600519"
        return _FakeResponse(
            payload={
                "announcements": [
                    {
                        "secCode": symbol,
                        "announcementTitle": f"{symbol} 测试公告",
                        "adjunctUrl": f"finalpage/2026-08-01/{symbol}.PDF",
                        "announcementTime": 1785571200000,
                    }
                ],
                "totalAnnouncement": 1,
                "hasMore": False,
            }
        )

    client = _FakeHttpClient(responder)
    config = p4_source_spike.load_source_spike_config(CONFIG_PATH)
    result = p4_source_spike._probe_cninfo(
        config,
        lambda **_kwargs: client,
        {"eastmoney.com"},
    )

    assert result["status"] == "usable_primary"
    assert result["request_count"] == 8
    assert result["retry_count"] == 0
    assert client.closed is True
    assert all(sample["observed_at"] for sample in result["samples"])
    assert all(sample["available_time"] is None for sample in result["samples"])


def test_sina_probe_extracts_native_title_and_url() -> None:
    html = """
    <html><body>
      <a href="http://finance.sina.com.cn/realstock/company/sh600519/nc.shtml">
        贵州茅台(600519.SH)
      </a>
      <div class="datelist">
        <a href="https://finance.sina.com.cn/stock/test-news.shtml">
          这是一条可验证的新浪个股新闻
        </a>
      </div>
    </body></html>
    """
    client = _FakeHttpClient(lambda *_args: _FakeResponse(text=html))
    config = p4_source_spike.load_source_spike_config(CONFIG_PATH)
    result = p4_source_spike._probe_sina(
        config,
        lambda **_kwargs: client,
        {"eastmoney.com"},
    )

    assert result["status"] == "usable_primary"
    assert result["request_count"] == 3
    assert result["samples"][0]["published_at"] is None
    assert result["samples"][0]["url"].startswith("https://finance.sina.com.cn/")
    assert "/realstock/company/" not in result["samples"][0]["url"]
    assert result["probes"][0]["all_anchors"] == 2
    assert result["probes"][0]["anchors_in_news_container"] == 1


def test_source_local_rate_limit_stops_without_retry() -> None:
    client = _FakeHttpClient(lambda *_args: _FakeResponse(status_code=429, text="访问过于频繁"))
    config = p4_source_spike.load_source_spike_config(CONFIG_PATH)
    result = p4_source_spike._probe_sina(
        config,
        lambda **_kwargs: client,
        {"eastmoney.com"},
    )

    assert result["status"] == "blocked"
    assert result["request_count"] == 1
    assert result["retry_count"] == 0
    assert result["failures"][0]["code"] == "http_rate_limited"


def test_akshare_probe_isolates_three_non_eastmoney_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_akshare = SimpleNamespace(
        __version__="test",
        stock_info_global_ths=_fake_ak_ths,
        stock_info_global_cls=_fake_ak_cls,
        stock_news_main_cx=_fake_ak_cx,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    client = _FakeHttpClient(lambda *_args: _FakeResponse(payload={"data": []}, text="{}"))
    config = p4_source_spike.load_source_spike_config(CONFIG_PATH)
    result = p4_source_spike._probe_akshare(
        config,
        lambda **_kwargs: client,
        {"eastmoney.com"},
    )

    assert result["status"] == "usable_primary"
    assert result["request_count"] == 3
    assert result["retry_count"] == 0
    assert {child["function"] for child in result["children"]} == {
        "stock_info_global_ths",
        "stock_info_global_cls",
        "stock_news_main_cx",
    }
    assert all("eastmoney" not in url for _, url, _ in client.calls)


def test_futu_probe_calls_quote_snapshot_only() -> None:
    class FakeFutu:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[Any] | None]] = []
            self.closed = False

        def quote_call_raw(
            self,
            method: str,
            args: list[Any] | None = None,
        ) -> pd.DataFrame:
            self.calls.append((method, args))
            return pd.DataFrame(
                [
                    {
                        "code": "SH.600519",
                        "name": "贵州茅台",
                        "last_price": 1400.0,
                        "change_rate": 1.2,
                        "amplitude": 2.3,
                        "update_time": "2026-08-01 15:00:00",
                    }
                ]
            )

        def capabilities(self) -> dict[str, Any]:
            return {"push_event_types": ["QUOTE"]}

        def close(self) -> None:
            self.closed = True

    client = FakeFutu()
    config = p4_source_spike.load_source_spike_config(CONFIG_PATH)
    result = p4_source_spike._probe_futu(config, lambda: client)

    assert client.calls == [("get_market_snapshot", [["SH.600519", "SZ.000001"]])]
    assert result["status"] == "usable_auxiliary"
    assert result["trade_methods_called"] == []
    assert client.closed is True


def test_full_spike_enforces_hash_pit_and_unchanged_safety(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = p4_source_spike.load_source_spike_config(CONFIG_PATH)
    monkeypatch.setattr(p4_source_spike, "_safety_snapshot", lambda _settings: _safe_snapshot())
    monkeypatch.setattr(
        p4_source_spike,
        "_probe_cninfo",
        lambda *_args: _source_result("cninfo"),
    )
    monkeypatch.setattr(
        p4_source_spike,
        "_probe_sina",
        lambda *_args: _source_result("sina_company_news"),
    )
    monkeypatch.setattr(
        p4_source_spike,
        "_probe_akshare",
        lambda *_args: _source_result("akshare_non_eastmoney"),
    )
    monkeypatch.setattr(
        p4_source_spike,
        "_probe_futu",
        lambda *_args: _source_result("futu_snapshot"),
    )

    p4_source_spike.register_p4_source_spike_job()
    try:
        record = run_job(
            "p4_source_spike",
            config_path=CONFIG_PATH,
            expected_config_sha256=config.sha256,
            execution_commit="test-commit",
            planned_report_path="docs/phase4/reports/test.json",
        )
    finally:
        JOBS.pop("p4_source_spike", None)
    stats = record.stats

    assert record.status == "ok"
    assert stats["safety_unchanged"] is True
    assert stats["pit_audit"]["available_time_coverage"] == 1.0
    assert stats["pit_audit"]["available_time_equals_published_at_count"] == 0
    assert stats["scope_exclusions"]["eastmoney"]["status"] == (
        "not_probed_by_owner_directed_scope"
    )


def test_hash_mismatch_stops_before_safety_or_source_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        p4_source_spike,
        "_safety_snapshot",
        lambda _settings: pytest.fail("safety must not run after config hash drift"),
    )

    with pytest.raises(p4_source_spike.JobExecutionError, match="config bytes changed"):
        p4_source_spike.run_p4_source_spike(
            config_path=CONFIG_PATH,
            expected_config_sha256="0" * 64,
            execution_commit="test-commit",
            planned_report_path="docs/phase4/reports/test.json",
        )


def test_expected_source_failure_is_durable_jobrun_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = p4_source_spike.load_source_spike_config(CONFIG_PATH)
    monkeypatch.setattr(p4_source_spike, "_safety_snapshot", lambda _settings: _safe_snapshot())

    def failed_cninfo(*_args: object) -> dict[str, Any]:
        raise p4_source_spike.ProbeFailure("transport_timeout", "bounded timeout")

    monkeypatch.setattr(p4_source_spike, "_probe_cninfo", failed_cninfo)
    monkeypatch.setattr(
        p4_source_spike,
        "_probe_sina",
        lambda *_args: _source_result("sina_company_news"),
    )
    monkeypatch.setattr(
        p4_source_spike,
        "_probe_akshare",
        lambda *_args: _source_result("akshare_non_eastmoney"),
    )
    monkeypatch.setattr(
        p4_source_spike,
        "_probe_futu",
        lambda *_args: _source_result("futu_snapshot"),
    )

    p4_source_spike.register_p4_source_spike_job()
    try:
        record = run_job(
            "p4_source_spike",
            config_path=CONFIG_PATH,
            expected_config_sha256=config.sha256,
            execution_commit="test-commit",
            planned_report_path="docs/phase4/reports/test.json",
        )
    finally:
        JOBS.pop("p4_source_spike", None)

    assert record.status == "ok"
    assert record.stats["sources"]["cninfo"]["status"] == "unavailable"
    assert record.stats["source_failures"][0]["source_id"] == "cninfo"
    assert record.stats["source_failures"][0]["code"] == "transport_timeout"
    assert record.stats["pit_audit"]["available_time_coverage"] == 1.0


def test_runner_rejects_alias_symlink_before_read_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias = tmp_path / "config-alias.yaml"
    alias.symlink_to(CONFIG_PATH)
    monkeypatch.setattr(
        run_p4_source_spike,
        "_arguments",
        lambda: argparse.Namespace(
            config=alias,
            report=run_p4_source_spike.DEFAULT_REPORT,
        ),
    )

    with pytest.raises(ValueError, match="config path is frozen"):
        run_p4_source_spike.main()


def test_report_writer_is_create_only(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    run_p4_source_spike._write_new_json(path, {"gate": {"ok": True}})

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        run_p4_source_spike._write_new_json(path, {"gate": {"ok": False}})

    assert '"ok": true' in path.read_text(encoding="utf-8")
