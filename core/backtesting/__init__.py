from __future__ import annotations

from importlib import import_module

__all__ = [
    "BacktestingEngine",
    "OptimizationResult",
    "OptimizationResultsManager",
    "TrialResult",
    "ExportConfig",
    "OptimizationConfig",
    "RiskFilterConfig",
    "UniversalOptimizationRunner",
    "UniversalStrategyConfigGenerator",
    "ValidationConfig",
    "ValidationSummary",
]


def __getattr__(name: str):
    if name == "BacktestingEngine":
        return import_module("core.backtesting.engine").BacktestingEngine
    if name in {"OptimizationResult", "OptimizationResultsManager", "TrialResult"}:
        module = import_module("core.backtesting.optimization_results")
        return getattr(module, name)
    if name in {
        "ExportConfig",
        "OptimizationConfig",
        "RiskFilterConfig",
        "UniversalOptimizationRunner",
        "UniversalStrategyConfigGenerator",
        "ValidationConfig",
        "ValidationSummary",
    }:
        module = import_module("core.backtesting.universal_strategy")
        return getattr(module, name)
    raise AttributeError(f"module 'core.backtesting' has no attribute {name!r}")
