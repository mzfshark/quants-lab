from __future__ import annotations

import csv
import json
import math
import struct
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.backtesting.optimization_results import OptimizationResult


class ReportArtifacts(BaseModel):
    summary_markdown: Optional[str] = None
    summary_json: Optional[str] = None
    equity_curve_csv: Optional[str] = None
    equity_curve_png: Optional[str] = None
    approval_instructions: Optional[str] = None


class OptimizationReporter:
    """Generate human-readable artifacts for optimization results."""

    def generate_report_bundle(
        self,
        result: OptimizationResult,
        approval_request: Optional[Dict[str, Any]] = None,
    ) -> ReportArtifacts:
        if not result.output_dir:
            raise ValueError("Optimization result does not have an output_dir.")

        output_dir = Path(result.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        equity_curve = result.best_trial.equity_curve
        artifacts = ReportArtifacts()

        summary_markdown = output_dir / "human_report.md"
        summary_json = output_dir / "human_report.json"
        instructions_file = output_dir / "approval_instructions.txt"

        with summary_markdown.open("w", encoding="utf-8") as file:
            file.write(self._build_markdown_report(result, approval_request))
        with summary_json.open("w", encoding="utf-8") as file:
            json.dump(self._build_json_report(result, approval_request), file, indent=2)
        with instructions_file.open("w", encoding="utf-8") as file:
            file.write(self._build_approval_instructions(result, approval_request))

        artifacts.summary_markdown = str(summary_markdown)
        artifacts.summary_json = str(summary_json)
        artifacts.approval_instructions = str(instructions_file)

        if equity_curve:
            curve_csv = output_dir / "equity_curve.csv"
            curve_png = output_dir / "equity_curve.png"
            self._write_equity_curve_csv(curve_csv, equity_curve)
            self._write_equity_curve_png(curve_png, equity_curve)
            artifacts.equity_curve_csv = str(curve_csv)
            artifacts.equity_curve_png = str(curve_png)

        return artifacts

    def _build_markdown_report(
        self,
        result: OptimizationResult,
        approval_request: Optional[Dict[str, Any]],
    ) -> str:
        validation = result.metadata.get("validation", {})
        deployment = result.metadata.get("condor_deployment", {})
        lines = [
            f"# Optimization Report: {result.study_name}",
            "",
            f"- Controller: `{result.controller_name}`",
            f"- Trading pairs: `{', '.join(result.trading_pairs) if result.trading_pairs else 'N/A'}`",
            f"- Created at: `{result.created_at.isoformat()}`",
            f"- Output dir: `{result.output_dir or 'N/A'}`",
            "",
            "## Best Trial",
            f"- Trial ID: `{result.best_trial.trial_id}`",
            f"- Objective value: `{result.best_trial.objective_value:.6f}`",
            f"- Sharpe ratio: `{result.best_trial.sharpe_ratio:.6f}`",
            f"- Total return: `{result.best_trial.total_return:.6f}`",
            f"- Max drawdown: `{result.best_trial.max_drawdown:.6f}`",
            f"- Profit factor: `{result.best_trial.profit_factor:.6f}`",
            f"- Win rate: `{result.best_trial.win_rate:.6f}`",
            f"- Total trades: `{result.best_trial.total_trades}`",
            "",
            "## Top 3",
        ]

        for trial in result.top_k_trials[:3]:
            lines.append(
                f"- Trial `{trial.trial_id}` | objective `{trial.objective_value:.6f}` | "
                f"sharpe `{trial.sharpe_ratio:.6f}` | return `{trial.total_return:.6f}` | "
                f"drawdown `{trial.max_drawdown:.6f}`"
            )

        if validation:
            lines.extend(
                [
                    "",
                    "## Walk-Forward Validation",
                    f"- Passed: `{'yes' if validation.get('passed') else 'no'}`",
                    f"- Sharpe ratio: `{float(validation.get('sharpe_ratio', 0.0)):.6f}`",
                    f"- Total return: `{float(validation.get('total_return', 0.0)):.6f}`",
                    f"- Max drawdown: `{float(validation.get('max_drawdown', 0.0)):.6f}`",
                    f"- Total trades: `{validation.get('total_trades', 0)}`",
                ]
            )

        if deployment:
            lines.extend(
                [
                    "",
                    "## Condor Status",
                    f"- Artifacts exported: `{len(deployment.get('artifacts', []))}`",
                    f"- Mode: `{'live' if not deployment.get('dry_run', True) else 'dry-run'}`",
                ]
            )
            if deployment.get("auto_deploy_blocked"):
                lines.append("- Auto deploy blocked: `yes`")

        if approval_request:
            lines.extend(
                [
                    "",
                    "## Approval",
                    f"- Request ID: `{approval_request.get('request_id')}`",
                    f"- Status: `{approval_request.get('status')}`",
                    f"- Approve command: `{approval_request.get('approve_command')}`",
                    f"- Reject command: `{approval_request.get('reject_command')}`",
                ]
            )

        lines.extend(
            [
                "",
                "## Best Params",
                "```json",
                json.dumps(result.best_trial.params, indent=2, default=str),
                "```",
            ]
        )
        return "\n".join(lines) + "\n"

    def _build_json_report(
        self,
        result: OptimizationResult,
        approval_request: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "study_name": result.study_name,
            "controller_name": result.controller_name,
            "created_at": result.created_at.isoformat(),
            "output_dir": result.output_dir,
            "best_trial": result.best_trial.model_dump(mode="json"),
            "top_trials": [trial.model_dump(mode="json") for trial in result.top_k_trials[:3]],
            "validation": result.metadata.get("validation"),
            "condor_deployment": result.metadata.get("condor_deployment"),
            "approval_request": approval_request,
        }

    def _build_approval_instructions(
        self,
        result: OptimizationResult,
        approval_request: Optional[Dict[str, Any]],
    ) -> str:
        if not approval_request:
            return "No approval request available.\n"
        return (
            f"Study: {result.study_name}\n"
            f"Request ID: {approval_request.get('request_id')}\n"
            f"Status: {approval_request.get('status')}\n\n"
            f"Approve (dry-run approval):\n{approval_request.get('approve_command')}\n\n"
            f"Approve and deploy live:\n{approval_request.get('approve_live_command')}\n\n"
            f"Reject:\n{approval_request.get('reject_command')}\n"
        )

    def _write_equity_curve_csv(self, output_file: Path, equity_curve: List[Dict[str, Any]]) -> None:
        with output_file.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["timestamp", "trade_pnl", "equity", "side", "status", "close_type"])
            writer.writeheader()
            for row in equity_curve:
                writer.writerow(row)

    def _write_equity_curve_png(self, output_file: Path, equity_curve: List[Dict[str, Any]]) -> None:
        width = 1200
        height = 600
        padding = 50
        pixels = bytearray([255] * width * height * 3)

        def set_pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
            if 0 <= x < width and 0 <= y < height:
                index = (y * width + x) * 3
                pixels[index:index + 3] = bytes(color)

        def draw_line(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
            dx = abs(x1 - x0)
            dy = -abs(y1 - y0)
            sx = 1 if x0 < x1 else -1
            sy = 1 if y0 < y1 else -1
            err = dx + dy
            while True:
                set_pixel(x0, y0, color)
                if x0 == x1 and y0 == y1:
                    break
                e2 = 2 * err
                if e2 >= dy:
                    err += dy
                    x0 += sx
                if e2 <= dx:
                    err += dx
                    y0 += sy

        for x in range(width):
            set_pixel(x, height - padding, (200, 200, 200))
        for y in range(height):
            set_pixel(padding, y, (200, 200, 200))

        if len(equity_curve) == 1:
            only_value = equity_curve[0].get("equity", 0.0)
            y = self._normalize_y(only_value, only_value, only_value, height, padding)
            draw_line(padding, y, width - padding, y, (59, 130, 246))
        elif len(equity_curve) > 1:
            values = [float(point.get("equity", 0.0)) for point in equity_curve]
            min_value = min(values)
            max_value = max(values)
            step = (width - 2 * padding) / max(len(values) - 1, 1)
            points: List[tuple[int, int]] = []
            for index, value in enumerate(values):
                x = int(padding + index * step)
                y = self._normalize_y(value, min_value, max_value, height, padding)
                points.append((x, y))

            zero_reference = 0.0
            if min_value <= zero_reference <= max_value:
                zero_y = self._normalize_y(zero_reference, min_value, max_value, height, padding)
                for x in range(padding, width - padding):
                    set_pixel(x, zero_y, (230, 230, 230))

            for first, second in zip(points, points[1:]):
                draw_line(first[0], first[1], second[0], second[1], (59, 130, 246))

        self._write_png(output_file, width, height, pixels)

    @staticmethod
    def _normalize_y(value: float, min_value: float, max_value: float, height: int, padding: int) -> int:
        if math.isclose(max_value, min_value):
            return height // 2
        usable_height = height - 2 * padding
        scaled = (value - min_value) / (max_value - min_value)
        return int(height - padding - scaled * usable_height)

    @staticmethod
    def _write_png(output_file: Path, width: int, height: int, pixels: bytearray) -> None:
        def chunk(chunk_type: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + chunk_type
                + data
                + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
            )

        raw = bytearray()
        stride = width * 3
        for row in range(height):
            raw.append(0)
            start = row * stride
            raw.extend(pixels[start:start + stride])

        png = bytearray()
        png.extend(b"\x89PNG\r\n\x1a\n")
        png.extend(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        png.extend(chunk(b"IDAT", zlib.compress(bytes(raw), level=9)))
        png.extend(chunk(b"IEND", b""))

        with output_file.open("wb") as file:
            file.write(png)
