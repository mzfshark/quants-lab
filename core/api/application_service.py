from __future__ import annotations

import json
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from core.api.job_manager import EngineJobManager, EngineJobRecord, EngineJobStatus, EngineJobType
from core.api.models import BacktestRequest, DeploymentRequest, OptimizationRequest, StrategyManifest
from core.backtesting.engine import BacktestingEngine
from core.backtesting.optimization_results import OptimizationResult, OptimizationResultsManager
from core.backtesting.optimizer import StrategyOptimizer
from core.backtesting.universal_strategy import OptimizationConfig, UniversalOptimizationRunner, UniversalStrategyConfigGenerator
from core.condor.approval_manager import ApprovalManager
from core.condor.deploy_manager import CondorDeployManager
from core.condor.models import ApprovalStatus
from core.data_paths import data_paths
from core.reporting.optimization_reporter import OptimizationReporter
from core.services.hummingbot_api_client import HummingbotAPIClient


class TrinityExecutionService:
    """Headless application service used by Trinity and future external UIs."""

    def __init__(self):
        self.jobs = EngineJobManager()
        self.results_manager = OptimizationResultsManager()
        self.approval_manager = ApprovalManager()
        self.reporter = OptimizationReporter()
        self.outputs_root = Path(os.getenv("QUANTS_LAB_OUTPUTS_DIR", data_paths.base_path / "app" / "outputs"))
        self.backtests_root = self.outputs_root / "api_backtests"
        self.backtests_root.mkdir(parents=True, exist_ok=True)

    def create_job(self, job_type: EngineJobType, input_payload: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> EngineJobRecord:
        return self.jobs.create_job(job_type=job_type, input_payload=input_payload, metadata=metadata)

    def get_job(self, job_id: str) -> EngineJobRecord:
        return self.jobs.get_job(job_id)

    def list_jobs(self, status: Optional[EngineJobStatus] = None, limit: int = 100) -> List[EngineJobRecord]:
        return self.jobs.list_jobs(status=status, limit=limit)

    def list_controllers(self) -> List[Dict[str, Any]]:
        engine = BacktestingEngine(load_cached_data=False)
        controllers = engine.list_available_controllers()
        for controller in controllers:
            controller["category"] = controller.get("controller_type", "unknown")
        return controllers

    async def validate_manifest(self, manifest: StrategyManifest) -> Dict[str, Any]:
        optimization_config = manifest.to_optimization_config()
        start_datetime, _, train_end = self._compute_time_window(optimization_config, use_validation_split=True)
        sample_params = self._build_sample_params(optimization_config)

        pair_validations: List[Dict[str, Any]] = []
        for trading_pair in optimization_config.data.trading_pairs:
            generator = UniversalStrategyConfigGenerator(
                optimization_config=optimization_config,
                trading_pair=trading_pair,
                start_date=start_datetime,
                end_date=train_end,
                backtesting_engine=BacktestingEngine(load_cached_data=False),
            )
            controller_config = generator.build_controller_config(sample_params)
            pair_validations.append(
                {
                    "trading_pair": trading_pair,
                    "valid": True,
                    "study_name": self._build_study_name(optimization_config.study.name, trading_pair),
                    "sample_params": sample_params,
                    "controller_config": self._serialize_controller_config(controller_config),
                }
            )

        return {
            "schema_version": manifest.schema_version,
            "manifest_id": manifest.manifest_id,
            "controller": optimization_config.study.controller,
            "objective": optimization_config.study.objective,
            "direction": optimization_config.study.direction,
            "pairs_validated": len(pair_validations),
            "pair_validations": pair_validations,
            "metadata": manifest.metadata,
        }

    async def execute_backtest_job(self, job_id: str, request_payload: Dict[str, Any]) -> None:
        self.jobs.mark_running(job_id)
        try:
            request = BacktestRequest.model_validate(request_payload)
            result = await self._run_backtests(request)
            self.jobs.mark_completed(job_id, result=result)
        except Exception as exc:
            self.jobs.mark_failed(job_id, error=str(exc), error_traceback=traceback.format_exc())

    async def execute_optimization_job(self, job_id: str, request_payload: Dict[str, Any]) -> None:
        self.jobs.mark_running(job_id)
        try:
            request = OptimizationRequest.model_validate(request_payload)
            manifest = request.manifest
            optimization_config = manifest.to_optimization_config()
            runner = UniversalOptimizationRunner()
            results = await runner.optimize(optimization_config=optimization_config, source_path="api://trinity")

            serialized_results: List[Dict[str, Any]] = []
            for result in results:
                result.metadata["strategy_manifest"] = {
                    "schema_version": manifest.schema_version,
                    "manifest_id": manifest.manifest_id,
                    "metadata": manifest.metadata,
                }
                self._rewrite_result_json(result)
                serialized_results.append(
                    {
                        "study_name": result.study_name,
                        "controller_name": result.controller_name,
                        "output_dir": result.output_dir,
                        "validation": result.metadata.get("validation"),
                        "approval_request": result.metadata.get("approval_request"),
                        "report_artifacts": result.metadata.get("report_artifacts"),
                    }
                )

            self.jobs.mark_completed(
                job_id,
                result={
                    "mode": "optimization",
                    "results_total": len(serialized_results),
                    "results": serialized_results,
                },
            )
        except Exception as exc:
            self.jobs.mark_failed(job_id, error=str(exc), error_traceback=traceback.format_exc())

    async def _run_backtests(self, request: BacktestRequest) -> Dict[str, Any]:
        manifest = request.manifest
        optimization_config = manifest.to_optimization_config()
        start_datetime, end_datetime, _ = self._compute_time_window(optimization_config, use_validation_split=False)
        start_ts = int(start_datetime.timestamp())
        end_ts = int(end_datetime.timestamp())

        pair_results: List[Dict[str, Any]] = []
        for trading_pair in optimization_config.data.trading_pairs:
            optimizer = StrategyOptimizer(
                resolution=optimization_config.data.interval,
                load_cached_data=True,
            )
            generator = UniversalStrategyConfigGenerator(
                optimization_config=optimization_config,
                trading_pair=trading_pair,
                start_date=start_datetime,
                end_date=end_datetime,
                backtesting_engine=optimizer.backtesting_engine,
            )
            params = self._build_sample_params(optimization_config, overrides=request.candidate_params)
            controller_config = generator.build_controller_config(params)
            serialized_config = self._serialize_controller_config(controller_config)

            actual_start, actual_end = await self._prime_candles(
                optimizer=optimizer,
                connector_name=optimization_config.data.connector,
                trading_pair=trading_pair,
                interval=optimization_config.data.interval,
                start_ts=start_ts,
                end_ts=end_ts,
            )

            backtesting_result = await optimizer.backtesting_engine.run_backtesting(
                config=controller_config,
                start=actual_start,
                end=actual_end,
                backtesting_resolution=optimization_config.data.interval,
            )

            metrics = self._normalize_value(backtesting_result.results)
            executors_df = backtesting_result.executors_df.copy()
            equity_curve = OptimizationResultsManager._build_equity_curve(executors_df)
            output_dir = self._build_backtest_output_dir(optimization_config.study.name, trading_pair)
            output_dir.mkdir(parents=True, exist_ok=True)

            artifacts: Dict[str, Optional[str]] = {}
            if request.export_artifacts:
                self._write_json(
                    output_dir / "manifest.json",
                    manifest.model_dump(mode="json"),
                )
                self._write_json(
                    output_dir / "metrics.json",
                    {
                        "study_name": optimization_config.study.name,
                        "trading_pair": trading_pair,
                        "start_bt": actual_start,
                        "end_bt": actual_end,
                        "params": params,
                        "controller_config": serialized_config,
                        "metrics": metrics,
                        "equity_curve": equity_curve,
                        "metadata": manifest.metadata,
                    },
                )
                with (output_dir / "controller.yml").open("w", encoding="utf-8") as file:
                    yaml.safe_dump(serialized_config, file, sort_keys=False, allow_unicode=False)
                if not executors_df.empty:
                    executors_df.to_csv(output_dir / "executors.csv", index=False)

                reporter = self.reporter
                if equity_curve:
                    reporter._write_equity_curve_csv(output_dir / "equity_curve.csv", equity_curve)
                    reporter._write_equity_curve_png(output_dir / "equity_curve.png", equity_curve)

                summary_lines = [
                    f"# Backtest Report: {optimization_config.study.name}",
                    "",
                    f"- Trading pair: `{trading_pair}`",
                    f"- Controller: `{optimization_config.study.controller}`",
                    f"- Objective: `{optimization_config.study.objective}`",
                    f"- Start: `{actual_start}`",
                    f"- End: `{actual_end}`",
                    "",
                    "## Metrics",
                ]
                for key in ("sharpe_ratio", "net_pnl", "max_drawdown_pct", "profit_factor", "total_executors"):
                    if key in metrics:
                        summary_lines.append(f"- {key}: `{metrics[key]}`")
                summary_lines.extend(
                    [
                        "",
                        "## Params",
                        "```json",
                        json.dumps(params, indent=2, default=str),
                        "```",
                    ]
                )
                (output_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
                artifacts = {
                    "manifest": str(output_dir / "manifest.json"),
                    "metrics": str(output_dir / "metrics.json"),
                    "controller": str(output_dir / "controller.yml"),
                    "executors": str(output_dir / "executors.csv") if not executors_df.empty else None,
                    "summary_markdown": str(output_dir / "summary.md"),
                    "equity_curve_csv": str(output_dir / "equity_curve.csv") if equity_curve else None,
                    "equity_curve_png": str(output_dir / "equity_curve.png") if equity_curve else None,
                }

            pair_results.append(
                {
                    "trading_pair": trading_pair,
                    "output_dir": str(output_dir),
                    "start_bt": actual_start,
                    "end_bt": actual_end,
                    "params": params,
                    "controller_config": serialized_config,
                    "metrics": metrics,
                    "equity_curve": equity_curve,
                    "artifacts": artifacts,
                }
            )

        return {
            "mode": "backtest",
            "study_name": optimization_config.study.name,
            "controller_name": optimization_config.study.controller,
            "results_total": len(pair_results),
            "results": pair_results,
            "manifest_metadata": manifest.metadata,
        }

    def get_study(self, study_name: str) -> Dict[str, Any]:
        result = self._load_study_result(study_name)
        return result.model_dump(mode="json")

    def get_report(self, study_name: str) -> Dict[str, Any]:
        result = self._load_study_result(study_name)

        approval_request = result.metadata.get("approval_request")
        report_artifacts = result.metadata.get("report_artifacts") or {}
        artifact_paths = self._ensure_report_bundle(result, approval_request=approval_request)

        def read_text(path_key: str) -> Optional[str]:
            path_value = artifact_paths.get(path_key)
            if not path_value:
                return None
            path = Path(path_value)
            return path.read_text(encoding="utf-8") if path.exists() else None

        def read_json(path_key: str) -> Optional[Dict[str, Any]]:
            path_value = artifact_paths.get(path_key)
            if not path_value:
                return None
            path = Path(path_value)
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)

        return {
            "study_name": result.study_name,
            "controller_name": result.controller_name,
            "output_dir": result.output_dir,
            "validation": result.metadata.get("validation"),
            "approval_request": approval_request,
            "report_artifacts": artifact_paths,
            "markdown": read_text("summary_markdown"),
            "summary": read_json("summary_json"),
            "approval_instructions": read_text("approval_instructions"),
            "equity_curve": result.best_trial.equity_curve,
        }

    def list_approvals(self, study_name: Optional[str] = None, status: Optional[ApprovalStatus] = None) -> List[Dict[str, Any]]:
        requests = self.approval_manager.list_requests(study_name=study_name, status=status)
        return [request.model_dump(mode="json") for request in requests]

    async def approve_request(
        self,
        request_id: str,
        approver: str,
        live: bool = False,
        agents_dir: Optional[str] = None,
        server: str = "localhost",
        port: int = 8000,
        username: Optional[str] = None,
        password: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        request = await self.approval_manager.approve_request(
            approver=approver,
            request_id=request_id,
            live=live,
            agents_dir=Path(agents_dir) if agents_dir else None,
            server=server,
            port=port,
            username=username,
            password=password,
            profile_name=profile,
        )
        return request.model_dump(mode="json")

    def reject_request(self, request_id: str, approver: str, reason: str) -> Dict[str, Any]:
        request = self.approval_manager.reject_request(
            approver=approver,
            reason=reason,
            request_id=request_id,
        )
        return request.model_dump(mode="json")

    async def deploy(self, request: DeploymentRequest) -> Dict[str, Any]:
        if request.request_id:
            approval = await self.approve_request(
                request_id=request.request_id,
                approver=request.requested_by,
                live=request.live,
                agents_dir=request.agents_dir,
                server=request.server,
                port=request.port,
                username=request.username,
                password=request.password,
                profile=request.profile,
            )
            return {"approval": approval}

        if not request.study_name:
            raise ValueError("study_name or request_id must be provided.")

        if request.live:
            try:
                approval = await self.approval_manager.approve_request(
                    approver=request.requested_by,
                    study_name=request.study_name,
                    live=True,
                    agents_dir=Path(request.agents_dir) if request.agents_dir else None,
                    server=request.server,
                    port=request.port,
                    username=request.username,
                    password=request.password,
                    profile_name=request.profile,
                )
                return {"approval": approval.model_dump(mode="json")}
            except FileNotFoundError:
                pass

        result_model = self._load_study_result(request.study_name)

        if request.live:
            validation = result_model.metadata.get("validation", {})
            if not validation.get("passed", False):
                raise RuntimeError("Live deployment blocked because walk-forward validation did not pass.")

        api_client = None
        try:
            if request.live:
                api_client = HummingbotAPIClient(
                    server=request.server,
                    port=request.port,
                    username=request.username,
                    password=request.password,
                )
            deploy_manager = CondorDeployManager(
                api_client=api_client,
                agents_dir=Path(request.agents_dir) if request.agents_dir else None,
            )
            report = await deploy_manager.deploy_optimization_result(
                result=result_model,
                deploy_top_k=request.top_k,
                dry_run=not request.live,
                profile_name=request.profile,
            )
        finally:
            if api_client is not None:
                await api_client.close()
        return report.model_dump(mode="json")

    def _ensure_report_bundle(self, result_model, approval_request: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        artifact_paths = result_model.metadata.get("report_artifacts") or {}
        summary_markdown = artifact_paths.get("summary_markdown")
        if summary_markdown and Path(summary_markdown).exists():
            return artifact_paths

        artifacts = self.reporter.generate_report_bundle(result=result_model, approval_request=approval_request)
        result_model.metadata["report_artifacts"] = artifacts.model_dump(mode="json")
        self._rewrite_result_json(result_model)
        return artifacts.model_dump(mode="json")

    def _load_study_result(self, study_name: str, top_k: int = 5) -> OptimizationResult:
        result = self.results_manager.load_latest_result(study_name)
        if result is not None:
            return result
        try:
            optimizer = StrategyOptimizer()
            return optimizer.build_optimization_result(study_name, top_k=top_k)
        except Exception as exc:
            raise FileNotFoundError(f"No optimization result found for study: {study_name}") from exc

    @staticmethod
    def _compute_time_window(
        optimization_config: OptimizationConfig,
        use_validation_split: bool,
    ) -> tuple[datetime, datetime, datetime]:
        end_datetime = datetime.now(timezone.utc) - timedelta(hours=optimization_config.data.end_time_buffer_hours)
        lookback_delta = timedelta(days=optimization_config.data.lookback_days)
        start_datetime = end_datetime - lookback_delta
        if not use_validation_split:
            return start_datetime, end_datetime, end_datetime

        validation_delta = timedelta(seconds=int(lookback_delta.total_seconds() * optimization_config.validation.ratio))
        train_end = end_datetime - validation_delta
        return start_datetime, end_datetime, train_end

    @staticmethod
    def _build_sample_params(
        optimization_config: OptimizationConfig,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = dict(optimization_config.fixed_params)
        if overrides:
            params.update(overrides)
        for name, range_def in optimization_config.param_ranges.items():
            if name in params:
                continue
            if range_def.type == "int":
                params[name] = int(range_def.low)
            elif range_def.type == "float":
                params[name] = float(range_def.low)
            elif range_def.type == "categorical":
                params[name] = range_def.choices[0] if range_def.choices else None
        return params

    @staticmethod
    async def _prime_candles(
        optimizer: StrategyOptimizer,
        connector_name: str,
        trading_pair: str,
        interval: str,
        start_ts: int,
        end_ts: int,
    ) -> tuple[int, int]:
        candles = await optimizer.clob_source.get_candles(
            connector_name,
            trading_pair,
            interval,
            start_ts,
            end_ts,
        )
        if candles.data.empty:
            raise RuntimeError(f"No candle data available for {connector_name} {trading_pair} {interval}")

        optimizer.backtesting_engine._bt_engine.backtesting_data_provider.candles_feeds[
            f"{connector_name}_{trading_pair}_{interval}"
        ] = candles.data
        actual_start = int(candles.data["timestamp"].min())
        actual_end = int(candles.data["timestamp"].max())
        return actual_start, actual_end

    def _build_backtest_output_dir(self, study_name: str, trading_pair: str) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_study_name = self._safe_name(study_name)
        safe_pair = self._safe_name(trading_pair)
        return self.backtests_root / f"{safe_study_name}_{safe_pair}_{timestamp}"

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "item"

    @staticmethod
    def _serialize_controller_config(controller_config: Any) -> Dict[str, Any]:
        if hasattr(controller_config, "model_dump_json"):
            return json.loads(controller_config.model_dump_json())
        if hasattr(controller_config, "json"):
            return json.loads(controller_config.json())
        if hasattr(controller_config, "dict"):
            return controller_config.dict()
        raise TypeError("Unsupported controller config object.")

    @classmethod
    def _normalize_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._normalize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._normalize_value(item) for item in value]
        if isinstance(value, tuple):
            return [cls._normalize_value(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "model_dump"):
            return cls._normalize_value(value.model_dump(mode="json"))
        if hasattr(value, "name") and not isinstance(value, str):
            return value.name
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return str(value)
        return value

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, default=str)

    @staticmethod
    def _build_study_name(base_name: str, trading_pair: str) -> str:
        return f"{base_name}_{trading_pair.replace('-', '_')}"

    @staticmethod
    def _rewrite_result_json(result_model) -> None:
        if not result_model.output_dir:
            return
        result_file = Path(result_model.output_dir) / "result.json"
        with result_file.open("w", encoding="utf-8") as file:
            json.dump(result_model.model_dump(mode="json"), file, indent=2)
