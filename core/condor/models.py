from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RiskLimits(BaseModel):
    max_position_size: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    min_trades: Optional[int] = None
    max_notional_size: Optional[float] = None


class DeploymentArtifact(BaseModel):
    rank: int
    trial_id: int
    agent_name: str
    agent_dir: str
    trading_pair: Optional[str] = None
    files: Dict[str, str] = Field(default_factory=dict)
    api_validation: Dict[str, Any] = Field(default_factory=dict)
    api_responses: Dict[str, Any] = Field(default_factory=dict)
    deployed: bool = False
    dry_run: bool = True
    errors: List[str] = Field(default_factory=list)


class DeploymentReport(BaseModel):
    study_name: str
    controller_name: str
    deploy_top_k: int
    dry_run: bool
    created_at: datetime
    artifacts: List[DeploymentArtifact] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEPLOYED = "deployed"
    FAILED = "failed"


class ApprovalRequest(BaseModel):
    request_id: str
    study_name: str
    controller_name: str
    trading_pairs: List[str]
    result_output_dir: str
    created_at: datetime
    status: ApprovalStatus
    requested_top_k: int = 1
    validation_passed: bool = False
    requested_by: str = "system"
    approve_command: Optional[str] = None
    approve_live_command: Optional[str] = None
    reject_command: Optional[str] = None
    decision_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    reason: Optional[str] = None
    deployment_report: Optional[Dict[str, Any]] = None
