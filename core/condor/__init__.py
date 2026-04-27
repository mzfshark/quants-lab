from __future__ import annotations

from importlib import import_module

__all__ = [
    "ApprovalManager",
    "ApprovalRequest",
    "ApprovalStatus",
    "CTAGenerator",
    "CondorDeployManager",
    "DeploymentArtifact",
    "DeploymentReport",
    "RiskLimits",
    "TelegramApprovalRouter",
]


def __getattr__(name: str):
    if name == "ApprovalManager":
        return import_module("core.condor.approval_manager").ApprovalManager
    if name == "TelegramApprovalRouter":
        return import_module("core.condor.telegram_approval_router").TelegramApprovalRouter
    if name in {"CTAGenerator"}:
        module = import_module("core.condor.cta_generator")
        return getattr(module, name)
    if name in {"CondorDeployManager"}:
        module = import_module("core.condor.deploy_manager")
        return getattr(module, name)
    if name in {"ApprovalRequest", "ApprovalStatus", "DeploymentArtifact", "DeploymentReport", "RiskLimits"}:
        module = import_module("core.condor.models")
        return getattr(module, name)
    raise AttributeError(f"module 'core.condor' has no attribute {name!r}")
