"""Auditable differential and linear trail security evaluation."""

from __future__ import annotations

from typing import Any, Mapping


def evaluate_security(
    candidate: object, context: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Load the Team A adapter lazily so ``python -m ...cli`` stays clean."""

    from .team_a_adapter import evaluate_security as implementation

    return implementation(candidate, context)

__all__ = ["evaluate_security"]
