"""Candidate J: jev client, pool, ranking, fail-closed list, tally and the weekly job."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from alphapilot.jobs import stock_pick_actions as actions
from alphapilot.jobs import stock_pick_jev as job
from alphapilot.jobs.registry import JOBS
from alphapilot.llm.typesafe import JevClient, JevError
from alphapilot.services import stock_pick_jev as jev


def _answer(noul: float, model: str = "jev-1.13.0") -> dict[str, Any]:
    return {
        "model": model,
        "answers": {"outperform_5d": {"type": "noul", "noul": noul}},
        "usage": {"input_tokens": 500, "output_tokens": 20},
    }


def test_client_retries_transient_errors_and_pins_the_model() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        assert request.headers["Authorization"] == "Bearer k"
        body = json.loads(request.content)
        assert body["model"] == "jev-1.13.0" and "outperform_5d" in body["questions"]
        return httpx.Response(529) if len(calls) == 1 else httpx.Response(200, json=_answer(0.7))

    slept: list[float] = []
    client = JevClient("k", transport=httpx.MockTransport(handler), sleep=slept.append)
    assert client.ask({"x": 1}, jev.QUESTION)["answers"]["outperform_5d"]["noul"] == 0.7
    assert len(calls) == 2 and slept == [1.0]
    wrong = JevClient(
        "k",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_answer(0.5, "jev-2"))),
    )
    with pytest.raises(JevError, match="model mismatch"):
        wrong.ask({}, jev.QUESTION)
    bad = JevClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    with pytest.raises(JevError, match="http 400"):
        bad.ask({}, jev.QUESTION)
    down = JevClient(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(503)), sleep=lambda s: None
    )
    with pytest.raises(JevError, match="after 3 attempts"):
        down.ask({}, jev.QUESTION)
    with pytest.raises(JevError, match="not configured"):
        JevClient("")


def _doc(
    name: str, order: list[str], week: str = "2026-W39", as_of: str = "2026-09-24"
) -> dict[str, Any]:
    members = [
        {"symbol": s, "rank": i + 1, "score": 1 - i / 100, "decile": 9, "close": 10.0}
        for i, s in enumerate(order)
    ]
    return {
        "candidate": name,
        "as_of": as_of,
        "iso_week": week,
        "universe_n": 100,
        "members": members,
        "top20": members[:20],
        "top_decile_symbols": order[:10],
        "top_decile_n": 10,
    }


A_ORDER = [f"6000{i:02d}" for i in range(40)]
B_ORDER = [f"6000{i:02d}" for i in range(20, 60)]


def test_pool_is_the_union_of_both_top_30s_in_order() -> None:
    pool = jev.pool_from_lists(_doc("A", A_ORDER), _doc("B", B_ORDER))
    assert pool[:30] == A_ORDER[:30] and pool[30:] == [f"6000{i:02d}" for i in range(30, 50)]
    assert len(pool) == 50 and len(set(pool)) == 50


def _states(pool: list[str]) -> dict[str, dict[str, Any]]:
    return {
        s: {"stock": {"symbol": s, "name": f"股{s[-2:]}", "industry": "C39", "close": 10.0}}
        for s in pool
    }


def test_list_ranks_by_probability_and_fails_closed() -> None:
    pool = jev.pool_from_lists(_doc("A", A_ORDER), _doc("B", B_ORDER))
    answers = {
        s: {"noul": (i % 7) / 10, "usage": {"input_tokens": 500}} for i, s in enumerate(pool)
    }
    doc = jev.build_j_list(
        _doc("A", A_ORDER),
        _doc("B", B_ORDER),
        _states(pool),
        answers,
        model="jev-1.13.0",
        generated_at=datetime(2026, 9, 26, 1, 45, tzinfo=UTC),
    )
    probs = [m["score"] for m in doc["members"]]
    assert probs == sorted(probs, reverse=True) and doc["top_decile_n"] == 20
    assert doc["top_decile_symbols"] == [m["symbol"] for m in doc["members"][:20]]
    assert (
        doc["iso_week"] == "2026-W39"
        and doc["candidate"] == "J"
        and doc["frozen_by"]["amendment"].startswith("forward-test-amendment-A2")
    )
    for m in doc["members"]:
        assert {"symbol", "rank", "score", "decile", "close"} <= set(m)
    few = dict(answers)
    for s in pool[:6]:  # 6 of 50 missing = 12% > 10%
        few[s] = {"error": "JevError: timeout"}
    with pytest.raises(RuntimeError, match="not written"):
        jev.build_j_list(
            _doc("A", A_ORDER),
            _doc("B", B_ORDER),
            _states(pool),
            few,
            model="jev-1.13.0",
            generated_at=datetime(2026, 9, 26, tzinfo=UTC),
        )
    for s in pool[:5]:  # 10% missing is still allowed
        few[s] = answers[s]
    few[pool[5]] = {"error": "x"}
    ok = jev.build_j_list(
        _doc("A", A_ORDER),
        _doc("B", B_ORDER),
        _states(pool),
        few,
        model="jev-1.13.0",
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    assert ok["missing"] == {pool[5]: "x"} and ok["universe_n"] == 49


def test_tally_uses_the_twenty_picks() -> None:
    scores = [
        {
            "as_of": f"2026-10-{10 + i:02d}",
            "list_top_decile": {"hit_rate": 0.6, "mean_excess": 0.01 + 0.001 * (i % 2)},
            "top_bin": {"hit_rate": 0.0, "mean_excess": -1.0},
        }
        for i in range(8)
    ]
    tally = jev.j_tally(scores)
    assert tally["status"] == "confirmed" and tally["mean_hit_rate"] == pytest.approx(0.6)


class _FakeJev:
    model = "jev-1.13.0"

    def __init__(self) -> None:
        self.asked: list[str] = []

    def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        symbol = state["stock"]["symbol"]
        self.asked.append(symbol)
        return _answer(int(symbol[-2:]) / 100)

    def close(self) -> None:
        pass


def test_weekly_job_builds_once_and_only_from_week_39(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "picks"
    for name, order in (("A", A_ORDER), ("B", B_ORDER)):
        for week, as_of in (("2026-W38", "2026-09-18"), ("2026-W39", "2026-09-24")):
            path = root / "lists" / name / f"{name}-{week}-{as_of.replace('-', '')}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_doc(name, order, week, as_of)), encoding="utf-8")
    monkeypatch.setattr(job, "build_states", lambda session, as_of, pool, a, b: _states(pool))
    fake = _FakeJev()
    saturday = datetime(2026, 9, 26, 1, 45, tzinfo=UTC)
    first = job.run_stock_pick_jev(now=saturday, output_dir=root, client=fake)
    assert (
        first["generated"]["status"] == "written" and first["generated"]["iso_week"] == "2026-W39"
    )
    assert first["generated"]["pool"] == 50 and first["generated"]["input_tokens"] == 50 * 500
    written = json.loads(Path(first["generated"]["path"]).read_bytes())
    assert written["top_decile_symbols"][0] == "600049"  # highest fake probability
    again = job.run_stock_pick_jev(now=saturday, output_dir=root, client=fake)
    assert (
        again["generated"] == {"status": "exists", "iso_week": "2026-W39"} and len(fake.asked) == 50
    )
    weekday = job.run_stock_pick_jev(
        now=datetime(2026, 9, 28, 12, 5, tzinfo=UTC), output_dir=root, client=fake
    )
    assert weekday["generated"] is None and weekday["tally"]["status"] == "pending"
    assert not list((root / "lists" / "J").glob("J-2026-W38-*.json"))  # before the forward window
    assert "J" in actions._lists(root)


def test_job_is_registered() -> None:
    from alphapilot.jobs import register_builtin_jobs

    register_builtin_jobs()
    spec = JOBS[job.JOB_NAME]
    assert spec.enabled_key == "stock_pick_jev_enabled"
    assert "hour='9', minute='45'" in str(spec.trigger) and "hour='20', minute='5'" in str(
        spec.trigger
    )
