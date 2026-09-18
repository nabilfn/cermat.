"""Intelligence & Operations endpoints. Read-only; metrics derive from source data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.intelligence.anomalies import detect_anomalies
from app.intelligence.brief import generate_brief
from app.intelligence.config import load_settings
from app.intelligence.dataset import IntelDataset, load_dataset, utcnow, window_for
from app.intelligence.overview import compute_overview
from app.intelligence.patterns import detect_patterns
from app.intelligence.priority import priority_queue
from app.intelligence.suppliers import supplier_detail, supplier_intelligence
from app.intelligence.trends import exception_trend
from app.schemas import (
    AnomalySignal,
    BriefResponse,
    IntelligenceSettings,
    OverviewResponse,
    PatternSignal,
    Period,
    PriorityItem,
    SupplierDetail,
    SupplierIntel,
    TrendCategory,
    TrendResponse,
)
from app.services.ask_llm import BriefModel, default_model

router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])


async def _load(session: AsyncSession) -> tuple[IntelDataset, IntelligenceSettings]:
    dataset = await load_dataset(session)
    settings, _ = await load_settings(session)
    return dataset, settings


def get_brief_model() -> BriefModel | None:
    return default_model()


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    period: Period = Query(default="30d"),
    session: AsyncSession = Depends(get_session),
) -> OverviewResponse:
    dataset, settings = await _load(session)
    return compute_overview(dataset, settings, period, utcnow())


@router.get("/priority", response_model=list[PriorityItem])
async def priority(
    limit: int = Query(default=25, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[PriorityItem]:
    dataset, settings = await _load(session)
    now = utcnow()
    return priority_queue(dataset, settings, now, detect_patterns(dataset, settings, now), limit=limit)


@router.get("/trends", response_model=TrendResponse)
async def trends(
    period: Period = Query(default="30d"),
    category: TrendCategory = Query(default="all"),
    session: AsyncSession = Depends(get_session),
) -> TrendResponse:
    dataset, _ = await _load(session)
    return exception_trend(dataset, period, utcnow(), category)


@router.get("/suppliers", response_model=list[SupplierIntel])
async def suppliers(
    period: Period = Query(default="all"),
    session: AsyncSession = Depends(get_session),
) -> list[SupplierIntel]:
    dataset, _ = await _load(session)
    window = None if period == "all" else window_for(period, utcnow())
    return supplier_intelligence(dataset, window)


@router.get("/suppliers/{supplier}", response_model=SupplierDetail)
async def supplier(
    supplier: str,
    session: AsyncSession = Depends(get_session),
) -> SupplierDetail:
    dataset, settings = await _load(session)
    detail = supplier_detail(dataset, settings, utcnow(), supplier[:200])
    if detail is None:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    return detail


@router.get("/patterns", response_model=list[PatternSignal])
async def patterns(session: AsyncSession = Depends(get_session)) -> list[PatternSignal]:
    dataset, settings = await _load(session)
    return detect_patterns(dataset, settings, utcnow())


@router.get("/anomalies", response_model=list[AnomalySignal])
async def anomalies(session: AsyncSession = Depends(get_session)) -> list[AnomalySignal]:
    dataset, settings = await _load(session)
    return detect_anomalies(dataset, settings, utcnow())


@router.get("/brief", response_model=BriefResponse)
async def brief(
    period: Period = Query(default="30d"),
    session: AsyncSession = Depends(get_session),
    model: BriefModel | None = Depends(get_brief_model),
) -> BriefResponse:
    dataset, settings = await _load(session)
    now = utcnow()
    return await generate_brief(compute_overview(dataset, settings, period, now), model, now)
