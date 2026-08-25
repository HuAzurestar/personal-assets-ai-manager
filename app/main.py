from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import APP_DISPLAY_NAME, APP_SLUG
from app.database import AssetSnapshot, Bill, SessionLocal, init_db
from app.schemas import AssetCreate, AssetRead, BillCreate, BillRead, TagRequest, TagResult
from app.tagging import classify

APP_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=APP_DISPLAY_NAME, version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def bill_read(bill: Bill) -> BillRead:
    return BillRead(id=bill.id, occurred_at=bill.occurred_at, merchant=bill.merchant, note=bill.note, amount=bill.amount, category=bill.category, tags=[tag for tag in bill.tags.split(",") if tag])


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"app_name": APP_DISPLAY_NAME})


@app.get("/api/health")
def health():
    return {"status": "ok", "service": APP_SLUG}


@app.post("/api/tag", response_model=TagResult)
def tag_bill(payload: TagRequest):
    category, tags, provider = classify(payload.merchant, payload.note)
    return TagResult(category=category, tags=tags, provider=provider)


@app.get("/api/bills", response_model=list[BillRead])
def list_bills(db: Session = Depends(get_db)):
    return [bill_read(bill) for bill in db.scalars(select(Bill).order_by(Bill.occurred_at.desc())).all()]


@app.post("/api/bills", response_model=BillRead, status_code=201)
def create_bill(payload: BillCreate, db: Session = Depends(get_db)):
    category, tags, _ = classify(payload.merchant, payload.note)
    bill = Bill(**payload.model_dump(), category=category, tags=",".join(tags))
    db.add(bill)
    db.commit()
    db.refresh(bill)
    return bill_read(bill)


@app.delete("/api/bills/{bill_id}", status_code=204)
def delete_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    db.delete(bill)
    db.commit()


@app.get("/api/assets", response_model=list[AssetRead])
def list_assets(db: Session = Depends(get_db)):
    return [AssetRead(id=asset.id, account_name=asset.account_name, account_type=asset.account_type, balance=asset.balance, recorded_at=asset.recorded_at) for asset in db.scalars(select(AssetSnapshot).order_by(AssetSnapshot.recorded_at.desc())).all()]


@app.post("/api/assets", response_model=AssetRead, status_code=201)
def create_asset(payload: AssetCreate, db: Session = Depends(get_db)):
    asset = AssetSnapshot(**payload.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return AssetRead(id=asset.id, account_name=asset.account_name, account_type=asset.account_type, balance=asset.balance, recorded_at=asset.recorded_at)


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    total_assets = db.scalar(select(func.coalesce(func.sum(AssetSnapshot.balance), 0.0)))
    income = db.scalar(select(func.coalesce(func.sum(Bill.amount), 0.0)).where(Bill.amount > 0))
    spending = db.scalar(select(func.coalesce(func.sum(Bill.amount), 0.0)).where(Bill.amount < 0))
    return {"total_assets": total_assets, "income": income, "spending": spending, "bill_count": db.scalar(select(func.count(Bill.id))), "generated_at": datetime.now().isoformat()}
