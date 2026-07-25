from __future__ import annotations

import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.backtest.factor_research import (
    all_factors_ic,
    classify_factors,
    factor_correlation,
)
from alphapilot.backtest.factor_scope import (
    HISTORICAL_FACTOR_CANDIDATES,
    HISTORY_EXCLUDED_PIT_GAP_FACTORS,
    LIVE_ONLY_FACTORS,
)
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.models import DailyBar
from alphapilot.engines.factors import FACTOR_SET

_RESEARCH_SESSION_COUNT = 301
_DEFAULT_OUTPUT = Path(__file__).resolve().parents[3] / "config" / "factor_weights_v2.yaml"


def _finite(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _research_calendar(session: Session) -> list[date]:
    descending = list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES))
            .distinct()
            .order_by(DailyBar.trade_date.desc())
            .limit(_RESEARCH_SESSION_COUNT)
        )
    )
    return sorted(descending)


def train_test_split(
    session: Session,
    ratio: float = 0.7,
) -> tuple[date, date, date, date]:
    """Split the fixed 301-session M1 window into disjoint train and test dates."""

    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise ValueError("ratio must be a finite number within (0, 1)")
    resolved_ratio = float(ratio)
    if not math.isfinite(resolved_ratio) or not 0 < resolved_ratio < 1:
        raise ValueError("ratio must be a finite number within (0, 1)")
    calendar = _research_calendar(session)
    if len(calendar) != _RESEARCH_SESSION_COUNT:
        raise ValueError(
            f"需要 {_RESEARCH_SESSION_COUNT} 个审计交易日，当前只有 {len(calendar)} 个。"
        )
    train_size = int(len(calendar) * resolved_ratio)
    if train_size < 2 or len(calendar) - train_size < 2:
        raise ValueError("train/test partitions must each contain at least two sessions")
    return (
        calendar[0],
        calendar[train_size - 1],
        calendar[train_size],
        calendar[-1],
    )


def _ic_ir_mapping(table: pd.DataFrame) -> dict[str, float | None]:
    rows = {
        str(row["factor"]): _finite(row.get("ic_ir"))
        for row in table.to_dict(orient="records")
    }
    return {factor: rows.get(factor) for factor in FACTOR_SET}


def _normalized_weights(
    factor_ic_ir: dict[str, float | None],
    diagnosis: dict[str, Any],
) -> dict[str, float]:
    factor_diagnosis = diagnosis["factors"]
    retained_ir: dict[str, float] = {}
    for factor in FACTOR_SET:
        value = factor_ic_ir[factor]
        redundant = bool(factor_diagnosis[factor]["redundant"])
        retained_ir[factor] = 0.0 if value is None or redundant else value
    denominator = sum(abs(value) for value in retained_ir.values())
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError("train 窗没有可用的非零 IC_IR，禁止生成 composite-v2 权重。")
    weights = {
        factor: retained_ir[factor] / denominator
        for factor in FACTOR_SET
    }
    excluded = (*HISTORY_EXCLUDED_PIT_GAP_FACTORS, *LIVE_ONLY_FACTORS)
    if any(weights[factor] != 0.0 for factor in excluded):
        raise RuntimeError("historically excluded factors must retain zero weight")
    if not math.isclose(sum(abs(value) for value in weights.values()), 1.0):
        raise RuntimeError("factor_weights_v2 L1 normalization failed")
    return weights


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
    )
    path.write_text(rendered, encoding="utf-8")


def rebuild_weights(
    session: Session,
    train_start: date,
    train_end: date,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate one signed train-only IC_IR weighting without test-window reads."""

    if train_end < train_start:
        raise ValueError("train_end must not be earlier than train_start")
    ic_table = all_factors_ic(
        session,
        train_start,
        train_end,
        sample_tag="train",
    )
    corr = factor_correlation(session, train_start, train_end)
    diagnosis = classify_factors(ic_table, corr)
    factor_ic_ir = _ic_ir_mapping(ic_table)
    weights = _normalized_weights(factor_ic_ir, diagnosis)
    generated_at = datetime.now(UTC).isoformat()
    target = Path(output_path) if output_path is not None else _DEFAULT_OUTPUT
    payload: dict[str, Any] = {
        "version": "v2.0.0",
        "profile": "train_ic_ir_once",
        "signal_id": "composite-v2",
        "method": "signed_train_ic_ir_l1",
        "train_window": {
            "start": train_start.isoformat(),
            "end": train_end.isoformat(),
        },
        "generated_at": generated_at,
        "horizon": "20d",
        "rebalance_freq": "20d",
        "correlation_threshold": 0.8,
        "historical_factor_candidates": list(HISTORICAL_FACTOR_CANDIDATES),
        "history_excluded_pit_gap": list(HISTORY_EXCLUDED_PIT_GAP_FACTORS),
        "live_only_factors": list(LIVE_ONLY_FACTORS),
        "factor_ic_ir": factor_ic_ir,
        "redundancy_groups": diagnosis["redundancy_groups"],
        "weights": weights,
    }
    _write_yaml(target, payload)
    return {
        "weights": weights,
        "train_window": payload["train_window"],
        "factor_ic_ir": factor_ic_ir,
        "redundancy_groups": diagnosis["redundancy_groups"],
        "method": payload["method"],
        "generated_at": generated_at,
        "output_path": str(target),
    }
