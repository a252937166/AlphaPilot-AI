from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Iterator, Sequence
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
import yaml
from sqlalchemy import delete, select

from alphapilot.db.engine import get_session
from alphapilot.db.models import BrokerOrder, JobRun, NewsItem, TradeProposalRecord
from alphapilot.jobs import news_poll
from alphapilot.jobs.registry import (
    JOBS,
    JobExecutionError,
    JobSpec,
    register,
    run_job,
)

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2_2.yaml"
V2_1_CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2_1.yaml"
CORE_CONFIG_PATH = PROJECT_DIR / "src/alphapilot/core/config.py"
PARTITIONS = ["sz", "sh", "bj"]


class _FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload

    def read(self) -> None:
        return None


class _FakeClient:
    def __init__(
        self,
        outcomes: Sequence[_FakeResponse | Exception],
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        if not self.outcomes:
            raise AssertionError("unexpected extra HTTP request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_news_poll_rows(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def _config(path: Path = CONFIG_PATH) -> news_poll.NewsPollConfig:
    loaded = news_poll.load_news_poll_config(path)
    document = deepcopy(loaded.document)
    source = cast(dict[str, object], document["sources"])["cninfo"]
    assert isinstance(source, dict)
    return news_poll.NewsPollConfig(
        path=loaded.path,
        sha256=loaded.sha256,
        document=document,
    )


def _call_data(call: tuple[str, str, dict[str, object]]) -> dict[str, object]:
    data = call[2].get("data")
    assert isinstance(data, dict)
    return cast(dict[str, object], data)


def _probe(total: object) -> _FakeResponse:
    return _FakeResponse({"totalAnnouncement": total})


def _page(
    total: object,
    rows: object,
    *,
    has_more: object = False,
) -> _FakeResponse:
    return _FakeResponse(
        {
            "totalAnnouncement": total,
            "announcements": rows,
            "hasMore": has_more,
        }
    )


def _row(
    identifier: int,
    market_date: date,
    *,
    seconds_after_open: int = 0,
) -> dict[str, object]:
    published = datetime(
        market_date.year,
        market_date.month,
        market_date.day,
        2,
        tzinfo=UTC,
    ) + timedelta(seconds=seconds_after_open)
    return {
        "secCode": f"{identifier % 1_000_000:06d}",
        "announcementId": f"v2-2-{identifier}",
        "announcementTitle": f"分区容量公告{identifier}",
        "adjunctUrl": f"finalpage/v2-2-{identifier}.PDF",
        "announcementTime": int(published.timestamp() * 1000),
    }


def _seed_checkpoint(
    *,
    version: str,
    config_sha256: str,
    checkpoint_date: date,
    observed: datetime,
    status: str = "ok",
    committed: bool = True,
) -> None:
    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status=status,
                stats={
                    "config_version": version,
                    "config_sha256": config_sha256,
                    "sources": {
                        "cninfo": {
                            "daily_checkpoint": {
                                "checkpoint_committed": committed,
                                "verified_checkpoint_date_shanghai_after": (
                                    checkpoint_date.isoformat()
                                ),
                                "newest_observed_at_utc": observed.isoformat(),
                            }
                        }
                    },
                },
            )
        )


def _seed_v2_1(checkpoint_date: date) -> None:
    observed = datetime(
        checkpoint_date.year,
        checkpoint_date.month,
        checkpoint_date.day,
        10,
        tzinfo=UTC,
    )
    _seed_checkpoint(
        version="p4.1-news-poll-v2.1",
        config_sha256=news_poll.EXPECTED_V2_1_CONFIG_SHA256,
        checkpoint_date=checkpoint_date,
        observed=observed,
    )


def _empty_partition_outcomes() -> list[_FakeResponse]:
    return [
        _probe(0),
        *(_page(0, []) for _partition in PARTITIONS),
    ]


def _fetch(
    client: _FakeClient,
    *,
    config: news_poll.NewsPollConfig | None = None,
    now: datetime = datetime(2026, 8, 23, 1, tzinfo=UTC),
) -> news_poll.SourceBatch:
    return news_poll._fetch_cninfo_v2_2(
        config or _config(),
        now,
        lambda _source_id: client,
    )


def _candidate(
    *,
    identifier: int,
    url: str,
    market_date: date = date(2026, 8, 20),
) -> news_poll.NewsCandidate:
    return news_poll.NewsCandidate(
        source="cninfo",
        symbol="000001",
        title="容量追补公告",
        url=url,
        published_at=datetime(
            market_date.year,
            market_date.month,
            market_date.day,
            2,
            tzinfo=UTC,
        ),
        content="",
        raw_payload={
            "announcementId": f"run-job-{identifier}",
            "_alphapilot_cninfo_partition": "sz",
        },
    )


def _successful_batch(
    candidates: list[news_poll.NewsCandidate],
    *,
    slice_date: date,
    page_cap: int = 100,
    date_closed: bool = True,
    observed_hour_utc: int = 10,
    observed_minute_utc: int = 0,
    poll_started_at: datetime = datetime(2026, 8, 23, 1, tzinfo=UTC),
) -> news_poll.SourceBatch:
    count = len(candidates)
    checkpoint_before = slice_date - timedelta(days=1)
    observed = datetime(
        slice_date.year,
        slice_date.month,
        slice_date.day,
        observed_hour_utc,
        observed_minute_utc,
        tzinfo=UTC,
    )
    return news_poll.SourceBatch(
        source_id="cninfo",
        status="ok",
        candidates=candidates,
        request_count=4,
        logical_request_count=4,
        physical_attempt_count=4,
        details={
            "canonical_column": "szse",
            "partition_parameter": "plate",
            "partitions": PARTITIONS,
            "slice_dates_shanghai": [slice_date.isoformat()],
            "slices": [
                {
                    "date_shanghai": slice_date.isoformat(),
                    "date_closed": date_closed,
                    "mode": (
                        "closed_date_reconciliation" if date_closed else "current_date_incremental"
                    ),
                    "incremental_floor_utc": None,
                    "attempted": True,
                    "page_count": 3,
                    "aggregate_probe_count": 1,
                    "aggregate_upstream_total": count,
                    "partition_page_cap": page_cap,
                    "partition_page_counts": {"sz": 1, "sh": 1, "bj": 1},
                    "partition_rows_seen": {"sz": count, "sh": 0, "bj": 0},
                    "partition_upstream_totals": {
                        "sz": count,
                        "sh": 0,
                        "bj": 0,
                    },
                    "partition_completion": {"sz": True, "sh": True, "bj": True},
                    "cross_partition_unique_rows": count,
                    "page_cap_hit_partitions": [],
                    "logical_request_count": 4,
                    "physical_attempt_count": 4,
                    "fetched": count,
                    "newest_observed_at_utc": observed.isoformat(),
                    "pagination_complete": True,
                    "coverage_proven": True,
                    "checkpoint_committed": True,
                    "page_cap_hit": False,
                    "checkpoint_before": checkpoint_before.isoformat(),
                    "checkpoint_after": (
                        slice_date.isoformat() if date_closed else checkpoint_before.isoformat()
                    ),
                    "checkpoint_advanced": date_closed,
                    "failure": None,
                }
            ],
            "request_budget": {
                "page_size": 30,
                "aggregate_count_probe_per_date": 1,
                "partition_count": 3,
                "max_pages_per_partition": page_cap,
                "official_max_pages_per_partition": 100,
                "max_dates_per_run": 1,
                "max_logical_requests_per_run": 1 + 3 * page_cap,
                "max_physical_attempts_per_run": 2 * (1 + 3 * page_cap),
                "max_attempts_per_logical_request": 2,
                "min_interval_seconds": 1.0,
                "retry_backoff_seconds": [0.0],
                "logical_request_count": 4,
                "physical_attempt_count": 4,
                "page_101_requested": False,
            },
            "daily_checkpoint": {
                "lineage_before": (
                    "v2.1_daily_checkpoint_predecessor"
                    if slice_date == date(2026, 8, 20)
                    else "v2.2_daily_checkpoint"
                ),
                "verified_checkpoint_date_shanghai_before": checkpoint_before.isoformat(),
                "checkpoint_committed": True,
                "verified_checkpoint_date_shanghai_after": (
                    slice_date.isoformat() if date_closed else checkpoint_before.isoformat()
                ),
                "newest_observed_at_utc": observed.isoformat(),
                "latest_attempt_observed_at_utc": observed.isoformat(),
                "partial_checkpoint": False,
                "v2_1_predecessor_used": slice_date == date(2026, 8, 20),
                "closed_date_without_observed_high_reconciled": False,
                "closed_date_without_observed_high_shanghai": None,
                "closed_date_without_observed_high_aggregate_total": None,
                "closed_date_without_observed_high_unique_rows": None,
            },
            "poll_started_at_utc": poll_started_at.isoformat(),
            "market_date_at_poll": poll_started_at.astimezone(news_poll.MARKET_TIMEZONE)
            .date()
            .isoformat(),
            "requests": [
                {
                    "logical_request": logical_request,
                    "attempt": 1,
                    "method": "POST",
                    "host": "www.cninfo.com.cn",
                    "path": "/new/hisAnnouncement/query",
                    "requested_at": (
                        poll_started_at + timedelta(milliseconds=logical_request)
                    ).isoformat(),
                    "received_at": (
                        poll_started_at + timedelta(milliseconds=logical_request + 1)
                    ).isoformat(),
                    "latency_ms": 1.0,
                    "http_status": 200,
                    "failure_code": None,
                }
                for logical_request in range(1, 5)
            ],
            "tls_verification": True,
        },
    )


def _terminal_public(
    batch: news_poll.SourceBatch,
    dispositions: list[str],
    *,
    preceded_by_coverage_gap: bool = True,
) -> dict[str, object]:
    requests = cast(list[dict[str, object]], batch.details["requests"])
    received_times = [
        datetime.fromisoformat(cast(str, request["received_at"])) for request in requests
    ]
    fetch_completed = max(received_times) + timedelta(milliseconds=1)
    write_lock_acquired = fetch_completed + timedelta(milliseconds=1)
    first_available = write_lock_acquired + timedelta(milliseconds=1)
    last_available = first_available + timedelta(milliseconds=1)
    flush_completed = last_available + timedelta(milliseconds=1)
    commit_completed = flush_completed + timedelta(milliseconds=1)
    inserted_candidates = [
        candidate
        for candidate, disposition in zip(batch.candidates, dispositions, strict=True)
        if disposition == "inserted"
    ]
    inserted = len(inserted_candidates)
    return news_poll._batch_stats(
        batch,
        {
            "_candidate_dispositions": dispositions,
            "fetched": len(dispositions),
            "prepared": len(dispositions) - dispositions.count("filtered"),
            "filtered": dispositions.count("filtered"),
            "inserted": inserted,
            "duplicate_url": dispositions.count("duplicate_url"),
            "duplicate_content_hash": dispositions.count("duplicate_content_hash"),
            "symbol_null": sum(candidate.symbol is None for candidate in inserted_candidates),
            "published_at_null": sum(
                candidate.published_at is None for candidate in inserted_candidates
            ),
            "fetch_completed_at": fetch_completed.isoformat(),
            "db_write_lock_acquired_at": write_lock_acquired.isoformat(),
            "db_flush_completed_at": flush_completed.isoformat(),
            "db_commit_completed_at": commit_completed.isoformat(),
            "first_available_time": first_available.isoformat() if inserted else None,
            "last_available_time": last_available.isoformat() if inserted else None,
            "available_time_coverage": 1.0 if inserted else None,
            "preceded_by_coverage_gap_inserted": (inserted if preceded_by_coverage_gap else 0),
        },
    )


def _terminal_job_stats(
    source: dict[str, object],
    *,
    config_sha256: str = news_poll.EXPECTED_V2_2_CONFIG_SHA256,
    coverage_gap: bool = True,
) -> dict[str, object]:
    slices = cast(list[dict[str, object]], source["slices"])
    checkpoint = cast(dict[str, object], source["daily_checkpoint"])
    poll_started = cast(str, source["poll_started_at_utc"])
    commit_completed = datetime.fromisoformat(cast(str, source["db_commit_completed_at"]))
    slice_date = cast(str, slices[0]["date_shanghai"])
    if coverage_gap:
        coverage_details: dict[str, object] | None = {
            "reason": "cninfo_capacity_checkpoint_lag",
            "timezone": "Asia/Shanghai",
            "checkpoint_lineage": checkpoint["lineage_before"],
            "checkpoint_date_shanghai": checkpoint["verified_checkpoint_date_shanghai_before"],
            "target_closed_date_shanghai": slice_date,
            "recovery_poll_started_at_utc": poll_started,
            "recovery_poll_started_at_shanghai": datetime.fromisoformat(poll_started)
            .astimezone(news_poll.MARKET_TIMEZONE)
            .isoformat(),
        }
        run_mode = "coverage_gap_catchup"
    else:
        coverage_details = None
        run_mode = "regular_incremental"
    return {
        "config_version": "p4.1-news-poll-v2.2",
        "config_path": "config/p4_news_poll_v2_2.yaml",
        "config_sha256": config_sha256,
        "poll_started_at": poll_started,
        "poll_completed_at": (commit_completed + timedelta(milliseconds=1)).isoformat(),
        "execution_mode": "scheduler",
        "run_mode": run_mode,
        "coverage_gap": coverage_gap,
        "coverage_gap_details": coverage_details,
        "safety_unchanged": True,
        "terminal_diagnostics": None,
        "p4_2_unlocked": False,
        "sources": {"cninfo": source},
    }


def _terminal_complete(
    source: dict[str, object],
    *,
    expected_page_cap: int = 100,
    coverage_gap: bool = True,
) -> bool:
    config_sha256 = news_poll.EXPECTED_V2_2_CONFIG_SHA256_BY_PAGE_CAP[expected_page_cap]
    return news_poll._v2_2_cninfo_slices_complete(
        source,
        job_stats=_terminal_job_stats(
            source,
            config_sha256=config_sha256,
            coverage_gap=coverage_gap,
        ),
        expected_partitions=PARTITIONS,
        expected_page_cap=expected_page_cap,
    )


def _seed_v2_2_terminal_source(
    batch: news_poll.SourceBatch,
    *,
    config_sha256: str = news_poll.EXPECTED_V2_2_CONFIG_SHA256,
    coverage_gap: bool = True,
) -> dict[str, object]:
    source = _terminal_public(
        batch,
        ["inserted" for _candidate in batch.candidates],
        preceded_by_coverage_gap=coverage_gap,
    )
    stats = _terminal_job_stats(
        source,
        config_sha256=config_sha256,
        coverage_gap=coverage_gap,
    )
    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status="ok",
                stats=stats,
            )
        )
    return source


def _seed_v2_2_chain_through(day: int) -> None:
    _seed_v2_1(date(2026, 8, 19))
    for current_day in range(20, day + 1):
        _seed_v2_2_terminal_source(
            _successful_batch(
                [
                    _candidate(
                        identifier=current_day,
                        url=f"https://static.cninfo.com.cn/chain-{current_day}.PDF",
                        market_date=date(2026, 8, current_day),
                    )
                ],
                slice_date=date(2026, 8, current_day),
            )
        )


def _noncritical_batch(source_id: str) -> news_poll.SourceBatch:
    return news_poll.SourceBatch(source_id=source_id, status="ok")


def test_v2_2_config_is_frozen_at_100_pages_and_retains_the_80_page_predecessor(
    tmp_path: Path,
) -> None:
    v2_1_bytes = V2_1_CONFIG_PATH.read_bytes()
    raw = CONFIG_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    document = yaml.safe_load(raw)

    assert news_poll.DEFAULT_CONFIG_PATH == news_poll.V2_3_CONFIG_PATH
    assert news_poll.V2_2_CONFIG_PATH == CONFIG_PATH
    assert hashlib.sha256(v2_1_bytes).hexdigest() == (
        "9d56e137baf10bd0858723a93aff02c57bf7b35f8705f1817b16a89ec615183f"
    )
    assert hashlib.sha256(CORE_CONFIG_PATH.read_bytes()).hexdigest() == (
        "6bdc24abbc943bb52ecc416fd72180e356fbcb3bc55eb6a96f98d5b0be8137cd"
    )
    assert digest == news_poll.EXPECTED_V2_2_CONFIG_SHA256
    assert document["schema_version"] == "p4.1-news-poll-v2.2"
    assert document["sources"]["cninfo"]["partitions"] == PARTITIONS
    assert document["sources"]["cninfo"]["max_dates_per_run"] == 1
    cninfo = cast(dict[str, object], document["sources"]["cninfo"])
    assert cninfo["max_pages_per_partition"] == 100
    assert news_poll._v2_2_cninfo_request_budgets(cninfo) == (301, 602)

    needle = b"    max_pages_per_partition: 100\n"
    predecessor = raw.replace(needle, b"    max_pages_per_partition: 80\n")
    assert raw.count(needle) == 1
    assert [
        (before, after)
        for before, after in zip(raw.splitlines(), predecessor.splitlines(), strict=True)
        if before != after
    ] == [
        (
            b"    max_pages_per_partition: 100",
            b"    max_pages_per_partition: 80",
        )
    ]
    predecessor_path = tmp_path / "p4_news_poll_v2_2-page-cap-80.yaml"
    predecessor_path.write_bytes(predecessor)
    predecessor_config = news_poll.load_news_poll_config(predecessor_path)
    predecessor_sources = cast(dict[str, object], predecessor_config.document["sources"])
    predecessor_cninfo = cast(dict[str, object], predecessor_sources["cninfo"])

    assert (
        hashlib.sha256(predecessor).hexdigest()
        == (news_poll.EXPECTED_V2_2_CONFIG_SHA256_BY_PAGE_CAP[80])
    )
    assert predecessor_cninfo["max_pages_per_partition"] == 80
    assert news_poll._v2_2_cninfo_request_budgets(predecessor_cninfo) == (241, 482)


def test_v2_2_default_registration_is_scheduler_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = news_poll.load_news_poll_config(CONFIG_PATH)
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
        assert error.value.stats["implementation_gate"] == "v2_2_scheduler_only"
        assert error.value.stats["network_attempted"] is False

    monkeypatch.delenv(news_poll.NEWS_POLL_ENABLED_ENV, raising=False)
    news_poll.register_news_poll_job()
    spec = JOBS["news_poll"]
    default = inspect.signature(spec.func).parameters["config_path"].default
    assert spec.func is news_poll.run_news_poll
    assert spec.trigger is None
    assert default == news_poll.V2_3_CONFIG_PATH


def test_v2_2_checkpoint_uses_v2_1_only_as_exact_predecessor() -> None:
    _seed_v2_1(date(2026, 8, 19))
    _seed_checkpoint(
        version="p4.1-news-poll-v2.2",
        config_sha256="0" * 64,
        checkpoint_date=date(2026, 8, 22),
        observed=datetime(2026, 8, 22, 10, tzinfo=UTC),
    )
    config = _config()

    predecessor = news_poll._last_committed_daily_checkpoint(config, "cninfo")

    assert predecessor.lineage == "v2.1_daily_checkpoint_predecessor"
    assert predecessor.checkpoint_date_shanghai == date(2026, 8, 19)

    _seed_v2_2_terminal_source(
        _successful_batch(
            [_candidate(identifier=20, url="https://static.cninfo.com.cn/checkpoint-20.PDF")],
            slice_date=date(2026, 8, 20),
        ),
        config_sha256=config.sha256,
    )
    active = news_poll._last_committed_daily_checkpoint(config, "cninfo")
    assert active.lineage == "v2.2_daily_checkpoint"
    assert active.checkpoint_date_shanghai == date(2026, 8, 20)


def test_v2_2_page_cap_upgrade_preserves_registered_v2_2_checkpoint() -> None:
    _seed_v2_1(date(2026, 8, 19))
    for day in (20, 21):
        _seed_v2_2_terminal_source(
            _successful_batch(
                [
                    _candidate(
                        identifier=day,
                        url=f"https://static.cninfo.com.cn/checkpoint-{day}.PDF",
                        market_date=date(2026, 8, day),
                    )
                ],
                slice_date=date(2026, 8, day),
                page_cap=80,
            ),
            config_sha256=news_poll.EXPECTED_V2_2_CONFIG_SHA256_BY_PAGE_CAP[80],
        )
    upgraded_config = news_poll.load_news_poll_config(CONFIG_PATH)
    _seed_v2_2_terminal_source(
        _successful_batch(
            [
                _candidate(
                    identifier=22,
                    url="https://static.cninfo.com.cn/checkpoint-22.PDF",
                    market_date=date(2026, 8, 22),
                )
            ],
            slice_date=date(2026, 8, 22),
            page_cap=100,
        ),
        config_sha256=news_poll.EXPECTED_V2_2_CONFIG_SHA256,
    )

    seed = news_poll._last_committed_daily_checkpoint(upgraded_config, "cninfo")

    assert seed.lineage == "v2.2_daily_checkpoint"
    assert seed.checkpoint_date_shanghai == date(2026, 8, 22)
    assert news_poll._v2_1_slice_dates(
        seed,
        poll_started_at_utc=datetime(2026, 8, 23, 1, tzinfo=UTC),
        max_dates_per_run=1,
    ) == [date(2026, 8, 23)]


def test_v2_2_history_rejects_orphan_terminal_without_exact_predecessor() -> None:
    _seed_v2_2_terminal_source(
        _successful_batch(
            [
                _candidate(
                    identifier=22,
                    url="https://static.cninfo.com.cn/orphan-22.PDF",
                    market_date=date(2026, 8, 22),
                )
            ],
            slice_date=date(2026, 8, 22),
        )
    )

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")

    assert seed.lineage == "missing"
    assert seed.checkpoint_date_shanghai is None


@pytest.mark.parametrize("case", ["first_claims_v2_2", "later_claims_v2_1"])
def test_v2_2_history_rejects_lineage_that_does_not_match_accepted_chain(
    case: str,
) -> None:
    if case == "first_claims_v2_2":
        _seed_v2_1(date(2026, 8, 19))
        target = date(2026, 8, 20)
        expected_date = date(2026, 8, 19)
        claimed_lineage = "v2.2_daily_checkpoint"
        predecessor_used = False
    else:
        _seed_v2_2_chain_through(20)
        target = date(2026, 8, 21)
        expected_date = date(2026, 8, 20)
        claimed_lineage = "v2.1_daily_checkpoint_predecessor"
        predecessor_used = True
    batch = _successful_batch(
        [
            _candidate(
                identifier=target.day,
                url=f"https://static.cninfo.com.cn/lineage-{target.day}.PDF",
                market_date=target,
            )
        ],
        slice_date=target,
    )
    source = _terminal_public(batch, ["inserted"])
    checkpoint = cast(dict[str, object], source["daily_checkpoint"])
    checkpoint["lineage_before"] = claimed_lineage
    checkpoint["v2_1_predecessor_used"] = predecessor_used
    stats = _terminal_job_stats(source)
    with get_session() as session:
        session.add(JobRun(job_name="news_poll", status="ok", stats=stats))

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")

    assert seed.checkpoint_date_shanghai == expected_date


def test_v2_2_history_rejects_internally_valid_date_gap() -> None:
    _seed_v2_2_chain_through(20)
    _seed_v2_2_terminal_source(
        _successful_batch(
            [
                _candidate(
                    identifier=22,
                    url="https://static.cninfo.com.cn/gap-22.PDF",
                    market_date=date(2026, 8, 22),
                )
            ],
            slice_date=date(2026, 8, 22),
        )
    )

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")

    assert seed.checkpoint_date_shanghai == date(2026, 8, 20)


def test_v2_2_history_rejects_observed_high_rollback_on_same_checkpoint() -> None:
    _seed_v2_2_chain_through(22)
    for minute in (30, 20):
        _seed_v2_2_terminal_source(
            _successful_batch(
                [
                    _candidate(
                        identifier=minute,
                        url=f"https://static.cninfo.com.cn/current-{minute}.PDF",
                        market_date=date(2026, 8, 23),
                    )
                ],
                slice_date=date(2026, 8, 23),
                date_closed=False,
                observed_hour_utc=0,
                observed_minute_utc=minute,
            ),
            coverage_gap=False,
        )

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")

    assert seed.checkpoint_date_shanghai == date(2026, 8, 22)
    assert seed.newest_observed_at_utc == datetime(2026, 8, 23, 0, 30, tzinfo=UTC)


def test_v2_2_catches_up_820_821_822_one_closed_date_per_run() -> None:
    _seed_v2_1(date(2026, 8, 19))
    observed_dates: list[str] = []

    for expected in (date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 22)):
        batch = _fetch(_FakeClient(_empty_partition_outcomes()))
        slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
        checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])
        observed_dates.append(cast(str, slice_stats["date_shanghai"]))
        assert batch.status == "ok"
        assert batch.details["slice_dates_shanghai"] == [expected.isoformat()]
        assert slice_stats["checkpoint_committed"] is True
        assert checkpoint["verified_checkpoint_date_shanghai_after"] == (expected.isoformat())
        assert checkpoint["closed_date_without_observed_high_reconciled"] is True
        assert checkpoint["closed_date_without_observed_high_shanghai"] == expected.isoformat()
        assert checkpoint["closed_date_without_observed_high_aggregate_total"] == 0
        assert checkpoint["closed_date_without_observed_high_unique_rows"] == 0
        _seed_v2_2_terminal_source(batch)

    assert observed_dates == ["2026-08-20", "2026-08-21", "2026-08-22"]
    current = _fetch(_FakeClient(_empty_partition_outcomes()))
    current_slice = cast(list[dict[str, object]], current.details["slices"])[0]
    assert current.details["slice_dates_shanghai"] == ["2026-08-23"]
    assert current_slice["mode"] == "current_date_incremental"
    assert current_slice["checkpoint_advanced"] is False
    assert news_poll._v2_run_context(
        _config(),
        datetime(2026, 8, 23, 1, tzinfo=UTC),
    ) == {
        "run_mode": "regular_incremental",
        "coverage_gap": False,
        "coverage_gap_details": None,
    }


def test_v2_2_nonempty_closed_date_without_timestamp_survives_jobrun_round_trip() -> None:
    _seed_v2_1(date(2026, 8, 19))
    target = date(2026, 8, 20)
    row = _row(1, target)
    row["announcementTime"] = None

    batch = _fetch(
        _FakeClient(
            [
                _probe(1),
                _page(1, [row]),
                _page(0, []),
                _page(0, []),
            ]
        )
    )
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])

    assert batch.status == "ok"
    assert len(batch.candidates) == 1
    assert batch.candidates[0].published_at is None
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == target.isoformat()
    assert checkpoint["newest_observed_at_utc"] == "2026-08-19T10:00:00+00:00"
    assert checkpoint["closed_date_without_observed_high_reconciled"] is True
    assert checkpoint["closed_date_without_observed_high_shanghai"] == target.isoformat()
    assert checkpoint["closed_date_without_observed_high_aggregate_total"] == 1
    assert checkpoint["closed_date_without_observed_high_unique_rows"] == 1

    _seed_v2_2_terminal_source(batch)

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")
    assert seed.lineage == "v2.2_daily_checkpoint"
    assert seed.checkpoint_date_shanghai == target
    assert seed.newest_observed_at_utc == datetime(2026, 8, 19, 10, tzinfo=UTC)

    following = _fetch(_FakeClient(_empty_partition_outcomes()))
    assert following.details["slice_dates_shanghai"] == ["2026-08-21"]


@pytest.mark.parametrize(
    "tamper",
    ["marker_only", "slice_date", "slice_count", "coverage"],
)
def test_v2_2_no_observed_high_proof_rejects_unbound_or_tampered_slice(
    tamper: str,
) -> None:
    _seed_v2_1(date(2026, 8, 19))
    target = date(2026, 8, 20)
    row = _row(1, target)
    row["announcementTime"] = None
    batch = _fetch(
        _FakeClient(
            [
                _probe(1),
                _page(1, [row]),
                _page(0, []),
                _page(0, []),
            ]
        )
    )
    source = _terminal_public(batch, ["inserted"])
    job_stats = _terminal_job_stats(source)
    checkpoint = cast(dict[str, object], source["daily_checkpoint"])
    slices = cast(list[dict[str, object]], source["slices"])
    if tamper == "marker_only":
        source = {"daily_checkpoint": checkpoint}
    elif tamper == "slice_date":
        slices[0]["date_shanghai"] = "2026-08-21"
    elif tamper == "slice_count":
        slices[0]["aggregate_upstream_total"] = 2
    elif tamper == "coverage":
        slices[0]["coverage_proven"] = False
    else:  # pragma: no cover - parametrization is frozen immediately above.
        raise AssertionError(tamper)
    job_stats["sources"] = {"cninfo": source}

    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status="ok",
                stats=job_stats,
            )
        )

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")
    assert seed.lineage == "v2.1_daily_checkpoint_predecessor"
    assert seed.checkpoint_date_shanghai == date(2026, 8, 19)


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_slices",
        "aggregate_count",
        "checkpoint_backwards",
        "observed_cross_date",
        "page_cap_digest",
    ],
)
def test_v2_2_timestamp_checkpoint_history_revalidates_terminal_evidence(
    tamper: str,
) -> None:
    _seed_v2_1(date(2026, 8, 19))
    batch = _successful_batch(
        [_candidate(identifier=1, url="https://static.cninfo.com.cn/history-tamper.PDF")],
        slice_date=date(2026, 8, 20),
    )
    source = _terminal_public(batch, ["inserted"])
    job_stats = _terminal_job_stats(source)
    item = cast(list[dict[str, object]], source["slices"])[0]
    checkpoint = cast(dict[str, object], source["daily_checkpoint"])
    if tamper == "missing_slices":
        source.pop("slices")
    elif tamper == "aggregate_count":
        item["aggregate_upstream_total"] = 2
    elif tamper == "checkpoint_backwards":
        item["checkpoint_before"] = "2026-08-22"
        checkpoint["verified_checkpoint_date_shanghai_before"] = "2026-08-22"
    elif tamper == "observed_cross_date":
        cross_date = datetime(2026, 8, 21, 10, tzinfo=UTC).isoformat()
        item["newest_observed_at_utc"] = cross_date
        checkpoint["newest_observed_at_utc"] = cross_date
        checkpoint["latest_attempt_observed_at_utc"] = cross_date
    elif tamper == "page_cap_digest":
        item["partition_page_cap"] = 80
    else:  # pragma: no cover - parametrization is frozen immediately above.
        raise AssertionError(tamper)
    job_stats["sources"] = {"cninfo": source}

    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status="ok",
                stats=job_stats,
            )
        )

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")
    assert seed.lineage == "v2.1_daily_checkpoint_predecessor"
    assert seed.checkpoint_date_shanghai == date(2026, 8, 19)


def test_v2_2_reconciles_aggregate_and_all_partitions_including_empty_bj() -> None:
    _seed_v2_1(date(2026, 8, 19))
    target = date(2026, 8, 20)
    client = _FakeClient(
        [
            _probe(3),
            _page(2, [_row(2, target), _row(1, target)]),
            _page(1, [_row(3, target)]),
            _page(0, []),
        ]
    )

    batch = _fetch(client)
    public = _terminal_public(batch, ["inserted", "inserted", "inserted"])
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    request_data = [_call_data(call) for call in client.calls]

    assert batch.status == "ok"
    assert len(batch.candidates) == 3
    assert request_data[0].get("plate") is None
    assert [data["plate"] for data in request_data[1:]] == PARTITIONS
    assert all(data["column"] == "szse" for data in request_data)
    assert slice_stats["aggregate_upstream_total"] == 3
    assert slice_stats["partition_rows_seen"] == {"sz": 2, "sh": 1, "bj": 0}
    assert slice_stats["partition_upstream_totals"] == {
        "sz": 2,
        "sh": 1,
        "bj": 0,
    }
    assert slice_stats["partition_completion"] == {
        "sz": True,
        "sh": True,
        "bj": True,
    }
    assert slice_stats["cross_partition_unique_rows"] == 3
    assert {
        candidate.raw_payload["_alphapilot_cninfo_partition"] for candidate in batch.candidates
    } == {"sz", "sh"}
    assert _terminal_complete(public)


def test_v2_2_accepts_live_shaped_empty_bj_null_and_terminal_consumer_passes() -> None:
    _seed_v2_1(date(2026, 8, 19))
    target = date(2026, 8, 20)
    client = _FakeClient(
        [
            _probe(2),
            _page(1, [_row(1, target)]),
            _page(1, [_row(2, target)]),
            _page(0, None),
        ]
    )

    batch = _fetch(client)
    public = _terminal_public(batch, ["inserted", "inserted"])
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])

    assert batch.status == "ok"
    assert len(batch.candidates) == 2
    assert slice_stats["partition_rows_seen"] == {"sz": 1, "sh": 1, "bj": 0}
    assert slice_stats["partition_upstream_totals"] == {"sz": 1, "sh": 1, "bj": 0}
    assert slice_stats["partition_completion"] == {"sz": True, "sh": True, "bj": True}
    assert slice_stats["pagination_complete"] is True
    assert slice_stats["coverage_proven"] is True
    assert slice_stats["checkpoint_committed"] is True
    assert slice_stats["checkpoint_advanced"] is True
    assert checkpoint["checkpoint_committed"] is True
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-20"
    assert batch.details["response_shape_events"] == [
        {
            "date_shanghai": "2026-08-20",
            "partition": "bj",
            "page": 1,
            "response_json_type": "object",
            "announcements_field_present": True,
            "announcements_json_type": "null",
            "total_announcement_json_type": "integer",
            "total_announcement_value": 0,
            "normalized_to_empty_list": True,
        }
    ]
    assert public["response_shape_events"] == batch.details["response_shape_events"]
    assert _terminal_complete(public)


def test_v2_2_rejects_other_announcement_shapes_without_checkpoint_or_event() -> None:
    _seed_v2_1(date(2026, 8, 19))
    invalid_pages = [
        _FakeResponse({"totalAnnouncement": 0, "hasMore": False}),
        _page(1, None),
        _page(False, None),
        _page(0, False),
        _page(0, ""),
        _page(0, {}),
        _page(0, 0),
    ]

    for invalid_page in invalid_pages:
        batch = _fetch(_FakeClient([_probe(0), invalid_page]))
        slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
        checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])

        assert batch.status == "unavailable"
        assert len(batch.failures) == 1
        assert batch.failures[0]["code"] == "schema_changed"
        assert batch.failures[0]["blocked"] is False
        assert batch.failures[0]["error_type"] == "NewsSourceError"
        assert batch.failures[0]["date_shanghai"] == "2026-08-20"
        assert batch.failures[0]["partition"] == "sz"
        events = cast(list[dict[str, object]], batch.details["response_shape_events"])
        assert len(events) == 1
        assert events[0]["event"] == "schema_changed_response_shape"
        assert events[0]["site"] in {
            "partition_announcements_not_list",
            "partition_total_invalid",
        }
        assert "normalized_to_empty_list" not in events[0]
        assert slice_stats["pagination_complete"] is False
        assert slice_stats["checkpoint_committed"] is False
        assert slice_stats["checkpoint_advanced"] is False
        assert slice_stats["checkpoint_before"] == "2026-08-19"
        assert slice_stats["checkpoint_after"] == "2026-08-19"
        assert checkpoint["checkpoint_committed"] is False
        assert checkpoint["verified_checkpoint_date_shanghai_before"] == "2026-08-19"
        assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-19"


def test_v2_2_schema_changed_sites_record_only_response_shapes_without_values() -> None:
    _seed_v2_1(date(2026, 8, 19))
    target = date(2026, 8, 20)
    missing_id = _row(1, target)
    missing_id.pop("announcementId")
    missing_adjunct = _row(2, target)
    missing_adjunct["announcementTitle"] = "DO_NOT_STORE_TITLE_CONTENT"
    missing_adjunct.pop("adjunctUrl")
    invalid_timestamp = _row(3, target)
    invalid_timestamp["announcementTime"] = "DO_NOT_STORE_TIMESTAMP_VALUE"
    cases: list[tuple[str, list[_FakeResponse]]] = [
        (
            "aggregate_response_not_object",
            [_FakeResponse(["DO_NOT_STORE_AGGREGATE_BODY"])],
        ),
        (
            "aggregate_total_invalid",
            [_probe("DO_NOT_STORE_AGGREGATE_TOTAL")],
        ),
        (
            "partition_response_not_object",
            [_probe(0), _FakeResponse(["DO_NOT_STORE_PARTITION_BODY"])],
        ),
        (
            "partition_total_invalid",
            [_probe(0), _page("DO_NOT_STORE_PARTITION_TOTAL", [])],
        ),
        (
            "partition_announcements_not_list",
            [_probe(0), _page(0, {"body": "DO_NOT_STORE_ANNOUNCEMENTS_BODY"})],
        ),
        (
            "announcement_row_not_object",
            [_probe(1), _page(1, ["DO_NOT_STORE_ROW_BODY"])],
        ),
        (
            "announcement_id_invalid",
            [_probe(1), _page(1, [missing_id])],
        ),
        (
            "announcement_content_fields_invalid",
            [_probe(1), _page(1, [missing_adjunct])],
        ),
        (
            "announcement_timestamp_invalid",
            [_probe(1), _page(1, [invalid_timestamp])],
        ),
    ]
    page_fields_integer = [
        ("announcements", "array"),
        ("hasMore", "boolean"),
        ("totalAnnouncement", "integer"),
    ]
    expected_shapes: dict[
        str,
        tuple[str, list[tuple[str, str]], str | None, list[tuple[str, str]]],
    ] = {
        "aggregate_response_not_object": ("array", [], None, []),
        "aggregate_total_invalid": (
            "object",
            [("totalAnnouncement", "string")],
            None,
            [],
        ),
        "partition_response_not_object": ("array", [], None, []),
        "partition_total_invalid": (
            "object",
            [
                ("announcements", "array"),
                ("hasMore", "boolean"),
                ("totalAnnouncement", "string"),
            ],
            None,
            [],
        ),
        "partition_announcements_not_list": (
            "object",
            [
                ("announcements", "object"),
                ("hasMore", "boolean"),
                ("totalAnnouncement", "integer"),
            ],
            None,
            [],
        ),
        "announcement_row_not_object": (
            "object",
            page_fields_integer,
            "string",
            [],
        ),
        "announcement_id_invalid": (
            "object",
            page_fields_integer,
            "object",
            [
                ("adjunctUrl", "string"),
                ("announcementTime", "integer"),
                ("announcementTitle", "string"),
                ("secCode", "string"),
            ],
        ),
        "announcement_content_fields_invalid": (
            "object",
            page_fields_integer,
            "object",
            [
                ("announcementId", "string"),
                ("announcementTime", "integer"),
                ("announcementTitle", "string"),
                ("secCode", "string"),
            ],
        ),
        "announcement_timestamp_invalid": (
            "object",
            page_fields_integer,
            "object",
            [
                ("adjunctUrl", "string"),
                ("announcementId", "string"),
                ("announcementTime", "string"),
                ("announcementTitle", "string"),
                ("secCode", "string"),
            ],
        ),
    }
    forbidden_values = {
        "DO_NOT_STORE_AGGREGATE_BODY",
        "DO_NOT_STORE_AGGREGATE_TOTAL",
        "DO_NOT_STORE_PARTITION_BODY",
        "DO_NOT_STORE_PARTITION_TOTAL",
        "DO_NOT_STORE_ANNOUNCEMENTS_BODY",
        "DO_NOT_STORE_ROW_BODY",
        "DO_NOT_STORE_TITLE_CONTENT",
        "DO_NOT_STORE_TIMESTAMP_VALUE",
    }
    event_keys = {
        "event",
        "site",
        "date_shanghai",
        "partition",
        "page",
        "row_index",
        "response_json_type",
        "response_fields",
        "row_json_type",
        "row_fields",
    }
    allowed_json_types = {
        "null",
        "boolean",
        "integer",
        "number",
        "string",
        "array",
        "object",
        "non_json",
    }

    for expected_site, outcomes in cases:
        batch = _fetch(_FakeClient(outcomes))
        events = cast(list[dict[str, object]], batch.details["response_shape_events"])

        assert batch.status == "unavailable"
        assert batch.failures[0]["code"] == "schema_changed"
        assert len(events) == 1
        event = events[0]
        assert set(event) == event_keys
        assert event["event"] == "schema_changed_response_shape"
        assert event["site"] == expected_site
        assert event["date_shanghai"] == "2026-08-20"
        (
            expected_response_type,
            expected_response_fields,
            expected_row_type,
            expected_row_fields,
        ) = expected_shapes[expected_site]
        assert event["response_json_type"] == expected_response_type
        assert event["row_json_type"] == expected_row_type
        for field_group in (event["response_fields"], event["row_fields"]):
            assert isinstance(field_group, list)
            for field in field_group:
                assert isinstance(field, dict)
                assert set(field) == {"name", "json_type"}
                assert isinstance(field["name"], str)
                assert field["json_type"] in allowed_json_types
        response_fields = cast(list[dict[str, object]], event["response_fields"])
        row_fields = cast(list[dict[str, object]], event["row_fields"])
        assert [
            (cast(str, field["name"]), cast(str, field["json_type"])) for field in response_fields
        ] == expected_response_fields
        assert [
            (cast(str, field["name"]), cast(str, field["json_type"])) for field in row_fields
        ] == expected_row_fields
        serialized = json.dumps(event, ensure_ascii=False, sort_keys=True)
        assert all(value not in serialized for value in forbidden_values)


@pytest.mark.parametrize(
    "missing_field",
    [
        "date_closed",
        "mode",
        "incremental_floor_utc",
        "logical_request_count",
        "physical_attempt_count",
        "newest_observed_at_utc",
        "checkpoint_before",
        "checkpoint_after",
        "partition_completion",
    ],
)
def test_v2_2_terminal_rejects_missing_required_slice_field(
    missing_field: str,
) -> None:
    batch = _successful_batch(
        [_candidate(identifier=1, url="https://static.cninfo.com.cn/terminal-field.PDF")],
        slice_date=date(2026, 8, 20),
    )
    public = _terminal_public(batch, ["inserted"])
    item = cast(list[dict[str, object]], public["slices"])[0]
    item.pop(missing_field)

    assert not _terminal_complete(public)


@pytest.mark.parametrize(
    "missing_field",
    [
        "closed_date_without_observed_high_reconciled",
        "closed_date_without_observed_high_shanghai",
        "closed_date_without_observed_high_aggregate_total",
        "closed_date_without_observed_high_unique_rows",
    ],
)
def test_v2_2_terminal_rejects_missing_checkpoint_proof_field(
    missing_field: str,
) -> None:
    batch = _successful_batch(
        [_candidate(identifier=1, url="https://static.cninfo.com.cn/checkpoint-field.PDF")],
        slice_date=date(2026, 8, 20),
    )
    public = _terminal_public(batch, ["inserted"])
    checkpoint = cast(dict[str, object], public["daily_checkpoint"])
    checkpoint.pop(missing_field)

    assert not _terminal_complete(public)


@pytest.mark.parametrize(
    "tamper",
    ["bool_rows", "probe_zero", "probe_absent", "pages_over_cap", "float_page_count"],
)
def test_v2_2_terminal_rejects_malformed_reconciliation_counts(tamper: str) -> None:
    batch = _successful_batch(
        [_candidate(identifier=1, url="https://static.cninfo.com.cn/count-tamper.PDF")],
        slice_date=date(2026, 8, 20),
    )
    public = _terminal_public(batch, ["inserted"])
    item = cast(list[dict[str, object]], public["slices"])[0]
    if tamper == "bool_rows":
        rows_seen = cast(dict[str, object], item["partition_rows_seen"])
        rows_seen["sh"] = False
    elif tamper == "probe_zero":
        item["aggregate_probe_count"] = 0
    elif tamper == "probe_absent":
        item.pop("aggregate_probe_count")
    elif tamper == "pages_over_cap":
        page_counts = cast(dict[str, object], item["partition_page_counts"])
        page_counts["sz"] = 81
    elif tamper == "float_page_count":
        item["page_count"] = 3.0
    else:  # pragma: no cover - parametrization is frozen immediately above.
        raise AssertionError(tamper)

    assert not _terminal_complete(public)


@pytest.mark.parametrize(
    "tamper",
    ["proof_aggregate", "proof_unique", "proof_date", "proof_marker"],
)
def test_v2_2_terminal_rejects_no_observed_high_proof_tampering(tamper: str) -> None:
    _seed_v2_1(date(2026, 8, 19))
    target = date(2026, 8, 20)
    row = _row(1, target)
    row["announcementTime"] = None
    batch = _fetch(
        _FakeClient(
            [
                _probe(1),
                _page(1, [row]),
                _page(0, []),
                _page(0, []),
            ]
        )
    )
    public = _terminal_public(batch, ["inserted"])
    assert _terminal_complete(public)
    checkpoint = cast(dict[str, object], public["daily_checkpoint"])
    if tamper == "proof_aggregate":
        checkpoint["closed_date_without_observed_high_aggregate_total"] = 2
    elif tamper == "proof_unique":
        checkpoint["closed_date_without_observed_high_unique_rows"] = 2
    elif tamper == "proof_date":
        checkpoint["closed_date_without_observed_high_shanghai"] = "2026-08-21"
    elif tamper == "proof_marker":
        checkpoint["closed_date_without_observed_high_reconciled"] = False
    else:  # pragma: no cover - parametrization is frozen immediately above.
        raise AssertionError(tamper)

    assert not _terminal_complete(public)


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_fetch_completed",
        "lock_order",
        "availability_missing",
        "coverage",
        "gap_count",
        "request_missing_logical",
        "request_bad_host",
        "request_bad_failure_identity",
    ],
)
def test_v2_2_terminal_rejects_pit_gap_or_request_evidence_tampering(
    tamper: str,
) -> None:
    batch = _successful_batch(
        [_candidate(identifier=1, url="https://static.cninfo.com.cn/audit-tamper.PDF")],
        slice_date=date(2026, 8, 20),
    )
    public = _terminal_public(batch, ["inserted"])
    requests = cast(list[dict[str, object]], public["requests"])
    if tamper == "missing_fetch_completed":
        public.pop("fetch_completed_at")
    elif tamper == "lock_order":
        public["db_write_lock_acquired_at"] = "2026-08-23T00:59:59+00:00"
    elif tamper == "availability_missing":
        public["first_available_time"] = None
    elif tamper == "coverage":
        public["available_time_coverage"] = None
    elif tamper == "gap_count":
        public["preceded_by_coverage_gap_inserted"] = 0
    elif tamper == "request_missing_logical":
        requests[0].pop("logical_request")
    elif tamper == "request_bad_host":
        requests[0]["host"] = "example.invalid"
    elif tamper == "request_bad_failure_identity":
        requests[0]["http_status"] = 500
    else:  # pragma: no cover - parametrization is frozen immediately above.
        raise AssertionError(tamper)

    assert not _terminal_complete(public)


@pytest.mark.parametrize("tamper", ["closed_without_gap", "commit_after_poll_completed"])
def test_v2_2_terminal_binds_source_audit_to_top_level_jobrun(tamper: str) -> None:
    batch = _successful_batch(
        [_candidate(identifier=1, url="https://static.cninfo.com.cn/top-level-tamper.PDF")],
        slice_date=date(2026, 8, 20),
    )
    if tamper == "closed_without_gap":
        public = _terminal_public(
            batch,
            ["inserted"],
            preceded_by_coverage_gap=False,
        )
        stats = _terminal_job_stats(public, coverage_gap=False)
    else:
        public = _terminal_public(batch, ["inserted"])
        stats = _terminal_job_stats(public)
        public["db_commit_completed_at"] = "2027-08-23T01:00:00+00:00"

    assert not news_poll._v2_2_cninfo_slices_complete(
        public,
        job_stats=stats,
        expected_partitions=PARTITIONS,
        expected_page_cap=80,
    )


@pytest.mark.parametrize(
    ("case", "expected_code", "outcomes"),
    [
        (
            "partition-count",
            "partition_count_mismatch",
            lambda target: [
                _probe(1),
                _page(2, [_row(1, target)]),
            ],
        ),
        (
            "aggregate-count",
            "aggregate_count_mismatch",
            lambda target: [
                _probe(2),
                _page(1, [_row(1, target)]),
                _page(0, []),
                _page(0, []),
            ],
        ),
        (
            "cross-partition-duplicate",
            "cross_partition_duplicate",
            lambda target: [
                _probe(2),
                _page(1, [_row(1, target)]),
                _page(1, [_row(1, target)]),
            ],
        ),
        (
            "schema",
            "schema_changed",
            lambda _target: [_probe("3")],
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_v2_2_count_duplicate_and_schema_fail_closed_without_checkpoint(
    case: str,
    expected_code: str,
    outcomes: Callable[[date], Sequence[_FakeResponse | Exception]],
) -> None:
    del case
    _seed_v2_1(date(2026, 8, 19))
    target = date(2026, 8, 20)
    client = _FakeClient(outcomes(target))

    batch = _fetch(client)
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])

    assert batch.status == "unavailable"
    assert batch.failures[0]["code"] == expected_code
    assert slice_stats["failure"] == {
        "code": expected_code,
        "blocked": False,
        "error_type": "NewsSourceError",
        "date_shanghai": "2026-08-20",
        **(
            {"partition": "sz"}
            if expected_code == "partition_count_mismatch"
            else ({"partition": "sh"} if expected_code == "cross_partition_duplicate" else {})
        ),
    }
    assert slice_stats["pagination_complete"] is False
    assert slice_stats["checkpoint_committed"] is False
    assert slice_stats["checkpoint_advanced"] is False
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-19"


@pytest.mark.parametrize("page_cap", [80, 100])
def test_v2_2_page_cap_is_explicit_degraded_and_never_requests_beyond_cap(
    tmp_path: Path,
    page_cap: int,
) -> None:
    _seed_v2_1(date(2026, 8, 19))
    if page_cap == 80:
        raw = CONFIG_PATH.read_bytes().replace(
            b"    max_pages_per_partition: 100\n",
            b"    max_pages_per_partition: 80\n",
        )
        path = tmp_path / "p4_news_poll_v2_2-page-cap-80.yaml"
        path.write_bytes(raw)
        config = _config(path)
    else:
        config = _config()
    target = date(2026, 8, 20)
    total = page_cap * 30 + 1
    pages = [
        _page(
            total,
            [
                _row(
                    page * 30 + offset,
                    target,
                    seconds_after_open=-(page * 30 + offset),
                )
                for offset in range(30)
            ],
            has_more=True,
        )
        for page in range(page_cap)
    ]
    client = _FakeClient([_probe(total), *pages, _page(0, []), _page(0, [])])

    batch = _fetch(client, config=config)
    public = news_poll._batch_stats(batch)
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])
    partition_calls = [_call_data(call) for call in client.calls[1:]]
    sz_pages = [cast(int, data["pageNum"]) for data in partition_calls if data.get("plate") == "sz"]

    assert batch.status == "degraded"
    assert len(batch.candidates) == page_cap * 30
    assert batch.failures == [
        {
            "code": "cninfo_partition_pagination_incomplete",
            "blocked": False,
            "error_type": "NewsSourceError",
            "date_shanghai": "2026-08-20",
            "partitions": ["sz"],
        }
    ]
    assert slice_stats["page_cap_hit"] is True
    assert slice_stats["page_cap_hit_partitions"] == ["sz"]
    assert slice_stats["partition_page_counts"] == {
        "sz": page_cap,
        "sh": 1,
        "bj": 1,
    }
    assert slice_stats["partition_rows_seen"] == {
        "sz": page_cap * 30,
        "sh": 0,
        "bj": 0,
    }
    assert slice_stats["pagination_complete"] is False
    assert slice_stats["coverage_proven"] is False
    assert slice_stats["checkpoint_committed"] is False
    assert slice_stats["checkpoint_advanced"] is False
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-19"
    assert sz_pages == list(range(1, page_cap + 1))
    assert max(sz_pages) == page_cap
    assert 101 not in sz_pages
    assert news_poll._v2_2_cninfo_page_cap_only(
        public,
        expected_partitions=PARTITIONS,
    )


def test_v2_2_transport_retry_and_budget_accounting_are_bounded() -> None:
    _seed_v2_1(date(2026, 8, 19))
    client = _FakeClient(
        [
            httpx.ConnectError("transient fixture disconnect"),
            _probe(0),
            _page(0, []),
            _page(0, []),
            _page(0, []),
        ]
    )

    batch = _fetch(client)
    budget = cast(dict[str, object], batch.details["request_budget"])

    assert batch.status == "ok"
    assert batch.logical_request_count == 4
    assert batch.physical_attempt_count == 5
    assert batch.request_count == 5
    assert batch.retry_count == 1
    assert budget["max_logical_requests_per_run"] == 301
    assert budget["max_physical_attempts_per_run"] == 602
    assert budget["logical_request_count"] == 4
    assert budget["physical_attempt_count"] == 5
    assert budget["page_101_requested"] is False


def test_v2_2_logical_budget_rejects_before_an_extra_network_attempt() -> None:
    config = _config()
    source = deepcopy(cast(dict[str, object], config.document["sources"])["cninfo"])
    assert isinstance(source, dict)
    source["max_pages_per_partition"] = 1
    client = _FakeClient([_FakeResponse({}) for _index in range(4)])
    transport = news_poll._transport(config, "cninfo", source, client)
    url = cast(str, source["announcements_url"])

    for _index in range(4):
        transport.request("POST", url, data={})
    with pytest.raises(news_poll.NewsSourceError) as error:
        transport.request("POST", url, data={})

    assert error.value.code == "logical_request_budget_exhausted"
    assert error.value.blocked is True
    assert news_poll._v2_2_cninfo_request_budgets(source) == (4, 8)
    assert transport.logical_request_count == 4
    assert transport.physical_attempt_count == 4
    assert len(client.calls) == 4


def test_v2_2_budget_exhaustion_does_not_advance_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_v2_1(date(2026, 8, 19))
    monkeypatch.setattr(
        news_poll,
        "_v2_2_cninfo_request_budgets",
        lambda _source: (1, 2),
    )
    client = _FakeClient([_probe(0)])

    batch = _fetch(client)
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])

    assert batch.status == "unavailable"
    assert batch.failures[0]["code"] == "logical_request_budget_exhausted"
    assert slice_stats["checkpoint_committed"] is False
    assert slice_stats["checkpoint_advanced"] is False
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-19"
    assert len(client.calls) == 1


def test_v2_2_transport_failure_preserves_checkpoint_and_failure_identity() -> None:
    _seed_v2_1(date(2026, 8, 19))
    client = _FakeClient(
        [
            httpx.ConnectError("fixture disconnect one"),
            httpx.ConnectError("fixture disconnect two"),
        ]
    )

    batch = _fetch(client)
    slice_stats = cast(list[dict[str, object]], batch.details["slices"])[0]
    checkpoint = cast(dict[str, object], batch.details["daily_checkpoint"])

    assert batch.status == "unavailable"
    assert batch.failures[0]["code"] == "transport_error"
    assert batch.logical_request_count == 1
    assert batch.physical_attempt_count == 2
    assert batch.retry_count == 1
    assert slice_stats["checkpoint_committed"] is False
    assert slice_stats["checkpoint_advanced"] is False
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-19"


@pytest.mark.parametrize("aggregate_total", [0, 1])
def test_v2_2_run_job_accepts_complete_closed_date_without_observed_high(
    monkeypatch: pytest.MonkeyPatch,
    aggregate_total: int,
) -> None:
    _seed_v2_1(date(2026, 8, 19))
    target = date(2026, 8, 20)
    if aggregate_total:
        row = _row(1, target)
        row["announcementTime"] = None
        outcomes = [
            _probe(1),
            _page(1, [row]),
            _page(0, []),
            _page(0, []),
        ]
    else:
        outcomes = _empty_partition_outcomes()
    batch = _fetch(_FakeClient(outcomes))
    monkeypatch.setattr(news_poll, "_fetch_cninfo", lambda *_args: batch)
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _noncritical_batch("akshare_ths"),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _noncritical_batch("sina_company_news"),
    )
    register(JobSpec(name="news_poll", func=news_poll.run_news_poll, trigger=None))

    record = run_job(
        "news_poll",
        config_path=CONFIG_PATH,
        now=datetime(2026, 8, 23, 1, tzinfo=UTC),
        execution_mode="scheduler",
    )

    assert record.status == "ok"
    source = record.stats["sources"]["cninfo"]
    checkpoint = source["daily_checkpoint"]
    assert checkpoint["closed_date_without_observed_high_reconciled"] is True
    assert checkpoint["closed_date_without_observed_high_shanghai"] == target.isoformat()
    assert checkpoint["closed_date_without_observed_high_aggregate_total"] == aggregate_total
    assert checkpoint["closed_date_without_observed_high_unique_rows"] == aggregate_total
    assert source["slices"][0]["disposition_identity_valid"] is True
    assert isinstance(record.stats["wall_clock_seconds"], float)
    assert record.stats["wall_clock_seconds"] >= 0.0
    assert record.stats["wall_clock_guard"] == {
        "clock": "monotonic_elapsed",
        "reportable_threshold_seconds": 480,
        "scheduler_spacing_seconds": 600,
        "reportable_threshold_exceeded": False,
        "scheduler_spacing_exceeded": False,
        "skipped_slot_absorption_forbidden": True,
    }
    assert record.stats["reportable_events"] == []


def test_v2_2_run_job_fails_closed_on_incomplete_terminal_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_v2_1(date(2026, 8, 19))
    candidate = _candidate(
        identifier=1,
        url="https://static.cninfo.com.cn/finalpage/incomplete-terminal.PDF",
    )
    batch = _successful_batch([candidate], slice_date=date(2026, 8, 20))
    item = cast(list[dict[str, object]], batch.details["slices"])[0]
    item.pop("partition_completion")
    monkeypatch.setattr(news_poll, "_fetch_cninfo", lambda *_args: batch)
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _noncritical_batch("akshare_ths"),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _noncritical_batch("sina_company_news"),
    )
    register(JobSpec(name="news_poll", func=news_poll.run_news_poll, trigger=None))

    record = run_job(
        "news_poll",
        config_path=CONFIG_PATH,
        now=datetime(2026, 8, 23, 1, tzinfo=UTC),
        execution_mode="scheduler",
    )

    assert record.status == "failed"
    assert record.stats["terminal_diagnostics"] == {
        "code": "cninfo_invalid_terminal_state",
        "source": "cninfo",
        "constraint": "critical_unknown",
        "recoverable": False,
        "retry_suppressed": False,
    }
    assert record.stats["critical_failures"] == ["cninfo"]
    assert isinstance(record.stats["wall_clock_seconds"], float)
    assert record.stats["wall_clock_seconds"] >= 0.0
    assert record.stats["wall_clock_guard"]["reportable_threshold_seconds"] == 480
    assert record.stats["wall_clock_guard"]["skipped_slot_absorption_forbidden"] is True


def test_v2_2_run_job_marks_capacity_catchup_pit_dedupe_and_zero_trading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_v2_1(date(2026, 8, 19))
    first = _candidate(
        identifier=1,
        url="https://static.cninfo.com.cn/finalpage/capacity-one.PDF",
    )
    same_url = _candidate(identifier=1, url=first.url)
    same_content = _candidate(
        identifier=2,
        url="https://static.cninfo.com.cn/finalpage/capacity-two.PDF",
    )
    batches = [
        _successful_batch([first], slice_date=date(2026, 8, 20)),
        _successful_batch(
            [same_url, same_content],
            slice_date=date(2026, 8, 21),
            poll_started_at=datetime(2026, 8, 23, 1, 10, tzinfo=UTC),
        ),
    ]
    monkeypatch.setattr(news_poll, "_fetch_cninfo", lambda *_args: batches.pop(0))
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _noncritical_batch("akshare_ths"),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _noncritical_batch("sina_company_news"),
    )
    register(JobSpec(name="news_poll", func=news_poll.run_news_poll, trigger=None))
    with get_session() as session:
        proposals_before = list(
            session.scalars(
                select(TradeProposalRecord.proposal_id).order_by(TradeProposalRecord.proposal_id)
            ).all()
        )
        orders_before = list(session.scalars(select(BrokerOrder.id).order_by(BrokerOrder.id)).all())

    first_record = run_job(
        "news_poll",
        config_path=CONFIG_PATH,
        now=datetime(2026, 8, 23, 1, tzinfo=UTC),
        execution_mode="scheduler",
    )

    assert first_record.status == "ok"
    assert first_record.stats["run_mode"] == "coverage_gap_catchup"
    assert first_record.stats["coverage_gap"] is True
    assert first_record.stats["coverage_gap_details"]["reason"] == (
        "cninfo_capacity_checkpoint_lag"
    )
    assert first_record.stats["sources"]["cninfo"]["inserted"] == 1
    assert (
        first_record.stats["catchup"]["counts_all_sources"]["preceded_by_coverage_gap_inserted"]
        == 1
    )
    assert first_record.stats["pit"] == {
        "available_time_policy": "write_locked_immediately_before_flush_utc",
        "available_time_coverage": 1.0,
        "decision_visibility_operator": "<",
        "published_at_never_substitutes_available_time": True,
    }
    assert first_record.stats["safety_unchanged"] is True
    with get_session() as session:
        stored = session.scalar(select(NewsItem).where(NewsItem.url == first.url))
    assert stored is not None
    ingestion = stored.raw_payload["_alphapilot_ingestion"]
    assert stored.raw_payload["_alphapilot_cninfo_partition"] == "sz"
    assert ingestion["run_mode"] == "coverage_gap_catchup"
    assert ingestion["preceded_by_coverage_gap"] is True
    assigned = datetime.fromisoformat(cast(str, ingestion["available_time_assigned_at_utc"]))
    assert stored.available_time == assigned.replace(tzinfo=None)
    assert stored.published_at != stored.available_time
    first_available_time = stored.available_time
    first_ingestion = deepcopy(ingestion)

    replay = run_job(
        "news_poll",
        config_path=CONFIG_PATH,
        now=datetime(2026, 8, 23, 1, 10, tzinfo=UTC),
        execution_mode="scheduler",
    )

    assert replay.status == "ok"
    assert replay.stats["sources"]["cninfo"]["inserted"] == 0
    assert replay.stats["sources"]["cninfo"]["duplicate_url"] == 1
    assert replay.stats["sources"]["cninfo"]["duplicate_content_hash"] == 1
    assert replay.stats["sources"]["cninfo"]["slices"][0]["disposition_identity_valid"] is True
    with get_session() as session:
        items = session.scalars(select(NewsItem).order_by(NewsItem.id)).all()
        proposals_after = list(
            session.scalars(
                select(TradeProposalRecord.proposal_id).order_by(TradeProposalRecord.proposal_id)
            ).all()
        )
        orders_after = list(session.scalars(select(BrokerOrder.id).order_by(BrokerOrder.id)).all())
    assert len(items) == 1
    assert items[0].available_time == first_available_time
    assert items[0].raw_payload["_alphapilot_ingestion"] == first_ingestion
    assert proposals_after == proposals_before
    assert orders_after == orders_before
