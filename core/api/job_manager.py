from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.data_paths import data_paths


class EngineJobType(str, Enum):
    BACKTEST = "backtest"
    OPTIMIZATION = "optimization"


class EngineJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EngineJobRecord(BaseModel):
    job_id: str
    job_type: EngineJobType
    status: EngineJobStatus
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_traceback: Optional[str] = None


class EngineJobManager:
    def __init__(self, jobs_dir: Optional[Path] = None):
        outputs_root = Path(os.getenv("QUANTS_LAB_OUTPUTS_DIR", data_paths.base_path / "app" / "outputs"))
        self.jobs_dir = Path(jobs_dir) if jobs_dir else outputs_root / "api_jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        job_type: EngineJobType,
        input_payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EngineJobRecord:
        now = datetime.now(timezone.utc)
        job = EngineJobRecord(
            job_id=str(uuid.uuid4()),
            job_type=job_type,
            status=EngineJobStatus.PENDING,
            created_at=now,
            updated_at=now,
            input_payload=input_payload or {},
            metadata=metadata or {},
        )
        self.save_job(job)
        return job

    def save_job(self, job: EngineJobRecord) -> None:
        job_file = self.jobs_dir / f"{job.job_id}.json"
        with job_file.open("w", encoding="utf-8") as file:
            json.dump(job.model_dump(mode="json"), file, indent=2)

    def get_job(self, job_id: str) -> EngineJobRecord:
        job_file = self.jobs_dir / f"{job_id}.json"
        if not job_file.exists():
            raise FileNotFoundError(f"Job not found: {job_id}")
        with job_file.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return EngineJobRecord.model_validate(payload)

    def list_jobs(
        self,
        status: Optional[EngineJobStatus] = None,
        limit: int = 100,
    ) -> List[EngineJobRecord]:
        jobs: List[EngineJobRecord] = []
        for job_file in sorted(self.jobs_dir.glob("*.json"), reverse=True):
            with job_file.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            job = EngineJobRecord.model_validate(payload)
            if status is None or job.status == status:
                jobs.append(job)
            if len(jobs) >= limit:
                break
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return jobs

    def mark_running(self, job_id: str) -> EngineJobRecord:
        job = self.get_job(job_id)
        now = datetime.now(timezone.utc)
        job.status = EngineJobStatus.RUNNING
        job.started_at = now
        job.updated_at = now
        job.error = None
        job.error_traceback = None
        self.save_job(job)
        return job

    def mark_completed(self, job_id: str, result: Dict[str, Any]) -> EngineJobRecord:
        job = self.get_job(job_id)
        now = datetime.now(timezone.utc)
        job.status = EngineJobStatus.COMPLETED
        job.updated_at = now
        job.completed_at = now
        job.result = result
        self.save_job(job)
        return job

    def mark_failed(self, job_id: str, error: str, error_traceback: Optional[str] = None) -> EngineJobRecord:
        job = self.get_job(job_id)
        now = datetime.now(timezone.utc)
        job.status = EngineJobStatus.FAILED
        job.updated_at = now
        job.completed_at = now
        job.error = error
        job.error_traceback = error_traceback
        self.save_job(job)
        return job
