import hashlib
import logging
import os
import re
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

    def _get_controller_roots(self) -> list[tuple[str, "Path"]]:
        from pathlib import Path

        roots: list[tuple[str, Path]] = []
        for module_name in self._get_controller_module_candidates():
            root_path = data_paths.base_path / Path(*module_name.split("."))
            if root_path.exists():
                roots.append((module_name, root_path))
        return roots

    def list_available_controllers(self) -> list[Dict[str, str]]:
        controller_name_pattern = re.compile(r'controller_name:\s*str\s*=\s*"([^"]+)"')
        discovered: list[Dict[str, str]] = []
        seen_keys: set[tuple[str, str]] = set()

        for module_name, root_path in self._get_controller_roots():
            for file_path in sorted(root_path.rglob("*.py")):
                if file_path.name == "__init__.py":
                    continue
                relative_path = file_path.relative_to(root_path)
                if len(relative_path.parts) < 2:
                    continue
                controller_type = relative_path.parts[0]
                module_parts = file_path.relative_to(data_paths.base_path).with_suffix("").parts
                module_path = ".".join(module_parts)
                try:
                    content = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = file_path.read_text(encoding="latin-1")
                match = controller_name_pattern.search(content)
                controller_name = match.group(1) if match else file_path.stem
                key = (controller_type, controller_name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                discovered.append(
                    {
                        "controller_type": controller_type,
                        "controller_name": controller_name,
                        "module_path": module_path,
                        "file_path": str(file_path),
                        "controllers_module": module_name,
                    }
                )
        discovered.sort(key=lambda item: (item["controller_type"], item["controller_name"]))
        return discovered

    def resolve_controller_info(self, controller_name: str) -> Optional[Dict[str, str]]:
        matches = [
            controller
            for controller in self.list_available_controllers()
            if controller["controller_name"] == controller_name
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _build_controller_id(config_data: Dict) -> str:
        controller_name = str(config_data.get("controller_name", "controller")).strip() or "controller"
        trading_pair = str(config_data.get("trading_pair", "market")).strip() or "market"
        raw_payload = repr(sorted((key, str(value)) for key, value in config_data.items() if key != "id"))
        digest = hashlib.sha1(raw_payload.encode("utf-8")).hexdigest()[:10]
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", controller_name).strip("_") or "controller"
        safe_pair = re.sub(r"[^A-Za-z0-9._-]+", "_", trading_pair).strip("_") or "market"
        return f"{safe_name}_{safe_pair}_{digest}"

    def get_controller_config_instance_from_dict(self, config: Dict):
        config_data = dict(config)
        controller_name = config_data.get("controller_name")
        if controller_name and not config_data.get("controller_type"):
            controller_info = self.resolve_controller_info(controller_name)
            if controller_info is not None:
                config_data["controller_type"] = controller_info["controller_type"]
        if controller_name and not config_data.get("id"):
            config_data["id"] = self._build_controller_id(config_data)

        last_error = None
        for controllers_module in self._get_controller_module_candidates():
            try:
                return BacktestingEngineBase.get_controller_config_instance_from_dict(
                    config_data=config_data,
                    controllers_module=controllers_module,
                )
            except ModuleNotFoundError as e:
                last_error = e
                logger.debug(
                    "Controller lookup failed for module %s and controller %s",
                    controllers_module,
                    config_data.get("controller_name"),
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
