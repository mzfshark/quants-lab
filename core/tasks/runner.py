"""
Enhanced Task Runner with new task system integration.
"""
import asyncio
import importlib
import inspect
import logging
import os
import re
import signal
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
import yaml

from core.data_paths import data_paths
from core.tasks.api import app, set_orchestrator
from core.tasks.base import BaseTask, ScheduleConfig, TaskConfig
from core.tasks.orchestrator import TaskOrchestrator
from core.tasks.storage import create_task_storage, get_storage_backend

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


class TaskRunner:
    """Enhanced task runner with new task system."""

    def __init__(self, config_path: str = "config/tasks.yml", enable_api: bool = None):
        self.config_path = config_path
        self.orchestrator: Optional[TaskOrchestrator] = None
        self.api_server: Optional[uvicorn.Server] = None
        if enable_api is not None:
            self.api_enabled = enable_api
        else:
            self.api_enabled = os.getenv("TASK_API_ENABLED", "false").lower() == "true"
        self.api_host = os.getenv("TASK_API_HOST", "0.0.0.0")
        self.api_port = int(os.getenv("TASK_API_PORT", "8000"))
        self.config = self._load_config()
        self._setup_signal_handlers()

    def _load_config(self) -> Dict[str, Any]:
        config_file = Path(self.config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
        config = self._expand_env_vars(config)
        logger.info(f"Loaded configuration from: {self.config_path}")
        return config

    def _expand_env_vars(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._expand_env_vars(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._expand_env_vars(item) for item in value]
        if isinstance(value, str):
            return ENV_VAR_PATTERN.sub(lambda match: os.getenv(match.group(1), match.group(0)), value)
        return value

    def _setup_signal_handlers(self):
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            if self.orchestrator:
                asyncio.create_task(self.stop())
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _get_storage_config(self) -> Dict[str, Any]:
        storage_config = self.config.get("storage", {})
        return storage_config if isinstance(storage_config, dict) else {}

    def _resolve_storage_backend(self) -> str:
        storage_config = self._get_storage_config()
        configured_backend = os.getenv("QUANTS_LAB_STORAGE", "").strip().lower() or storage_config.get("type")
        return get_storage_backend(configured_backend)

    def _log_storage_configuration(self) -> None:
        storage_config = self._get_storage_config()
        backend = self._resolve_storage_backend()
        logger.info("=== Task Storage Configuration ===")
        logger.info(f"Backend: {backend}")
        if storage_config:
            logger.info(f"Config storage.type: {storage_config.get('type', 'unset')}")
        if os.getenv("QUANTS_LAB_STORAGE"):
            logger.info(f"Env QUANTS_LAB_STORAGE: {os.getenv('QUANTS_LAB_STORAGE')}")
        if backend == "mongodb":
            logger.info(f"MONGO_URI: {'Configured' if os.getenv('MONGO_URI') else 'Not configured'}")
            logger.info(f"MONGO_DATABASE: {os.getenv('MONGO_DATABASE', 'quants_lab')}")
        else:
            sqlite_path = (
                storage_config.get("sqlite_path")
                or storage_config.get("path")
                or os.getenv("QUANTS_LAB_SQLITE_PATH")
                or str(data_paths.processed_dir / "task_storage.db")
            )
            logger.info(f"SQLite path: {sqlite_path}")
        logger.info("==================================")

    def _create_task_config(self, task_name: str, task_data: Dict[str, Any]) -> TaskConfig:
        schedule_data = task_data.get("schedule", {})
        if "frequency_hours" in task_data:
            schedule_data = {"type": "frequency", "frequency_hours": task_data["frequency_hours"]}
        schedule = ScheduleConfig(**schedule_data)
        return TaskConfig(
            name=task_name,
            enabled=task_data.get("enabled", True),
            task_class=task_data["task_class"],
            schedule=schedule,
            max_retries=task_data.get("max_retries", 3),
            retry_delay_seconds=task_data.get("retry_delay_seconds", 60),
            timeout_seconds=task_data.get("timeout_seconds"),
            dependencies=task_data.get("dependencies", []),
            config=task_data.get("config", {}),
            tags=task_data.get("tags", []),
        )

    def _import_task_class(self, task_class_path: str) -> type:
        try:
            from core.tasks.registry import resolve_task_class
            resolved_path = resolve_task_class(task_class_path)
            module_path, class_name = resolved_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            logger.error(f"Error importing task class {task_class_path} (resolved to {resolved_path}): {e}")
            raise

    def _build_legacy_frequency(self, config: TaskConfig):
        if config.schedule.type == "cron" and config.schedule.cron:
            return config.schedule.cron
        return timedelta(hours=config.schedule.frequency_hours or 24.0)

    def _create_task_instance(self, config: TaskConfig) -> BaseTask:
        task_class = self._import_task_class(config.task_class)
        signature = inspect.signature(task_class.__init__)
        param_names = [param.name for param in list(signature.parameters.values())[1:]]
        if "frequency" in param_names and "config" in param_names:
            return task_class(name=config.name, frequency=self._build_legacy_frequency(config), config=config.config)
        return task_class(config)

    async def _initialize_tasks(self) -> List[BaseTask]:
        tasks = []
        if "tasks" not in self.config:
            logger.warning("No tasks found in configuration")
            return tasks
        for task_name, task_data in self.config["tasks"].items():
            try:
                logger.info(f"Initializing task: {task_name}")
                config = self._create_task_config(task_name, task_data)
                task = self._create_task_instance(config)
                tasks.append(task)
                logger.info(f"Successfully initialized task: {task_name}")
            except Exception as e:
                logger.error(f"Failed to initialize task {task_name}: {e}")
                if self.config.get("strict_mode", False):
                    raise
        return tasks

    async def _start_api_server(self):
        if not self.api_enabled:
            logger.info("API server disabled")
            return
        logger.info(f"Starting API server on {self.api_host}:{self.api_port}")
        set_orchestrator(self.orchestrator)
        config = uvicorn.Config(app, host=self.api_host, port=self.api_port, log_level="info", loop="asyncio")
        self.api_server = uvicorn.Server(config)
        await self.api_server.serve()

    async def start(self):
        logger.info("Starting QuantsLab Task Runner v2.0")
        try:
            self._log_storage_configuration()
            storage_config = self._get_storage_config()
            storage = create_task_storage(
                storage_backend=self._resolve_storage_backend(),
                storage_config=storage_config,
            )
            self.orchestrator = TaskOrchestrator(
                storage=storage,
                max_concurrent_tasks=self.config.get("max_concurrent_tasks", 10),
                retry_failed_tasks=self.config.get("retry_failed_tasks", True),
            )
            tasks = await self._initialize_tasks()
            if not tasks:
                logger.warning("No tasks initialized, exiting...")
                return
            for task in tasks:
                self.orchestrator.add_task(task)
            logger.info(f"Initialized {len(tasks)} tasks")
            api_task = asyncio.create_task(self._start_api_server()) if self.api_enabled else None
            orchestrator_task = asyncio.create_task(self.orchestrator.start())
            tasks_to_wait = [orchestrator_task]
            if api_task:
                tasks_to_wait.append(api_task)
            await asyncio.gather(*tasks_to_wait)
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            logger.error(f"Error starting task runner: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self):
        logger.info("Stopping task runner...")
        if self.orchestrator:
            await self.orchestrator.stop()
        if self.api_server:
            logger.info("Stopping API server...")
            self.api_server.should_exit = True
        logger.info("Task runner stopped")

    async def reload_config(self, new_config_path: Optional[str] = None):
        logger.info("Reloading configuration...")
        if new_config_path:
            self.config_path = new_config_path
        new_config = self._load_config()
        if self.orchestrator:
            old_task_names = set(self.orchestrator.tasks.keys())
            new_tasks = await self._initialize_tasks()
            new_task_names = {task.config.name for task in new_tasks}
            for task_name in old_task_names - new_task_names:
                self.orchestrator.remove_task(task_name)
                logger.info(f"Removed task: {task_name}")
            for task in new_tasks:
                task_name = task.config.name
                if task_name in old_task_names:
                    await self.orchestrator.reload_task(task_name, task.config)
                    logger.info(f"Reloaded task: {task_name}")
                else:
                    self.orchestrator.add_task(task)
                    logger.info(f"Added new task: {task_name}")
        self.config = new_config
        logger.info("Configuration reloaded successfully")

    def load_config(self) -> Dict[str, Any]:
        return self.config

    def validate_config(self) -> bool:
        try:
            logger.info("Validating configuration...")
            if "tasks" not in self.config:
                logger.error("No 'tasks' section found in configuration")
                return False
            for task_name, task_data in self.config["tasks"].items():
                config = self._create_task_config(task_name, task_data)
                self._import_task_class(config.task_class)
            logger.info("Configuration is valid")
            return True
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False
