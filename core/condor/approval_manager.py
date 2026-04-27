from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from core.condor.models import ApprovalRequest, ApprovalStatus
from core.data_paths import data_paths

if TYPE_CHECKING:
    from core.backtesting.optimization_results import OptimizationResult


class ApprovalManager:
    """Persist and resolve manual deployment approvals."""

    def __init__(self, approvals_dir: Optional[Path] = None):
        outputs_root = Path(os.getenv("QUANTS_LAB_OUTPUTS_DIR", data_paths.base_path / "app" / "outputs"))
        self.approvals_dir = Path(approvals_dir) if approvals_dir else outputs_root / "approvals"
        self.approvals_dir.mkdir(parents=True, exist_ok=True)

    def create_request(
        self,
        result: "OptimizationResult",
        deploy_top_k: int = 1,
        requested_by: str = "system",
    ) -> ApprovalRequest:
        validation = result.metadata.get("validation", {}) or {}
        deployment = result.metadata.get("condor_deployment", {}) or {}
        live_deployed = deployment and not deployment.get("dry_run", True) and not deployment.get("errors")
        validation_passed = bool(validation.get("passed", False))

        if live_deployed:
            status = ApprovalStatus.DEPLOYED
        elif validation_passed:
            status = ApprovalStatus.PENDING
        else:
            status = ApprovalStatus.BLOCKED

        request_id = f"{self._safe_name(result.study_name)}-{uuid.uuid4().hex[:8]}"
        request = ApprovalRequest(
            request_id=request_id,
            study_name=result.study_name,
            controller_name=result.controller_name,
            trading_pairs=result.trading_pairs,
            result_output_dir=result.output_dir or "",
            created_at=datetime.now(timezone.utc),
            status=status,
            requested_top_k=deploy_top_k,
            validation_passed=validation_passed,
            requested_by=requested_by,
        )
        request.approve_command = f'quants-lab approve --request-id {request.request_id} --approver "<name>"'
        request.approve_live_command = f'quants-lab approve --request-id {request.request_id} --approver "<name>" --live'
        request.reject_command = (
            f'quants-lab reject --request-id {request.request_id} --approver "<name>" --reason "not approved"'
        )
        self.save_request(request)
        return request

    def save_request(self, request: ApprovalRequest) -> None:
        request_dir = self.approvals_dir / self._safe_name(request.study_name)
        request_dir.mkdir(parents=True, exist_ok=True)
        request_file = request_dir / f"{request.request_id}.json"
        with request_file.open("w", encoding="utf-8") as file:
            json.dump(request.model_dump(mode="json"), file, indent=2)

    def list_requests(
        self,
        study_name: Optional[str] = None,
        status: Optional[ApprovalStatus] = None,
    ) -> List[ApprovalRequest]:
        requests: List[ApprovalRequest] = []
        if study_name:
            directories = [self.approvals_dir / self._safe_name(study_name)]
        else:
            directories = [path for path in self.approvals_dir.iterdir() if path.is_dir()] if self.approvals_dir.exists() else []

        for directory in directories:
            if not directory.exists():
                continue
            for file_path in sorted(directory.glob("*.json"), reverse=True):
                with file_path.open("r", encoding="utf-8") as file:
                    payload = json.load(file)
                request = ApprovalRequest.model_validate(payload)
                if status is None or request.status == status:
                    requests.append(request)
        requests.sort(key=lambda item: item.created_at, reverse=True)
        return requests

    def load_request(
        self,
        request_id: Optional[str] = None,
        study_name: Optional[str] = None,
    ) -> ApprovalRequest:
        if request_id:
            for request in self.list_requests():
                if request.request_id == request_id:
                    return request
            raise FileNotFoundError(f"Approval request not found: {request_id}")

        if study_name:
            requests = self.list_requests(study_name=study_name)
            if requests:
                return requests[0]
            raise FileNotFoundError(f"No approval requests found for study: {study_name}")

        raise ValueError("Either request_id or study_name must be provided.")

    def load_result_for_request(self, request: ApprovalRequest) -> OptimizationResult:
        from core.backtesting.optimization_results import OptimizationResult

        result_file = Path(request.result_output_dir) / "result.json"
        if not result_file.exists():
            raise FileNotFoundError(f"Result file not found for approval request: {result_file}")
        with result_file.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return OptimizationResult.model_validate(payload)

    async def approve_request(
        self,
        approver: str,
        request_id: Optional[str] = None,
        study_name: Optional[str] = None,
        live: bool = False,
        agents_dir: Optional[Path] = None,
        server: str = "localhost",
        port: int = 8000,
        username: Optional[str] = None,
        password: Optional[str] = None,
        profile_name: Optional[str] = None,
    ) -> ApprovalRequest:
        from core.condor.deploy_manager import CondorDeployManager
        from core.services.hummingbot_api_client import HummingbotAPIClient

        request = self.load_request(request_id=request_id, study_name=study_name)
        if not request.validation_passed:
            raise RuntimeError("Approval blocked because walk-forward validation did not pass.")

        request.decided_by = approver
        request.decision_at = datetime.now(timezone.utc)
        request.reason = None

        if live:
            result = self.load_result_for_request(request)
            api_client = HummingbotAPIClient(server=server, port=port, username=username, password=password)
            try:
                deploy_manager = CondorDeployManager(api_client=api_client, agents_dir=agents_dir)
                deployment_report = await deploy_manager.deploy_optimization_result(
                    result=result,
                    deploy_top_k=request.requested_top_k,
                    dry_run=False,
                    profile_name=profile_name,
                )
            finally:
                await api_client.close()
            request.deployment_report = deployment_report.model_dump(mode="json")
            request.status = ApprovalStatus.DEPLOYED if not deployment_report.errors else ApprovalStatus.FAILED
        else:
            request.status = ApprovalStatus.APPROVED

        self.save_request(request)
        return request

    def reject_request(
        self,
        approver: str,
        reason: str,
        request_id: Optional[str] = None,
        study_name: Optional[str] = None,
    ) -> ApprovalRequest:
        request = self.load_request(request_id=request_id, study_name=study_name)
        request.status = ApprovalStatus.REJECTED
        request.decided_by = approver
        request.decision_at = datetime.now(timezone.utc)
        request.reason = reason
        self.save_request(request)
        return request

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "study"
