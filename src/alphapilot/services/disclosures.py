from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.cninfo.client import CninfoClient, CninfoError
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import Disclosure, Security
from alphapilot.services.events import emit
from alphapilot.services.watchlist import normalize_symbol


def sync_disclosures(
    session: Session,
    client: CninfoClient,
    symbol: str,
    *,
    days: int = 45,
) -> dict[str, Any]:
    """Pull recent announcements from cninfo and upsert them by (symbol, url)."""
    code = normalize_symbol(symbol)
    end = date.today()
    start = end - timedelta(days=days)
    fetched = client.announcements(code, start, end)
    existing_by_url = {
        row.url: row
        for row in session.scalars(
            select(Disclosure).where(Disclosure.symbol == code)
        ).all()
    }
    inserted = 0
    for item in fetched:
        disclosure = existing_by_url.get(item["url"])
        if disclosure is None:
            disclosure = Disclosure(
                symbol=code,
                title=item["title"],
                url=item["url"],
                category=item.get("category"),
                published_at=item.get("published_at"),
                source="cninfo",
            )
            session.add(disclosure)
            session.flush()
            existing_by_url[disclosure.url] = disclosure
            inserted += 1
        emit(
            session,
            symbol=code,
            event_type="disclosure",
            title=disclosure.title,
            direction=0.0,
            strength=0.5,
            summary=disclosure.category,
            source_ref=f"disclosure:{disclosure.id}",
            occurred_at=disclosure.published_at or disclosure.ingested_at,
        )
    return {"symbol": code, "fetched": len(fetched), "inserted": inserted}


def list_disclosures(
    session: Session,
    symbol: str | None = None,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = select(Disclosure).order_by(Disclosure.published_at.desc()).limit(limit)
    if symbol:
        query = query.where(Disclosure.symbol == normalize_symbol(symbol))
    rows = session.scalars(query).all()
    return [
        {
            "id": row.id,
            "symbol": row.symbol,
            "title": row.title,
            "url": row.url,
            "category": row.category,
            "published_at": iso_utc(row.published_at),
            "source": row.source,
        }
        for row in rows
    ]


def enrich_security(
    session: Session,
    client: CninfoClient,
    symbol: str,
) -> Security:
    """Refresh the security-master row from the cninfo company profile."""
    code = normalize_symbol(symbol)
    profile = client.stock_profile(code)
    security = session.get(Security, code)
    if security is None:
        security = Security(symbol=code)
        session.add(security)
    security.name = profile.get("name") or security.name
    security.board = profile.get("board") or security.board
    security.listed_date = profile.get("listed_date") or security.listed_date
    security.status = profile.get("status") or security.status
    security.profile = profile.get("raw") or {}
    security.updated_at = datetime.now(UTC)
    return security


def get_security(session: Session, symbol: str) -> dict[str, Any] | None:
    row = session.get(Security, normalize_symbol(symbol))
    if row is None:
        return None
    return {
        "symbol": row.symbol,
        "name": row.name,
        "board": row.board,
        "industry": row.industry,
        "industry_csrc": row.industry_csrc,
        "industry_futu": row.industry_futu,
        "is_st": row.is_st,
        "list_status": row.list_status,
        "market_cap": row.market_cap,
        "float_cap": row.float_cap,
        "pe_ttm": row.pe_ttm,
        "pb": row.pb,
        "turnover_rate": row.turnover_rate,
        "snapshot_at": iso_utc(row.snapshot_at),
        "listed_date": row.listed_date,
        "status": row.status,
        "updated_at": iso_utc(row.updated_at),
    }


def safe_sync_symbol(
    session: Session,
    client: CninfoClient,
    symbol: str,
) -> dict[str, Any]:
    """Best-effort profile + announcement sync; never raises to the caller."""
    result: dict[str, Any] = {"symbol": normalize_symbol(symbol)}
    try:
        enrich_security(session, client, symbol)
        result["profile"] = "ok"
    except CninfoError as exc:
        result["profile"] = f"error: {exc}"
    try:
        sync = sync_disclosures(session, client, symbol)
        result["disclosures"] = sync
    except CninfoError as exc:
        result["disclosures"] = f"error: {exc}"
    return result
