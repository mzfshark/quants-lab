"""
QuantsLab Core Task System v2.0

This module provides the core infrastructure for task management, scheduling,
and orchestration in the QuantsLab trading system.
"""

from .base import (
    BaseTask,
    TaskConfig,
    TaskContext,
    TaskResult,
    TaskStatus,
    ScheduleConfig,
    TaskDependency,
)
from .storage import (
    TaskStorage,
    MongoDBTaskStorage,
    SQLiteTaskStorage,
    TaskExecutionRecord,
    create_task_storage,
    get_storage_backend,
)
from .orchestrator import TaskOrchestrator
from .runner import TaskRunner

__all__ = [
    "BaseTask",
    "TaskConfig",
    "TaskContext",
    "TaskResult",
    "TaskStatus",
    "ScheduleConfig",
    "TaskDependency",
    "TaskStorage",
    "MongoDBTaskStorage",
    "SQLiteTaskStorage",
    "TaskExecutionRecord",
    "create_task_storage",
    "get_storage_backend",
    "TaskOrchestrator",
    "TaskRunner",
]

__version__ = "2.0.0"
