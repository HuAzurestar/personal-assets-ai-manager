from fastapi.testclient import TestClient

from app.main import app


def test_health_and_bill_flow(tmp_path, monkeypatch):
    # Application setup uses a persistent DB in normal operation; this baseline
    # test validates the public health surface independently of user data.
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        created = client.post("/api/bills", json={"occurred_at": "2026-08-25T10:00:00", "merchant": "滴滴出行", "amount": -18.5, "note": "通勤"})
        assert created.status_code == 201
        assert created.json()["category"] == "交通出行"
        assert client.get("/api/dashboard").status_code == 200
