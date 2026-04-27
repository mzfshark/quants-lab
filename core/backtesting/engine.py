import logging
import os
from typing import Dict, Optional

import pandas as pd

from core.data_structures.backtesting_result import BacktestingResult
from core.data_paths import data_paths
from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase
from hummingbot.strategy_v2.controllers import ControllerConfigBase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BacktestingEngine:
    def __init__(
        self,
        load_cached_data: bool = True,
        custom_backtester: Optional[BacktestingEngineBase] = None,
        root_path: Optional[str] = None,
        controllers_module: Optional[str] = None,
    ):
        self._bt_engine = custom_backtester if custom_backtester is not None else BacktestingEngineBase()
        self.root_path = root_path
        self.controllers_module = controllers_module or os.getenv("QUANTS_LAB_CONTROLLERS_MODULE", "app.controllers")
        if load_cached_data:
            self._load_candles_cache()

    def _load_candles_cache(self):
        candles_path = data_paths.candles_dir
        if not candles_path.exists():
            logger.warning(f"Candles directory {candles_path} does not exist.")
            return
        all_files = os.listdir(candles_path)
        for file in all_files:
            if file == ".gitignore":
                continue
            try:
                connector_name, trading_pair, interval = file.split(".")[0].split("|")
                candles = pd.read_parquet(candles_path / file)
                candles.index = pd.to_datetime(candles.timestamp, unit="s")
                candles.index.name = None
                columns = [
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "quote_asset_volume",
                    "n_trades",
                    "taker_buy_base_volume",
                    "taker_buy_quote_volume",
                ]
                for column in columns:
                    candles[column] = pd.to_numeric(candles[column])
                self._bt_engine.backtesting_data_provider.candles_feeds[
                    f"{connector_name}_{trading_pair}_{interval}"
                ] = candles
                start_time = candles["timestamp"].min()
                end_time = candles["timestamp"].max()
                self._bt_engine.backtesting_data_provider.start_time = start_time
                self._bt_engine.backtesting_data_provider.end_time = end_time
            except Exception as e:
                logger.error(f"Error loading {file}: {e}")

    def load_candles_cache_by_connector_pair(self, connector_name: str, trading_pair: str):
        candles_path = data_paths.candles_dir
        if not candles_path.exists():
            logger.warning(f"Candles directory {candles_path} does not exist.")
            return
        all_files = os.listdir(candles_path)
        for file in all_files:
            if file == ".gitignore":
                continue
            try:
                if connector_name in file and trading_pair in file:
                    connector_name, trading_pair, interval = file.split(".")[0].split("|")
                    candles = pd.read_parquet(candles_path / file)
                    candles.index = pd.to_datetime(candles.timestamp, unit="s")
                    candles.index.name = None
                    columns = [
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "quote_asset_volume",
                        "n_trades",
                        "taker_buy_base_volume",
                        "taker_buy_quote_volume",
                    ]
                    for column in columns:
                        candles[column] = pd.to_numeric(candles[column])
                    self._bt_engine.backtesting_data_provider.candles_feeds[
                        f"{connector_name}_{trading_pair}_{interval}"
                    ] = candles
            except Exception as e:
                logger.error(f"Error loading {file}: {e}")

    def _get_controller_module_candidates(self) -> list[str]:
        candidates = []
        for candidate in [self.controllers_module, "app.controllers", "controllers"]:
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def get_controller_config_instance_from_dict(self, config: Dict):
        last_error = None
        for controllers_module in self._get_controller_module_candidates():
            try:
                return BacktestingEngineBase.get_controller_config_instance_from_dict(
                    config_data=config,
                    controllers_module=controllers_module,
                )
            except ModuleNotFoundError as e:
                last_error = e
                logger.debug(
                    "Controller lookup failed for module %s and controller %s",
                    controllers_module,
                    config.get("controller_name"),
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unable to resolve controller config from provided data.")

    async def run_backtesting(
        self,
        config: ControllerConfigBase,
        start: int,
        end: int,
        backtesting_resolution: str,
        trade_cost: float = 0.0006,
        backtester: Optional[BacktestingEngineBase] = None,
    ) -> BacktestingResult:
        engine = backtester if backtester is not None else self._bt_engine
        bt_result = await engine.run_backtesting(config, start, end, backtesting_resolution, trade_cost)
        return BacktestingResult(bt_result, config)

    async def backtest_controller_from_yml(
        self,
        config_file: str,
        controllers_conf_dir_path: str,
        start: int,
        end: int,
        backtesting_resolution: str = "1m",
        trade_cost: float = 0.0006,
        backtester: Optional[BacktestingEngineBase] = None,
    ):
        config = self._bt_engine.get_controller_config_instance_from_yml(config_file, controllers_conf_dir_path)
        return await self.run_backtesting(
            config,
            start,
            end,
            backtesting_resolution,
            trade_cost,
            backtester=backtester,
        )
