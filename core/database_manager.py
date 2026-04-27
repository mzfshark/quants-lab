"""
Simple database manager for QuantsLab tasks.
Reads database configuration from environment variables and provides shared database client instances.
"""
import logging
import os
from typing import Optional

from core.services.mongodb_client import MongoClient

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages shared database connections for tasks."""

    def __init__(self):
        self._mongodb_client: Optional[MongoClient] = None

    @staticmethod
    def get_storage_backend() -> str:
        configured_backend = os.getenv("QUANTS_LAB_STORAGE", "").strip().lower()
        if configured_backend:
            return configured_backend
        return "sqlite"

    async def get_mongodb_client(self, force: bool = False) -> Optional[MongoClient]:
        """Get MongoDB client instance when MongoDB storage is enabled."""
        if not force and self.get_storage_backend() != "mongodb":
            logger.debug("MongoDB client skipped because QUANTS_LAB_STORAGE is not set to 'mongodb'")
            return None

        if self._mongodb_client is None:
            mongo_uri = os.getenv("MONGO_URI")
            mongo_database = os.getenv("MONGO_DATABASE", "quants_lab")

            if not mongo_uri:
                logger.warning("MONGO_URI environment variable not set")
                return None

            try:
                self._mongodb_client = MongoClient(
                    uri=mongo_uri,
                    database=mongo_database,
                )
                await self._mongodb_client.connect()
                logger.info(f"MongoDB client initialized successfully (database: {mongo_database})")
            except Exception as e:
                logger.error(f"Failed to initialize MongoDB client: {e}")
                return None

        return self._mongodb_client

    async def cleanup(self):
        """Cleanup database connections."""
        if self._mongodb_client:
            await self._mongodb_client.disconnect()
            self._mongodb_client = None


db_manager = DatabaseManager()
