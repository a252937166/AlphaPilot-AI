from __future__ import annotations

import importlib.util
import json
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from alphapilot.backtest.financial_acceptance import PUBDATE_PLAN_SCHEMA_VERSION
from alphapilot.backtest.financial_pubdate_execution import execute_pubdate_audit


class _Result:
    def __init__(
        self,
        *,
        error_code: str = "0",
        error_msg: str = "",
        fields: Sequence[str] = (),
        rows: Sequence[Sequence[str]] = (),
    ) -> None:
        self.error_code = error_code
        self.error_msg = error_msg
        self.fields = list(fields)
        self._rows = iter(rows)
        self._current: list[str] = []

    def next(self) -> bool:
        try:
            self._current = list(next(self._rows))
        except StopIteration:
            return False
        return True

    def get_row_data(self) -> Sequence[str]:
        return self._current


def _samples() -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for index, symbol in enumerate(("000001", "300001", "600001", "601001", "688001")):
        samples.append(
            {
                "symbol": symbol,
                "report_period": "2025Q4",
                "year": 2025,
                "quarter": 4,
                "local_stat_date": "2025-12-31",
                "local_pub_date": f"2026-04-{20 + index:02d}",
                "local_available_time": f"2026-04-{20 + index:02d}T16:00:00+00:00",
                "local_source_field": "profit.roeAvg",
                "planned_query": "BaoStock query_profit_data",
            }
        )
    return samples


def _plan() -> dict[str, object]:
    return {
        "schema_version": PUBDATE_PLAN_SCHEMA_VERSION,
        "mode": "plan_only",
        "network_called": False,
        "ready_for_authorized_execution": True,
        "sample_size": 5,
        "planned_provider_queries": 5,
        "hard_provider_query_cap": 5,
        "blockers": [],
        "samples": _samples(),
    }


class _SuccessfulClient:
    login_calls = 0
    logout_calls = 0
    queries: ClassVar[list[dict[str, object]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.login_calls = 0
        cls.logout_calls = 0
        cls.queries = []

    @classmethod
    def login(cls) -> _Result:
        cls.login_calls += 1
        return _Result()

    @classmethod
    def query_profit_data(
        cls,
        *,
        code: str,
        year: int,
        quarter: int,
    ) -> _Result:
        cls.queries.append({"code": code, "year": year, "quarter": quarter})
        index = len(cls.queries) - 1
        return _Result(
            fields=("code", "statDate", "pubDate", "roeAvg"),
            rows=((code, "2025-12-31", f"2026-04-{20 + index:02d}", "12.3"),),
        )

    @classmethod
    def logout(cls) -> _Result:
        cls.logout_calls += 1
        return _Result()


def test_fixed_plan_executes_five_queries_and_matches_available_time() -> None:
    _SuccessfulClient.reset()

    report = execute_pubdate_audit(_plan(), client=_SuccessfulClient)

    assert report["network_called"] is True
    assert report["queries_attempted"] == 5
    assert len(report["samples"]) == 5
    assert report["gate"]["matched_count"] == 5
    assert report["gate"]["passed"] is True
    assert report["invariants"]["retry_count"] == 0
    assert report["invariants"]["resampling_allowed"] is False
    assert _SuccessfulClient.login_calls == 1
    assert _SuccessfulClient.logout_calls == 1
    assert [query["code"] for query in _SuccessfulClient.queries] == [
        "sz.000001",
        "sz.300001",
        "sh.600001",
        "sh.601001",
        "sh.688001",
    ]
    assert all(sample["matched"] for sample in report["samples"])


def test_blacklist_stops_immediately_without_retry_and_always_logs_out() -> None:
    class BlacklistedClient(_SuccessfulClient):
        @classmethod
        def query_profit_data(
            cls,
            *,
            code: str,
            year: int,
            quarter: int,
        ) -> _Result:
            cls.queries.append({"code": code, "year": year, "quarter": quarter})
            return _Result(error_code="10001011", error_msg="黑名单用户")

    BlacklistedClient.reset()

    report = execute_pubdate_audit(_plan(), client=BlacklistedClient)

    assert report["queries_attempted"] == 1
    assert report["samples"] == []
    assert report["stopped_early"] is True
    assert report["stop_reason"]["code"] == "provider_blacklisted"
    assert report["gate"]["passed"] is False
    assert BlacklistedClient.login_calls == 1
    assert BlacklistedClient.logout_calls == 1
    assert len(BlacklistedClient.queries) == 1


def test_login_blacklist_never_queries_and_always_logs_out() -> None:
    class LoginBlacklistedClient(_SuccessfulClient):
        @classmethod
        def login(cls) -> _Result:
            cls.login_calls += 1
            return _Result(error_code="10001011", error_msg="黑名单用户")

    LoginBlacklistedClient.reset()

    report = execute_pubdate_audit(_plan(), client=LoginBlacklistedClient)

    assert report["queries_attempted"] == 0
    assert report["stop_reason"]["code"] == "provider_blacklisted"
    assert report["gate"]["passed"] is False
    assert LoginBlacklistedClient.login_calls == 1
    assert LoginBlacklistedClient.logout_calls == 1
    assert LoginBlacklistedClient.queries == []


def test_query_exception_stops_without_retry_and_always_logs_out() -> None:
    class ExplodingClient(_SuccessfulClient):
        @classmethod
        def query_profit_data(
            cls,
            *,
            code: str,
            year: int,
            quarter: int,
        ) -> _Result:
            cls.queries.append({"code": code, "year": year, "quarter": quarter})
            raise TimeoutError("must not leak arbitrary exception messages")

    ExplodingClient.reset()

    report = execute_pubdate_audit(_plan(), client=ExplodingClient)

    assert report["queries_attempted"] == 1
    assert report["stop_reason"] == {
        "code": "query_exception",
        "detail": "client raised TimeoutError",
        "sample_index": 0,
    }
    assert "must not leak" not in json.dumps(report)
    assert ExplodingClient.logout_calls == 1


def test_mismatch_keeps_fixed_samples_and_never_resamples() -> None:
    class MismatchClient(_SuccessfulClient):
        @classmethod
        def query_profit_data(
            cls,
            *,
            code: str,
            year: int,
            quarter: int,
        ) -> _Result:
            cls.queries.append({"code": code, "year": year, "quarter": quarter})
            index = len(cls.queries) - 1
            pub_date = "2026-05-01" if index == 2 else f"2026-04-{20 + index:02d}"
            return _Result(
                fields=("code", "statDate", "pubDate"),
                rows=((code, "2025-12-31", pub_date),),
            )

    MismatchClient.reset()

    report = execute_pubdate_audit(_plan(), client=MismatchClient)

    assert report["queries_attempted"] == 5
    assert len(MismatchClient.queries) == 5
    assert len(report["samples"]) == 5
    assert report["gate"]["matched_count"] == 4
    assert report["gate"]["passed"] is False
    assert report["gate"]["blockers"] == [
        {
            "code": "pubdate_mismatch",
            "detail": "mismatched fixed sample indexes=[2]",
        }
    ]


def test_invalid_plan_never_logs_in() -> None:
    _SuccessfulClient.reset()
    plan = _plan()
    plan["samples"] = _samples()[:4]

    report = execute_pubdate_audit(plan, client=_SuccessfulClient)

    assert report["network_called"] is False
    assert report["queries_attempted"] == 0
    assert report["stop_reason"]["code"] == "invalid_fixed_plan"
    assert _SuccessfulClient.login_calls == 0
    assert _SuccessfulClient.logout_calls == 0


def test_cli_accepts_complete_preflight_report_and_fake_client(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_path = (
        Path(__file__).resolve().parent.parent / "scripts/run_p3_m3_s2_pubdate_audit.py"
    )
    spec = importlib.util.spec_from_file_location("run_p3_m3_s2_pubdate_audit", script_path)
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    _SuccessfulClient.reset()
    plan_path = tmp_path / "preflight.json"
    plan_path.write_text(
        json.dumps({"pubdate_plan": _plan()}),
        encoding="utf-8",
    )

    exit_code = script.main(["--plan", str(plan_path)], client=_SuccessfulClient)

    assert exit_code == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["gate"]["passed"] is True
    assert report["plan"]["location"] == "acceptance_report.pubdate_plan"
