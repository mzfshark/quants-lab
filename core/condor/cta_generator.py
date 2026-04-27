from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import yaml

from core.backtesting.optimization_results import OptimizationResult, TrialResult
from core.condor.models import RiskLimits


class CTAGenerator:
    """Generate Condor-compatible agent bundles from optimization results."""

    def generate_agent_md(
        self,
        result: OptimizationResult,
        trial: Optional[TrialResult] = None,
        risk_limits: Optional[RiskLimits] = None,
        tick_interval_seconds: int = 60,
        connector: Optional[str] = None,
        controller_config_file: str = "controller.yml",
        agent_name: Optional[str] = None,
    ) -> str:
        trial = trial or result.best_trial
        risk_limits = risk_limits or RiskLimits()
        controller_config = trial.controller_config
        agent_name = agent_name or self.build_agent_name(result, trial)
        connector_name = connector or controller_config.get("connector_name") or "binance_perpetual"
        trading_pair = self._extract_primary_pair(controller_config)

        frontmatter = {
            "name": agent_name,
            "description": f"CTA generated from Quants-Lab study {result.study_name}",
            "tick_interval": tick_interval_seconds,
            "connector": connector_name,
            "controller_name": result.controller_name,
            "controller_config_file": controller_config_file,
            "study_name": result.study_name,
            "trial_id": trial.trial_id,
            "sharpe_ratio": round(trial.sharpe_ratio, 6),
            "max_drawdown": round(trial.max_drawdown, 6),
            "total_return": round(trial.total_return, 6),
            "profit_factor": round(trial.profit_factor, 6),
            "win_rate": round(trial.win_rate, 6),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if trading_pair:
            frontmatter["trading_pair"] = trading_pair
        if result.metadata:
            frontmatter["optimization_metadata"] = result.metadata

        risk_frontmatter = risk_limits.model_dump(exclude_none=True)
        if risk_frontmatter:
            frontmatter["risk_limits"] = risk_frontmatter

        sections = [
            f"# {agent_name}",
            "",
            "## Strategy",
            f"- Controller: `{result.controller_name}`",
            f"- Study: `{result.study_name}`",
            f"- Trial: `{trial.trial_id}`",
            f"- Connector: `{connector_name}`",
            f"- Trading pair: `{trading_pair or 'multi-pair'}`",
            "",
            "## Analysis Steps",
            f"1. Load `{controller_config_file}` and validate it against the Hummingbot controller schema.",
            "2. Monitor portfolio health, active executors, and live bot status through the Hummingbot API.",
            "3. Compare live behavior with the optimization metrics before allowing changes in deployment state.",
            "",
            "## Decision Logic",
            f"- Prefer the optimized config from trial `{trial.trial_id}` unless runtime checks or risk rules fail.",
            f"- Baseline Sharpe ratio: `{trial.sharpe_ratio:.6f}`",
            f"- Baseline total return: `{trial.total_return:.6f}`",
            f"- Baseline profit factor: `{trial.profit_factor:.6f}`",
            "",
            "## Risk Rules",
        ]

        risk_dump = risk_limits.model_dump(exclude_none=True)
        if risk_dump:
            for key, value in risk_dump.items():
                sections.append(f"- {key}: `{value}`")
        else:
            sections.append("- Use the controller's built-in risk settings and deployment defaults.")

        sections.extend(
            [
                "",
                "## Executor Config",
                f"- Source config file: `{controller_config_file}`",
                f"- Controller name: `{controller_config.get('controller_name', result.controller_name)}`",
                f"- Controller id: `{controller_config.get('id', agent_name)}`",
            ]
        )

        frontmatter_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
        return f"---\n{frontmatter_yaml}\n---\n\n" + "\n".join(sections) + "\n"

    @staticmethod
    def build_agent_name(result: OptimizationResult, trial: TrialResult) -> str:
        trading_pair = CTAGenerator._extract_primary_pair(trial.controller_config) or "multi_pair"
        pair_token = trading_pair.replace("-", "").replace("/", "").lower()
        controller = result.controller_name.replace(" ", "_").lower()
        return f"{controller}_{pair_token}_trial{trial.trial_id}"

    @staticmethod
    def _extract_primary_pair(controller_config: dict) -> Optional[str]:
        for key in ("trading_pair", "candles_trading_pair", "base_trading_pair"):
            value = controller_config.get(key)
            if isinstance(value, str) and value:
                return value
        return None
