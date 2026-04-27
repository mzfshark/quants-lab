#!/usr/bin/env python3
"""
QuantsLab CLI - Main entry point for task management and optimization workflows.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv():
        return False

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _missing_dependency_message(exc: ModuleNotFoundError) -> str:
    dependency = exc.name or "unknown"
    argv = sys.argv[:] if sys.argv else ["cli.py"]
    invoked_as = " ".join(argv)
    if argv[0].endswith(".py"):
        invoked_as = f"python {invoked_as}"
    lines = [
        f"Missing Python dependency: {dependency}",
        "",
        "Quants-Lab is probably running outside the 'quants-lab' Conda environment.",
        "Do not install packages into the system Python for this project.",
        "",
        "Use one of these commands instead:",
        "  conda activate quants-lab",
        f"  {invoked_as}",
        "",
        "Or run it without activating first:",
        f"  conda run -n quants-lab {invoked_as}",
    ]
    if dependency == "pydantic":
        lines.extend(
            [
                "",
                "Tip: the error you saw is consistent with calling the OS Python",
                "instead of the environment created by `make install`.",
            ]
        )
    return "\n".join(lines)


def _normalize_config_path(config_path: str) -> str:
    if not config_path.startswith("config/") and not os.path.isabs(config_path):
        return f"config/{config_path}"
    return config_path


def _default_api_server() -> str:
    return os.getenv("HUMMINGBOT_API_URL") or os.getenv("BACKEND_API_SERVER") or "localhost"


def _default_api_port() -> int:
    return int(os.getenv("HUMMINGBOT_API_PORT") or os.getenv("BACKEND_API_PORT") or "8000")


def _default_agents_dir() -> str:
    return os.getenv("CONDOR_AGENTS_DIR", "~/condor/agents")


def _default_approver() -> str:
    return os.getenv("USER") or os.getenv("USERNAME") or "manual"


def parse_args():
    parser = argparse.ArgumentParser(
        description="QuantsLab Task Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run tasks continuously from templates
  python cli.py run-tasks --config template_1_candles_optimization.yml
  python cli.py trigger-task --task candles_downloader --config template_1_candles_optimization.yml

  # Universal optimization and Condor export
  quants-lab optimize --config config/optimize_macd_bb.yml
  quants-lab results --study macd_bb_btc_q2_2026_BTC_USDT
  quants-lab report --study macd_bb_btc_q2_2026_BTC_USDT
  quants-lab approvals list
  quants-lab approve --request-id macd_bb_btc_q2_2026_BTC_USDT-1234abcd --approver manual --live
  quants-lab export --study macd_bb_btc_q2_2026_BTC_USDT --top 1
  quants-lab deploy --study macd_bb_btc_q2_2026_BTC_USDT --top 1 --live

  # Runtime utilities
  quants-lab bots list --server localhost --port 8000
  quants-lab data download --connector binance_perpetual --pairs BTC-USDT,ETH-USDT --interval 15m --days 60
  quants-lab approvals poll-telegram --server localhost --port 8000
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    run_parser = subparsers.add_parser("run-tasks", help="Run tasks continuously")
    run_parser.add_argument(
        "--config",
        "-c",
        default="template_1_candles_optimization.yml",
        help="Configuration file name (from config/ directory)",
    )
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    trigger_parser = subparsers.add_parser("trigger-task", help="Run a single task once")
    trigger_parser.add_argument("--task", "-t", required=True, help="Task name to trigger")
    trigger_parser.add_argument(
        "--config",
        "-c",
        default="template_1_candles_optimization.yml",
        help="Configuration file name (from config/ directory)",
    )
    trigger_parser.add_argument("--timeout", type=int, default=300, help="Task timeout in seconds")

    direct_parser = subparsers.add_parser("run", help="Run a task directly with built-in defaults")
    direct_parser.add_argument("task_path", help="Task module path (e.g., app.tasks.data_collection.pools_screener)")
    direct_parser.add_argument("--timeout", type=int, default=600, help="Task timeout in seconds")

    serve_parser = subparsers.add_parser("serve", help="Start API server, optionally without background tasks")
    serve_parser.add_argument(
        "--config",
        "-c",
        default="template_1_candles_optimization.yml",
        help="Configuration file name (from config/ directory)",
    )
    serve_parser.add_argument("--port", "-p", type=int, default=8000, help="API server port")
    serve_parser.add_argument("--host", default="0.0.0.0", help="API server host")
    serve_parser.add_argument(
        "--api-only",
        action="store_true",
        help="Start only the FastAPI service without running background tasks",
    )

    list_parser = subparsers.add_parser("list-tasks", help="List available tasks")
    list_parser.add_argument(
        "--config",
        "-c",
        default="template_1_candles_optimization.yml",
        help="Configuration file name (from config/ directory)",
    )

    validate_parser = subparsers.add_parser("validate-config", help="Validate task configuration")
    validate_parser.add_argument("--config", "-c", required=True, help="Configuration file name (from config/ directory)")

    optimize_parser = subparsers.add_parser("optimize", help="Run optimization from a universal YAML config")
    optimize_parser.add_argument("--config", "-c", required=True, help="Optimization YAML file")

    results_parser = subparsers.add_parser("results", help="Show optimization results for a study")
    results_parser.add_argument("--study", required=True, help="Study name")
    results_parser.add_argument("--top", type=int, default=5, help="How many top trials to show")
    results_parser.add_argument("--json", action="store_true", help="Print JSON payload instead of formatted output")

    report_parser = subparsers.add_parser("report", help="Show human-facing report artifacts for a study")
    report_parser.add_argument("--study", required=True, help="Study name")
    report_parser.add_argument("--json", action="store_true", help="Print JSON payload instead of formatted output")

    export_parser = subparsers.add_parser("export", help="Export the best study results into Condor bundles")
    export_parser.add_argument("--study", required=True, help="Study name")
    export_parser.add_argument("--top", type=int, default=1, help="How many top trials to export")
    export_parser.add_argument("--agents-dir", default=_default_agents_dir(), help="Condor agents directory")

    deploy_parser = subparsers.add_parser("deploy", help="Deploy a study result through the Hummingbot API")
    deploy_parser.add_argument("--study", required=True, help="Study name")
    deploy_parser.add_argument("--top", type=int, default=1, help="How many top trials to deploy")
    deploy_parser.add_argument("--agents-dir", default=_default_agents_dir(), help="Condor agents directory")
    deploy_parser.add_argument("--server", default=_default_api_server(), help="Hummingbot API host or URL")
    deploy_parser.add_argument("--port", type=int, default=_default_api_port(), help="Hummingbot API port")
    deploy_parser.add_argument("--username", default=os.getenv("HUMMINGBOT_API_USERNAME"), help="API username")
    deploy_parser.add_argument("--password", default=os.getenv("HUMMINGBOT_API_PASSWORD"), help="API password")
    deploy_parser.add_argument("--profile", default=os.getenv("HUMMINGBOT_PROFILE", "master_account"), help="Hummingbot profile")
    deploy_parser.add_argument("--live", action="store_true", help="Actually call the Hummingbot API")

    approvals_parser = subparsers.add_parser("approvals", help="Manage deployment approval requests")
    approvals_subparsers = approvals_parser.add_subparsers(dest="approvals_command", help="Approval commands")
    approvals_list_parser = approvals_subparsers.add_parser("list", help="List approval requests")
    approvals_list_parser.add_argument("--study", help="Filter by study name")
    approvals_list_parser.add_argument(
        "--status",
        choices=["pending", "blocked", "approved", "rejected", "deployed", "failed"],
        help="Filter by approval status",
    )
    approvals_list_parser.add_argument("--json", action="store_true", help="Print JSON payload instead of formatted output")

    approvals_poll_parser = approvals_subparsers.add_parser("poll-telegram", help="Poll Telegram once for approval commands")
    approvals_poll_parser.add_argument("--server", default=_default_api_server(), help="Hummingbot API host or URL")
    approvals_poll_parser.add_argument("--port", type=int, default=_default_api_port(), help="Hummingbot API port")
    approvals_poll_parser.add_argument("--username", default=os.getenv("HUMMINGBOT_API_USERNAME"), help="API username")
    approvals_poll_parser.add_argument("--password", default=os.getenv("HUMMINGBOT_API_PASSWORD"), help="API password")
    approvals_poll_parser.add_argument("--profile", default=os.getenv("HUMMINGBOT_PROFILE", "master_account"), help="Hummingbot profile")

    approve_parser = subparsers.add_parser("approve", help="Approve a pending deployment request")
    approve_parser.add_argument("--request-id", help="Approval request ID")
    approve_parser.add_argument("--study", help="Study name fallback when request-id is not provided")
    approve_parser.add_argument("--approver", default=_default_approver(), help="Human approver name")
    approve_parser.add_argument("--live", action="store_true", help="Approve and deploy live immediately")
    approve_parser.add_argument("--agents-dir", default=_default_agents_dir(), help="Condor agents directory")
    approve_parser.add_argument("--server", default=_default_api_server(), help="Hummingbot API host or URL")
    approve_parser.add_argument("--port", type=int, default=_default_api_port(), help="Hummingbot API port")
    approve_parser.add_argument("--username", default=os.getenv("HUMMINGBOT_API_USERNAME"), help="API username")
    approve_parser.add_argument("--password", default=os.getenv("HUMMINGBOT_API_PASSWORD"), help="API password")
    approve_parser.add_argument("--profile", default=os.getenv("HUMMINGBOT_PROFILE", "master_account"), help="Hummingbot profile")

    reject_parser = subparsers.add_parser("reject", help="Reject a pending deployment request")
    reject_parser.add_argument("--request-id", help="Approval request ID")
    reject_parser.add_argument("--study", help="Study name fallback when request-id is not provided")
    reject_parser.add_argument("--approver", default=_default_approver(), help="Human approver name")
    reject_parser.add_argument("--reason", required=True, help="Reason for rejection")

    bots_parser = subparsers.add_parser("bots", help="Inspect Hummingbot runtime state")
    bots_subparsers = bots_parser.add_subparsers(dest="bots_command", help="Bots commands")
    bots_list_parser = bots_subparsers.add_parser("list", help="List active bots and summary")
    bots_list_parser.add_argument("--server", default=_default_api_server(), help="Hummingbot API host or URL")
    bots_list_parser.add_argument("--port", type=int, default=_default_api_port(), help="Hummingbot API port")
    bots_list_parser.add_argument("--username", default=os.getenv("HUMMINGBOT_API_USERNAME"), help="API username")
    bots_list_parser.add_argument("--password", default=os.getenv("HUMMINGBOT_API_PASSWORD"), help="API password")
    bots_list_parser.add_argument("--json", action="store_true", help="Print JSON payload instead of formatted output")

    data_parser = subparsers.add_parser("data", help="Data utilities")
    data_subparsers = data_parser.add_subparsers(dest="data_command", help="Data commands")
    data_download_parser = data_subparsers.add_parser("download", help="Download candles into the local cache")
    data_download_parser.add_argument("--connector", required=True, help="Connector name")
    data_download_parser.add_argument("--pairs", required=True, help="Comma-separated trading pairs")
    data_download_parser.add_argument("--interval", default="15m", help="Candle interval")
    data_download_parser.add_argument("--days", type=int, default=60, help="Lookback window in days")
    data_download_parser.add_argument("--from-trades", action="store_true", help="Build candles from trades")

    return parser.parse_args()


async def run_tasks(config_path: str, verbose: bool = False):
    """Run tasks continuously."""
    from core.tasks.runner import TaskRunner

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config_path = _normalize_config_path(config_path)

    logger.info("Starting QuantsLab Task Runner v2.0")
    logger.info(f"Config: {config_path}")

    try:
        runner = TaskRunner(config_path=config_path, enable_api=False)
        await runner.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as exc:
        logger.error(f"Error running tasks: {exc}")
        sys.exit(1)


async def trigger_task(task_name: str, config_path: str, timeout: int):
    """Trigger a single task."""
    from core.tasks.runner import TaskRunner

    config_path = _normalize_config_path(config_path)

    logger.info(f"Triggering task: {task_name}")
    logger.info(f"Config: {config_path}")
    logger.info(f"Timeout: {timeout}s")

    try:
        runner = TaskRunner(config_path=config_path)

        from core.tasks.orchestrator import TaskOrchestrator
        from core.tasks.storage import create_task_storage

        storage_config = runner.config.get("storage", {}) if isinstance(runner.config.get("storage", {}), dict) else {}
        resolved_backend = os.getenv("QUANTS_LAB_STORAGE", "").strip().lower() or storage_config.get("type")
        storage = create_task_storage(storage_backend=resolved_backend, storage_config=storage_config)
        max_concurrent = runner.config.get("max_concurrent_tasks", 10)
        runner.orchestrator = TaskOrchestrator(
            storage=storage,
            max_concurrent_tasks=max_concurrent,
            retry_failed_tasks=runner.config.get("retry_failed_tasks", True),
        )

        tasks = await runner._initialize_tasks()
        for task in tasks:
            runner.orchestrator.add_task(task)

        result = await runner.orchestrator.execute_task(task_name=task_name, force=True)

        if result:
            logger.info(f"Task {task_name} completed with status: {result.status}")
            if result.error_message:
                logger.error(f"Error: {result.error_message}")
                sys.exit(1)
        else:
            logger.error(f"Task {task_name} not found or could not be executed")
            sys.exit(1)

    except Exception as exc:
        logger.error(f"Error triggering task: {exc}")
        sys.exit(1)


async def serve_api(config_path: str, host: str, port: int, api_only: bool = False):
    """Start API server with optional background task orchestration."""
    config_path = _normalize_config_path(config_path)

    logger.info("Starting QuantsLab API Server")
    logger.info(f"Server: http://{host}:{port}")
    if api_only:
        logger.info("Mode: api-only")
    else:
        logger.info(f"Config: {config_path}")

    try:
        if api_only:
            import uvicorn

            from core.tasks.api import app

            server = uvicorn.Server(
                uvicorn.Config(app, host=host, port=port, log_level="info", loop="asyncio")
            )
            await server.serve()
            return

        from core.tasks.runner import TaskRunner

        runner = TaskRunner(config_path=config_path, enable_api=True)
        runner.api_host = host
        runner.api_port = port
        await runner.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as exc:
        logger.error(f"Error running server: {exc}")
        sys.exit(1)


async def run_task_direct(task_path: str, timeout: int):
    """Run a task directly using its built-in main() function."""
    logger.info(f"Running task directly: {task_path}")
    logger.info(f"Timeout: {timeout}s")

    try:
        import importlib

        module = importlib.import_module(task_path)

        if not hasattr(module, "main"):
            logger.error(f"Task module {task_path} does not have a main() function")
            sys.exit(1)

        await asyncio.wait_for(module.main(), timeout=timeout)
        logger.info(f"Task {task_path} completed successfully")

    except asyncio.TimeoutError:
        logger.error(f"Task {task_path} timed out after {timeout} seconds")
        sys.exit(1)
    except ImportError as exc:
        logger.error(f"Failed to import task {task_path}: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Error running task {task_path}: {exc}")
        sys.exit(1)


def list_tasks(config_path: str):
    """List available tasks from configuration."""
    from core.tasks.runner import TaskRunner

    config_path = _normalize_config_path(config_path)

    logger.info(f"Loading tasks from: {config_path}")

    try:
        if not os.path.exists(config_path):
            logger.error(f"Config file not found: {config_path}")
            sys.exit(1)

        runner = TaskRunner(config_path=config_path)
        tasks_config = runner.config

        print("\nAvailable Tasks:")
        print("=" * 50)
        for task_name, task_config in tasks_config.get("tasks", {}).items():
            enabled = task_config.get("enabled", True)
            status = "enabled" if enabled else "disabled"
            task_class = task_config.get("task_class", "Unknown")
            schedule = task_config.get("schedule", {})
            schedule_info = f"({schedule.get('type', 'unknown')})"
            print(f"{task_name:30} {status:12} {task_class} {schedule_info}")

    except Exception as exc:
        logger.error(f"Error listing tasks: {exc}")
        sys.exit(1)


def validate_config(config_path: str):
    """Validate task configuration file."""
    from core.tasks.base import TaskConfig
    from core.tasks.runner import TaskRunner

    config_path = _normalize_config_path(config_path)

    logger.info(f"Validating config: {config_path}")

    try:
        if not os.path.exists(config_path):
            logger.error(f"Config file not found: {config_path}")
            sys.exit(1)

        runner = TaskRunner(config_path=config_path)
        tasks_config = runner.config

        errors = []
        for task_name, task_config in tasks_config.get("tasks", {}).items():
            try:
                TaskConfig(**task_config, name=task_name)
            except Exception as exc:
                errors.append(f"Task {task_name}: {exc}")

        if errors:
            logger.error("Validation errors found:")
            for error in errors:
                logger.error(f"  - {error}")
            sys.exit(1)
        logger.info("Config is valid")

    except Exception as exc:
        logger.error(f"Config validation failed: {exc}")
        sys.exit(1)


def _load_result_from_artifacts(study_name: str):
    from core.backtesting.optimization_results import OptimizationResultsManager

    manager = OptimizationResultsManager()
    return manager.load_latest_result(study_name)


def _load_result_for_study(study_name: str, top_k: int, refresh_from_study: bool = False):
    cached_result = _load_result_from_artifacts(study_name)
    if cached_result is not None and len(cached_result.top_k_trials) >= max(1, top_k):
        return cached_result

    from core.backtesting.optimizer import StrategyOptimizer

    optimizer = StrategyOptimizer()
    if refresh_from_study:
        return optimizer.export_optimization_result(study_name, top_k=max(1, top_k))
    return optimizer.build_optimization_result(study_name, top_k=max(1, top_k))


def _print_result_summary(result, top_k: int):
    validation = result.metadata.get("validation", {})
    deployment = result.metadata.get("condor_deployment", {})

    print(f"\nStudy: {result.study_name}")
    print(f"Controller: {result.controller_name}")
    print(f"Pairs: {', '.join(result.trading_pairs) if result.trading_pairs else 'N/A'}")
    print(f"Trials: {result.n_trials}")
    print(f"Output: {result.output_dir or 'N/A'}")
    if validation:
        print(
            "Validation: "
            f"{'passed' if validation.get('passed') else 'failed'} | "
            f"Sharpe={validation.get('sharpe_ratio', 0.0):.6f} | "
            f"Return={validation.get('total_return', 0.0):.6f}"
        )
    if deployment:
        print(
            "Condor: "
            f"{len(deployment.get('artifacts', []))} artifact(s) | "
            f"{'dry-run' if deployment.get('dry_run', True) else 'live'}"
        )

    print("\nTop Trials:")
    print("-" * 90)
    for trial in result.top_k_trials[:top_k]:
        print(
            f"#{trial.trial_id:<4} "
            f"objective={trial.objective_value:>10.6f} "
            f"sharpe={trial.sharpe_ratio:>10.6f} "
            f"return={trial.total_return:>10.6f} "
            f"drawdown={trial.max_drawdown:>10.6f} "
            f"trades={trial.total_trades:>5}"
        )


async def optimize_strategy(config_path: str):
    from core.backtesting.universal_strategy import UniversalOptimizationRunner

    config_path = _normalize_config_path(config_path)
    logger.info(f"Starting universal optimization from {config_path}")
    runner = UniversalOptimizationRunner()
    results = await runner.optimize_from_yaml(config_path)
    for result in results:
        _print_result_summary(result, top_k=min(5, len(result.top_k_trials)))


def show_results(study_name: str, top_k: int, as_json: bool = False):
    result = _load_result_for_study(study_name, top_k=top_k, refresh_from_study=False)
    if as_json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        _print_result_summary(result, top_k)


def show_report(study_name: str, as_json: bool = False):
    from core.condor.approval_manager import ApprovalManager
    from core.reporting.optimization_reporter import OptimizationReporter

    result = _load_result_for_study(study_name, top_k=3, refresh_from_study=False)
    approval_manager = ApprovalManager()
    approval_request = result.metadata.get("approval_request")
    if approval_request is None:
        try:
            approval_request = approval_manager.load_request(study_name=study_name).model_dump(mode="json")
        except Exception:
            approval_request = None

    reporter = OptimizationReporter()
    artifacts = reporter.generate_report_bundle(result=result, approval_request=approval_request)
    payload = {
        "study_name": result.study_name,
        "output_dir": result.output_dir,
        "approval_request": approval_request,
        "artifacts": artifacts.model_dump(mode="json"),
    }
    if as_json:
        print(json.dumps(payload, indent=2))
        return

    print(f"\nReport for {result.study_name}")
    print(f"Output: {result.output_dir or 'N/A'}")
    if approval_request:
        print(f"Approval: {approval_request.get('request_id')} [{approval_request.get('status')}]")
    print("Artifacts:")
    for key, value in payload["artifacts"].items():
        if value:
            print(f"- {key}: {value}")


def list_approvals(study_name: Optional[str], status: Optional[str], as_json: bool = False):
    from core.condor.approval_manager import ApprovalManager
    from core.condor.models import ApprovalStatus

    approval_manager = ApprovalManager()
    status_value = ApprovalStatus(status) if status else None
    requests = approval_manager.list_requests(study_name=study_name, status=status_value)

    if as_json:
        print(json.dumps([request.model_dump(mode="json") for request in requests], indent=2))
        return

    if not requests:
        print("No approval requests found.")
        return

    print("\nApproval Requests:")
    print("-" * 100)
    for request in requests:
        print(
            f"{request.request_id} | {request.study_name} | {request.status.value} | "
            f"top_k={request.requested_top_k} | created_at={request.created_at.isoformat()}"
        )


async def approve_request(
    request_id: Optional[str],
    study_name: Optional[str],
    approver: str,
    live: bool,
    agents_dir: str,
    server: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    profile: str,
):
    from core.condor.approval_manager import ApprovalManager

    approval_manager = ApprovalManager()
    request = await approval_manager.approve_request(
        approver=approver,
        request_id=request_id,
        study_name=study_name,
        live=live,
        agents_dir=Path(agents_dir),
        server=server,
        port=port,
        username=username,
        password=password,
        profile_name=profile,
    )
    print(f"Approved: {request.request_id}")
    print(f"Status: {request.status.value}")
    if request.deployment_report:
        print("Deployment report recorded.")


def reject_request(request_id: Optional[str], study_name: Optional[str], approver: str, reason: str):
    from core.condor.approval_manager import ApprovalManager

    approval_manager = ApprovalManager()
    request = approval_manager.reject_request(
        approver=approver,
        reason=reason,
        request_id=request_id,
        study_name=study_name,
    )
    print(f"Rejected: {request.request_id}")
    print(f"Status: {request.status.value}")


async def poll_telegram_approvals(
    server: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    profile: str,
):
    from core.condor.telegram_approval_router import TelegramApprovalRouter

    router = TelegramApprovalRouter(
        server=server,
        port=port,
        username=username,
        password=password,
        profile_name=profile,
    )
    result = await router.poll_once()
    print(json.dumps(result, indent=2))


async def export_study(study_name: str, top_k: int, agents_dir: str, live: bool = False, api_client=None, profile: Optional[str] = None):
    from core.condor.deploy_manager import CondorDeployManager

    result = _load_result_for_study(study_name, top_k=top_k, refresh_from_study=not live)
    if live:
        validation = result.metadata.get("validation")
        if not validation:
            raise RuntimeError(
                "Live deploy requires a stored result.json with walk-forward validation metadata. "
                "Run `quants-lab optimize` for this study first."
            )
        if not validation.get("passed", False):
            raise RuntimeError("Live deploy blocked because walk-forward validation did not pass.")

    deploy_manager = CondorDeployManager(api_client=api_client, agents_dir=Path(agents_dir))
    report = await deploy_manager.deploy_optimization_result(
        result=result,
        deploy_top_k=top_k,
        dry_run=not live,
        profile_name=profile,
    )

    print(f"\nDeployment Report: {report.study_name}")
    print(f"Mode: {'live' if live else 'dry-run'}")
    print(f"Artifacts: {len(report.artifacts)}")
    for artifact in report.artifacts:
        status = "deployed" if artifact.deployed else "exported"
        print(f"- {artifact.agent_name}: {status} -> {artifact.agent_dir}")
        for file_name, file_path in artifact.files.items():
            print(f"  {file_name}: {file_path}")
        if artifact.errors:
            print(f"  errors: {' | '.join(artifact.errors)}")
    return report


async def deploy_study(
    study_name: str,
    top_k: int,
    agents_dir: str,
    server: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    profile: str,
    live: bool,
):
    from core.services.hummingbot_api_client import HummingbotAPIClient

    api_client = HummingbotAPIClient(server=server, port=port, username=username, password=password)
    try:
        await export_study(
            study_name=study_name,
            top_k=top_k,
            agents_dir=agents_dir,
            live=live,
            api_client=api_client,
            profile=profile,
        )
    finally:
        await api_client.close()


async def list_bots(server: str, port: int, username: Optional[str], password: Optional[str], as_json: bool = False):
    from core.services.hummingbot_api_client import HummingbotAPIClient

    api_client = HummingbotAPIClient(server=server, port=port, username=username, password=password)
    try:
        snapshot = await api_client.get_runtime_snapshot()
    finally:
        await api_client.close()

    if as_json:
        print(json.dumps(snapshot, indent=2, default=str))
        return

    print(f"\nAPI: {'online' if snapshot['api_available'] else 'offline'}")
    print(
        f"Bots: total={snapshot['bot_summary']['total']} "
        f"active={snapshot['bot_summary']['active']} "
        f"stopped={snapshot['bot_summary']['stopped']}"
    )
    if snapshot.get("portfolio_value") is not None:
        print(f"Portfolio value: {snapshot['portfolio_value']:.2f}")
    if snapshot["bots"]:
        print("\nBot Snapshot:")
        for bot in snapshot["bots"][:15]:
            line = f"- {bot['bot_name']} [{bot.get('status', 'unknown')}]"
            if bot.get("trading_pair"):
                line += f" pair={bot['trading_pair']}"
            if bot.get("pnl") is not None:
                line += f" pnl={float(bot['pnl']):.6f}"
            print(line)


async def download_data(connector: str, pairs: str, interval: str, days: int, from_trades: bool = False):
    from core.data_sources import CLOBDataSource

    trading_pairs = [pair.strip() for pair in pairs.split(",") if pair.strip()]
    if not trading_pairs:
        raise ValueError("At least one trading pair must be provided.")

    clob = CLOBDataSource()
    total_rows = 0
    for trading_pair in trading_pairs:
        candles = await clob.get_candles_last_days(
            connector_name=connector,
            trading_pair=trading_pair,
            interval=interval,
            days=days,
            from_trades=from_trades,
        )
        rows = len(candles.data.index)
        total_rows += rows
        print(f"- {trading_pair}: {rows} candles")
    clob.dump_candles_cache()
    print(f"\nDownloaded {total_rows} candles across {len(trading_pairs)} pair(s).")


async def async_main():
    args = parse_args()

    if args.command == "run-tasks":
        await run_tasks(args.config, args.verbose)
    elif args.command == "trigger-task":
        await trigger_task(args.task, args.config, args.timeout)
    elif args.command == "run":
        await run_task_direct(args.task_path, args.timeout)
    elif args.command == "serve":
        await serve_api(args.config, args.host, args.port, api_only=args.api_only)
    elif args.command == "list-tasks":
        list_tasks(args.config)
    elif args.command == "validate-config":
        validate_config(args.config)
    elif args.command == "optimize":
        await optimize_strategy(args.config)
    elif args.command == "results":
        show_results(args.study, args.top, args.json)
    elif args.command == "report":
        show_report(args.study, args.json)
    elif args.command == "export":
        await export_study(args.study, args.top, args.agents_dir, live=False)
    elif args.command == "deploy":
        await deploy_study(
            study_name=args.study,
            top_k=args.top,
            agents_dir=args.agents_dir,
            server=args.server,
            port=args.port,
            username=args.username,
            password=args.password,
            profile=args.profile,
            live=args.live,
        )
    elif args.command == "approvals" and args.approvals_command == "list":
        list_approvals(args.study, args.status, args.json)
    elif args.command == "approvals" and args.approvals_command == "poll-telegram":
        await poll_telegram_approvals(args.server, args.port, args.username, args.password, args.profile)
    elif args.command == "approve":
        await approve_request(
            request_id=args.request_id,
            study_name=args.study,
            approver=args.approver,
            live=args.live,
            agents_dir=args.agents_dir,
            server=args.server,
            port=args.port,
            username=args.username,
            password=args.password,
            profile=args.profile,
        )
    elif args.command == "reject":
        reject_request(args.request_id, args.study, args.approver, args.reason)
    elif args.command == "bots" and args.bots_command == "list":
        await list_bots(args.server, args.port, args.username, args.password, args.json)
    elif args.command == "data" and args.data_command == "download":
        await download_data(args.connector, args.pairs, args.interval, args.days, args.from_trades)
    else:
        logger.error("No command specified. Use --help for usage.")
        sys.exit(1)


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    except ModuleNotFoundError as exc:
        logger.error(_missing_dependency_message(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
