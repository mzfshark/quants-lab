from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.backtesting.universal_strategy import OptimizationConfig


TRINITY_MANIFEST_EXAMPLE: Dict[str, Any] = {
    "schema_version": "1.0",
    "manifest_id": "trinity-macd-bb-demo",
    "metadata": {
        "requested_by": "openclaw-trinity",
        "tenant": "axodus",
        "purpose": "strategy-evaluation",
        "tags": ["macd", "bollinger", "perpetuals"],
    },
    "study": {
        "name": "trinity_macd_bb_template",
        "controller": "macd_bb_v1",
        "controller_type": "directional_trading",
        "n_trials": 25,
        "direction": "maximize",
        "objective": "sharpe_ratio",
    },
    "data": {
        "connector": "binance_perpetual",
        "trading_pairs": ["BTC-USDT"],
        "interval": "15m",
        "lookback_days": 60,
        "end_time_buffer_hours": 6,
    },
    "param_ranges": {
        "bb_length": {"type": "int", "low": 20, "high": 200, "step": 10},
        "bb_std": {"type": "float", "low": 1.5, "high": 3.0, "step": 0.25},
        "macd_fast": {"type": "int", "low": 9, "high": 30, "step": 3},
        "macd_slow": {"type": "int", "low": 21, "high": 60, "step": 3},
        "macd_signal": {"type": "int", "low": 5, "high": 15, "step": 1},
        "max_executors_per_side": {"type": "int", "low": 1, "high": 3, "step": 1},
    },
    "fixed_params": {
        "total_amount_quote": 1000,
        "take_profit": 0.05,
        "stop_loss": 0.03,
        "cooldown_time": 900,
    },
    "risk_limits": {
        "max_drawdown_filter": 0.15,
        "min_trades_filter": 30,
    },
    "validation": {
        "enabled": True,
        "ratio": 0.2,
    },
    "export": {
        "top_k": 2,
        "auto_deploy": False,
        "condor_agents_dir": "~/condor/agents",
        "notify_telegram": False,
        "telegram_chat_ids": [],
        "tick_interval_seconds": 60,
    },
}

TRINITY_BACKTEST_REQUEST_EXAMPLE: Dict[str, Any] = {
    "manifest": TRINITY_MANIFEST_EXAMPLE,
    "candidate_params": {
        "bb_length": 180,
        "bb_std": 1.75,
        "macd_fast": 30,
        "macd_slow": 54,
        "macd_signal": 8,
        "max_executors_per_side": 1,
    },
    "export_artifacts": True,
}

TRINITY_OPTIMIZATION_REQUEST_EXAMPLE: Dict[str, Any] = {
    "manifest": TRINITY_MANIFEST_EXAMPLE,
}


class StrategyManifest(OptimizationConfig):
    model_config = ConfigDict(json_schema_extra={"example": TRINITY_MANIFEST_EXAMPLE})

    schema_version: str = "1.0"
    manifest_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_optimization_config(self) -> OptimizationConfig:
        payload = self.model_dump(exclude={"schema_version", "manifest_id", "metadata"})
        return OptimizationConfig.model_validate(payload)


class BacktestRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": TRINITY_BACKTEST_REQUEST_EXAMPLE})

    manifest: StrategyManifest
    candidate_params: Dict[str, Any] = Field(default_factory=dict)
    export_artifacts: bool = True


class OptimizationRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": TRINITY_OPTIMIZATION_REQUEST_EXAMPLE})

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
