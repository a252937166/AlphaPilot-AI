from __future__ import annotations

import hashlib
import inspect
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import yaml
from sqlalchemy import delete, select

from alphapilot.db.engine import get_session
from alphapilot.db.models import BrokerOrder, JobRun, NewsItem, TradeProposalRecord
from alphapilot.jobs import news_poll
from alphapilot.jobs.registry import JOBS, JobExecutionError, JobSpec, register, run_job
from test_p4_news_poll_v2_2 import (
    _call_data,
    _candidate,
    _FakeClient,
    _noncritical_batch,
    _page,
    _probe,
    _row,
    _seed_checkpoint,
    _seed_v2_1,
    _seed_v2_2_terminal_source,
    _successful_batch,
    _terminal_public,
)

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2_3.yaml"
V2_2_CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2_2.yaml"
CORE_CONFIG_PATH = PROJECT_DIR / "src/alphapilot/core/config.py"
PARTITIONS = ["szmb", "szcy", "shmb", "shkcp", "bj"]
ROW_CEILING = 3000
GAP_CODE = "cninfo_partition_capacity_gap"
GAP_DIAGNOSTIC = {
    "code": GAP_CODE,
    "source": "cninfo",
    "constraint": "partition_row_ceiling",
    "recoverable": False,
    "retry_suppressed": False,
}


@pytest.fixture(autouse=True)
def _clean_v2_3_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(news_poll, "sleep", lambda _seconds: None)
    with get_session() as session:
        session.execute(delete(NewsItem))
        session.execute(delete(JobRun).where(JobRun.job_name == "news_poll"))
    JOBS.pop("news_poll", None)
    yield
    JOBS.pop("news_poll", None)
    with get_session() as session:
        session.execute(delete(NewsItem))
        session.execute(delete(JobRun).where(JobRun.job_name == "news_poll"))


def _config() -> news_poll.NewsPollConfig:
    loaded = news_poll.load_news_poll_config(CONFIG_PATH)
    return news_poll.NewsPollConfig(
        path=loaded.path,
        sha256=loaded.sha256,
        document=deepcopy(loaded.document),
    )


def _fetch(
    client: _FakeClient,
    *,
    config: news_poll.NewsPollConfig | None = None,
    now: datetime = datetime(2026, 8, 23, 1, tzinfo=UTC),
) -> news_poll.SourceBatch:
    return news_poll._fetch_cninfo_v2_3(config or _config(), now, lambda _source_id: client)


def _seed_v2_2_predecessor(day: int = 20) -> None:
    """A v2.1 seed plus one complete v2.2 terminal row is the exact v2.3 predecessor."""

    _seed_v2_1(date(2026, 8, 19))
    _seed_v2_2_terminal_source(
        _successful_batch(
            [
                _candidate(
                    identifier=day,
                    url=f"https://static.cninfo.com.cn/predecessor-{day}.PDF",
                    market_date=date(2026, 8, day),
                )
            ],
            slice_date=date(2026, 8, day),
        )
    )


def _terminal_job_stats(source: dict[str, object]) -> dict[str, object]:
    slices = cast(list[dict[str, object]], source["slices"])
    checkpoint = cast(dict[str, object], source["daily_checkpoint"])
    poll_started = cast(str, source["poll_started_at_utc"])
    commit_completed = datetime.fromisoformat(cast(str, source["db_commit_completed_at"]))
    slice_date = cast(str, slices[0]["date_shanghai"])
    has_gap = any(item.get("capacity_gap") is True for item in slices)
    return {
        "config_version": "p4.1-news-poll-v2.3",
        "config_path": "config/p4_news_poll_v2_3.yaml",
        "config_sha256": news_poll.EXPECTED_V2_3_CONFIG_SHA256,
        "poll_started_at": poll_started,
        "poll_completed_at": (commit_completed + timedelta(milliseconds=1)).isoformat(),
        "execution_mode": "scheduler",
        "run_mode": "coverage_gap_catchup",
        "coverage_gap": True,
        "coverage_gap_details": {
            "reason": "cninfo_capacity_checkpoint_lag",
            "timezone": "Asia/Shanghai",
            "checkpoint_lineage": checkpoint["lineage_before"],
            "checkpoint_date_shanghai": checkpoint["verified_checkpoint_date_shanghai_before"],
            "target_closed_date_shanghai": slice_date,
            "recovery_poll_started_at_utc": poll_started,
            "recovery_poll_started_at_shanghai": datetime.fromisoformat(poll_started)
            .astimezone(news_poll.MARKET_TIMEZONE)
            .isoformat(),
        },
        "safety_unchanged": True,
        "terminal_diagnostics": dict(GAP_DIAGNOSTIC) if has_gap else None,
        "p4_2_unlocked": False,
        "sources": {"cninfo": source},
    }


def _terminal_complete(source: dict[str, object]) -> bool:
    return news_poll._v2_3_cninfo_slices_complete(source, job_stats=_terminal_job_stats(source))


def _seed_v2_3_terminal_source(batch: news_poll.SourceBatch) -> dict[str, object]:
    source = _terminal_public(batch, ["inserted" for _candidate in batch.candidates])
    stats = _terminal_job_stats(source)
    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status="degraded" if stats["terminal_diagnostics"] is not None else "ok",
                stats=stats,
            )
        )
    return source


def _capped_pages(
    target: date,
    *,
    total: int,
    page_cap: int = 100,
    identifier_base: int = 0,
) -> list[object]:
    return [
        _page(
            total,
            [
                _row(
                    identifier_base + page * 30 + offset,
                    target,
                    seconds_after_open=-(page * 30 + offset),
                )
                for offset in range(30)
            ],
            has_more=True,
        )
        for page in range(page_cap)
    ]


def test_v2_3_config_is_frozen_and_binds_v2_2_as_read_only_predecessor(tmp_path: Path) -> None:
    raw = CONFIG_PATH.read_bytes()
    document = yaml.safe_load(raw)
    cninfo = cast(dict[str, object], document["sources"]["cninfo"])

    assert news_poll.DEFAULT_CONFIG_PATH == CONFIG_PATH
    assert news_poll.V2_3_CONFIG_PATH == CONFIG_PATH
    assert hashlib.sha256(raw).hexdigest() == news_poll.EXPECTED_V2_3_CONFIG_SHA256
    assert hashlib.sha256(V2_2_CONFIG_PATH.read_bytes()).hexdigest() == (
        news_poll.EXPECTED_V2_2_CONFIG_SHA256
    )
    assert hashlib.sha256(CORE_CONFIG_PATH.read_bytes()).hexdigest() == (
        "6bdc24abbc943bb52ecc416fd72180e356fbcb3bc55eb6a96f98d5b0be8137cd"
    )
    assert document["schema_version"] == "p4.1-news-poll-v2.3"
    assert cninfo["partitions"] == PARTITIONS
    assert cninfo["min_interval_seconds"] == 0.5
    assert cninfo["max_pages_per_partition"] == 100
    assert cninfo["page_size"] == 30
    assert cninfo["max_dates_per_run"] == 1
    assert news_poll._v2_2_cninfo_request_budgets(cninfo) == (501, 1002)
    assert document["superseded_v2_2"]["config_sha256"] == news_poll.EXPECTED_V2_2_CONFIG_SHA256
    assert document["phase_gate"]["p4_2b_production_wiring_unlocked"] is False
    assert document["phase_gate"]["p4_3_unlocked"] is False
    assert news_poll.load_news_poll_config(CONFIG_PATH).sha256 == (
        news_poll.EXPECTED_V2_3_CONFIG_SHA256
    )

    tampered = tmp_path / "p4_news_poll_v2_3-tampered.yaml"
    tampered.write_bytes(
        raw.replace(b"    min_interval_seconds: 0.5\n", b"    min_interval_seconds: 0.1\n")
    )
    with pytest.raises(ValueError, match="pre-registered SHA-256"):
        news_poll.load_news_poll_config(tampered)


def test_v2_3_default_registration_is_scheduler_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = news_poll.load_news_poll_config()
    assert config.path == CONFIG_PATH
    assert news_poll._v2_execution_authorization(
        config,
        execution_mode="scheduler",
        authorization_receipt_path=None,
    ) == {"execution_mode": "scheduler", "scheduler_activated": True}

    receipt = tmp_path / "receipt-is-forbidden.json"
    receipt.write_text("{}\n", encoding="utf-8")
    for mode, path in (
        ("scheduler", receipt),
        ("initial_backlog_migration", None),
        ("standard_incremental_validation", None),
    ):
        with pytest.raises(JobExecutionError) as error:
            news_poll._v2_execution_authorization(
                config,
                execution_mode=cast(news_poll.V2ExecutionMode, mode),
                authorization_receipt_path=path,
            )
        assert error.value.stats["implementation_gate"] == "v2_3_scheduler_only"
        assert error.value.stats["network_attempted"] is False

    monkeypatch.delenv(news_poll.NEWS_POLL_ENABLED_ENV, raising=False)
    news_poll.register_news_poll_job()
    spec = JOBS["news_poll"]
    assert spec.trigger is None
    assert inspect.signature(spec.func).parameters["config_path"].default == CONFIG_PATH
    v2_3 = news_poll._news_poll_trigger(CONFIG_PATH)
    v2_2 = news_poll._news_poll_trigger(V2_2_CONFIG_PATH)
    probe = datetime(2026, 8, 9, 16, tzinfo=UTC)
    assert v2_3.get_next_fire_time(None, probe) == v2_2.get_next_fire_time(None, probe)


def test_v2_3_checkpoint_uses_latest_complete_v2_2_row_as_exact_predecessor() -> None:
    _seed_v2_2_predecessor(20)
    _seed_checkpoint(
        version="p4.1-news-poll-v2.3",
        config_sha256="0" * 64,
        checkpoint_date=date(2026, 8, 22),
        observed=datetime(2026, 8, 22, 10, tzinfo=UTC),
    )
    config = _config()

    seed = news_poll._last_committed_daily_checkpoint(config, "cninfo")

    assert seed.lineage == "v2.2_daily_checkpoint_predecessor"
    assert seed.checkpoint_date_shanghai == date(2026, 8, 20)
    assert news_poll._v2_1_slice_dates(
        seed,
        poll_started_at_utc=datetime(2026, 8, 23, 1, tzinfo=UTC),
        max_dates_per_run=1,
    ) == [date(2026, 8, 21)]


def test_v2_3_without_v2_2_predecessor_is_missing_and_never_requests() -> None:
    _seed_v2_1(date(2026, 8, 19))
    client = _FakeClient([_probe(0), *(_page(0, []) for _partition in PARTITIONS)])
    assert news_poll._last_committed_daily_checkpoint(_config(), "cninfo").lineage == "missing"
    with pytest.raises(news_poll.NewsSourceError) as error:
        _fetch(client)
    assert error.value.code == "daily_checkpoint_unavailable"
    assert error.value.blocked is True
    assert client.calls == []


def test_v2_3_reconciles_aggregate_over_five_subplates_including_null_bj() -> None:
    _seed_v2_2_predecessor(20)
    target = date(2026, 8, 21)
    client = _FakeClient(
        [
            _probe(4),
            _page(2, [_row(2, target), _row(1, target)]),
            _page(1, [_row(3, target)]),
            _page(1, [_row(4, target)]),
            _page(0, []),
            _page(0, None),
        ]
    )

    batch = _fetch(client)
    public = _terminal_public(batch, ["inserted"] * 4)
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])
    request_data = [_call_data(call) for call in client.calls]

    assert batch.status == "ok"
    assert batch.failures == []
    assert len(batch.candidates) == 4
    assert request_data[0].get("plate") is None
    assert [data["plate"] for data in request_data[1:]] == PARTITIONS
    assert all(data["column"] == "szse" for data in request_data)
    assert slice_stats["aggregate_upstream_total"] == 4
    assert slice_stats["partition_rows_seen"] == {
        "szmb": 2,
        "szcy": 1,
        "shmb": 1,
        "shkcp": 0,
        "bj": 0,
    }
    assert slice_stats["partition_completion"] == dict.fromkeys(PARTITIONS, True)
    assert slice_stats["capacity_gap"] is False
    assert slice_stats["capacity_gap_partitions"] == []
    assert slice_stats["capacity_gap_rows"] == 0
    assert slice_stats["partition_capacity_shortfall"] == {}
    assert slice_stats["partition_row_ceiling"] == ROW_CEILING
    assert slice_stats["pagination_complete"] is True
    assert slice_stats["coverage_proven"] is True
    assert slice_stats["checkpoint_committed"] is True
    assert slice_stats["checkpoint_advanced"] is True
    assert checkpoint["lineage_before"] == "v2.2_daily_checkpoint_predecessor"
    assert checkpoint["v2_2_predecessor_used"] is True
    assert checkpoint["capacity_gap_dates_shanghai"] == []
    assert checkpoint["verified_checkpoint_date_shanghai_before"] == "2026-08-20"
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-21"
    assert {
        candidate.raw_payload["_alphapilot_cninfo_partition"] for candidate in batch.candidates
    } == {"szmb", "szcy", "shmb"}
    assert cast(dict[str, object], batch.details["request_budget"])["min_interval_seconds"] == 0.5
    assert cast(dict[str, object], batch.details["request_budget"])["partition_row_ceiling"] == (
        ROW_CEILING
    )
    assert _terminal_complete(public)


def test_v2_3_capacity_gap_closes_the_date_with_disclosure_and_never_requests_page_101() -> None:
    _seed_v2_2_predecessor(20)
    target = date(2026, 8, 21)
    total = ROW_CEILING + 5
    client = _FakeClient(
        [
            _probe(total + 1),
            *_capped_pages(target, total=total),
            _page(1, [_row(900_000, target)]),
            _page(0, []),
            _page(0, []),
            _page(0, None),
        ]
    )

    batch = _fetch(client)
    public = _terminal_public(batch, ["inserted"] * (ROW_CEILING + 1))
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])
    partition_calls = [_call_data(call) for call in client.calls[1:]]
    szmb_pages = [
        cast(int, data["pageNum"]) for data in partition_calls if data.get("plate") == "szmb"
    ]

    assert batch.status == "degraded"
    assert len(batch.candidates) == ROW_CEILING + 1
    assert batch.failures == [
        {
            "code": GAP_CODE,
            "blocked": False,
            "error_type": "NewsSourceError",
            "date_shanghai": "2026-08-21",
            "partitions": ["szmb"],
            "rows_missing": 5,
        }
    ]
    assert szmb_pages == list(range(1, 101))
    assert 101 not in szmb_pages
    assert slice_stats["page_cap_hit"] is True
    assert slice_stats["page_cap_hit_partitions"] == ["szmb"]
    assert slice_stats["capacity_gap"] is True
    assert slice_stats["capacity_gap_partitions"] == ["szmb"]
    assert slice_stats["capacity_gap_rows"] == 5
    assert slice_stats["partition_capacity_shortfall"] == {"szmb": 5}
    assert slice_stats["partition_rows_seen"]["szmb"] == ROW_CEILING
    assert slice_stats["partition_upstream_totals"]["szmb"] == total
    assert slice_stats["partition_completion"] == {
        "szmb": False,
        "szcy": True,
        "shmb": True,
        "shkcp": True,
        "bj": True,
    }
    assert slice_stats["cross_partition_unique_rows"] == ROW_CEILING + 1
    assert slice_stats["pagination_complete"] is False
    assert slice_stats["coverage_proven"] is False
    assert slice_stats["checkpoint_committed"] is True
    assert slice_stats["checkpoint_advanced"] is True
    assert checkpoint["checkpoint_committed"] is True
    assert checkpoint["partial_checkpoint"] is False
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-21"
    assert checkpoint["capacity_gap_dates_shanghai"] == ["2026-08-21"]
    assert public["failures"] == batch.failures
    assert [news_poll._safe_v2_failure(failure) for failure in batch.failures] == [
        {
            "code": GAP_CODE,
            "blocked": False,
            "error_type": "NewsSourceError",
            "partitions": ["szmb"],
            "date_shanghai": "2026-08-21",
        }
    ]
    assert _terminal_complete(public)
    assert not news_poll._v2_3_cninfo_slices_complete(
        public,
        job_stats={**_terminal_job_stats(public), "terminal_diagnostics": None},
    )

    _seed_v2_3_terminal_source(batch)
    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")
    assert seed.lineage == "v2.3_daily_checkpoint"
    assert seed.checkpoint_date_shanghai == date(2026, 8, 21)


def test_v2_3_gap_reconciliation_still_fails_closed_on_aggregate_mismatch() -> None:
    _seed_v2_2_predecessor(20)
    target = date(2026, 8, 21)
    total = ROW_CEILING + 5
    client = _FakeClient(
        [
            _probe(total + 7),
            *_capped_pages(target, total=total),
            _page(1, [_row(900_000, target)]),
            _page(0, []),
            _page(0, []),
            _page(0, None),
        ]
    )

    batch = _fetch(client)
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])

    assert batch.status == "unavailable"
    assert [failure["code"] for failure in batch.failures] == ["aggregate_count_mismatch"]
    assert slice_stats["capacity_gap"] is False
    assert slice_stats["checkpoint_committed"] is False
    assert slice_stats["checkpoint_advanced"] is False
    assert checkpoint["checkpoint_committed"] is False
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-20"


def test_v2_3_capped_partition_below_ceiling_is_a_count_mismatch_not_a_gap() -> None:
    _seed_v2_2_predecessor(20)
    target = date(2026, 8, 21)
    client = _FakeClient(
        [
            _probe(ROW_CEILING - 1),
            *_capped_pages(target, total=ROW_CEILING - 1),
        ]
    )

    batch = _fetch(client)
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]

    assert batch.status == "unavailable"
    assert [failure["code"] for failure in batch.failures] == ["partition_count_mismatch"]
    assert batch.failures[0]["partition"] == "szmb"
    assert slice_stats["capacity_gap"] is False
    assert slice_stats["checkpoint_committed"] is False
    assert len(client.calls) == 101


def test_v2_3_run_job_commits_a_gap_date_then_chains_a_clean_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_v2_2_predecessor(20)
    gap_client = _FakeClient(
        [
            _probe(ROW_CEILING + 6),
            *_capped_pages(date(2026, 8, 21), total=ROW_CEILING + 5),
            _page(1, [_row(900_000, date(2026, 8, 21))]),
            _page(0, []),
            _page(0, []),
            _page(0, None),
        ]
    )
    clean_client = _FakeClient(
        [
            _probe(2),
            _page(1, [_row(910_000, date(2026, 8, 22))]),
            _page(1, [_row(910_001, date(2026, 8, 22))]),
            _page(0, []),
            _page(0, []),
            _page(0, None),
        ]
    )
    clients = [gap_client, clean_client]
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda config, now, _factory: news_poll._fetch_cninfo_v2_3(
            config, now, lambda _source_id: clients.pop(0)
        ),
    )
    monkeypatch.setattr(news_poll, "_fetch_ths", lambda *_args: _noncritical_batch("akshare_ths"))
    monkeypatch.setattr(
        news_poll, "_fetch_sina", lambda *_args: _noncritical_batch("sina_company_news")
    )
    register(JobSpec(name="news_poll", func=news_poll.run_news_poll, trigger=None))
    with get_session() as session:
        proposals_before = list(session.scalars(select(TradeProposalRecord.proposal_id)).all())
        orders_before = list(session.scalars(select(BrokerOrder.id)).all())

    first = run_job(
        "news_poll",
        config_path=CONFIG_PATH,
        now=datetime(2026, 8, 23, 1, tzinfo=UTC),
        execution_mode="scheduler",
    )

    assert first.status == "degraded"
    assert first.error is None
    assert first.stats["config_version"] == "p4.1-news-poll-v2.3"
    assert first.stats["config_sha256"] == news_poll.EXPECTED_V2_3_CONFIG_SHA256
    assert first.stats["terminal_diagnostics"] == GAP_DIAGNOSTIC
    assert first.stats["run_mode"] == "coverage_gap_catchup"
    assert first.stats["coverage_gap_details"]["reason"] == "cninfo_capacity_checkpoint_lag"
    cninfo = first.stats["sources"]["cninfo"]
    assert cninfo["status"] == "degraded"
    assert cninfo["inserted"] == ROW_CEILING + 1
    assert cninfo["daily_checkpoint"]["checkpoint_committed"] is True
    assert cninfo["daily_checkpoint"]["verified_checkpoint_date_shanghai_after"] == "2026-08-21"
    assert cninfo["daily_checkpoint"]["capacity_gap_dates_shanghai"] == ["2026-08-21"]
    assert cninfo["slices"][0]["capacity_gap_rows"] == 5
    assert "wall_clock_guard" in first.stats
    assert first.stats["safety_unchanged"] is True

    second = run_job(
        "news_poll",
        config_path=CONFIG_PATH,
        now=datetime(2026, 8, 23, 1, 30, tzinfo=UTC),
        execution_mode="scheduler",
    )

    assert second.status == "ok"
    assert second.stats["terminal_diagnostics"] is None
    second_cninfo = second.stats["sources"]["cninfo"]
    assert second_cninfo["status"] == "ok"
    assert second_cninfo["inserted"] == 2
    assert second_cninfo["daily_checkpoint"]["lineage_before"] == "v2.3_daily_checkpoint"
    assert second_cninfo["daily_checkpoint"]["verified_checkpoint_date_shanghai_before"] == (
        "2026-08-21"
    )
    assert second_cninfo["daily_checkpoint"]["verified_checkpoint_date_shanghai_after"] == (
        "2026-08-22"
    )
    assert second_cninfo["daily_checkpoint"]["v2_2_predecessor_used"] is False
    assert second_cninfo["daily_checkpoint"]["capacity_gap_dates_shanghai"] == []
    with get_session() as session:
        stored = session.scalars(select(NewsItem).order_by(NewsItem.id)).all()
        proposals_after = list(session.scalars(select(TradeProposalRecord.proposal_id)).all())
        orders_after = list(session.scalars(select(BrokerOrder.id)).all())
    assert len(stored) == ROW_CEILING + 3
    assert {item.raw_payload["_alphapilot_cninfo_partition"] for item in stored} == {
        "szmb",
        "szcy",
    }
    assert proposals_after == proposals_before
    assert orders_after == orders_before
