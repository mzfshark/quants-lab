import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.backtesting.universal_strategy import OptimizationConfig, UniversalOptimizationRunner
from core.condor.deploy_manager import CondorDeployManager
from core.services.hummingbot_api_client import HummingbotAPIClient
from core.tasks import BaseTask, TaskContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class UniversalOptimizationTask(BaseTask):
    """Run one or more universal optimization YAML configs inside the task orchestrator."""

    def __init__(self, config):
        super().__init__(config)
        task_config = self.config.config
        self.optimization_config_paths = task_config.get("optimization_configs", [])
        self.auto_export_to_condor = bool(task_config.get("auto_export_to_condor", True))
        self.auto_deploy_override = task_config.get("auto_deploy")
        self.notify_telegram_override = task_config.get("notify_telegram")
        self.continue_on_error = bool(task_config.get("continue_on_error", False))
        self.hummingbot_server = task_config.get("hummingbot_api_url") or task_config.get("hummingbot_host") or os.getenv(
            "HUMMINGBOT_API_URL",
            "localhost",
        )
        self.hummingbot_port = int(task_config.get("hummingbot_port", os.getenv("HUMMINGBOT_API_PORT", "8000")))
        self.hummingbot_username = task_config.get("hummingbot_username") or os.getenv("HUMMINGBOT_API_USERNAME")
        self.hummingbot_password = task_config.get("hummingbot_password") or os.getenv("HUMMINGBOT_API_PASSWORD")

    async def setup(self, context: TaskContext) -> None:
        await super().setup(context)
        if not self.optimization_config_paths:
            raise RuntimeError("optimization_configs not configured")
        missing_paths = [path for path in self.optimization_config_paths if not Path(self._normalize_path(path)).exists()]
        if missing_paths:
            raise RuntimeError(f"Optimization config(s) not found: {', '.join(missing_paths)}")
        logger.info(f"Setup completed for {context.task_name}")
        logger.info(f"Optimization configs: {len(self.optimization_config_paths)}")

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        summaries: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []

        for config_path in self.optimization_config_paths:
            normalized_path = self._normalize_path(config_path)
            api_client: Optional[HummingbotAPIClient] = None
            try:
                optimization_config = OptimizationConfig.from_yaml(normalized_path)
                if not self.auto_export_to_condor:
                    optimization_config.export.condor_agents_dir = ""
                if self.auto_deploy_override is not None:
                    optimization_config.export.auto_deploy = bool(self.auto_deploy_override)
                if self.notify_telegram_override is not None:
                    optimization_config.export.notify_telegram = bool(self.notify_telegram_override)

                deploy_manager = None
                if optimization_config.export.condor_agents_dir:
                    if optimization_config.export.auto_deploy:
                        api_client = HummingbotAPIClient(
                            server=self.hummingbot_server,
                            port=self.hummingbot_port,
                            username=self.hummingbot_username,
                            password=self.hummingbot_password,
                        )
                    deploy_manager = CondorDeployManager(
                        api_client=api_client,
                        agents_dir=Path(optimization_config.export.condor_agents_dir),
                    )

                runner = UniversalOptimizationRunner(deploy_manager=deploy_manager)
                results = await runner.optimize(optimization_config=optimization_config, source_path=normalized_path)
                for result in results:
                    validation = result.metadata.get("validation", {})
                    deployment = result.metadata.get("condor_deployment", {})
                    summaries.append(
                        {
                            "study_name": result.study_name,
                            "controller_name": result.controller_name,
                            "output_dir": result.output_dir,
                            "best_trial_id": result.best_trial.trial_id,
                            "objective_value": result.best_trial.objective_value,
                            "validation_passed": validation.get("passed"),
                            "exported_artifacts": len(deployment.get("artifacts", [])),
                            "auto_deploy_blocked": deployment.get("auto_deploy_blocked", False),
                        }
                    )
            except Exception as exc:
                logger.error(f"Optimization failed for {normalized_path}: {exc}")
                failures.append({"config_path": normalized_path, "error": str(exc)})
                if not self.continue_on_error:
                    raise
            finally:
                if api_client is not None:
                    await api_client.close()

        return {
            "status": "completed",
            "execution_id": context.execution_id,
            "optimization_configs": [self._normalize_path(path) for path in self.optimization_config_paths],
            "results": summaries,
            "failures": failures,
            "stats": {
                "configs_total": len(self.optimization_config_paths),
                "results_total": len(summaries),
                "failures_total": len(failures),
            },
        }

    async def on_success(self, context: TaskContext, result) -> None:
        stats = result.result_data.get("stats", {})
        logger.info(f"UniversalOptimizationTask succeeded in {result.duration_seconds:.2f}s")
        logger.info(
            f"Configs: {stats.get('configs_total', 0)} | Results: {stats.get('results_total', 0)} | Failures: {stats.get('failures_total', 0)}"
        )

    async def on_failure(self, context: TaskContext, result) -> None:
        logger.error(f"UniversalOptimizationTask failed: {result.error_message}")

    @staticmethod
    def _normalize_path(config_path: str) -> str:
        if not config_path.startswith("config/") and not os.path.isabs(config_path):
            return f"config/{config_path}"
        return config_path
