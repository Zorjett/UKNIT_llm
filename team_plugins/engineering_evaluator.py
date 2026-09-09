"""Team C validation and OpenLane-backed performance evaluator."""

from __future__ import annotations

from typing import Any

from .plugin_contracts import (
    PLUGIN_API_VERSION,
    SCHEMA_VERSION,
    ContractError,
    candidate_to_dict,
    normalize_plugin_result,
    validate_candidate_payload,
)
from .openlane_performance import evaluate_performance as _evaluate_openlane_performance


PLUGIN_NAME = "team-c-engineering-openlane"


def validate_candidate(candidate: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Perform shared structural validation while Team C checks are absent.

    This is intentionally narrower than an engineering validation. Its warning
    lets callers distinguish basic contract validity from a full Team C result.
    """

    del context
    try:
        payload = candidate_to_dict(candidate, validate=False)
    except (ContractError, TypeError, ValueError) as exc:
        return normalize_plugin_result(
            {
                "schema_version": SCHEMA_VERSION,
                "plugin_api_version": PLUGIN_API_VERSION,
                "plugin_name": PLUGIN_NAME,
                "candidate_id": "unknown",
                "status": "error",
                "valid": False,
                "errors": [{"code": "candidate_not_serializable", "message": str(exc), "path": "$"}],
                "warnings": [],
                "artifacts": {},
            },
            "validation",
        )

    issues = validate_candidate_payload(payload)
    return normalize_plugin_result(
        {
            "schema_version": SCHEMA_VERSION,
            "plugin_api_version": PLUGIN_API_VERSION,
            "plugin_name": PLUGIN_NAME,
            "candidate_id": payload["candidate_id"],
            "status": "ok" if not issues else "error",
            "valid": not issues,
            "errors": issues,
            "warnings": ["Only the shared structural checks ran; Team C checks are not installed."],
            "artifacts": {},
        },
        "validation",
        candidate_id=payload["candidate_id"],
    )


def evaluate_performance(candidate: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run OpenLane and return the measured critical-path delay in ns.

    OpenLane failures deliberately propagate. The framework must stop instead
    of assigning a placeholder latency or silently accepting an unevaluated
    candidate.
    """
    payload = candidate_to_dict(candidate)
    result = _evaluate_openlane_performance(payload, context)
    result["plugin_name"] = PLUGIN_NAME
    result["candidate_id"] = payload["candidate_id"]
    return normalize_plugin_result(result, "performance", candidate_id=payload["candidate_id"])


validate = validate_candidate
evaluate = evaluate_performance


__all__ = [
    "PLUGIN_NAME",
    "PLUGIN_API_VERSION",
    "validate_candidate",
    "validate",
    "evaluate_performance",
    "evaluate",
]
