from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.db.models import Base, Security
from alphapilot.main import app


def test_industries_returns_trimmed_sorted_distinct_real_values(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'meta.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Security(symbol="600001", industry_csrc="制造业", list_status="listed"),
                Security(symbol="600002", industry_csrc=" 制造业 ", list_status="listed"),
                Security(symbol="600003", industry_csrc="金融业", list_status="listed"),
                Security(symbol="600004", industry_csrc="", list_status="listed"),
                Security(symbol="600005", industry_csrc="   ", list_status="listed"),
                Security(symbol="600006", industry_csrc=None, list_status="listed"),
                Security(symbol="600007", industry_csrc="退市行业", list_status="delisted"),
            ]
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        with TestClient(app) as client:
            response = client.get("/v1/meta/industries")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    assert response.json() == {
        "count": 2,
        "industries": ["制造业", "金融业"],
    }
