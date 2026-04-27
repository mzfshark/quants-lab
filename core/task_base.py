"""
Backward-compatible task base for legacy QuantsLab tasks.

This adapter keeps the old ``core.task_base.BaseTask`` import working while
delegating lifecycle management to the v2 task system.
"""
from __future__ import annotations

import inspect
from collections.abc import Iterator, MutableMapping
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.tasks.base import BaseTask as ModernBaseTask
from core.tasks.base import ScheduleConfig, TaskConfig, TaskContext, TaskResult, TaskStatus


class LegacyTaskConfigAdapter(MutableMapping[str, Any]):
    """Expose TaskConfig with dict-like access for legacy task code."""

    def __init__(self, task_config: TaskConfig):
        self._task_config = task_config

    def __getitem__(self, key: str) -> Any:
        return self._task_config.config[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._task_config.config[key] = value

    def __delitem__(self, key: str) -> None:
        del self._task_config.config[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._task_config.config)

    def __len__(self) -> int:
        return len(self._task_config.config)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._task_config, item)

    @property
    def config(self) -> dict[str, Any]:
        return self._task_config.config

    def get(self, key: str, default: Any = None) -> Any:
        return self._task_config.config.get(key, default)

    def model_dump(self, *args, **kwargs):
        return self._task_config.model_dump(*args, **kwargs)


def _build_schedule(frequency: Any) -> ScheduleConfig:
    if isinstance(frequency, timedelta):
        return ScheduleConfig(type="frequency", frequency_hours=frequency.total_seconds() / 3600)
    if isinstance(frequency, (int, float)):
        return ScheduleConfig(type="frequency", frequency_hours=float(frequency))
    if isinstance(frequency, str) and frequency:
        return ScheduleConfig(type="cron", cron=frequency)
    return ScheduleConfig(type="frequency", frequency_hours=24.0)


def _coerce_task_config(*args, **kwargs) -> TaskConfig:
    if args and isinstance(args[0], TaskConfig):
        return args[0]
    config_obj = kwargs.get("config")
    if isinstance(config_obj, TaskConfig):
        return config_obj

    if args:
        name = kwargs.get("name", args[0] if len(args) > 0 else "legacy_task")
        frequency = kwargs.get("frequency", args[1] if len(args) > 1 else timedelta(days=1))
        raw_config = kwargs.get("config", args[2] if len(args) > 2 else {})
    else:
        name = kwargs.get("name", "legacy_task")
        frequency = kwargs.get("frequency", timedelta(days=1))
        raw_config = kwargs.get("config", {})

    return TaskConfig(
        name=name,
        enabled=True,
        task_class="legacy.task",
        schedule=_build_schedule(frequency),
        config=raw_config or {},
    )


class BaseTask(ModernBaseTask):
    """
    Compatibility layer for legacy tasks.

    Supports both constructor styles:
    - ``BaseTask(TaskConfig(...))``
    - ``BaseTask(name=..., frequency=timedelta(...), config={...})``
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        execute = cls.__dict__.get("execute")
        if execute is not None:
            signature = inspect.signature(execute)
            if len(signature.parameters) == 1:
                async def execute_wrapper(self, context: Optional[TaskContext] = None):
                    created_context = False
                    task_result = None

                    if context is None:
                        context = TaskContext(task_name=self.config.name, triggered_by="legacy-direct")
                        created_context = True
                        await self.setup(context)

                    try:
                        result = execute(self)
                        if inspect.isawaitable(result):
                            result = await result
                        task_result = TaskResult(
                            execution_id=context.execution_id,
                            task_name=self.config.name,
                            status=TaskStatus.COMPLETED,
                            started_at=context.started_at,
                            completed_at=datetime.now(timezone.utc),
                            result_data=result if isinstance(result, dict) else None,
                        )
                        task_result.calculate_duration()
                        return result
                    finally:
                        if created_context:
                            if task_result is None:
                                task_result = TaskResult(
                                    execution_id=context.execution_id,
                                    task_name=self.config.name,
                                    status=TaskStatus.FAILED,
                                    started_at=context.started_at,
                                    completed_at=datetime.now(timezone.utc),
                                )
                                task_result.calculate_duration()
                            await self.cleanup(context, task_result)

                cls._legacy_execute = execute
                cls.execute = execute_wrapper

        cleanup = cls.__dict__.get("cleanup")
        if cleanup is not None:
            cleanup_signature = inspect.signature(cleanup)
            if len(cleanup_signature.parameters) == 1:
                async def cleanup_wrapper(self, context=None, result=None):
                    cleanup_result = cleanup(self)
                    if inspect.isawaitable(cleanup_result):
                        await cleanup_result
                    return cleanup_result

                cls._legacy_cleanup = cleanup
                cls.cleanup = cleanup_wrapper

    def __init__(self, *args, **kwargs):
        task_config = _coerce_task_config(*args, **kwargs)
        super().__init__(task_config)
        self.task_config = task_config
        self.config = LegacyTaskConfigAdapter(task_config)
        self.mongo_client = None
        self.logs: list[str] = []
        self.metadata: dict[str, Any] = {}

    async def setup(self, context: TaskContext) -> None:
        await super().setup(context)
        self.mongo_client = self.mongodb_client
        if not self.metadata:
            self.reset_metadata()

    def reset_metadata(self) -> None:
        self.logs = []
        self.metadata = {
            "task_name": self.config.name,
            "last_reset_at": self.now(),
        }

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")