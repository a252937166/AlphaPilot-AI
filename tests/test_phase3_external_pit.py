from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from alphapilot.backtest import data_health, external_pit
from alphapilot.backtest.external_pit import (
    ExternalObservation,
    ExternalPITError,
    LiveExternalPITSources,
    ManifestValidationError,
    build_preflight_plan,
    build_signed_evidence_v2,
    build_trial_document,
    load_pit_manifest,
)


def _manifest_payload(samples: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": data_health.PIT_MANIFEST_SCHEMA_VERSION,
        "selection": samples["selection"],
        "seed": samples["seed"],
        "sample_size_per_table": samples["sample_size_per_table"],
        "daily_bars_with_adj": samples["daily_bars_with_adj"],
        "financial_indicators": samples["financial_indicators"],
        "valuation_daily": samples["valuation_daily"],
    }


def _report_document() -> dict[str, Any]:
    pit_samples: dict[str, Any] = {
        "selection": "deterministic pseudo-random row-id sampling",
        "seed": 20260725,
        "sample_size_per_table": 1,
        "daily_bars_with_adj": [
            {
                "symbol": "600519",
                "trade_date": "2025-01-02",
                "close": 10.0,
                "source": "baostock",
                "adj_factor": 2.0,
                "adj_source": "baostock-hfq",
                "adj_anchor_date": "2025-01-03",
                "adj_anchor_factor": 1.0,
            }
        ],
        "financial_indicators": [
            {
                "symbol": "000573",
                "report_period": "2023Q2",
                "metric": "net_profit_yoy",
                "value": -0.93185,
                "source": "baostock",
                "available_time": "2023-08-31 16:00:00.000000",
                "payload": "{}",
            }
        ],
        "valuation_daily": [
            {
                "symbol": "000565",
                "trade_date": "2022-11-21",
                "pe_ttm": 31.48867883,
                "pb_mrq": 1.98407603,
                "ps_ttm": 5.521201,
                "source": "em",
                "available_time": "2022-11-21 07:00:00.000000",
            }
        ],
    }
    canonical = json.dumps(
        _manifest_payload(pit_samples),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    pit_samples["manifest_schema_version"] = data_health.PIT_MANIFEST_SCHEMA_VERSION
    pit_samples["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    pit_samples["external_source_pairing"] = "not_performed_by_this_read_only_script"
    return {"report_version": "p3.3-s6-v2", "pit_samples": pit_samples}


def _write_report(tmp_path: Path, document: dict[str, Any] | None = None) -> Path:
    path = tmp_path / "s6-report.json"
    path.write_text(
        json.dumps(document or _report_document(), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class _FutuStub:
    def __init__(self) -> None:
        self.closed = False

    def quote_call_raw(
        self,
        _method: str,
        *,
        kwargs: dict[str, object],
    ) -> tuple[pd.DataFrame, None]:
        target = str(kwargs["start"])
        if target == "2025-01-02":
            close = 20.0 if kwargs["autype"] == "hfq" else 10.0
        else:
            assert target == "2025-01-03"
            close = 15.0
        return (
            pd.DataFrame(
                [{"time_key": f"{target} 00:00:00", "close": close}]
            ),
            None,
        )

    def close(self) -> None:
        self.closed = True


class _ResponseStub:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _valuation_fetcher(
    _symbol: str,
    *,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    assert start_date == end_date == date(2022, 11, 21)
    return pd.DataFrame(
        [
            {
                "trade_date": date(2022, 11, 21),
                "pe_ttm": 31.48867883,
                "pb_mrq": 1.98407603,
                "ps_ttm": 5.521201,
            }
        ]
    )


def _financial_get(_url: str, **kwargs: Any) -> _ResponseStub:
    assert kwargs["params"]["filter"] == '(SECUCODE="000573.SZ")'
    return _ResponseStub(
        {
            "result": {
                "data": [
                    {
                        "REPORT_DATE": "2023-06-30",
                        "NOTICE_DATE": "2023-08-31",
                        "PARENTNETPROFITTZ": -93.185,
                    }
                ]
            }
        }
    )


def test_live_routes_build_unsigned_trial_and_v2_evidence_without_network(
    tmp_path: Path,
) -> None:
    report_path = _write_report(tmp_path)
    manifest = load_pit_manifest(report_path)
    futu = _FutuStub()
    sources = LiveExternalPITSources(
        futu_client=futu,
        valuation_fetcher=_valuation_fetcher,
        http_get=_financial_get,
    )

    trial = build_trial_document(
        manifest,
        sources,
        generated_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
    )

    assert trial["approved"] is False
    assert trial["secrets_included"] is False
    assert trial["summary"] == {
        "sample_count": 3,
        "match": 3,
        "mismatch": 0,
        "unavailable": 0,
        "eligible_for_v2_signing": True,
    }
    assert [sample["external_source"] for sample in trial["samples"]] == [
        "futu-unadjusted-day+futu-hfq-day",
        "eastmoney-f10-main-financial",
        "eastmoney-stock-value-em",
    ]
    daily_checks = trial["samples"][0]["checked_values"]
    assert daily_checks["source"] == {
        "local_value": "baostock",
        "external_value": "baostock",
        "pass": True,
    }
    assert daily_checks["adj_factor"]["external_value"] == pytest.approx(2.0)
    sources.close()
    assert futu.closed is True
    assert sources._futu_client is None
    sources.close()

    evidence = build_signed_evidence_v2(
        trial,
        reviewer_role="data_architect",
        reviewed_at=datetime(2026, 7, 25, 20, 0, tzinfo=UTC),
    )
    evidence_path = tmp_path / "evidence-v2.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    pit_samples = _report_document()["pit_samples"]
    accepted = data_health._external_pairing_evidence(
        evidence_path,
        pit_samples=pit_samples,
    )
    assert accepted["accepted"] is True
    assert accepted["sample_count"] == 3
    assert accepted["schema_version"] == data_health.EXTERNAL_PAIRING_SCHEMA_VERSION


def test_trial_fails_closed_and_never_serializes_upstream_secret(
    tmp_path: Path,
) -> None:
    manifest = load_pit_manifest(_write_report(tmp_path))

    class BrokenSources:
        def route_for(self, table: str, _sample: object) -> str:
            return {
                "daily_bars": "futu-unadjusted-day+futu-hfq-day",
                "financial_indicators": "eastmoney-f10-main-financial",
                "valuation_daily": "eastmoney-stock-value-em",
            }[table]

        def fetch(self, _table: str, _sample: object) -> ExternalObservation:
            raise RuntimeError("token=do-not-serialize-this-secret")

    trial = build_trial_document(manifest, BrokenSources())
    serialized = json.dumps(trial)
    assert "do-not-serialize-this-secret" not in serialized
    assert trial["summary"]["unavailable"] == 3
    assert trial["summary"]["eligible_for_v2_signing"] is False
    assert {sample["error_type"] for sample in trial["samples"]} == {"RuntimeError"}
    with pytest.raises(ExternalPITError, match="not eligible"):
        build_signed_evidence_v2(
            trial,
            reviewer_role="data_architect",
            reviewed_at=datetime.now(UTC),
        )


def test_external_route_rejects_baostock_even_when_observation_would_match(
    tmp_path: Path,
) -> None:
    manifest = load_pit_manifest(_write_report(tmp_path))

    class ForbiddenSources:
        def route_for(self, _table: str, _sample: object) -> str:
            return "BaoStock-second-look"

        def fetch(self, _table: str, _sample: object) -> ExternalObservation:
            raise AssertionError("fetch must not run after forbidden route")

    with pytest.raises(ExternalPITError, match="forbidden"):
        build_trial_document(manifest, ForbiddenSources())


def test_external_route_must_match_the_audited_table_route(tmp_path: Path) -> None:
    manifest = load_pit_manifest(_write_report(tmp_path))

    class WrongTableSources:
        def route_for(self, _table: str, _sample: object) -> str:
            return "eastmoney-stock-value-em"

        def fetch(self, _table: str, _sample: object) -> ExternalObservation:
            raise AssertionError("fetch must not run after wrong-table route")

    with pytest.raises(ExternalPITError, match="not audited for daily_bars"):
        build_trial_document(manifest, WrongTableSources())


def test_manifest_hash_and_duplicate_json_keys_fail_closed(tmp_path: Path) -> None:
    document = _report_document()
    document["pit_samples"]["daily_bars_with_adj"][0]["close"] = 11.0
    with pytest.raises(ManifestValidationError, match="SHA-256"):
        load_pit_manifest(_write_report(tmp_path, document))

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"pit_samples": {}, "pit_samples": {}}', encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="duplicate JSON key"):
        load_pit_manifest(duplicate)


def test_preflight_is_network_free_and_blocks_tushare_over_budget(
    tmp_path: Path,
) -> None:
    manifest = load_pit_manifest(_write_report(tmp_path))

    eastmoney = build_preflight_plan(
        manifest,
        financial_source="em-f10",
        max_tushare_calls=1,
    )
    assert eastmoney["network_called"] is False
    assert eastmoney["ready_for_trial"] is True
    assert eastmoney["routes"]["financial_indicators"]["planned_symbol_calls"] == 1

    tushare = build_preflight_plan(
        manifest,
        financial_source="tushare",
        max_tushare_calls=0,
    )
    assert tushare["network_called"] is False
    assert tushare["ready_for_trial"] is False
    assert tushare["blockers"] == [
        "TUSHARE_CALL_BUDGET_EXCEEDED:required=1,limit=0"
    ]


def test_tushare_exact_mapping_fails_closed_for_ocf_to_profit() -> None:
    with pytest.raises(external_pit.ExternalSourceError, match="no audited exact"):
        LiveExternalPITSources._financial_value(
            {"ocf_to_opincome": 30.0},
            "ocf_to_profit",
            source="tushare",
        )


@pytest.mark.parametrize(
    ("metric", "field", "raw", "expected"),
    [
        ("roe", "ROEJQ", 4.04, 0.0404),
        ("net_profit_yoy", "PARENTNETPROFITTZ", -12.5, -0.125),
        ("ocf_to_profit", "NCO_NETPROFIT", -33.146695161, -33.146695161),
        ("debt_ratio", "ZCFZL", 41.2, 0.412),
    ],
)
def test_eastmoney_exact_financial_aliases_and_scales(
    metric: str,
    field: str,
    raw: float,
    expected: float,
) -> None:
    value = LiveExternalPITSources._financial_value(
        {field: raw},
        metric,
        source="em-f10",
    )
    assert value == pytest.approx(expected)


@pytest.mark.parametrize("source", ["em-f10", "tushare"])
def test_revenue_yoy_requires_an_exact_main_business_revenue_mapping(
    source: str,
) -> None:
    with pytest.raises(external_pit.ExternalSourceError, match="no audited exact"):
        LiveExternalPITSources._financial_value(
            {
                "TOTALOPERATEREVETZ": 7.3,
                "or_yoy": 7.3,
            },
            "revenue_yoy",
            source=source,
        )
