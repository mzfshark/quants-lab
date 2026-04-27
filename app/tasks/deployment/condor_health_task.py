import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.data_paths import data_paths
from core.notifiers.base import NotificationMessage
from core.services.hummingbot_api_client import HummingbotAPIClient
from core.tasks import BaseTask, TaskContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CondorHealthTask(BaseTask):
    """Health-check task for Condor/Hummingbot runtime state."""

    def __init__(self, config):
        super().__init__(config)
        task_config = self.config.config
        self.hummingbot_server = task_config.get("hummingbot_api_url") or task_config.get("hummingbot_host", "localhost")
        self.hummingbot_port = int(task_config.get("hummingbot_port", 8000))
        self.hummingbot_username = task_config.get("hummingbot_username")
        self.hummingbot_password = task_config.get("hummingbot_password")
        self.alert_on_bot_stopped = bool(task_config.get("alert_on_bot_stopped", True))
        self.min_portfolio_value = self._coerce_float(task_config.get("min_portfolio_value"))
        self.notification_config = task_config.get("notification", {})
        self.output_dir = Path(
            task_config.get(
                "output_dir",
                data_paths.base_path / "app" / "outputs" / "reports" / "condor_health",
            )
        )
        self.api_client: Optional[HummingbotAPIClient] = None

    async def setup(self, context: TaskContext) -> None:
        await super().setup(context)
        self.api_client = HummingbotAPIClient(
            server=self.hummingbot_server,
            port=self.hummingbot_port,
            username=self.hummingbot_username,
            password=self.hummingbot_password,
        )
        logger.info(f"Setup completed for {context.task_name}")
        logger.info(f"Hummingbot API target: {self.hummingbot_server}:{self.hummingbot_port}")

    async def cleanup(self, context: TaskContext, result) -> None:
        await super().cleanup(context, result)
        if self.api_client is not None:
            await self.api_client.close()

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        started_at = datetime.now(timezone.utc)
        snapshot = await self.api_client.get_runtime_snapshot() if self.api_client is not None else self._offline_snapshot()
        alerts = self._build_alerts(snapshot)
        output_file = await self._store_report(context, snapshot, alerts, started_at)
        notification_sent = await self._send_notification_if_needed(snapshot, alerts)

        return {
            "status": "completed",
            "execution_id": context.execution_id,
            "timestamp": started_at.isoformat(),
            "api_available": snapshot["api_available"],
            "alerts": alerts,
            "notification_sent": notification_sent,
            "output_path": str(output_file),
            "stats": {
                "bots_total": snapshot["bot_summary"]["total"],
                "bots_active": snapshot["bot_summary"]["active"],
                "bots_stopped": snapshot["bot_summary"]["stopped"],
                "alerts_count": len(alerts),
                "portfolio_value": snapshot.get("portfolio_value"),
            },
        }

    async def on_success(self, context: TaskContext, result) -> None:
        stats = result.result_data.get("stats", {})
        logger.info(f"CondorHealthTask succeeded in {result.duration_seconds:.2f}s")
        logger.info(
            f"Bots active={stats.get('bots_active', 0)}/{stats.get('bots_total', 0)} | Alerts={stats.get('alerts_count', 0)}"
        )

    async def on_failure(self, context: TaskContext, result) -> None:
        logger.error(f"CondorHealthTask failed: {result.error_message}")

    def _build_alerts(self, snapshot: Dict[str, Any]) -> List[str]:
        alerts: List[str] = []
        if not snapshot["api_available"]:
            alerts.append("Hummingbot API unavailable.")

        if self.alert_on_bot_stopped:
            stopped = [
                bot["bot_name"]
                for bot in snapshot["bots"]
                if str(bot.get("status") or "").lower() in {"stopped", "offline", "error", "failed", "inactive"}
            ]
            if stopped:
                alerts.append(f"Stopped bots detected: {', '.join(stopped[:5])}")

        portfolio_value = snapshot.get("portfolio_value")
        if self.min_portfolio_value is not None and portfolio_value is not None and portfolio_value < self.min_portfolio_value:
            alerts.append(
                f"Portfolio value ${portfolio_value:,.2f} is below threshold ${self.min_portfolio_value:,.2f}."
            )

        return alerts

    async def _store_report(
        self,
        context: TaskContext,
        snapshot: Dict[str, Any],
        alerts: List[str],
        started_at: datetime,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_file = self.output_dir / f"condor_health_{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        payload = {
            "timestamp": started_at.isoformat(),
            "execution_id": context.execution_id,
            "snapshot": snapshot,
            "alerts": alerts,
        }
        with output_file.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, default=str)
        return output_file

    async def _send_notification_if_needed(self, snapshot: Dict[str, Any], alerts: List[str]) -> bool:
        if self.notification_manager is None:
            return False

        alert_only = bool(self.notification_config.get("alert_only", True))
        if alert_only and not alerts:
            return False

        message_lines = [
            f"API: {'online' if snapshot['api_available'] else 'offline'}",
            f"Bots: total={snapshot['bot_summary']['total']} active={snapshot['bot_summary']['active']} stopped={snapshot['bot_summary']['stopped']}",
        ]
        if snapshot.get("portfolio_value") is not None:
            message_lines.append(f"Portfolio value: ${snapshot['portfolio_value']:,.2f}")
        if alerts:
            message_lines.append("")
            message_lines.append("Alerts:")
            message_lines.extend(f"- {alert}" for alert in alerts)

        message = NotificationMessage(
            title="Condor Health Check",
            message="\n".join(message_lines),
            level="warning" if alerts else "info",
        )

        telegram_chat_ids = self.notification_config.get("telegram_chat_ids", [])
        telegram_notifier = self.notification_manager.get_notifier("telegram") if self.notification_manager else None
        if telegram_notifier and telegram_chat_ids:
            return await telegram_notifier.send_notification(message, chat_ids=telegram_chat_ids)

        results = await self.notification_manager.send_notification(message)
        return any(results.values()) if results else False

    @staticmethod
    def _offline_snapshot() -> Dict[str, Any]:
        return {
            "api_available": False,
            "health": {"healthy": False, "endpoint": None, "response": None},
            "sources": {"bots_endpoint": None, "balances_endpoint": None, "performance_endpoint": None},
            "balances": [],
            "bots": [],
            "portfolio_value": None,
            "bot_summary": {"total": 0, "active": 0, "stopped": 0, "unknown": 0, "total_pnl": None},
        }

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
