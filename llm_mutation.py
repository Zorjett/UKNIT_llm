"""DeepSeek-guided mutation for the uKNIT genetic search.

The module deliberately keeps the language model outside the cipher data model.  A
generation is serialized once, one ``chat/completions`` request is made, and the
returned operations are applied to deep copies only after strict schema checks.

The public entry point intended for the search loop is ``mutate_generation``.
Failures are represented as a no-op report; API or model failures never make the
genetic loop fail.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import json
from numbers import Integral
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request


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
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_TOTAL_OPERATIONS = 64
DEFAULT_MAX_OPERATIONS_PER_CANDIDATE = 4
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000 # API响应最大允许1MB

_ROOT_KEYS = {
    "schema_version",
    "request_id",
    "generation",
    "operations",
    "plans",
    "rationale",
}
_PLAN_KEYS = {
    "target_candidate_id",
    "base_fingerprint",
    "candidate_index",
    "operations",
    "rationale",
}
_OPERATION_KEYS = {
    "candidate_index",
    "target_candidate_id",
    "base_fingerprint",
    "round_index",
    "component",
    "operation",
    "params",
    "reason",
}

_OPERATION_SPECS: dict[str, dict[str, Any]] = {
    "swap_sbox_entries": {
        "component": "sbox",
        "params": {"sbox_index", "entry_a", "entry_b"},
    },
    "replace_sbox": {
        "component": "sbox",
        "params": {"sbox_index", "table"},
    },
    "swap_sbox_positions": {
        "component": "sbox",
        "params": {"sbox_a", "sbox_b"},
    },
    "swap_linear_rows": {
        "component": "linear",
        "params": {"row_a", "row_b"},
    },
    "swap_linear_columns": {
        "component": "linear",
        "params": {"column_a", "column_b"},
    },
    # Public aliases used in the team-A/B/C handoff schema.
    "sbox_swap_entries": {
        "component": "sbox",
        "params": {"sbox_index", "entry_a", "entry_b"},
    },
    "sbox_replace": {
        "component": "sbox",
        "params": {"sbox_index", "table"},
    },
    "sbox_affine": {
        "component": "sbox",
        "params": {"sbox_index", "table"},
    },
    "linear_swap_rows": {
        "component": "linear",
        "params": {"row_a", "row_b"},
    },
    "linear_swap_columns": {
        "component": "linear",
        "params": {"column_a", "column_b"},
    },
    "copy_component": {
        "component": "copy_component",
        # The source selector accepts either a prompt-local candidate index or
        # a candidate id.  The exact alternative is checked in
        # ``_normalize_params`` rather than through a fixed key set.
        "params": None,
    },
    "copy_round": {
        "component": "round",
        "params": None,
    },
}

_OPERATION_ALIASES = {
    # Keep the historical names accepted by older experiments.  The public
    # handoff names remain valid as first-class names and are preserved in
    # reports, so downstream tooling can see exactly what the model returned.
    "sbox_swap_entries": "sbox_swap_entries",
    "sbox_replace": "sbox_replace",
    "linear_swap_rows": "linear_swap_rows",
    "linear_swap_columns": "linear_swap_columns",
    "swap_sbox_entries": "swap_sbox_entries",
    "replace_sbox": "replace_sbox",
    "swap_sbox_positions": "swap_sbox_positions",
    "sbox_affine": "sbox_affine",
}

# Execution names are kept separate from the public operation name so reports
# preserve exactly what the model returned while the implementation can share
# the historical code paths.
_OPERATION_EXECUTION_NAMES = {
    "sbox_swap_entries": "swap_sbox_entries",
    "sbox_replace": "replace_sbox",
    "sbox_affine": "replace_sbox",
    "linear_swap_rows": "swap_linear_rows",
    "linear_swap_columns": "swap_linear_columns",
}


class MutationSchemaError(ValueError):
    """The model response does not conform to the mutation-plan schema."""


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
    max_total_operations: int = DEFAULT_MAX_TOTAL_OPERATIONS
    max_operations_per_candidate: int = DEFAULT_MAX_OPERATIONS_PER_CANDIDATE
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

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
                "max_total_operations": (
                    "DEEPSEEK_MAX_TOTAL_OPERATIONS",
                    "MAX_TOTAL_OPERATIONS",
                ),
                "max_operations_per_candidate": (
                    "DEEPSEEK_MAX_OPERATIONS_PER_CANDIDATE",
                    "MAX_OPERATIONS_PER_CANDIDATE",
                ),
                "max_response_bytes": (
                    "DEEPSEEK_MAX_RESPONSE_BYTES",
                    "MAX_RESPONSE_BYTES",
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
            max_total_operations=_bounded_int(
                values.get("max_total_operations", DEFAULT_MAX_TOTAL_OPERATIONS), 0, 4096
            ),
            max_operations_per_candidate=_bounded_int(
                values.get(
                    "max_operations_per_candidate",
                    DEFAULT_MAX_OPERATIONS_PER_CANDIDATE,
                ),
                0,
                256,
            ),
            max_response_bytes=_bounded_int(
                values.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES),
                1024,
                20_000_000,
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

        try:
            candidates = [
                _candidate_prompt_payload(member, index) for index, member in enumerate(members)
            ]
            prompt_payload = {
                "schema_version": MUTATION_SCHEMA_VERSION,
                "request_id": request_id,
                "generation_context": to_builtin(generation_context or {}),
                "candidates": candidates,
            }
            response = self._request_plan(prompt_payload)
            operations, schema_rejections, rationale = _parse_plan(
                response,
                request_id=request_id,
                member_count=len(members),
                max_total_operations=self.settings.max_total_operations,
                max_operations_per_candidate=self.settings.max_operations_per_candidate,
                candidate_bindings=_candidate_bindings(members),
                expected_generation=(generation_context or {}).get("generation"),
            )
            report["response_generation"] = response.get("generation") if isinstance(response, Mapping) else None
            report["request_attempts"] = self._last_request_attempts
        except MutationSchemaError as exc:
            report["error_detail"] = _safe_error_text(exc)
            return originals, _fallback(report, "schema_error")
        except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, OSError) as exc:
            report["error_detail"] = _safe_error_text(exc)
            return originals, _fallback(report, "api_error")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            report["error_detail"] = _safe_error_text(exc)
            return originals, _fallback(report, "response_error")
        except Exception as exc:  # The search loop must never fail because the advisor did.
            report["error_detail"] = _safe_error_text(exc)
            return originals, _fallback(report, "advisor_error")

        report["rationale"] = rationale
        report["plans"] = _plans_from_operations(operations, response)
        report["change_records"].extend(schema_rejections)

        mutated, application_records = apply_mutation_plan(
            members,
            operations,
            engineering_validator=engineering_validator,
            generation_context=generation_context,
        )
        report["change_records"].extend(application_records)
        accepted = sum(record.get("status") == "accepted" for record in report["change_records"])
        rejected = sum(record.get("status") == "rejected" for record in report["change_records"])
        report["accepted_count"] = accepted
        report["rejected_count"] = rejected
        report["status"] = "applied" if accepted else "no_changes"
        report["fallback_reason"] = None
        report["finished_at"] = _utc_now()
        return mutated, report

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
                "generation_context": to_builtin(generation_context or {}),
                "candidates": candidates,
            }
        )
        operations, rejections, rationale = _parse_plan(
            response,
            request_id=actual_request_id,
            member_count=len(members),
            max_total_operations=self.settings.max_total_operations,
            max_operations_per_candidate=self.settings.max_operations_per_candidate,
            candidate_bindings=_candidate_bindings(members),
            expected_generation=(generation_context or {}).get("generation"),
        )
        return {
            "schema_version": MUTATION_SCHEMA_VERSION,
            "request_id": actual_request_id,
            "generation": response.get("generation") if isinstance(response, Mapping) else None,
            "plans": _plans_from_operations(operations, response),
            # Keep the flat form for callers written against the first scaffold.
            "operations": operations,
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
        }
        encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
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


def apply_mutation_plan(
    members: Sequence[Any],
    plan: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    engineering_validator: Optional[Any] = None,
    generation_context: Optional[Mapping[str, Any]] = None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Apply already-normalized operations to deep copies and return change records."""

    if isinstance(plan, Mapping):
        raw_operations = _flatten_plan_payload(plan)
    else:
        raw_operations = plan
    if not isinstance(raw_operations, Sequence) or isinstance(raw_operations, (str, bytes)):
        raise TypeError("plan operations must be a sequence")

    originals = [copy.deepcopy(member) for member in members]
    mutated = [copy.deepcopy(member) for member in members]
    records: list[dict[str, Any]] = []
    records_by_candidate: dict[int, list[int]] = {}

    bindings = _candidate_bindings(originals)
    binding_entries = _binding_entries(bindings)
    for operation_index, operation in enumerate(raw_operations):
        record = _base_change_record(operation, operation_index)
        operation_for_apply = operation
        candidate_index = operation.get("candidate_index") if isinstance(operation, Mapping) else None
        if (candidate_index is None and isinstance(operation, Mapping)
                and operation.get("target_candidate_id")):
            resolved = _binding_index_by_id(binding_entries, operation.get("target_candidate_id"))
            if resolved is not None:
                candidate_index = resolved
                operation_for_apply = dict(operation)
                operation_for_apply["candidate_index"] = resolved
        if _is_int(candidate_index):
            record["candidate_index"] = int(candidate_index)
        if not _is_int(candidate_index) or not 0 <= candidate_index < len(mutated):
            record.update(status="rejected", rejection_reason="candidate_index_out_of_range")
            records.append(record)
            continue

        before_candidate = copy.deepcopy(mutated[candidate_index])
        record["candidate_id"] = _candidate_id(mutated[candidate_index], candidate_index)
        record["target_candidate_id"] = operation.get("target_candidate_id")
        record["base_fingerprint"] = operation.get("base_fingerprint")
        base_fingerprint = _member_fingerprint(originals[candidate_index])
        current_fingerprint = _member_fingerprint(mutated[candidate_index])
        record["before_fingerprint"] = current_fingerprint
        expected_id = operation.get("target_candidate_id")
        expected_fingerprint = operation.get("base_fingerprint")
        if expected_id and str(expected_id) != record["candidate_id"]:
            record.update(
                status="rejected",
                rejection_reason="target_candidate_id_mismatch",
                after_fingerprint=current_fingerprint,
            )
            records.append(record)
            continue
        if expected_fingerprint and expected_fingerprint != base_fingerprint:
            record.update(
                status="rejected",
                rejection_reason="base_fingerprint_mismatch",
                after_fingerprint=current_fingerprint,
            )
            records.append(record)
            continue
        try:
            record["before"] = _affected_component_snapshot(mutated[candidate_index], operation_for_apply, members=originals)
            _apply_operation(mutated[candidate_index], operation_for_apply, members=originals)
            record["after"] = _affected_component_snapshot(mutated[candidate_index], operation_for_apply, members=originals)
            issues = _structure_issues(mutated[candidate_index], candidate_index)
            if issues:
                mutated[candidate_index] = before_candidate
                record.update(
                    status="rejected",
                    rejection_reason="structural_validation_failed",
                    validation_issues=issues,
                    after_fingerprint=_member_fingerprint(mutated[candidate_index]),
                )
            else:
                record["status"] = "accepted"
                record["after_fingerprint"] = _member_fingerprint(mutated[candidate_index])
                records_by_candidate.setdefault(candidate_index, []).append(len(records))
        except Exception as exc:
            mutated[candidate_index] = before_candidate
            record.update(
                status="rejected",
                rejection_reason="application_failed",
                error_detail=_safe_error_text(exc),
                after=_affected_component_snapshot(mutated[candidate_index], operation_for_apply, members=originals),
                after_fingerprint=_member_fingerprint(mutated[candidate_index]),
            )
        records.append(record)

    for candidate_index, record_indices in records_by_candidate.items():
        issues = _structure_issues(mutated[candidate_index], candidate_index)
        engineering_ok, engineering_detail = _run_engineering_validator(
            engineering_validator,
            mutated[candidate_index],
            generation_context,
        )
        if not issues and engineering_ok:
            for record_index in record_indices:
                records[record_index]["engineering_validation"] = engineering_detail
            continue

        mutated[candidate_index] = copy.deepcopy(originals[candidate_index])
        reason = (
            "final_structural_validation_failed" if issues else "engineering_validation_failed"
        )
        for record_index in record_indices:
            records[record_index].update(
                status="rejected",
                rejection_reason=reason,
                rolled_back=True,
                final_validation_issues=issues,
                engineering_validation=engineering_detail,
                after_fingerprint=_member_fingerprint(mutated[candidate_index]),
            )

    return mutated, records


def _parse_plan(
    payload: Mapping[str, Any],
    request_id: str,
    member_count: int,
    max_total_operations: int,
    max_operations_per_candidate: int,
    candidate_bindings: Optional[Mapping[str, Any] | Sequence[Mapping[str, Any]]] = None,
    expected_generation: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    if not isinstance(payload, Mapping):
        raise MutationSchemaError("mutation plan must be a JSON object")
    unknown_root_keys = set(payload) - _ROOT_KEYS
    if unknown_root_keys:
        raise MutationSchemaError(
            "unknown mutation-plan fields: " + ", ".join(sorted(map(str, unknown_root_keys)))
        )
    required_root_keys = {"schema_version", "request_id"}
    missing = required_root_keys - set(payload)
    if missing:
        raise MutationSchemaError("missing mutation-plan fields: " + ", ".join(sorted(missing)))
    if payload["schema_version"] != MUTATION_SCHEMA_VERSION:
        raise MutationSchemaError("unsupported mutation-plan schema version")
    if payload["request_id"] != request_id:
        raise MutationSchemaError("mutation-plan request_id does not match the request")
    if "generation" in payload and payload["generation"] is not None and not _is_int(payload["generation"]):
        raise MutationSchemaError("generation must be an integer or null")
    if expected_generation is not None and payload.get("generation") not in (None, expected_generation):
        raise MutationSchemaError("mutation-plan generation does not match the request")
    rationale = payload.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise MutationSchemaError("rationale must be a string or null")

    has_operations = "operations" in payload
    has_plans = "plans" in payload
    if not has_operations and not has_plans:
        raise MutationSchemaError("mutation-plan requires operations or plans")
    raw_operations: list[Any] = []
    if has_operations:
        if not isinstance(payload["operations"], list):
            raise MutationSchemaError("operations must be an array")
        raw_operations.extend(payload["operations"])
    if has_plans:
        plans = payload["plans"]
        if not isinstance(plans, list):
            raise MutationSchemaError("plans must be an array")
        for plan_index, plan in enumerate(plans):
            if not isinstance(plan, Mapping):
                raise MutationSchemaError(f"plan {plan_index} must be an object")
            unknown_plan_keys = set(plan) - _PLAN_KEYS
            if unknown_plan_keys:
                raise MutationSchemaError(
                    "unknown plan fields: " + ", ".join(sorted(map(str, unknown_plan_keys)))
                )
            plan_operations = plan.get("operations", [])
            if not isinstance(plan_operations, list):
                raise MutationSchemaError(f"plan {plan_index} operations must be an array")
            plan_target = plan.get("target_candidate_id")
            plan_fingerprint = plan.get("base_fingerprint")
            plan_candidate_index = plan.get("candidate_index")
            plan_rationale = plan.get("rationale")
            if plan_target is not None and (
                not isinstance(plan_target, str) or not plan_target.strip()
            ):
                raise MutationSchemaError(
                    f"plan {plan_index} target_candidate_id must be a non-empty string"
                )
            if plan_fingerprint is not None and not isinstance(plan_fingerprint, str):
                raise MutationSchemaError(f"plan {plan_index} base_fingerprint must be a string")
            if plan_candidate_index is not None and not _is_int(plan_candidate_index):
                raise MutationSchemaError(f"plan {plan_index} candidate_index must be an integer")
            if plan_rationale is not None and not isinstance(plan_rationale, str):
                raise MutationSchemaError(f"plan {plan_index} rationale must be a string or null")
            for operation in plan_operations:
                if not isinstance(operation, Mapping):
                    raw_operations.append(operation)
                    continue
                enriched = dict(operation)
                if plan_target is not None:
                    if (
                        "target_candidate_id" in enriched
                        and enriched["target_candidate_id"] != plan_target
                    ):
                        raise MutationSchemaError(
                            f"plan {plan_index} operation target_candidate_id conflicts with the plan"
                        )
                    enriched.setdefault("target_candidate_id", plan_target)
                if plan_fingerprint is not None:
                    if (
                        "base_fingerprint" in enriched
                        and enriched["base_fingerprint"] != plan_fingerprint
                    ):
                        raise MutationSchemaError(
                            f"plan {plan_index} operation base_fingerprint conflicts with the plan"
                        )
                    enriched.setdefault("base_fingerprint", plan_fingerprint)
                if plan_candidate_index is not None:
                    if (
                        "candidate_index" in enriched
                        and enriched["candidate_index"] != plan_candidate_index
                    ):
                        raise MutationSchemaError(
                            f"plan {plan_index} operation candidate_index conflicts with the plan"
                        )
                    enriched.setdefault("candidate_index", plan_candidate_index)
                raw_operations.append(enriched)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    per_candidate_counts: dict[int, int] = {}
    for index, raw_operation in enumerate(raw_operations):
        try:
            operation = _normalize_operation(
                raw_operation,
                member_count,
                candidate_bindings=candidate_bindings,
            )
            candidate_index = operation["candidate_index"]
            if len(accepted) >= max_total_operations:
                raise MutationSchemaError("maximum total operation count exceeded")
            if per_candidate_counts.get(candidate_index, 0) >= max_operations_per_candidate:
                raise MutationSchemaError("maximum per-candidate operation count exceeded")
            per_candidate_counts[candidate_index] = per_candidate_counts.get(candidate_index, 0) + 1
            accepted.append(operation)
        except MutationSchemaError as exc:
            rejected.append(
                {
                    "operation_index": index,
                    "status": "rejected",
                    "rejection_reason": "schema_validation_failed",
                    "error_detail": _safe_error_text(exc),
                    "operation": to_builtin(raw_operation),
                }
            )
    return accepted, rejected, rationale[:2000] if rationale else None


def _normalize_operation(
    raw_operation: Any,
    member_count: int,
    *,
    candidate_bindings: Optional[Mapping[str, Any] | Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    if not isinstance(raw_operation, Mapping):
        raise MutationSchemaError("operation must be an object")
    unknown_keys = set(raw_operation) - _OPERATION_KEYS
    if unknown_keys:
        raise MutationSchemaError(
            "unknown operation fields: " + ", ".join(sorted(map(str, unknown_keys)))
        )
    required = {"round_index", "component", "operation", "params"}
    missing = required - set(raw_operation)
    if missing:
        raise MutationSchemaError("missing operation fields: " + ", ".join(sorted(missing)))

    candidate_index = raw_operation.get("candidate_index")
    target_candidate_id = raw_operation.get("target_candidate_id")
    base_fingerprint = raw_operation.get("base_fingerprint")
    bindings = _binding_entries(candidate_bindings)
    if candidate_index is None and target_candidate_id is not None:
        candidate_index = _binding_index_by_id(bindings, target_candidate_id)
        if candidate_index is None:
            raise MutationSchemaError("target_candidate_id is not bound to a candidate")
    if candidate_index is None:
        raise MutationSchemaError("candidate_index or target_candidate_id is required")
    round_index = raw_operation["round_index"]
    component = raw_operation["component"]
    operation_name = raw_operation["operation"]
    params = raw_operation["params"]
    reason = raw_operation.get("reason")

    if not _is_int(candidate_index) or not 0 <= candidate_index < member_count:
        raise MutationSchemaError("candidate_index is out of range")
    candidate_index = int(candidate_index)
    if target_candidate_id is not None and not isinstance(target_candidate_id, str):
        raise MutationSchemaError("target_candidate_id must be a string")
    if target_candidate_id is not None and not target_candidate_id.strip():
        raise MutationSchemaError("target_candidate_id must be a non-empty string")
    if base_fingerprint is not None and (
        not isinstance(base_fingerprint, str) or not base_fingerprint.strip()
    ):
        raise MutationSchemaError("base_fingerprint must be a non-empty string")
    if bindings:
        binding = bindings[candidate_index] if candidate_index < len(bindings) else None
        if binding is not None:
            bound_id = binding.get("candidate_id")
            bound_fingerprint = binding.get("fingerprint")
            if target_candidate_id is not None and str(target_candidate_id) != str(bound_id):
                raise MutationSchemaError("target_candidate_id does not match candidate_index")
            if base_fingerprint is not None and bound_fingerprint and base_fingerprint != bound_fingerprint:
                raise MutationSchemaError("base_fingerprint does not match candidate")
            target_candidate_id = str(bound_id)
            if base_fingerprint is None:
                base_fingerprint = bound_fingerprint
    if not _is_int(round_index) or round_index < 0:
        raise MutationSchemaError("round_index must be a non-negative integer")
    if not isinstance(operation_name, str) or operation_name not in _OPERATION_SPECS:
        raise MutationSchemaError("operation is not in the whitelist")
    specification = _OPERATION_SPECS[operation_name]
    if component != specification["component"]:
        raise MutationSchemaError("component does not match operation")
    if not isinstance(params, Mapping):
        raise MutationSchemaError("params must be an object")
    if specification["params"] is not None and set(params) != specification["params"]:
        raise MutationSchemaError("params do not exactly match the operation schema")
    if reason is not None and not isinstance(reason, str):
        raise MutationSchemaError("reason must be a string or null")

    normalized_params = _normalize_params(operation_name, params)
    return {
        "candidate_index": candidate_index,
        "target_candidate_id": target_candidate_id,
        "base_fingerprint": base_fingerprint,
        "round_index": round_index,
        "component": component,
        "operation": operation_name,
        "params": normalized_params,
        "reason": reason[:1000] if reason else None,
    }


def _normalize_params(operation_name: str, params: Mapping[str, Any]) -> dict[str, Any]:
    operation_name = _OPERATION_EXECUTION_NAMES.get(operation_name, operation_name)
    if operation_name == "swap_sbox_entries":
        result = {
            "sbox_index": _index(params["sbox_index"], 16, "sbox_index"),
            "entry_a": _index(params["entry_a"], 16, "entry_a"),
            "entry_b": _index(params["entry_b"], 16, "entry_b"),
        }
        if result["entry_a"] == result["entry_b"]:
            raise MutationSchemaError("entry_a and entry_b must differ")
        return result
    if operation_name == "replace_sbox":
        table = params["table"]
        if not isinstance(table, list) or len(table) != 16:
            raise MutationSchemaError("table must contain exactly 16 integers")
        if any(not _is_int(value) for value in table) or sorted(table) != list(range(16)):
            raise MutationSchemaError("table must be a permutation of integers 0..15")
        return {
            "sbox_index": _index(params["sbox_index"], 16, "sbox_index"),
            "table": list(table),
        }
    if operation_name == "swap_sbox_positions":
        result = {
            "sbox_a": _index(params["sbox_a"], 16, "sbox_a"),
            "sbox_b": _index(params["sbox_b"], 16, "sbox_b"),
        }
        if result["sbox_a"] == result["sbox_b"]:
            raise MutationSchemaError("sbox_a and sbox_b must differ")
        return result
    if operation_name == "swap_linear_rows":
        result = {
            "row_a": _index(params["row_a"], 64, "row_a"),
            "row_b": _index(params["row_b"], 64, "row_b"),
        }
        if result["row_a"] == result["row_b"]:
            raise MutationSchemaError("row_a and row_b must differ")
        return result
    if operation_name == "swap_linear_columns":
        result = {
            "column_a": _index(params["column_a"], 64, "column_a"),
            "column_b": _index(params["column_b"], 64, "column_b"),
        }
        if result["column_a"] == result["column_b"]:
            raise MutationSchemaError("column_a and column_b must differ")
        return result
    if operation_name == "copy_component":
        allowed = {"source_candidate_index", "source_candidate_id", "source_round_index", "source_component"}
        if set(params) - allowed or "source_round_index" not in params or "source_component" not in params:
            raise MutationSchemaError(
                "copy_component params require source_round_index, source_component, and one source candidate selector"
            )
        selectors = [key for key in ("source_candidate_index", "source_candidate_id") if key in params]
        if len(selectors) != 1:
            raise MutationSchemaError("copy_component requires exactly one source candidate selector")
        source_round_index = params["source_round_index"]
        if not _is_int(source_round_index) or source_round_index < 0:
            raise MutationSchemaError("source_round_index must be a non-negative integer")
        source_component = params["source_component"]
        if source_component not in {"sbox", "linear"}:
            raise MutationSchemaError("source_component must be sbox or linear")
        result = {
            "source_round_index": int(source_round_index),
            "source_component": source_component,
        }
        if selectors[0] == "source_candidate_index":
            if not _is_int(params[selectors[0]]) or int(params[selectors[0]]) < 0:
                raise MutationSchemaError("source_candidate_index must be a non-negative integer")
            result[selectors[0]] = int(params[selectors[0]])
        else:
            source_id = params[selectors[0]]
            if not isinstance(source_id, str) or not source_id.strip():
                raise MutationSchemaError("source_candidate_id must be a non-empty string")
            result[selectors[0]] = source_id
        return result
    if operation_name == "copy_round":
        allowed = {"source_candidate_index", "source_candidate_id", "source_round_index"}
        if set(params) - allowed or "source_round_index" not in params:
            raise MutationSchemaError(
                "copy_round params require source_round_index and one source candidate selector"
            )
        selectors = [key for key in ("source_candidate_index", "source_candidate_id") if key in params]
        if len(selectors) != 1:
            raise MutationSchemaError("copy_round requires exactly one source candidate selector")
        source_round_index = params["source_round_index"]
        if not _is_int(source_round_index) or source_round_index < 0:
            raise MutationSchemaError("source_round_index must be a non-negative integer")
        result = {"source_round_index": int(source_round_index)}
        if selectors[0] == "source_candidate_index":
            if not _is_int(params[selectors[0]]) or int(params[selectors[0]]) < 0:
                raise MutationSchemaError("source_candidate_index must be a non-negative integer")
            result[selectors[0]] = int(params[selectors[0]])
        else:
            source_id = params[selectors[0]]
            if not isinstance(source_id, str) or not source_id.strip():
                raise MutationSchemaError("source_candidate_id must be a non-empty string")
            result[selectors[0]] = source_id
        return result
    raise MutationSchemaError("operation is not in the whitelist")


def _apply_operation(
    member: Any, operation: Mapping[str, Any], members: Optional[Sequence[Any]] = None
) -> None:
    round_index = operation["round_index"]
    rounds = getattr(member, "round_functions", None)
    if not isinstance(rounds, list) or not 0 <= round_index < len(rounds):
        raise ValueError("round_index_out_of_range")
    round_function = rounds[round_index]
    operation_name = _OPERATION_EXECUTION_NAMES.get(operation["operation"], operation["operation"])
    params = operation["params"]

    if operation_name in {"swap_sbox_entries", "replace_sbox", "swap_sbox_positions"}:
        substitution = getattr(round_function, "substitution", None)
        sboxes = getattr(substitution, "sboxes", None)
        if not isinstance(sboxes, list) or len(sboxes) != 16:
            raise ValueError("invalid_substitution_layer")
        if operation_name == "swap_sbox_entries":
            table = list(sboxes[params["sbox_index"]])
            a, b = params["entry_a"], params["entry_b"]
            table[a], table[b] = table[b], table[a]
            sboxes[params["sbox_index"]] = table
        elif operation_name == "replace_sbox":
            sboxes[params["sbox_index"]] = list(params["table"])
        else:
            a, b = params["sbox_a"], params["sbox_b"]
            sboxes[a], sboxes[b] = copy.deepcopy(sboxes[b]), copy.deepcopy(sboxes[a])
        return
    if operation_name in {"copy_component", "copy_round"}:
        if members is None:
            raise ValueError("copy_operation_requires_member_population")
        source_index = _resolve_source_index(params, members)
        source_round_index = params.get("source_round_index")
        source_rounds = getattr(members[source_index], "round_functions", None)
        if not isinstance(source_rounds, list) or not 0 <= source_round_index < len(source_rounds):
            raise ValueError("source_round_index_out_of_range")
        source_round = source_rounds[source_round_index]
        if operation_name == "copy_round":
            copied_round = copy.deepcopy(source_round)
            copied_round.round_index = round_index
            rounds[round_index] = copied_round
            return
        source_component = params.get("source_component")
        if source_component == "sbox":
            member.round_functions[round_index].substitution = copy.deepcopy(source_round.substitution)
        elif source_component == "linear":
            if getattr(source_round, "linear", None) is None:
                raise ValueError("source_round_has_no_linear_layer")
            member.round_functions[round_index].linear = copy.deepcopy(source_round.linear)
        else:
            raise ValueError("unsupported_source_component")
        return

    linear = getattr(round_function, "linear", None)
    matrix = getattr(linear, "matrix", None) if linear is not None else None
    if matrix is None:
        raise ValueError("round_has_no_linear_layer")
    if operation_name == "swap_linear_rows":
        _swap_matrix_rows(matrix, params["row_a"], params["row_b"])
        return
    if operation_name == "swap_linear_columns":
        _swap_matrix_columns(matrix, params["column_a"], params["column_b"])
        return
    raise ValueError("operation_not_whitelisted")


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
        elif not _is_invertible_binary_rows(rows, 64):
            issues.append(
                {
                    "code": "linear_invertibility",
                    "message": f"round {round_index} linear matrix is singular",
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


def _candidate_prompt_payload(candidate: Any, index: int) -> dict[str, Any]:
    candidate_id = _candidate_id(candidate, index)
    contract_payload: Optional[dict[str, Any]] = None
    try:
        contract_payload = candidate_to_dict(
            candidate,
            candidate_id=candidate_id,
            metadata=_member_metrics(candidate),
            validate=True,
        )
    except Exception:
        contract_payload = None

    fingerprint = None
    if contract_payload is not None:
        fingerprint = contract_payload.get("fingerprint")
    if not fingerprint:
        try:
            fingerprint = candidate_fingerprint(contract_payload or _local_candidate_payload(candidate))
        except Exception:
            fingerprint = None

    rounds_payload = []
    for round_index, round_function in enumerate(getattr(candidate, "round_functions", [])):
        substitution = getattr(round_function, "substitution", None)
        sboxes = getattr(substitution, "sboxes", [])
        linear = getattr(round_function, "linear", None)
        matrix = getattr(linear, "matrix", None) if linear is not None else None
        rounds_payload.append(
            {
                "round_index": round_index,
                "sboxes": to_builtin(sboxes),
                "linear_rows_hex": _matrix_rows_hex(matrix) if matrix is not None else None,
            }
        )
    return {
        "candidate_index": index,
        "candidate_id": candidate_id,
        "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "metrics": _member_metrics(candidate),
        "num_rounds": len(rounds_payload),
        "rounds": rounds_payload,
    }


def _local_candidate_payload(
    candidate: Any,
    candidate_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    rounds = []
    for round_function in getattr(candidate, "round_functions", []):
        substitution = getattr(round_function, "substitution", None)
        linear = getattr(round_function, "linear", None)
        rounds.append(
            {
                "sboxes": to_builtin(getattr(substitution, "sboxes", [])),
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
        "You are the mutation planner for an SPN block-cipher genetic search. "
        "Analyze the whole generation and return exactly one JSON object, with no markdown or prose outside JSON. "
        f"The root schema is {{\"schema_version\":\"{MUTATION_SCHEMA_VERSION}\","
        "\"request_id\":<copy input request_id>,\"generation\":<input generation>,"
        "\"plans\":[{{\"target_candidate_id\":<id>,\"base_fingerprint\":<fingerprint>,"
        "\"operations\":[...]}}],\"rationale\":<string or null>}. "
        "Each plan targets one crossover child using its exact target_candidate_id and base_fingerprint. "
        "Each operation must contain round_index, component, operation, params, reason; candidate_index is optional inside plans. "
        "Allowed operations and exact params are: "
        "swap_sbox_entries/sbox {sbox_index,entry_a,entry_b}; "
        "replace_sbox/sbox {sbox_index,table}, where table is a permutation of 0..15; "
        "sbox_swap_entries/sbox {sbox_index,entry_a,entry_b}; "
        "sbox_replace or sbox_affine/sbox {sbox_index,table}; "
        "swap_sbox_positions/sbox {sbox_a,sbox_b}; "
        "swap_linear_rows/linear {row_a,row_b}; "
        "swap_linear_columns/linear {column_a,column_b}; "
        "copy_component/copy_component {source_candidate_index or source_candidate_id, source_round_index, source_component=sbox|linear}; "
        "copy_round {source_candidate_index or source_candidate_id, source_round_index}. "
        "Indices are zero-based. Do not target a final round with a linear operation. "
        f"Return at most {settings.max_total_operations} total operations and at most "
        f"{settings.max_operations_per_candidate} operations per candidate. "
        "Use the supplied security, validation, performance, diversity and population state when present. "
        "An empty operations array is valid when no defensible mutation exists."
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


def _affected_component_snapshot(
    member: Any,
    operation: Mapping[str, Any],
    members: Optional[Sequence[Any]] = None,
) -> Any:
    """Capture the target component before/after an operation for audit logs.

    Snapshots intentionally describe the target object only. Copy operations add
    their source selector so a log can reconstruct both provenance and the
    resulting change without serializing the entire population.
    """
    rounds = getattr(member, "round_functions", [])
    round_index = operation.get("round_index")
    if not _is_int(round_index) or not 0 <= round_index < len(rounds):
        return None
    round_function = rounds[round_index]
    operation_name = _OPERATION_EXECUTION_NAMES.get(
        operation.get("operation"), operation.get("operation")
    )
    params = operation.get("params", {})
    substitution = getattr(round_function, "substitution", None)
    sboxes = getattr(substitution, "sboxes", None)
    if operation_name in {"swap_sbox_entries", "replace_sbox"}:
        index = params.get("sbox_index")
        if not isinstance(sboxes, list) or not _is_int(index) or not 0 <= index < len(sboxes):
            return None
        return {"sbox_index": int(index), "table": to_builtin(sboxes[index])}
    if operation_name == "swap_sbox_positions":
        a, b = params.get("sbox_a"), params.get("sbox_b")
        if (not isinstance(sboxes, list) or not _is_int(a) or not _is_int(b)
                or not 0 <= a < len(sboxes) or not 0 <= b < len(sboxes)):
            return None
        return {"sbox_a": to_builtin(sboxes[a]), "sbox_b": to_builtin(sboxes[b])}
    if operation_name == "copy_component":
        source_component = params.get("source_component")
        target_value = (
            to_builtin(sboxes)
            if source_component == "sbox"
            else to_builtin(getattr(getattr(round_function, "linear", None), "matrix", None))
        )
        result = {
            "target_round_index": int(round_index),
            "target_component": source_component,
            "value": target_value,
        }
        if members is not None:
            try:
                source_index = _resolve_source_index(params, members)
                result["source_candidate_index"] = source_index
                result["source_round_index"] = params.get("source_round_index")
            except Exception:
                pass
        return result
    if operation_name == "copy_round":
        return {
            "round_index": int(round_index),
            "sboxes": to_builtin(sboxes),
            "linear_matrix": to_builtin(
                getattr(getattr(round_function, "linear", None), "matrix", None)
            ),
        }
    linear = getattr(round_function, "linear", None)
    matrix = getattr(linear, "matrix", None) if linear is not None else None
    if matrix is None:
        return None
    if operation_name == "swap_linear_rows":
        a, b = params.get("row_a"), params.get("row_b")
        if not _is_int(a) or not _is_int(b):
            return None
        try:
            return {"row_a": _binary_row_hex(matrix[a]), "row_b": _binary_row_hex(matrix[b])}
        except (IndexError, TypeError, ValueError):
            return None
    if operation_name == "swap_linear_columns":
        a, b = params.get("column_a"), params.get("column_b")
        if not _is_int(a) or not _is_int(b):
            return None
        try:
            return {
                "column_a": _matrix_column_bits(matrix, a),
                "column_b": _matrix_column_bits(matrix, b),
            }
        except (IndexError, TypeError, ValueError):
            return None
    return None


def _base_change_record(operation: Any, operation_index: int) -> dict[str, Any]:
    result = {
        "operation_index": operation_index,
        "status": "pending",
        "candidate_index": operation.get("candidate_index") if isinstance(operation, Mapping) else None,
        "round_index": operation.get("round_index") if isinstance(operation, Mapping) else None,
        "component": operation.get("component") if isinstance(operation, Mapping) else None,
        "operation": operation.get("operation") if isinstance(operation, Mapping) else None,
        "params": to_builtin(operation.get("params")) if isinstance(operation, Mapping) else None,
        "reason": operation.get("reason") if isinstance(operation, Mapping) else None,
        "target_candidate_id": operation.get("target_candidate_id") if isinstance(operation, Mapping) else None,
        "base_fingerprint": operation.get("base_fingerprint") if isinstance(operation, Mapping) else None,
    }
    return result


def _new_report(
    request_id: str, settings: DeepSeekSettings, member_count: int
) -> dict[str, Any]:
    return {
        "schema_version": MUTATION_SCHEMA_VERSION,
        "request_id": request_id,
        "status": "pending",
        "fallback_reason": None,
        "plans": [],
        "change_records": [],
        "provider": "deepseek",
        "model": settings.model,
        "candidate_count": member_count,
        "request_attempts": 0,
        "response_generation": None,
        "rationale": None,
        "accepted_count": 0,
        "rejected_count": 0,
        "started_at": _utc_now(),
        "finished_at": None,
    }


def _fallback(report: dict[str, Any], reason: str) -> dict[str, Any]:
    report["status"] = "fallback_noop"
    report["fallback_reason"] = reason
    report["finished_at"] = _utc_now()
    return report


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


def _candidate_bindings(members: Sequence[Any]) -> dict[str, Any]:
    """Return stable prompt-local candidate ID/fingerprint bindings.

    The LLM is allowed to address a child by either its positional index or its
    stable candidate ID.  The fingerprint is captured from the exact pre-request
    structure and is checked again before applying each operation.
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


def _resolve_source_index(params: Mapping[str, Any], members: Sequence[Any]) -> int:
    source_index = params.get("source_candidate_index")
    if source_index is not None:
        if not _is_int(source_index) or not 0 <= int(source_index) < len(members):
            raise ValueError("source_candidate_index_out_of_range")
        return int(source_index)
    source_id = params.get("source_candidate_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_candidate_selector_missing")
    matches = [
        index
        for index, member in enumerate(members)
        if _candidate_id(member, index) == source_id
    ]
    if len(matches) != 1:
        raise ValueError("source_candidate_id_not_found_or_ambiguous")
    return matches[0]


def _flatten_plan_payload(plan: Mapping[str, Any]) -> list[Any]:
    """Flatten canonical ``plans[]`` and legacy ``operations[]`` payloads."""

    raw_operations: list[Any] = []
    operations = plan.get("operations", [])
    if isinstance(operations, list):
        raw_operations.extend(operations)
    plans = plan.get("plans", [])
    if isinstance(plans, list):
        for plan_item in plans:
            if not isinstance(plan_item, Mapping):
                raw_operations.append(plan_item)
                continue
            plan_operations = plan_item.get("operations", [])
            if not isinstance(plan_operations, list):
                continue
            for operation in plan_operations:
                if not isinstance(operation, Mapping):
                    raw_operations.append(operation)
                    continue
                enriched = dict(operation)
                for key in ("target_candidate_id", "base_fingerprint", "candidate_index"):
                    if key in plan_item:
                        enriched.setdefault(key, plan_item[key])
                raw_operations.append(enriched)
    return raw_operations


def _plans_from_operations(
    operations: Sequence[Mapping[str, Any]],
    source_payload: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Group operations and preserve provider plan rationales in reports."""

    groups: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        key = (
            operation.get("target_candidate_id"),
            operation.get("base_fingerprint"),
            operation.get("candidate_index"),
        )
        group = groups.get(key)
        if group is None:
            group = {
                "target_candidate_id": operation.get("target_candidate_id"),
                "base_fingerprint": operation.get("base_fingerprint"),
                "candidate_index": operation.get("candidate_index"),
                "operations": [],
            }
            groups[key] = group
        group["operations"].append(
            {
                key_name: to_builtin(value)
                for key_name, value in operation.items()
                if key_name not in {"target_candidate_id", "base_fingerprint"}
            }
        )
    result = list(groups.values())
    source_plans = (
        source_payload.get("plans", [])
        if isinstance(source_payload, Mapping)
        else []
    )
    if isinstance(source_plans, list):
        for source_plan in source_plans:
            if not isinstance(source_plan, Mapping):
                continue
            target_id = source_plan.get("target_candidate_id")
            fingerprint = source_plan.get("base_fingerprint")
            candidate_index = source_plan.get("candidate_index")
            matches = [
                group
                for group in result
                if (target_id is None or group.get("target_candidate_id") == target_id)
                and (
                    fingerprint is None
                    or group.get("base_fingerprint") == fingerprint
                )
                and (
                    candidate_index is None
                    or group.get("candidate_index") == candidate_index
                )
            ]
            if matches:
                if "rationale" in source_plan:
                    matches[0]["rationale"] = source_plan.get("rationale")
                continue
            # Preserve an empty plan as evidence that the model intentionally
            # proposed no mutation for that candidate.
            result.append(
                {
                    "target_candidate_id": target_id,
                    "base_fingerprint": fingerprint,
                    "candidate_index": candidate_index,
                    "operations": [],
                    "rationale": source_plan.get("rationale"),
                }
            )
    return result


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


def _matrix_rows_hex(matrix: Any) -> Optional[list[str]]:
    rows = _matrix_as_binary_rows(matrix)
    if rows is None:
        return None
    return [format(row, "016x") for row in rows]


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
                if _is_int(value):
                    bit = int(value)
                elif hasattr(value, "item"):
                    bit = int(value.item())
                else:
                    return None
                if bit not in (0, 1):
                    return None
                row = (row << 1) | bit
            rows.append(row)
        return rows
    except (TypeError, ValueError):
        return None


def _is_invertible_binary_rows(rows: Sequence[int], size: int) -> bool:
    work = list(rows)
    rank = 0
    for column in range(size - 1, -1, -1):
        pivot = next((index for index in range(rank, size) if (work[index] >> column) & 1), None)
        if pivot is None:
            continue
        work[rank], work[pivot] = work[pivot], work[rank]
        for row_index in range(size):
            if row_index != rank and ((work[row_index] >> column) & 1):
                work[row_index] ^= work[rank]
        rank += 1
        if rank == size:
            return True
    return False


def _swap_matrix_rows(matrix: Any, a: int, b: int) -> None:
    if hasattr(matrix, "shape") and hasattr(matrix, "copy"):
        temporary = matrix[a].copy()
        matrix[a] = matrix[b]
        matrix[b] = temporary
        return
    matrix[a], matrix[b] = list(matrix[b]), list(matrix[a])


def _swap_matrix_columns(matrix: Any, a: int, b: int) -> None:
    if hasattr(matrix, "shape") and hasattr(matrix, "copy"):
        temporary = matrix[:, a].copy()
        matrix[:, a] = matrix[:, b]
        matrix[:, b] = temporary
        return
    for row in matrix:
        row[a], row[b] = row[b], row[a]


def _binary_row_hex(row: Any) -> str:
    value = 0
    for bit in list(row):
        value = (value << 1) | int(bit)
    return format(value, "016x")


def _matrix_column_bits(matrix: Any, column: int) -> str:
    return "".join(str(int(row[column])) for row in matrix)


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


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    parsed = float(value)
    return max(minimum, min(maximum, parsed))


__all__ = [
    "DeepSeekMutationAdvisor",
    "DeepSeekSettings",
    "MUTATION_SCHEMA_VERSION",
    "MutationSchemaError",
    "apply_mutation_plan",
    "mutate_generation",
]
