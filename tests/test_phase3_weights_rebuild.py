from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.backtest import weights_rebuild
from alphapilot.backtest.weights_rebuild import rebuild_weights, train_test_split
from alphapilot.db.models import Base, DailyBar
from alphapilot.engines.factors import FACTOR_SET


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'weights-rebuild.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _bar(trade_date: date) -> DailyBar:
    return DailyBar(
        symbol="SH.000001",
        trade_date=trade_date,
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=100.0,
        amount=10_000.0,
        source="baostock",
    )


def test_train_test_split_is_chronological_disjoint_210_91(
    tmp_path: Path,
) -> None:
    dates = [stamp.date() for stamp in pd.bdate_range("2025-01-01", periods=301)]
    session = _session(tmp_path)
    try:
        session.add_all(_bar(trade_day) for trade_day in dates)
        session.commit()

        train_start, train_end, test_start, test_end = train_test_split(session)

        assert (train_start, train_end) == (dates[0], dates[209])
        assert (test_start, test_end) == (dates[210], dates[300])
        assert train_end < test_start
        assert len([day for day in dates if train_start <= day <= train_end]) == 210
        assert len([day for day in dates if test_start <= day <= test_end]) == 91
    finally:
        session.close()


def test_rebuild_weights_uses_only_train_once_and_signed_l1_formula(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path)
    train_start = date(2025, 4, 28)
    train_end = date(2026, 2, 27)
    forbidden_test_start = date(2026, 3, 2)
    calls: list[tuple[str, date, date]] = []

    rows = [
        {
            "factor": factor,
            "ic_mean": None,
            "ic_ir": None,
            "t_stat": None,
            "n_periods": 0,
        }
        for factor in FACTOR_SET
    ]
    by_factor = {str(row["factor"]): row for row in rows}
    by_factor["momentum_20d"].update(
        ic_mean=-0.20,
        ic_ir=-2.00,
        t_stat=-2.50,
        n_periods=10,
    )
    by_factor["momentum_60d"].update(
        ic_mean=-0.10,
        ic_ir=-1.00,
        t_stat=-2.10,
        n_periods=10,
    )
    by_factor["volatility_20d"].update(
        ic_mean=0.05,
        ic_ir=0.50,
        t_stat=1.00,
        n_periods=10,
    )

    def fake_ic(
        _session: Session,
        start: date,
        end: date,
        *,
        sample_tag: str,
    ) -> pd.DataFrame:
        calls.append((f"ic:{sample_tag}", start, end))
        assert end < forbidden_test_start
        return pd.DataFrame(rows)

    def fake_corr(
        _session: Session,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        calls.append(("corr", start, end))
        assert end < forbidden_test_start
        corr = pd.DataFrame(
            float("nan"),
            index=FACTOR_SET,
            columns=FACTOR_SET,
        )
        for factor in FACTOR_SET:
            corr.at[factor, factor] = 1.0
        corr.at["momentum_20d", "momentum_60d"] = 0.90
        corr.at["momentum_60d", "momentum_20d"] = 0.90
        return corr

    monkeypatch.setattr(weights_rebuild, "all_factors_ic", fake_ic)
    monkeypatch.setattr(weights_rebuild, "factor_correlation", fake_corr)
    output = tmp_path / "factor_weights_v2.yaml"
    try:
        result = rebuild_weights(
            session,
            train_start,
            train_end,
            output_path=output,
        )
        payload = yaml.safe_load(output.read_text(encoding="utf-8"))

        assert calls == [
            ("ic:train", train_start, train_end),
            ("corr", train_start, train_end),
        ]
        assert result["weights"]["momentum_20d"] == pytest.approx(-0.8)
        assert result["weights"]["momentum_60d"] == 0.0
        assert result["weights"]["volatility_20d"] == pytest.approx(0.2)
        assert sum(abs(value) for value in result["weights"].values()) == (
            pytest.approx(1.0)
        )
        assert payload["method"] == "signed_train_ic_ir_l1"
        assert payload["train_window"] == {
            "start": train_start.isoformat(),
            "end": train_end.isoformat(),
        }
        assert payload["factor_ic_ir"]["momentum_20d"] == -2.0
        assert payload["weights"] == result["weights"]
    finally:
        session.close()
