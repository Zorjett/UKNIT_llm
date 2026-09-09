"""Team B security evaluator placeholder.

Replace this file with the Team B implementation while preserving
``PLUGIN_API_VERSION = "1.0"`` and
``evaluate_security(candidate, context)``. Both arguments and the returned
value must be JSON-compatible dictionaries; see ``team_plugins/README.md``.
"""

from __future__ import annotations

from typing import Any

from .plugin_contracts import (
    PLUGIN_API_VERSION,
    candidate_to_dict,
    unavailable_result,
)


PLUGIN_NAME = "team-b-security-placeholder"


def evaluate_security(candidate: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return an unavailable result until Team B supplies analysis.

    The placeholder deliberately emits no security measurement. The framework
    consequently assigns neutral fitness and continues the search/logging flow.
    """

    del context
    payload = candidate_to_dict(candidate)
    return unavailable_result(
        "security",
        payload["candidate_id"],
        "Team B security evaluator has not been installed; neutral weights are in use.",
        PLUGIN_NAME,
    )


evaluate = evaluate_security


__all__ = ["PLUGIN_NAME", "PLUGIN_API_VERSION", "evaluate_security", "evaluate"]
