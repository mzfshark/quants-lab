import logging
import os
from typing import Any, Dict

from core.condor.telegram_approval_router import TelegramApprovalRouter
from core.tasks import BaseTask, TaskContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TelegramApprovalTask(BaseTask):
    """Poll Telegram once and resolve approval commands."""

    def __init__(self, config):
        super().__init__(config)
        task_config = self.config.config
        self.hummingbot_server = self._coerce_host(task_config.get("hummingbot_api_url")) or self._coerce_host(
            task_config.get("hummingbot_host")
        ) or self._coerce_host(os.getenv("HUMMINGBOT_API_URL")) or "localhost"
        self.hummingbot_port = int(task_config.get("hummingbot_port", os.getenv("HUMMINGBOT_API_PORT", "8000")))
        self.hummingbot_username = task_config.get("hummingbot_username") or os.getenv("HUMMINGBOT_API_USERNAME")
        self.hummingbot_password = task_config.get("hummingbot_password") or os.getenv("HUMMINGBOT_API_PASSWORD")
        self.hummingbot_profile = task_config.get("hummingbot_profile") or os.getenv("HUMMINGBOT_PROFILE", "master_account")
        self.telegram_bot_token = self._coerce_optional_value(task_config.get("telegram_bot_token")) or self._coerce_optional_value(
            os.getenv("TELEGRAM_BOT_TOKEN")
        )
        self.authorized_chat_ids = self._resolve_chat_ids(
            task_config.get("authorized_chat_ids", []),
            os.getenv("TELEGRAM_APPROVAL_CHAT_IDS"),
            os.getenv("TELEGRAM_CHAT_ID"),
        )
        self.polling_enabled = True

    async def setup(self, context: TaskContext) -> None:
        await super().setup(context)
        if not self.telegram_bot_token:
            self.polling_enabled = False
            logger.warning("Telegram approval polling disabled: TELEGRAM_BOT_TOKEN not configured")
            return
        if not self.authorized_chat_ids:
            self.polling_enabled = False
            logger.warning("Telegram approval polling disabled: authorized chat IDs not configured")
            return
        logger.info(f"Setup completed for {context.task_name}")

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        if not self.polling_enabled:
            return {
                "execution_id": context.execution_id,
                "status": "skipped",
                "updates_processed": 0,
                "approved": 0,
                "rejected": 0,
                "reason": "telegram_not_configured",
            }
        router = TelegramApprovalRouter(
            bot_token=self.telegram_bot_token,
            authorized_chat_ids=self.authorized_chat_ids,
            server=self.hummingbot_server,
            port=self.hummingbot_port,
            username=self.hummingbot_username,
            password=self.hummingbot_password,
            profile_name=self.hummingbot_profile,
        )
        result = await router.poll_once()
        result["execution_id"] = context.execution_id
        result["status"] = "completed"
        return result

    async def on_success(self, context: TaskContext, result) -> None:
        stats = result.result_data or {}
        if stats.get("status") == "skipped":
            logger.info("TelegramApprovalTask skipped because Telegram is not configured")
            return
        logger.info(
            f"TelegramApprovalTask succeeded | updates={stats.get('updates_processed', 0)} "
            f"approved={stats.get('approved', 0)} rejected={stats.get('rejected', 0)}"
        )

    async def on_failure(self, context: TaskContext, result) -> None:
        logger.error(f"TelegramApprovalTask failed: {result.error_message}")

    @staticmethod
    def _coerce_host(value: Any) -> str | None:
        return TelegramApprovalTask._coerce_optional_value(value)

    @staticmethod
    def _coerce_optional_value(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or (text.startswith("${") and text.endswith("}")):
            return None
        return text

    @classmethod
    def _resolve_chat_ids(cls, *sources: Any) -> list[str]:
        resolved: list[str] = []
        for source in sources:
            if source is None:
                continue
            if isinstance(source, list):
                candidates = source
            else:
                normalized = cls._coerce_optional_value(source)
                if not normalized:
                    continue
                candidates = normalized.split(",")
            for candidate in candidates:
                value = cls._coerce_optional_value(candidate)
                if value and value not in resolved:
                    resolved.append(value)
        return resolved
