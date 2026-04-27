import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import yaml

from core.data_sources import CLOBDataSource
from core.data_paths import data_paths
from core.tasks import BaseTask, TaskContext

logging.basicConfig(level=logging.INFO)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)


class CandlesDownloaderTask(BaseTask):
    """Download OHLC candles data from exchanges and store as parquet files."""

    def __init__(self, config):
        super().__init__(config)

        task_config = self.config.config
        self.connector_name = task_config["connector_name"]
        self.days_data_retention = task_config.get("days_data_retention", 7)
        self.intervals = task_config.get("intervals", ["1m"])
        self.quote_asset = task_config.get("quote_asset", "USDT")
        self.min_notional_size = Decimal(str(task_config.get("min_notional_size", 10.0)))
        self.trading_pairs = task_config.get("trading_pairs", [])
        self.pairs_from_optimization_configs = task_config.get("pairs_from_optimization_configs", [])

        self.clob = CLOBDataSource()

    async def setup(self, context: TaskContext) -> None:
        try:
            await super().setup(context)

            if not self.connector_name:
                raise RuntimeError("connector_name not configured")

            logging.info(f"Setup completed for {context.task_name}")
            logging.info(f"Connector: {self.connector_name}")
            logging.info(f"Quote asset: {self.quote_asset}")
            logging.info(f"Intervals: {self.intervals}")
            logging.info(f"Data retention: {self.days_data_retention} days")
            if self.trading_pairs:
                logging.info(f"Explicit trading pairs: {self.trading_pairs}")
            elif self.pairs_from_optimization_configs:
                logging.info(f"Pair source configs: {self.pairs_from_optimization_configs}")
        except Exception as exc:
            logging.error(f"Setup failed: {exc}")
            raise

    async def cleanup(self, context: TaskContext, result) -> None:
        try:
            await super().cleanup(context, result)
            logging.info(f"Cleanup completed for {context.task_name}")
        except Exception as exc:
            logging.warning(f"Cleanup error: {exc}")

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        start_execution = datetime.now(timezone.utc)
        logging.info(f"Starting candles downloader for {self.connector_name}")

        try:
            end_time = datetime.now(timezone.utc)
            start_time = pd.Timestamp(
                time.time() - self.days_data_retention * 24 * 60 * 60,
                unit="s",
            ).tz_localize(timezone.utc).timestamp()

            logging.info(f"Time range: {start_time} to {end_time}")

            trading_rules = await self.clob.get_trading_rules(self.connector_name)
            trading_pairs = self._resolve_trading_pairs(trading_rules.get_all_trading_pairs())
            logging.info(f"Target trading pairs: {len(trading_pairs)}")

            stats = {
                "pairs_processed": 0,
                "pairs_total": len(trading_pairs),
                "intervals_processed": 0,
                "candles_downloaded": 0,
                "errors": 0,
            }

            for i, trading_pair in enumerate(trading_pairs):
                for interval in self.intervals:
                    try:
                        logging.info(f"Fetching candles for {trading_pair} [{i + 1}/{len(trading_pairs)}] {interval}")
                        candles = await self.clob.get_candles(
                            self.connector_name,
                            trading_pair,
                            interval,
                            int(start_time),
                            int(end_time.timestamp()),
                        )

                        if candles.data.empty:
                            logging.info(f"No new candles for {trading_pair} {interval}")
                            continue

                        stats["candles_downloaded"] += len(candles.data)
                        stats["intervals_processed"] += 1
                        await asyncio.sleep(1)
                    except Exception as exc:
                        stats["errors"] += 1
                        logging.exception(f"Error processing {trading_pair} {interval}: {exc}")
                        continue

                stats["pairs_processed"] += 1

            logging.info("Saving candles cache to parquet files...")
            self.clob.dump_candles_cache()

            duration = datetime.now(timezone.utc) - start_execution
            result = {
                "status": "completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "execution_id": context.execution_id,
                "connector": self.connector_name,
                "stats": stats,
                "duration_seconds": duration.total_seconds(),
            }

            logging.info(f"Candles download completed: {stats}")
            return result
        except Exception as exc:
            logging.error(f"Error executing candles downloader: {exc}")
            raise

    async def on_success(self, context: TaskContext, result) -> None:
        stats = result.result_data.get("stats", {})
        logging.info(f"CandlesDownloaderTask succeeded in {result.duration_seconds:.2f}s")
        logging.info(f"  - Pairs: {stats.get('pairs_processed', 0)}/{stats.get('pairs_total', 0)}")
        logging.info(f"  - Intervals: {stats.get('intervals_processed', 0)}")
        logging.info(f"  - Candles: {stats.get('candles_downloaded', 0)}")
        if stats.get("errors", 0) > 0:
            logging.warning(f"  - Errors: {stats.get('errors', 0)}")

    async def on_failure(self, context: TaskContext, result) -> None:
        logging.error(f"CandlesDownloaderTask failed: {result.error_message}")
        logging.error(f"  Execution ID: {context.execution_id}")

    async def on_retry(self, context: TaskContext, attempt: int, error: Exception) -> None:
        logging.warning(f"CandlesDownloaderTask retry attempt {attempt}: {error}")

    def _resolve_trading_pairs(self, available_pairs: List[str]) -> List[str]:
        explicit_pairs = [pair.strip() for pair in self.trading_pairs if str(pair).strip()]
        if explicit_pairs:
            allowed = set(available_pairs)
            return [pair for pair in explicit_pairs if pair in allowed]

        config_pairs = self._load_pairs_from_optimization_configs()
        if config_pairs:
            allowed = set(available_pairs)
            return [pair for pair in config_pairs if pair in allowed]

        quote_suffix = f"-{self.quote_asset}".upper()
        filtered_pairs = [pair for pair in available_pairs if pair.upper().endswith(quote_suffix)]
        return filtered_pairs or available_pairs

    def _load_pairs_from_optimization_configs(self) -> List[str]:
        resolved_pairs: List[str] = []
        seen = set()
        for config_path in self.pairs_from_optimization_configs:
            normalized_path = self._normalize_config_path(config_path)
            if not normalized_path.exists():
                logging.warning(f"Optimization config not found while resolving trading pairs: {normalized_path}")
                continue
            try:
                with normalized_path.open("r", encoding="utf-8") as file:
                    payload = yaml.safe_load(file) or {}
                pairs = payload.get("data", {}).get("trading_pairs", []) or []
                for trading_pair in pairs:
                    pair_value = str(trading_pair).strip()
                    if pair_value and pair_value not in seen:
                        resolved_pairs.append(pair_value)
                        seen.add(pair_value)
            except Exception as exc:
                logging.warning(f"Failed to load trading pairs from {normalized_path}: {exc}")
        return resolved_pairs

    @staticmethod
    def _normalize_config_path(config_path: str) -> Path:
        path = Path(config_path)
        if path.is_absolute():
            return path
        if str(path).startswith("config/"):
            return data_paths.base_path / str(path)
        return data_paths.base_path / "config" / path


async def main():
    from core.tasks.base import ScheduleConfig, TaskConfig

    config = TaskConfig(
        name="candles_downloader_test",
        enabled=True,
        task_class="tasks.data_collection.candles_downloader_task.CandlesDownloaderTask",
        schedule=ScheduleConfig(type="frequency", frequency_hours=1.0),
        config={
            "connector_name": "binance_perpetual",
            "quote_asset": "USDT",
            "intervals": ["15m", "1h"],
            "days_data_retention": 30,
            "min_notional_size": 10,
            "trading_pairs": ["BTC-USDT", "ETH-USDT"],
        },
    )

    task = CandlesDownloaderTask(config)
    result = await task.run()

    print(f"Task completed with status: {result.status}")
    if result.result_data:
        stats = result.result_data.get("stats", {})
        print(f"Downloaded {stats.get('candles_downloaded', 0)} candles")
        print(f"Processed {stats.get('pairs_processed', 0)} pairs")
    if result.error_message:
        print(f"Error: {result.error_message}")


if __name__ == "__main__":
    asyncio.run(main())
