"""Intelligence & Operations endpoints. Read-only; metrics derive from source data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import WorkspaceContext, workspace_context
from app.config import settings as app_settings
from app.core.errors import not_found
from app.core.ratelimit import limiter
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
    SupplierPage,
    TrendCategory,
    TrendResponse,
)
from app.services.ask_llm import BriefModel, default_model

router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])


async def _load(session: AsyncSession, ctx: WorkspaceContext) -> tuple[IntelDataset, IntelligenceSettings]:
    dataset = await load_dataset(session, ctx.workspace_id)
    settings, _ = await load_settings(session, ctx.workspace_id)
    return dataset, settings


def get_brief_model() -> BriefModel | None:
    return default_model()


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    period: Period = Query(default="30d"),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> OverviewResponse:
    dataset, settings = await _load(session, ctx)
    return compute_overview(dataset, settings, period, utcnow())


@router.get("/priority", response_model=list[PriorityItem])
async def priority(
    limit: int = Query(default=25, ge=1, le=200),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[PriorityItem]:
    dataset, settings = await _load(session, ctx)
    now = utcnow()
    return priority_queue(dataset, settings, now, detect_patterns(dataset, settings, now), limit=limit)


@router.get("/trends", response_model=TrendResponse)
async def trends(
    period: Period = Query(default="30d"),
    category: TrendCategory = Query(default="all"),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> TrendResponse:
    dataset, _ = await _load(session, ctx)
    return exception_trend(dataset, period, utcnow(), category)


@router.get("/suppliers", response_model=SupplierPage, summary="Supplier intelligence (paginated)")
async def suppliers(
    period: Period = Query(default="all"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> SupplierPage:
    dataset, _ = await _load(session, ctx)
    window = None if period == "all" else window_for(period, utcnow())
    rows = supplier_intelligence(dataset, window)
    return SupplierPage(items=rows[offset : offset + limit], total=len(rows), limit=limit, offset=offset)


@router.get("/suppliers/{supplier}", response_model=SupplierDetail)
async def supplier(
    supplier: str,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> SupplierDetail:
    dataset, settings = await _load(session, ctx)
    detail = supplier_detail(dataset, settings, utcnow(), supplier[:200])
    if detail is None:
        raise not_found("Supplier")
    return detail


@router.get("/patterns", response_model=list[PatternSignal])
async def patterns(
    ctx: WorkspaceContext = Depends(workspace_context), session: AsyncSession = Depends(get_session)
) -> list[PatternSignal]:
    dataset, settings = await _load(session, ctx)
    return detect_patterns(dataset, settings, utcnow())


@router.get("/anomalies", response_model=list[AnomalySignal])
async def anomalies(
    ctx: WorkspaceContext = Depends(workspace_context), session: AsyncSession = Depends(get_session)
) -> list[AnomalySignal]:
    dataset, settings = await _load(session, ctx)
    return detect_anomalies(dataset, settings, utcnow())


@router.get("/brief", response_model=BriefResponse)
async def brief(
    period: Period = Query(default="30d"),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
    model: BriefModel | None = Depends(get_brief_model),
) -> BriefResponse:
    limiter.check(f"ai:{ctx.user.id}", limit=app_settings.rate_limit_ai_per_minute)
    dataset, settings = await _load(session, ctx)
    now = utcnow()
    return await generate_brief(compute_overview(dataset, settings, period, now), model, now)
