import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from core.data_paths import data_paths
from core.data_sources import CLOBDataSource
from core.features.candles.volume import Volume, VolumeConfig
from core.notifiers.base import NotificationMessage
from core.services.mongodb_client import MongoClient
from core.tasks import BaseTask, TaskContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VolumeVolatilityScreenerTask(BaseTask):
    """Ranks markets by volume expansion and current volatility using cached candles."""

    def __init__(self, config):
        super().__init__(config)
        task_config = self.config.config
        self.connector_name = task_config.get("connector_name", "binance_perpetual")
        self.quote_asset = task_config.get("quote_asset", "USDT")
        self.interval = task_config.get("interval", "15m")
        self.volatility_window = int(task_config.get("volatility_window", 20))
        self.volume_short_window = int(task_config.get("volume_short_window", 60))
        self.volume_long_window = int(task_config.get("volume_long_window", 600))
        self.days = int(task_config.get("days", 15))
        self.batch_candles_request = int(task_config.get("batch_candles_request", 10))
        self.sleep_request = float(task_config.get("sleep_request", 2.0))
        self.volume_quantile = float(task_config.get("volume_quantile", 0.5))
        self.natr_quantile = float(task_config.get("natr_quantile", 0.5))
        self.top_x_markets = int(task_config.get("top_x_markets", 20))
        self.notification_config = task_config.get("notification", {})
        self.mongo_config = task_config.get("mongo_config", {})
        self.local_output_dir = Path(
            task_config.get(
                "output_dir",
                data_paths.base_path / "app" / "outputs" / "screeners" / "volume_volatility",
            )
        )
        self.clob = CLOBDataSource()
        self._local_mongodb_client = None

    async def setup(self, context: TaskContext) -> None:
        await super().setup(context)
        if not self.connector_name:
            raise RuntimeError("connector_name not configured")
        if self.mongodb_client is None and self.mongo_config.get("uri"):
            try:
                self._local_mongodb_client = MongoClient(
                    uri=self.mongo_config["uri"],
                    database=self.mongo_config.get("db", "quants_lab"),
                )
                await self._local_mongodb_client.connect()
                self.mongodb_client = self._local_mongodb_client
            except Exception as exc:
                logger.warning(f"Unable to initialize task-level MongoDB client: {exc}")
        logger.info(f"Setup completed for {context.task_name}")
        logger.info(
            f"Connector: {self.connector_name} | Quote asset: {self.quote_asset} | Interval: {self.interval}"
        )

    async def cleanup(self, context: TaskContext, result) -> None:
        if self._local_mongodb_client is not None:
            await self._local_mongodb_client.disconnect()
        await super().cleanup(context, result)

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        started_at = datetime.now(timezone.utc)
        trading_rules = await self.clob.get_trading_rules(self.connector_name)
        trading_pairs = trading_rules.filter_by_quote_asset(self.quote_asset).get_all_trading_pairs()
        self.clob.load_candles_cache(connector_name=self.connector_name, interval=self.interval)

        min_required_rows = max(self.volatility_window + 2, self.volume_long_window + 2)
        requested_pairs = await self._load_required_candles(trading_pairs, min_required_rows)

        snapshots: List[Dict[str, Any]] = []
        skipped_pairs: List[str] = []
        for trading_pair in trading_pairs:
            candles = self.clob.get_candles_from_cache(self.connector_name, trading_pair, self.interval)
            if candles is None or candles.data is None or len(candles.data.index) < min_required_rows:
                skipped_pairs.append(trading_pair)
                continue
            try:
                snapshots.append(self._build_market_snapshot(candles.data.copy(), trading_pair))
            except Exception as exc:
                logger.warning(f"Skipping {trading_pair} due to screener error: {exc}")
                skipped_pairs.append(trading_pair)

        snapshots_df = pd.DataFrame(snapshots)
        if snapshots_df.empty:
            raise RuntimeError("No markets qualified for the volume/volatility screener.")

        volume_threshold = float(snapshots_df["volume_surge"].quantile(self.volume_quantile))
        natr_threshold = float(snapshots_df["current_natr"].quantile(self.natr_quantile))

        snapshots_df["volume_rank"] = snapshots_df["volume_surge"].rank(pct=True)
        snapshots_df["natr_rank"] = snapshots_df["current_natr"].rank(pct=True)
        snapshots_df["pressure_rank"] = snapshots_df["pressure_signal"].abs().rank(pct=True)
        snapshots_df["normalized_score"] = (
            snapshots_df["volume_rank"] * 0.45
            + snapshots_df["natr_rank"] * 0.40
            + snapshots_df["pressure_rank"] * 0.15
        )

        filtered_df = snapshots_df[
            (snapshots_df["volume_surge"] >= volume_threshold)
            & (snapshots_df["current_natr"] >= natr_threshold)
        ].copy()
        filtered_df.sort_values(["normalized_score", "volume_surge"], ascending=False, inplace=True)
        top_results_df = filtered_df.head(self.top_x_markets).reset_index(drop=True)

        payload = {
            "timestamp": started_at.isoformat(),
            "execution_id": context.execution_id,
            "connector_name": self.connector_name,
            "quote_asset": self.quote_asset,
            "interval": self.interval,
            "days": self.days,
            "volume_threshold": volume_threshold,
            "natr_threshold": natr_threshold,
            "markets_evaluated": len(snapshots_df.index),
            "markets_selected": len(top_results_df.index),
            "requested_pairs": requested_pairs,
            "skipped_pairs": skipped_pairs,
            "results": self._sanitize_records(top_results_df.to_dict(orient="records")),
        }

        output_file = await self._store_results(payload)
        await self._send_notification_if_needed(top_results_df)

        duration = datetime.now(timezone.utc) - started_at
        return {
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "execution_id": context.execution_id,
            "connector_name": self.connector_name,
            "interval": self.interval,
            "stats": {
                "markets_evaluated": len(snapshots_df.index),
                "markets_selected": len(top_results_df.index),
                "skipped_pairs": len(skipped_pairs),
                "candles_requested": len(requested_pairs),
            },
            "output_path": str(output_file),
            "duration_seconds": duration.total_seconds(),
        }

    async def on_success(self, context: TaskContext, result) -> None:
        stats = result.result_data.get("stats", {})
        logger.info(f"VolumeVolatilityScreenerTask succeeded in {result.duration_seconds:.2f}s")
        logger.info(
            f"Markets evaluated: {stats.get('markets_evaluated', 0)} | Selected: {stats.get('markets_selected', 0)}"
        )

    async def on_failure(self, context: TaskContext, result) -> None:
        logger.error(f"VolumeVolatilityScreenerTask failed: {result.error_message}")

    async def _load_required_candles(self, trading_pairs: List[str], min_required_rows: int) -> List[str]:
        missing_pairs: List[str] = []
        for trading_pair in trading_pairs:
            candles = self.clob.get_candles_from_cache(self.connector_name, trading_pair, self.interval)
            if candles is None or candles.data is None or len(candles.data.index) < min_required_rows:
                missing_pairs.append(trading_pair)

        if missing_pairs:
            logger.info(
                f"Fetching candles for {len(missing_pairs)} pairs with insufficient cache coverage "
                f"({self.days} days, interval {self.interval})"
            )
            await self.clob.get_candles_batch_last_days(
                connector_name=self.connector_name,
                trading_pairs=missing_pairs,
                interval=self.interval,
                days=self.days,
                batch_size=max(1, self.batch_candles_request),
                sleep_time=self.sleep_request,
            )
        return missing_pairs

    def _build_market_snapshot(self, candles_df: pd.DataFrame, trading_pair: str) -> Dict[str, Any]:
        volume_feature = Volume(
            VolumeConfig(short_term_window=self.volume_short_window, long_term_window=self.volume_long_window)
        ).calculate(candles_df)
        volume_feature["current_natr"] = self._calculate_natr(volume_feature)
        latest = volume_feature.iloc[-1]

        accumulation_score = float(latest.get("accumulation_score", 0.0))
        distribution_score = float(latest.get("distribution_score", 0.0))
        pressure_signal = accumulation_score - distribution_score
        regime = "long" if pressure_signal > 0 else "short"

        return {
            "trading_pair": trading_pair,
            "close": float(latest.get("close", 0.0)),
            "current_natr": float(latest.get("current_natr", 0.0)),
            "volume_surge": float(latest.get("volume_surge", 0.0)),
            "buy_pressure_short_term": float(latest.get("buy_pressure_short_term", 0.0)),
            "buy_pressure_long_term": float(latest.get("buy_pressure_long_term", 0.0)),
            "buy_pressure_divergence": float(latest.get("buy_pressure_divergence", 0.0)),
            "buy_sell_imbalance": float(latest.get("buy_sell_imbalance", 0.0)),
            "accumulation_score": accumulation_score,
            "distribution_score": distribution_score,
            "pressure_signal": pressure_signal,
            "regime": regime,
        }

    def _calculate_natr(self, candles_df: pd.DataFrame) -> pd.Series:
        prev_close = candles_df["close"].shift(1)
        true_range = pd.concat(
            [
                candles_df["high"] - candles_df["low"],
                (candles_df["high"] - prev_close).abs(),
                (candles_df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.rolling(window=self.volatility_window, min_periods=self.volatility_window).mean()
        natr = (atr / candles_df["close"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        return natr

    async def _store_results(self, payload: Dict[str, Any]) -> Path:
        self.local_output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_file = self.local_output_dir / f"{self.connector_name}_{self.interval}_{timestamp}.json"

        with output_file.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, allow_nan=False)

        if self.mongodb_client is not None:
            try:
                await self.mongodb_client.insert_documents(
                    collection_name=self.mongo_config.get("collection", "volume_volatility_screener"),
                    documents=[payload],
                    db_name=self.mongo_config.get("db"),
                )
            except Exception as exc:
                logger.warning(f"MongoDB storage failed for screener results: {exc}")

        return output_file

    async def _send_notification_if_needed(self, top_results_df: pd.DataFrame) -> None:
        if not self.notification_config.get("enabled", False):
            return
        if top_results_df.empty or self.notification_manager is None:
            return

        min_score_threshold = float(self.notification_config.get("min_score_threshold", 0.3))
        filtered_results = top_results_df[top_results_df["normalized_score"] >= min_score_threshold]
        if filtered_results.empty:
            return

        top_results_count = int(self.notification_config.get("top_results_count", 5))
        preview = filtered_results.head(top_results_count)
        lines = []
        for row in preview.to_dict(orient="records"):
            lines.append(
                f"{row['trading_pair']}: score={row['normalized_score']:.3f}, "
                f"volume_surge={row['volume_surge']:.2f}, natr={row['current_natr']:.4f}, regime={row['regime']}"
            )

        message = NotificationMessage(
            title=f"{self.connector_name} Volume/Volatility Screener",
            message="\n".join(lines),
            level="info",
        )
        telegram_chat_ids = self.notification_config.get("telegram_chat_ids", [])
        telegram_notifier = self.notification_manager.get_notifier("telegram") if self.notification_manager else None
        if telegram_notifier and telegram_chat_ids:
            await telegram_notifier.send_notification(message, chat_ids=telegram_chat_ids)
        else:
            await self.notification_manager.send_notification(message)

    def _sanitize_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self._sanitize_value(record) for record in records]

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sanitize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, (np.integer, np.floating)):
            value = value.item()
        if isinstance(value, float) and np.isnan(value):
            return None
        return value
