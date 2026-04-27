from __future__ import annotations

import datetime
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from core.backtesting.engine import BacktestingEngine
from core.backtesting.optimization_results import OptimizationResult
from core.backtesting.optimizer import BacktestingConfig, BaseStrategyConfigGenerator, StrategyOptimizer
from core.condor.approval_manager import ApprovalManager
from core.condor.deploy_manager import CondorDeployManager
from core.condor.models import RiskLimits
from core.notifiers.base import NotificationMessage
from core.notifiers.manager import get_notification_manager
from core.reporting.optimization_reporter import OptimizationReporter, ReportArtifacts
from core.services.hummingbot_api_client import HummingbotAPIClient


class StudyConfig(BaseModel):
    name: str
    controller: str
    controller_type: Optional[str] = None
    n_trials: int = 50
    direction: str = "maximize"
    objective: str = "sharpe_ratio"

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, value: str) -> str:
        if value not in {"maximize", "minimize"}:
            raise ValueError("direction must be 'maximize' or 'minimize'")
        return value

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, value: str) -> str:
        if value not in {"sharpe_ratio", "profit_factor", "total_return"}:
            raise ValueError("objective must be one of: sharpe_ratio, profit_factor, total_return")
        return value


class DataConfig(BaseModel):
    connector: str
    trading_pairs: List[str]
    interval: str = "15m"
    lookback_days: int = 60
    end_time_buffer_hours: int = 0


class ParameterRange(BaseModel):
    type: str
    low: Optional[float] = None
    high: Optional[float] = None
    step: Optional[float] = None
    choices: Optional[List[Any]] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in {"int", "float", "categorical"}:
            raise ValueError("type must be one of: int, float, categorical")
        return value

    @model_validator(mode="after")
    def validate_shape(self):
        if self.type in {"int", "float"}:
            if self.low is None or self.high is None:
                raise ValueError("numeric ranges require low and high")
        if self.type == "categorical" and not self.choices:
            raise ValueError("categorical ranges require choices")
        return self


class RiskFilterConfig(BaseModel):
    max_drawdown_filter: Optional[float] = None
    min_trades_filter: Optional[int] = None


class ValidationConfig(BaseModel):
    enabled: bool = True
    ratio: float = 0.2

    @field_validator("ratio")
    @classmethod
    def validate_ratio(cls, value: float) -> float:
        if value <= 0 or value >= 0.5:
            raise ValueError("validation ratio must be between 0 and 0.5")
        return value


class ExportConfig(BaseModel):
    top_k: int = 3
    auto_deploy: bool = False
    condor_agents_dir: str = "~/condor/agents"
    notify_telegram: bool = False
    telegram_chat_ids: List[str] = Field(default_factory=list)
    tick_interval_seconds: int = 60


class ValidationSummary(BaseModel):
    start_bt: int
    end_bt: int
    sharpe_ratio: float
    total_return: float
    max_drawdown: float
    profit_factor: float
    total_trades: int
    passed: bool
    objective: str


class OptimizationConfig(BaseModel):
    study: StudyConfig
    data: DataConfig
    param_ranges: Dict[str, ParameterRange]
    fixed_params: Dict[str, Any] = Field(default_factory=dict)
    risk_limits: RiskFilterConfig = Field(default_factory=RiskFilterConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "OptimizationConfig":
        path = Path(config_path)
        with path.open("r", encoding="utf-8") as file:
            raw_config = yaml.safe_load(file) or {}

        raw_param_ranges = raw_config.get("param_ranges", {}) or {}
        fixed_params = raw_config.get("fixed_params", {}) or {}
        if "fixed" in raw_param_ranges:
            fixed_params = {**fixed_params, **(raw_param_ranges.pop("fixed") or {})}

        raw_config["param_ranges"] = raw_param_ranges
        raw_config["fixed_params"] = fixed_params
        return cls(**raw_config)

    @model_validator(mode="after")
    def validate_walk_forward(self):
        if not self.validation.enabled:
            raise ValueError("walk-forward validation is mandatory. Set validation.enabled to true.")
        return self


class UniversalStrategyConfigGenerator(BaseStrategyConfigGenerator):
    """Generate controller configs from a generic YAML optimization specification."""

    def __init__(
        self,
        optimization_config: OptimizationConfig,
        trading_pair: str,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        backtesting_engine: Optional[BacktestingEngine] = None,
    ):
        super().__init__(start_date=start_date, end_date=end_date, config={"trading_pair": trading_pair})
        self.optimization_config = optimization_config
        self.trading_pair = trading_pair
        self.backtesting_engine = backtesting_engine or BacktestingEngine(load_cached_data=False)

    async def generate_config(self, trial) -> BacktestingConfig:
        params = self.build_trial_parameters(trial)
        controller_config = self.build_controller_config(params)
        return BacktestingConfig(config=controller_config, start=self.start, end=self.end)

    def build_trial_parameters(self, trial) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        for name, range_def in self.optimization_config.param_ranges.items():
            if range_def.type == "int":
                params[name] = trial.suggest_int(name, int(range_def.low), int(range_def.high), step=int(range_def.step or 1))
            elif range_def.type == "float":
                params[name] = trial.suggest_float(name, float(range_def.low), float(range_def.high), step=range_def.step)
            elif range_def.type == "categorical":
                params[name] = trial.suggest_categorical(name, range_def.choices)
        params.update(self.optimization_config.fixed_params)
        return params

    def build_controller_config_data(self, params: Dict[str, Any]) -> Dict[str, Any]:
        controller_info = None
        if self.optimization_config.study.controller_type:
            controller_type = self.optimization_config.study.controller_type
        else:
            controller_info = self.backtesting_engine.resolve_controller_info(self.optimization_config.study.controller)
            controller_type = controller_info["controller_type"] if controller_info else None

        config_data = {
            "controller_name": self.optimization_config.study.controller,
            **params,
        }
        if controller_type:
            config_data["controller_type"] = controller_type

        config_data.setdefault("connector_name", self.optimization_config.data.connector)
        if "trading_pair" not in config_data:
            config_data["trading_pair"] = self.trading_pair
        if "interval" not in config_data:
            config_data["interval"] = self.optimization_config.data.interval
        if "candles_connector" not in config_data:
            config_data["candles_connector"] = self.optimization_config.data.connector
        if "candles_trading_pair" not in config_data and "trading_pair" in config_data:
            config_data["candles_trading_pair"] = config_data["trading_pair"]

        if "controller_type" not in config_data:
            raise ValueError(
                f"Unable to resolve controller_type for controller '{self.optimization_config.study.controller}'. "
                "Set study.controller_type explicitly or ensure the controller exists under app/controllers."
            )
        return config_data

    def build_controller_config(self, params: Dict[str, Any]):
        config_data = self.build_controller_config_data(params)
        optional_fields = ["candles_connector", "candles_trading_pair", "interval", "trading_pair"]
        last_error = None

        for prune_count in range(len(optional_fields) + 1):
            candidate = config_data.copy()
            for field_name in optional_fields[:prune_count]:
                candidate.pop(field_name, None)
            try:
                return self.backtesting_engine.get_controller_config_instance_from_dict(candidate)
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("Unable to build controller config from universal strategy parameters.")

    def score_backtesting_result(self, backtesting_result) -> float:
        metrics = backtesting_result.results
        max_drawdown_filter = self.optimization_config.risk_limits.max_drawdown_filter
        if max_drawdown_filter is not None:
            max_drawdown = float(metrics.get("max_drawdown_pct", metrics.get("max_drawdown_usd", 0.0)))
            if max_drawdown > max_drawdown_filter:
                return float("-inf")

        min_trades_filter = self.optimization_config.risk_limits.min_trades_filter
        if min_trades_filter is not None:
            total_trades = int(metrics.get("total_executors", 0))
            if total_trades < min_trades_filter:
                return float("-inf")

        objective = self.optimization_config.study.objective
        objective_map = {
            "sharpe_ratio": metrics.get("sharpe_ratio", float("-inf")),
            "profit_factor": metrics.get("profit_factor", float("-inf")),
            "total_return": metrics.get("net_pnl", float("-inf")),
        }
        score = objective_map.get(objective, metrics.get("sharpe_ratio", float("-inf")))
        try:
            numeric_score = float(score)
        except (TypeError, ValueError):
            return float("-inf")
        return numeric_score if not math.isnan(numeric_score) else float("-inf")


class UniversalOptimizationRunner:
    """Run optimization studies from YAML specs and export optional Condor bundles."""

    def __init__(
        self,
        deploy_manager: Optional[CondorDeployManager] = None,
    ):
        self.deploy_manager = deploy_manager
        self.approval_manager = ApprovalManager()
        self.reporter = OptimizationReporter()

    async def optimize_from_yaml(self, config_path: str | Path) -> List[OptimizationResult]:
        optimization_config = OptimizationConfig.from_yaml(config_path)
        return await self.optimize(optimization_config=optimization_config, source_path=config_path)

    async def optimize(
        self,
        optimization_config: OptimizationConfig,
        source_path: Optional[str | Path] = None,
    ) -> List[OptimizationResult]:
        results: List[OptimizationResult] = []

        end_datetime = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            hours=optimization_config.data.end_time_buffer_hours
        )
        lookback_delta = datetime.timedelta(days=optimization_config.data.lookback_days)
        start_datetime = end_datetime - lookback_delta

        validation_delta = datetime.timedelta(
            seconds=int(lookback_delta.total_seconds() * optimization_config.validation.ratio)
        )
        train_end = end_datetime - validation_delta if optimization_config.validation.enabled else end_datetime
        if train_end <= start_datetime:
            raise ValueError("Validation window consumes the entire lookback period. Increase lookback_days.")

        for trading_pair in optimization_config.data.trading_pairs:
            optimizer = StrategyOptimizer(
                resolution=optimization_config.data.interval,
                load_cached_data=True,
            )
            study_name = self._build_study_name(optimization_config.study.name, trading_pair)
            generator = UniversalStrategyConfigGenerator(
                optimization_config=optimization_config,
                trading_pair=trading_pair,
                start_date=start_datetime,
                end_date=train_end,
                backtesting_engine=optimizer.backtesting_engine,
            )

            result = await optimizer.optimize(
                study_name=study_name,
                config_generator=generator,
                n_trials=optimization_config.study.n_trials,
                export_top_k=optimization_config.export.top_k,
                direction=optimization_config.study.direction,
            )

            result.metadata["optimization_config"] = {
                "source_file": str(Path(source_path).resolve()) if source_path else None,
                "controller": optimization_config.study.controller,
                "objective": optimization_config.study.objective,
                "trading_pair": trading_pair,
                "interval": optimization_config.data.interval,
            }

            validation_summary = await self._validate_best_trial(
                optimizer=optimizer,
                result=result,
                validation_start=train_end,
                validation_end=end_datetime,
                objective=optimization_config.study.objective,
                risk_filters=optimization_config.risk_limits,
            )
            result.metadata["validation"] = validation_summary.model_dump(mode="json")

            auto_deploy_blocked = optimization_config.export.auto_deploy and not validation_summary.passed
            should_export_condor = bool(self.deploy_manager or optimization_config.export.condor_agents_dir)
            if should_export_condor:
                owned_api_client = None
                if self.deploy_manager is not None:
                    deploy_manager = self.deploy_manager
                else:
                    if optimization_config.export.auto_deploy and not auto_deploy_blocked:
                        owned_api_client = HummingbotAPIClient()
                    deploy_manager = CondorDeployManager(
                        api_client=owned_api_client,
                        agents_dir=Path(optimization_config.export.condor_agents_dir),
                        backtesting_engine=optimizer.backtesting_engine,
                    )
                risk_limits = RiskLimits(
                    max_drawdown_pct=optimization_config.risk_limits.max_drawdown_filter,
                    min_trades=optimization_config.risk_limits.min_trades_filter,
                )
                try:
                    deployment_report = await deploy_manager.deploy_optimization_result(
                        result=result,
                        deploy_top_k=optimization_config.export.top_k,
                        dry_run=not optimization_config.export.auto_deploy or auto_deploy_blocked,
                        risk_limits=risk_limits,
                        tick_interval_seconds=optimization_config.export.tick_interval_seconds,
                    )
                finally:
                    if owned_api_client is not None:
                        await owned_api_client.close()
                result.metadata["condor_deployment"] = deployment_report.model_dump(mode="json")
                result.metadata["condor_deployment"]["auto_deploy_blocked"] = auto_deploy_blocked
                if auto_deploy_blocked:
                    result.metadata["condor_deployment"]["block_reason"] = "validation_failed"

            approval_request = self.approval_manager.create_request(
                result=result,
                deploy_top_k=optimization_config.export.top_k,
            )
            result.metadata["approval_request"] = approval_request.model_dump(mode="json")

            report_artifacts = self._persist_result_metadata(result)
            if optimization_config.export.notify_telegram:
                await self._send_result_notification(
                    result,
                    report_artifacts=report_artifacts,
                    chat_ids=optimization_config.export.telegram_chat_ids,
                )
            results.append(result)

        return results

    async def _validate_best_trial(
        self,
        optimizer: StrategyOptimizer,
        result: OptimizationResult,
        validation_start: datetime.datetime,
        validation_end: datetime.datetime,
        objective: str,
        risk_filters: RiskFilterConfig,
    ) -> ValidationSummary:
        controller_config = optimizer.backtesting_engine.get_controller_config_instance_from_dict(
            result.best_trial.controller_config
        )
        validation_result = await optimizer.backtesting_engine.run_backtesting(
            config=controller_config,
            start=int(validation_start.timestamp()),
            end=int(validation_end.timestamp()),
            backtesting_resolution=optimizer.resolution,
        )
        metrics = validation_result.results

        max_drawdown = float(metrics.get("max_drawdown_pct", metrics.get("max_drawdown_usd", 0.0)))
        total_trades = int(metrics.get("total_executors", 0))
        passed = True
        if risk_filters.max_drawdown_filter is not None and max_drawdown > risk_filters.max_drawdown_filter:
            passed = False
        if risk_filters.min_trades_filter is not None and total_trades < risk_filters.min_trades_filter:
            passed = False

        return ValidationSummary(
            start_bt=int(validation_start.timestamp()),
            end_bt=int(validation_end.timestamp()),
            sharpe_ratio=float(metrics.get("sharpe_ratio", 0.0)),
            total_return=float(metrics.get("net_pnl", 0.0)),
            max_drawdown=max_drawdown,
            profit_factor=float(metrics.get("profit_factor", 0.0)),
            total_trades=total_trades,
            passed=passed,
            objective=objective,
        )

    @staticmethod
    def _build_study_name(base_name: str, trading_pair: str) -> str:
        return f"{base_name}_{trading_pair.replace('-', '_')}"

    def _persist_result_metadata(self, result: OptimizationResult) -> ReportArtifacts:
        if not result.output_dir:
            return ReportArtifacts()
        output_dir = Path(result.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        approval_request = result.metadata.get("approval_request")
        artifacts = self.reporter.generate_report_bundle(result=result, approval_request=approval_request)
        result.metadata["report_artifacts"] = artifacts.model_dump(mode="json")
        result_file = output_dir / "result.json"
        with result_file.open("w", encoding="utf-8") as file:
            json.dump(result.model_dump(mode="json"), file, indent=2)
        return artifacts

    @staticmethod
    def _build_notification_message(result: OptimizationResult) -> NotificationMessage:
        validation = result.metadata.get("validation", {})
        deployment = result.metadata.get("condor_deployment", {})
        approval_request = result.metadata.get("approval_request", {})
        best_trial = result.best_trial
        lines = [
            f"Study: {result.study_name}",
            f"Controller: {result.controller_name}",
            f"Pair(s): {', '.join(result.trading_pairs) if result.trading_pairs else 'N/A'}",
            f"Objective: {best_trial.objective_value:.6f}",
            f"Sharpe: {best_trial.sharpe_ratio:.6f}",
            f"Return: {best_trial.total_return:.6f}",
            f"Drawdown: {best_trial.max_drawdown:.6f}",
            f"Trades: {best_trial.total_trades}",
        ]
        if validation:
            lines.append(f"Validation passed: {'yes' if validation.get('passed') else 'no'}")
        if deployment:
            lines.append(
                "Condor export: "
                f"{len(deployment.get('artifacts', []))} artifact(s)"
                + (" [live]" if not deployment.get("dry_run", True) else " [dry-run]")
            )
            if deployment.get("auto_deploy_blocked"):
                lines.append("Auto-deploy blocked by walk-forward validation.")
        if approval_request:
            lines.append(f"Approval request: {approval_request.get('request_id')} [{approval_request.get('status')}]")
        if result.output_dir:
            lines.append(f"Output: {result.output_dir}")
        return NotificationMessage(
            title="Quants-Lab Optimization Complete",
            message="\n".join(lines),
            level="success" if validation.get("passed", True) else "warning",
        )

    async def _send_result_notification(
        self,
        result: OptimizationResult,
        report_artifacts: Optional[ReportArtifacts] = None,
        chat_ids: Optional[List[str]] = None,
    ) -> None:
        notification_manager = get_notification_manager()
        if notification_manager is None:
            return
        message = self._build_notification_message(result)
        await notification_manager.send_notification(message)

        telegram_notifier = notification_manager.get_notifier("telegram") if notification_manager else None
        if telegram_notifier is None or report_artifacts is None:
            return

        target_chat_ids = chat_ids or None
        if report_artifacts.equity_curve_png:
            await telegram_notifier.send_photo(
                report_artifacts.equity_curve_png,
                caption=message.title,
                chat_ids=target_chat_ids,
            )
        if report_artifacts.summary_markdown:
            await telegram_notifier.send_document(
                report_artifacts.summary_markdown,
                caption=result.study_name,
                chat_ids=target_chat_ids,
            )
