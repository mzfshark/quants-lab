import datetime
import inspect
import json
import logging
import math
import subprocess
import traceback
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

import optuna
from dotenv import load_dotenv
from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase
from hummingbot.strategy_v2.controllers import ControllerConfigBase
from pydantic import BaseModel

from core.backtesting.engine import BacktestingEngine
from core.backtesting.optimization_results import OptimizationResult, OptimizationResultsManager
from core.data_paths import data_paths
from core.data_sources import CLOBDataSource

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BacktestingConfig(BaseModel):
    """A simple data structure to hold the backtesting configuration."""

    config: ControllerConfigBase
    start: int
    end: int


class BaseStrategyConfigGenerator(ABC):
    """Base class for generating strategy configurations for optimization."""

    def __init__(self, start_date: datetime.datetime, end_date: datetime.datetime, config: Optional[Dict] = None):
        self.start = int(start_date.timestamp())
        self.end = int(end_date.timestamp())
        self.config = config or {}

    def update_config(self, config):
        self.config.update(config)

    @abstractmethod
    async def generate_config(self, trial) -> BacktestingConfig:
        pass

    async def generate_custom_configs(self) -> List[BacktestingConfig]:
        pass


class StrategyOptimizer:
    """Class for optimizing trading strategies using Optuna and a backtesting engine."""

    def __init__(
        self,
        storage_name: Optional[str] = None,
        load_cached_data: bool = False,
        resolution: str = "1m",
        clob_source: Optional[CLOBDataSource] = None,
        custom_backtester: Optional[BacktestingEngineBase] = None,
    ):
        self._storage_name = storage_name if storage_name else self.get_storage_name(engine="sqlite")
        self.dashboard_process = None
        self.resolution = resolution
        self._backtesting_engine = None
        self._clob_source = None
        self._load_cached_data = load_cached_data
        self._custom_backtester = custom_backtester
        self._clob_source_param = clob_source
        self.results_manager = OptimizationResultsManager()

    @classmethod
    def get_storage_name(cls, engine, **kwargs):
        if engine == "sqlite":
            database_name = kwargs.get("database_name", "optimization_database")
            path = data_paths.get_backtesting_db_path(f"{database_name}.db")
            return f"sqlite:///{path}"
        if engine == "postgres":
            db_host = kwargs.get("db_host", "localhost")
            db_port = kwargs.get("db_port", 5432)
            db_user = kwargs.get("db_user", "admin")
            db_pass = kwargs.get("db_pass", "admin")
            database_name = kwargs.get("database_name", "optimization_database")
            return f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{database_name}"
        raise ValueError(f"Unsupported storage engine: {engine}")

    @property
    def backtesting_engine(self):
        if self._backtesting_engine is None:
            self._backtesting_engine = BacktestingEngine(
                load_cached_data=self._load_cached_data,
                custom_backtester=self._custom_backtester,
            )
        return self._backtesting_engine

    @property
    def clob_source(self):
        if self._clob_source is None:
            self._clob_source = self._clob_source_param or CLOBDataSource()
        return self._clob_source

    def load_candles_cache_by_connector_pair(self, connector_name: str, trading_pair: str):
        self.backtesting_engine.load_candles_cache_by_connector_pair(connector_name, trading_pair)

    def get_all_study_names(self):
        return optuna.get_all_study_names(self._storage_name)

    def get_study(self, study_name: str):
        return optuna.load_study(study_name=study_name, storage=self._storage_name)

    def get_study_trials_df(self, study_name: str):
        study = self.get_study(study_name)
        df = study.trials_dataframe()
        df.dropna(how="all", inplace=True)
        df.rename(
            columns={col: col.replace("user_attrs_", "") for col in df.columns if col.startswith("user_attrs_")},
            inplace=True,
        )
        df.rename(
            columns={col: col.replace("params_", "") for col in df.columns if col.startswith("params_")},
            inplace=True,
        )
        return df

    def get_study_best_params(self, study_name: str):
        study = self.get_study(study_name)
        return study.best_params

    def _create_study(self, study_name: str, direction: str = "maximize", load_if_exists: bool = True) -> optuna.Study:
        logger.info("About to create a study...")
        return optuna.create_study(
            direction=direction,
            study_name=study_name,
            storage=self._storage_name,
            sampler=optuna.samplers.TPESampler(),
            load_if_exists=load_if_exists,
        )

    async def optimize(
        self,
        study_name: str,
        config_generator: Type[BaseStrategyConfigGenerator],
        n_trials: int = 100,
        load_if_exists: bool = True,
        export_top_k: int = 5,
        direction: str = "maximize",
    ) -> OptimizationResult:
        study = self._create_study(study_name, direction=direction, load_if_exists=load_if_exists)
        logger.info("About to start optimizing...")
        await self._optimize_async(study, config_generator, n_trials=n_trials)
        return self.export_optimization_result(study_name, top_k=export_top_k)

    async def optimize_custom_configs(
        self,
        study_name: str,
        config_generator: Type[BaseStrategyConfigGenerator],
        load_if_exists: bool = True,
        export_top_k: int = 5,
        direction: str = "maximize",
    ) -> OptimizationResult:
        study = self._create_study(study_name, direction=direction, load_if_exists=load_if_exists)
        await self._optimize_async_custom_configs(study, config_generator)
        return self.export_optimization_result(study_name, top_k=export_top_k)

    def build_optimization_result(self, study_name: str, top_k: int = 5) -> OptimizationResult:
        study = self.get_study(study_name)
        return self.results_manager.build_result(study_name=study_name, study=study, top_k=top_k)

    def export_optimization_result(self, study_name: str, top_k: int = 5) -> OptimizationResult:
        study = self.get_study(study_name)
        result = self.results_manager.build_result(study_name=study_name, study=study, top_k=top_k)
        trials_df = self.get_study_trials_df(study_name)
        self.results_manager.export_result(study=study, result=result, trials_df=trials_df)
        return result

    async def _optimize_async(self, study: optuna.Study, config_generator: Type[BaseStrategyConfigGenerator], n_trials: int):
        for _ in range(n_trials):
            trial = study.ask()
            try:
                value = await self._async_objective(trial, config_generator)
                study.tell(trial, value)
            except Exception as e:
                logger.error(f"Error in _optimize_async: {str(e)}")
                study.tell(trial, state=optuna.trial.TrialState.FAIL)

    async def _optimize_async_custom_configs(self, study: optuna.Study, config_generator: Type[BaseStrategyConfigGenerator]):
        backtesting_configs = config_generator.generate_custom_configs()
        if inspect.isawaitable(backtesting_configs):
            backtesting_configs = await backtesting_configs

        for bt_config in backtesting_configs:
            trial = study.ask()
            try:
                connector_name = bt_config.config.connector_name
                trading_pair = getattr(bt_config.config, "trading_pair", None) or getattr(bt_config.config, "candles_trading_pair", None)
                start = bt_config.start
                end = bt_config.end

                if trading_pair:
                    candles = await self.clob_source.get_candles(
                        connector_name,
                        trading_pair,
                        self.resolution,
                        start,
                        end,
                    )
                    self.backtesting_engine._bt_engine.backtesting_data_provider.candles_feeds[
                        f"{connector_name}_{trading_pair}_{self.resolution}"
                    ] = candles.data
                    start = int(candles.data["timestamp"].min())
                    end = int(candles.data["timestamp"].max())

                backtesting_result = await self.backtesting_engine.run_backtesting(
                    config=bt_config.config,
                    start=start,
                    end=end,
                    backtesting_resolution=self.resolution,
                )
                self._store_trial_artifacts(trial, backtesting_result, start=start, end=end)
                value = await self._score_backtesting_result(backtesting_result, config_generator)
            except Exception as e:
                logger.error(f"An error occurred during optimization: {str(e)}")
                traceback.print_exc()
                value = float("-inf")
            study.tell(trial, value)

    async def _async_objective(self, trial: optuna.Trial, config_generator: Type[BaseStrategyConfigGenerator]) -> float:
        try:
            backtesting_config = await config_generator.generate_config(trial)
            backtesting_result = await self.backtesting_engine.run_backtesting(
                config=backtesting_config.config,
                start=backtesting_config.start,
                end=backtesting_config.end,
                backtesting_resolution=self.resolution,
            )
            self._store_trial_artifacts(
                trial,
                backtesting_result,
                start=backtesting_config.start,
                end=backtesting_config.end,
            )
            return await self._score_backtesting_result(backtesting_result, config_generator)
        except Exception as e:
            logger.error(f"An error occurred during optimization: {str(e)}")
            traceback.print_exc()
            return float("-inf")

    def _store_trial_artifacts(self, trial: optuna.Trial, backtesting_result, start: int, end: int) -> None:
        strategy_analysis = backtesting_result.results
        for key, value in strategy_analysis.items():
            trial.set_user_attr(key, self._normalize_user_attr_value(value))

        controller_config = self._serialize_controller_config(backtesting_result.controller_config)
        validated_config = self._validate_controller_config_serialization(controller_config)
        trial.set_user_attr("config", json.dumps(validated_config, sort_keys=True))
        trial.set_user_attr("start_bt", int(start))
        trial.set_user_attr("end_bt", int(end))

        executors_df = backtesting_result.executors_df.copy()
        if not executors_df.empty:
            if "close_type" in executors_df.columns:
                executors_df["close_type"] = executors_df["close_type"].apply(lambda x: x.name if hasattr(x, "name") else x)
            if "status" in executors_df.columns:
                executors_df["status"] = executors_df["status"].apply(lambda x: x.name if hasattr(x, "name") else x)
            if "config" in executors_df.columns:
                executors_df = executors_df.drop(columns=["config"])
        trial.set_user_attr("executors", executors_df.to_json())

    def _serialize_controller_config(self, controller_config: ControllerConfigBase) -> Dict[str, Any]:
        if hasattr(controller_config, "model_dump_json"):
            return json.loads(controller_config.model_dump_json())
        if hasattr(controller_config, "json"):
            return json.loads(controller_config.json())
        if hasattr(controller_config, "dict"):
            return controller_config.dict()
        raise TypeError("Unsupported controller config object.")

    def _validate_controller_config_serialization(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        validated_config = self.backtesting_engine.get_controller_config_instance_from_dict(config_data)
        serialized_config = self._serialize_controller_config(validated_config)
        if serialized_config != config_data:
            raise ValueError("Controller config serialization is not stable after validation.")
        return serialized_config

    def _normalize_user_attr_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._normalize_user_attr_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._normalize_user_attr_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._normalize_user_attr_value(item) for item in value]
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return str(value)
        if hasattr(value, "name") and not isinstance(value, str):
            return value.name
        return value

    async def _score_backtesting_result(self, backtesting_result, config_generator: Type[BaseStrategyConfigGenerator]) -> float:
        score_method = getattr(config_generator, "score_backtesting_result", None)
        if callable(score_method):
            score = score_method(backtesting_result)
            if inspect.isawaitable(score):
                score = await score
            score = self._normalize_user_attr_value(score)
            try:
                numeric_score = float(score)
            except (TypeError, ValueError):
                return float("-inf")
            return numeric_score if not math.isnan(numeric_score) else float("-inf")

        strategy_analysis = backtesting_result.results
        return self._normalize_user_attr_value(strategy_analysis.get("sharpe_ratio", float("-inf")))

    def launch_optuna_dashboard(self):
        self.dashboard_process = subprocess.Popen(["optuna-dashboard", self._storage_name])

    def kill_optuna_dashboard(self):
        if self.dashboard_process and self.dashboard_process.poll() is None:
            self.dashboard_process.terminate()
            self.dashboard_process.wait()
            self.dashboard_process = None
        else:
            logger.info("Dashboard is not running or already terminated.")
