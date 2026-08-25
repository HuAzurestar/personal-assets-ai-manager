from datetime import datetime

from pydantic import BaseModel, Field


class BillCreate(BaseModel):
    occurred_at: datetime
    merchant: str = Field(min_length=1, max_length=200)
    note: str = ""
    amount: float


class BillRead(BillCreate):
    id: int
    category: str
    tags: list[str]


class AssetCreate(BaseModel):
    account_name: str = Field(min_length=1, max_length=120)
    account_type: str = Field(min_length=1, max_length=80)
    balance: float
    recorded_at: datetime


class AssetRead(AssetCreate):
    id: int


class TagRequest(BaseModel):
    merchant: str
    note: str = ""


class TagResult(BaseModel):
    category: str
    tags: list[str]
    provider: str
