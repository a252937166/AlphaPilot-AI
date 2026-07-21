from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.data.base import DataProviderError
from alphapilot.db.models import Base, Security
from alphapilot.jobs import universe


def test_sync_universe_upserts_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'universe.db'}")
    Base.metadata.create_all(engine)

    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    stocks = pd.DataFrame(
        [{"code": f"{600000 + index:06d}", "name": f"样本{index}"} for index in range(100)]
        + [
            {"code": "600519", "name": "贵州茅台"},
            {"code": "300001", "name": "*ST测试"},
            {"code": "920000", "name": "安徽凤凰"},
        ]
    )
    industries = pd.DataFrame(
        [{"code": "sh.600519", "industry": "酒、饮料和精制茶制造业"}]
    )
    monkeypatch.setattr(universe, "get_session", local_session)
    monkeypatch.setattr(universe, "_load_akshare_universe", lambda: stocks)
    monkeypatch.setattr(
        universe,
        "_load_baostock_industries",
        lambda _provider: industries,
    )

    first = universe.sync_universe()
    second = universe.sync_universe()

    assert first["inserted"] == 103
    assert first["st_count"] == 1
    assert second["inserted"] == 0
    assert second["updated"] == 103

    def industry_failure(_provider: object) -> pd.DataFrame:
        raise DataProviderError("temporary industry outage")

    monkeypatch.setattr(universe, "_load_baostock_industries", industry_failure)
    degraded = universe.sync_universe()
    assert degraded["industry_count"] == 0
    assert degraded["warnings"] == ["temporary industry outage"]

    with local_session() as session:
        maotai = session.get(Security, "600519")
        assert maotai is not None
        assert maotai.industry_csrc == "酒、饮料和精制茶制造业"
        assert maotai.board == "主板"
        gem = session.get(Security, "300001")
        assert gem is not None
        assert gem.board == "创业板"
        assert gem.is_st is True
        north = session.get(Security, "920000")
        assert north is not None
        assert north.board == "北交所"
