from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.main import app, get_db


def test_health_and_bill_flow(tmp_path, monkeypatch):
    # Application setup uses a persistent DB in normal operation; this baseline
    # test validates the public health surface independently of user data.
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/health").json()["service"] == "personal-assets-ai-manager"
        created = client.post("/api/bills", json={"occurred_at": "2026-08-25T10:00:00", "merchant": "滴滴出行", "amount": -18.5, "note": "通勤"})
        assert created.status_code == 201
        assert created.json()["category"] == "交通出行"
        assert client.get("/api/dashboard").status_code == 200


def test_import_tag_audit_and_transfer_review(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            csv_body = "交易时间,交易对方,商品,收/支,金额(元),交易单号\n2026-08-25 12:00:00,测试商户,午餐,支出,20.00,wx-001\n".encode()
            imported = client.post("/api/imports/wechat?filename=wechat.csv", content=csv_body)
            assert imported.status_code == 201
            assert imported.json()["imported_count"] == 1
            bill = next(item for item in client.get("/api/bills").json() if item["source_reference"] == "wx-001")
            manual = client.post(f"/api/bills/{bill['id']}/tags", json={"strategy": "manual", "category": "餐饮", "tags": ["消费", "午餐"]})
            assert manual.status_code == 201
            assert manual.json()["confidence"] == 0.95
            history = client.get(f"/api/bills/{bill['id']}/tags").json()
            assert len(history) == 2

            first = client.post("/api/bills", json={"occurred_at": "2026-08-25T13:00:00", "merchant": "账户转出", "amount": -100, "note": "测试转账"})
            second = client.post("/api/bills", json={"occurred_at": "2026-08-25T13:01:00", "merchant": "账户转入", "amount": 100, "note": "测试转账"})
            assert first.status_code == second.status_code == 201
            candidates = client.get("/api/candidates").json()
            transfer = next(item for item in candidates if item["candidate_type"] == "transfer" and item["status"] == "pending")
            decided = client.post(f"/api/candidates/{transfer['id']}", json={"status": "confirmed"})
            assert decided.status_code == 200
            assert decided.json()["status"] == "confirmed"
            dashboard = client.get("/api/dashboard").json()
            assert dashboard["income"] == 0
            assert dashboard["spending"] == -20
    finally:
        app.dependency_overrides.clear()


def test_provider_icon_assets_are_local_and_referenced():
    with TestClient(app) as client:
        for path in ("/static/providers/alipay.svg", "/static/providers/wechat.svg"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("image/svg+xml")
        workbench = client.get("/static/workbench.js").text
        assert 'src="/static/providers/alipay.svg"' in workbench
        assert 'src="/static/providers/wechat.svg"' in workbench
        assert 'aria-label="导入支付宝账单 CSV 文件"' in workbench
        assert 'aria-label="导入微信账单 CSV 文件"' in workbench
