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
        self.hummingbot_server = task_config.get("hummingbot_api_url") or task_config.get("hummingbot_host") or os.getenv(
            "HUMMINGBOT_API_URL",
            "localhost",
        )
        self.hummingbot_port = int(task_config.get("hummingbot_port", os.getenv("HUMMINGBOT_API_PORT", "8000")))
        self.hummingbot_username = task_config.get("hummingbot_username") or os.getenv("HUMMINGBOT_API_USERNAME")
        self.hummingbot_password = task_config.get("hummingbot_password") or os.getenv("HUMMINGBOT_API_PASSWORD")
        self.hummingbot_profile = task_config.get("hummingbot_profile") or os.getenv("HUMMINGBOT_PROFILE", "master_account")
        self.authorized_chat_ids = task_config.get("authorized_chat_ids", [])

    async def setup(self, context: TaskContext) -> None:
        await super().setup(context)
        if not (os.getenv("TELEGRAM_BOT_TOKEN") or self.config.config.get("telegram_bot_token")):
            raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
        if not (self.authorized_chat_ids or os.getenv("TELEGRAM_APPROVAL_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID")):
            raise RuntimeError("Authorized Telegram chat IDs not configured")
        logger.info(f"Setup completed for {context.task_name}")

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        router = TelegramApprovalRouter(
            bot_token=self.config.config.get("telegram_bot_token"),
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
        logger.info(
            f"TelegramApprovalTask succeeded | updates={stats.get('updates_processed', 0)} "
            f"approved={stats.get('approved', 0)} rejected={stats.get('rejected', 0)}"
        )

    async def on_failure(self, context: TaskContext, result) -> None:
        logger.error(f"TelegramApprovalTask failed: {result.error_message}")
