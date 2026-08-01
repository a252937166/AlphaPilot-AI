from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from time import sleep

import pytest
from sqlalchemy import delete, func, select

from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import JobRun, NewsItem
from alphapilot.jobs import news_poll
from alphapilot.jobs.registry import JOBS, run_job


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
def _clean_news_rows() -> None:
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
