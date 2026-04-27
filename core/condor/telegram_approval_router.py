from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

from core.condor.approval_manager import ApprovalManager
from core.condor.models import ApprovalStatus


class TelegramApprovalRouter:
    """Poll Telegram Bot updates and resolve approval commands."""

    def __init__(
        self,
        approval_manager: Optional[ApprovalManager] = None,
        bot_token: Optional[str] = None,
        authorized_chat_ids: Optional[List[str]] = None,
        offset_file: Optional[Path] = None,
        server: str = "localhost",
        port: int = 8000,
        username: Optional[str] = None,
        password: Optional[str] = None,
        profile_name: Optional[str] = None,
    ):
        self.approval_manager = approval_manager or ApprovalManager()
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_ids = authorized_chat_ids or self._load_authorized_chat_ids()
        self.authorized_chat_ids = {str(chat_id) for chat_id in chat_ids if str(chat_id).strip()}
        self.offset_file = offset_file or (self.approval_manager.approvals_dir / "telegram_offset.json")
        self.server = server
        self.port = port
        self.username = username or os.getenv("HUMMINGBOT_API_USERNAME")
        self.password = password or os.getenv("HUMMINGBOT_API_PASSWORD")
        self.profile_name = profile_name or os.getenv("HUMMINGBOT_PROFILE", "master_account")

    async def poll_once(self) -> Dict[str, int]:
        if not self.bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not configured.")
        if not self.authorized_chat_ids:
            raise RuntimeError("TELEGRAM_APPROVAL_CHAT_IDS or TELEGRAM_CHAT_ID must be configured.")
        processed = 0
        approved = 0
        rejected = 0

        offset = self._load_offset()
        updates = await self._get_updates(offset=offset)
        for update in updates:
            processed += 1
            update_id = int(update.get("update_id", 0))
            message = update.get("message") or {}
            text = str(message.get("text") or "").strip()
            chat_id = str((message.get("chat") or {}).get("id") or "")
            if not text or not chat_id:
                self._store_offset(update_id + 1)
                continue

            if self.authorized_chat_ids and chat_id not in self.authorized_chat_ids:
                await self._send_message(chat_id, "Unauthorized chat for approval commands.")
                self._store_offset(update_id + 1)
                continue

            lowered = text.lower()
            if lowered.startswith("/pending"):
                pending = self.approval_manager.list_requests(status=ApprovalStatus.PENDING)
                if not pending:
                    await self._send_message(chat_id, "No pending approvals.")
                else:
                    lines = ["Pending approvals:"]
                    for request in pending[:10]:
                        lines.append(f"- {request.request_id} | {request.study_name} | top_k={request.requested_top_k}")
                    await self._send_message(chat_id, "\n".join(lines))
            elif lowered.startswith("/status"):
                token = self._extract_argument(text)
                request = self.approval_manager.load_request(request_id=token) if token else None
                if request is None:
                    await self._send_message(chat_id, "Usage: /status <request_id>")
                else:
                    await self._send_message(
                        chat_id,
                        f"{request.request_id}\nStudy: {request.study_name}\nStatus: {request.status}\n"
                        f"Validation passed: {'yes' if request.validation_passed else 'no'}",
                    )
            elif lowered.startswith("/approve"):
                token = self._extract_argument(text)
                if not token:
                    await self._send_message(chat_id, "Usage: /approve <request_id>")
                else:
                    request = await self.approval_manager.approve_request(
                        approver=f"telegram:{chat_id}",
                        request_id=token,
                        live=True,
                        server=self.server,
                        port=self.port,
                        username=self.username,
                        password=self.password,
                        profile_name=self.profile_name,
                    )
                    approved += 1
                    await self._send_message(chat_id, f"Approved and deployed: {request.request_id} ({request.status})")
            elif lowered.startswith("/reject"):
                token, reason = self._extract_argument_and_reason(text)
                if not token:
                    await self._send_message(chat_id, "Usage: /reject <request_id> <reason>")
                else:
                    request = self.approval_manager.reject_request(
                        approver=f"telegram:{chat_id}",
                        request_id=token,
                        reason=reason or "Rejected from Telegram",
                    )
                    rejected += 1
                    await self._send_message(chat_id, f"Rejected: {request.request_id}")
            else:
                await self._send_message(
                    chat_id,
                    "Commands:\n/pending\n/status <request_id>\n/approve <request_id>\n/reject <request_id> <reason>",
                )

            self._store_offset(update_id + 1)

        return {"updates_processed": processed, "approved": approved, "rejected": rejected}

    async def _get_updates(self, offset: Optional[int] = None) -> List[Dict]:
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        payload: Dict[str, object] = {"timeout": 1, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=payload) as response:
                response.raise_for_status()
                body = await response.json()
        return body.get("result", []) if isinstance(body, dict) else []

    async def _send_message(self, chat_id: str, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                response.raise_for_status()

    def _load_offset(self) -> Optional[int]:
        if not self.offset_file.exists():
            return None
        with self.offset_file.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        offset = payload.get("offset")
        return int(offset) if offset is not None else None

    def _store_offset(self, offset: int) -> None:
        self.offset_file.parent.mkdir(parents=True, exist_ok=True)
        with self.offset_file.open("w", encoding="utf-8") as file:
            json.dump({"offset": int(offset)}, file)

    @staticmethod
    def _extract_argument(text: str) -> Optional[str]:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return None
        return parts[1].strip()

    @staticmethod
    def _extract_argument_and_reason(text: str) -> tuple[Optional[str], Optional[str]]:
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            return None, None
        reason = parts[2].strip() if len(parts) > 2 else None
        return parts[1].strip(), reason

    @staticmethod
    def _load_authorized_chat_ids() -> List[str]:
        raw_value = os.getenv("TELEGRAM_APPROVAL_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
        return [item.strip() for item in raw_value.split(",") if item.strip()]
