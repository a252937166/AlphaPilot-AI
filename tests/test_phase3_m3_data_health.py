from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.backtest import data_health
from alphapilot.backtest.data_health import (
    build_data_health_report,
    readonly_connection,
    render_data_health_markdown,
)
from alphapilot.db.models import (
    AdjFactor,
    Base,
    DailyBar,
    FinancialIndicator,
    SectorConstituent,
    SectorConstituentSnapshot,
    SectorFlowDaily,
    Security,
    ValuationDaily,
)
from alphapilot.engines.factors import FACTOR_SET

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _utc_at_shanghai(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=SHANGHAI).astimezone(UTC)


def _database(
    tmp_path: Path,
    *,
    trading_day_count: int = 5,
    membership_refresh_index: int = 0,
) -> Path:
    database_path = tmp_path / "health.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    trading_days = [
        date(2026, 7, 20) + timedelta(days=index)
        for index in range(trading_day_count)
    ]
    symbols = ("SH.000001", "600519", "000001")
    with Session(engine) as session:
        session.add_all(
            [
                Security(
                    symbol=symbol,
                    market="CN",
                    list_status="listed",
                    name=symbol,
                    listed_date="2010-01-01",
                )
                for symbol in symbols
                if symbol != "SH.000001"
            ]
        )
        for day in trading_days:
            for symbol in symbols:
                session.add(
                    DailyBar(
                        symbol=symbol,
                        trade_date=day,
                        open=10.0,
                        high=11.0,
                        low=9.0,
                        close=10.5,
                        volume=1_000.0,
                        amount=10_000.0,
                        source="baostock",
                    )
                )
                session.add(
                    AdjFactor(
                        symbol=symbol,
                        trade_date=day,
                        adj_factor=1.0,
                        source="baostock-hfq",
                    )
                )
                session.add(
                    ValuationDaily(
                        symbol=symbol,
                        trade_date=day,
                        pe_ttm=20.0,
                        pb_mrq=2.0,
                        ps_ttm=3.0,
                        source="em",
                        available_time=_utc_at_shanghai(day, 15),
                    )
                )
            session.add(
                SectorFlowDaily(
                    plate_code="BK0001",
                    trade_date=day,
                    net_inflow=100.0,
                    main_inflow=80.0,
                    source="futu-daily",
                )
            )
        session.add(
            SectorConstituent(
                plate_code="BK0001",
                plate_name="测试",
                symbol="600519",
                name="贵州茅台",
                refreshed_at=_utc_at_shanghai(
                    trading_days[membership_refresh_index],
                    14,
                ),
            )
        )
        for snapshot_date in trading_days[membership_refresh_index:]:
            session.add(
                SectorConstituentSnapshot(
                    plate_code="BK0001",
                    symbol="600519",
                    as_of_date=snapshot_date,
                    available_time=_utc_at_shanghai(snapshot_date, 14),
                )
            )
        report_period_end = date(2025, 12, 31)
        publication_date = date(2026, 3, 30)
        available_time = datetime.combine(
            publication_date + timedelta(days=1),
            time.min,
            tzinfo=SHANGHAI,
        ).astimezone(UTC)
        for metric in (
            "roe",
            "net_profit_yoy",
            "ocf_to_profit",
            "debt_ratio",
            "revenue_yoy",
        ):
            session.add(
                FinancialIndicator(
                    symbol="600519",
                    report_period="2025Q4",
                    metric=metric,
                    value=0.1,
                    source="baostock",
                    available_time=available_time,
                    payload={
                        "available_time_basis": "provider_pub_date_end_of_day",
                        "approx": False,
                        "stat_date": report_period_end.isoformat(),
                        "pub_dates": [publication_date.isoformat()],
                    },
                )
            )
        session.commit()
    engine.dispose()
    return database_path


def _financial_periods_20() -> list[tuple[str, date]]:
    periods: list[tuple[str, date]] = []
    for year in range(2021, 2027):
        for quarter, month_day in (
            (1, (3, 31)),
            (2, (6, 30)),
            (3, (9, 30)),
            (4, (12, 31)),
        ):
            label = f"{year}Q{quarter}"
            if label < "2021Q2" or label > "2026Q1":
                continue
            periods.append((label, date(year, month_day[0], month_day[1])))
    assert len(periods) == 20
    return periods


def _replace_with_full_financial_history(database_path: Path) -> None:
    engine = create_engine(f"sqlite:///{database_path}")
    with Session(engine) as session:
        session.query(FinancialIndicator).delete()
        for symbol in ("600519", "000001"):
            for report_period, period_end in _financial_periods_20():
                publication_date = period_end + timedelta(days=30)
                available_time = datetime.combine(
                    publication_date + timedelta(days=1),
                    time.min,
                    tzinfo=SHANGHAI,
                ).astimezone(UTC)
                for metric in (
                    "roe",
                    "net_profit_yoy",
                    "ocf_to_profit",
                    "debt_ratio",
                    "revenue_yoy",
                ):
                    session.add(
                        FinancialIndicator(
                            symbol=symbol,
                            report_period=report_period,
                            metric=metric,
                            value=0.1,
                            source="baostock",
                            available_time=available_time,
                            payload={
                                "available_time_basis": (
                                    "provider_pub_date_end_of_day"
                                ),
                                "approx": False,
                                "stat_date": period_end.isoformat(),
                                "pub_dates": [publication_date.isoformat()],
                            },
                        )
                    )
        session.commit()
    engine.dispose()


def _external_evidence_document(
    report: dict[str, Any],
) -> dict[str, Any]:
    pit_samples = report["pit_samples"]
    assert isinstance(pit_samples, dict)
    samples: list[dict[str, Any]] = []
    table_specs = (
        ("daily_bars", "daily_bars_with_adj", ("symbol", "trade_date")),
        (
            "financial_indicators",
            "financial_indicators",
            ("symbol", "report_period", "metric"),
        ),
        ("valuation_daily", "valuation_daily", ("symbol", "trade_date")),
    )
    external_sources = {
        "daily_bars": "futu-unadjusted-day+futu-hfq-day",
        "financial_indicators": "eastmoney-f10-main-financial",
        "valuation_daily": "eastmoney-stock-value-em",
    }
    for table, report_key, key_fields in table_specs:
        selected = pit_samples[report_key]
        assert isinstance(selected, list)
        for row in selected:
            assert isinstance(row, dict)
            checked_values: dict[str, dict[str, Any]] = {}
            for field in data_health.PIT_CHECKED_FIELDS[table]:
                local_value = row[field]
                if field in data_health.PIT_NUMERIC_CHECKED_FIELDS:
                    external_value = (
                        "N/A" if local_value is None else local_value
                    )
                    if local_value is None:
                        tolerance = 0.0
                    else:
                        absolute, relative = (
                            data_health.PIT_NUMERIC_TOLERANCE_POLICY[field]
                        )
                        tolerance = max(
                            absolute,
                            relative * abs(float(local_value)),
                        )
                    checked_values[field] = {
                        "local_value": local_value,
                        "external_value": external_value,
                        "pass": True,
                        "tolerance": tolerance,
                    }
                else:
                    checked_values[field] = {
                        "local_value": local_value,
                        "external_value": local_value,
                        "pass": True,
                    }
            samples.append(
                {
                    "table": table,
                    "key": {field: str(row[field]) for field in key_fields},
                    "verdict": "match",
                    "external_source": external_sources[table],
                    "checked_values": checked_values,
                }
            )
    return {
        "schema_version": data_health.EXTERNAL_PAIRING_SCHEMA_VERSION,
        "pit_manifest_schema_version": pit_samples["manifest_schema_version"],
        "pit_manifest_sha256": pit_samples["manifest_sha256"],
        "approved": True,
        "reviewed_at": "2026-07-25T20:00:00+08:00",
        "reviewer_role": "data_architect",
        "seed": pit_samples["seed"],
        "sample_size_per_table": pit_samples["sample_size_per_table"],
        "samples": samples,
    }


def _sufficient_probe(_session: Session, _as_of: date) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            factor: [0.1, -0.1, 0.2]
            for factor in FACTOR_SET
        },
        index=["SH.000001", "600519", "000001"],
    )
    frame.attrs["eligible"] = 3
    return frame


def _missing_financial_probe(_session: Session, _as_of: date) -> pd.DataFrame:
    frame = _sufficient_probe(_session, _as_of)
    for factor in (
        "roe",
        "net_profit_yoy",
        "ocf_to_profit",
        "debt_ratio",
        "revenue_yoy",
    ):
        frame[factor] = float("nan")
    return frame


def test_s6_report_is_read_only_and_blocks_incomplete_s2(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    before = _sha256(database_path)

    report = build_data_health_report(
        database_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=2,
        factor_probe=_sufficient_probe,
    )

    assert _sha256(database_path) == before
    assert report["database"]["open_mode"] == "ro"
    assert report["database"]["query_only"] is True
    assert report["gate"]["status"] == "blocked"
    assert report["gate"]["ready_for_s7"] is False
    assert {item["code"] for item in report["gate"]["blockers"]} == {
        "S2_FINANCIAL_COVERAGE_INCOMPLETE",
        "EXTERNAL_PIT_PAIRING_PENDING",
    }
    assert report["input_coverage"]["financial_indicators"]["symbols"] == 1
    assert report["input_coverage"]["financial_indicators"]["symbol_coverage_ratio"] == (
        pytest.approx(1 / 2)
    )
    assert report["input_coverage"]["financial_indicators"]["pit"]["anomaly_rows"] == 0
    assert report["input_coverage"]["valuation_daily"]["pit_anomaly_rows"] == 0
    assert report["daily_adj_key_audit"] == {
        "audited_daily_keys_without_adj": 0,
        "adj_keys_without_audited_daily": 0,
    }
    factors = {
        item["factor"]: item for item in report["factor_availability"]["factors"]
    }
    assert len(factors) == 13
    assert factors["sector_strength"]["status"] == "live_only"
    assert (
        factors["net_inflow_5d"]["status"]
        == "history_excluded_pit_gap"
    )
    assert all(
        item["status"] == "sufficient"
        for factor, item in factors.items()
        if factor not in {"sector_strength", "net_inflow_5d"}
    )
    assert report["pit_samples"]["external_source_pairing"] == (
        "not_performed_by_this_read_only_script"
    )
    for sample in report["pit_samples"]["daily_bars_with_adj"]:
        assert sample["adj_anchor_date"] > sample["trade_date"]
        assert sample["adj_anchor_factor"] > 0
    warning_codes = {item["code"] for item in report["gate"]["warnings"]}
    assert "SECTOR_FLOW_ONE_YEAR_LIMIT" in warning_codes
    assert "SECTOR_FLOW_FIXED_TOP5_LOOKAHEAD" in warning_codes
    assert "FACTOR_NET_INFLOW_5D_HISTORY_EXCLUDED_PIT_GAP" in warning_codes
    assert report["historical_factor_scope"]["candidate_count"] == 11
    assert json.loads(json.dumps(report, ensure_ascii=False))["gate"]["status"] == "blocked"
    for table in ("daily_bars", "adj_factors", "valuation_daily"):
        plan = report["input_coverage"][table]["latest_broad_cross_section"][
            "query_plan"
        ]
        normalized = [str(detail).upper() for detail in plan]
        assert plan
        assert not any("SCAN OBSERVED" in detail for detail in normalized)
        assert any("SEARCH OBSERVED" in detail for detail in normalized)


def test_flow_factor_is_recorded_as_nonblocking_exclusion_and_tracks_forward_pit(
    tmp_path: Path,
) -> None:
    database_path = _database(
        tmp_path,
        trading_day_count=9,
        membership_refresh_index=7,
    )

    report = build_data_health_report(
        database_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    schedule = report["factor_availability"]["schedule"]["sector_flow_one_year"]
    assert schedule == ["2026-07-24", "2026-07-26", "2026-07-28"]
    visibility = report["factor_availability"]["sector_membership_pit_visibility"]
    assert [visibility[probe]["visible"] for probe in schedule] == [False, False, True]
    flow = next(
        item
        for item in report["factor_availability"]["factors"]
        if item["factor"] == "net_inflow_5d"
    )
    assert flow["status"] == "history_excluded_pit_gap"
    assert flow["cause_class"] == "history_excluded_pit_gap"
    assert flow["probes"] == []
    assert flow["historical_candidate"] is False
    assert flow["live_forward"] is True
    blocker_codes = {item["code"] for item in report["gate"]["blockers"]}
    assert not any(code.startswith("FACTOR_NET_INFLOW_5D") for code in blocker_codes)


def test_each_broad_cross_section_must_qualify_independently(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    writable = sqlite3.connect(database_path)
    try:
        for table in ("daily_bars", "adj_factors", "valuation_daily"):
            writable.execute(
                f"""
                DELETE FROM {table}
                WHERE symbol IN (
                  SELECT symbol
                  FROM securities
                  WHERE market = 'CN' AND list_status = 'listed'
                )
                """
            )
        writable.commit()
    finally:
        writable.close()

    report = build_data_health_report(
        database_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    blocker_codes = {item["code"] for item in report["gate"]["blockers"]}
    assert {
        "DAILY_BARS_BROAD_CROSS_SECTION_MISSING",
        "ADJ_FACTORS_BROAD_CROSS_SECTION_MISSING",
        "VALUATION_DAILY_BROAD_CROSS_SECTION_MISSING",
    }.issubset(blocker_codes)
    for table in ("daily_bars", "adj_factors", "valuation_daily"):
        broad = report["input_coverage"][table]["latest_broad_cross_section"]
        assert broad["qualified"] is False
        assert broad["date"] is None
        assert broad["entities"] < broad["minimum_entities"]


def test_financial_gate_rejects_ninety_percent_symbols_with_one_old_quarter(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    engine = create_engine(f"sqlite:///{database_path}")
    added_symbols = [f"{600000 + index:06d}" for index in range(8)]
    trading_days = [date(2026, 7, 20) + timedelta(days=index) for index in range(5)]
    period_end = date(2022, 3, 31)
    publication_date = date(2022, 4, 30)
    available_time = datetime.combine(
        publication_date + timedelta(days=1),
        time.min,
        tzinfo=SHANGHAI,
    ).astimezone(UTC)
    with Session(engine) as session:
        for symbol in added_symbols:
            session.add(
                Security(
                    symbol=symbol,
                    market="CN",
                    list_status="listed",
                    listed_date="2010-01-01",
                    name=symbol,
                )
            )
            for day in trading_days:
                session.add(
                    DailyBar(
                        symbol=symbol,
                        trade_date=day,
                        open=10.0,
                        high=11.0,
                        low=9.0,
                        close=10.5,
                        volume=1_000.0,
                        amount=10_000.0,
                        source="baostock",
                    )
                )
                session.add(
                    AdjFactor(
                        symbol=symbol,
                        trade_date=day,
                        adj_factor=1.0,
                        source="baostock-hfq",
                    )
                )
                session.add(
                    ValuationDaily(
                        symbol=symbol,
                        trade_date=day,
                        pe_ttm=20.0,
                        pb_mrq=2.0,
                        ps_ttm=3.0,
                        source="em",
                        available_time=_utc_at_shanghai(day, 15),
                    )
                )
            for metric in (
                "roe",
                "net_profit_yoy",
                "ocf_to_profit",
                "debt_ratio",
                "revenue_yoy",
            ):
                session.add(
                    FinancialIndicator(
                        symbol=symbol,
                        report_period="2022Q1",
                        metric=metric,
                        value=0.1,
                        source="baostock",
                        available_time=available_time,
                        payload={
                            "available_time_basis": (
                                "provider_pub_date_end_of_day"
                            ),
                            "approx": False,
                            "stat_date": period_end.isoformat(),
                            "pub_dates": [publication_date.isoformat()],
                        },
                    )
                )
        session.commit()
    engine.dispose()
    report = build_data_health_report(
        database_path,
        as_of_date=date(2026, 7, 25),
        minimum_market_coverage=0.90,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    financial = report["input_coverage"]["financial_indicators"]
    assert financial["symbol_coverage_ratio"] == pytest.approx(0.9)
    assert financial["depth_contract"]["target_quarters"] == 40
    roe_depth = financial["depth_contract"]["metric_depth"]["roe"]
    assert roe_depth["depth_sufficient_ratio"] == 0.0
    assert roe_depth["cross_year_sufficient_ratio"] == 0.0
    assert roe_depth["fresh_ratio"] == 0.0
    assert roe_depth["representative_gaps"]
    blocker_codes = {item["code"] for item in report["gate"]["blockers"]}
    assert "S2_FINANCIAL_COVERAGE_INCOMPLETE" not in blocker_codes
    assert "FINANCIAL_ROE_DEPTH" in blocker_codes
    assert "FINANCIAL_ROE_CROSS_YEAR" in blocker_codes
    assert "FINANCIAL_ROE_FRESHNESS" in blocker_codes
    assert report["gate"]["automated_checks_pass"] is False


def test_provider_publication_date_basis_ratio_is_a_hard_gate(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    report_period_end = date(2025, 12, 31)
    fallback_time = datetime.combine(
        report_period_end + timedelta(days=45),
        time.min,
        tzinfo=SHANGHAI,
    ).astimezone(UTC)
    fallback_payload = json.dumps(
        {
            "available_time_basis": "stat_date_plus_45_days",
            "approx": True,
            "stat_date": report_period_end.isoformat(),
            "pub_dates": [],
        }
    )
    writable = sqlite3.connect(database_path)
    try:
        writable.execute(
            """
            UPDATE financial_indicators
            SET available_time = ?, payload = ?
            """,
            (fallback_time.isoformat(), fallback_payload),
        )
        writable.commit()
    finally:
        writable.close()

    report = build_data_health_report(
        database_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    pit = report["input_coverage"]["financial_indicators"]["pit"]
    assert pit["anomaly_rows"] == 0
    assert pit["provider_pub_date_end_of_day_ratio"] == 0.0
    assert "FINANCIAL_PROVIDER_PUB_DATE_BASIS" in {
        item["code"] for item in report["gate"]["blockers"]
    }


def test_external_pairing_requires_exact_strict_json_and_reports_metadata_only(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    _replace_with_full_financial_history(database_path)

    unsigned = build_data_health_report(
        database_path,
        as_of_date=date(2026, 7, 25),
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    assert unsigned["gate"]["automated_checks_pass"] is True
    assert unsigned["gate"]["ready_for_s7"] is False
    assert {item["code"] for item in unsigned["gate"]["blockers"]} == {
        "EXTERNAL_PIT_PAIRING_PENDING"
    }
    evidence_path = tmp_path / "architect-pit-pairing.json"
    checked_marker = "futu-unadjusted-day+futu-hfq-day"
    evidence_document = _external_evidence_document(unsigned)
    evidence_content = json.dumps(
        evidence_document,
        ensure_ascii=False,
        indent=2,
    )
    evidence_path.write_text(evidence_content, encoding="utf-8")

    signed = build_data_health_report(
        database_path,
        as_of_date=date(2026, 7, 25),
        external_pit_pairing_evidence=evidence_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    assert signed["gate"]["automated_checks_pass"] is True
    assert signed["gate"]["ready_for_s7"] is True
    assert signed["gate"]["status"] == "pass"
    evidence_report = signed["external_pit_pairing"]
    assert evidence_report == {
        "accepted": True,
        "basename": evidence_path.name,
        "sha256": hashlib.sha256(evidence_content.encode()).hexdigest(),
        "bytes": len(evidence_content.encode()),
        "schema_version": data_health.EXTERNAL_PAIRING_SCHEMA_VERSION,
        "pit_manifest_schema_version": data_health.PIT_MANIFEST_SCHEMA_VERSION,
        "pit_manifest_sha256": unsigned["pit_samples"]["manifest_sha256"],
        "reviewer_role": "data_architect",
        "reviewed_at": "2026-07-25T20:00:00+08:00",
        "sample_count": 3,
    }
    assert signed["pit_samples"]["external_source_pairing"] == "evidence_supplied"
    signed_markdown = render_data_health_markdown(signed)
    serialized = json.dumps(signed, ensure_ascii=False)
    assert checked_marker not in serialized
    assert str(evidence_path.resolve()) not in serialized
    assert checked_marker not in signed_markdown
    assert str(evidence_path.resolve()) not in signed_markdown
    assert evidence_report["sha256"] in signed_markdown
    assert evidence_path.name in signed_markdown


def test_external_pairing_rejects_missing_empty_and_arbitrary_text(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    missing = tmp_path / "missing.md"
    with pytest.raises(FileNotFoundError, match="evidence not found"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=missing,
            factor_probe=_sufficient_probe,
        )
    empty = tmp_path / "empty.md"
    empty.touch()
    with pytest.raises(ValueError, match="must not be empty"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=empty,
            factor_probe=_sufficient_probe,
        )
    arbitrary_documents = {
        "README.md": "# Approval\nLooks good.\n",
        ".env": "PIT_APPROVED=true\n",
        "one-line.txt": "approved",
    }
    for filename, content in arbitrary_documents.items():
        candidate = tmp_path / filename
        candidate.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="must be strict JSON"):
            build_data_health_report(
                database_path,
                external_pit_pairing_evidence=candidate,
                factor_probe=_sufficient_probe,
            )
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (data_health.MAX_EXTERNAL_EVIDENCE_BYTES + 1))
    with pytest.raises(ValueError, match="evidence exceeds"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=oversized,
            factor_probe=_sufficient_probe,
        )


def test_external_pairing_rejects_forbidden_source_inflated_tolerance_and_nonhuman_role(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    baseline = build_data_health_report(
        database_path,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )
    evidence_path = tmp_path / "forged-evidence.json"

    forbidden_source = _external_evidence_document(baseline)
    forbidden_samples = forbidden_source["samples"]
    assert isinstance(forbidden_samples, list)
    forbidden_samples[0]["external_source"] = "BaoStock-forbidden-route"
    evidence_path.write_text(json.dumps(forbidden_source), encoding="utf-8")
    with pytest.raises(ValueError, match="non-BaoStock route"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )

    wrong_table_route = _external_evidence_document(baseline)
    wrong_table_samples = wrong_table_route["samples"]
    assert isinstance(wrong_table_samples, list)
    wrong_table_samples[0]["external_source"] = "eastmoney-stock-value-em"
    evidence_path.write_text(json.dumps(wrong_table_route), encoding="utf-8")
    with pytest.raises(ValueError, match="route for daily_bars"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )

    inflated_tolerance = _external_evidence_document(baseline)
    inflated_samples = inflated_tolerance["samples"]
    assert isinstance(inflated_samples, list)
    first_checks = inflated_samples[0]["checked_values"]
    assert isinstance(first_checks, dict)
    first_checks["close"]["external_value"] += 1_000_000.0
    first_checks["close"]["tolerance"] = 1_000_001.0
    evidence_path.write_text(json.dumps(inflated_tolerance), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed policy"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )

    nonhuman_role = _external_evidence_document(baseline)
    nonhuman_role["reviewer_role"] = "automated trial"
    evidence_path.write_text(json.dumps(nonhuman_role), encoding="utf-8")
    with pytest.raises(ValueError, match="human reviewer"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )

    valid = _external_evidence_document(baseline)
    duplicate_json = json.dumps(valid)
    duplicate_json = duplicate_json.replace(
        '"approved": true,',
        '"approved": false, "approved": true,',
        1,
    )
    evidence_path.write_text(duplicate_json, encoding="utf-8")
    with pytest.raises(ValueError, match="strict JSON"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )


def test_external_pairing_rejects_wrong_seed_and_inexact_sample_coverage(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    baseline = build_data_health_report(
        database_path,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )
    wrong_seed = _external_evidence_document(baseline)
    wrong_seed["seed"] = int(wrong_seed["seed"]) + 1
    evidence_path = tmp_path / "wrong-seed.json"
    evidence_path.write_text(json.dumps(wrong_seed), encoding="utf-8")
    with pytest.raises(ValueError, match="seed does not match"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )

    incomplete = _external_evidence_document(baseline)
    samples = incomplete["samples"]
    assert isinstance(samples, list)
    samples.pop()
    evidence_path.write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(ValueError, match="do not exactly cover"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )

    wrong_local = _external_evidence_document(baseline)
    wrong_local_samples = wrong_local["samples"]
    assert isinstance(wrong_local_samples, list)
    first_checks = wrong_local_samples[0]["checked_values"]
    assert isinstance(first_checks, dict)
    first_checks["close"]["local_value"] += 1.0
    evidence_path.write_text(json.dumps(wrong_local), encoding="utf-8")
    with pytest.raises(ValueError, match="local_value does not match"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )


def test_external_pairing_rejects_replay_after_sample_value_changes(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    baseline = build_data_health_report(
        database_path,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )
    evidence_path = tmp_path / "signed-before-change.json"
    evidence_path.write_text(
        json.dumps(_external_evidence_document(baseline)),
        encoding="utf-8",
    )
    sampled = baseline["pit_samples"]["financial_indicators"][0]
    payload = json.loads(str(sampled["payload"]))
    payload["post_signoff_revision"] = "changed"
    writable = sqlite3.connect(database_path)
    try:
        writable.execute(
            """
            UPDATE financial_indicators
            SET payload = ?
            WHERE symbol = ?
              AND report_period = ?
              AND metric = ?
            """,
            (
                json.dumps(payload, sort_keys=True),
                sampled["symbol"],
                sampled["report_period"],
                sampled["metric"],
            ),
        )
        writable.commit()
    finally:
        writable.close()

    with pytest.raises(ValueError, match="manifest SHA-256 does not match"):
        build_data_health_report(
            database_path,
            external_pit_pairing_evidence=evidence_path,
            sample_size=1,
            factor_probe=_sufficient_probe,
        )


def test_financial_depth_uses_all_listed_and_adapts_missing_listed_date(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    _replace_with_full_financial_history(database_path)
    engine = create_engine(f"sqlite:///{database_path}")
    new_symbol = "300999"
    latest_day = date(2026, 7, 24)
    with Session(engine) as session:
        session.add(
            Security(
                symbol=new_symbol,
                market="CN",
                list_status="listed",
                listed_date=None,
                name="recent listing",
            )
        )
        session.add(
            DailyBar(
                symbol=new_symbol,
                trade_date=latest_day,
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.5,
                volume=1_000.0,
                amount=10_000.0,
                source="baostock",
            )
        )
        session.add(
            AdjFactor(
                symbol=new_symbol,
                trade_date=latest_day,
                adj_factor=1.0,
                source="baostock-hfq",
            )
        )
        session.add(
            ValuationDaily(
                symbol=new_symbol,
                trade_date=latest_day,
                pe_ttm=20.0,
                pb_mrq=2.0,
                ps_ttm=3.0,
                source="em",
                available_time=_utc_at_shanghai(latest_day, 15),
            )
        )
        session.commit()
    engine.dispose()

    report = build_data_health_report(
        database_path,
        as_of_date=date(2026, 7, 25),
        minimum_market_coverage=0.60,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    depth = report["input_coverage"]["financial_indicators"]["depth_contract"]
    assert depth["universe_symbols"] == 3
    assert depth["listing_date_basis_counts"] == {
        "security_master": 2,
        "first_audited_bar": 1,
        "unknown": 0,
    }
    assert depth["new_symbols_without_publishable_quarter"] == 1
    assert all(
        metric["symbols_evaluated"] == 2
        for metric in depth["metric_depth"].values()
    )
    blocker_codes = {item["code"] for item in report["gate"]["blockers"]}
    assert "S2_FINANCIAL_COVERAGE_INCOMPLETE" not in blocker_codes
    assert not any(
        code.startswith("FINANCIAL_")
        and code.endswith(("_DEPTH", "_CROSS_YEAR", "_FRESHNESS"))
        for code in blocker_codes
    )
    markdown = render_data_health_markdown(report)
    assert "first_audited_bar=1" in markdown
    assert markdown.index("- 深度审计全集") < markdown.index("| 指标 | ≥20季度")


def test_readonly_connection_rejects_writes(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    with readonly_connection(database_path) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(
                "INSERT INTO securities(symbol, market) VALUES ('999999', 'CN')"
            )


def test_daily_adj_key_audit_ignores_external_benchmark_but_not_listed_stock(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    writable = sqlite3.connect(database_path)
    try:
        writable.execute(
            "DELETE FROM adj_factors WHERE symbol = 'SH.000001'"
        )
        writable.commit()
    finally:
        writable.close()

    benchmark_only = build_data_health_report(
        database_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    assert benchmark_only["daily_adj_key_audit"] == {
        "audited_daily_keys_without_adj": 0,
        "adj_keys_without_audited_daily": 0,
    }
    assert "DAILY_ADJ_KEY_MISMATCH" not in {
        item["code"] for item in benchmark_only["gate"]["blockers"]
    }

    writable = sqlite3.connect(database_path)
    try:
        writable.execute(
            """
            DELETE FROM adj_factors
            WHERE symbol = '600519'
              AND trade_date = (SELECT MAX(trade_date) FROM daily_bars)
            """
        )
        writable.commit()
    finally:
        writable.close()

    listed_gap = build_data_health_report(
        database_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    assert listed_gap["daily_adj_key_audit"] == {
        "audited_daily_keys_without_adj": 1,
        "adj_keys_without_audited_daily": 0,
    }
    assert "DAILY_ADJ_KEY_MISMATCH" in {
        item["code"] for item in listed_gap["gate"]["blockers"]
    }


def test_s6_explains_zero_period_financial_factors_as_s2_gap(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    report = build_data_health_report(
        database_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_missing_financial_probe,
    )
    factors = {
        item["factor"]: item for item in report["factor_availability"]["factors"]
    }

    assert factors["roe"]["status"] == "unavailable"
    assert factors["roe"]["cause_class"] == "input_data_gap"
    assert "S2 财务股票覆盖率" in factors["roe"]["reason"]
    assert "FACTOR_ROE_UNAVAILABLE" in {
        item["code"] for item in report["gate"]["blockers"]
    }


def test_s6_reports_source_pit_and_key_violations(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    writable = sqlite3.connect(database_path)
    try:
        writable.execute(
            "UPDATE daily_bars SET source = 'unknown' WHERE id = (SELECT MIN(id) FROM daily_bars)"
        )
        writable.execute(
            """
            UPDATE valuation_daily
            SET available_time = '2026-07-20 06:59:00'
            WHERE id = (SELECT MIN(id) FROM valuation_daily)
            """
        )
        writable.execute(
            "DELETE FROM adj_factors WHERE id = (SELECT MAX(id) FROM adj_factors)"
        )
        writable.commit()
    finally:
        writable.close()

    report = build_data_health_report(
        database_path,
        minimum_market_coverage=0.50,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    blocker_codes = {item["code"] for item in report["gate"]["blockers"]}
    assert "DAILY_BARS_SOURCE" in blocker_codes
    assert "DAILY_ADJ_KEY_MISMATCH" in blocker_codes
    assert "VALUATION_PIT_ANOMALY" in blocker_codes
    assert report["input_coverage"]["daily_bars"]["invalid_source_rows"] == 1
    assert report["input_coverage"]["valuation_daily"]["pit_anomaly_rows"] == 1


def test_markdown_keeps_gate_and_factor_states_explicit(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    report = build_data_health_report(
        database_path,
        minimum_market_coverage=0.80,
        minimum_factor_cross_section=2,
        minimum_sector_plates=1,
        minimum_sector_dates=5,
        sample_size=1,
        factor_probe=_sufficient_probe,
    )

    markdown = render_data_health_markdown(report)

    assert "# P3.3-S6 回填后数据体检" in markdown
    assert "闸门：**BLOCKED**" in markdown
    assert "S2_FINANCIAL_COVERAGE_INCOMPLETE" in markdown
    assert "| sector_strength | live_only | live_only | min=—" in markdown
    assert "自动检查通过：" in markdown
    assert "外部 PIT 对拍签认" in markdown
    assert "query_only=true" in markdown
    assert "JobSpec(" not in inspect.getsource(data_health)
    assert "alphapilot.jobs.registry" not in inspect.getsource(data_health)
    assert "GROUP BY" not in inspect.getsource(data_health._latest_cross_section)
    assert "CREATE INDEX" not in inspect.getsource(data_health)


def test_cli_writes_both_formats_and_exits_nonzero_when_blocked(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    json_path = tmp_path / "health.json"
    markdown_path = tmp_path / "health.md"
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(repository / "scripts" / "run_p3_m3_data_health.py"),
            "--db",
            str(database_path),
            "--minimum-market-coverage",
            "0.8",
            "--minimum-factor-cross-section",
            "2",
            "--minimum-sector-plates",
            "1",
            "--minimum-sector-dates",
            "5",
            "--sample-size",
            "1",
            "--json-out",
            str(json_path),
            "--markdown-out",
            str(markdown_path),
        ],
        cwd=repository,
        env={**os.environ, "PYTHONPATH": str(repository / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    stdout = json.loads(result.stdout)
    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    assert stdout["gate"]["status"] == "blocked"
    assert persisted["gate"]["status"] == "blocked"
    assert stdout["factor_availability"]["probe_results"]
    assert all(
        probe["error"] is None
        for probe in stdout["factor_availability"]["probe_results"]
    )
    assert "S2_FINANCIAL_COVERAGE_INCOMPLETE" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_cli_rejects_database_output_inode_and_duplicate_outputs(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    base = [
        sys.executable,
        str(repository / "scripts" / "run_p3_m3_data_health.py"),
        "--db",
        str(database_path),
    ]
    environment = {**os.environ, "PYTHONPATH": str(repository / "src")}
    before = _sha256(database_path)

    database_collision = subprocess.run(
        [*base, "--json-out", str(database_path)],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert database_collision.returncode == 2
    assert "must not resolve to the SQLite database" in database_collision.stderr
    assert _sha256(database_path) == before

    hardlink_output = tmp_path / "hardlink.json"
    os.link(database_path, hardlink_output)
    inode_collision = subprocess.run(
        [*base, "--json-out", str(hardlink_output)],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert inode_collision.returncode == 2
    assert "must not resolve to the SQLite database" in inode_collision.stderr
    assert _sha256(database_path) == before

    shared_output = tmp_path / "same-output"
    output_collision = subprocess.run(
        [
            *base,
            "--json-out",
            str(shared_output),
            "--markdown-out",
            str(shared_output),
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert output_collision.returncode == 2
    assert "must be different files" in output_collision.stderr
    assert not shared_output.exists()
    assert _sha256(database_path) == before
