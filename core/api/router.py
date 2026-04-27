from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from core.api.application_service import TrinityExecutionService
from core.api.job_manager import EngineJobStatus, EngineJobType
from core.api.models import (
    ApprovalDecisionRequest,
    BacktestRequest,
    DeploymentRequest,
    JobListResponse,
    JobSubmissionResponse,
    OptimizationRequest,
    RejectionRequest,
    StrategyManifest,
)
from core.condor.models import ApprovalStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["strategy-engine"])
service = TrinityExecutionService()


def get_service() -> TrinityExecutionService:
    return service


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.exception("Unhandled strategy-engine API error")
    raise HTTPException(status_code=500, detail="Internal strategy-engine error") from exc


@router.get("/health")
async def strategy_engine_health() -> dict:
    controllers = service.list_controllers()
    return {
        "status": "healthy",
        "service": "quants-lab-strategy-engine",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "controllers_total": len(controllers),
        "jobs_dir": str(service.jobs.jobs_dir),
    }


@router.get("/controllers")
async def list_controllers() -> dict:
    return {"controllers": service.list_controllers()}


@router.post("/strategies/validate")
async def validate_strategy_manifest(manifest: StrategyManifest) -> dict:
    try:
        return await service.validate_manifest(manifest)
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/backtests", response_model=JobSubmissionResponse)
async def create_backtest_job(request: BacktestRequest, background_tasks: BackgroundTasks) -> JobSubmissionResponse:
    try:
        job = service.create_job(
            job_type=EngineJobType.BACKTEST,
            input_payload=request.model_dump(mode="json"),
            metadata={
                "controller": request.manifest.study.controller,
                "study_name": request.manifest.study.name,
                "requested_by": "api",
            },
        )
        background_tasks.add_task(service.execute_backtest_job, job.job_id, request.model_dump(mode="json"))
        return JobSubmissionResponse(
            job_id=job.job_id,
            job_type=job.job_type.value,
            status=job.status.value,
            created_at=job.created_at,
            message="Backtest job queued.",
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/optimizations", response_model=JobSubmissionResponse)
async def create_optimization_job(
    request: OptimizationRequest,
    background_tasks: BackgroundTasks,
) -> JobSubmissionResponse:
    try:
        job = service.create_job(
            job_type=EngineJobType.OPTIMIZATION,
            input_payload=request.model_dump(mode="json"),
            metadata={
                "controller": request.manifest.study.controller,
                "study_name": request.manifest.study.name,
                "requested_by": "api",
            },
        )
        background_tasks.add_task(service.execute_optimization_job, job.job_id, request.model_dump(mode="json"))
        return JobSubmissionResponse(
            job_id=job.job_id,
            job_type=job.job_type.value,
            status=job.status.value,
            created_at=job.created_at,
            message="Optimization job queued.",
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    status: Optional[EngineJobStatus] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> JobListResponse:
    try:
        jobs = service.list_jobs(status=status, limit=limit)
        return JobListResponse(jobs=[job.model_dump(mode="json") for job in jobs])
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    try:
        job = service.get_job(job_id)
        return job.model_dump(mode="json")
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/studies/{study_name}")
async def get_study(study_name: str) -> dict:
    try:
        return service.get_study(study_name)
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/reports/{study_name}")
async def get_report(study_name: str) -> dict:
    try:
        return service.get_report(study_name)
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/approvals")
async def list_approvals(
    study_name: Optional[str] = Query(default=None),
    status: Optional[ApprovalStatus] = Query(default=None),
) -> dict:
    try:
        approvals = service.list_approvals(study_name=study_name, status=status)
        return {"approvals": approvals}
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/approvals/{request_id}/approve")
async def approve_request(request_id: str, payload: ApprovalDecisionRequest) -> dict:
    try:
        approval = await service.approve_request(
            request_id=request_id,
            approver=payload.approver,
            live=payload.live,
            agents_dir=payload.agents_dir,
            server=payload.server,
            port=payload.port,
            username=payload.username,
            password=payload.password,
            profile=payload.profile,
        )
        return {"approval": approval}
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/approvals/{request_id}/reject")
async def reject_request(request_id: str, payload: RejectionRequest) -> dict:
    try:
        approval = service.reject_request(
            request_id=request_id,
            approver=payload.approver,
            reason=payload.reason,
        )
        return {"approval": approval}
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/deployments")
async def create_deployment(request: DeploymentRequest) -> dict:
    try:
        return await service.deploy(request)
    except Exception as exc:
        _raise_http_error(exc)
