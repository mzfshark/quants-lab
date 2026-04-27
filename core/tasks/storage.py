"""
Storage implementations for the task management system.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.data_paths import data_paths
from core.tasks.base import TaskContext, TaskResult, TaskStatus

try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
    from pymongo import ASCENDING, DESCENDING
    MONGODB_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    AsyncIOMotorClient = Any
    AsyncIOMotorDatabase = Any
    ASCENDING = 1
    DESCENDING = -1
    MONGODB_AVAILABLE = False

logger = logging.getLogger(__name__)


def _status_value(status: TaskStatus | str) -> str:
    return status.value if isinstance(status, TaskStatus) else status


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _dumps_json(value: Optional[Dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, default=_json_default)


def _loads_json(value: Optional[str], default: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if value is None or value == "":
        return default
    return json.loads(value)


def _parse_datetime(value: Optional[Any]) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class TaskExecutionRecord(BaseModel):
    execution_id: str
    task_name: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    triggered_by: str
    attempt_number: int
    parent_execution_id: Optional[str] = None
    result_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TaskStorage(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    @abstractmethod
    async def save_execution(self, result: TaskResult, context: TaskContext) -> None:
        pass

    @abstractmethod
    async def get_last_execution(self, task_name: str) -> Optional[TaskExecutionRecord]:
        pass

    @abstractmethod
    async def get_executions(
        self,
        task_name: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[TaskExecutionRecord]:
        pass

    @abstractmethod
    async def mark_task_running(self, task_name: str, execution_id: str) -> bool:
        pass

    @abstractmethod
    async def mark_task_completed(self, task_name: str) -> None:
        pass


class MongoDBTaskStorage(TaskStorage):
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None
        self.executions_collection = None
        self.schedules_collection = None

    async def initialize(self) -> None:
        if not MONGODB_AVAILABLE:
            raise RuntimeError(
                "MongoDB storage requested but motor/pymongo are not installed. "
                "Use QUANTS_LAB_STORAGE=sqlite for local development."
            )

        from core.database_manager import db_manager

        mongodb_client = await db_manager.get_mongodb_client()
        if mongodb_client is None:
            raise RuntimeError(
                "Failed to get MongoDB client from database manager. "
                "Please ensure QUANTS_LAB_STORAGE=mongodb and MONGO_URI are configured."
            )

        database = os.getenv("MONGO_DATABASE", "quants_lab")
        logger.info("=== MongoDB Storage Initialization ===")
        logger.info("Using centralized database manager")
        logger.info(f"Database: {database}")
        logger.info("=======================================")

        self.client = mongodb_client.client
        self.db = mongodb_client.get_database(database)
        self.executions_collection = self.db["task_executions"]
        self.schedules_collection = self.db["task_schedules"]

        await self.executions_collection.create_index([("task_name", ASCENDING), ("started_at", DESCENDING)])
        await self.executions_collection.create_index([("status", ASCENDING), ("started_at", DESCENDING)])
        await self.executions_collection.create_index([("execution_id", ASCENDING)], unique=True)
        await self.executions_collection.create_index([("parent_execution_id", ASCENDING)], sparse=True)
        await self.executions_collection.create_index([("started_at", DESCENDING)])
        await self.executions_collection.create_index([("created_at", ASCENDING)], expireAfterSeconds=90 * 24 * 60 * 60)
        await self.schedules_collection.create_index([("task_name", ASCENDING)], unique=True)
        await self.schedules_collection.create_index([("is_running", ASCENDING)])
        logger.info("MongoDB storage initialized successfully")

    async def close(self) -> None:
        self.client = None
        self.db = None
        self.executions_collection = None
        self.schedules_collection = None

    async def save_execution(self, result: TaskResult, context: TaskContext) -> None:
        execution_doc = {
            "execution_id": result.execution_id,
            "task_name": result.task_name,
            "status": _status_value(result.status),
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "duration_seconds": result.duration_seconds,
            "triggered_by": context.triggered_by,
            "attempt_number": context.attempt_number,
            "parent_execution_id": context.parent_execution_id,
            "result_data": result.result_data,
            "error_message": result.error_message,
            "error_traceback": result.error_traceback,
            "metrics": result.metrics,
            "metadata": context.metadata,
            "created_at": datetime.utcnow(),
        }
        await self.executions_collection.insert_one(execution_doc)

        schedule_update = {
            "$set": {
                "last_run": result.started_at,
                "is_running": _status_value(result.status) == TaskStatus.RUNNING.value,
                "current_execution_id": result.execution_id if _status_value(result.status) == TaskStatus.RUNNING.value else None,
                "updated_at": datetime.utcnow(),
            },
            "$inc": {
                "run_count": 1,
                "success_count": 1 if _status_value(result.status) == TaskStatus.COMPLETED.value else 0,
                "failure_count": 1 if _status_value(result.status) == TaskStatus.FAILED.value else 0,
            },
        }

        if _status_value(result.status) == TaskStatus.COMPLETED.value and result.duration_seconds is not None:
            schedule_doc = await self.schedules_collection.find_one({"task_name": result.task_name})
            if schedule_doc:
                current_avg = schedule_doc.get("average_duration_seconds", 0)
                current_count = schedule_doc.get("success_count", 0)
                new_avg = ((current_avg * current_count) + result.duration_seconds) / (current_count + 1)
                schedule_update["$set"]["average_duration_seconds"] = new_avg
            else:
                schedule_update["$set"]["average_duration_seconds"] = result.duration_seconds

        await self.schedules_collection.update_one({"task_name": result.task_name}, schedule_update, upsert=True)

    async def get_last_execution(self, task_name: str) -> Optional[TaskExecutionRecord]:
        doc = await self.executions_collection.find_one({"task_name": task_name}, sort=[("started_at", DESCENDING)])
        if not doc:
            return None
        return TaskExecutionRecord(
            execution_id=doc["execution_id"],
            task_name=doc["task_name"],
            status=doc["status"],
            started_at=doc["started_at"],
            completed_at=doc.get("completed_at"),
            duration_seconds=doc.get("duration_seconds"),
            triggered_by=doc["triggered_by"],
            attempt_number=doc["attempt_number"],
            parent_execution_id=doc.get("parent_execution_id"),
            result_data=doc.get("result_data"),
            error_message=doc.get("error_message"),
            error_traceback=doc.get("error_traceback"),
            metrics=doc.get("metrics", {}),
            metadata=doc.get("metadata", {}),
            created_at=doc.get("created_at", datetime.utcnow()),
        )

    async def get_executions(
        self,
        task_name: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[TaskExecutionRecord]:
        query: Dict[str, Any] = {}
        if task_name:
            query["task_name"] = task_name
        if status:
            query["status"] = status.value
        if start_time or end_time:
            query["started_at"] = {}
            if start_time:
                query["started_at"]["$gte"] = start_time
            if end_time:
                query["started_at"]["$lte"] = end_time

        cursor = self.executions_collection.find(query).sort("started_at", DESCENDING).limit(limit)
        executions = []
        async for doc in cursor:
            executions.append(
                TaskExecutionRecord(
                    execution_id=doc["execution_id"],
                    task_name=doc["task_name"],
                    status=doc["status"],
                    started_at=doc["started_at"],
                    completed_at=doc.get("completed_at"),
                    duration_seconds=doc.get("duration_seconds"),
                    triggered_by=doc["triggered_by"],
                    attempt_number=doc["attempt_number"],
                    parent_execution_id=doc.get("parent_execution_id"),
                    result_data=doc.get("result_data"),
                    error_message=doc.get("error_message"),
                    error_traceback=doc.get("error_traceback"),
                    metrics=doc.get("metrics", {}),
                    metadata=doc.get("metadata", {}),
                    created_at=doc.get("created_at", datetime.utcnow()),
                )
            )
        return executions

    async def get_task_performance_metrics(self, task_name: Optional[str] = None, last_days: int = 7) -> Dict[str, Any]:
        pipeline = [{"$match": {"started_at": {"$gte": datetime.utcnow() - timedelta(days=last_days)}}}]
        if task_name:
            pipeline[0]["$match"]["task_name"] = task_name
        pipeline.extend([
            {"$group": {
                "_id": "$task_name",
                "total_executions": {"$sum": 1},
                "successful_executions": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
                "failed_executions": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
                "avg_duration": {"$avg": "$duration_seconds"},
                "max_duration": {"$max": "$duration_seconds"},
                "min_duration": {"$min": "$duration_seconds"},
            }},
            {"$project": {
                "task_name": "$_id",
                "total_executions": 1,
                "successful_executions": 1,
                "failed_executions": 1,
                "success_rate": {"$multiply": [{"$divide": ["$successful_executions", "$total_executions"]}, 100]},
                "avg_duration": {"$round": ["$avg_duration", 2]},
                "max_duration": 1,
                "min_duration": 1,
                "_id": 0,
            }},
        ])
        cursor = self.executions_collection.aggregate(pipeline)
        metrics = []
        async for doc in cursor:
            metrics.append(doc)
        return {
            "period_days": last_days,
            "start_date": (datetime.utcnow() - timedelta(days=last_days)).isoformat(),
            "end_date": datetime.utcnow().isoformat(),
            "metrics": metrics,
        }

    async def mark_task_running(self, task_name: str, execution_id: str) -> bool:
        result = await self.schedules_collection.update_one(
            {"task_name": task_name, "is_running": False},
            {"$set": {"is_running": True, "current_execution_id": execution_id, "updated_at": datetime.utcnow()}},
        )
        if result.modified_count == 0:
            existing = await self.schedules_collection.find_one({"task_name": task_name})
            if existing and existing.get("is_running", False):
                return False
            if not existing:
                await self.schedules_collection.insert_one({
                    "task_name": task_name,
                    "is_running": True,
                    "current_execution_id": execution_id,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                })
                return True
        return result.modified_count > 0

    async def mark_task_completed(self, task_name: str) -> None:
        await self.schedules_collection.update_one(
            {"task_name": task_name},
            {"$set": {"is_running": False, "current_execution_id": None, "updated_at": datetime.utcnow()}},
        )

    async def get_running_tasks(self) -> List[str]:
        cursor = self.schedules_collection.find({"is_running": True})
        tasks = []
        async for doc in cursor:
            tasks.append(doc["task_name"])
        return tasks

    async def get_task_schedule(self, task_name: str) -> Optional[Dict[str, Any]]:
        return await self.schedules_collection.find_one({"task_name": task_name})


class SQLiteTaskStorage(TaskStorage):
    def __init__(self, db_path: Optional[str | Path] = None):
        configured_path = db_path or os.getenv("QUANTS_LAB_SQLITE_PATH")
        self.db_path = Path(configured_path) if configured_path else data_paths.processed_dir / "task_storage.db"
        self.conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self.conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    async def initialize(self) -> None:
        conn = self._connect()
        conn.execute("CREATE TABLE IF NOT EXISTS task_executions (execution_id TEXT PRIMARY KEY, task_name TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, duration_seconds REAL, triggered_by TEXT NOT NULL, attempt_number INTEGER NOT NULL, parent_execution_id TEXT, result_data TEXT, error_message TEXT, error_traceback TEXT, metrics TEXT, metadata TEXT, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS task_schedules (task_name TEXT PRIMARY KEY, is_running INTEGER NOT NULL DEFAULT 0, current_execution_id TEXT, last_run TEXT, run_count INTEGER NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0, average_duration_seconds REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_executions_task_name_started_at ON task_executions(task_name, started_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_executions_status_started_at ON task_executions(status, started_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_executions_parent_execution_id ON task_executions(parent_execution_id)")
        conn.commit()
        logger.info(f"SQLite task storage initialized at {self.db_path}")

    async def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _row_to_record(self, row: sqlite3.Row) -> TaskExecutionRecord:
        return TaskExecutionRecord(
            execution_id=row["execution_id"],
            task_name=row["task_name"],
            status=row["status"],
            started_at=_parse_datetime(row["started_at"]),
            completed_at=_parse_datetime(row["completed_at"]),
            duration_seconds=row["duration_seconds"],
            triggered_by=row["triggered_by"],
            attempt_number=row["attempt_number"],
            parent_execution_id=row["parent_execution_id"],
            result_data=_loads_json(row["result_data"]),
            error_message=row["error_message"],
            error_traceback=row["error_traceback"],
            metrics=_loads_json(row["metrics"], {}) or {},
            metadata=_loads_json(row["metadata"], {}) or {},
            created_at=_parse_datetime(row["created_at"]) or datetime.utcnow(),
        )

    async def save_execution(self, result: TaskResult, context: TaskContext) -> None:
        conn = self._connect()
        status_value = _status_value(result.status)
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO task_executions (execution_id, task_name, status, started_at, completed_at, duration_seconds, triggered_by, attempt_number, parent_execution_id, result_data, error_message, error_traceback, metrics, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result.execution_id, result.task_name, status_value, result.started_at.isoformat(), result.completed_at.isoformat() if result.completed_at else None, result.duration_seconds, context.triggered_by, context.attempt_number, context.parent_execution_id, _dumps_json(result.result_data), result.error_message, result.error_traceback, _dumps_json(result.metrics), _dumps_json(context.metadata), now),
        )

        existing = conn.execute("SELECT success_count, average_duration_seconds, created_at FROM task_schedules WHERE task_name = ?", (result.task_name,)).fetchone()
        success_count = existing["success_count"] if existing else 0
        average_duration = existing["average_duration_seconds"] if existing else None
        created_at = existing["created_at"] if existing else now

        if status_value == TaskStatus.COMPLETED.value and result.duration_seconds is not None:
            if average_duration is None:
                average_duration = result.duration_seconds
            else:
                average_duration = ((average_duration * success_count) + result.duration_seconds) / (success_count + 1)

        conn.execute(
            "INSERT INTO task_schedules (task_name, is_running, current_execution_id, last_run, run_count, success_count, failure_count, average_duration_seconds, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(task_name) DO UPDATE SET is_running = excluded.is_running, current_execution_id = excluded.current_execution_id, last_run = excluded.last_run, run_count = task_schedules.run_count + 1, success_count = task_schedules.success_count + excluded.success_count, failure_count = task_schedules.failure_count + excluded.failure_count, average_duration_seconds = excluded.average_duration_seconds, updated_at = excluded.updated_at",
            (result.task_name, 1 if status_value == TaskStatus.RUNNING.value else 0, result.execution_id if status_value == TaskStatus.RUNNING.value else None, result.started_at.isoformat(), 1, 1 if status_value == TaskStatus.COMPLETED.value else 0, 1 if status_value == TaskStatus.FAILED.value else 0, average_duration, created_at, now),
        )
        conn.commit()

    async def get_last_execution(self, task_name: str) -> Optional[TaskExecutionRecord]:
        conn = self._connect()
        row = conn.execute("SELECT * FROM task_executions WHERE task_name = ? ORDER BY started_at DESC LIMIT 1", (task_name,)).fetchone()
        return self._row_to_record(row) if row else None

    async def get_executions(self, task_name: Optional[str] = None, status: Optional[TaskStatus] = None, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None, limit: int = 100) -> List[TaskExecutionRecord]:
        conn = self._connect()
        clauses = []
        params: List[Any] = []
        if task_name:
            clauses.append("task_name = ?")
            params.append(task_name)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if start_time:
            clauses.append("started_at >= ?")
            params.append(start_time.isoformat())
        if end_time:
            clauses.append("started_at <= ?")
            params.append(end_time.isoformat())
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = conn.execute(f"SELECT * FROM task_executions {where_clause} ORDER BY started_at DESC LIMIT ?", params).fetchall()
        return [self._row_to_record(row) for row in rows]

    async def get_task_performance_metrics(self, task_name: Optional[str] = None, last_days: int = 7) -> Dict[str, Any]:
        conn = self._connect()
        start_date = datetime.utcnow() - timedelta(days=last_days)
        clauses = ["started_at >= ?"]
        params: List[Any] = [start_date.isoformat()]
        if task_name:
            clauses.append("task_name = ?")
            params.append(task_name)
        query = "SELECT task_name, COUNT(*) AS total_executions, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS successful_executions, SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_executions, AVG(duration_seconds) AS avg_duration, MAX(duration_seconds) AS max_duration, MIN(duration_seconds) AS min_duration FROM task_executions WHERE " + " AND ".join(clauses) + " GROUP BY task_name"
        rows = conn.execute(query, params).fetchall()
        metrics = []
        for row in rows:
            total = row["total_executions"] or 0
            successful = row["successful_executions"] or 0
            metrics.append({
                "task_name": row["task_name"],
                "total_executions": total,
                "successful_executions": successful,
                "failed_executions": row["failed_executions"] or 0,
                "success_rate": (successful / total * 100) if total else 0,
                "avg_duration": round(row["avg_duration"], 2) if row["avg_duration"] is not None else None,
                "max_duration": row["max_duration"],
                "min_duration": row["min_duration"],
            })
        return {"period_days": last_days, "start_date": start_date.isoformat(), "end_date": datetime.utcnow().isoformat(), "metrics": metrics}

    async def mark_task_running(self, task_name: str, execution_id: str) -> bool:
        conn = self._connect()
        existing = conn.execute("SELECT is_running, created_at, run_count, success_count, failure_count, average_duration_seconds FROM task_schedules WHERE task_name = ?", (task_name,)).fetchone()
        if existing and existing["is_running"]:
            return False
        now = datetime.utcnow().isoformat()
        created_at = existing["created_at"] if existing else now
        run_count = existing["run_count"] if existing else 0
        success_count = existing["success_count"] if existing else 0
        failure_count = existing["failure_count"] if existing else 0
        average_duration = existing["average_duration_seconds"] if existing else None
        conn.execute(
            "INSERT OR REPLACE INTO task_schedules (task_name, is_running, current_execution_id, last_run, run_count, success_count, failure_count, average_duration_seconds, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_name, 1, execution_id, None, run_count, success_count, failure_count, average_duration, created_at, now),
        )
        conn.commit()
        return True

    async def mark_task_completed(self, task_name: str) -> None:
        conn = self._connect()
        conn.execute("UPDATE task_schedules SET is_running = 0, current_execution_id = NULL, updated_at = ? WHERE task_name = ?", (datetime.utcnow().isoformat(), task_name))
        conn.commit()

    async def get_running_tasks(self) -> List[str]:
        conn = self._connect()
        rows = conn.execute("SELECT task_name FROM task_schedules WHERE is_running = 1").fetchall()
        return [row["task_name"] for row in rows]

    async def get_task_schedule(self, task_name: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        row = conn.execute("SELECT * FROM task_schedules WHERE task_name = ?", (task_name,)).fetchone()
        return dict(row) if row else None


def get_storage_backend(storage_backend: Optional[str] = None) -> str:
    backend = (storage_backend or os.getenv("QUANTS_LAB_STORAGE", "")).strip().lower()
    if not backend:
        backend = "mongodb" if os.getenv("MONGO_URI") else "sqlite"
    if backend not in {"mongodb", "sqlite"}:
        raise ValueError("QUANTS_LAB_STORAGE must be either 'sqlite' or 'mongodb'")
    return backend


def create_task_storage(storage_backend: Optional[str] = None) -> TaskStorage:
    backend = get_storage_backend(storage_backend)
    if backend == "mongodb":
        return MongoDBTaskStorage()
    return SQLiteTaskStorage()
