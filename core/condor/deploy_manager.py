from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from core.backtesting.engine import BacktestingEngine
from core.backtesting.optimization_results import OptimizationResult
from core.condor.cta_generator import CTAGenerator
from core.condor.models import DeploymentArtifact, DeploymentReport, RiskLimits
from core.services.hummingbot_api_client import HummingbotAPIClient


class CondorDeployManager:
    """Export and optionally deploy optimized configs into a Condor agents directory."""

    def __init__(
        self,
        api_client: Optional[HummingbotAPIClient] = None,
        agents_dir: Optional[Path] = None,
        cta_generator: Optional[CTAGenerator] = None,
        backtesting_engine: Optional[BacktestingEngine] = None,
    ):
        self.api_client = api_client
        self.agents_dir = Path(os.path.expanduser(str(agents_dir or os.getenv("CONDOR_AGENTS_DIR", "~/condor/agents"))))
        self.cta_generator = cta_generator or CTAGenerator()
        self.backtesting_engine = backtesting_engine or BacktestingEngine(load_cached_data=False)

    async def deploy_optimization_result(
        self,
        result: OptimizationResult,
        deploy_top_k: int = 1,
        dry_run: bool = True,
        risk_limits: Optional[RiskLimits] = None,
        tick_interval_seconds: int = 60,
        profile_name: Optional[str] = None,
    ) -> DeploymentReport:
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        report = DeploymentReport(
            study_name=result.study_name,
            controller_name=result.controller_name,
            deploy_top_k=deploy_top_k,
            dry_run=dry_run,
            created_at=datetime.now(timezone.utc),
        )

        top_trials = result.top_k_trials[: max(1, deploy_top_k)]
        for rank, trial in enumerate(top_trials, start=1):
            agent_name = self.cta_generator.build_agent_name(result, trial)
            agent_dir = self.agents_dir / self._build_bundle_dir_name(agent_name)
            agent_dir.mkdir(parents=True, exist_ok=True)

            controller_config = trial.controller_config.copy()
            controller_config.setdefault("id", agent_name)
            api_validation = await self._validate_controller_config(controller_config)

            artifact = DeploymentArtifact(
                rank=rank,
                trial_id=trial.trial_id,
                agent_name=agent_name,
                agent_dir=str(agent_dir),
                trading_pair=self._extract_primary_pair(controller_config),
                api_validation=api_validation,
                dry_run=dry_run,
            )

            try:
                controller_file = agent_dir / "controller.yml"
                agent_file = agent_dir / "agent.md"
                metadata_file = agent_dir / "optimization_meta.json"

                with controller_file.open("w", encoding="utf-8") as file:
                    yaml.safe_dump(controller_config, file, sort_keys=False, allow_unicode=False)

                agent_md = self.cta_generator.generate_agent_md(
                    result=result,
                    trial=trial,
                    risk_limits=risk_limits,
                    tick_interval_seconds=tick_interval_seconds,
                    connector=controller_config.get("connector_name"),
                    controller_config_file=controller_file.name,
                    agent_name=agent_name,
                )
                with agent_file.open("w", encoding="utf-8") as file:
                    file.write(agent_md)

                metadata_payload = {
                    "study_name": result.study_name,
                    "controller_name": result.controller_name,
                    "trial_id": trial.trial_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source_output_dir": result.output_dir,
                    "metrics": {
                        "sharpe_ratio": trial.sharpe_ratio,
                        "max_drawdown": trial.max_drawdown,
                        "total_return": trial.total_return,
                        "profit_factor": trial.profit_factor,
                        "win_rate": trial.win_rate,
                        "total_trades": trial.total_trades,
                    },
                    "metadata": result.metadata,
                }
                with metadata_file.open("w", encoding="utf-8") as file:
                    json.dump(metadata_payload, file, indent=2)

                artifact.files = {
                    "controller": str(controller_file),
                    "agent": str(agent_file),
                    "metadata": str(metadata_file),
                }

                if self.api_client is not None and not dry_run:
                    upload_response = await self.api_client.add_controller_config(controller_config)
                    artifact.api_responses["upload_controller_config"] = upload_response
                    if not self.api_client.is_success_response(upload_response):
                        artifact.errors.append(str(upload_response))
                    else:
                        profile = profile_name or os.getenv("HUMMINGBOT_PROFILE") or os.getenv("BACKEND_API_PROFILE") or "master_account"
                        deploy_response = await self.api_client.deploy_v2_controllers(
                            name=agent_name,
                            profile=profile,
                            controllers=[controller_config.get("id", agent_name)],
                        )
                        artifact.api_responses["deploy"] = deploy_response
                        artifact.deployed = self.api_client.is_success_response(deploy_response)
                        if not artifact.deployed:
                            artifact.errors.append(str(deploy_response))
                report.artifacts.append(artifact)
            except Exception as exc:
                artifact.errors.append(str(exc))
                report.artifacts.append(artifact)
                report.errors.append(f"{agent_name}: {exc}")

        return report

    async def _validate_controller_config(self, controller_config: Dict[str, Any]) -> Dict[str, Any]:
        validation = {"local_schema_valid": False, "api_schema_available": False, "api_schema": None}
        validated = self.backtesting_engine.get_controller_config_instance_from_dict(controller_config)
        validation["local_schema_valid"] = validated is not None

        if self.api_client is None:
            return validation

        controller_name = controller_config.get("controller_name")
        if not controller_name:
            return validation

        schema_response = await self.api_client.get_controller_schema(controller_name)
        if self.api_client.is_success_response(schema_response) and schema_response:
            validation["api_schema_available"] = True
            validation["api_schema"] = schema_response
        return validation

    @staticmethod
    def _build_bundle_dir_name(agent_name: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", agent_name).strip("_")
        return f"{safe_name}_{timestamp}"

    @staticmethod
    def _extract_primary_pair(controller_config: Dict[str, Any]) -> Optional[str]:
        for key in ("trading_pair", "candles_trading_pair", "base_trading_pair"):
            value = controller_config.get(key)
            if isinstance(value, str) and value:
                return value
        return None
