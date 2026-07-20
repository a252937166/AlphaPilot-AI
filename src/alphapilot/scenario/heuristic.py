from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import ClassVar

import numpy as np

from alphapilot.domain.models import AgentScenarioView, ScenarioRequest, ScenarioResponse


class HeuristicScenarioEngine:
    """Deterministic local substitute for the future MiroFish finance bridge."""

    name = "local-heuristic-multi-agent-v0.1.0"
    archetypes: ClassVar[dict[str, tuple[float, float]]] = {
        "institutional_value": (0.70, 0.55),
        "institutional_growth": (1.00, 0.62),
        "trend_quant": (0.85, 0.58),
        "mean_reversion_quant": (-0.30, 0.50),
        "short_term_retail": (1.20, 0.42),
        "fundamental_retail": (0.65, 0.46),
        "sell_side_analyst": (0.80, 0.57),
        "passive_fund": (0.15, 0.70),
        "liquidity_provider": (-0.10, 0.68),
        "financial_media": (1.10, 0.38),
    }

    def run(self, request: ScenarioRequest) -> ScenarioResponse:
        rng = np.random.default_rng(request.seed)
        horizon_scale = np.sqrt(request.horizon_days / 20)
        agent_views: list[AgentScenarioView] = []
        weighted_views: list[float] = []

        for agent_type, (sensitivity, base_confidence) in self.archetypes.items():
            belief = float(
                np.clip(request.event_direction * sensitivity + rng.normal(0, 0.12), -1, 1)
            )
            expected = float(np.clip(belief * 0.06 * horizon_scale, -0.35, 0.35))
            confidence = float(np.clip(base_confidence - abs(rng.normal(0, 0.05)), 0.25, 0.85))
            intent = "BUY" if expected > 0.012 else "SELL" if expected < -0.012 else "HOLD"
            weighted_views.append(expected * confidence)
            agent_views.append(
                AgentScenarioView(
                    agent_type=agent_type,
                    belief_change=belief,
                    trade_intent=intent,
                    expected_return=expected,
                    confidence=confidence,
                    explanation=(
                        f"{agent_type} sensitivity={sensitivity:.2f}; event direction="
                        f"{request.event_direction:.2f}."
                    ),
                )
            )

        central = float(np.mean(weighted_views))
        simulation = rng.normal(loc=central, scale=0.045 * horizon_scale, size=request.runs)
        p_positive = float(np.mean(simulation > 0))
        scenario_hash = hashlib.sha256(
            f"{request.title}|{request.event_description}|{request.seed}".encode()
        ).hexdigest()[:16]

        return ScenarioResponse(
            scenario_id=f"scenario_{scenario_hash}",
            engine=self.name,
            generated_at=datetime.now(UTC),
            p_positive=p_positive,
            expected_impact=float(np.mean(simulation)),
            q10=float(np.quantile(simulation, 0.10)),
            q50=float(np.quantile(simulation, 0.50)),
            q90=float(np.quantile(simulation, 0.90)),
            dispersion=float(np.std(simulation)),
            agent_views=agent_views,
            assumptions=[
                "This local engine validates contracts only and is not a calibrated market model.",
                "No observed fact is mutated by the scenario run.",
                (
                    "A production MiroFish bridge must persist scenario_id, run_id and "
                    "source provenance."
                ),
            ],
        )
