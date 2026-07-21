from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import SectorSnapshot
from alphapilot.futu.client import FutuClient, FutuClientError

# get_plate_stock allows 10 calls per 30 seconds, so constituents are cached
# per process and only the single snapshot request repeats on refresh.
MAX_PLATES = 10
MAX_CONSTITUENTS_PER_PLATE = 30
SNAPSHOT_LIMIT = 400
_plate_cache_lock = Lock()
_plate_constituents: dict[str, dict[str, Any]] = {}


class SectorServiceError(RuntimeError):
    pass


def _load_plate_constituents(client: FutuClient) -> dict[str, dict[str, Any]]:
    with _plate_cache_lock:
        if _plate_constituents:
            return _plate_constituents
        plates = client.quote_call_raw("get_plate_list", args=["SH", "INDUSTRY"])
        if not isinstance(plates, pd.DataFrame) or plates.empty:
            raise SectorServiceError("Futu returned no industry plate list.")
        selected = plates.head(MAX_PLATES)
        for record in selected.to_dict(orient="records"):
            plate_code = str(record.get("code"))
            plate_name = str(record.get("plate_name"))
            try:
                stocks = client.quote_call_raw("get_plate_stock", args=[plate_code])
            except FutuClientError:
                continue
            if not isinstance(stocks, pd.DataFrame) or stocks.empty:
                continue
            constituents = [
                {"code": str(row.get("code")), "name": str(row.get("stock_name"))}
                for row in stocks.head(MAX_CONSTITUENTS_PER_PLATE).to_dict(orient="records")
            ]
            _plate_constituents[plate_code] = {
                "plate_name": plate_name,
                "constituents": constituents,
            }
        if not _plate_constituents:
            raise SectorServiceError("No plate constituents could be loaded from Futu.")
        return _plate_constituents


def _sample_snapshot(client: FutuClient, codes: list[str]) -> pd.DataFrame:
    """One snapshot request with change_pct derived from prev_close_price."""
    snapshot = client.quote_call_raw("get_market_snapshot", args=[codes])
    if not isinstance(snapshot, pd.DataFrame) or snapshot.empty:
        raise SectorServiceError("Futu snapshot returned no rows for sampling.")
    snapshot = snapshot.copy()
    if "prev_close_price" in snapshot.columns:
        last = pd.to_numeric(snapshot["last_price"], errors="coerce")
        prev_close = pd.to_numeric(snapshot["prev_close_price"], errors="coerce")
        snapshot["change_pct"] = (last / prev_close - 1) * 100
    else:
        snapshot["change_pct"] = 0.0
    return snapshot


def compute_sector_strength(client: FutuClient) -> list[dict[str, Any]]:
    """Sampled sector strength: average constituent move, breadth and turnover.

    This is an observable heuristic ranking, not a calibrated forecast; the
    sampling caps keep every refresh within one Futu snapshot request.
    """
    plates = _load_plate_constituents(client)
    all_codes: list[str] = []
    for info in plates.values():
        all_codes.extend(item["code"] for item in info["constituents"])
    all_codes = list(dict.fromkeys(all_codes))[:SNAPSHOT_LIMIT]

    snapshot = _sample_snapshot(client, all_codes)
    quotes = {
        str(row.get("code")): row
        for row in snapshot.to_dict(orient="records")
    }

    results: list[dict[str, Any]] = []
    for plate_code, info in plates.items():
        rows = [quotes[item["code"]] for item in info["constituents"] if item["code"] in quotes]
        if not rows:
            continue
        changes = [float(row.get("change_pct") or 0.0) for row in rows]
        turnovers = [float(row.get("turnover") or 0.0) for row in rows]
        up_count = sum(1 for value in changes if value > 0)
        leader = max(rows, key=lambda row: float(row.get("change_pct") or 0.0))
        avg_change = sum(changes) / len(changes)
        breadth = up_count / len(changes)
        # 0-10 heuristic strength blending move and breadth.
        strength = max(0.0, min(10.0, 5 + avg_change * 1.2 + (breadth - 0.5) * 4))
        results.append(
            {
                "plate_code": plate_code,
                "plate_name": info["plate_name"],
                "sampled": len(rows),
                "avg_change_pct": round(avg_change, 3),
                "up_ratio": round(breadth, 3),
                "turnover": sum(turnovers),
                "strength": round(strength, 2),
                "leader_code": str(leader.get("code")),
                "leader_name": str(leader.get("name") or leader.get("stock_name") or ""),
                "leader_change_pct": round(float(leader.get("change_pct") or 0.0), 3),
            }
        )
    results.sort(key=lambda item: item["strength"], reverse=True)
    for rank, item in enumerate(results, 1):
        item["rank"] = rank
    return results


def market_breadth_from_sample(client: FutuClient) -> dict[str, Any]:
    """Advance/decline breadth computed over the sector sample universe."""
    plates = _load_plate_constituents(client)
    codes: list[str] = []
    for info in plates.values():
        codes.extend(item["code"] for item in info["constituents"])
    codes = list(dict.fromkeys(codes))[:SNAPSHOT_LIMIT]
    snapshot = _sample_snapshot(client, codes)
    changes = pd.to_numeric(snapshot["change_pct"], errors="coerce").dropna()
    return {
        "sample_size": len(changes),
        "advancers": int((changes > 0).sum()),
        "decliners": int((changes < 0).sum()),
        "unchanged": int((changes == 0).sum()),
        "avg_change_pct": round(float(changes.mean()), 3),
        "note": "样本宽度：基于板块抽样股票池，非全市场统计。",
    }


def get_sector_strength(
    session: Session,
    client: FutuClient,
    *,
    max_age_seconds: int = 150,
    refresh: bool = False,
) -> dict[str, Any]:
    latest = session.scalars(
        select(SectorSnapshot).order_by(SectorSnapshot.as_of.desc()).limit(1)
    ).first()
    now = datetime.now(UTC)
    if (
        latest is not None
        and not refresh
        and latest.as_of.replace(tzinfo=latest.as_of.tzinfo or UTC)
        > now - timedelta(seconds=max_age_seconds)
    ):
        return {"as_of": iso_utc(latest.as_of), "cached": True, "sectors": latest.payload}

    try:
        sectors = compute_sector_strength(client)
    except (FutuClientError, SectorServiceError) as exc:
        if latest is not None:
            return {
                "as_of": iso_utc(latest.as_of),
                "cached": True,
                "stale": True,
                "error": str(exc),
                "sectors": latest.payload,
            }
        raise SectorServiceError(f"Sector strength unavailable: {exc}") from exc

    session.add(SectorSnapshot(as_of=now, payload=sectors, source="futu"))
    return {"as_of": now.isoformat(), "cached": False, "sectors": sectors}
