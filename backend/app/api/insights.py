from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.insights import (
    AlertsData,
    BreakevenTableData,
    CategoriesData,
    FlowData,
    GoalsData,
    HygieneData,
    InsightsEnvelope,
    NatureData,
    ProjectionData,
    PurchaseDecisionData,
    PurchaseDecisionRequest,
    VitalsData,
)
from app.services import insights_service

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/hygiene", response_model=InsightsEnvelope[HygieneData])
async def get_hygiene(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = await insights_service.get_hygiene(session, ctx.workspace.id, ctx.user.primary_currency)
    return InsightsEnvelope[HygieneData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/categories", response_model=InsightsEnvelope[CategoriesData])
async def get_categories(
    reference: Literal["budget", "historical"] = Query(...),
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_categories(
        session, ctx.workspace.id, ctx.user.primary_currency, reference, month=month,
    )


@router.get("/nature", response_model=InsightsEnvelope[NatureData])
async def get_nature(
    months: int = Query(12, ge=1, le=36),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = await insights_service.get_nature(session, ctx.workspace.id, ctx.user.primary_currency, months=months)
    return InsightsEnvelope[NatureData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/breakeven-table", response_model=InsightsEnvelope[BreakevenTableData])
async def get_breakeven_table(
    monthly_yield: float = Query(insights_service.DEFAULT_MONTHLY_YIELD, gt=0),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = insights_service.get_breakeven_table(monthly_yield=monthly_yield)
    return InsightsEnvelope[BreakevenTableData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


@router.post("/purchase-decision", response_model=InsightsEnvelope[PurchaseDecisionData])
async def post_purchase_decision(
    request: PurchaseDecisionRequest,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = await insights_service.get_purchase_decision(
        session, ctx.workspace.id, ctx.user.primary_currency, request,
    )
    return InsightsEnvelope[PurchaseDecisionData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/vitals", response_model=InsightsEnvelope[VitalsData])
async def get_vitals(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = await insights_service.get_vitals(session, ctx.workspace.id, ctx.user.primary_currency)
    return InsightsEnvelope[VitalsData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/flow", response_model=InsightsEnvelope[FlowData])
async def get_flow(
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = await insights_service.get_flow(session, ctx.workspace.id, ctx.user.primary_currency, month=month)
    return InsightsEnvelope[FlowData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/projection", response_model=InsightsEnvelope[ProjectionData])
async def get_projection(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = await insights_service.get_projection(session, ctx.workspace.id, ctx.user.primary_currency)
    return InsightsEnvelope[ProjectionData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/goals", response_model=InsightsEnvelope[GoalsData])
async def get_goals(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = await insights_service.get_goals(session, ctx.workspace.id, ctx.user.primary_currency)
    return InsightsEnvelope[GoalsData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/alerts", response_model=InsightsEnvelope[AlertsData])
async def get_alerts(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = await insights_service.get_alerts(session, ctx.workspace.id, ctx.user.primary_currency)
    return InsightsEnvelope[AlertsData](
        data=data,
        error=None,
        window=None,
        reference=None,
        currency=ctx.user.primary_currency,
        generated_at=datetime.now(timezone.utc),
    )
