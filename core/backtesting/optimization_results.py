from __future__ import annotations

import json
import logging
import math
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import optuna
import pandas as pd
import yaml
from pydantic import BaseModel, Field

from core.data_paths import data_paths

logger = logging.getLogger(__name__)


class TrialResult(BaseModel):
    trial_id: int
    params: Dict[str, Any]
    objective_value: float
    sharpe_ratio: float
    max_drawdown: float
    total_return: float
    win_rate: float
    profit_factor: float
    total_trades: int
    controller_config: Dict[str, Any]
    backtest_period: Tuple[int, int]
    metrics: Dict[str, Any] = Field(default_factory=dict)
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list)


class OptimizationResult(BaseModel):
    study_name: str
    controller_name: str
    trading_pairs: List[str]
    n_trials: int
    best_trial: TrialResult
    top_k_trials: List[TrialResult]
    created_at: datetime
    quants_lab_version: str
    output_dir: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OptimizationResultsManager:
    def __init__(self, base_dir: Optional[Path] = None):
        outputs_root = Path(os.getenv("QUANTS_LAB_OUTPUTS_DIR", data_paths.base_path / "app" / "outputs"))
        self.base_dir = Path(base_dir) if base_dir else outputs_root / "optimization_results"

    def build_result(
        self,
        study_name: str,
        study: optuna.Study,
        top_k: int = 5,
    ) -> OptimizationResult:
        completed_trials = [
            trial
            for trial in study.trials
            if trial.state == optuna.trial.TrialState.COMPLETE and trial.user_attrs.get("config")
        ]
        if not completed_trials:
            raise ValueError(f"Study {study_name} does not have completed trials with controller configs.")

        maximize = getattr(study.direction, "name", str(study.direction)).lower().endswith("maximize")
        completed_trials.sort(
            key=lambda trial: self._to_float(
                trial.value,
                float("-inf") if maximize else float("inf"),
            ),
            reverse=maximize,
        )
        top_trials = [self._trial_to_result(trial) for trial in completed_trials[:top_k]]
        best_trial = top_trials[0]

        trading_pairs: List[str] = []
        for trial_result in top_trials:
            for trading_pair in self._extract_trading_pairs(trial_result.controller_config):
                if trading_pair not in trading_pairs:
                    trading_pairs.append(trading_pair)

        return OptimizationResult(
            study_name=study_name,
            controller_name=best_trial.controller_config.get("controller_name", "unknown"),
            trading_pairs=trading_pairs,
            n_trials=len(study.trials),
            best_trial=best_trial,
            top_k_trials=top_trials,
            created_at=datetime.now(timezone.utc),
            quants_lab_version=self._get_quants_lab_version(),
        )

    def export_result(
        self,
        study: optuna.Study,
        result: OptimizationResult,
        trials_df: pd.DataFrame,
    ) -> Path:
        output_dir = self._build_output_dir(result.study_name, result.created_at)
        output_dir.mkdir(parents=True, exist_ok=True)

        result.output_dir = str(output_dir)
        self._write_result_json(output_dir / "result.json", result)
        self._write_best_config(output_dir / "best_config.yml", result.best_trial.controller_config)
        self._write_trials_parquet(output_dir / "trials.parquet", trials_df)
        self._write_report_html(output_dir / "report.html", study, result, trials_df)
        return output_dir

    def find_result_dirs(self, study_name: str) -> List[Path]:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", study_name).strip("_") or "study"
        if not self.base_dir.exists():
            return []
        return sorted(
            [path for path in self.base_dir.glob(f"{safe_name}_*") if path.is_dir()],
            reverse=True,
        )

    def load_latest_result(self, study_name: str) -> Optional[OptimizationResult]:
        for result_dir in self.find_result_dirs(study_name):
            result_file = result_dir / "result.json"
            if not result_file.exists():
                continue
            with result_file.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            result = OptimizationResult.model_validate(payload)
            if not result.output_dir:
                result.output_dir = str(result_dir)
            return result
        return None

    def _build_output_dir(self, study_name: str, created_at: datetime) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", study_name).strip("_") or "study"
        timestamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return self.base_dir / f"{safe_name}_{timestamp}"

    def _trial_to_result(self, trial: optuna.trial.FrozenTrial) -> TrialResult:
        user_attrs = trial.user_attrs
        controller_config = self._load_json_dict(user_attrs.get("config"))
        executors_df = self._load_executors_dataframe(user_attrs.get("executors"))
        total_trades = int(user_attrs.get("total_executors") or len(executors_df.index))

        if not executors_df.empty and "net_pnl_quote" in executors_df.columns:
            pnl_series = pd.to_numeric(executors_df["net_pnl_quote"], errors="coerce").fillna(0.0)
            win_rate = float((pnl_series > 0).mean()) if len(pnl_series.index) > 0 else 0.0
        else:
            accuracy_long = self._to_float(user_attrs.get("accuracy_long"), 0.0)
            accuracy_short = self._to_float(user_attrs.get("accuracy_short"), 0.0)
            win_rate = (accuracy_long + accuracy_short) / 2 if (accuracy_long or accuracy_short) else 0.0

        metrics = {
            key: value
            for key, value in user_attrs.items()
            if key not in {"config", "executors", "start_bt", "end_bt"}
        }

        return TrialResult(
            trial_id=trial.number,
            params=dict(trial.params),
            objective_value=self._to_float(trial.value, 0.0),
            sharpe_ratio=self._to_float(user_attrs.get("sharpe_ratio", trial.value), 0.0),
            max_drawdown=self._to_float(
                user_attrs.get("max_drawdown_pct", user_attrs.get("max_drawdown_usd")),
                0.0,
            ),
            total_return=self._to_float(user_attrs.get("net_pnl", trial.value), 0.0),
            win_rate=win_rate,
            profit_factor=self._to_float(user_attrs.get("profit_factor"), 0.0),
            total_trades=total_trades,
            controller_config=controller_config,
            backtest_period=(
                int(self._to_float(user_attrs.get("start_bt"), 0)),
                int(self._to_float(user_attrs.get("end_bt"), 0)),
            ),
            metrics=metrics,
            equity_curve=self._build_equity_curve(executors_df),
        )

    def _write_result_json(self, output_file: Path, result: OptimizationResult) -> None:
        with output_file.open("w", encoding="utf-8") as file:
            json.dump(result.model_dump(mode="json"), file, indent=2)

    def _write_best_config(self, output_file: Path, controller_config: Dict[str, Any]) -> None:
        with output_file.open("w", encoding="utf-8") as file:
            yaml.safe_dump(controller_config, file, sort_keys=False, allow_unicode=False)

    def _write_trials_parquet(self, output_file: Path, trials_df: pd.DataFrame) -> None:
        sanitized_df = trials_df.copy()
        for column in sanitized_df.columns:
            sanitized_df[column] = sanitized_df[column].map(self._sanitize_dataframe_value)
            if sanitized_df[column].dtype == "object":
                sanitized_df[column] = sanitized_df[column].fillna("").map(lambda value: value if isinstance(value, str) else str(value))
        sanitized_df.to_parquet(output_file, index=False)

    def _write_report_html(
        self,
        output_file: Path,
        study: optuna.Study,
        result: OptimizationResult,
        trials_df: pd.DataFrame,
    ) -> None:
        import plotly.io as pio

        figures: List[Tuple[str, Any]] = []
        try:
            figures.append(("Optimization History", optuna.visualization.plot_optimization_history(study)))
        except Exception as exc:
            logger.debug(f"Unable to build optimization history chart: {exc}")

        try:
            figures.append(("Parameter Importances", optuna.visualization.plot_param_importances(study)))
        except Exception as exc:
            logger.debug(f"Unable to build parameter importances chart: {exc}")

        try:
            figures.append(("Slice Plot", optuna.visualization.plot_slice(study)))
        except Exception as exc:
            logger.debug(f"Unable to build slice plot: {exc}")

        summary_rows = [
            {
                "trial_id": trial.trial_id,
                "objective_value": round(trial.objective_value, 6),
                "sharpe_ratio": round(trial.sharpe_ratio, 6),
                "max_drawdown": round(trial.max_drawdown, 6),
                "total_return": round(trial.total_return, 6),
                "win_rate": round(trial.win_rate, 6),
                "profit_factor": round(trial.profit_factor, 6),
                "total_trades": trial.total_trades,
            }
            for trial in result.top_k_trials
        ]
        summary_df = pd.DataFrame(summary_rows)
        if not summary_df.empty:
            for column in summary_df.columns:
                summary_df[column] = summary_df[column].map(self._sanitize_dataframe_value)
            summary_df = summary_df.where(pd.notna(summary_df), "").astype(str)
        summary_table = summary_df.to_html(index=False, classes="summary-table")

        trials_preview_df = trials_df.head(25).copy()
        if not trials_preview_df.empty:
            for column in trials_preview_df.columns:
                trials_preview_df[column] = trials_preview_df[column].map(self._sanitize_dataframe_value)
            trials_preview_df = trials_preview_df.where(pd.notna(trials_preview_df), "").astype(str)
        trials_preview = (
            trials_preview_df.to_html(index=False, classes="trials-table")
            if not trials_preview_df.empty
            else "<p>No trials available.</p>"
        )

        figure_sections: List[str] = []
        include_plotlyjs = "cdn"
        for title, figure in figures:
            figure_sections.append(
                f"<section><h2>{title}</h2>{pio.to_html(figure, full_html=False, include_plotlyjs=include_plotlyjs)}</section>"
            )
            include_plotlyjs = False

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{result.study_name} Optimization Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #111827; }}
    h1, h2 {{ margin-bottom: 0.4rem; }}
    .meta {{ margin-bottom: 24px; color: #4b5563; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0 24px; }}
    th, td {{ border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; }}
    th {{ background: #f3f4f6; }}
    section {{ margin-top: 32px; }}
    code {{ background: #f3f4f6; padding: 2px 4px; }}
  </style>
</head>
<body>
  <h1>{result.study_name}</h1>
  <div class="meta">
    <div>Controller: <strong>{result.controller_name}</strong></div>
    <div>Trading pairs: <strong>{', '.join(result.trading_pairs) if result.trading_pairs else 'N/A'}</strong></div>
    <div>Created at: <strong>{result.created_at.isoformat()}</strong></div>
    <div>Quants-Lab version: <strong>{result.quants_lab_version}</strong></div>
    <div>Output dir: <strong>{result.output_dir or 'N/A'}</strong></div>
  </div>
  <section>
    <h2>Best Trial</h2>
    <p>Objective: <strong>{result.best_trial.objective_value:.6f}</strong> | Sharpe: <strong>{result.best_trial.sharpe_ratio:.6f}</strong> | Return: <strong>{result.best_trial.total_return:.6f}</strong> | Max drawdown: <strong>{result.best_trial.max_drawdown:.6f}</strong></p>
    <p>Backtest period: <code>{result.best_trial.backtest_period[0]}</code> to <code>{result.best_trial.backtest_period[1]}</code></p>
  </section>
  <section>
    <h2>Top Trials</h2>
    {summary_table}
  </section>
  <section>
    <h2>Trials Preview</h2>
    {trials_preview}
  </section>
  {''.join(figure_sections)}
</body>
</html>
"""
        with output_file.open("w", encoding="utf-8") as file:
            file.write(html)

    def _extract_trading_pairs(self, controller_config: Dict[str, Any]) -> List[str]:
        trading_pairs: List[str] = []
        for key in ["trading_pair", "candles_trading_pair", "base_trading_pair", "quote_trading_pair"]:
            value = controller_config.get(key)
            if isinstance(value, str) and value and value not in trading_pairs:
                trading_pairs.append(value)
        return trading_pairs

    def _get_quants_lab_version(self) -> str:
        pyproject_path = data_paths.base_path / "pyproject.toml"
        if pyproject_path.exists():
            try:
                with pyproject_path.open("rb") as file:
                    pyproject = tomllib.load(file)
                return pyproject.get("project", {}).get("version", "unknown")
            except Exception as exc:
                logger.debug(f"Unable to read project version from pyproject.toml: {exc}")
        return "unknown"

    @staticmethod
    def _load_json_dict(raw_value: Any) -> Dict[str, Any]:
        if isinstance(raw_value, dict):
            return raw_value
        if isinstance(raw_value, str) and raw_value:
            try:
                loaded = json.loads(raw_value)
                return loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _load_executors_dataframe(raw_value: Any) -> pd.DataFrame:
        if isinstance(raw_value, str) and raw_value:
            try:
                loaded = json.loads(raw_value)
                return pd.DataFrame(loaded)
            except json.JSONDecodeError:
                return pd.DataFrame()
        if isinstance(raw_value, list):
            return pd.DataFrame(raw_value)
        if isinstance(raw_value, dict):
            return pd.DataFrame(raw_value)
        return pd.DataFrame()

    @classmethod
    def _build_equity_curve(cls, executors_df: pd.DataFrame) -> List[Dict[str, Any]]:
        if executors_df.empty or "net_pnl_quote" not in executors_df.columns:
            return []

        curve_df = executors_df.copy()
        timestamp_column = "close_timestamp" if "close_timestamp" in curve_df.columns else "timestamp"
        if timestamp_column not in curve_df.columns:
            return []

        curve_df["net_pnl_quote"] = pd.to_numeric(curve_df["net_pnl_quote"], errors="coerce").fillna(0.0)
        curve_df[timestamp_column] = pd.to_numeric(curve_df[timestamp_column], errors="coerce")
        curve_df = curve_df.dropna(subset=[timestamp_column]).sort_values(by=timestamp_column).reset_index(drop=True)
        if curve_df.empty:
            return []

        curve_df["equity"] = curve_df["net_pnl_quote"].cumsum()
        records: List[Dict[str, Any]] = []
        for _, row in curve_df.iterrows():
            records.append(
                {
                    "timestamp": int(row[timestamp_column]),
                    "trade_pnl": cls._to_float(row.get("net_pnl_quote"), 0.0),
                    "equity": cls._to_float(row.get("equity"), 0.0),
                    "side": row.get("side"),
                    "status": row.get("status"),
                    "close_type": row.get("close_type"),
                }
            )
        return records

    @staticmethod
    def _sanitize_dataframe_value(value: Any) -> Any:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, default=str)
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return str(value)
        return value

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            numeric_value = float(value)
            if math.isnan(numeric_value):
                return default
            return numeric_value
        except (TypeError, ValueError):
            return default
