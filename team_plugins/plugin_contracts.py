"""Stable data contracts shared by the uKNIT orchestration and team plugins.

The plugin boundary deliberately uses JSON-compatible dictionaries instead of
``Member`` instances.  This keeps Team B and Team C implementations independent
from the framework's internal classes and makes requests, responses and logs
portable across processes.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_VERSION = "1.0"
PLUGIN_API_VERSION = "1.0"

RESULT_TYPES = frozenset({"security", "validation", "performance"})
RESULT_STATUSES = frozenset({"ok", "unavailable", "error"})


class ContractError(ValueError):
    """Raised when a candidate or plugin result violates the public contract."""

    def __init__(self, message: str, issues: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.issues = issues or []


def to_builtin(value: Any) -> Any:
    """Recursively convert common Python/NumPy values to JSON-safe builtins."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError("Non-finite floats are not valid plugin data")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return to_builtin(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [to_builtin(item) for item in value]
        return sorted(converted, key=lambda item: canonical_json(item))

    # NumPy scalars and arrays expose item()/tolist() without requiring NumPy
    # as a hard dependency of the contract module.
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return to_builtin(item_method())
        except (TypeError, ValueError):
            pass
    tolist_method = getattr(value, "tolist", None)
    if callable(tolist_method):
        return to_builtin(tolist_method())

    raise ContractError(f"Unsupported value in plugin data: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return deterministic JSON used for fingerprints and audit records."""

    return json.dumps(
        to_builtin(value),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _issue(code: str, message: str, path: str = "$") -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def _extract_sboxes(round_value: Any) -> Any:
    if isinstance(round_value, Mapping):
        if "sboxes" in round_value:
            return round_value["sboxes"]
        substitution = round_value.get("substitution")
    else:
        substitution = getattr(round_value, "substitution", None)
        if substitution is None and hasattr(round_value, "sboxes"):
            return getattr(round_value, "sboxes")

    if isinstance(substitution, Mapping):
        return substitution.get("sboxes")
    return getattr(substitution, "sboxes", None)


def _extract_linear_matrix(round_value: Any) -> Any:
    if isinstance(round_value, Mapping):
        if "linear_matrix" in round_value:
            return round_value["linear_matrix"]
        linear = round_value.get("linear")
    else:
        linear = getattr(round_value, "linear", None)

    if linear is None:
        return None
    if isinstance(linear, Mapping):
        return linear.get("matrix")
    return getattr(linear, "matrix", linear)


def _candidate_structure(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "num_rounds": candidate.get("num_rounds"),
        "rounds": candidate.get("rounds"),
    }


def candidate_fingerprint(candidate: Any) -> str:
    """Fingerprint only the cipher structure, excluding ids and measurements."""

    if not isinstance(candidate, Mapping):
        # Accept framework ``Member`` objects as a convenience, while keeping
        # the actual fingerprint input limited to the stable JSON structure.
        candidate = candidate_to_dict(candidate, validate=False)
    encoded = canonical_json(_candidate_structure(candidate)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_metadata(candidate: Any, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(candidate, Mapping):
        source = candidate.get("metadata", {})
        if isinstance(source, Mapping):
            result.update(to_builtin(source))
    else:
        for output_name, attribute_name in (
            ("generation_index", "gen_index"),
            ("population_index", "pop_index"),
            ("identifier", "identifier"),
        ):
            value = getattr(candidate, attribute_name, None)
            if value is not None:
                result[output_name] = to_builtin(value)
    if metadata:
        result.update(to_builtin(metadata))
    return result


def candidate_to_dict(
    candidate: Any,
    candidate_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Normalize a framework ``Member`` or mapping to the stable candidate schema."""

    if isinstance(candidate, Mapping):
        rounds_source = candidate.get("rounds", [])
        declared_rounds = candidate.get("num_rounds")
        source_id = candidate.get("candidate_id")
    else:
        rounds_source = getattr(candidate, "round_functions", [])
        declared_rounds = getattr(candidate, "num_rounds", None)
        source_id = getattr(candidate, "candidate_id", None)

    if rounds_source is None:
        rounds_source = []
    try:
        rounds_source = list(rounds_source)
    except TypeError as exc:
        raise ContractError("Candidate rounds must be a sequence") from exc

    rounds: list[dict[str, Any]] = []
    for index, round_value in enumerate(rounds_source):
        round_index = (
            round_value.get("round_index", index)
            if isinstance(round_value, Mapping)
            else getattr(round_value, "round_index", index)
        )
        if round_index is None:
            round_index = index
        sboxes = _extract_sboxes(round_value)
        matrix = _extract_linear_matrix(round_value)
        rounds.append(
            {
                "round_index": to_builtin(round_index),
                "sboxes": to_builtin(sboxes) if sboxes is not None else None,
                "linear_matrix": to_builtin(matrix) if matrix is not None else None,
            }
        )

    num_rounds = len(rounds) if declared_rounds is None else to_builtin(declared_rounds)
    candidate_metadata = _candidate_metadata(candidate, metadata)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": "",
        "fingerprint": "",
        "num_rounds": num_rounds,
        "rounds": rounds,
        "metadata": candidate_metadata,
    }
    payload["fingerprint"] = candidate_fingerprint(payload)

    resolved_id = candidate_id or source_id
    if not resolved_id:
        generation_index = candidate_metadata.get("generation_index")
        population_index = candidate_metadata.get("population_index")
        if isinstance(generation_index, int) and isinstance(population_index, int):
            resolved_id = (
                f"r{int(num_rounds):02d}-g{generation_index:04d}-p{population_index:04d}"
            )
        else:
            resolved_id = f"candidate-{payload['fingerprint'][:12]}"
    payload["candidate_id"] = str(resolved_id)

    if validate:
        return ensure_valid_candidate(payload)
    return payload


def normalize_candidate(
    candidate: Any,
    candidate_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Public convenience alias for validated candidate serialization."""

    return candidate_to_dict(
        candidate,
        candidate_id=candidate_id,
        metadata=metadata,
        validate=True,
    )


def candidate_from_member(member: Any) -> dict[str, Any]:
    """Backward-compatible named adapter for framework ``Member`` objects."""

    return candidate_to_dict(member, validate=True)


def _is_binary_matrix(matrix: Any, size: int = 64) -> bool:
    if not isinstance(matrix, list) or len(matrix) != size:
        return False
    return all(
        isinstance(row, list)
        and len(row) == size
        and all(isinstance(value, int) and not isinstance(value, bool) and value in (0, 1) for value in row)
        for row in matrix
    )


def _is_invertible_binary_matrix(matrix: list[list[int]]) -> bool:
    """Check invertibility over GF(2) with compact integer Gaussian elimination."""

    size = len(matrix)
    rows = []
    for row in matrix:
        packed = 0
        for value in row:
            packed = (packed << 1) | value
        rows.append(packed)

    rank = 0
    for column in range(size):
        bit = 1 << (size - column - 1)
        pivot = next((index for index in range(rank, size) if rows[index] & bit), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        for index in range(size):
            if index != rank and rows[index] & bit:
                rows[index] ^= rows[rank]
        rank += 1
    return rank == size


def _is_orthogonal_binary_matrix(matrix: list[list[int]]) -> bool:
    """Check ``M^T M = I`` over GF(2) using packed matrix columns."""

    if not _is_binary_matrix(matrix):
        return False
    size = len(matrix)
    columns = []
    for column in range(size):
        packed = 0
        for row in matrix:
            packed = (packed << 1) | row[column]
        columns.append(packed)
    return all(
        (bin(columns[left] & columns[right]).count('1') % 2)
        == int(left == right)
        for left in range(size)
        for right in range(size)
    )


def _has_regular_linear_weight(matrix: list[list[int]], weight: int = 3) -> bool:
    """Check the uKNIT-BC row and column Hamming-weight invariant."""

    return (
        all(sum(row) == weight for row in matrix)
        and all(
            sum(matrix[row][column] for row in range(len(matrix))) == weight
            for column in range(len(matrix))
        )
    )


def validate_candidate_payload(
    candidate: Any,
    require_invertible: bool = True,
    check_fingerprint: bool = True,
) -> list[dict[str, Any]]:
    """Return structured contract issues; an empty list means the candidate is valid."""

    if not isinstance(candidate, Mapping):
        try:
            candidate = candidate_to_dict(candidate, validate=False)
        except (ContractError, TypeError, ValueError) as exc:
            return [_issue("candidate_not_serializable", str(exc))]
    else:
        try:
            candidate = to_builtin(candidate)
        except ContractError as exc:
            return [_issue("candidate_not_serializable", str(exc))]

    issues: list[dict[str, Any]] = []
    if candidate.get("schema_version") != SCHEMA_VERSION:
        issues.append(
            _issue(
                "schema_version_mismatch",
                f"Expected schema_version {SCHEMA_VERSION!r}",
                "$.schema_version",
            )
        )

    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        issues.append(_issue("invalid_candidate_id", "candidate_id must be a non-empty string", "$.candidate_id"))

    num_rounds = candidate.get("num_rounds")
    if not isinstance(num_rounds, int) or isinstance(num_rounds, bool) or num_rounds <= 0:
        issues.append(_issue("invalid_num_rounds", "num_rounds must be a positive integer", "$.num_rounds"))

    rounds = candidate.get("rounds")
    if not isinstance(rounds, list):
        issues.append(_issue("invalid_rounds", "rounds must be a list", "$.rounds"))
        rounds = []
    elif isinstance(num_rounds, int) and len(rounds) != num_rounds:
        issues.append(
            _issue(
                "round_count_mismatch",
                "num_rounds does not match the number of rounds",
                "$.rounds",
            )
        )

    for round_position, round_value in enumerate(rounds):
        round_path = f"$.rounds[{round_position}]"
        if not isinstance(round_value, Mapping):
            issues.append(_issue("invalid_round", "Each round must be an object", round_path))
            continue
        if round_value.get("round_index") != round_position:
            issues.append(
                _issue(
                    "invalid_round_index",
                    "round_index must match its position",
                    f"{round_path}.round_index",
                )
            )

        sboxes = round_value.get("sboxes")
        if not isinstance(sboxes, list) or len(sboxes) != 16:
            issues.append(
                _issue(
                    "invalid_sbox_layer",
                    "Each substitution layer must contain exactly 16 S-boxes",
                    f"{round_path}.sboxes",
                )
            )
        else:
            expected = list(range(16))
            for sbox_index, sbox in enumerate(sboxes):
                sbox_path = f"{round_path}.sboxes[{sbox_index}]"
                if not isinstance(sbox, list) or len(sbox) != 16:
                    issues.append(
                        _issue(
                            "invalid_sbox_size",
                            "Each S-box must contain exactly 16 entries",
                            sbox_path,
                        )
                    )
                    continue
                if any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    or value > 15
                    for value in sbox
                ):
                    issues.append(
                        _issue(
                            "invalid_sbox_value",
                            "S-box entries must be integers in the range 0..15",
                            sbox_path,
                        )
                    )
                elif sorted(sbox) != expected:
                    issues.append(
                        _issue(
                            "non_bijective_sbox",
                            "Each S-box must be a permutation of 0..15",
                            sbox_path,
                        )
                    )

        matrix = round_value.get("linear_matrix")
        is_last = round_position == len(rounds) - 1
        if is_last:
            if matrix is not None:
                issues.append(
                    _issue(
                        "last_round_linear_must_be_null",
                        "The last round must not contain a linear layer",
                        f"{round_path}.linear_matrix",
                    )
                )
        elif matrix is None:
            issues.append(
                _issue(
                    "missing_linear_matrix",
                    "Every non-final round requires a linear matrix",
                    f"{round_path}.linear_matrix",
                )
            )
        elif not _is_binary_matrix(matrix):
            issues.append(
                _issue(
                    "invalid_linear_matrix",
                    "linear_matrix must be a 64x64 binary integer matrix",
                    f"{round_path}.linear_matrix",
                )
            )
        elif not _has_regular_linear_weight(matrix, 3):
            issues.append(
                _issue(
                    "invalid_linear_weight",
                    "linear_matrix must have exactly three 1s in every row and column",
                    f"{round_path}.linear_matrix",
                )
            )
        elif not _is_orthogonal_binary_matrix(matrix):
            issues.append(
                _issue(
                    "non_orthogonal_linear_matrix",
                    "linear_matrix must satisfy M^T M = I over GF(2)",
                    f"{round_path}.linear_matrix",
                )
            )
        elif require_invertible and not _is_invertible_binary_matrix(matrix):
            issues.append(
                _issue(
                    "singular_linear_matrix",
                    "linear_matrix must be invertible over GF(2)",
                    f"{round_path}.linear_matrix",
                )
            )

    if check_fingerprint and isinstance(candidate.get("fingerprint"), str):
        try:
            expected_fingerprint = candidate_fingerprint(candidate)
        except ContractError as exc:
            issues.append(_issue("fingerprint_error", str(exc), "$.fingerprint"))
        else:
            if candidate["fingerprint"] != expected_fingerprint:
                issues.append(
                    _issue(
                        "fingerprint_mismatch",
                        "fingerprint does not match the normalized cipher structure",
                        "$.fingerprint",
                    )
                )
    elif check_fingerprint:
        issues.append(_issue("missing_fingerprint", "fingerprint must be a string", "$.fingerprint"))

    metadata = candidate.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        issues.append(_issue("invalid_metadata", "metadata must be an object", "$.metadata"))
    return issues


def ensure_valid_candidate(
    candidate: Any,
    require_invertible: bool = True,
    check_fingerprint: bool = True,
) -> dict[str, Any]:
    """Return a JSON-safe candidate or raise ``ContractError`` with all issues."""

    payload = to_builtin(candidate)
    issues = validate_candidate_payload(
        payload,
        require_invertible=require_invertible,
        check_fingerprint=check_fingerprint,
    )
    if issues:
        raise ContractError("Candidate does not satisfy the plugin contract", issues)
    return payload


def _normalise_messages(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return to_builtin(value)
    return [to_builtin(value)]


def _normalise_metric_group(group: Any, fallback_weights: Any = None) -> dict[str, Any]:
    if isinstance(group, Mapping):
        weights = group.get("weights", fallback_weights)
        trails = group.get("trails", [])
    elif isinstance(group, Sequence) and not isinstance(group, (str, bytes, bytearray)):
        weights = group
        trails = []
    else:
        weights = fallback_weights
        trails = []
    if weights is None:
        weights = []
    return {"weights": to_builtin(weights), "trails": to_builtin(trails)}


def normalize_plugin_result(
    result: Any,
    result_type: str,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Normalize and validate a Team B/C result into one of three result schemas."""

    if result_type not in RESULT_TYPES:
        raise ContractError(f"Unknown plugin result type: {result_type!r}")

    if result_type == "validation" and isinstance(result, bool):
        result = {"valid": result, "status": "ok" if result else "error"}
    elif result_type == "validation" and isinstance(result, list):
        result = {"valid": not result, "errors": result, "status": "ok" if not result else "error"}
    if not isinstance(result, Mapping):
        raise ContractError(f"{result_type} plugin result must be a mapping")

    raw = to_builtin(result)
    status = raw.get("status")
    if status is None:
        if result_type == "validation":
            status = "ok" if raw.get("valid", False) else "error"
        else:
            status = "ok"

    common = {
        "schema_version": raw.get("schema_version", SCHEMA_VERSION),
        "plugin_api_version": raw.get("plugin_api_version", PLUGIN_API_VERSION),
        "plugin_name": str(raw.get("plugin_name", f"unknown-{result_type}-plugin")),
        "candidate_id": str(candidate_id or raw.get("candidate_id") or "unknown"),
        "status": status,
        "warnings": _normalise_messages(raw.get("warnings")),
        "artifacts": to_builtin(raw.get("artifacts", {})),
    }

    if result_type == "security":
        common.update(
            {
                "ok": status == "ok",
                "differential": _normalise_metric_group(
                    raw.get("differential"), raw.get("security_diff")
                ),
                "linear": _normalise_metric_group(
                    raw.get("linear"), raw.get("security_linear")
                ),
                "errors": _normalise_messages(raw.get("errors")),
            }
        )
    elif result_type == "validation":
        valid = bool(raw.get("valid", raw.get("ok", raw.get("passed", status == "ok"))))
        common.update(
            {
                "valid": valid,
                "errors": _normalise_messages(raw.get("errors", raw.get("issues"))),
            }
        )
    else:
        metrics = raw.get("metrics", {})
        if not isinstance(metrics, Mapping):
            raise ContractError("performance.metrics must be a mapping")
        metrics = dict(to_builtin(metrics))
        for metric_name in ("latency", "area", "energy"):
            if metric_name not in metrics and metric_name in raw:
                metrics[metric_name] = raw[metric_name]
        metrics.setdefault("latency", None)
        metrics.setdefault("area", None)
        metrics.setdefault("energy", None)
        metrics.setdefault("units", {})
        common.update(
            {
                "valid": bool(raw.get("valid", status != "error")),
                "metrics": metrics,
                "errors": _normalise_messages(raw.get("errors")),
            }
        )

    issues = validate_plugin_result(common, result_type)
    if issues:
        raise ContractError(f"Invalid {result_type} plugin result", issues)
    return common


def validate_plugin_result(result: Any, result_type: str) -> list[dict[str, Any]]:
    """Return structured issues for a normalized plugin result."""

    if result_type not in RESULT_TYPES:
        return [_issue("unknown_result_type", f"Unknown result type: {result_type!r}")]
    if not isinstance(result, Mapping):
        return [_issue("result_not_object", "Plugin result must be an object")]

    issues: list[dict[str, Any]] = []
    if result.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue("schema_version_mismatch", f"Expected {SCHEMA_VERSION!r}", "$.schema_version"))
    if result.get("plugin_api_version") != PLUGIN_API_VERSION:
        issues.append(_issue("plugin_api_version_mismatch", f"Expected {PLUGIN_API_VERSION!r}", "$.plugin_api_version"))
    if result.get("status") not in RESULT_STATUSES:
        issues.append(_issue("invalid_status", f"status must be one of {sorted(RESULT_STATUSES)}", "$.status"))
    if not isinstance(result.get("candidate_id"), str) or not result.get("candidate_id"):
        issues.append(_issue("invalid_candidate_id", "candidate_id must be a non-empty string", "$.candidate_id"))
    if not isinstance(result.get("warnings"), list):
        issues.append(_issue("invalid_warnings", "warnings must be a list", "$.warnings"))
    if not isinstance(result.get("errors"), list):
        issues.append(_issue("invalid_errors", "errors must be a list", "$.errors"))
    if not isinstance(result.get("artifacts"), Mapping):
        issues.append(_issue("invalid_artifacts", "artifacts must be an object", "$.artifacts"))

    if result_type == "security":
        for group_name in ("differential", "linear"):
            group = result.get(group_name)
            if not isinstance(group, Mapping):
                issues.append(_issue("invalid_metric_group", f"{group_name} must be an object", f"$.{group_name}"))
                continue
            weights = group.get("weights")
            if not isinstance(weights, list) or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                for value in weights
            ):
                issues.append(_issue("invalid_weights", "weights must be a list of finite numbers", f"$.{group_name}.weights"))
            if not isinstance(group.get("trails"), list):
                issues.append(_issue("invalid_trails", "trails must be a list", f"$.{group_name}.trails"))
    elif result_type == "validation":
        if not isinstance(result.get("valid"), bool):
            issues.append(_issue("invalid_valid_flag", "valid must be boolean", "$.valid"))
    else:
        if not isinstance(result.get("valid"), bool):
            issues.append(_issue("invalid_valid_flag", "valid must be boolean", "$.valid"))
        metrics = result.get("metrics")
        if not isinstance(metrics, Mapping):
            issues.append(_issue("invalid_metrics", "metrics must be an object", "$.metrics"))
        else:
            for metric_name in ("latency", "area", "energy"):
                value = metrics.get(metric_name)
                if value is not None and (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(float(value))
                ):
                    issues.append(_issue("invalid_metric", f"{metric_name} must be finite or null", f"$.metrics.{metric_name}"))
            if not isinstance(metrics.get("units"), Mapping):
                issues.append(_issue("invalid_units", "metrics.units must be an object", "$.metrics.units"))
    return issues


def unavailable_result(
    result_type: str,
    candidate_id: str | None,
    message: str,
    plugin_name: str,
) -> dict[str, Any]:
    """Construct a valid placeholder result when a Team B/C plugin is absent."""

    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plugin_api_version": PLUGIN_API_VERSION,
        "plugin_name": plugin_name,
        "candidate_id": candidate_id or "unknown",
        "status": "unavailable",
        "warnings": [message],
        "errors": [],
        "artifacts": {},
    }
    if result_type == "security":
        base.update(
            {
                "differential": {"weights": [0], "trails": []},
                "linear": {"weights": [0], "trails": []},
            }
        )
    elif result_type == "validation":
        base["valid"] = False
    elif result_type == "performance":
        base.update(
            {
                "valid": True,
                "metrics": {
                    "latency": 1.0,
                    "area": None,
                    "energy": None,
                    "units": {"latency": "placeholder"},
                },
            }
        )
    else:
        raise ContractError(f"Unknown plugin result type: {result_type!r}")
    return normalize_plugin_result(base, result_type, candidate_id=candidate_id)


__all__ = [
    "SCHEMA_VERSION",
    "PLUGIN_API_VERSION",
    "RESULT_TYPES",
    "RESULT_STATUSES",
    "ContractError",
    "to_builtin",
    "canonical_json",
    "candidate_fingerprint",
    "candidate_to_dict",
    "candidate_from_member",
    "normalize_candidate",
    "validate_candidate_payload",
    "ensure_valid_candidate",
    "normalize_plugin_result",
    "validate_plugin_result",
    "unavailable_result",
]
