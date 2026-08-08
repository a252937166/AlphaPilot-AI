from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
import yaml
from sqlalchemy import delete, select

from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import BrokerOrder, JobRun, NewsItem, TradeProposalRecord
from alphapilot.jobs import news_poll
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register, run_job

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2_1.yaml"
RECEIPT_PATH = PROJECT_DIR / "config/p4_news_poll_v2_1.preregistration.json"
PROBE_PATH = PROJECT_DIR / "config/p4_news_poll_v2_1.probe.json"


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> object:
        return self._payload

    def read(self) -> None:
        return None


class _FakeClient:
    def __init__(self, outcomes: Sequence[_FakeResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_rows() -> Iterator[None]:
    with get_session() as session:
        session.execute(delete(NewsItem))
        session.execute(delete(JobRun).where(JobRun.job_name == "news_poll"))
    yield
    with get_session() as session:
        session.execute(delete(NewsItem))
        session.execute(delete(JobRun).where(JobRun.job_name == "news_poll"))


def _config(*, max_dates: int = 2, max_pages: int = 80) -> news_poll.NewsPollConfig:
    loaded = news_poll.load_news_poll_config(CONFIG_PATH)
    document = deepcopy(loaded.document)
    source = document["sources"]["cninfo"]
    source["min_interval_seconds"] = 0
    source["retry_backoff_seconds"] = [0]
    source["max_dates_per_run"] = max_dates
    source["max_pages_per_day"] = max_pages
    source["max_logical_requests_per_run"] = max_dates * max_pages
    source["max_physical_attempts_per_run"] = max_dates * max_pages * 2
    return news_poll.NewsPollConfig(
        path=loaded.path,
        sha256=loaded.sha256,
        document=document,
    )


def _row(index: int, published_at: datetime) -> dict[str, object]:
    return {
        "secCode": f"{index % 1_000_000:06d}",
        "announcementId": str(1_300_000_000 + index),
        "announcementTitle": f"日切片公告{index}",
        "adjunctUrl": f"finalpage/v2-1-{index}.PDF",
        "announcementTime": int(published_at.timestamp() * 1000),
    }


def _response(rows: list[dict[str, object]], *, has_more: bool) -> _FakeResponse:
    return _FakeResponse({"announcements": rows, "hasMore": has_more})


def _call_data(call: tuple[str, str, dict[str, object]]) -> dict[str, object]:
    data = call[2].get("data")
    assert isinstance(data, dict)
    return cast(dict[str, object], data)


def _seed_v1(watermark: datetime) -> None:
    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status="ok",
                stats={
                    "config_version": "p4.1-news-poll-v1",
                    "config_sha256": news_poll.EXPECTED_CONFIG_SHA256,
                    "sources": {"cninfo": {"watermark_after": watermark.isoformat()}},
                },
            )
        )


def _seed_v2_1(checkpoint_date: date, observed: datetime) -> None:
    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status="ok",
                stats={
                    "config_version": "p4.1-news-poll-v2.1",
                    "config_sha256": news_poll.EXPECTED_V2_1_CONFIG_SHA256,
                    "sources": {
                        "cninfo": {
                            "daily_checkpoint": {
                                "checkpoint_committed": True,
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


def test_v2_1_self_contained_config_probe_and_receipt_are_hash_bound() -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_bytes())
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    probe = json.loads(PROBE_PATH.read_text(encoding="utf-8"))
    digest = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()

    assert raw["schema_version"] == "p4.1-news-poll-v2.1"
    assert "extends_config" not in raw
    assert raw["sources"]["cninfo"]["columns"] == ["szse"]
    assert "max_pages_per_column" not in raw["sources"]["cninfo"]
    assert "cninfo_required_column_stats" not in raw["jobrun_contract"]
    assert raw["sources"]["cninfo"]["max_pages_per_day"] == 80
    assert raw["sources"]["cninfo"]["flood_capacity_contract"][
        "configured_rows_per_day"
    ] == 2400
    assert receipt["config_sha256"] == digest == news_poll.EXPECTED_V2_1_CONFIG_SHA256
    assert receipt["superseded_v2_config_sha256"] == news_poll.EXPECTED_V2_CONFIG_SHA256
    assert receipt["activation_gates"] == {
        "code_ready": True,
        "scheduler_activated": False,
        "initial_backlog_migration_complete": False,
        "standard_incremental_validation_complete": False,
    }
    assert receipt["controlled_probe_sha256"] == hashlib.sha256(
        PROBE_PATH.read_bytes()
    ).hexdigest()
    assert probe["network_policy"] == {
        "strict_tls": True,
        "automatic_retries": 0,
        "database_writes": 0,
        "aggregate_query_count": 2,
        "daily_query_count": 8,
        "total_query_count": 10,
    }
    assert [item["szse_total_announcement"] for item in probe["daily_probe"]] == [
        477,
        1533,
        1129,
        1097,
    ]


def test_v2_1_legacy_watermark_starts_initial_daily_migration() -> None:
    watermark = datetime(2026, 8, 3, 13, 37, 29, tzinfo=UTC)
    _seed_v1(watermark)
    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")
    dates = news_poll._v2_1_slice_dates(
        seed,
        poll_started_at_utc=datetime(2026, 8, 6, 3, tzinfo=UTC),
        max_dates_per_run=2,
    )

    assert seed.lineage == "legacy_v1_global_watermark"
    assert dates == [date(2026, 8, 3), date(2026, 8, 4)]


def test_v2_1_daily_checkpoint_accepts_observed_high_after_closed_date() -> None:
    observed = datetime(2026, 8, 5, 1, 50, tzinfo=UTC)
    _seed_v2_1(date(2026, 8, 4), observed)

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")

    assert seed.lineage == "v2.1_daily_checkpoint"
    assert seed.checkpoint_date_shanghai == date(2026, 8, 4)
    assert seed.newest_observed_at_utc == observed
    assert news_poll._v2_1_slice_dates(
        seed,
        poll_started_at_utc=datetime(2026, 8, 5, 2, 10, tzinfo=UTC),
        max_dates_per_run=2,
    ) == [date(2026, 8, 5)]
    assert news_poll._v2_1_slice_dates(
        seed,
        poll_started_at_utc=datetime(2026, 8, 6, 2, 10, tzinfo=UTC),
        max_dates_per_run=2,
    ) == [date(2026, 8, 5), date(2026, 8, 6)]


def test_v2_1_peak_1533_row_closed_day_fits_before_eighty_page_cap() -> None:
    _seed_v2_1(date(2026, 8, 3), datetime(2026, 8, 3, 13, 37, tzinfo=UTC))
    published = datetime(2026, 8, 4, 8, tzinfo=UTC)
    rows = [_row(index, published - timedelta(milliseconds=index)) for index in range(1533)]
    outcomes: list[_FakeResponse | Exception] = [
        _response(rows[offset : offset + 30], has_more=offset + 30 < len(rows))
        for offset in range(0, len(rows), 30)
    ]
    client = _FakeClient(outcomes)

    batch = news_poll._fetch_cninfo_v2_1(
        _config(max_dates=1),
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source: client,
    )

    assert batch.status == "ok"
    assert len(batch.candidates) == 1533
    assert len(client.calls) == 52
    assert {_call_data(call)["column"] for call in client.calls} == {"szse"}
    slice_stats = batch.details["slices"][0]
    assert slice_stats["mode"] == "closed_date_reconciliation"
    assert slice_stats["page_count"] == 52
    assert slice_stats["pagination_complete"] is True
    assert slice_stats["checkpoint_committed"] is True
    assert batch.details["daily_checkpoint"][
        "verified_checkpoint_date_shanghai_after"
    ] == "2026-08-04"


def test_v2_1_current_incremental_stops_at_floor_with_one_canonical_request() -> None:
    prior_observed = datetime(2026, 8, 5, 1, tzinfo=UTC)
    _seed_v2_1(date(2026, 8, 4), prior_observed)
    rows = [
        _row(1, datetime(2026, 8, 5, 1, 50, tzinfo=UTC)),
        _row(2, datetime(2026, 8, 5, 0, 20, tzinfo=UTC)),
    ]
    client = _FakeClient([_response(rows, has_more=True)])

    batch = news_poll._fetch_cninfo_v2_1(
        _config(max_dates=1),
        datetime(2026, 8, 5, 2, tzinfo=UTC),
        lambda _source: client,
    )

    assert len(client.calls) == 1
    data = _call_data(client.calls[0])
    assert data["column"] == "szse"
    assert data["seDate"] == "2026-08-05~2026-08-05"
    assert batch.details["slices"][0]["mode"] == "current_date_incremental"
    assert batch.details["slices"][0]["incremental_floor_utc"] == (
        prior_observed - timedelta(minutes=30)
    ).isoformat()
    assert batch.details["slices"][0]["coverage_proven"] is True
    checkpoint = batch.details["daily_checkpoint"]
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-04"
    assert checkpoint["newest_observed_at_utc"] == datetime(
        2026, 8, 5, 1, 50, tzinfo=UTC
    ).isoformat()


def test_v2_1_same_day_second_incremental_uses_latest_committed_observed_high() -> None:
    prior_observed = datetime(2026, 8, 5, 1, 50, tzinfo=UTC)
    _seed_v2_1(date(2026, 8, 4), prior_observed)
    rows = [
        _row(1, datetime(2026, 8, 5, 2, 5, tzinfo=UTC)),
        _row(2, datetime(2026, 8, 5, 1, 15, tzinfo=UTC)),
    ]
    client = _FakeClient([_response(rows, has_more=True)])

    batch = news_poll._fetch_cninfo_v2_1(
        _config(max_dates=1),
        datetime(2026, 8, 5, 2, 10, tzinfo=UTC),
        lambda _source: client,
    )

    expected_floor = prior_observed - timedelta(minutes=30)
    assert batch.status == "ok"
    assert batch.details["slices"][0]["incremental_floor_utc"] == (
        expected_floor.isoformat()
    )
    assert batch.details["daily_checkpoint"]["newest_observed_at_utc"] == datetime(
        2026, 8, 5, 2, 5, tzinfo=UTC
    ).isoformat()


def test_v2_1_later_network_failure_keeps_stats_but_failed_seed_is_untrusted() -> None:
    legacy = datetime(2026, 8, 3, 13, 37, 29, tzinfo=UTC)
    completed_newest = datetime(2026, 8, 3, 14, tzinfo=UTC)
    _seed_v1(legacy)
    client = _FakeClient(
        [
            _response([_row(1, completed_newest)], has_more=False),
            httpx.ConnectError("fixture disconnect"),
            httpx.ConnectError("fixture disconnect"),
        ]
    )

    batch = news_poll._fetch_cninfo_v2_1(
        _config(),
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source: client,
    )

    assert batch.status == "unavailable"
    assert [item["date_shanghai"] for item in batch.details["slices"]] == [
        "2026-08-03",
        "2026-08-04",
    ]
    checkpoint = batch.details["daily_checkpoint"]
    assert checkpoint["verified_checkpoint_date_shanghai_after"] == "2026-08-03"
    assert checkpoint["newest_observed_at_utc"] == completed_newest.isoformat()
    assert checkpoint["partial_checkpoint"] is True
    assert batch.details["slices"][1]["checkpoint_committed"] is False
    assert batch.details["slices"][1]["failure"]["code"] == "transport_error"
    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status="failed",
                stats={
                    "config_version": "p4.1-news-poll-v2.1",
                    "config_sha256": news_poll.EXPECTED_V2_1_CONFIG_SHA256,
                    "sources": {"cninfo": batch.details},
                },
            )
        )
    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")
    assert seed.lineage == "legacy_v1_global_watermark"
    assert seed.legacy_watermark_utc == legacy


def test_v2_1_later_page_cap_degraded_checkpoint_can_seed_next_run() -> None:
    legacy = datetime(2026, 8, 3, 13, 37, 29, tzinfo=UTC)
    completed_newest = datetime(2026, 8, 3, 14, tzinfo=UTC)
    _seed_v1(legacy)
    full_page = [
        _row(index, datetime(2026, 8, 4, 8, tzinfo=UTC)) for index in range(30)
    ]
    client = _FakeClient(
        [
            _response([_row(100, completed_newest)], has_more=False),
            _response(full_page, has_more=True),
            _response(full_page, has_more=True),
        ]
    )

    batch = news_poll._fetch_cninfo_v2_1(
        _config(max_pages=2),
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source: client,
    )
    assert batch.status == "degraded"
    assert batch.details["slices"][0]["checkpoint_committed"] is True
    assert batch.details["slices"][1]["page_cap_hit"] is True
    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status="degraded",
                stats={
                    "config_version": "p4.1-news-poll-v2.1",
                    "config_sha256": news_poll.EXPECTED_V2_1_CONFIG_SHA256,
                    "sources": {"cninfo": batch.details},
                },
            )
        )

    seed = news_poll._last_committed_daily_checkpoint(_config(), "cninfo")
    assert seed.lineage == "v2.1_daily_checkpoint"
    assert seed.checkpoint_date_shanghai == date(2026, 8, 3)
    assert seed.newest_observed_at_utc == completed_newest


def test_v2_1_page_cap_never_advances_checkpoint() -> None:
    prior = datetime(2026, 8, 3, 13, 37, tzinfo=UTC)
    _seed_v2_1(date(2026, 8, 3), prior)
    full_page = [_row(index, datetime(2026, 8, 4, 8, tzinfo=UTC)) for index in range(30)]
    client = _FakeClient(
        [_response(full_page, has_more=True), _response(full_page, has_more=True)]
    )

    batch = news_poll._fetch_cninfo_v2_1(
        _config(max_dates=1, max_pages=2),
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source: client,
    )

    assert batch.status == "degraded"
    assert batch.details["slices"][0]["page_cap_hit"] is True
    assert batch.details["slices"][0]["checkpoint_committed"] is False
    assert batch.details["daily_checkpoint"][
        "verified_checkpoint_date_shanghai_after"
    ] == "2026-08-03"
    assert batch.details["daily_checkpoint"]["newest_observed_at_utc"] == prior.isoformat()


@pytest.mark.parametrize("cross_page", [False, True])
def test_v2_1_descending_order_violation_fails_without_checkpoint(
    cross_page: bool,
) -> None:
    prior = datetime(2026, 8, 3, 13, 37, tzinfo=UTC)
    _seed_v2_1(date(2026, 8, 3), prior)
    if cross_page:
        first_page = [
            _row(
                index,
                datetime(2026, 8, 4, 9, tzinfo=UTC) - timedelta(minutes=index),
            )
            for index in range(30)
        ]
        outcomes = [
            _response(first_page, has_more=True),
            _response(
                [_row(31, datetime(2026, 8, 4, 8, 45, tzinfo=UTC))],
                has_more=False,
            ),
        ]
    else:
        outcomes = [
            _response(
                [
                    _row(1, datetime(2026, 8, 4, 8, tzinfo=UTC)),
                    _row(2, datetime(2026, 8, 4, 9, tzinfo=UTC)),
                ],
                has_more=False,
            )
        ]
    client = _FakeClient(outcomes)

    batch = news_poll._fetch_cninfo_v2_1(
        _config(max_dates=1),
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source: client,
    )

    assert batch.status == "unavailable"
    assert batch.details["slices"][0]["checkpoint_committed"] is False
    assert batch.details["daily_checkpoint"][
        "verified_checkpoint_date_shanghai_after"
    ] == "2026-08-03"
    assert batch.failures[0]["code"] == "cninfo_order_contract_violated"


def test_v2_1_rejects_nonnull_timestamp_outside_requested_cst_slice() -> None:
    prior = datetime(2026, 8, 3, 13, 37, tzinfo=UTC)
    _seed_v2_1(date(2026, 8, 3), prior)
    # Requested slice is 2026-08-04 CST; this row is 2026-08-05 CST.
    client = _FakeClient(
        [_response([_row(1, datetime(2026, 8, 5, 8, tzinfo=UTC))], has_more=False)]
    )

    batch = news_poll._fetch_cninfo_v2_1(
        _config(max_dates=1),
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source: client,
    )

    assert batch.status == "unavailable"
    assert batch.candidates == []
    assert batch.details["slices"][0]["checkpoint_committed"] is False
    assert batch.failures[0]["code"] == "cninfo_slice_date_contract_violated"


def test_v2_1_null_published_at_is_persistable_but_not_order_or_floor_evidence() -> None:
    prior = datetime(2026, 8, 3, 13, 37, tzinfo=UTC)
    _seed_v2_1(date(2026, 8, 3), prior)
    raw = _row(1, datetime(2026, 8, 4, 8, tzinfo=UTC))
    raw["announcementTime"] = None
    client = _FakeClient([_response([raw], has_more=False)])

    batch = news_poll._fetch_cninfo_v2_1(
        _config(max_dates=1),
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source: client,
    )

    assert batch.status == "ok"
    assert len(batch.candidates) == 1
    assert batch.candidates[0].published_at is None
    assert batch.details["slices"][0]["newest_observed_at_utc"] is None
    assert batch.details["slices"][0]["pagination_complete"] is True
    assert batch.details["slices"][0]["checkpoint_committed"] is True


def test_v2_1_replay_keeps_first_available_time_and_dual_key_dedupe() -> None:
    candidate = news_poll.NewsCandidate(
        source="cninfo",
        symbol="000001",
        title="同一公告",
        url="https://static.cninfo.com.cn/finalpage/idempotent.PDF",
        published_at=datetime(2026, 8, 4, 8, tzinfo=UTC),
        content="",
        raw_payload={"fixture": True},
    )
    fetched = datetime.now(UTC) - timedelta(seconds=1)
    first = news_poll._persist_candidates([candidate], fetched, job_run_id=1)
    with get_session() as session:
        first_available = session.scalar(select(NewsItem.available_time))
    replay = news_poll._persist_candidates([candidate], fetched, job_run_id=2)
    with get_session() as session:
        after_available = session.scalar(select(NewsItem.available_time))
        count = len(session.scalars(select(NewsItem)).all())

    assert first["inserted"] == 1
    assert replay["inserted"] == 0
    assert replay["duplicate_url"] == 1
    assert replay["duplicate_content_hash"] == 0
    assert count == 1
    assert after_available == first_available


def test_v2_1_records_dual_key_dispositions_per_request_slice() -> None:
    day_one = datetime(2026, 8, 5, 8, tzinfo=UTC)
    candidates = [
        news_poll.NewsCandidate(
            source="cninfo",
            symbol="000001",
            title="第一日公告",
            url="https://static.cninfo.com.cn/finalpage/day-one.PDF",
            published_at=None,
            content="",
            raw_payload={"fixture": 1},
        ),
        news_poll.NewsCandidate(
            source="cninfo",
            symbol="000001",
            title="第一日公告重放",
            url="https://static.cninfo.com.cn/finalpage/day-one.PDF",
            published_at=day_one,
            content="different",
            raw_payload={"fixture": 2},
        ),
        news_poll.NewsCandidate(
            source="cninfo",
            symbol="000002",
            title="第二日公告",
            url="https://static.cninfo.com.cn/finalpage/day-two-a.PDF",
            published_at=datetime(2026, 8, 6, 8, tzinfo=UTC),
            content="same",
            raw_payload={"fixture": 3},
        ),
        news_poll.NewsCandidate(
            source="cninfo",
            symbol="000002",
            title="第二日公告",
            url="https://static.cninfo.com.cn/finalpage/day-two-b.PDF",
            published_at=datetime(2026, 8, 6, 8, tzinfo=UTC),
            content="same",
            raw_payload={"fixture": 4},
        ),
    ]
    batch = news_poll.SourceBatch(
        source_id="cninfo",
        candidates=candidates,
        details={
            "slices": [
                {"date_shanghai": "2026-08-05", "fetched": 2},
                {"date_shanghai": "2026-08-06", "fetched": 2},
            ]
        },
    )
    persistence = news_poll._persist_candidates(
        candidates,
        datetime.now(UTC) - timedelta(seconds=1),
        job_run_id=1,
    )

    stats = news_poll._batch_stats(batch, persistence)

    assert "_candidate_dispositions" not in stats
    assert stats["slices"] == [
        {
            "date_shanghai": "2026-08-05",
            "fetched": 2,
            "inserted": 1,
            "duplicate_url": 1,
            "duplicate_content_hash": 0,
            "filtered": 0,
            "disposition_total": 2,
            "disposition_identity_valid": True,
        },
        {
            "date_shanghai": "2026-08-06",
            "fetched": 2,
            "inserted": 1,
            "duplicate_url": 0,
            "duplicate_content_hash": 1,
            "filtered": 0,
            "disposition_total": 2,
            "disposition_identity_valid": True,
        },
    ]


def test_v2_1_slice_disposition_ranges_fail_closed() -> None:
    candidate = news_poll.NewsCandidate(
        source="cninfo",
        symbol="000001",
        title="公告",
        url="https://static.cninfo.com.cn/finalpage/one.PDF",
        published_at=None,
        content="",
        raw_payload={"fixture": True},
    )
    batch = news_poll.SourceBatch(
        source_id="cninfo",
        candidates=[candidate],
        details={"slices": [{"date_shanghai": "2026-08-05", "fetched": 0}]},
    )

    with pytest.raises(RuntimeError, match="ranges do not close"):
        news_poll._batch_stats(
            batch,
            {
                "fetched": 1,
                "inserted": 1,
                "duplicate_url": 0,
                "duplicate_content_hash": 0,
                "_candidate_dispositions": ["inserted"],
            },
        )


def test_v2_1_slice_dispositions_must_match_source_aggregate() -> None:
    candidate = news_poll.NewsCandidate(
        source="cninfo",
        symbol="000001",
        title="公告",
        url="https://static.cninfo.com.cn/finalpage/one.PDF",
        published_at=None,
        content="",
        raw_payload={"fixture": True},
    )
    batch = news_poll.SourceBatch(
        source_id="cninfo",
        candidates=[candidate],
        details={"slices": [{"date_shanghai": "2026-08-05", "fetched": 1}]},
    )

    with pytest.raises(RuntimeError, match="inserted does not match"):
        news_poll._batch_stats(
            batch,
            {
                "fetched": 1,
                "filtered": 0,
                "inserted": 0,
                "duplicate_url": 0,
                "duplicate_content_hash": 0,
                "_candidate_dispositions": ["inserted"],
            },
        )


def test_news_poll_safety_snapshot_blocks_manual_paper_and_futu_trade() -> None:
    unsafe = get_settings().model_copy(
        update={"paper_trading_enabled": True, "futu_enable_trade": True}
    )
    snapshot = news_poll._safety_snapshot(unsafe)
    issues = news_poll._safety_issues(snapshot)

    assert snapshot["settings"]["paper_trading_enabled"] is True
    assert snapshot["settings"]["futu_enable_trade"] is True
    assert any("paper_trading_enabled" in issue for issue in issues)
    assert any("futu_enable_trade" in issue for issue in issues)


def _write_manual_authorization(
    path: Path,
    *,
    mode: news_poll.V2ExecutionMode,
    migration_complete: bool,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": (
                    "p4.1-news-poll-v2.1-manual-authorization-v1"
                ),
                "authorization_id": "fixture-review-approval",
                "config_sha256": news_poll.EXPECTED_V2_1_CONFIG_SHA256,
                "execution_mode": mode,
                "authorized": True,
                "network_execution_authorized": True,
                "scheduler_activated": False,
                "authorized_at_utc": "2026-08-06T12:00:00Z",
                "authorized_by": "independent-review-fixture",
                "initial_backlog_migration_complete": migration_complete,
                "standard_incremental_validation_complete": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [("ok", "ok"), ("page_cap", "degraded"), ("transport", "failed")],
)
def test_v2_1_manual_migration_run_job_persists_candidates_with_safe_run_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    expected_status: str,
) -> None:
    _seed_v1(datetime(2026, 8, 3, 13, 37, 29, tzinfo=UTC))
    first = _response(
        [_row(1, datetime(2026, 8, 3, 10, tzinfo=UTC))],
        has_more=False,
    )
    if case == "ok":
        outcomes: list[_FakeResponse | Exception] = [
            first,
            _response(
                [_row(2, datetime(2026, 8, 4, 10, tzinfo=UTC))],
                has_more=False,
            ),
        ]
    elif case == "page_cap":
        page_one = [
            _row(
                100 + index,
                datetime(2026, 8, 4, 10, tzinfo=UTC) - timedelta(seconds=index),
            )
            for index in range(30)
        ]
        page_two = [
            _row(
                200 + index,
                datetime(2026, 8, 4, 9, tzinfo=UTC) - timedelta(seconds=index),
            )
            for index in range(30)
        ]
        outcomes = [
            first,
            _response(page_one, has_more=True),
            _response(page_two, has_more=True),
        ]
    else:
        outcomes = [
            first,
            httpx.ConnectError("fixture disconnect"),
            httpx.ConnectError("fixture disconnect"),
        ]
    client = _FakeClient(outcomes)
    fixture_config = _config(max_dates=2, max_pages=2)
    monkeypatch.setattr(
        news_poll,
        "load_news_poll_config",
        lambda _path=news_poll.DEFAULT_CONFIG_PATH: fixture_config,
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: news_poll.SourceBatch(source_id="akshare_ths", status="ok"),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: news_poll.SourceBatch(
            source_id="sina_company_news", status="ok"
        ),
    )
    authorization = tmp_path / "manual-migration-authorization.json"
    _write_manual_authorization(
        authorization,
        mode="initial_backlog_migration",
        migration_complete=False,
    )
    with get_session() as session:
        proposal_ids_before = list(
            session.scalars(select(TradeProposalRecord.proposal_id)).all()
        )
        order_ids_before = list(session.scalars(select(BrokerOrder.id)).all())
    register(JobSpec(name="news_poll", func=news_poll.run_news_poll, trigger=None))

    record = run_job(
        "news_poll",
        config_path=CONFIG_PATH,
        now=datetime(2026, 8, 5, 1, tzinfo=UTC),
        http_client_factory=lambda _source: client,
        execution_mode="initial_backlog_migration",
        authorization_receipt_path=authorization,
    )

    assert record.status == expected_status
    assert record.stats["execution_mode"] == "initial_backlog_migration"
    assert record.stats["run_mode"] == "coverage_gap_catchup"
    assert record.stats["coverage_gap"] is True
    assert record.stats["sources"]["cninfo"]["inserted"] > 0
    with get_session() as session:
        items = session.scalars(select(NewsItem).order_by(NewsItem.id)).all()
        proposal_ids_after = list(
            session.scalars(select(TradeProposalRecord.proposal_id)).all()
        )
        order_ids_after = list(session.scalars(select(BrokerOrder.id)).all())
    assert items
    assert all(item.available_time is not None for item in items)
    assert all(
        item.raw_payload["_alphapilot_ingestion"]["run_mode"]
        == "coverage_gap_catchup"
        for item in items
    )
    assert proposal_ids_after == proposal_ids_before
    assert order_ids_after == order_ids_before


def test_v2_1_scheduler_stays_closed_while_manual_migration_is_authorizable(
    tmp_path: Path,
) -> None:
    config = news_poll.load_news_poll_config(CONFIG_PATH)
    with pytest.raises(JobExecutionError) as scheduler_error:
        news_poll._v2_execution_authorization(
            config,
            execution_mode="scheduler",
            authorization_receipt_path=None,
        )
    assert scheduler_error.value.stats["implementation_gate"] == (
        "v2_1_scheduler_not_activated"
    )
    assert scheduler_error.value.stats["network_attempted"] is False

    receipt = tmp_path / "manual-migration-authorization.json"
    _write_manual_authorization(
        receipt,
        mode="initial_backlog_migration",
        migration_complete=False,
    )
    authorization = news_poll._v2_execution_authorization(
        config,
        execution_mode="initial_backlog_migration",
        authorization_receipt_path=receipt,
    )
    assert authorization["execution_mode"] == "initial_backlog_migration"
    assert authorization["scheduler_activated"] is False
    assert authorization["authorization_receipt_sha256"] == hashlib.sha256(
        receipt.read_bytes()
    ).hexdigest()


def test_v2_1_incremental_validation_requires_migration_complete_receipt(
    tmp_path: Path,
) -> None:
    config = news_poll.load_news_poll_config(CONFIG_PATH)
    receipt = tmp_path / "manual-incremental-authorization.json"
    _write_manual_authorization(
        receipt,
        mode="standard_incremental_validation",
        migration_complete=False,
    )

    with pytest.raises(JobExecutionError) as caught:
        news_poll._v2_execution_authorization(
            config,
            execution_mode="standard_incremental_validation",
            authorization_receipt_path=receipt,
        )

    assert caught.value.stats["implementation_gate"] == (
        "v2_1_manual_authorization_invalid"
    )
    assert caught.value.stats["network_attempted"] is False


@pytest.mark.parametrize("invalid_kind", ["duplicate_authorized", "naive_time"])
def test_v2_1_manual_authorization_rejects_ambiguous_json_before_network(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    receipt = tmp_path / "invalid-manual-authorization.json"
    _write_manual_authorization(
        receipt,
        mode="initial_backlog_migration",
        migration_complete=False,
    )
    if invalid_kind == "duplicate_authorized":
        payload = receipt.read_text(encoding="utf-8").replace(
            '"authorized": true',
            '"authorized": false, "authorized": true',
            1,
        )
        receipt.write_text(payload, encoding="utf-8")
    else:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["authorized_at_utc"] = "2026-08-06T12:00:00"
        receipt.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    calls = {"http": 0}

    def http_factory(_source: str) -> _FakeClient:
        calls["http"] += 1
        return _FakeClient([])

    with pytest.raises(JobExecutionError) as caught:
        news_poll.run_news_poll(
            config_path=CONFIG_PATH,
            execution_mode="initial_backlog_migration",
            authorization_receipt_path=receipt,
            http_client_factory=http_factory,
        )

    assert calls["http"] == 0
    assert caught.value.stats["implementation_gate"] == (
        "v2_1_manual_authorization_invalid"
    )
    assert caught.value.stats["network_attempted"] is False
    assert caught.value.stats["fail_closed_before_job_context"] is True


def test_v2_1_monday_catchup_uses_canonical_daily_incremental_floor() -> None:
    started = datetime(2026, 8, 10, 1, 50, tzinfo=UTC)
    floor = datetime(2026, 8, 10, 0, 55, tzinfo=UTC)
    stats = news_poll._v2_catchup_stats(
        started_at=started,
        source_results={
            "cninfo": {
                "canonical_column": "szse",
                "slices": [
                    {
                        "date_shanghai": "2026-08-10",
                        "mode": "current_date_incremental",
                        "incremental_floor_utc": floor.isoformat(),
                    }
                ],
                "fetched": 4,
                "inserted": 2,
                "duplicate_url": 2,
                "duplicate_content_hash": 0,
                "preceded_by_coverage_gap_inserted": 2,
            }
        },
    )

    assert stats["range_basis"] == (
        "canonical_daily_verified_observed_minus_overlap_to_actual_poll"
    )
    assert stats["cninfo_column_ranges"] == {
        "szse": {
            "start_utc": floor.isoformat(),
            "end_utc": started.isoformat(),
            "span_seconds": int((started - floor).total_seconds()),
        }
    }
