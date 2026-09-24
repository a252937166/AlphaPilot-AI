"""jev's shadow of the severe screen: typed answers logged next to the rules, nothing emitted."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, DomainEvent, NewsItem
from alphapilot.jobs import severe_disclosure_shadow as shadow_job
from alphapilot.jobs.registry import JobOutcome
from alphapilot.services import severe_shadow

# Available at 2026-09-11 08:00 UTC = 16:00 in Shanghai.
FRESH = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
TITLES = {
    1: "关于收到中国证券监督管理委员会立案告知书的公告",
    2: "东兴证券股份有限公司关于吸收合并事项导致公司A股股票可能终止上市的风险提示公告",
    3: "关于收到上海证券交易所公开谴责决定的公告",
    4: "2026年半年度报告",
}
JEV_CHOICE = {
    TITLES[1]: "investigation",
    TITLES[2]: "merger_delisting",
    TITLES[3]: "penalty_decision",
    TITLES[4]: "other",
}


class FakeJev:
    model = "jev-1.13.0"

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.asked: list[str] = []

    def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        title = state["announcement_title"]
        assert isinstance(state["recent_titles_same_company"], list)
        self.asked.append(title)
        assert set(questions) == {"category"}
        assert set(questions["category"]["criteria"]) == set(severe_shadow.CHOICES)
        if title in self.fail or "*" in self.fail:
            raise RuntimeError("jev http 503")
        choice = JEV_CHOICE[title]
        return {
            "model": self.model,
            "answers": {
                "category": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": {choice: 0.9, "other": 0.1},
                    "confidence": 0.8,
                }
            },
            "usage": {"input_tokens": 612},
        }


def _news(news_id: int, title: str, *, published_at: datetime = FRESH) -> NewsItem:
    return NewsItem(
        id=news_id,
        source="cninfo",
        symbol=f"60000{news_id}",
        title=title,
        url=f"https://static.cninfo.com.cn/finalpage/2026-09-11/{news_id}.PDF",
        published_at=published_at,
        available_time=FRESH,
        content_hash=f"hash-{news_id}",
        raw_payload={},
    )


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / 'shadow.db'}")
    Base.metadata.create_all(engine)

    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    monkeypatch.setattr(shadow_job, "get_session", local_session)
    with Session(engine) as session:
        session.add_all([_news(i, title) for i, title in TITLES.items()])
        # A poller catch-up: ingested with the batch but published a month earlier.
        session.add(
            _news(5, "关于公司立案调查进展暨风险提示公告", published_at=FRESH - timedelta(days=30))
        )
        session.commit()
    return engine


def _lines(root: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (root / "2026-09-11.jsonl").read_text().splitlines()]


def test_shadow_logs_jev_next_to_the_rules_and_emits_nothing(database: Any, tmp_path: Path) -> None:
    root = tmp_path / "shadow"
    fake = FakeJev()
    stats = shadow_job.run_severe_disclosure_shadow(output_dir=root, client=fake, workers=2)
    assert isinstance(stats, dict)
    assert stats["fetched"] == 5 and stats["stale_skipped"] == 1 and stats["asked"] == 4
    assert stats["errors"] == 0 and stats["rule_severe"] == 1 and stats["jev_severe"] == 2
    assert stats["written"] == {"2026-09-11": 4} and stats["cursor_to"] == 5
    assert sorted(fake.asked) == sorted(TITLES.values())  # the stale title is never asked

    records = {r["news_id"]: r for r in _lines(root)}
    assert records[1]["rule"] == {"subtype": "investigation", "keyword": "立案告知书"}
    assert records[1]["jev"]["choice"] == "investigation"
    assert records[2]["rule"] is None and records[2]["jev"]["choice"] == "merger_delisting"
    assert records[3]["jev"]["probabilities"] == {"penalty_decision": 0.9, "other": 0.1}
    assert records[4]["input_tokens"] == 612 and records[4]["model"] == "jev-1.13.0"
    assert records[4]["question_version"] == severe_shadow.QUESTION_VERSION
    assert records[1]["context_ids"] == []  # each fixture title is its company's only one

    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 0

    again = shadow_job.run_severe_disclosure_shadow(output_dir=root, client=fake, workers=2)
    assert isinstance(again, dict) and again["fetched"] == 0 and len(fake.asked) == 4


def test_a_mostly_failing_batch_keeps_the_cursor(database: Any, tmp_path: Path) -> None:
    root = tmp_path / "shadow"
    down = shadow_job.run_severe_disclosure_shadow(
        output_dir=root, client=FakeJev(fail={"*"}), workers=2
    )
    assert isinstance(down, JobOutcome) and down.status == "degraded"
    assert down.stats["reason"] == "jev_mostly_failed" and down.stats["errors"] == 4
    assert not (root / "state.json").exists() and not (root / "2026-09-11.jsonl").exists()

    partial = shadow_job.run_severe_disclosure_shadow(
        output_dir=root, client=FakeJev(fail={TITLES[4]}), workers=2
    )
    assert isinstance(partial, dict) and partial["errors"] == 1 and partial["cursor_to"] == 5
    failed = [r for r in _lines(root) if r["error"]]
    assert [r["news_id"] for r in failed] == [4] and failed[0]["jev"] is None


def test_missing_key_is_a_degraded_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alphapilot.core.config import get_settings

    monkeypatch.setattr(get_settings(), "jev_api_key", None)
    outcome = shadow_job.run_severe_disclosure_shadow(output_dir=tmp_path)
    assert isinstance(outcome, JobOutcome) and outcome.stats == {"skipped": "jev_not_configured"}


def test_summary_separates_agreement_from_each_side(database: Any, tmp_path: Path) -> None:
    root = tmp_path / "shadow"
    shadow_job.run_severe_disclosure_shadow(output_dir=root, client=FakeJev(), workers=2)
    records = severe_shadow.load_records(root, date(2026, 9, 11), date(2026, 9, 11))
    summary = severe_shadow.summarize(records)
    assert summary["answered"] == 4 and summary["both_severe"] == 1 and summary["same_subtype"] == 1
    assert summary["rule_only"] == 0 and summary["jev_only"] == 1 and summary["neither"] == 2
    assert [e["news_id"] for e in summary["jev_only_examples"]] == [3]
    assert summary["jev_choices"]["merger_delisting"] == 1


def test_a_reasked_title_keeps_its_last_answer(tmp_path: Path) -> None:
    base = {"symbol": "600001", "title": "t", "available_time": FRESH.isoformat(), "rule": None}
    severe_shadow.append_records(tmp_path, [{**base, "news_id": 1, "jev": {"choice": "other"}}])
    severe_shadow.append_records(
        tmp_path, [{**base, "news_id": 1, "jev": {"choice": "investigation"}}]
    )
    records = severe_shadow.load_records(tmp_path, date(2026, 9, 11), date(2026, 9, 11))
    assert [r["jev"]["choice"] for r in records] == ["investigation"]


def test_job_is_registered_beside_the_screen() -> None:
    from alphapilot.core.config import Settings
    from alphapilot.jobs import register_builtin_jobs
    from alphapilot.jobs.registry import JOBS

    register_builtin_jobs()
    spec = JOBS["severe_disclosure_shadow"]
    assert spec.enabled_key == "severe_shadow_enabled"
    assert spec.trigger is not None and spec.misfire_grace_time == 300
    assert Settings().severe_shadow_enabled is True


def test_context_is_point_in_time_same_company_and_newest_first() -> None:
    def item(news_id: int, title: str, hours: float, symbol: str = "601198") -> Any:
        at = FRESH + timedelta(hours=hours)
        return SimpleNamespace(
            id=news_id, symbol=symbol, title=title, published_at=at, available_time=at
        )

    target = item(10, "东兴证券股份有限公司关于公司A股股票可能终止上市的风险提示公告", 0)
    pool = [
        target,
        item(11, "关于换股吸收合并事项获得中国证监会同意注册的公告", -48),
        item(12, "关于换股吸收合并的进展公告", -2),
        item(13, "关于换股吸收合并的进展公告", -30),  # same title twice: kept once
        item(14, "关于A股股票终止上市的公告", 3),  # available after the target: never seen
        item(15, "关于换股吸收合并的进展公告", -1, symbol="600000"),  # another company
        item(16, "二〇二六年第一次临时股东会决议公告", -24 * 70),  # older than the window
    ]
    context = severe_shadow.pick_context(target, pool)
    assert [c.id for c in context] == [12, 11]
    state = severe_shadow.build_state(target.title, context)
    assert state["recent_titles_same_company"] == [
        "关于换股吸收合并的进展公告",
        "关于换股吸收合并事项获得中国证监会同意注册的公告",
    ]


def test_drift_enters_at_the_next_tradable_open_and_nets_out_the_market() -> None:
    import pandas as pd

    sessions = [date(2026, 9, d) for d in (10, 11, 14, 15, 16)]
    closes = pd.DataFrame(
        {
            "600001": [10.0, 10.0, 9.0, 8.5, 8.0],
            "600002": [20.0, 20.0, 20.0, 21.0, 22.0],
            "600009": [5.0, 5.0, 5.0, 5.0, 5.0],
        },
        index=sessions,
    )
    opens = pd.DataFrame(
        {
            "600001": [10.0, 10.0, 9.5, 9.0, 8.5],
            "600002": [20.0, 20.0, 20.0, 20.0, 21.0],
            "600009": [5.0, 5.0, 5.0, 5.0, 5.0],
        },
        index=sessions,
    )

    def record(news_id: int, symbol: str, at: datetime, rule: bool, jev: str) -> dict[str, Any]:
        return {
            "news_id": news_id,
            "symbol": symbol,
            "available_time": at.isoformat(),
            "rule": {"subtype": "investigation"} if rule else None,
            "jev": {"choice": jev},
        }

    after_close = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)  # 16:00 Shanghai, Friday
    before_open = datetime(2026, 9, 14, 0, 30, tzinfo=UTC)  # 08:30 Shanghai, Monday
    records = [
        record(1, "600001", after_close, True, "investigation"),
        record(2, "600001", after_close, True, "investigation"),  # same company, same entry
        record(3, "600002", before_open, False, "delisting_risk"),
        record(4, "600009", before_open, False, "other"),  # neither side: ignored
    ]
    result = severe_shadow.drift(records, opens, closes, horizon=3)
    both = result["both"]
    # Entry Monday 09-14 at 9.5 after a 10.0 close; exit Wednesday 09-16 close 8.0.
    assert both["events"] == 1 and both["gap_mean"] == pytest.approx(-0.05)
    market = sorted([8.0 / 9.5 - 1, 22.0 / 20.0 - 1, 0.0])[1]
    assert both["excess_median"] == pytest.approx(8.0 / 9.5 - 1 - market)
    jev_only = result["jev_only"]
    assert jev_only["events"] == 1 and jev_only["gap_mean"] == pytest.approx(0.0)
    assert result["rule_only"]["events"] == 0


def test_batches_count_asked_titles_and_stale_rows_are_crossed(
    database: Any, tmp_path: Path
) -> None:
    root = tmp_path / "shadow"
    fake = FakeJev()
    runs: list[dict[str, Any]] = []
    for _ in range(4):
        result = shadow_job.run_severe_disclosure_shadow(
            output_dir=root, client=fake, batch=2, workers=2
        )
        assert isinstance(result, dict)
        runs.append(result)
    assert [r["cursor_to"] for r in runs[:3]] == [2, 4, 5]
    assert [r["asked"] for r in runs[:3]] == [2, 2, 0]
    assert runs[2]["stale_skipped"] == 1 and runs[3]["fetched"] == 0
    assert len(fake.asked) == 4
