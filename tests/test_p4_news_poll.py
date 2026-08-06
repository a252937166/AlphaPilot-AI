from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import Event, Thread
from time import sleep

import pytest
from apscheduler.triggers.base import BaseTrigger
from sqlalchemy import delete, func, select

from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import JobRun, NewsItem
from alphapilot.jobs import news_poll
from alphapilot.jobs.registry import JOBS, JobExecutionError, JobSpec, register, run_job


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: object = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        return self._payload

    def read(self) -> None:
        return None


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_news_rows() -> Iterator[None]:
    with get_session() as session:
        session.execute(delete(NewsItem))
        session.execute(delete(JobRun).where(JobRun.job_name == "news_poll"))
    JOBS.pop("news_poll", None)
    yield
    JOBS.pop("news_poll", None)
    with get_session() as session:
        session.execute(delete(NewsItem))
        session.execute(delete(JobRun).where(JobRun.job_name == "news_poll"))


def _candidate(
    *,
    url: str,
    title: str = "全市场测试资讯",
    content: str = "同一正文",
) -> news_poll.NewsCandidate:
    return news_poll.NewsCandidate(
        source="cninfo",
        symbol=None,
        title=title,
        url=url,
        published_at=None,
        content=content,
        raw_payload={"fixture": True},
    )


def _ok_batch(
    source_id: str,
    candidates: list[news_poll.NewsCandidate] | None = None,
) -> news_poll.SourceBatch:
    return news_poll.SourceBatch(
        source_id=source_id,
        status="ok",
        candidates=candidates or [],
        request_count=1,
    )


def _install_fake_sources(
    monkeypatch: pytest.MonkeyPatch,
    cninfo_batches: list[news_poll.SourceBatch],
) -> None:
    def next_cninfo(*_args: object) -> news_poll.SourceBatch:
        return cninfo_batches.pop(0)

    monkeypatch.setattr(news_poll, "_fetch_cninfo", next_cninfo)
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _ok_batch("akshare_ths"),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _ok_batch("sina_company_news"),
    )


def test_config_is_hash_locked_and_keeps_excluded_sources_disabled(tmp_path: Path) -> None:
    config = news_poll.load_news_poll_config()

    assert config.sha256 == news_poll.EXPECTED_CONFIG_SHA256
    assert config.document["sources"]["cninfo"]["verify_tls"] is True
    assert config.document["sources"]["akshare_cls"]["max_attempts_per_request"] == 0
    assert config.document["sources"]["futu_auxiliary"]["enabled"] is False
    assert config.document["phase_gate"]["p4_2_unlocked"] is False

    changed = tmp_path / "changed.yaml"
    changed.write_bytes(config.path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="pre-registered SHA-256"):
        news_poll.load_news_poll_config(changed)


def test_config_loader_explicitly_accepts_hash_locked_v1_and_v2(tmp_path: Path) -> None:
    v1 = news_poll.load_news_poll_config(news_poll.V1_CONFIG_PATH)
    v2 = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)

    assert v1.sha256 == news_poll.EXPECTED_CONFIG_SHA256
    assert v1.document["schema_version"] == "p4.1-news-poll-v1"
    assert v2.sha256 == news_poll.EXPECTED_V2_CONFIG_SHA256
    assert v2.document["schema_version"] == "p4.1-news-poll-v2"
    assert news_poll.DEFAULT_CONFIG_PATH == news_poll.V1_CONFIG_PATH

    changed_v2 = tmp_path / "changed-v2.yaml"
    changed_v2.write_bytes(v2.path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="pre-registered SHA-256"):
        news_poll.load_news_poll_config(changed_v2)


def test_v2_runtime_gate_fails_before_context_fetch_or_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"context": 0, "fetch": 0, "http": 0}

    def context() -> None:
        calls["context"] += 1
        return None

    def fetch(*_args: object) -> news_poll.SourceBatch:
        calls["fetch"] += 1
        return _ok_batch("cninfo")

    def http_factory(_source_id: str) -> _FakeClient:
        calls["http"] += 1
        return _FakeClient([])

    monkeypatch.setattr(news_poll, "current_job_run", context)
    monkeypatch.setattr(news_poll, "_fetch_cninfo", fetch)

    with pytest.raises(JobExecutionError, match="implementation is not ready") as caught:
        news_poll.run_news_poll(
            config_path=news_poll.V2_CONFIG_PATH,
            http_client_factory=http_factory,
        )

    assert news_poll.V2_IMPLEMENTATION_READY is False
    assert calls == {"context": 0, "fetch": 0, "http": 0}
    assert caught.value.stats == {
        "config_version": "p4.1-news-poll-v2",
        "config_path": "config/p4_news_poll_v2.yaml",
        "config_sha256": news_poll.EXPECTED_V2_CONFIG_SHA256,
        "implementation_gate": "prereg_not_ready",
        "v2_implementation_ready": False,
        "fail_closed_before_job_context": True,
        "network_attempted": False,
        "fetch_started": False,
        "poll_started_at": caught.value.stats["poll_started_at"],
        "poll_completed_at": caught.value.stats["poll_completed_at"],
        "run_mode": "regular_incremental",
        "coverage_gap": False,
        "safety_unchanged": True,
        "sources": {},
        "p4_2b_production_wiring_unlocked": False,
        "p4_3_unlocked": False,
        "terminal_diagnostics": {
            "code": "v2_implementation_not_ready",
            "source": "news_poll",
            "constraint": "implementation_gate",
            "recoverable": False,
            "retry_suppressed": False,
        },
    }


def _add_news_poll_run(
    *,
    status: str,
    config_version: str,
    config_sha256: str | None,
    cninfo: dict[str, object],
) -> None:
    stats: dict[str, object] = {
        "config_version": config_version,
        "sources": {"cninfo": cninfo},
    }
    if config_sha256 is not None:
        stats["config_sha256"] = config_sha256
    with get_session() as session:
        session.add(
            JobRun(
                job_name="news_poll",
                status=status,
                stats=stats,
            )
        )


def _column_checkpoint(value: datetime, *, committed: bool = True) -> dict[str, object]:
    return {
        "verified_watermark_after_utc": value.isoformat(),
        "checkpoint_committed": committed,
    }


def test_v2_column_watermark_lookup_accepts_ok_and_degraded_but_ignores_failed() -> None:
    legacy = datetime(2026, 8, 3, 10, tzinfo=UTC)
    sse_ok = datetime(2026, 8, 4, 10, tzinfo=UTC)
    failed_newer = datetime(2026, 8, 4, 11, tzinfo=UTC)
    szse_degraded = datetime(2026, 8, 4, 12, tzinfo=UTC)
    _add_news_poll_run(
        status="ok",
        config_version="p4.1-news-poll-v1",
        config_sha256=news_poll.EXPECTED_CONFIG_SHA256,
        cninfo={"watermark_after": legacy.isoformat()},
    )
    _add_news_poll_run(
        status="ok",
        config_version="p4.1-news-poll-v2",
        config_sha256=news_poll.EXPECTED_V2_CONFIG_SHA256,
        cninfo={
            "column_watermarks": {
                "sse": _column_checkpoint(sse_ok),
                "szse": _column_checkpoint(failed_newer, committed=False),
            }
        },
    )
    _add_news_poll_run(
        status="failed",
        config_version="p4.1-news-poll-v2",
        config_sha256=news_poll.EXPECTED_V2_CONFIG_SHA256,
        cninfo={
            "column_watermarks": {
                "sse": _column_checkpoint(failed_newer),
                "szse": _column_checkpoint(failed_newer),
            }
        },
    )
    _add_news_poll_run(
        status="degraded",
        config_version="p4.1-news-poll-v2",
        config_sha256=news_poll.EXPECTED_V2_CONFIG_SHA256,
        cninfo={
            "column_watermarks": {
                "sse": _column_checkpoint(failed_newer, committed=False),
                "szse": _column_checkpoint(szse_degraded),
            }
        },
    )
    _add_news_poll_run(
        status="ok",
        config_version="p4.1-news-poll-v2",
        config_sha256="0" * 64,
        cninfo={
            "column_watermarks": {
                "sse": _column_checkpoint(failed_newer + timedelta(hours=1)),
                "szse": _column_checkpoint(failed_newer + timedelta(hours=1)),
            }
        },
    )
    _add_news_poll_run(
        status="degraded",
        config_version="p4.1-news-poll-v2",
        config_sha256=None,
        cninfo={
            "column_watermarks": {
                "sse": _column_checkpoint(failed_newer + timedelta(hours=2)),
                "szse": _column_checkpoint(failed_newer + timedelta(hours=2)),
            }
        },
    )

    result = news_poll._last_committed_column_watermarks(
        news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH),
        "cninfo",
        ["sse", "szse"],
    )

    assert result == {"sse": sse_ok, "szse": szse_degraded}


def test_v2_column_watermark_uses_v1_global_only_for_unmigrated_columns() -> None:
    legacy = datetime(2026, 8, 3, 10, tzinfo=UTC)
    sse_v2 = datetime(2026, 8, 4, 10, tzinfo=UTC)
    _add_news_poll_run(
        status="ok",
        config_version="p4.1-news-poll-v1",
        config_sha256=news_poll.EXPECTED_CONFIG_SHA256,
        cninfo={"watermark_after": legacy.isoformat()},
    )
    _add_news_poll_run(
        status="ok",
        config_version="p4.1-news-poll-v1",
        config_sha256="0" * 64,
        cninfo={"watermark_after": (legacy + timedelta(hours=1)).isoformat()},
    )
    _add_news_poll_run(
        status="ok",
        config_version="p4.1-news-poll-v1",
        config_sha256=None,
        cninfo={"watermark_after": (legacy + timedelta(hours=2)).isoformat()},
    )
    _add_news_poll_run(
        status="ok",
        config_version="p4.1-news-poll-v2",
        config_sha256=news_poll.EXPECTED_V2_CONFIG_SHA256,
        cninfo={"column_watermarks": {"sse": _column_checkpoint(sse_v2)}},
    )

    result = news_poll._last_committed_column_watermarks(
        news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH),
        "cninfo",
        ["sse", "szse"],
    )

    assert result == {"sse": sse_v2, "szse": legacy}


def _cninfo_row(
    *,
    code: str,
    title: str,
    published_at: datetime,
) -> dict[str, object]:
    return {
        "secCode": code,
        "announcementTitle": title,
        "adjunctUrl": f"finalpage/{code}-{int(published_at.timestamp())}.PDF",
        "announcementTime": int(published_at.timestamp() * 1000),
    }


def test_v2_cninfo_incomplete_column_keeps_candidates_and_does_not_block_other_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)
    document = deepcopy(config.document)
    source = document["sources"]["cninfo"]
    source["max_pages_per_column"] = 2
    source["max_logical_requests_per_run"] = 4
    source["min_interval_seconds"] = 0
    fixture_config = news_poll.NewsPollConfig(
        path=config.path,
        sha256=config.sha256,
        document=document,
    )
    sse_before = datetime(2026, 8, 4, 8, tzinfo=UTC)
    szse_before = datetime(2026, 8, 4, 9, tzinfo=UTC)
    sse_newest = datetime(2026, 8, 4, 10, tzinfo=UTC)
    szse_newest = datetime(2026, 8, 4, 11, tzinfo=UTC)
    monkeypatch.setattr(
        news_poll,
        "_last_committed_column_watermarks",
        lambda *_args: {"sse": sse_before, "szse": szse_before},
    )
    client = _FakeClient(
        [
            _FakeResponse(
                payload={
                    "announcements": [
                        _cninfo_row(
                            code="600001",
                            title="上交所第一页公告",
                            published_at=sse_newest,
                        )
                    ],
                    "hasMore": True,
                }
            ),
            _FakeResponse(
                payload={
                    "announcements": [
                        _cninfo_row(
                            code="600002",
                            title="上交所第二页公告",
                            published_at=sse_newest - timedelta(minutes=1),
                        )
                    ],
                    "hasMore": True,
                }
            ),
            _FakeResponse(
                payload={
                    "announcements": [
                        _cninfo_row(
                            code="000001",
                            title="深交所完整公告",
                            published_at=szse_newest,
                        )
                    ],
                    "hasMore": False,
                }
            ),
        ]
    )

    batch = news_poll._fetch_cninfo(
        fixture_config,
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source_id: client,
    )

    assert batch.status == "degraded"
    assert [item.symbol for item in batch.candidates] == ["600001", "600002", "000001"]
    called_columns: list[object] = []
    for _method, _url, kwargs in client.calls:
        data = kwargs["data"]
        assert isinstance(data, dict)
        called_columns.append(data["column"])
    assert called_columns == ["sse", "sse", "szse"]
    checkpoints = batch.details["column_watermarks"]
    assert checkpoints["sse"] == {
        "verified_watermark_before_utc": sse_before.isoformat(),
        "verified_watermark_floor_utc": (sse_before - timedelta(minutes=30)).isoformat(),
        "newest_observed_at_utc": sse_newest.isoformat(),
        "verified_watermark_after_utc": sse_before.isoformat(),
        "pagination_complete": False,
        "checkpoint_committed": False,
        "page_cap_hit": True,
        "attempted": True,
        "skipped_due_to_prior_critical_failure": False,
    }
    assert checkpoints["szse"] == {
        "verified_watermark_before_utc": szse_before.isoformat(),
        "verified_watermark_floor_utc": (
            szse_before - timedelta(minutes=30)
        ).isoformat(),
        "newest_observed_at_utc": szse_newest.isoformat(),
        "verified_watermark_after_utc": szse_newest.isoformat(),
        "pagination_complete": True,
        "checkpoint_committed": True,
        "page_cap_hit": False,
        "attempted": True,
        "skipped_due_to_prior_critical_failure": False,
    }
    assert [failure["code"] for failure in batch.failures] == ["pagination_incomplete"]
    assert client.closed is True


def test_v2_page_cap_candidates_persist_but_only_complete_column_advances() -> None:
    config = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)
    document = deepcopy(config.document)
    source = document["sources"]["cninfo"]
    source["max_pages_per_column"] = 2
    source["max_logical_requests_per_run"] = 4
    source["min_interval_seconds"] = 0
    fixture_config = news_poll.NewsPollConfig(
        path=config.path,
        sha256=config.sha256,
        document=document,
    )
    legacy = datetime(2026, 8, 4, 8, tzinfo=UTC)
    sse_newest = datetime(2026, 8, 4, 10, tzinfo=UTC)
    szse_newest = datetime(2026, 8, 4, 11, tzinfo=UTC)
    _add_news_poll_run(
        status="ok",
        config_version="p4.1-news-poll-v1",
        config_sha256=news_poll.EXPECTED_CONFIG_SHA256,
        cninfo={"watermark_after": legacy.isoformat()},
    )
    client = _FakeClient(
        [
            _FakeResponse(
                payload={
                    "announcements": [
                        _cninfo_row(
                            code="600011",
                            title="页限内第一条上交所公告",
                            published_at=sse_newest,
                        )
                    ],
                    "hasMore": True,
                }
            ),
            _FakeResponse(
                payload={
                    "announcements": [
                        _cninfo_row(
                            code="600012",
                            title="页限内第二条上交所公告",
                            published_at=sse_newest - timedelta(minutes=1),
                        )
                    ],
                    "hasMore": True,
                }
            ),
            _FakeResponse(
                payload={
                    "announcements": [
                        _cninfo_row(
                            code="000011",
                            title="完整深交所公告",
                            published_at=szse_newest,
                        )
                    ],
                    "hasMore": False,
                }
            ),
        ]
    )

    batch = news_poll._fetch_cninfo_v2(
        fixture_config,
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source_id: client,
    )
    persistence = news_poll._persist_candidates(
        batch.candidates,
        datetime.now(UTC),
        job_run_id=999_991,
    )
    _add_news_poll_run(
        status="degraded",
        config_version="p4.1-news-poll-v2",
        config_sha256=news_poll.EXPECTED_V2_CONFIG_SHA256,
        cninfo=news_poll._batch_stats(batch, persistence),
    )

    next_watermarks = news_poll._last_committed_column_watermarks(
        config,
        "cninfo",
        ["sse", "szse"],
    )
    with get_session() as session:
        persisted_symbols = list(
            session.scalars(select(NewsItem.symbol).order_by(NewsItem.symbol))
        )

    assert persistence["inserted"] == 3
    assert persisted_symbols == ["000011", "600011", "600012"]
    assert next_watermarks == {"sse": legacy, "szse": szse_newest}


def test_v2_cninfo_blocked_failure_stops_before_next_column_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)
    document = deepcopy(config.document)
    source = document["sources"]["cninfo"]
    source["max_pages_per_column"] = 2
    source["max_logical_requests_per_run"] = 4
    source["min_interval_seconds"] = 0
    fixture_config = news_poll.NewsPollConfig(
        path=config.path,
        sha256=config.sha256,
        document=document,
    )
    sse_before = datetime(2026, 8, 4, 8, tzinfo=UTC)
    szse_before = datetime(2026, 8, 4, 9, tzinfo=UTC)
    monkeypatch.setattr(
        news_poll,
        "_last_committed_column_watermarks",
        lambda *_args: {"sse": sse_before, "szse": szse_before},
    )
    client = _FakeClient([_FakeResponse(status_code=403)])

    batch = news_poll._fetch_cninfo(
        fixture_config,
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source_id: client,
    )

    assert batch.status == "unavailable"
    assert batch.request_count == 1
    assert len(client.calls) == 1
    assert batch.failures == [
        {
            "code": "http_forbidden_or_antibot",
            "blocked": True,
            "error_type": "NewsSourceError",
            "message": "HTTP 403",
            "column": "sse",
        }
    ]
    checkpoints = batch.details["column_watermarks"]
    assert checkpoints["sse"] == {
        "verified_watermark_before_utc": sse_before.isoformat(),
        "verified_watermark_floor_utc": (sse_before - timedelta(minutes=30)).isoformat(),
        "newest_observed_at_utc": sse_before.isoformat(),
        "verified_watermark_after_utc": sse_before.isoformat(),
        "pagination_complete": False,
        "checkpoint_committed": False,
        "page_cap_hit": False,
        "attempted": True,
        "skipped_due_to_prior_critical_failure": False,
    }
    assert checkpoints["szse"] == {
        "verified_watermark_before_utc": szse_before.isoformat(),
        "verified_watermark_floor_utc": (
            szse_before - timedelta(minutes=30)
        ).isoformat(),
        "newest_observed_at_utc": szse_before.isoformat(),
        "verified_watermark_after_utc": szse_before.isoformat(),
        "pagination_complete": False,
        "checkpoint_committed": False,
        "page_cap_hit": False,
        "attempted": False,
        "skipped_due_to_prior_critical_failure": True,
    }
    assert client.closed is True


def test_v2_cninfo_two_columns_stop_at_frozen_forty_page_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)
    document = deepcopy(config.document)
    source = document["sources"]["cninfo"]
    source["min_interval_seconds"] = 0
    fixture_config = news_poll.NewsPollConfig(
        path=config.path,
        sha256=config.sha256,
        document=document,
    )
    before = datetime(2026, 8, 4, 8, tzinfo=UTC)
    monkeypatch.setattr(
        news_poll,
        "_last_committed_column_watermarks",
        lambda *_args: {"sse": before, "szse": before},
    )
    responses = [
        _FakeResponse(
            payload={
                "announcements": [
                    _cninfo_row(
                        code=f"{600000 + index:06d}",
                        title=f"洪峰边界公告第{index + 1}条",
                        published_at=before + timedelta(hours=1, seconds=index),
                    )
                ],
                "hasMore": True,
            }
        )
        for index in range(80)
    ]
    client = _FakeClient(responses)

    batch = news_poll._fetch_cninfo_v2(
        fixture_config,
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source_id: client,
    )

    assert source["page_size"] == 30
    assert source["max_pages_per_column"] == 40
    assert source["max_logical_requests_per_run"] == 80
    assert source["max_physical_attempts_per_run"] == 160
    assert batch.status == "degraded"
    assert batch.logical_request_count == 80
    assert batch.physical_attempt_count == batch.request_count == 80
    assert batch.retry_count == 0
    assert len(batch.candidates) == len(batch.details["requests"]) == 80
    called_columns: list[object] = []
    called_pages: list[object] = []
    for _method, _url, kwargs in client.calls:
        data = kwargs["data"]
        assert isinstance(data, dict)
        called_columns.append(data["column"])
        called_pages.append(data["pageNum"])
    assert called_columns == ["sse"] * 40 + ["szse"] * 40
    assert called_pages == list(range(1, 41)) * 2
    assert 41 not in called_pages
    assert [failure["code"] for failure in batch.failures] == [
        "pagination_incomplete",
        "pagination_incomplete",
    ]
    checkpoints = batch.details["column_watermarks"]
    assert checkpoints["sse"]["verified_watermark_after_utc"] == before.isoformat()
    assert checkpoints["szse"]["verified_watermark_after_utc"] == before.isoformat()
    assert checkpoints["sse"]["checkpoint_committed"] is False
    assert checkpoints["szse"]["checkpoint_committed"] is False
    stats = news_poll._batch_stats(batch)
    assert stats["logical_request_count"] == 80
    assert stats["physical_attempt_count"] == 80


def test_v2_cninfo_0730_shanghai_query_window_uses_market_dates_for_both_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)
    document = deepcopy(config.document)
    document["sources"]["cninfo"]["min_interval_seconds"] = 0
    fixture_config = news_poll.NewsPollConfig(
        path=config.path,
        sha256=config.sha256,
        document=document,
    )
    poll_started_at_utc = datetime(2026, 8, 3, 23, 30, tzinfo=UTC)
    sse_before = datetime(2026, 8, 3, 23, 20, tzinfo=UTC)
    szse_before = datetime(2026, 8, 3, 15, 45, tzinfo=UTC)
    monkeypatch.setattr(
        news_poll,
        "_last_committed_column_watermarks",
        lambda *_args: {"sse": sse_before, "szse": szse_before},
    )
    client = _FakeClient(
        [
            _FakeResponse(payload={"announcements": [], "hasMore": False}),
            _FakeResponse(payload={"announcements": [], "hasMore": False}),
        ]
    )

    batch = news_poll._fetch_cninfo_v2(
        fixture_config,
        poll_started_at_utc,
        lambda _source_id: client,
    )

    called_windows: dict[object, object] = {}
    for _method, _url, kwargs in client.calls:
        data = kwargs["data"]
        assert isinstance(data, dict)
        called_windows[data["column"]] = data["seDate"]
    assert called_windows == {
        "sse": "2026-08-04~2026-08-04",
        "szse": "2026-08-03~2026-08-04",
    }
    assert batch.details["query_start_date_shanghai"] == {
        "sse": "2026-08-04",
        "szse": "2026-08-03",
    }
    assert batch.details["query_end_date_shanghai"] == "2026-08-04"
    assert batch.details["market_date_at_poll"] == "2026-08-04"
    assert batch.details["poll_started_at_utc"] == poll_started_at_utc.isoformat()


def test_v1_cninfo_transport_and_report_fields_remain_legacy_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = news_poll.load_news_poll_config(news_poll.V1_CONFIG_PATH)
    document = deepcopy(config.document)
    document["sources"]["cninfo"]["min_interval_seconds"] = 0
    fixture_config = news_poll.NewsPollConfig(
        path=config.path,
        sha256=config.sha256,
        document=document,
    )
    before = datetime(2026, 8, 4, 8, tzinfo=UTC)
    monkeypatch.setattr(news_poll, "_last_successful_watermark", lambda _source: before)
    client = _FakeClient(
        [
            _FakeResponse(payload={"announcements": [], "hasMore": False}),
            _FakeResponse(payload={"announcements": [], "hasMore": False}),
        ]
    )

    batch = news_poll._fetch_cninfo(
        fixture_config,
        datetime(2026, 8, 5, 1, tzinfo=UTC),
        lambda _source_id: client,
    )
    stats = news_poll._batch_stats(batch)

    assert batch.status == "ok"
    assert batch.request_count == 2
    assert batch.retry_count == 0
    assert batch.logical_request_count is None
    assert batch.physical_attempt_count is None
    assert "logical_request_count" not in stats
    assert "physical_attempt_count" not in stats
    assert stats["request_count"] == 2
    assert stats["retry_count"] == 0
    assert batch.details["watermark_before"] == before.isoformat()
    assert batch.details["watermark_after"] == before.isoformat()
    assert batch.details["columns_complete"] == {"sse": True, "szse": True}
    for _method, _url, kwargs in client.calls:
        data = kwargs["data"]
        assert isinstance(data, dict)
        assert data["seDate"] == "2026-08-04~2026-08-05"
    assert "query_start_date_shanghai" not in batch.details
    assert "query_end_date_shanghai" not in batch.details
    assert "market_date_at_poll" not in batch.details
    assert "poll_started_at_utc" not in batch.details


def test_dual_dedupe_preserves_first_available_time_and_ingestion_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _candidate(url="https://example.test/news/1")
    same_url = _candidate(
        url="https://example.test/news/1?utm_source=replay",
        content="正文发生变化但 URL 规范键相同",
    )
    same_content = _candidate(url="https://example.test/news/2")
    _install_fake_sources(
        monkeypatch,
        [
            _ok_batch("cninfo", [first]),
            _ok_batch("cninfo", [same_url]),
            _ok_batch("cninfo", [same_content]),
        ],
    )
    news_poll.register_news_poll_job()

    records = [run_job("news_poll") for _ in range(3)]

    assert [record.status for record in records] == ["ok", "ok", "ok"]
    assert records[0].stats["sources"]["cninfo"]["inserted"] == 1
    assert records[1].stats["sources"]["cninfo"]["duplicate_url"] == 1
    assert records[1].stats["sources"]["cninfo"]["duplicate_content_hash"] == 0
    assert records[2].stats["sources"]["cninfo"]["duplicate_url"] == 0
    assert records[2].stats["sources"]["cninfo"]["duplicate_content_hash"] == 1
    assert records[0].stats["sources"]["akshare_cls"]["request_count"] == 0
    assert records[0].stats["sources"]["futu_auxiliary"]["trade_methods_called"] == []

    with get_session() as session:
        rows = list(session.scalars(select(NewsItem)))
    assert len(rows) == 1
    item = rows[0]
    assert item.symbol is None
    assert item.published_at is None
    ingestion = item.raw_payload["_alphapilot_ingestion"]
    assert ingestion["job_run_id"] == records[0].id
    assert (
        ingestion["available_time_basis"]
        == "write_locked_immediately_before_flush_utc"
    )
    assert "run_mode" not in ingestion
    assert "preceded_by_coverage_gap" not in ingestion
    assigned = datetime.fromisoformat(str(ingestion["available_time_assigned_at_utc"]))
    assert item.available_time == assigned.replace(tzinfo=None)
    cninfo_stats = records[0].stats["sources"]["cninfo"]
    assert datetime.fromisoformat(cninfo_stats["db_flush_completed_at"]) >= assigned
    assert datetime.fromisoformat(cninfo_stats["db_commit_completed_at"]) >= datetime.fromisoformat(
        cninfo_stats["db_flush_completed_at"]
    )

    with get_session() as session:
        at_cutoff = int(
            session.scalar(
                select(func.count())
                .select_from(NewsItem)
                .where(NewsItem.available_time < item.available_time)
            )
            or 0
        )
        after_cutoff = int(
            session.scalar(
                select(func.count())
                .select_from(NewsItem)
                .where(
                    NewsItem.available_time
                    < item.available_time + timedelta(microseconds=1)
                )
            )
            or 0
        )
    assert at_cutoff == 0
    assert after_cutoff == 1


def test_sina_symbol_binding_requires_explicit_title_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = """
    <html><body><div class="datelist">
      <a href="https://finance.sina.com.cn/stock/unrelated.shtml">行业景气度继续改善</a>
      <a href="https://finance.sina.com.cn/stock/maotai.shtml">贵州茅台发布经营公告</a>
    </div></body></html>
    """
    client = _FakeClient([_FakeResponse(text=html)])
    monkeypatch.setattr(
        news_poll,
        "_load_security_names",
        lambda *, watchlist_only: [("600519", "贵州茅台")],
    )

    batch = news_poll._fetch_sina(
        news_poll.load_news_poll_config(),
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "ok"
    assert [item.symbol for item in batch.candidates] == [None, "600519"]
    assert all(item.published_at is None for item in batch.candidates)
    assert batch.candidates[0].raw_payload["page_symbol_context"] == "600519"
    assert batch.candidates[0].raw_payload["symbol_binding"] == "none"


def _ths_response(*, page: int, rtime: object = 1_786_000_000) -> _FakeResponse:
    return _FakeResponse(
        payload={
            "data": {
                "list": [
                    {
                        "title": f"贵州茅台发布经营公告（{page}）",
                        "url": f"https://news.10jqka.com.cn/fixture-{page}",
                        "digest": "贵州茅台经营情况保持稳定",
                        "rtime": rtime,
                    }
                ]
            }
        }
    )


def _empty_ths_response() -> _FakeResponse:
    return _FakeResponse(payload={"data": {"list": []}})


def test_v1_ths_remains_a_single_page_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        news_poll,
        "_load_security_names",
        lambda *, watchlist_only=False: [("600519", "贵州茅台")],
    )
    client = _FakeClient([_ths_response(page=1)])

    batch = news_poll._fetch_ths(
        news_poll.load_news_poll_config(news_poll.V1_CONFIG_PATH),
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "ok"
    assert len(batch.candidates) == 1
    assert batch.request_count == 1
    assert batch.logical_request_count is None
    assert batch.physical_attempt_count is None
    assert len(client.calls) == 1
    assert client.calls[0][2]["params"] == {
        "page": "1",
        "tag": "",
        "track": "website",
    }


def test_v2_ths_paginates_until_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(news_poll, "_load_security_names", lambda **_kwargs: [])
    client = _FakeClient(
        [_ths_response(page=1), _ths_response(page=2), _empty_ths_response()]
    )

    batch = news_poll._fetch_ths(
        news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH),
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "ok"
    assert len(batch.candidates) == 2
    assert batch.logical_request_count == 3
    assert batch.physical_attempt_count == batch.request_count == 3
    assert batch.retry_count == 0
    assert batch.details["pagination_stop_reason"] == "empty_page"
    assert batch.details["catchup_complete"] is True
    assert batch.details["catchup_floor_applied"] is False
    assert [call[2]["params"]["page"] for call in client.calls] == ["1", "2", "3"]


def test_v2_ths_open_third_page_with_null_times_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(news_poll, "_load_security_names", lambda **_kwargs: [])
    client = _FakeClient(
        [
            _ths_response(page=1, rtime=None),
            _ths_response(page=2, rtime=None),
            _ths_response(page=3, rtime=None),
        ]
    )

    batch = news_poll._fetch_ths(
        news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH),
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "degraded"
    assert len(batch.candidates) == 3
    assert all(candidate.published_at is None for candidate in batch.candidates)
    assert batch.failures == [
        {
            "code": "catchup_incomplete",
            "blocked": False,
            "error_type": "NewsSourceError",
            "message": "THS page cap reached before an empty page",
            "constraint": "max_pages_per_run",
        }
    ]
    assert batch.details["pagination_stop_reason"] == "page_cap_open"
    assert batch.details["catchup_complete"] is False
    assert batch.logical_request_count == 3
    assert batch.physical_attempt_count == batch.request_count == 3
    assert len(client.calls) == 3


def test_v2_ths_retry_is_physical_not_logical_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(news_poll, "_load_security_names", lambda **_kwargs: [])
    client = _FakeClient(
        [
            _FakeResponse(status_code=500),
            _ths_response(page=1),
            _empty_ths_response(),
        ]
    )

    batch = news_poll._fetch_ths(
        news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH),
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "ok"
    assert batch.logical_request_count == 2
    assert batch.physical_attempt_count == batch.request_count == 3
    assert batch.retry_count == 1
    assert len(client.calls) == 3


def test_v2_ths_rate_limit_stops_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(news_poll, "_load_security_names", lambda **_kwargs: [])
    client = _FakeClient([_FakeResponse(status_code=429)])

    batch = news_poll._fetch_ths(
        news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH),
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "unavailable"
    assert batch.failures[0]["code"] == "http_rate_limited"
    assert batch.failures[0]["blocked"] is True
    assert batch.logical_request_count == 1
    assert batch.physical_attempt_count == batch.request_count == 1
    assert batch.retry_count == 0
    assert len(client.calls) == 1


def test_v2_ths_schema_failure_is_not_rewritten_as_budget_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(news_poll, "_load_security_names", lambda **_kwargs: [])
    client = _FakeClient([_FakeResponse(payload={"data": {"unexpected": []}})])

    batch = news_poll._fetch_ths(
        news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH),
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "unavailable"
    assert batch.failures[0]["code"] == "schema_changed"
    assert batch.logical_request_count == 1
    assert batch.physical_attempt_count == batch.request_count == 1
    assert batch.retry_count == 0
    assert len(client.calls) == 1


def test_v2_ths_physical_suppression_retains_original_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)
    document = deepcopy(config.document)
    source = document["sources"]["akshare_ths"]
    source["max_physical_attempts_per_run"] = 1
    fixture_config = news_poll.NewsPollConfig(
        path=config.path,
        sha256=config.sha256,
        document=document,
    )
    monkeypatch.setattr(news_poll, "_load_security_names", lambda **_kwargs: [])
    client = _FakeClient([_FakeResponse(status_code=500)])

    batch = news_poll._fetch_ths(
        fixture_config,
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "unavailable"
    failure = batch.failures[0]
    assert failure["code"] == "http_server_error"
    assert failure["suppression"]["code"] == (
        "retry_suppressed_physical_attempt_budget"
    )
    assert batch.logical_request_count == 1
    assert batch.physical_attempt_count == batch.request_count == 1
    assert batch.retry_count == 0
    assert len(client.calls) == 1


def test_v2_noncritical_sources_report_frozen_dual_budget_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)
    monkeypatch.setattr(
        news_poll,
        "_load_security_names",
        lambda *, watchlist_only=False: [("600519", "贵州茅台")],
    )
    ths_client = _FakeClient([_ths_response(page=1), _empty_ths_response()])
    sina_client = _FakeClient(
        [
            _FakeResponse(
                text=(
                    '<html><body><div class="datelist">'
                    '<a href="https://finance.sina.com.cn/stock/maotai.shtml">'
                    "贵州茅台发布经营公告</a></div></body></html>"
                )
            )
        ]
    )

    ths = news_poll._fetch_ths(
        config,
        datetime.now(UTC),
        lambda _source_id: ths_client,
    )
    sina = news_poll._fetch_sina(
        config,
        datetime.now(UTC),
        lambda _source_id: sina_client,
    )

    assert ths.status == sina.status == "ok"
    assert ths.logical_request_count == 2
    assert ths.physical_attempt_count == ths.request_count == 2
    assert ths.retry_count == 0
    assert sina.logical_request_count == 1
    assert sina.physical_attempt_count == sina.request_count == 1
    assert sina.retry_count == 0
    for batch in (ths, sina):
        stats = news_poll._batch_stats(batch)
        assert stats["logical_request_count"] == batch.logical_request_count
        assert stats["physical_attempt_count"] == batch.physical_attempt_count
        assert stats["retry_count"] == batch.retry_count


def test_v2_sina_no_watchlist_reports_zero_dual_budget_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)
    monkeypatch.setattr(news_poll, "_load_security_names", lambda **_kwargs: [])
    client = _FakeClient([])

    batch = news_poll._fetch_sina(
        config,
        datetime.now(UTC),
        lambda _source_id: client,
    )

    assert batch.status == "skipped_no_watchlist"
    assert batch.logical_request_count == 0
    assert batch.physical_attempt_count == batch.request_count == 0
    assert batch.retry_count == 0
    assert news_poll._batch_stats(batch)["logical_request_count"] == 0
    assert news_poll._batch_stats(batch)["physical_attempt_count"] == 0
    assert client.calls == []
    assert client.closed is True


def test_zero_request_stats_are_v2_only() -> None:
    v1 = news_poll.load_news_poll_config(news_poll.V1_CONFIG_PATH)
    v2 = news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH)

    assert news_poll._zero_request_counter_stats(v1) == {
        "request_count": 0,
        "retry_count": 0,
    }
    assert news_poll._zero_request_counter_stats(v2) == {
        "request_count": 0,
        "retry_count": 0,
        "logical_request_count": 0,
        "physical_attempt_count": 0,
    }


def _v2_checkpoint_stats(*, page_cap_hit: bool) -> dict[str, object]:
    complete = not page_cap_hit
    return {
        "verified_watermark_before_utc": "2026-08-05T00:00:00+00:00",
        "verified_watermark_floor_utc": "2026-08-04T23:30:00+00:00",
        "newest_observed_at_utc": "2026-08-05T00:05:00+00:00",
        "verified_watermark_after_utc": (
            "2026-08-05T00:05:00+00:00" if complete else "2026-08-05T00:00:00+00:00"
        ),
        "pagination_complete": complete,
        "checkpoint_committed": complete,
        "page_cap_hit": page_cap_hit,
        "attempted": True,
        "skipped_due_to_prior_critical_failure": False,
    }


def _v2_cninfo_batch(
    *,
    status: str,
    failures: list[dict[str, object]] | None = None,
    capped_columns: set[str] | None = None,
    candidates: list[news_poll.NewsCandidate] | None = None,
) -> news_poll.SourceBatch:
    capped = capped_columns or set()
    return news_poll.SourceBatch(
        source_id="cninfo",
        status=status,
        request_count=2,
        logical_request_count=2,
        physical_attempt_count=2,
        candidates=candidates or [],
        failures=failures or [],
        details={
            "column_watermarks": {
                column: _v2_checkpoint_stats(page_cap_hit=column in capped)
                for column in ("sse", "szse")
            },
            "requests": [],
            "tls_verification": True,
        },
    )


def _v2_noncritical_batch(source_id: str, *, failed: bool) -> news_poll.SourceBatch:
    return news_poll.SourceBatch(
        source_id=source_id,
        status="unavailable" if failed else "ok",
        request_count=1,
        logical_request_count=1,
        physical_attempt_count=1,
        failures=(
            [
                {
                    "code": "transport_error",
                    "blocked": False,
                    "error_type": "NewsSourceError",
                    "message": "bounded noncritical failure",
                }
            ]
            if failed
            else []
        ),
    )


def _run_v2_news_poll(
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime | None = None,
) -> JobRun:
    monkeypatch.setattr(news_poll, "V2_IMPLEMENTATION_READY", True)
    register(
        JobSpec(
            name="news_poll",
            func=lambda: news_poll.run_news_poll(
                config_path=news_poll.V2_CONFIG_PATH,
                now=now,
            ),
            trigger=None,
        )
    )
    return run_job("news_poll")


def test_v2_complete_cninfo_is_ok_even_when_noncritical_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(status="ok"),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=True),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=True),
    )

    record = _run_v2_news_poll(monkeypatch)

    assert record.status == "ok"
    assert record.error is None
    assert record.stats["terminal_diagnostics"] is None
    assert record.stats["sources"]["cninfo"]["status"] == "ok"
    assert record.stats["sources"]["akshare_ths"]["status"] == "unavailable"
    assert record.stats["sources"]["sina_company_news"]["status"] == "unavailable"


def test_v2_monday_recovery_marks_rows_and_records_structured_catchup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(url="https://example.test/news/monday-catchup")
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(status="ok", candidates=[candidate]),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )
    monday_0950 = datetime(2026, 8, 10, 1, 50, tzinfo=UTC)

    record = _run_v2_news_poll(monkeypatch, now=monday_0950)

    assert record.status == "ok"
    assert record.stats["run_mode"] == "coverage_gap_catchup"
    assert record.stats["coverage_gap"] is True
    details = record.stats["coverage_gap_details"]
    assert details["suppressed_slot_count"] == 3
    assert [value[11:16] for value in details["suppressed_slots_shanghai"]] == [
        "09:00",
        "09:30",
        "09:40",
    ]
    assert details["span_seconds"] == 3000
    assert details["span_basis"] == (
        "first_suppressed_slot_to_actual_poll_started_at"
    )
    catchup = record.stats["catchup"]
    assert catchup["range_end_utc"] == monday_0950.isoformat()
    assert set(catchup["cninfo_column_ranges"]) == {"sse", "szse"}
    expected_start = datetime(2026, 8, 4, 23, 30, tzinfo=UTC)
    for column_range in catchup["cninfo_column_ranges"].values():
        assert column_range == {
            "start_utc": expected_start.isoformat(),
            "end_utc": monday_0950.isoformat(),
            "span_seconds": int((monday_0950 - expected_start).total_seconds()),
        }
    assert catchup["counts_all_sources"]["inserted"] == 1
    assert catchup["counts_all_sources"]["preceded_by_coverage_gap_inserted"] == 1
    assert catchup["counts_by_source"]["cninfo"]["inserted"] == 1

    with get_session() as session:
        item = session.scalar(select(NewsItem).where(NewsItem.url == candidate.url))
    assert item is not None
    ingestion = item.raw_payload["_alphapilot_ingestion"]
    assert ingestion["run_mode"] == "coverage_gap_catchup"
    assert ingestion["preceded_by_coverage_gap"] is True
    original_available_time = item.available_time

    replay = _run_v2_news_poll(
        monkeypatch,
        now=datetime(2026, 8, 11, 1, 50, tzinfo=UTC),
    )
    assert replay.status == "ok"
    assert replay.stats["sources"]["cninfo"]["inserted"] == 0
    assert replay.stats["sources"]["cninfo"]["duplicate_url"] == 1
    with get_session() as session:
        replayed = session.scalar(select(NewsItem).where(NewsItem.url == candidate.url))
    assert replayed is not None
    assert replayed.available_time == original_available_time
    assert replayed.raw_payload["_alphapilot_ingestion"] == ingestion


@pytest.mark.parametrize(
    ("started_at", "expected_mode", "expected_gap"),
    [
        (datetime(2026, 8, 10, 1, 50, tzinfo=UTC), "coverage_gap_catchup", True),
        (
            datetime(2026, 8, 10, 1, 59, 59, tzinfo=UTC),
            "coverage_gap_catchup",
            True,
        ),
        (datetime(2026, 8, 10, 2, 0, tzinfo=UTC), "regular_incremental", False),
        (datetime(2026, 8, 11, 1, 50, tzinfo=UTC), "regular_incremental", False),
    ],
)
def test_v2_run_mode_uses_the_frozen_monday_recovery_window(
    started_at: datetime,
    expected_mode: str,
    expected_gap: bool,
) -> None:
    context = news_poll._v2_run_context(
        news_poll.load_news_poll_config(news_poll.V2_CONFIG_PATH),
        started_at,
    )

    assert context["run_mode"] == expected_mode
    assert context["coverage_gap"] is expected_gap
    assert (context["coverage_gap_details"] is not None) is expected_gap


def test_v2_regular_run_marks_new_rows_as_not_preceded_by_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(url="https://example.test/news/regular-v2")
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(status="ok", candidates=[candidate]),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )

    record = _run_v2_news_poll(
        monkeypatch,
        now=datetime(2026, 8, 11, 1, 50, tzinfo=UTC),
    )

    assert record.status == "ok"
    assert record.stats["run_mode"] == "regular_incremental"
    assert record.stats["coverage_gap"] is False
    assert record.stats["coverage_gap_details"] is None
    assert record.stats["catchup"] is None
    with get_session() as session:
        item = session.scalar(select(NewsItem).where(NewsItem.url == candidate.url))
    assert item is not None
    ingestion = item.raw_payload["_alphapilot_ingestion"]
    assert ingestion["run_mode"] == "regular_incremental"
    assert ingestion["preceded_by_coverage_gap"] is False


def test_v2_complete_cninfo_is_ok_when_ths_page_cap_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(status="ok"),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: news_poll.SourceBatch(
            source_id="akshare_ths",
            status="degraded",
            request_count=3,
            logical_request_count=3,
            physical_attempt_count=3,
            failures=[
                {
                    "code": "catchup_incomplete",
                    "blocked": False,
                    "error_type": "NewsSourceError",
                    "message": "THS page cap reached before an empty page",
                    "constraint": "max_pages_per_run",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )

    record = _run_v2_news_poll(monkeypatch)

    assert record.status == "ok"
    assert record.error is None
    assert record.stats["terminal_diagnostics"] is None
    assert record.stats["sources"]["cninfo"]["status"] == "ok"
    assert record.stats["sources"]["akshare_ths"]["status"] == "degraded"
    assert record.stats["sources"]["akshare_ths"]["failures"][0]["code"] == (
        "catchup_incomplete"
    )


def test_v2_page_cap_only_cninfo_is_degraded_with_null_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = {
        "code": "pagination_incomplete",
        "blocked": False,
        "error_type": "NewsSourceError",
        "message": "must not be copied into terminal diagnostics",
        "column": "sse",
    }
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(
            status="degraded",
            failures=[failure],
            capped_columns={"sse"},
        ),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )

    record = _run_v2_news_poll(monkeypatch)

    assert record.status == "degraded"
    assert record.error is None
    assert record.stats["terminal_diagnostics"] == {
        "code": "cninfo_column_pagination_incomplete",
        "source": "cninfo",
        "constraint": "max_pages_per_column",
        "recoverable": True,
        "retry_suppressed": False,
    }
    assert "message" not in record.stats["terminal_diagnostics"]


def test_v2_monday_catchup_page_cap_uses_recovery_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = {
        "code": "pagination_incomplete",
        "blocked": False,
        "error_type": "NewsSourceError",
        "message": "bounded fixture",
        "column": "sse",
    }
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(
            status="degraded",
            failures=[failure],
            capped_columns={"sse"},
        ),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )

    record = _run_v2_news_poll(
        monkeypatch,
        now=datetime(2026, 8, 10, 1, 50, tzinfo=UTC),
    )

    assert record.status == "degraded"
    assert record.error is None
    assert record.stats["run_mode"] == "coverage_gap_catchup"
    assert record.stats["terminal_diagnostics"] == {
        "code": "recovery_catchup_incomplete",
        "source": "cninfo",
        "constraint": "max_pages_per_column",
        "recoverable": True,
        "retry_suppressed": False,
    }


def test_v2_cninfo_transport_failure_is_failed_with_safe_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "https://user:secret@example.test/?token=do-not-persist"
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(
            status="unavailable",
            failures=[
                {
                    "code": "transport_timeout",
                    "blocked": False,
                    "error_type": "NewsSourceError",
                    "message": secret,
                    "column": "sse",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )

    record = _run_v2_news_poll(monkeypatch)

    assert record.status == "failed"
    assert record.error == (
        "JobExecutionError: P4.1 critical source failed: cninfo/transport_timeout"
    )
    assert record.stats["terminal_diagnostics"] == {
        "code": "transport_timeout",
        "source": "cninfo",
        "constraint": "critical_transport",
        "recoverable": True,
        "retry_suppressed": False,
    }
    assert secret not in str(record.stats)
    assert secret not in str(record.error)


def test_v2_cninfo_schema_failure_is_failed_not_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(
            status="unavailable",
            failures=[
                {
                    "code": "schema_changed",
                    "blocked": False,
                    "error_type": "NewsSourceError",
                    "message": "unexpected upstream field",
                    "column": "sse",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )

    record = _run_v2_news_poll(monkeypatch)

    assert record.status == "failed"
    assert record.stats["terminal_diagnostics"] == {
        "code": "schema_changed",
        "source": "cninfo",
        "constraint": "critical_schema",
        "recoverable": False,
        "retry_suppressed": False,
    }


def test_v2_safety_preflight_is_failed_with_structured_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        news_poll,
        "_safety_snapshot",
        lambda _settings: {
            "settings": {
                "trading_mode": "research",
                "live_trading_enabled": True,
                "paper_trading_enabled": False,
                "paper_auto_trading_enabled": False,
                "futu_enable_account_mutation": False,
                "futu_enable_trade": False,
                "unlock_trade_permanently_blocked": True,
            },
            "trade_proposal_ids": [],
            "broker_order_ids": [],
            "non_simulate_order_count": 0,
        },
    )

    record = _run_v2_news_poll(monkeypatch)

    assert record.status == "failed"
    assert record.error == "JobExecutionError: P4.1 news poll safety preflight failed"
    assert record.stats["terminal_diagnostics"] == {
        "code": "safety_preflight_failed",
        "source": "news_poll",
        "constraint": "trading_safety_invariants",
        "recoverable": False,
        "retry_suppressed": False,
    }
    assert record.stats["run_mode"] == "regular_incremental"
    assert record.stats["coverage_gap"] is False
    assert record.stats["poll_started_at"]
    assert record.stats["poll_completed_at"]
    assert record.stats["safety_after"] == record.stats["safety_before"]
    assert record.stats["safety_unchanged"] is True


def test_v2_unknown_degraded_cause_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(
            status="degraded",
            failures=[
                {
                    "code": "unregistered_degraded_reason",
                    "blocked": False,
                    "error_type": "NewsSourceError",
                    "message": "response shape changed",
                    "column": "sse",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )

    record = _run_v2_news_poll(monkeypatch)

    assert record.status == "failed"
    assert record.error == (
        "JobExecutionError: P4.1 critical source failed: cninfo/cninfo_invalid_degraded_state"
    )
    assert record.stats["terminal_diagnostics"]["constraint"] == ("degraded_cause_allowlist")


def test_v2_cninfo_persistence_failure_is_sanitized_and_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sqlite://private:credential@host/database"
    monkeypatch.setattr(
        news_poll,
        "_fetch_cninfo",
        lambda *_args: _v2_cninfo_batch(status="ok"),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_ths",
        lambda *_args: _v2_noncritical_batch("akshare_ths", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_fetch_sina",
        lambda *_args: _v2_noncritical_batch("sina_company_news", failed=False),
    )
    monkeypatch.setattr(
        news_poll,
        "_persist_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    record = _run_v2_news_poll(monkeypatch)

    assert record.status == "failed"
    assert record.error == (
        "JobExecutionError: P4.1 critical source failed: cninfo/persistence_failed"
    )
    assert record.stats["terminal_diagnostics"]["constraint"] == "critical_persistence"
    assert secret not in str(record.stats)
    assert secret not in str(record.error)


def test_critical_source_failure_keeps_complete_jobrun_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = news_poll.SourceBatch(
        source_id="cninfo",
        status="unavailable",
        request_count=2,
        retry_count=1,
        failures=[
            {
                "code": "transport_timeout",
                "blocked": False,
                "error_type": "NewsSourceError",
                "message": "bounded timeout",
            }
        ],
    )
    _install_fake_sources(monkeypatch, [failed])
    news_poll.register_news_poll_job()

    record = run_job("news_poll")

    assert record.status == "failed"
    assert record.error is not None and "critical news source failed" in record.error
    cninfo = record.stats["sources"]["cninfo"]
    assert cninfo["status"] == "unavailable"
    assert cninfo["request_count"] == 2
    assert cninfo["retry_count"] == 1
    assert cninfo["failure_count"] == 1
    assert cninfo["failures"][0]["code"] == "transport_timeout"
    assert record.stats["poll_completed_at"].endswith("+00:00")
    assert record.stats["safety_unchanged"] is True


def test_rate_limit_stops_without_retry() -> None:
    client = _FakeClient([_FakeResponse(status_code=429)])
    transport = news_poll._BoundedHttp(
        source_id="fixture",
        client=client,
        allowed_hosts={"example.test"},
        max_requests=3,
        max_attempts=3,
        min_interval_seconds=0,
        retry_backoff_seconds=[0, 0],
    )

    with pytest.raises(news_poll.NewsSourceError) as caught:
        transport.request("GET", "https://example.test/news")

    assert caught.value.code == "http_rate_limited"
    assert caught.value.blocked is True
    assert transport.request_count == 1
    assert transport.retry_count == 0
    assert len(client.calls) == 1


def _v2_transport(
    client: _FakeClient,
    *,
    max_logical_requests: int,
    max_physical_attempts: int,
    max_attempts_per_logical_request: int,
) -> news_poll._BoundedHttp:
    return news_poll._BoundedHttp(
        source_id="fixture-v2",
        client=client,
        allowed_hosts={"example.test"},
        max_requests=max_physical_attempts,
        max_attempts=max_attempts_per_logical_request,
        max_logical_requests=max_logical_requests,
        max_physical_attempts=max_physical_attempts,
        max_attempts_per_logical_request=max_attempts_per_logical_request,
        min_interval_seconds=0,
        retry_backoff_seconds=[0],
    )


def test_v2_transport_one_logical_request_can_use_two_physical_attempts() -> None:
    client = _FakeClient(
        [_FakeResponse(status_code=500), _FakeResponse(status_code=200)]
    )
    transport = _v2_transport(
        client,
        max_logical_requests=1,
        max_physical_attempts=2,
        max_attempts_per_logical_request=2,
    )

    response = transport.request("GET", "https://example.test/news")

    assert response.status_code == 200
    assert transport.logical_request_count == 1
    assert transport.physical_attempt_count == transport.request_count == 2
    assert transport.retry_count == 1
    assert [(item["logical_request"], item["attempt"]) for item in transport.requests] == [
        (1, 1),
        (1, 2),
    ]
    assert len(client.calls) == 2


def test_v2_transport_physical_budget_suppresses_retry_without_overwriting_error() -> None:
    client = _FakeClient([_FakeResponse(status_code=500)])
    transport = _v2_transport(
        client,
        max_logical_requests=1,
        max_physical_attempts=1,
        max_attempts_per_logical_request=2,
    )

    with pytest.raises(news_poll.NewsSourceError) as caught:
        transport.request("GET", "https://example.test/news")

    assert caught.value.code == "http_server_error"
    assert caught.value.blocked is False
    assert caught.value.suppression == {
        "code": "retry_suppressed_physical_attempt_budget",
        "constraint": "max_physical_attempts_per_run",
        "source_id": "fixture-v2",
        "logical_request_count": 1,
        "physical_attempt_count": 1,
        "max_physical_attempts": 1,
        "retry_suppressed": True,
    }
    assert news_poll._source_failure(caught.value)["code"] == "http_server_error"
    assert transport.logical_request_count == 1
    assert transport.physical_attempt_count == transport.request_count == 1
    assert transport.retry_count == 0
    assert transport.requests[0]["failure_code"] == "http_server_error"
    assert transport.requests[0]["retry_suppression"] == caught.value.suppression
    assert len(client.calls) == 1


def test_v2_transport_logical_budget_rejects_new_operation_before_network() -> None:
    client = _FakeClient([_FakeResponse(status_code=200)])
    transport = _v2_transport(
        client,
        max_logical_requests=1,
        max_physical_attempts=2,
        max_attempts_per_logical_request=2,
    )
    transport.request("GET", "https://example.test/news/first")

    with pytest.raises(news_poll.NewsSourceError) as caught:
        transport.request("GET", "https://example.test/news/second")

    assert caught.value.code == "logical_request_budget_exhausted"
    assert caught.value.blocked is True
    assert transport.logical_request_count == 1
    assert transport.physical_attempt_count == transport.request_count == 1
    assert transport.retry_count == 0
    assert len(transport.requests) == len(client.calls) == 1


def test_v2_transport_exhausted_physical_budget_rejects_before_logical_acceptance() -> None:
    client = _FakeClient([_FakeResponse(status_code=200)])
    transport = _v2_transport(
        client,
        max_logical_requests=2,
        max_physical_attempts=1,
        max_attempts_per_logical_request=1,
    )
    transport.request("GET", "https://example.test/news/first")

    with pytest.raises(news_poll.NewsSourceError) as caught:
        transport.request("GET", "https://example.test/news/second")

    assert caught.value.code == "physical_attempt_budget_exhausted"
    assert caught.value.blocked is True
    assert transport.logical_request_count == 1
    assert transport.physical_attempt_count == transport.request_count == 1
    assert transport.retry_count == 0
    assert (
        transport.retry_count
        == transport.physical_attempt_count - transport.logical_request_count
    )
    assert len(transport.requests) == len(client.calls) == 1


def _trigger_fire_times(
    trigger: BaseTrigger,
    target: date,
) -> list[datetime]:
    start = datetime.combine(target, time.min, tzinfo=news_poll.MARKET_TIMEZONE)
    end = start + timedelta(days=1)
    previous: datetime | None = None
    current = start
    result: list[datetime] = []
    while True:
        value = trigger.get_next_fire_time(previous, current)
        if value is None or value >= end:
            return result
        result.append(value)
        previous = value
        current = value + timedelta(microseconds=1)


def test_v2_trigger_matches_the_frozen_61_64_64_slot_contract() -> None:
    monday = date(2026, 8, 10)
    tuesday = date(2026, 8, 11)
    wednesday = date(2026, 8, 12)

    v1_monday = _trigger_fire_times(news_poll._news_poll_trigger_v1(), monday)
    v2_monday = _trigger_fire_times(news_poll._news_poll_trigger_v2(), monday)
    v2_tuesday = _trigger_fire_times(news_poll._news_poll_trigger_v2(), tuesday)
    v2_wednesday = _trigger_fire_times(news_poll._news_poll_trigger_v2(), wednesday)

    assert len(v1_monday) == 64
    assert len(v2_monday) == 61
    assert len(v2_tuesday) == len(v2_wednesday) == 64
    assert len(v2_monday) + len(v2_tuesday) + len(v2_wednesday) == 189
    assert [value.strftime("%H:%M") for value in v1_monday if value.hour == 9] == [
        "09:00",
        "09:30",
        "09:40",
        "09:50",
    ]
    assert [value.strftime("%H:%M") for value in v2_monday if value.hour == 9] == [
        "09:50"
    ]
    for values in (v2_monday, v2_tuesday, v2_wednesday):
        assert len(values) == len(set(values))
        assert all(value.tzinfo == news_poll.MARKET_TIMEZONE for value in values)


def test_news_poll_scheduler_requires_explicit_env_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(news_poll.NEWS_POLL_ENABLED_ENV, "false")
    news_poll.register_news_poll_job()
    assert JOBS["news_poll"].trigger is None

    monkeypatch.setenv(news_poll.NEWS_POLL_ENABLED_ENV, "true")
    news_poll.register_news_poll_job()
    assert JOBS["news_poll"].trigger is not None

    monkeypatch.setenv(news_poll.NEWS_POLL_ENABLED_ENV, "yes")
    with pytest.raises(ValueError, match="must be exactly true or false"):
        news_poll.register_news_poll_job()


def test_pit_timestamp_is_assigned_only_after_sqlite_write_lock() -> None:
    database_url = get_settings().database_url
    assert database_url.startswith("sqlite:///")
    database = database_url.removeprefix("sqlite:///")
    holder = sqlite3.connect(database, timeout=1, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    entered = Event()
    outcome: dict[str, object] = {}

    def persist() -> None:
        entered.set()
        try:
            outcome["stats"] = news_poll._persist_candidates(
                [_candidate(url="https://example.test/news/write-lock")],
                datetime.now(UTC),
                job_run_id=999_999,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            outcome["error"] = exc

    worker = Thread(target=persist)
    worker.start()
    assert entered.wait(timeout=1)
    sleep(0.1)
    assert worker.is_alive()
    with get_session() as session:
        assert int(session.scalar(select(func.count()).select_from(NewsItem)) or 0) == 0

    release_started_at = datetime.now(UTC)
    holder.rollback()
    holder.close()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert "error" not in outcome
    stats = outcome["stats"]
    assert isinstance(stats, dict)
    lock_acquired = datetime.fromisoformat(str(stats["db_write_lock_acquired_at"]))
    available = datetime.fromisoformat(str(stats["first_available_time"]))
    flush_completed = datetime.fromisoformat(str(stats["db_flush_completed_at"]))
    commit_completed = datetime.fromisoformat(str(stats["db_commit_completed_at"]))
    assert release_started_at <= lock_acquired <= available <= flush_completed <= commit_completed
