"""Backward-compatible alias for the consolidated Hummingbot API client."""
from __future__ import annotations

from core.services.hummingbot_api_client import HummingbotAPIClient


class BackendAPIClient(HummingbotAPIClient):
    """Compatibility wrapper kept for legacy deployment imports."""

