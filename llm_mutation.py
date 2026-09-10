"""DeepSeek-guided structural decisions for the uKNIT genetic search.

The language model returns only a target, a transformation matrix, or a
crossover starting component.  The framework materializes components locally,
validates them, and retries invalid decisions a bounded number of times.

The public entry point intended for the search loop is ``mutate_generation``.
Transport failures remain structured no-op reports; exhausting component
validation attempts raises ``ComponentValidationError`` and stops the search.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import json
from numbers import Integral
import os
import re
import time
import uuid
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, NoReturn, Optional, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request

from cipher.linear_functions import linear_functions as _linear_functions
from cipher.sbox_functions import sbox_functions as _sbox_functions
import cipher.components as _components
import config as _config


try:
    from team_plugins.plugin_contracts import (
        SCHEMA_VERSION as CANDIDATE_SCHEMA_VERSION,
        candidate_fingerprint,
        candidate_to_dict,
        canonical_json,
        to_builtin,
        validate_candidate_payload,
    )

    _PLUGIN_CONTRACTS_AVAILABLE = True
except (ImportError, AttributeError):
    # The contracts are a team integration point and may not yet be installed when
    # this module is imported in isolation.  Runtime behavior remains a safe no-op
    # or uses the local structural validator until the shared module is present.
    CANDIDATE_SCHEMA_VERSION = "1.0"
    _PLUGIN_CONTRACTS_AVAILABLE = False

    def to_builtin(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): to_builtin(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [to_builtin(item) for item in value]
        if hasattr(value, "tolist"):
            return to_builtin(value.tolist())
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def canonical_json(value: Any) -> str:
        return json.dumps(
            to_builtin(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )

    def candidate_to_dict(
        candidate: Any,
        candidate_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        validate: bool = True,
    ) -> dict[str, Any]:
        payload = _local_candidate_payload(candidate, candidate_id, metadata)
        if validate:
            issues = _local_structure_issues(candidate)
            if issues:
                raise ValueError(issues[0]["message"])
        return payload

    def candidate_fingerprint(candidate: Any) -> str:
        # This fallback is only used before the shared contract module exists.
        import hashlib

        return hashlib.sha256(canonical_json(candidate).encode("utf-8")).hexdigest()

    def validate_candidate_payload(
        candidate: Any,
        require_invertible: bool = True,
        check_fingerprint: bool = True,
    ) -> list[dict[str, Any]]:
        del require_invertible, check_fingerprint
        return []


MUTATION_SCHEMA_VERSION = "1.0"
DEFAULT_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = ""
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_MAX_TOKENS = 2048
DEFAULT_MAX_ACTIONS = 16
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000 # API响应最大允许1MB
DEFAULT_MAX_COMPONENT_GENERATION_ATTEMPTS = 10

_ROOT_KEYS = {
    "schema_version",
    "request_id",
    "generation",
    "actions",
}
_ACTION_KEYS = {
    "action_type",
    "target_candidate_id",
    "base_fingerprint",
    "parent_candidate_ids",
    "round_index",
    "component",
    "sbox_index",
    "bit_permutation",
    "transformation_matrix",
    "row_swap",
    "column_swap",
    "start_component",
}

_SBOX_BIT_PERMUTATIONS = [
    [0, 1, 3, 2], [0, 2, 1, 3], [0, 2, 3, 1], [0, 3, 1, 2],
    [0, 3, 2, 1], [1, 0, 2, 3], [1, 0, 3, 2], [1, 2, 0, 3],
    [1, 2, 3, 0], [1, 3, 0, 2], [1, 3, 2, 0], [2, 0, 1, 3],
    [2, 0, 3, 1], [2, 1, 0, 3], [2, 1, 3, 0], [2, 3, 0, 1],
    [2, 3, 1, 0], [3, 0, 1, 2], [3, 0, 2, 1], [3, 1, 0, 2],
    [3, 1, 2, 0], [3, 2, 0, 1], [3, 2, 1, 0],
]


def _is_binary_matrix(value: Any) -> bool:
    try:
        rows = list(value)
        return (
            len(rows) == 64
            and all(isinstance(row, (list, tuple)) and len(row) == 64 for row in rows)
            and all(value in (0, 1) and not isinstance(value, bool) for row in rows for value in row)
        )
    except (TypeError, ValueError):
        return False


def _is_valid_mantis_sbox_payload(value: Any, input_permutation: Any, output_permutation: Any) -> tuple[bool, list[int] | None]:
    """Validate B/D and return the concrete ``D o S_MANTIS o B`` table."""
    if not _sbox_functions.is_bit_permutation_matrix(input_permutation):
        return False, None
    if not _sbox_functions.is_bit_permutation_matrix(output_permutation):
        return False, None
    try:
        constructed = list(
            _sbox_functions.construct_mantis_sbox(input_permutation, output_permutation)
        )
    except (TypeError, ValueError):
        return False, None
    if value is not None:
        try:
            supplied = list(value)
        except TypeError:
            return False, None
        if supplied != constructed:
            return False, None
    return sorted(constructed) == list(range(16)), constructed
class MutationSchemaError(ValueError):
    """The model response does not conform to the mutation-plan schema."""


class ComponentValidationError(RuntimeError):
    """Raised when the model cannot produce valid cipher components in time."""

    def __init__(
        self,
        message: str,
        *,
        report: Optional[Mapping[str, Any]] = None,
        issues: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.report = dict(report or {})
        self.issues = to_builtin(list(issues or []))


@dataclass(frozen=True)
class DeepSeekSettings:
    """Resolved DeepSeek settings without exposing the API key in reports."""

    enabled: bool = True
    api_key: str = ""
    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_ENDPOINT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    temperature: float = 0.1
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_actions: int = DEFAULT_MAX_ACTIONS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_component_generation_attempts: int = DEFAULT_MAX_COMPONENT_GENERATION_ATTEMPTS

    @classmethod
    def from_sources(
        cls, overrides: Optional[Mapping[str, Any] | object] = None
    ) -> "DeepSeekSettings":
        """Load defaults, ``deepseek_config.py``, environment, then overrides."""

        values: dict[str, Any] = {}
        try:
            module = importlib.import_module("deepseek_config")
        except ImportError:
            module = None

        if module is not None:
            for mapping_name in ("DEEPSEEK_CONFIG", "CONFIG", "SETTINGS"):
                mapping = getattr(module, mapping_name, None)
                if isinstance(mapping, Mapping):
                    values.update(mapping)
            module_aliases = {
                "enabled": ("DEEPSEEK_ENABLED", "ENABLED"),
                "api_key": ("DEEPSEEK_API_KEY", "API_KEY"),
                "model": ("DEEPSEEK_MODEL", "MODEL"),
                "endpoint": ("DEEPSEEK_ENDPOINT", "ENDPOINT", "API_URL"),
                "base_url": ("DEEPSEEK_BASE_URL", "BASE_URL"),
                "timeout_seconds": ("DEEPSEEK_TIMEOUT_SECONDS", "TIMEOUT_SECONDS", "TIMEOUT"),
                "max_retries": ("DEEPSEEK_MAX_RETRIES", "MAX_RETRIES", "RETRIES"),
                "temperature": ("DEEPSEEK_TEMPERATURE", "TEMPERATURE"),
                "max_tokens": ("DEEPSEEK_MAX_TOKENS", "MAX_TOKENS"),
                "max_actions": ("DEEPSEEK_MAX_ACTIONS", "MAX_ACTIONS"),
                "max_response_bytes": (
                    "DEEPSEEK_MAX_RESPONSE_BYTES",
                    "MAX_RESPONSE_BYTES",
                ),
                "max_component_generation_attempts": (
                    "DEEPSEEK_MAX_COMPONENT_GENERATION_ATTEMPTS",
                    "MAX_COMPONENT_GENERATION_ATTEMPTS",
                ),
            }
            for destination, aliases in module_aliases.items():
                for alias in aliases:
                    if hasattr(module, alias):
                        values[destination] = getattr(module, alias)
                        break

        environment = {
            "api_key": os.getenv("DEEPSEEK_API_KEY"),
            "model": os.getenv("DEEPSEEK_MODEL"),
            "endpoint": os.getenv("DEEPSEEK_ENDPOINT"),
            "base_url": os.getenv("DEEPSEEK_BASE_URL"),
            "timeout_seconds": os.getenv("DEEPSEEK_TIMEOUT_SECONDS"),
            "enabled": os.getenv("DEEPSEEK_ENABLED"),
            "max_component_generation_attempts": os.getenv(
                "DEEPSEEK_MAX_COMPONENT_GENERATION_ATTEMPTS"
            ),
        }
        values.update({key: value for key, value in environment.items() if value not in (None, "")})

        if overrides is not None:
            if isinstance(overrides, Mapping):
                values.update(overrides)
            else:
                for field_name in cls.__dataclass_fields__:
                    if hasattr(overrides, field_name):
                        values[field_name] = getattr(overrides, field_name)

        endpoint = values.get("endpoint")
        if not endpoint and values.get("base_url"):
            endpoint = str(values["base_url"]).rstrip("/") + "/chat/completions"

        return cls(
            enabled=_as_bool(values.get("enabled", True)),
            api_key=str(values.get("api_key", "") or "").strip(),
            model=str(values.get("model", DEFAULT_MODEL) or DEFAULT_MODEL).strip(),
            endpoint=str(endpoint or DEFAULT_ENDPOINT).strip(),
            timeout_seconds=_bounded_float(
                values.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS), 1.0, 600.0
            ),
            max_retries=_bounded_int(values.get("max_retries", DEFAULT_MAX_RETRIES), 0, 5),
            temperature=_bounded_float(values.get("temperature", 0.1), 0.0, 2.0),
            max_tokens=_bounded_int(values.get("max_tokens", DEFAULT_MAX_TOKENS), 1, 65536),
            max_actions=_bounded_int(values.get("max_actions", DEFAULT_MAX_ACTIONS), 0, 4096),
            max_response_bytes=_bounded_int(
                values.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES),
                1024,
                20_000_000,
            ),
            # The safety requirement is deliberately capped at three complete
            # model-generation attempts, even when a local override is larger.
            max_component_generation_attempts=_bounded_int(
                values.get(
                    "max_component_generation_attempts",
                    DEFAULT_MAX_COMPONENT_GENERATION_ATTEMPTS,
                ),
                1,
                3,
            ),
        )


class DeepSeekMutationAdvisor:
    """Plan and apply one batched LLM mutation request per generation."""

    def __init__(
        self,
        settings: Optional[DeepSeekSettings | Mapping[str, Any] | object] = None,
        urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        if isinstance(settings, DeepSeekSettings):
            self.settings = settings
        else:
            self.settings = DeepSeekSettings.from_sources(settings)
        self._urlopen = urlopen or urllib_request.urlopen
        self._last_request_attempts = 0

    def mutate_generation(
        self,
        members: Sequence[Any],
        generation_context: Optional[Mapping[str, Any]] = None,
        engineering_validator: Optional[Any] = None,
    ) -> tuple[list[Any], dict[str, Any]]:
        """Return deep-copied members and a directly serializable mutation report."""

        request_id = str(uuid.uuid4())
        report = _new_report(request_id, self.settings, len(members))
        originals = [copy.deepcopy(member) for member in members]

        if not self.settings.enabled:
            return originals, _fallback(report, "disabled")
        if not self.settings.api_key:
            return originals, _fallback(report, "missing_api_key")
        if not self.settings.model:
            return originals, _fallback(report, "missing_model")
        if not members:
            report["status"] = "no_changes"
            report["fallback_reason"] = "empty_generation"
            report["finished_at"] = _utc_now()
            return originals, report

        candidates = [
            _candidate_prompt_payload(member, index) for index, member in enumerate(members)
        ]
        base_prompt_payload = {
            "schema_version": MUTATION_SCHEMA_VERSION,
            "request_id": request_id,
            "generation_context": _compact_generation_context(generation_context or {}),
            "candidates": candidates,
            "candidate_scores": {
                item["candidate_id"]: item.get("score", 0.0) for item in candidates
            },
        }
        required_children = [
            str(item)
            for item in (
                (generation_context or {}).get("required_action_children")
                or (generation_context or {}).get("duplicate_children", [])
            )
            if item
        ]
        report["required_action_candidate_ids"] = required_children
        if required_children:
            # This request creates the complete next population. Tell the model
            # which child slots must receive an action and enforce that
            # requirement below instead of silently carrying copies forward.
            base_prompt_payload["action_requirements"] = {
                "required_for_candidate_ids": required_children,
                "exactly_one_action_per_candidate": True,
                "reason": "every next-generation child slot requires a mutation or crossover action",
                "allowed_sbox_bit_permutations": _SBOX_BIT_PERMUTATIONS,
            }
            history_by_id = (generation_context or {}).get("candidate_history", {})
            if not isinstance(history_by_id, Mapping):
                history_by_id = {}
            required_specs = []
            for item in candidates:
                if item.get("candidate_id") not in set(required_children):
                    continue
                candidate_id = item["candidate_id"]
                history = history_by_id.get(candidate_id, {})
                if not isinstance(history, Mapping):
                    history = {}
                spec = {
                    "target_candidate_id": item["candidate_id"],
                    "base_fingerprint": item["fingerprint"],
                    "allowed_round_indices": list(range(int(item.get("num_rounds", 0) or 0))),
                    "allowed_sbox_indices": list(range(16)),
                    "allowed_mutation_components": ["sbox_B", "sbox_D", "linear"],
                }
                forbidden = history.get("forbidden_fingerprints", [])
                recent = history.get("recent_action_specs", [])
                if isinstance(forbidden, Sequence) and not isinstance(forbidden, (str, bytes)):
                    spec["forbidden_result_fingerprints"] = [str(value) for value in forbidden if value]
                if isinstance(recent, Sequence) and not isinstance(recent, (str, bytes)):
                    spec["recent_action_specs"] = to_builtin(list(recent))
                required_specs.append(spec)
            base_prompt_payload["required_action_specs"] = required_specs

        # A model can return a syntactically valid plan which still produces an
        # illegal component (for example a malformed copied round).  Validate
        # the complete result after every model generation and ask the model to
        # regenerate with the concrete issues from the previous attempt.
        validation_history: list[dict[str, Any]] = []
        validation_feedback: list[dict[str, Any]] = []
        max_attempts = _max_component_generation_attempts(self.settings)
        total_request_attempts = 0
        for generation_attempt in range(1, max_attempts + 1):
            prompt_payload = dict(base_prompt_payload)
            prompt_payload["generation_attempt"] = generation_attempt
            if validation_feedback:
                prompt_payload["validation_feedback"] = to_builtin(validation_feedback)

            attempt_request_attempts = 0
            try:
                print(
                    "[llm] requesting actions: generation=%s attempt=%d/%d candidates=%d model=%s"
                    % (
                        (generation_context or {}).get("generation"),
                        generation_attempt,
                        max_attempts,
                        len(members),
                        self.settings.model,
                    ),
                    flush=True,
                )
                response = self._request_plan(prompt_payload)
                attempt_request_attempts = max(1, self._last_request_attempts)
                total_request_attempts += attempt_request_attempts
                actions, schema_rejections, rationale = _parse_action_plan(
                    response,
                    request_id=request_id,
                    candidate_bindings=_candidate_bindings(members),
                    expected_generation=(generation_context or {}).get("generation"),
                    max_actions=self.settings.max_actions,
                )
                print(
                    "[llm] response received: actions=%d schema_rejections=%d"
                    % (len(actions), len(schema_rejections)),
                    flush=True,
                )
                for rejection in schema_rejections:
                    print(
                        "[llm] action rejected by schema: action=%s reason=%s"
                        % (
                            rejection.get("action_index"),
                            rejection.get("error_detail", "unknown schema error"),
                        ),
                        flush=True,
                    )
            except MutationSchemaError as exc:
                if attempt_request_attempts == 0:
                    total_request_attempts += max(1, self._last_request_attempts)
                issue = {
                    "code": "model_response_schema_invalid",
                    "message": _safe_error_text(exc),
                    "generation_attempt": generation_attempt,
                }
                validation_feedback = [issue]
                validation_history.append(
                    {
                        "generation_attempt": generation_attempt,
                        "status": "invalid",
                        "issues": [issue],
                    }
                )
                if generation_attempt < max_attempts:
                    continue
                return _raise_component_validation_error(
                    originals,
                    report,
                    validation_history,
                    validation_feedback,
                    total_request_attempts,
                    exc,
                )
            except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, OSError) as exc:
                if attempt_request_attempts == 0:
                    total_request_attempts += max(1, self._last_request_attempts)
                report["generation_attempts"] = len(validation_history)
                report["validation_retries"] = max(0, len(validation_history) - 1)
                report["validation_history"] = validation_history
                report["validation_issues"] = validation_feedback
                report["error_detail"] = _safe_error_text(exc)
                report["request_attempts"] = total_request_attempts
                return originals, _fallback(report, "api_error")
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                if attempt_request_attempts == 0:
                    total_request_attempts += max(1, self._last_request_attempts)
                report["generation_attempts"] = len(validation_history)
                report["validation_retries"] = max(0, len(validation_history) - 1)
                report["validation_history"] = validation_history
                report["validation_issues"] = validation_feedback
                report["error_detail"] = _safe_error_text(exc)
                report["request_attempts"] = total_request_attempts
                return originals, _fallback(report, "response_error")
            except Exception as exc:  # The search loop must never fail because the advisor did.
                if attempt_request_attempts == 0:
                    total_request_attempts += max(1, self._last_request_attempts)
                report["generation_attempts"] = len(validation_history)
                report["validation_retries"] = max(0, len(validation_history) - 1)
                report["validation_history"] = validation_history
                report["validation_issues"] = validation_feedback
                report["error_detail"] = _safe_error_text(exc)
                report["request_attempts"] = total_request_attempts
                return originals, _fallback(report, "advisor_error")

            required_ids = set(required_children)
            action_targets = {
                str(action.get("target_candidate_id"))
                for action in actions
                if isinstance(action, Mapping)
            }
            missing_required = sorted(required_ids - action_targets)
            duplicate_targets = sorted(
                candidate_id
                for candidate_id in required_ids
                if sum(
                    1
                    for action in actions
                    if isinstance(action, Mapping)
                    and str(action.get("target_candidate_id")) == candidate_id
                ) != 1
            )
            if missing_required or duplicate_targets:
                issue = {
                    "code": "required_action_missing_or_duplicate",
                    "message": (
                        "LLM must return exactly one action for every required candidate; "
                        "missing=" + ",".join(missing_required)
                        + "; duplicate_or_wrong_count=" + ",".join(duplicate_targets)
                    ),
                    "candidate_ids": missing_required,
                    "duplicate_or_wrong_count": duplicate_targets,
                    "generation_attempt": generation_attempt,
                }
                schema_issues = [
                    {
                        "code": "model_action_schema_invalid",
                        "message": rejection.get("error_detail") or "model action failed schema validation",
                        "action_index": rejection.get("action_index"),
                        "rejected_action": rejection.get("action"),
                        "generation_attempt": generation_attempt,
                    }
                    for rejection in schema_rejections
                ]
                # Put the concrete field error first. Previously the model only
                # saw "missing action" and repeated the same malformed action.
                validation_feedback = schema_issues + [issue]
                validation_history.append(
                    {
                        "generation_attempt": generation_attempt,
                        "status": "invalid",
                        "accepted_count": 0,
                        "rejected_count": len(schema_rejections),
                        "issues": to_builtin(validation_feedback),
                        "response_generation": (
                            response.get("generation")
                            if isinstance(response, Mapping)
                            else None
                        ),
                        "actions": to_builtin(actions),
                        "change_records": to_builtin(schema_rejections),
                        "rationale": rationale,
                    }
                )
                if generation_attempt < max_attempts:
                    continue
                return _raise_component_validation_error(
                    originals,
                    report,
                    validation_history,
                    validation_feedback,
                    total_request_attempts,
                    None,
                )

            history_by_id = (generation_context or {}).get("candidate_history", {})
            if not isinstance(history_by_id, Mapping):
                history_by_id = {}
            repeated_action_ids = []
            for action in actions:
                candidate_id = str(action.get("target_candidate_id"))
                history = history_by_id.get(candidate_id, {})
                recent_specs = history.get("recent_action_specs", []) if isinstance(history, Mapping) else []
                recent_signatures = {
                    _action_signature(spec)
                    for spec in recent_specs
                    if isinstance(spec, Mapping)
                }
                if _action_signature(action) in recent_signatures:
                    repeated_action_ids.append(candidate_id)
            if repeated_action_ids:
                issue = {
                    "code": "repeated_llm_action",
                    "message": (
                        "LLM repeated a recent mutation/crossover action for: "
                        + ", ".join(sorted(set(repeated_action_ids)))
                        + ". Change the component, round, S-box index, transformation, or crossover point."
                    ),
                    "candidate_ids": sorted(set(repeated_action_ids)),
                    "generation_attempt": generation_attempt,
                }
                validation_feedback = [issue]
                validation_history.append({
                    "generation_attempt": generation_attempt,
                    "status": "invalid",
                    "accepted_count": 0,
                    "rejected_count": len(actions) + len(schema_rejections),
                    "issues": [issue],
                    "response_generation": response.get("generation") if isinstance(response, Mapping) else None,
                    "actions": to_builtin(actions),
                    "change_records": to_builtin(schema_rejections),
                    "rationale": rationale,
                })
                if generation_attempt < max_attempts:
                    continue
                return _raise_component_validation_error(
                    originals, report, validation_history, validation_feedback,
                    total_request_attempts, None,
                )

            mutated, application_records = apply_action_plan(
                members,
                actions,
                engineering_validator=engineering_validator,
                generation_context=generation_context,
            )
            unchanged_required = []
            historical_duplicates = []
            for candidate_index, member in enumerate(mutated):
                candidate_id = _candidate_id(member, candidate_index)
                if candidate_id not in required_ids:
                    continue
                result_fingerprint = member.candidate_fingerprint()
                if result_fingerprint == originals[candidate_index].candidate_fingerprint():
                    unchanged_required.append(candidate_id)
                history = history_by_id.get(candidate_id, {})
                forbidden = history.get("forbidden_fingerprints", []) if isinstance(history, Mapping) else []
                if result_fingerprint in set(str(value) for value in forbidden if value):
                    if candidate_id not in unchanged_required:
                        historical_duplicates.append(candidate_id)
            novelty_issues = []
            if unchanged_required:
                novelty_issues.append({
                    "code": "required_duplicate_action_no_effect",
                    "message": "LLM action did not change candidate(s): " + ", ".join(sorted(unchanged_required)),
                    "candidate_ids": sorted(unchanged_required),
                    "generation_attempt": generation_attempt,
                })
            if historical_duplicates:
                novelty_issues.append({
                    "code": "historical_fingerprint_recreated",
                    "message": (
                        "LLM action recreated a previously visited cipher for: "
                        + ", ".join(sorted(historical_duplicates))
                        + ". Choose a different structural action."
                    ),
                    "candidate_ids": sorted(historical_duplicates),
                    "generation_attempt": generation_attempt,
                })
            if novelty_issues:
                validation_feedback = novelty_issues
                validation_history.append(
                    {
                        "generation_attempt": generation_attempt,
                        "status": "invalid",
                        "accepted_count": sum(
                            record.get("status") == "accepted"
                            for record in application_records
                        ),
                        "rejected_count": len(schema_rejections) + sum(
                            record.get("status") == "rejected"
                            for record in application_records
                        ),
                        "issues": to_builtin(novelty_issues),
                        "response_generation": (
                            response.get("generation")
                            if isinstance(response, Mapping)
                            else None
                        ),
                        "actions": to_builtin(actions),
                        "change_records": to_builtin(
                            list(schema_rejections) + list(application_records)
                        ),
                        "rationale": rationale,
                    }
                )
                if generation_attempt < max_attempts:
                    continue
                return _raise_component_validation_error(
                    originals,
                    report,
                    validation_history,
                    validation_feedback,
                    total_request_attempts,
                    None,
                )
            structural_issues = _generation_component_issues(
                mutated,
                schema_rejections=schema_rejections,
                application_records=application_records,
                generation_attempt=generation_attempt,
            )
            accepted = sum(record.get("status") == "accepted" for record in application_records)
            rejected = len(schema_rejections) + sum(
                record.get("status") == "rejected" for record in application_records
            )
            validation_history.append(
                {
                    "generation_attempt": generation_attempt,
                    "status": "invalid" if structural_issues else "valid",
                    "accepted_count": accepted,
                    "rejected_count": rejected,
                    "issues": to_builtin(structural_issues),
                    "response_generation": (
                        response.get("generation") if isinstance(response, Mapping) else None
                    ),
                    "actions": to_builtin(actions),
                    "change_records": to_builtin(
                        list(schema_rejections) + list(application_records)
                    ),
                    "rationale": rationale,
                }
            )

            if structural_issues:
                validation_feedback = structural_issues
                if generation_attempt < max_attempts:
                    continue
                return _raise_component_validation_error(
                    originals,
                    report,
                    validation_history,
                    validation_feedback,
                    total_request_attempts,
                    None,
                )

            report["response_generation"] = (
                response.get("generation") if isinstance(response, Mapping) else None
            )
            report["request_attempts"] = total_request_attempts
            report["prompt_chars"] = getattr(self, "_last_prompt_chars", 0)
            report["prompt_token_estimate"] = getattr(self, "_last_prompt_token_estimate", 0)
            report["response_chars"] = getattr(self, "_last_response_chars", 0)
            report["generation_attempts"] = generation_attempt
            report["validation_retries"] = generation_attempt - 1
            report["validation_history"] = validation_history
            report["rationale"] = rationale
            report["actions"] = to_builtin(actions)
            report["change_records"].extend(schema_rejections)
            report["change_records"].extend(application_records)
            report["accepted_count"] = accepted
            report["rejected_count"] = rejected
            report["status"] = "applied" if accepted else "no_changes"
            report["fallback_reason"] = None
            report["finished_at"] = _utc_now()
            print(
                "[llm] generation decision applied: accepted=%d rejected=%d attempts=%d"
                % (accepted, rejected, generation_attempt),
                flush=True,
            )
            return mutated, report

        # The loop always either returns or raises, but keep a defensive guard
        # in case a future change alters the attempt bounds.
        return _raise_component_validation_error(
            originals,
            report,
            validation_history,
            validation_feedback,
            total_request_attempts,
            None,
        )

    def propose(
        self,
        members: Sequence[Any],
        generation_context: Optional[Mapping[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Request and normalize a plan without applying it; useful for diagnostics."""

        actual_request_id = request_id or str(uuid.uuid4())
        candidates = [
            _candidate_prompt_payload(member, index) for index, member in enumerate(members)
        ]
        response = self._request_plan(
            {
                "schema_version": MUTATION_SCHEMA_VERSION,
                "request_id": actual_request_id,
                "generation_context": _compact_generation_context(generation_context or {}),
                "candidates": candidates,
            }
        )
        actions, rejections, rationale = _parse_action_plan(
            response,
            request_id=actual_request_id,
            candidate_bindings=_candidate_bindings(members),
            expected_generation=(generation_context or {}).get("generation"),
            max_actions=self.settings.max_actions,
        )
        return {
            "schema_version": MUTATION_SCHEMA_VERSION,
            "request_id": actual_request_id,
            "generation": response.get("generation") if isinstance(response, Mapping) else None,
            "actions": to_builtin(actions),
            "rejections": rejections,
            "rationale": rationale,
        }

    def _request_plan(self, prompt_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.settings.api_key:
            raise ValueError("DeepSeek API key is not configured")
        if not self.settings.model:
            raise ValueError("DeepSeek model is not configured")

        body = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": _system_prompt(self.settings)},
                {"role": "user", "content": _canonical_json(prompt_payload)},
            ],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self._last_prompt_chars = len(body["messages"][0]["content"]) + len(
            body["messages"][1]["content"]
        )
        self._last_prompt_token_estimate = max(1, (self._last_prompt_chars + 3) // 4)
        request = urllib_request.Request(
            self.settings.endpoint,
            data=encoded,
            headers={
                "Authorization": "Bearer " + self.settings.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        attempts = max(1, self.settings.max_retries + 1)
        self._last_request_attempts = 0
        response = None
        for attempt in range(attempts):
            self._last_request_attempts = attempt + 1
            try:
                response = self._urlopen(request, timeout=self.settings.timeout_seconds)
                break
            except Exception as exc:
                # Configuration/client errors should fail immediately. Only
                # transient transport failures and HTTP 429/5xx responses are
                # eligible for retry; retrying a 4xx would hide a bad request or
                # credential and needlessly multiply API calls.
                if not _is_retryable_request_error(exc) or attempt >= attempts - 1:
                    raise
                # A short bounded backoff prevents a transient 429/5xx from
                # immediately consuming the whole retry budget while keeping
                # tests and offline bring-up fast.
                time.sleep(min(0.25 * (2**attempt), 1.0))
        if response is None:  # Defensive guard for unusual urlopen shims.
            raise OSError("DeepSeek request returned no response")
        try:
            raw = response.read(self.settings.max_response_bytes + 1)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if len(raw) > self.settings.max_response_bytes:
            raise MutationSchemaError("DeepSeek response exceeds configured size limit")
        self._last_response_chars = len(raw.decode("utf-8", errors="replace"))

        envelope = json.loads(raw.decode("utf-8"))
        if not isinstance(envelope, Mapping):
            raise MutationSchemaError("DeepSeek response envelope must be an object")
        choices = envelope.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise MutationSchemaError("DeepSeek response must contain exactly one choice")
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            raise MutationSchemaError("DeepSeek response choice has no JSON content")
        return _decode_json_object(content)


def mutate_generation(
    members: Sequence[Any],
    generation_context: Optional[Mapping[str, Any]] = None,
    config: Optional[DeepSeekSettings | Mapping[str, Any] | object] = None,
    engineering_validator: Optional[Any] = None,
    urlopen: Optional[Callable[..., Any]] = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Convenience entry point used by the genetic search once per generation."""

    advisor = DeepSeekMutationAdvisor(settings=config, urlopen=urlopen)
    return advisor.mutate_generation(
        members,
        generation_context=generation_context,
        engineering_validator=engineering_validator,
    )


def _parse_action_plan(
    payload: Mapping[str, Any],
    *,
    request_id: str,
    candidate_bindings: Optional[Mapping[str, Any] | Sequence[Mapping[str, Any]]] = None,
    expected_generation: Any = None,
    max_actions: int = DEFAULT_MAX_ACTIONS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    """Parse the decision-only protocol exposed to the language model."""
    if not isinstance(payload, Mapping):
        raise MutationSchemaError("action plan must be a JSON object")
    allowed_root = {"schema_version", "request_id", "generation", "actions"}
    unknown = set(payload) - allowed_root
    if unknown:
        raise MutationSchemaError("unknown action-plan fields: " + ", ".join(sorted(map(str, unknown))))
    if payload.get("schema_version") != MUTATION_SCHEMA_VERSION:
        raise MutationSchemaError("unsupported action-plan schema version")
    if payload.get("request_id") != request_id:
        raise MutationSchemaError("action-plan request_id does not match the request")
    generation = payload.get("generation")
    if generation is not None and not _is_int(generation):
        raise MutationSchemaError("generation must be an integer or null")
    # Generation is informational metadata. A model may describe the target as
    # the next generation, so do not reject an otherwise valid action plan only
    # because this echo field differs from the request.
    actions = payload.get("actions")
    if not isinstance(actions, list) or len(actions) > int(max_actions):
        raise MutationSchemaError("actions must be an array within the configured limit")
    bindings = _binding_entries(candidate_bindings)
    bound_ids = {str(item.get("candidate_id")): int(item.get("candidate_index")) for item in bindings}
    parsed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(actions):
        try:
            if not isinstance(raw, Mapping):
                raise MutationSchemaError("action must be an object")
            if set(raw) - _ACTION_KEYS:
                raise MutationSchemaError("unknown action fields")
            action_type = raw.get("action_type")
            target_id = raw.get("target_candidate_id")
            if action_type not in {"mutation", "crossover"}:
                raise MutationSchemaError("action_type must be mutation or crossover")
            if not isinstance(target_id, str) or target_id not in bound_ids:
                raise MutationSchemaError("target_candidate_id is not bound to a candidate")
            result = {
                "action_type": action_type,
                "target_candidate_id": target_id,
                "candidate_index": bound_ids[target_id],
                "base_fingerprint": raw.get("base_fingerprint"),
            }
            if result["base_fingerprint"] is not None and not isinstance(result["base_fingerprint"], str):
                raise MutationSchemaError("base_fingerprint must be a string or null")
            if action_type == "mutation":
                round_index = raw.get("round_index")
                component = raw.get("component")
                matrix = raw.get("transformation_matrix")
                if not _is_int(round_index) or int(round_index) < 0:
                    raise MutationSchemaError("round_index must be a non-negative integer")
                if component not in {"sbox_B", "sbox_D", "linear"}:
                    raise MutationSchemaError("component must be sbox_B, sbox_D, or linear")
                if component.startswith("sbox_"):
                    sbox_index = raw.get("sbox_index")
                    if not _is_int(sbox_index) or not 0 <= int(sbox_index) < 16:
                        raise MutationSchemaError("sbox_index must be in 0..15")
                    bit_permutation = raw.get("bit_permutation")
                    if matrix is not None:
                        raise MutationSchemaError(
                            "S-box transformation_matrix is no longer supported; use bit_permutation"
                        )
                    if (
                        not isinstance(bit_permutation, list)
                        or len(bit_permutation) != 4
                        or any(not _is_int(value) for value in bit_permutation)
                        or sorted(int(value) for value in bit_permutation) != [0, 1, 2, 3]
                        or [int(value) for value in bit_permutation] == [0, 1, 2, 3]
                    ):
                        raise MutationSchemaError(
                            "S-box bit_permutation must be a non-identity permutation such as [1,0,2,3]"
                        )
                    normalized_permutation = [int(value) for value in bit_permutation]
                    matrix = np.eye(4, dtype=int)[:, normalized_permutation]
                    result["bit_permutation"] = normalized_permutation
                    result["sbox_index"] = int(sbox_index)
                    result["transformation_matrix"] = to_builtin(matrix)
                else:
                    row_swap = raw.get("row_swap")
                    column_swap = raw.get("column_swap")
                    if matrix is not None:
                        if not _linear_functions.is_valid_permutation_matrix(matrix, 64):
                            raise MutationSchemaError(
                                "linear transformation_matrix must be a 64x64 permutation matrix"
                            )
                        result["transformation_matrix"] = to_builtin(matrix)
                    elif row_swap is not None or column_swap is not None:
                        if (row_swap is not None) == (column_swap is not None):
                            raise MutationSchemaError(
                                "linear action must provide exactly one row_swap or column_swap"
                            )
                        pair = row_swap if row_swap is not None else column_swap
                        if (
                            not isinstance(pair, list)
                            or len(pair) != 2
                            or any(not _is_int(value) or not 0 <= int(value) < 64 for value in pair)
                            or int(pair[0]) == int(pair[1])
                        ):
                            raise MutationSchemaError(
                                "linear swap must contain two distinct indices in 0..63"
                            )
                        result["row_swap" if row_swap is not None else "column_swap"] = [
                            int(pair[0]), int(pair[1])
                        ]
                    else:
                        raise MutationSchemaError(
                            "linear action requires transformation_matrix, row_swap, or column_swap"
                        )
                result.update(round_index=int(round_index), component=component)
            else:
                parents = raw.get("parent_candidate_ids")
                if not isinstance(parents, list) or len(parents) != 2:
                    raise MutationSchemaError("crossover requires exactly two parent_candidate_ids")
                if parents[0] == parents[1] or any(not isinstance(item, str) or item not in bound_ids for item in parents):
                    raise MutationSchemaError("crossover parent ids must be two distinct bound candidates")
                start = raw.get("start_component")
                if not isinstance(start, Mapping):
                    raise MutationSchemaError("crossover requires start_component")
                if set(start) - {"round_index", "component", "sbox_index"}:
                    raise MutationSchemaError("unknown start_component fields")
                start_round = start.get("round_index")
                start_kind = start.get("component")
                if not _is_int(start_round) or int(start_round) < 0 or start_kind not in {"sbox", "linear"}:
                    raise MutationSchemaError("start_component is malformed")
                normalized_start = {"round_index": int(start_round), "component": start_kind}
                if start_kind == "sbox":
                    start_sbox = start.get("sbox_index")
                    if not _is_int(start_sbox) or not 0 <= int(start_sbox) < 16:
                        raise MutationSchemaError("start_component.sbox_index must be in 0..15")
                    normalized_start["sbox_index"] = int(start_sbox)
                result.update(parent_candidate_ids=list(parents), start_component=normalized_start)
            parsed.append(result)
        except MutationSchemaError as exc:
            rejected.append({"action_index": index, "status": "rejected",
                             "rejection_reason": "schema_validation_failed",
                             "error_detail": _safe_error_text(exc), "action": to_builtin(raw)})
    return parsed, rejected, None


def apply_action_plan(
    members: Sequence[Any],
    actions: Sequence[Mapping[str, Any]],
    engineering_validator: Optional[Any] = None,
    generation_context: Optional[Mapping[str, Any]] = None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Apply decision-only actions and materialize the next candidates locally."""
    originals = [copy.deepcopy(member) for member in members]
    mutated = [copy.deepcopy(member) for member in members]
    id_to_index = {
        str(item.get("candidate_id")): int(item.get("candidate_index"))
        for item in _binding_entries(_candidate_bindings(originals))
    }
    records: list[dict[str, Any]] = []

    def _replace_sbox(target: Any, change: Mapping[str, Any]) -> None:
        rf = target.round_functions[int(change["round_index"])]
        substitution = rf.substitution
        index = int(change["sbox_index"])
        if not hasattr(substitution, "input_permutations") or not hasattr(substitution, "output_permutations"):
            raise ValueError("sbox_requires_B_and_D_metadata")
        B = np.asarray(substitution.input_permutations[index], dtype=int)
        D = np.asarray(substitution.output_permutations[index], dtype=int)
        T = np.asarray(change["transformation_matrix"], dtype=int)
        if not _sbox_functions.is_bit_permutation_matrix(B) or not _sbox_functions.is_bit_permutation_matrix(D):
            raise ValueError("existing_sbox_B_or_D_is_invalid")
        if change["component"] == "sbox_B":
            B = (T.dot(B)) % 2
        else:
            D = (D.dot(T)) % 2
        if not _sbox_functions.is_bit_permutation_matrix(B) or not _sbox_functions.is_bit_permutation_matrix(D):
            raise ValueError("transformation_does_not_preserve_sbox_permutation")
        substitution.input_permutations[index] = B
        substitution.output_permutations[index] = D
        substitution.sboxes[index] = _sbox_functions.construct_mantis_sbox(B, D)

    def _splice(target: Any, parent_a: Any, parent_b: Any, start: Mapping[str, Any]) -> None:
        rounds_a = parent_a.round_functions
        rounds_b = parent_b.round_functions
        if len(rounds_a) != len(rounds_b):
            raise ValueError("crossover_round_count_mismatch")
        round_index = int(start["round_index"])
        if not 0 <= round_index < len(rounds_a):
            raise ValueError("crossover_round_index_out_of_range")
        start_kind = start["component"]
        start_sbox = int(start.get("sbox_index", 0)) if start_kind == "sbox" else 16
        for r_index in range(round_index, len(rounds_a)):
            first_sbox = start_sbox if r_index == round_index else 0
            for sbox_index in range(first_sbox, 16):
                src = rounds_b[r_index].substitution
                dst = target.round_functions[r_index].substitution
                dst.sboxes[sbox_index] = copy.deepcopy(src.sboxes[sbox_index])
                dst.input_permutations[sbox_index] = copy.deepcopy(src.input_permutations[sbox_index])
                dst.output_permutations[sbox_index] = copy.deepcopy(src.output_permutations[sbox_index])
            if r_index > round_index or start_kind in {"linear", "sbox"}:
                target.round_functions[r_index].linear = copy.deepcopy(rounds_b[r_index].linear)
        target.round_functions[-1].linear = None

    for action_index, action in enumerate(actions):
        target_index = id_to_index.get(str(action.get("target_candidate_id")))
        record = {
            "action_index": action_index,
            "action_type": action.get("action_type"),
            "target_candidate_id": action.get("target_candidate_id"),
            "candidate_index": target_index,
            "action_spec": _action_spec(action),
            "action_signature": _action_signature(action),
        }
        try:
            if target_index is None:
                raise ValueError("target_candidate_id_mismatch")
            target = mutated[target_index]
            record["before_fingerprint"] = target.candidate_fingerprint()
            expected = action.get("base_fingerprint")
            if expected and expected != originals[target_index].candidate_fingerprint():
                raise ValueError("base_fingerprint_mismatch")
            if action["action_type"] == "mutation":
                if str(action["component"]).startswith("sbox_"):
                    _replace_sbox(target, action)
                else:
                    round_function = target.round_functions[int(action["round_index"])]
                    if round_function.linear is None:
                        raise ValueError("round_has_no_linear_layer")
                    matrix = np.asarray(round_function.linear.matrix, dtype=int).copy()
                    if action.get("row_swap") is not None:
                        first, second = map(int, action["row_swap"])
                        matrix[[first, second], :] = matrix[[second, first], :]
                        transformed = matrix
                    elif action.get("column_swap") is not None:
                        first, second = map(int, action["column_swap"])
                        matrix[:, [first, second]] = matrix[:, [second, first]]
                        transformed = matrix
                    else:
                        transformed = (
                            np.asarray(action["transformation_matrix"], dtype=int).dot(matrix)
                        ) % 2
                    if not _linear_functions.is_valid_linear_matrix(
                        transformed, row_column_weight=3
                    ):
                        raise ValueError("transformation_does_not_preserve_linear_properties")
                    round_function.linear.matrix = transformed
            else:
                parent_ids = action["parent_candidate_ids"]
                parent_a = originals[id_to_index[parent_ids[0]]]
                parent_b = originals[id_to_index[parent_ids[1]]]
                _splice(target, parent_a, parent_b, action["start_component"])
            issues = _local_structure_issues(target)
            if issues:
                record["validation_issues"] = to_builtin(issues)
                raise ValueError("structural_validation_failed")
            engineering_ok, engineering_detail = _run_engineering_validator(
                engineering_validator,
                target,
                generation_context,
            )
            record["engineering_validation"] = to_builtin(engineering_detail)
            if not engineering_ok:
                raise ValueError("engineering_validation_failed")
            record["after_fingerprint"] = target.candidate_fingerprint()
            record["status"] = "accepted"
        except Exception as exc:
            mutated[target_index] = originals[target_index] if target_index is not None else mutated[target_index]
            record.update(
                status="rejected",
                rejection_reason=str(exc),
                after_fingerprint=(
                    originals[target_index].candidate_fingerprint()
                    if target_index is not None
                    else None
                ),
            )
        records.append(record)
    return mutated, records


def _action_spec(action: Mapping[str, Any]) -> dict[str, Any]:
    """Return the structural part of an action, excluding generation-local IDs."""
    keys = (
        "action_type", "parent_candidate_ids", "round_index", "component",
        "sbox_index", "row_swap", "column_swap", "start_component",
    )
    result = {key: to_builtin(action[key]) for key in keys if action.get(key) is not None}
    if str(action.get("component", "")).startswith("sbox_"):
        bit_permutation = action.get("bit_permutation")
        if bit_permutation is None:
            bit_permutation = _bit_permutation_from_matrix(action.get("transformation_matrix"))
        if bit_permutation is not None:
            result["bit_permutation"] = to_builtin(bit_permutation)
    elif action.get("transformation_matrix") is not None:
        result["transformation_matrix"] = to_builtin(action["transformation_matrix"])
    return result


def _bit_permutation_from_matrix(matrix: Any) -> Optional[list[int]]:
    if not _sbox_functions.is_bit_permutation_matrix(matrix):
        return None
    values = np.asarray(matrix, dtype=int)
    return [int(np.argmax(values[:, column])) for column in range(4)]


def _action_signature(action: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(_action_spec(action)).encode("utf-8")).hexdigest()


def _structure_issues(candidate: Any, candidate_index: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if _PLUGIN_CONTRACTS_AVAILABLE:
        try:
            payload = candidate_to_dict(
                candidate,
                candidate_id=_candidate_id(candidate, candidate_index),
                metadata=_member_metrics(candidate),
                validate=True,
            )
            contract_issues = validate_candidate_payload(
                payload, require_invertible=True, check_fingerprint=True
            )
            if contract_issues:
                issues.extend(to_builtin(contract_issues))
        except Exception as exc:
            issues.append(
                {
                    "code": "plugin_contract_validation_failed",
                    "message": _safe_error_text(exc),
                }
            )
    issues.extend(_local_structure_issues(candidate))
    return _deduplicate_issues(issues)


def _local_structure_issues(candidate: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    rounds = getattr(candidate, "round_functions", None)
    if not isinstance(rounds, list) or not rounds:
        return [{"code": "rounds", "message": "candidate has no round_functions list"}]
    if getattr(candidate, "num_rounds", len(rounds)) != len(rounds):
        issues.append({"code": "num_rounds", "message": "num_rounds does not match rounds"})

    for round_index, round_function in enumerate(rounds):
        substitution = getattr(round_function, "substitution", None)
        sboxes = getattr(substitution, "sboxes", None)
        if not isinstance(sboxes, list) or len(sboxes) != 16:
            issues.append(
                {
                    "code": "sbox_count",
                    "message": f"round {round_index} must contain 16 S-boxes",
                }
            )
        else:
            for sbox_index, table in enumerate(sboxes):
                try:
                    values = list(table)
                except TypeError:
                    values = []
                if len(values) != 16 or any(not _is_int(value) for value in values):
                    issues.append(
                        {
                            "code": "sbox_shape",
                            "message": f"round {round_index} S-box {sbox_index} is malformed",
                        }
                    )
                elif sorted(values) != list(range(16)):
                    issues.append(
                        {
                            "code": "sbox_permutation",
                            "message": f"round {round_index} S-box {sbox_index} is not a permutation",
                        }
                    )

            input_permutations = getattr(substitution, "input_permutations", None)
            output_permutations = getattr(substitution, "output_permutations", None)
            # Legacy non-MANTIS candidates may have no B/D metadata at all. If
            # metadata is present, however, it is part of the component
            # contract and every position must carry two legal permutations
            # whose construction reproduces the stored S-box exactly.
            metadata_present = input_permutations is not None or output_permutations is not None
            if metadata_present:
                if not isinstance(input_permutations, (list, tuple)) or not isinstance(
                    output_permutations, (list, tuple)
                ) or len(input_permutations) != 16 or len(output_permutations) != 16:
                    issues.append(
                        {
                            "code": "sbox_permutation_metadata_count",
                            "message": f"round {round_index} must contain 16 input and 16 output bit permutations",
                        }
                    )
                else:
                    for sbox_index, table in enumerate(sboxes):
                        input_permutation = input_permutations[sbox_index]
                        output_permutation = output_permutations[sbox_index]
                        if input_permutation is None and output_permutation is None:
                            continue
                        valid, constructed = _is_valid_mantis_sbox_payload(
                            table, input_permutation, output_permutation
                        )
                        if not valid or constructed is None:
                            issues.append(
                                {
                                    "code": "sbox_permutation_metadata",
                                    "message": (
                                        f"round {round_index} S-box {sbox_index} must have legal 4x4 B/D "
                                        "and satisfy D o S_MANTIS o B"
                                    ),
                                }
                            )

        linear = getattr(round_function, "linear", None)
        matrix = getattr(linear, "matrix", None) if linear is not None else None
        is_last = round_index == len(rounds) - 1
        if is_last:
            if linear is not None and matrix is not None:
                issues.append(
                    {
                        "code": "final_linear",
                        "message": "final round must not contain a linear matrix",
                    }
                )
            continue
        if matrix is None:
            issues.append(
                {
                    "code": "missing_linear",
                    "message": f"round {round_index} has no linear matrix",
                }
            )
            continue
        rows = _matrix_as_binary_rows(matrix)
        if rows is None:
            issues.append(
                {
                    "code": "linear_shape",
                    "message": f"round {round_index} linear matrix must be binary 64x64",
                }
            )
        elif not _linear_functions.is_valid_linear_matrix(
            matrix, row_column_weight=3
        ):
            issues.append(
                {
                    "code": "linear_structure",
                    "message": (
                        f"round {round_index} linear matrix must be binary, "
                        "3-regular, orthogonal, and invertible over GF(2)"
                    ),
                }
            )
    return issues


def _run_engineering_validator(
    validator: Optional[Any], candidate: Any, context: Optional[Mapping[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    if validator is None:
        return True, {"status": "skipped", "reason": "validator_not_configured"}
    function = None
    if hasattr(validator, "validate_candidate"):
        function = validator.validate_candidate
    elif hasattr(validator, "validate"):
        function = validator.validate
    elif callable(validator):
        function = validator
    if function is None:
        return False, {"status": "error", "reason": "validator_not_callable"}

    try:
        result = _call_validator(function, candidate, context)
    except Exception as exc:
        return False, {
            "status": "error",
            "reason": "validator_exception",
            "error_detail": _safe_error_text(exc),
        }
    detail = to_builtin(result)
    if isinstance(result, bool):
        return result, {"status": "valid" if result else "invalid", "valid": result}
    if result is None:
        return False, {"status": "invalid", "reason": "validator_returned_none"}
    if isinstance(result, list):
        return len(result) == 0, {"status": "valid" if not result else "invalid", "issues": detail}
    if isinstance(result, Mapping):
        status = str(result.get("status", "")).lower()
        if status in {"unavailable", "not_configured", "skipped"}:
            # B/C are optional during framework bring-up.  Structural validation
            # remains mandatory, while an unavailable engineering plugin is logged.
            return True, dict(detail)
        if status in {"invalid", "error", "failed"}:
            # Error states take precedence over contradictory convenience flags
            # such as ``valid=true``.  A provider must report an explicitly
            # successful status before a candidate can pass this gate.
            return False, dict(detail)
        if "valid" in result:
            return bool(result["valid"]), dict(detail)
        if "ok" in result:
            return bool(result["ok"]), dict(detail)
        if "passed" in result:
            return bool(result["passed"]), dict(detail)
        if status:
            return status in {"ok", "valid", "passed", "success"}, dict(detail)
    return False, {"status": "invalid", "reason": "unsupported_validator_result", "result": detail}


def _call_validator(
    function: Callable[..., Any], candidate: Any, context: Optional[Mapping[str, Any]]
) -> Any:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(candidate, context)
    parameters = signature.parameters
    if "context" in parameters:
        return function(candidate, context=context)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return function(candidate, context=context)
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) >= 2:
        return function(candidate, context)
    return function(candidate)


def _short_digest(value: Any) -> str:
    """Return a compact semantic digest for prompt context, not validation."""
    raw = _canonical_json(to_builtin(value)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _prompt_metrics(candidate: Any) -> dict[str, Any]:
    """Keep all scores while dropping verbose plugin warnings/artifacts."""
    security = getattr(candidate, "plugin_security", None)
    validation = getattr(candidate, "plugin_validation", None)
    performance = getattr(candidate, "plugin_performance", None)
    return {
        "fitness": getattr(candidate, "fitness", None),
        "security_diff": to_builtin(getattr(candidate, "security_diff", None)),
        "security_linear": to_builtin(getattr(candidate, "security_linear", None)),
        "latency": getattr(candidate, "latency", None),
        "evaluation_status": getattr(candidate, "evaluation_status", None),
        "security_status": security.get("status") if isinstance(security, Mapping) else None,
        "validation_status": validation.get("status") if isinstance(validation, Mapping) else None,
        "performance_status": performance.get("status") if isinstance(performance, Mapping) else None,
    }


def _candidate_prompt_payload(candidate: Any, index: int) -> dict[str, Any]:
    """Build a compact candidate summary; never send complete components."""
    candidate_id = _candidate_id(candidate, index)
    fingerprint = _member_fingerprint(candidate)
    layer_summaries = []
    rounds = getattr(candidate, "round_functions", []) or []
    for round_index, round_function in enumerate(rounds):
        substitution = getattr(round_function, "substitution", None)
        sboxes = list(getattr(substitution, "sboxes", []) or [])
        input_permutations = list(getattr(substitution, "input_permutations", []) or [])
        output_permutations = list(getattr(substitution, "output_permutations", []) or [])
        linear = getattr(round_function, "linear", None)
        matrix = getattr(linear, "matrix", None) if linear is not None else None
        layer_summaries.append({
            "round_index": round_index,
            "sbox_count": len(sboxes),
            "sbox_digest": _short_digest({
                "in": input_permutations,
                "out": output_permutations,
            }),
            "linear": None if matrix is None else {
                "shape": [64, 64],
                "row_weight": sorted(set(np.asarray(matrix, dtype=int).sum(axis=1).tolist())),
                "column_weight": sorted(set(np.asarray(matrix, dtype=int).sum(axis=0).tolist())),
                "digest": _short_digest(matrix),
            },
        })
    return {
        "candidate_index": index,
        "candidate_id": candidate_id,
        "score": _prompt_score(candidate),
        "fingerprint": fingerprint,
        "metrics": _prompt_metrics(candidate),
        "num_rounds": len(rounds),
        "layers": layer_summaries,
    }


def _compact_generation_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Strip repeated population/component details from the user prompt."""
    allowed = {
        "run_id", "generation", "num_rounds", "population_size", "elite_ids",
        "duplicate_children", "required_action_children", "crossover_records", "crossover_children",
    }
    compact = {key: to_builtin(context[key]) for key in allowed if key in context}
    compact["structure"] = {
        "state_bits": int(getattr(_config, "CIPHER_STRUCTURE", {}).get("STATE_BITS", 64)),
        "cell_bits": int(getattr(_config, "CIPHER_STRUCTURE", {}).get("CELL_BITS", 4)),
        "rounds": int(context.get("num_rounds", 0) or 0),
        "layer_count": max(0, 2 * int(context.get("num_rounds", 0) or 0) - 1),
        "final_round_has_linear": False,
    }
    if "crossover_children" in compact:
        compact["crossover_children"] = [
            {
                "candidate_id": item.get("candidate_id"),
                "fingerprint": item.get("fingerprint"),
                "duplicate_before_llm": item.get("duplicate_before_llm", False),
            }
            for item in compact["crossover_children"]
            if isinstance(item, Mapping)
        ]
    return compact


def _local_candidate_payload(
    candidate: Any,
    candidate_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    rounds = []
    for round_function in getattr(candidate, "round_functions", []):
        substitution = getattr(round_function, "substitution", None)
        linear = getattr(round_function, "linear", None)
        sboxes = getattr(substitution, "sboxes", [])
        input_permutations = getattr(substitution, "input_permutations", [None] * len(sboxes))
        output_permutations = getattr(substitution, "output_permutations", [None] * len(sboxes))
        rounds.append(
            {
                "sboxes": to_builtin(sboxes),
                "sbox_components": [
                    {
                        "value": to_builtin(sboxes[index]),
                        "input_permutation": to_builtin(input_permutations[index])
                        if index < len(input_permutations)
                        else None,
                        "output_permutation": to_builtin(output_permutations[index])
                        if index < len(output_permutations)
                        else None,
                    }
                    for index in range(len(sboxes))
                ],
                "sbox_input_permutations": to_builtin(
                    input_permutations
                ),
                "sbox_output_permutations": to_builtin(
                    output_permutations
                ),
                "linear_matrix": to_builtin(getattr(linear, "matrix", None))
                if linear is not None
                else None,
            }
        )
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "num_rounds": len(rounds),
        "rounds": rounds,
        "metadata": to_builtin(metadata or {}),
    }


def _system_prompt(settings: DeepSeekSettings) -> str:
    return (
        "You are the constrained structural decision maker for an SPN block-cipher search. "
        "Use only the compact candidate summaries and scores supplied; do not infer missing components. "
        "Return exactly one JSON object and no markdown. "
        f"The only output schema is {{\"schema_version\":\"{MUTATION_SCHEMA_VERSION}\","
        "\"request_id\":<copy input request_id>,\"generation\":<copy input generation exactly>,\"actions\":[...]}. "
        "Return only compact actions; never output complete S-box tables or a 64x64 matrix unless required. "
        "For S-box mutation use sbox_B or sbox_D with sbox_index and bit_permutation. "
        "bit_permutation must be copied from action_requirements.allowed_sbox_bit_permutations. "
        "For linear mutation prefer exactly one row_swap or column_swap pair with indices 0..63; "
        "a full 64x64 transformation_matrix is allowed only when necessary. "
        "For a crossover, output exactly two distinct parent_candidate_ids and start_component containing "
        "round_index and component sbox or linear, plus sbox_index when component is sbox. Do not output the child. "
        "Each request covers the complete current generation and creates every next-generation child slot. "
        "If the user payload contains action_requirements.required_for_candidate_ids, you MUST emit at least one "
        "valid mutation or crossover targeting every listed candidate; with only one candidate, use a mutation. "
        "When exactly_one_action_per_candidate is true, output exactly one action for each listed candidate ID, "
        "with no missing IDs and no duplicate target IDs. Copy target_candidate_id and base_fingerprint exactly "
        "from required_action_specs. Choose the round, S-box index, component, and transformation deliberately "
        "from their allowed values. Do not repeatedly choose round 0, S-box 0, or the same component. "
        "For an S-box mutation use this exact field layout: "
        '{"schema_version":"1.0","request_id":"<request_id>","generation":<generation>,"actions":['
        '{"action_type":"mutation","target_candidate_id":"<listed_candidate_id>",'
        '"base_fingerprint":"<exact_candidate_fingerprint>","round_index":0,'
        '"component":"sbox_B","sbox_index":0,"bit_permutation":[1,0,2,3]}]}. '
        "Use concrete JSON numbers, never angle-bracket text. You may choose other allowed round/component/index/"
        "bit_permutation values to avoid recent actions. For a linear mutation use component linear and exactly "
        "one compact row_swap or column_swap, for example row_swap:[0,1]. For crossover, use the shape "
        '{"action_type":"crossover","target_candidate_id":"<listed_candidate_id>",'
        '"base_fingerprint":"<exact_target_fingerprint>","parent_candidate_ids":'
        '["<parent_a_id>","<parent_b_id>"],"start_component":'
        '{"round_index":0,"component":"sbox","sbox_index":0}}. '
        "Treat every recent_action_specs entry as forbidden for that candidate. The resulting cipher must not "
        "match any forbidden_result_fingerprints entry; vary the action enough to produce a new structure. "
        "Do not output placeholder text, markdown, extra root fields, rationale, or an empty actions array. "
        "If validation_feedback is present, repair every listed issue in the next JSON object and do not repeat "
        "the rejected action. "
        "All resulting components are validated by the program. Do not include explanations or rationale. "
        f"Return at most {settings.max_actions} actions, with at least one action for every required candidate."
    )


def _decode_json_object(content: str) -> Mapping[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    value = json.loads(text)
    if not isinstance(value, Mapping):
        raise MutationSchemaError("model content must decode to a JSON object")
    return value




def _new_report(
    request_id: str, settings: DeepSeekSettings, member_count: int
) -> dict[str, Any]:
    return {
        "schema_version": MUTATION_SCHEMA_VERSION,
        "request_id": request_id,
        "status": "pending",
        "fallback_reason": None,
        "actions": [],
        "change_records": [],
        "provider": "deepseek",
        "model": settings.model,
        "candidate_count": member_count,
        "request_attempts": 0,
        "prompt_chars": 0,
        "prompt_token_estimate": 0,
        "response_chars": 0,
        "max_component_generation_attempts": _max_component_generation_attempts(settings),
        "generation_attempts": 0,
        "validation_retries": 0,
        "validation_history": [],
        "validation_issues": [],
        "response_generation": None,
        "rationale": None,
        "accepted_count": 0,
        "rejected_count": 0,
        "required_action_candidate_ids": [],
        "started_at": _utc_now(),
        "finished_at": None,
    }


def _fallback(report: dict[str, Any], reason: str) -> dict[str, Any]:
    report["status"] = "fallback_noop"
    report["fallback_reason"] = reason
    report["finished_at"] = _utc_now()
    return report


def _generation_component_issues(
    candidates: Sequence[Any],
    *,
    schema_rejections: Sequence[Mapping[str, Any]],
    application_records: Sequence[Mapping[str, Any]],
    generation_attempt: int,
) -> list[dict[str, Any]]:
    """Collect action legality issues for one complete model attempt.

    ``apply_action_plan`` rolls back rejected actions, so checking only the
    returned candidates would hide malformed model output. Include those
    action-level rejections as feedback, while leaving engineering-plugin
    rejections to the existing engineering gate.
    """

    issues: list[dict[str, Any]] = []
    for rejection in schema_rejections:
        issues.append(
            {
                "code": "model_action_schema_invalid",
                "message": rejection.get("error_detail") or "model action failed schema validation",
                "action_index": rejection.get("action_index"),
                "candidate_index": (
                    rejection.get("action", {}).get("candidate_index")
                    if isinstance(rejection.get("action"), Mapping)
                    else None
                ),
                "generation_attempt": generation_attempt,
            }
        )

    retryable_reasons = {
        "candidate_index_out_of_range",
        "target_candidate_id_mismatch",
        "base_fingerprint_mismatch",
        "structural_validation_failed",
        "final_structural_validation_failed",
        "application_failed",
        "engineering_validation_failed",
    }
    for record in application_records:
        if record.get("status") != "rejected":
            continue
        reason = record.get("rejection_reason")
        if reason not in retryable_reasons:
            continue
        issue: dict[str, Any] = {
            "code": "model_component_invalid",
            "message": record.get("error_detail") or reason or "model mutation was rejected",
            "candidate_index": record.get("candidate_index"),
            "round_index": record.get("round_index"),
            "component": record.get("component"),
            "action_type": record.get("action_type"),
            "rejection_reason": reason,
            "generation_attempt": generation_attempt,
        }
        details = record.get("validation_issues") or record.get("final_validation_issues")
        if details:
            issue["validation_issues"] = to_builtin(details)
        issues.append(issue)

    for candidate_index, candidate in enumerate(candidates):
        for issue in _structure_issues(candidate, candidate_index):
            enriched = dict(to_builtin(issue))
            enriched.update(
                candidate_index=candidate_index,
                generation_attempt=generation_attempt,
            )
            issues.append(enriched)
    return _deduplicate_issues(issues)


def _raise_component_validation_error(
    originals: Sequence[Any],
    report: dict[str, Any],
    validation_history: Sequence[Mapping[str, Any]],
    validation_issues: Sequence[Mapping[str, Any]],
    request_attempts: int,
    cause: Optional[BaseException],
) -> NoReturn:
    """Finalize an error report and interrupt the search after three attempts."""

    del originals  # Kept in the signature to make the failure path explicit.
    issues = to_builtin(list(validation_issues))
    report["status"] = "error"
    report["fallback_reason"] = "component_validation_failed"
    report["request_attempts"] = int(request_attempts)
    report["generation_attempts"] = len(validation_history)
    report["validation_retries"] = max(0, len(validation_history) - 1)
    report["validation_history"] = to_builtin(list(validation_history))
    report["validation_issues"] = issues
    if validation_history:
        latest = validation_history[-1]
        report["response_generation"] = latest.get("response_generation")
        report["actions"] = to_builtin(latest.get("actions", []))
        report["rationale"] = latest.get("rationale")
        report["change_records"] = to_builtin(latest.get("change_records", []))
        report["accepted_count"] = int(latest.get("accepted_count", 0) or 0)
        report["rejected_count"] = int(latest.get("rejected_count", 0) or 0)
    report["error_detail"] = (
        _safe_error_text(cause)
        if cause is not None
        else (
            "model generated illegal cipher components after %d attempts"
            % len(validation_history)
        )
    )
    report["finished_at"] = _utc_now()
    first_issue = issues[0] if issues and isinstance(issues[0], Mapping) else {}
    detail = first_issue.get("message") or first_issue.get("code") or "see validation report"
    raise ComponentValidationError(
        "LLM generated illegal cipher components after %d attempts: %s"
        % (len(validation_history), detail),
        report=report,
        issues=issues,
    )


def _member_metrics(member: Any) -> dict[str, Any]:
    fields = (
        "gen_index",
        "pop_index",
        "identifier",
        "candidate_id",
        "fitness",
        "diversity",
        "security_diff",
        "security_linear",
        "latency",
        "evaluation_status",
        "evaluation_error",
        "plugin_security",
        "plugin_validation",
        "plugin_performance",
        "parent_ids",
        "crossover_strategy",
        "crossover_details",
        "mutation_changes",
    )
    return {field: to_builtin(getattr(member, field, None)) for field in fields}


def _prompt_score(member: Any) -> float:
    """Return a stable numeric score for the LLM prompt (0.0 when unset)."""
    value = getattr(member, "fitness", None)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if np.isfinite(value) else 0.0


def _candidate_bindings(members: Sequence[Any]) -> dict[str, Any]:
    """Return stable prompt-local candidate ID/fingerprint bindings.

    The LLM is allowed to address a child by either its positional index or its
    stable candidate ID.  The fingerprint is captured from the exact pre-request
    structure and is checked again before applying each action.
    """

    entries: list[dict[str, Any]] = []
    for index, member in enumerate(members):
        entries.append(
            {
                "candidate_index": index,
                "candidate_id": _candidate_id(member, index),
                "fingerprint": _member_fingerprint(member),
            }
        )
    return {"entries": entries}


def _binding_entries(
    bindings: Optional[Mapping[str, Any] | Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    if bindings is None:
        return []
    if isinstance(bindings, Mapping):
        source = bindings.get("entries", [])
    else:
        source = bindings
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
        return []
    entries: list[dict[str, Any]] = []
    for position, value in enumerate(source):
        if not isinstance(value, Mapping):
            continue
        entry = dict(value)
        entry.setdefault("candidate_index", position)
        entries.append(entry)
    entries.sort(key=lambda item: int(item.get("candidate_index", 0)))
    return entries


def _binding_index_by_id(
    bindings: Sequence[Mapping[str, Any]], candidate_id: Any
) -> Optional[int]:
    if not isinstance(candidate_id, str):
        return None
    matches = [
        int(entry["candidate_index"])
        for entry in bindings
        if str(entry.get("candidate_id")) == candidate_id
        and _is_int(entry.get("candidate_index"))
    ]
    return matches[0] if len(matches) == 1 else None


def _member_fingerprint(member: Any) -> Optional[str]:
    try:
        candidate_id = _candidate_id(member, getattr(member, "pop_index", 0) or 0)
        payload = candidate_to_dict(member, candidate_id=candidate_id, validate=False)
        fingerprint = payload.get("fingerprint")
        if fingerprint:
            return str(fingerprint)
        return str(candidate_fingerprint(payload))
    except Exception:
        return None


def _candidate_id(member: Any, index: int) -> str:
    candidate_id = getattr(member, "candidate_id", None)
    if candidate_id not in (None, ""):
        return str(candidate_id)
    identifier = getattr(member, "identifier", None)
    if identifier not in (None, ""):
        return str(identifier)
    generation = getattr(member, "gen_index", None)
    population = getattr(member, "pop_index", None)
    if generation is not None and population is not None:
        return f"gen-{generation}-member-{population}"
    return f"candidate-{index}"


def _matrix_as_binary_rows(matrix: Any) -> Optional[list[int]]:
    try:
        if len(matrix) != 64:
            return None
        rows: list[int] = []
        for raw_row in matrix:
            values = list(raw_row)
            if len(values) != 64:
                return None
            row = 0
            for value in values:
                bit = int(value.item()) if hasattr(value, "item") else int(value)
                if bit not in (0, 1):
                    return None
                row = (row << 1) | bit
            rows.append(row)
        return rows
    except (TypeError, ValueError, OverflowError):
        return None


def _deduplicate_issues(issues: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in issues:
        normalized = to_builtin(issue)
        key = _canonical_json(normalized)
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _canonical_json(value: Any) -> str:
    try:
        return canonical_json(value)
    except Exception:
        return json.dumps(
            to_builtin(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )


def _safe_error_text(exc: BaseException) -> str:
    text = re.sub(r"Bearer\s+\S+", "Bearer <redacted>", str(exc), flags=re.IGNORECASE)
    return f"{type(exc).__name__}: {text}"[:1000]


def _is_retryable_request_error(exc: BaseException) -> bool:
    """Return whether a failed DeepSeek request is safe to retry.

    HTTP 4xx responses are generally deterministic configuration or payload
    failures. Retry only rate limiting (429) and server-side 5xx responses; URL
    resolution/transport errors and timeouts remain transient candidates.
    """

    if isinstance(exc, urllib_error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        return code == 429 or 500 <= code <= 599
    if isinstance(exc, urllib_error.URLError):
        return True
    if isinstance(exc, TimeoutError):
        return True
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_int(value: Any) -> bool:
    if isinstance(value, Integral) and not isinstance(value, bool):
        return True
    # NumPy integer scalars intentionally remain an optional dependency.  They
    # expose ``item``; accept them only when the unboxed value is an integer.
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            unboxed = item_method()
        except (TypeError, ValueError):
            return False
        return isinstance(unboxed, Integral) and not isinstance(unboxed, bool)
    return False


def _index(value: Any, size: int, name: str) -> int:
    if not _is_int(value) or not 0 <= value < size:
        raise MutationSchemaError(f"{name} must be an integer in 0..{size - 1}")
    return value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled", ""}
    return bool(value)


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    parsed = int(value)
    return max(minimum, min(maximum, parsed))


def _max_component_generation_attempts(settings: DeepSeekSettings) -> int:
    """Return the hard-capped number of complete model generations allowed."""

    try:
        value = int(settings.max_component_generation_attempts)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_COMPONENT_GENERATION_ATTEMPTS
    # return max(1, value)
    return max(1, min(3, value))


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    parsed = float(value)
    return max(minimum, min(maximum, parsed))


__all__ = [
    "DeepSeekMutationAdvisor",
    "DeepSeekSettings",
    "MUTATION_SCHEMA_VERSION",
    "MutationSchemaError",
    "ComponentValidationError",
    "apply_action_plan",
    "mutate_generation",
]
