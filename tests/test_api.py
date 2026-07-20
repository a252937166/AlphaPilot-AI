from fastapi.testclient import TestClient

from alphapilot.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["trading"]["order_submission_endpoint_exposed"] is False


def test_mock_screen_api() -> None:
    response = client.post(
        "/v1/screens/run",
        json={"symbols": ["600000", "000001", "000333"], "top_n": 2, "provider": "mock"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["candidates"]) == 2
