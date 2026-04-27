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


class HummingbotStatusTask(BaseTask):
    """Collects portfolio and bot status snapshots from the Hummingbot API."""

    def __init__(self, config):
        super().__init__(config)
        task_config = self.config.config
        self.hummingbot_host = task_config.get("hummingbot_host", task_config.get("backend_api_server", "localhost"))
        self.hummingbot_port = int(task_config.get("hummingbot_port", 8000))
        self.hummingbot_username = task_config.get("hummingbot_username")
        self.hummingbot_password = task_config.get("hummingbot_password")
        self.notification_config = task_config.get("notification", {})
        self.alert_config = task_config.get("alert_config", {})
        self.report_type = self.notification_config.get("report_type", "summary").lower()
        self.output_dir = Path(
            task_config.get(
                "output_dir",
                data_paths.base_path / "app" / "outputs" / "reports" / "hummingbot_status",
            )
        )
        self.api_client: Optional[HummingbotAPIClient] = None

    async def setup(self, context: TaskContext) -> None:
        await super().setup(context)
        if not self.hummingbot_host:
            raise RuntimeError("hummingbot_host not configured")
        if self.report_type not in {"detailed", "summary", "weekly", "alert"}:
            logger.warning(f"Unknown report_type '{self.report_type}', defaulting to summary")
            self.report_type = "summary"
        self.api_client = HummingbotAPIClient(
            server=self.hummingbot_host,
            port=self.hummingbot_port,
            username=self.hummingbot_username,
            password=self.hummingbot_password,
        )
        logger.info(f"Setup completed for {context.task_name}")
        logger.info(f"Hummingbot API target: {self.hummingbot_host}:{self.hummingbot_port}")

    async def cleanup(self, context: TaskContext, result) -> None:
        await super().cleanup(context, result)
        if self.api_client is not None:
            await self.api_client.close()

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        started_at = datetime.now(timezone.utc)
        snapshot = await self._collect_snapshot()
        alerts = self._build_alerts(snapshot)
        report_message = self._build_report_message(snapshot, alerts)
        output_file = await self._store_report(context, started_at, snapshot, alerts, report_message)
        notification_sent = await self._send_notification_if_needed(report_message, alerts)

        duration = datetime.now(timezone.utc) - started_at
        return {
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "execution_id": context.execution_id,
            "report_type": self.report_type,
            "api_available": snapshot["api_available"],
            "alerts": alerts,
            "notification_sent": notification_sent,
            "output_path": str(output_file),
            "stats": {
                "bots_total": snapshot["bot_summary"]["total"],
                "bots_active": snapshot["bot_summary"]["active"],
                "bots_stopped": snapshot["bot_summary"]["stopped"],
                "balances_total": len(snapshot["balances"]),
                "portfolio_value": snapshot["portfolio_value"],
                "alerts_count": len(alerts),
            },
            "duration_seconds": duration.total_seconds(),
        }

    async def on_success(self, context: TaskContext, result) -> None:
        stats = result.result_data.get("stats", {})
        logger.info(f"HummingbotStatusTask succeeded in {result.duration_seconds:.2f}s")
        logger.info(
            f"Bots: {stats.get('bots_active', 0)}/{stats.get('bots_total', 0)} active | Alerts: {stats.get('alerts_count', 0)}"
        )

    async def on_failure(self, context: TaskContext, result) -> None:
        logger.error(f"HummingbotStatusTask failed: {result.error_message}")

    async def _collect_snapshot(self) -> Dict[str, Any]:
        if self.api_client is None:
            return {
                "api_available": False,
                "health": {"healthy": False, "endpoint": None, "response": None},
                "sources": {
                    "bots_endpoint": None,
                    "balances_endpoint": None,
                    "performance_endpoint": None,
                },
                "balances": [],
                "bots": [],
                "portfolio_value": None,
                "bot_summary": {"total": 0, "active": 0, "stopped": 0, "unknown": 0, "total_pnl": None},
            }
        return await self.api_client.get_runtime_snapshot()

    def _build_alerts(self, snapshot: Dict[str, Any]) -> List[str]:
        alerts: List[str] = []
        if self.alert_config.get("check_api_connection", False) and not snapshot["api_available"]:
            alerts.append("Hummingbot API unavailable.")

        min_portfolio_value = self._coerce_float(self.alert_config.get("min_portfolio_value"))
        portfolio_value = snapshot.get("portfolio_value")
        if min_portfolio_value is not None and portfolio_value is not None and portfolio_value < min_portfolio_value:
            alerts.append(
                f"Portfolio value {self._format_currency(portfolio_value)} is below threshold {self._format_currency(min_portfolio_value)}."
            )

        max_negative_pnl = self._coerce_float(self.alert_config.get("max_negative_pnl"))
        if max_negative_pnl is not None:
            breached_bots = [
                bot for bot in snapshot["bots"] if bot.get("pnl") is not None and float(bot["pnl"]) <= max_negative_pnl
            ]
            for bot in breached_bots[:5]:
                alerts.append(
                    f"Bot {bot['bot_name']} PnL {self._format_currency(float(bot['pnl']))} is below limit {self._format_currency(max_negative_pnl)}."
                )

        if self.alert_config.get("check_stopped_bots", False):
            stopped_statuses = {"stopped", "offline", "error", "failed", "inactive"}
            stopped_bots = [
                bot["bot_name"] for bot in snapshot["bots"] if str(bot.get("status") or "").lower() in stopped_statuses
            ]
            if stopped_bots:
                alerts.append(f"Stopped bots detected: {', '.join(stopped_bots[:5])}")

        return alerts

    def _build_report_message(self, snapshot: Dict[str, Any], alerts: List[str]) -> str:
        bot_summary = snapshot["bot_summary"]
        lines = [
            f"API: {'online' if snapshot['api_available'] else 'offline'}",
            f"Portfolio value: {self._format_currency(snapshot['portfolio_value']) if snapshot['portfolio_value'] is not None else 'unavailable'}",
            f"Bots: total={bot_summary['total']} active={bot_summary['active']} stopped={bot_summary['stopped']}",
        ]
        if bot_summary.get("total_pnl") is not None:
            lines.append(f"Total bot PnL: {self._format_currency(bot_summary['total_pnl'])}")

        if self.report_type in {"summary", "weekly", "detailed"}:
            top_balances = snapshot["balances"][: 5 if self.report_type == "summary" else 10]
            if top_balances:
                lines.append("")
                lines.append("Top balances:")
                for balance in top_balances:
                    total = balance.get("total")
                    usd_value = balance.get("usd_value")
                    details = f"total={total}" if total is not None else "total=unavailable"
                    if usd_value is not None:
                        details += f", usd={self._format_currency(usd_value)}"
                    lines.append(f"- {balance['asset']}: {details}")

        if self.report_type in {"weekly", "detailed"} and snapshot["bots"]:
            lines.append("")
            lines.append("Bot snapshot:")
            for bot in snapshot["bots"][:10]:
                bot_line = f"- {bot['bot_name']} [{bot.get('status', 'unknown')}]"
                if bot.get("trading_pair"):
                    bot_line += f" pair={bot['trading_pair']}"
                if bot.get("pnl") is not None:
                    bot_line += f" pnl={self._format_currency(float(bot['pnl']))}"
                lines.append(bot_line)

        if self.report_type == "alert":
            lines = ["Alerts:"]
            if alerts:
                lines.extend(f"- {alert}" for alert in alerts)
            else:
                lines.append("- No alerts detected.")
        elif alerts:
            lines.append("")
            lines.append("Alerts:")
            lines.extend(f"- {alert}" for alert in alerts)

        return "\n".join(lines)

    async def _store_report(
        self,
        context: TaskContext,
        started_at: datetime,
        snapshot: Dict[str, Any],
        alerts: List[str],
        report_message: str,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        output_file = self.output_dir / f"{self.report_type}_{timestamp}.json"
        payload = {
            "timestamp": started_at.isoformat(),
            "execution_id": context.execution_id,
            "report_type": self.report_type,
            "api_available": snapshot["api_available"],
            "health": snapshot["health"],
            "sources": snapshot["sources"],
            "portfolio_value": snapshot["portfolio_value"],
            "bot_summary": snapshot["bot_summary"],
            "balances": snapshot["balances"],
            "bots": snapshot["bots"],
            "alerts": alerts,
            "report_message": report_message,
        }
        with output_file.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, default=str)
        return output_file

    async def _send_notification_if_needed(self, report_message: str, alerts: List[str]) -> bool:
        if self.notification_manager is None:
            return False
        alert_only = bool(self.notification_config.get("alert_only", False))
        if alert_only and not alerts:
            return False

        message = NotificationMessage(
            title=f"Hummingbot {self.report_type.capitalize()} Report",
            message=report_message,
            level="warning" if alerts else "info",
        )
        telegram_chat_ids = self.notification_config.get("telegram_chat_ids", [])
        telegram_notifier = self.notification_manager.get_notifier("telegram") if self.notification_manager else None
        if telegram_notifier and telegram_chat_ids:
            return await telegram_notifier.send_notification(message, chat_ids=telegram_chat_ids)

        results = await self.notification_manager.send_notification(message)
        return any(results.values()) if results else False

    def _coerce_float(self, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _format_currency(self, value: float) -> str:
        return f"${value:,.2f}"
