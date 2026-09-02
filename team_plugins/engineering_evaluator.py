"""Team C validation/performance evaluator placeholder.

Replace this file with the Team C implementation while preserving
``PLUGIN_API_VERSION = "1.0"``, ``validate_candidate(candidate, context)``,
and ``evaluate_performance(candidate, context)``. Both arguments and returned
values must be JSON-compatible dictionaries; see ``team_plugins/README.md``.
"""

from __future__ import annotations

from typing import Any

from .plugin_contracts import (
    PLUGIN_API_VERSION,
    SCHEMA_VERSION,
    ContractError,
    candidate_to_dict,
    normalize_plugin_result,
    unavailable_result,
    validate_candidate_payload,
)


PLUGIN_NAME = "team-c-engineering-placeholder"


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
    """Return an unavailable result until Team C supplies a real measurement.

    The placeholder metric is solely schema-compatible; it must not be treated
    as a latency result because the enclosing status is ``unavailable``.
    """

    del context
    payload = candidate_to_dict(candidate)
    return unavailable_result(
        "performance",
        payload["candidate_id"],
        "Team C performance evaluator has not been installed; placeholder latency is in use.",
        PLUGIN_NAME,
    )


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
