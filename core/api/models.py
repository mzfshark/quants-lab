from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from core.backtesting.universal_strategy import OptimizationConfig


class StrategyManifest(OptimizationConfig):
    schema_version: str = "1.0"
    manifest_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_optimization_config(self) -> OptimizationConfig:
        payload = self.model_dump(exclude={"schema_version", "manifest_id", "metadata"})
        return OptimizationConfig.model_validate(payload)


class BacktestRequest(BaseModel):
    manifest: StrategyManifest
    candidate_params: Dict[str, Any] = Field(default_factory=dict)
    export_artifacts: bool = True


class OptimizationRequest(BaseModel):
    manifest: StrategyManifest


class JobSubmissionResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    created_at: datetime
    message: str


class ApprovalDecisionRequest(BaseModel):
    approver: str = Field(default="api-user")
    live: bool = Field(default=False)
    agents_dir: Optional[str] = None
    server: str = Field(default="localhost")
    port: int = Field(default=8000)
    username: Optional[str] = None
    password: Optional[str] = None
    profile: Optional[str] = Field(default="master_account")


class RejectionRequest(BaseModel):
    approver: str = Field(default="api-user")
    reason: str = Field(..., min_length=1)


class DeploymentRequest(BaseModel):
    study_name: Optional[str] = None
    request_id: Optional[str] = None
    top_k: int = Field(default=1, ge=1, le=20)
    live: bool = Field(default=False)
    agents_dir: Optional[str] = None
    requested_by: str = Field(default="api-user")
    server: str = Field(default="localhost")
    port: int = Field(default=8000)
    username: Optional[str] = None
    password: Optional[str] = None
    profile: Optional[str] = Field(default="master_account")

    @model_validator(mode="after")
    def validate_target(self):
        if not self.study_name and not self.request_id:
            raise ValueError("study_name or request_id must be provided.")
        return self


class JobListResponse(BaseModel):
    jobs: List[Dict[str, Any]]
