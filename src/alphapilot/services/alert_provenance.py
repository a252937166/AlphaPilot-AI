from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isclose, isfinite

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.db.models import AlertRecord, ForecastSnapshot
from alphapilot.domain.models import TradeProposal
from alphapilot.services.alert_outcomes import BUY_ACTIONS, SELL_ACTIONS

AUDITED_FORECAST_PROVIDERS = frozenset(
    {"akshare", "baostock", "futu", "futu-close", "sina"}
)
PROVENANCE_MATCH_WINDOW_SECONDS = 5.0
SUGGESTED_NOTIONAL_TOLERANCE = 1.05


class AlertSourceError(ValueError):
    """A proposal source alert cannot be proven safe for trading."""


@dataclass(frozen=True)
class AlertProvenance:
    forecast_snapshot_id: int | None
    provider: str | None
    verified: bool


@dataclass(frozen=True)
class TradeAlertEvidence:
    alert: AlertRecord
    provenance: AlertProvenance
    max_notional: float


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_audited_forecast_provider(provider: str | None) -> bool:
    if provider is None:
        return False
    normalized = provider.strip().lower()
    if normalized in AUDITED_FORECAST_PROVIDERS:
        return True
    for prefix in ("cache-audited:", "cache-resampled:"):
        if normalized.startswith(prefix):
            sources = {item for item in normalized.removeprefix(prefix).split("+") if item}
            return bool(sources) and sources <= AUDITED_FORECAST_PROVIDERS
    return False


def alert_provenance(session: Session, alert: AlertRecord) -> AlertProvenance:
    if alert.as_of is None or not alert.model_version:
        return AlertProvenance(None, None, False)
    candidates = session.scalars(
        select(ForecastSnapshot).where(
            ForecastSnapshot.symbol == alert.symbol,
            ForecastSnapshot.as_of == alert.as_of,
            ForecastSnapshot.model_version == alert.model_version,
        )
    ).all()
    if not candidates:
        return AlertProvenance(None, None, False)
    alert_created = _as_utc(alert.created_at)
    closest = min(
        candidates,
        key=lambda item: abs((_as_utc(item.created_at) - alert_created).total_seconds()),
    )
    distance = abs((_as_utc(closest.created_at) - alert_created).total_seconds())
    if distance > PROVENANCE_MATCH_WINDOW_SECONDS:
        return AlertProvenance(None, None, False)
    return AlertProvenance(
        closest.id,
        closest.provider,
        is_audited_forecast_provider(closest.provider),
    )


def validate_trade_alert_source(
    session: Session,
    proposal: TradeProposal,
    *,
    now: datetime | None = None,
) -> TradeAlertEvidence:
    source_alert_id = proposal.source_alert_id
    if source_alert_id is None:
        raise AlertSourceError("确认交易提案必须绑定一条可审计的方向性提醒。")
    alert = session.get(AlertRecord, source_alert_id)
    if alert is None:
        raise AlertSourceError(f"来源提醒 {source_alert_id} 不存在。")

    proposal_symbol = proposal.symbol.strip().upper().removeprefix("SH.").removeprefix("SZ.")
    alert_symbol = alert.symbol.strip().upper().removeprefix("SH.").removeprefix("SZ.")
    if proposal_symbol != alert_symbol:
        raise AlertSourceError(f"来源提醒 {source_alert_id} 与提案股票不一致。")
    if alert.action in BUY_ACTIONS:
        expected_side = "BUY"
    elif alert.action in SELL_ACTIONS:
        expected_side = "SELL"
    else:
        raise AlertSourceError(
            f"来源提醒 {source_alert_id} 的动作 {alert.action} 不是可生成交易提案的方向性信号。"
        )
    if proposal.side.value != expected_side:
        raise AlertSourceError(
            f"来源提醒 {source_alert_id} 要求 {expected_side}，但提案方向为 {proposal.side.value}。"
        )

    current_time = _as_utc(now or datetime.now(UTC))
    if alert.expires_at is None or _as_utc(alert.expires_at) <= current_time:
        raise AlertSourceError(f"来源提醒 {source_alert_id} 已过期或缺少有效期。")
    provenance = alert_provenance(session, alert)
    if not provenance.verified:
        provider = provenance.provider or "无法关联预测快照"
        raise AlertSourceError(
            f"来源提醒 {source_alert_id} 的行情来源 {provider} 不可用于交易提案。"
        )
    if not isclose(proposal.confidence, alert.confidence, rel_tol=0.0, abs_tol=1e-9):
        raise AlertSourceError(f"来源提醒 {source_alert_id} 的置信度与提案不一致。")
    if not alert.model_version or proposal.model_version != alert.model_version:
        raise AlertSourceError(f"来源提醒 {source_alert_id} 的模型版本与提案不一致。")

    raw_notional = alert.suggested_notional
    signed_notional = float(raw_notional) if raw_notional is not None else 0.0
    max_notional = abs(signed_notional)
    if not isfinite(max_notional) or max_notional <= 0:
        raise AlertSourceError(f"来源提醒 {source_alert_id} 缺少有效建议金额。")
    if (expected_side == "BUY" and signed_notional < 0) or (
        expected_side == "SELL" and signed_notional > 0
    ):
        raise AlertSourceError(f"来源提醒 {source_alert_id} 的建议金额方向与动作不一致。")
    target_low = float(alert.target_low) if alert.target_low is not None else 0.0
    target_high = float(alert.target_high) if alert.target_high is not None else 0.0
    if (
        not isfinite(target_low)
        or not isfinite(target_high)
        or target_low <= 0
        or target_high <= target_low
    ):
        raise AlertSourceError(f"来源提醒 {source_alert_id} 缺少有效目标区间。")
    if proposal.estimated_notional > max_notional * SUGGESTED_NOTIONAL_TOLERANCE:
        raise AlertSourceError(
            f"提案金额超过来源提醒 {source_alert_id} 的建议金额上限。"
        )
    return TradeAlertEvidence(alert=alert, provenance=provenance, max_notional=max_notional)


__all__ = [
    "AlertProvenance",
    "AlertSourceError",
    "TradeAlertEvidence",
    "alert_provenance",
    "is_audited_forecast_provider",
    "validate_trade_alert_source",
]
