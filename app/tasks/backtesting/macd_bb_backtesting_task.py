import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

import pandas as pd
from dotenv import load_dotenv
from hummingbot.strategy_v2.executors.position_executor.data_types import TrailingStop

from app.controllers.directional_trading.macd_bb_v1 import MACDBBV1ControllerConfig
from core.backtesting.optimizer import BacktestingConfig, BaseStrategyConfigGenerator, StrategyOptimizer
from core.tasks import BaseTask, TaskContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()


class MACDBBConfigGenerator(BaseStrategyConfigGenerator):
    """Strategy configuration generator for MACD and Bollinger Bands optimization."""

    async def generate_config(self, trial) -> BacktestingConfig:
        fast_ma = trial.suggest_int("fast_ma", 9, 59, step=10)
        slow_ma = trial.suggest_int("slow_ma", 21, 201, step=10)
        signal_ma = trial.suggest_int("signal_ma", 10, 60, step=10)
        bb_length = trial.suggest_int("bb_length", 10, 200, step=10)
        bb_std = trial.suggest_float("bb_std", 0.5, 2.5, step=0.5)
        total_amount_quote = 1000
        max_executors_per_side = trial.suggest_int("max_executors_per_side", 1, 3)
        take_profit = trial.suggest_float("take_profit", 0.04, 0.05, step=0.01)
        stop_loss = trial.suggest_float("stop_loss", 0.01, 0.05, step=0.01)
        trailing_stop_activation_price = trial.suggest_float("trailing_stop_activation_price", 0.005, 0.02, step=0.005)
        trailing_delta_ratio = trial.suggest_float("trailing_delta_ratio", 0.1, 0.3, step=0.1)
        trailing_stop_trailing_delta = trailing_stop_activation_price * trailing_delta_ratio
        time_limit = 60 * 60 * 24 * 2
        cooldown_time = 60 * 15

        config = MACDBBV1ControllerConfig(
            id=f"macd_bb_v1_{self.config['trading_pair'].replace('-', '_')}_{trial.number}",
            connector_name=self.config["connector_name"],
            trading_pair=self.config["trading_pair"],
            candles_connector=self.config["connector_name"],
            candles_trading_pair=self.config["trading_pair"],
            macd_fast=fast_ma,
            macd_slow=slow_ma,
            macd_signal=signal_ma,
            bb_length=bb_length,
            bb_std=bb_std,
            total_amount_quote=Decimal(total_amount_quote),
            take_profit=Decimal(take_profit),
            stop_loss=Decimal(stop_loss),
            trailing_stop=TrailingStop(
                activation_price=Decimal(trailing_stop_activation_price),
                trailing_delta=Decimal(trailing_stop_trailing_delta),
            ),
            time_limit=time_limit,
            max_executors_per_side=max_executors_per_side,
            cooldown_time=cooldown_time,
        )

        return BacktestingConfig(config=config, start=self.start, end=self.end)


class MACDBBBacktestingTask(BaseTask):
    """Backtesting task for MACD Bollinger Bands strategy optimization."""

    def __init__(self, config):
        super().__init__(config)

        task_config = self.config.config
        self.resolution = task_config.get("resolution", "1m")
        self.connector_name = task_config.get("connector_name", "binance_perpetual")
        self.selected_pairs = task_config.get(
            "selected_pairs",
            [
                "1000SHIB-USDT",
                "WLD-USDT",
                "ACT-USDT",
                "1000BONK-USDT",
                "DOGE-USDT",
                "AGLD-USDT",
                "SUI-USDT",
                "1000SATS-USDT",
                "MOODENG-USDT",
                "NEIRO-USDT",
                "HBAR-USDT",
                "ENA-USDT",
                "HMSTR-USDT",
                "TROY-USDT",
                "1000PEPE-USDT",
                "1000X-USDT",
                "PNUT-USDT",
                "SOL-USDT",
                "XRP-USDT",
                "SWELL-USDT",
            ],
        )
        self.lookback_days = task_config.get("lookback_days", 7)
        self.end_time_buffer_hours = task_config.get("end_time_buffer_hours", 6)
        self.n_trials = task_config.get("n_trials", 50)
        self.study_name_base = task_config.get("study_name", "macd_bb_v1_task")
        self.optimizer = None

    async def setup(self, context: TaskContext) -> None:
        await super().setup(context)

        try:
            if not self.connector_name:
                raise RuntimeError("connector_name not configured")

            if not self.selected_pairs:
                raise RuntimeError("selected_pairs not configured")

            self.optimizer = StrategyOptimizer(
                resolution=self.resolution,
                load_cached_data=True,
            )

            logging.info(f"Setup completed for {context.task_name}")
            logging.info(f"Connector: {self.connector_name}")
            logging.info(f"Resolution: {self.resolution}")
            logging.info(f"Trading pairs: {len(self.selected_pairs)} pairs")
            logging.info(f"Lookback days: {self.lookback_days}")

        except Exception as e:
            logging.error(f"Setup failed: {e}")
            raise

    async def cleanup(self, context: TaskContext, result) -> None:
        try:
            logging.info(f"Cleanup completed for {context.task_name}")
        except Exception as e:
            logging.warning(f"Cleanup error: {e}")

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        start_execution = datetime.now(timezone.utc)
        logging.info(f"Starting MACD BB backtesting for {len(self.selected_pairs)} pairs")

        try:
            stats = {
                "pairs_processed": 0,
                "pairs_total": len(self.selected_pairs),
                "optimizations_completed": 0,
                "total_trials": 0,
                "errors": 0,
                "study_outputs": [],
            }

            today_str = datetime.now().strftime("%Y-%m-%d")

            for trading_pair in self.selected_pairs:
                try:
                    end_date = time.time() - (self.end_time_buffer_hours * 3600)
                    start_date = end_date - (self.lookback_days * 24 * 3600)

                    logging.info(f"Optimizing strategy for {self.connector_name} {trading_pair}")
                    logging.info(f"Time range: {pd.to_datetime(start_date, unit='s')} to {pd.to_datetime(end_date, unit='s')}")

                    config_generator = MACDBBConfigGenerator(
                        start_date=pd.to_datetime(start_date, unit="s"),
                        end_date=pd.to_datetime(end_date, unit="s"),
                        config={
                            "connector_name": self.connector_name,
                            "trading_pair": trading_pair,
                        },
                    )

                    study_name = f"{self.study_name_base}_{today_str}_{trading_pair.replace('-', '_')}"
                    optimization_result = await self.optimizer.optimize(
                        study_name=study_name,
                        config_generator=config_generator,
                        n_trials=self.n_trials,
                    )

                    stats["optimizations_completed"] += 1
                    stats["total_trials"] += self.n_trials
                    stats["study_outputs"].append(
                        {
                            "trading_pair": trading_pair,
                            "study_name": study_name,
                            "output_dir": optimization_result.output_dir,
                            "study_trials_total": optimization_result.n_trials,
                            "best_sharpe_ratio": optimization_result.best_trial.sharpe_ratio,
                            "best_total_return": optimization_result.best_trial.total_return,
                            "best_max_drawdown": optimization_result.best_trial.max_drawdown,
                            "best_total_trades": optimization_result.best_trial.total_trades,
                        }
                    )
                    logging.info(f"Completed optimization for {trading_pair}")
                    logging.info(f"Artifacts exported to: {optimization_result.output_dir}")

                except Exception as e:
                    stats["errors"] += 1
                    logging.exception(f"Error optimizing {trading_pair}: {e}")
                    continue

                stats["pairs_processed"] += 1

            duration = datetime.now(timezone.utc) - start_execution
            result = {
                "status": "completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "execution_id": context.execution_id,
                "connector": self.connector_name,
                "strategy": "macd_bb_v1",
                "stats": stats,
                "optimization_results": stats["study_outputs"],
                "duration_seconds": duration.total_seconds(),
            }

            logging.info(f"MACD BB backtesting completed: {stats}")
            return result

        except Exception as e:
            logging.error(f"Error executing MACD BB backtesting: {e}")
            raise

    async def on_success(self, context: TaskContext, result) -> None:
        stats = result.result_data.get("stats", {})
        logging.info(f"MACDBBBacktestingTask succeeded in {result.duration_seconds:.2f}s")
        logging.info(f"  - Pairs: {stats.get('pairs_processed', 0)}/{stats.get('pairs_total', 0)}")
        logging.info(f"  - Optimizations: {stats.get('optimizations_completed', 0)}")
        logging.info(f"  - Total trials: {stats.get('total_trials', 0)}")
        logging.info(f"  - Exported studies: {len(stats.get('study_outputs', []))}")
        if stats.get("errors", 0) > 0:
            logging.warning(f"  - Errors: {stats.get('errors', 0)}")

    async def on_failure(self, context: TaskContext, result) -> None:
        logging.error(f"MACDBBBacktestingTask failed: {result.error_message}")
        logging.error(f"  Execution ID: {context.execution_id}")

    async def on_retry(self, context: TaskContext, attempt: int, error: Exception) -> None:
        logging.warning(f"MACDBBBacktestingTask retry attempt {attempt}: {error}")


async def main():
    """Standalone execution for testing."""
    from core.tasks.base import ScheduleConfig, TaskConfig

    config = TaskConfig(
        name="macd_bb_backtesting_test",
        enabled=True,
        task_class="tasks.backtesting.macd_bb_backtesting_task.MACDBBBacktestingTask",
        schedule=ScheduleConfig(
            type="frequency",
            frequency_hours=12.0,
        ),
        config={
            "root_path": os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")),
            "connector_name": "binance_perpetual",
            "total_amount": 100,
            "lookback_days": 7,
            "end_time_buffer_hours": 6,
            "resolution": "1m",
            "n_trials": 10,
            "selected_pairs": [
                "BTC-USDT",
                "ETH-USDT",
                "SOL-USDT",
            ],
        },
    )

    task = MACDBBBacktestingTask(config)
    result = await task.run()

    print(f"Task completed with status: {result.status}")
    if result.result_data:
        stats = result.result_data.get("stats", {})
        print(f"Processed {stats.get('pairs_processed', 0)}/{stats.get('pairs_total', 0)} pairs")
        print(f"Optimizations: {stats.get('optimizations_completed', 0)}")
        print(f"Total trials: {stats.get('total_trials', 0)}")
        print(f"Artifacts exported: {len(stats.get('study_outputs', []))}")
    if result.error_message:
        print(f"Error: {result.error_message}")


if __name__ == "__main__":
    asyncio.run(main())
