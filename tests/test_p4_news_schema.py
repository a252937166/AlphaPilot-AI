from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alphapilot.db.migrate import run_migrations
from alphapilot.db.models import Base, NewsItem


def _news_item(
    *,
    url: str = "https://example.test/news/1",
    content_hash: str = "a" * 64,
) -> NewsItem:
    return NewsItem(
        source="fixture",
        symbol=None,
        title="全市场资讯",
        url=url,
        published_at=None,
        available_time=datetime(2026, 8, 3, 1, 30, tzinfo=UTC),
        content_hash=content_hash,
        raw_payload={"source_id": "fixture-1"},
    )


def test_news_items_table_migration_is_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'news-migration.db'}")
    Base.metadata.create_all(engine)
    NewsItem.__table__.drop(engine)

    assert run_migrations(engine) == ["news_items"]
    assert run_migrations(engine) == []

    inspector = inspect(engine)
    columns = {str(item["name"]): item for item in inspector.get_columns("news_items")}
    assert set(columns) == {
        "id",
        "source",
        "symbol",
        "title",
        "url",
        "published_at",
        "available_time",
        "content_hash",
        "raw_payload",
    }
    assert columns["symbol"]["nullable"] is True
    assert columns["published_at"]["nullable"] is True
    for name in ("source", "title", "url", "available_time", "content_hash", "raw_payload"):
        assert columns[name]["nullable"] is False

    unique_constraints = {
        str(item["name"]): tuple(str(column) for column in item["column_names"])
        for item in inspector.get_unique_constraints("news_items")
    }
    assert unique_constraints["uq_news_items_url"] == ("url",)
    assert unique_constraints["uq_news_items_content_hash"] == ("content_hash",)

    indexes = {
        str(item["name"]): tuple(str(column) for column in item["column_names"])
        for item in inspector.get_indexes("news_items")
    }
    assert indexes["ix_news_items_available_time_id"] == ("available_time", "id")


def test_news_item_allows_market_wide_and_unknown_publication_time(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'news-nullable.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(_news_item())
        session.commit()
        stored = session.scalar(select(NewsItem))

    assert stored is not None
    assert stored.symbol is None
    assert stored.published_at is None
    assert stored.raw_payload == {"source_id": "fixture-1"}

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO news_items "
                "(source, symbol, title, url, published_at, available_time, "
                "content_hash, raw_payload) VALUES "
                "('fixture', NULL, 'missing PIT', 'https://example.test/news/null-pit', "
                "NULL, NULL, :content_hash, '{}')"
            ),
            {"content_hash": "b" * 64},
        )


def test_news_items_enforce_url_and_content_hash_as_independent_unique_keys(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'news-dedupe.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(_news_item())
        session.commit()

        session.add(_news_item(content_hash="b" * 64))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            _news_item(
                url="https://example.test/news/2",
                content_hash="a" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            _news_item(
                url="https://example.test/news/3",
                content_hash="c" * 64,
            )
        )
        session.commit()
        count = session.scalar(select(func.count()).select_from(NewsItem))

    assert count == 2
