from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from alphapilot.cninfo.client import CninfoClient, CninfoError
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import Disclosure, DomainEvent, Security
from alphapilot.services.event_extract import (
    DisclosureExtraction,
    classify_disclosure,
    persist_disclosure_event,
)
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
    # safe_sync_symbol may already have a dirty Security row. Do not autoflush
    # it or acquire SQLite's writer lock before the first LLM network wait.
    with session.no_autoflush:
        existing_by_url = {
            row.url: row
            for row in session.scalars(
                select(Disclosure).where(Disclosure.symbol == code)
            ).all()
        }
        existing_event_refs = set(
            session.scalars(
                select(DomainEvent.source_ref).where(
                    DomainEvent.symbol == code,
                    DomainEvent.source_ref.like("disclosure:%"),
                )
            ).all()
        )
    inserted = 0
    events_extracted = 0
    fallback_events = 0
    new_disclosures: list[Disclosure] = []
    pending_disclosures: list[Disclosure] = []
    pending_urls: set[str] = set()
    for item in fetched:
        disclosure = existing_by_url.get(item["url"])
        is_new = disclosure is None
        if disclosure is None:
            disclosure = Disclosure(
                symbol=code,
                title=item["title"],
                url=item["url"],
                category=item.get("category"),
                published_at=item.get("published_at"),
                source="cninfo",
            )
            new_disclosures.append(disclosure)
            existing_by_url[disclosure.url] = disclosure
        source_ref = (
            f"disclosure:{disclosure.id}" if disclosure.id is not None else None
        )
        # Preserve the P2.1 auto-heal contract for a previously stored
        # announcement whose event row disappeared, without re-running the LLM
        # for healthy announcements on every sync.
        if (
            (is_new or source_ref not in existing_event_refs)
            and disclosure.url not in pending_urls
        ):
            pending_disclosures.append(disclosure)
            pending_urls.add(disclosure.url)

    # Finish every network/audit phase before the caller session performs its
    # first disclosure/event write. A batch with two new announcements can
    # therefore never hold SQLite's writer lock during the second LLM wait.
    classified: list[tuple[Disclosure, DisclosureExtraction]] = [
        (disclosure, classify_disclosure(disclosure.title))
        for disclosure in pending_disclosures
    ]

    if session.get_bind().dialect.name == "sqlite":
        for disclosure in new_disclosures:
            statement = (
                sqlite_insert(Disclosure)
                .values(
                    symbol=disclosure.symbol,
                    title=disclosure.title,
                    url=disclosure.url,
                    category=disclosure.category,
                    published_at=disclosure.published_at,
                    source=disclosure.source,
                )
                .on_conflict_do_nothing(index_elements=["symbol", "url"])
            )
            result = session.execute(statement)
            rowcount = getattr(result, "rowcount", 0)
            if isinstance(rowcount, int):
                inserted += max(0, rowcount)
        session.flush()
    else:
        session.add_all(new_disclosures)
        inserted = len(new_disclosures)

    for disclosure, extraction in classified:
        stored_disclosure = disclosure
        if disclosure.id is None and session.get_bind().dialect.name == "sqlite":
            stored_disclosure = session.scalar(
                select(Disclosure)
                .where(
                    Disclosure.symbol == disclosure.symbol,
                    Disclosure.url == disclosure.url,
                )
                .limit(1)
            )
            if stored_disclosure is None:
                raise RuntimeError("公告原子写入失败")
        event = persist_disclosure_event(session, stored_disclosure, extraction)
        if event is not None:
            if event.source_ref is not None:
                existing_event_refs.add(event.source_ref)
            events_extracted += 1
            fallback_events += int(extraction.source == "rule")
    return {
        "symbol": code,
        "fetched": len(fetched),
        "inserted": inserted,
        "events_extracted": events_extracted,
        "fallback_events": fallback_events,
    }


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
