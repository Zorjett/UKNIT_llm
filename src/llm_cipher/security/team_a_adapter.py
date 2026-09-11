"""Team A Plugin API 1.0 adapter for the three-to-twelve-round B core.

This module deliberately does not change the independent B implementation.
It converts Team A's dense candidate payload into CandidateSpec v0.2, runs the
existing evaluator, and only exposes weights when both analyses are proven
optimal.  Unsupported round counts remain explicit and never become scores.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Mapping

from . import cli
from .candidate_reader import (
    BLOCK_SIZE,
    ROUND_LAYOUT,
    SCHEMA_VERSION as B_CANDIDATE_SCHEMA_VERSION,
    STATE_ENCODING,
    CandidateValidationError,
    MAX_NUM_ROUNDS,
    MIN_NUM_ROUNDS,
    candidate_hash,
    validate_candidate,
)
from .model_adapter import MODEL_VERSION
from .reports import (
    EVALUATOR_VERSION,
    ReportValidationError,
    atomic_write_text,
    reusable_cache_hit,
    security_cache_key,
    validate_security_report,
    write_security_report,
)


PLUGIN_API_VERSION = "1.0"
PLUGIN_NAME = "team-b-security-multiround-adapter"
TEAM_A_SCHEMA_VERSION = "1.0"
SUPPORTED_ROUNDS = tuple(range(MIN_NUM_ROUNDS, MAX_NUM_ROUNDS + 1))
SUPPORTED_MODES = ("differential", "linear")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SECURITY_CACHE_DIRECTORY_PREFIX_LENGTH = 16


class TeamAAdapterError(ValueError):
    """A field-addressable Team A/B boundary error."""

    def __init__(self, code: str, field: str, message: str):
        super().__init__(f"{code} at {field}: {message}")
        self.code = code
        self.field = field
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


def _fail(code: str, field: str, message: str) -> None:
    raise TeamAAdapterError(code, field, message)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def team_a_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Recompute the fingerprint used by Team A's plugin_contracts.py."""

    structure = {
        "schema_version": TEAM_A_SCHEMA_VERSION,
        "num_rounds": candidate.get("num_rounds"),
        "rounds": candidate.get("rounds"),
    }
    return hashlib.sha256(_canonical_json(structure).encode("utf-8")).hexdigest()


def _candidate_id(candidate: object) -> str:
    if isinstance(candidate, Mapping):
        value = candidate.get("candidate_id")
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _plugin_result(
    candidate_id: str,
    status: str,
    *,
    differential_weight: float = 0.0,
    linear_weight: float = 0.0,
    warnings: list[object] | None = None,
    errors: list[object] | None = None,
    artifacts: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": TEAM_A_SCHEMA_VERSION,
        "plugin_api_version": PLUGIN_API_VERSION,
        "plugin_name": PLUGIN_NAME,
        "candidate_id": candidate_id,
        "status": status,
        "warnings": list(warnings or []),
        "errors": list(errors or []),
        "artifacts": dict(artifacts or {}),
        "differential": {"weights": [float(differential_weight)], "trails": []},
        "linear": {"weights": [float(linear_weight)], "trails": []},
    }


def _dense_matrix_to_sparse_rows(matrix: object, field: str) -> list[list[int]]:
    if not isinstance(matrix, list) or len(matrix) != BLOCK_SIZE:
        _fail("invalid_linear_matrix", field, "must be a 64x64 binary matrix")
    sparse_rows: list[list[int]] = []
    for row_index, row in enumerate(matrix):
        row_field = f"{field}[{row_index}]"
        if not isinstance(row, list) or len(row) != BLOCK_SIZE:
            _fail("invalid_linear_matrix", row_field, "must contain 64 entries")
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value not in (0, 1)
            for value in row
        ):
            _fail("invalid_linear_matrix", row_field, "entries must be integer 0 or 1")
        sparse_rows.append([index for index, value in enumerate(row) if value == 1])
    return sparse_rows


def convert_team_a_candidate(candidate: object) -> dict[str, Any]:
    """Convert and independently validate one supported Team A candidate."""

    if not isinstance(candidate, Mapping):
        _fail("invalid_candidate", "$", "candidate must be an object")
    candidate_id = _candidate_id(candidate)
    if candidate_id == "unknown":
        _fail("invalid_candidate_id", "candidate_id", "must be a non-empty string")
    if candidate.get("schema_version") != TEAM_A_SCHEMA_VERSION:
        _fail(
            "unsupported_schema_version",
            "schema_version",
            f"expected {TEAM_A_SCHEMA_VERSION}",
        )

    num_rounds = candidate.get("num_rounds")
    if not isinstance(num_rounds, int) or isinstance(num_rounds, bool):
        _fail("invalid_num_rounds", "num_rounds", "must be an integer")
    if num_rounds not in SUPPORTED_ROUNDS:
        _fail(
            "unsupported_num_rounds",
            "num_rounds",
            f"supported round counts are {SUPPORTED_ROUNDS}",
        )

    rounds = candidate.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != num_rounds:
        _fail("round_count_mismatch", "rounds", "length must equal num_rounds")

    supplied_fingerprint = candidate.get("fingerprint")
    recomputed_fingerprint = team_a_fingerprint(candidate)
    if (
        not isinstance(supplied_fingerprint, str)
        or not _HEX_DIGEST.fullmatch(supplied_fingerprint)
        or supplied_fingerprint != recomputed_fingerprint
    ):
        _fail(
            "fingerprint_mismatch",
            "fingerprint",
            f"expected {recomputed_fingerprint}",
        )

    converted_rounds: list[dict[str, Any]] = []
    for round_index, round_value in enumerate(rounds):
        field = f"rounds[{round_index}]"
        if not isinstance(round_value, Mapping):
            _fail("invalid_round", field, "round must be an object")
        if round_value.get("round_index") != round_index:
            _fail("invalid_round_index", f"{field}.round_index", "must match its position")
        sboxes = round_value.get("sboxes")
        matrix = round_value.get("linear_matrix")
        if round_index == num_rounds - 1:
            if matrix is not None:
                _fail(
                    "unexpected_final_linear_layer",
                    f"{field}.linear_matrix",
                    "final round must omit the linear layer",
                )
            sparse_rows = None
        else:
            sparse_rows = _dense_matrix_to_sparse_rows(
                matrix, f"{field}.linear_matrix"
            )
        converted_rounds.append({"sboxes": sboxes, "linear_rows": sparse_rows})

    converted: dict[str, Any] = {
        "schema_version": B_CANDIDATE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "candidate_hash": "",
        "block_size": BLOCK_SIZE,
        "num_rounds": num_rounds,
        "state_encoding": dict(STATE_ENCODING),
        "round_layout": ROUND_LAYOUT,
        "rounds": converted_rounds,
    }
    converted["candidate_hash"] = candidate_hash(converted)
    return validate_candidate(converted)


def _positive_float(raw: object, name: str, default: float) -> float:
    value = default if raw in (None, "") else raw
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TeamAAdapterError("invalid_configuration", name, "must be numeric") from exc
    if not math.isfinite(result) or result <= 0:
        _fail("invalid_configuration", name, "must be finite and greater than zero")
    return result


def _nonnegative_int(raw: object, name: str, default: int) -> int:
    value = default if raw in (None, "") else raw
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TeamAAdapterError("invalid_configuration", name, "must be an integer") from exc
    if result < 0:
        _fail("invalid_configuration", name, "must be non-negative")
    return result


def _option(
    context: Mapping[str, Any], nested: Mapping[str, Any], key: str, env_name: str
) -> object | None:
    if key in nested:
        return nested[key]
    environment_value = os.getenv(env_name)
    if environment_value is not None:
        return environment_value
    try:
        import config as project_config

        configured = getattr(project_config, "SECURITY_ANALYSIS", {})
        if isinstance(configured, Mapping):
            config_key = {
                "solver_executable": "SOLVER",
                "single_query_timeout_s": "SINGLE_QUERY_TIMEOUT_S",
                "total_timeout_s_per_mode": "TOTAL_TIMEOUT_S_PER_MODE",
                "maximum_weight": "MAXIMUM_WEIGHT",
            }.get(key)
            if config_key is not None and config_key in configured:
                return configured[config_key]
    except (ImportError, AttributeError):
        pass
    return None


def _analysis_config(
    context: Mapping[str, Any], candidate_num_rounds: int
) -> dict[str, Any]:
    nested_value = context.get("b_security", {})
    if nested_value is None:
        nested_value = {}
    if not isinstance(nested_value, Mapping):
        _fail("invalid_context", "context.b_security", "must be an object")
    solver = _option(context, nested_value, "solver_executable", "UKNIT_B_SOLVER")
    if solver in (None, ""):
        solver = "kissat"
    if not isinstance(solver, (str, os.PathLike)):
        _fail("invalid_configuration", "solver_executable", "must be a path")
    maximum_weight = _option(
        context, nested_value, "maximum_weight", "UKNIT_B_MAXIMUM_WEIGHT"
    )
    if maximum_weight in (None, ""):
        maximum_weight = "auto"
    elif maximum_weight != "auto":
        maximum_weight = _nonnegative_int(
            maximum_weight, "maximum_weight", 0
        )
    return cli.validate_security_config(
        {
            "schema_version": "security-config-v0.3",
            "analysis_profile": "project_candidate_multiround",
            "analysis_scope": "full_candidate",
            "modes": list(SUPPORTED_MODES),
            "start_round": 0,
            "num_rounds": candidate_num_rounds,
            "endpoint_constraints": {"input": "nonzero_free", "output": "free"},
            "final_linear_policy": "follow_candidate_spec",
            "solver": {
                "name": "kissat",
                "executable": os.fspath(solver),
                "single_query_timeout_s": _positive_float(
                    _option(
                        context,
                        nested_value,
                        "single_query_timeout_s",
                        "UKNIT_B_SINGLE_QUERY_TIMEOUT_S",
                    ),
                    "single_query_timeout_s",
                    5.0,
                ),
                "total_timeout_s_per_mode": _positive_float(
                    _option(
                        context,
                        nested_value,
                        "total_timeout_s_per_mode",
                        "UKNIT_B_TOTAL_TIMEOUT_S_PER_MODE",
                    ),
                    "total_timeout_s_per_mode",
                    20.0,
                ),
                "max_memory_mb": None,
            },
            "optimization": {
                "require_witness_validation": True,
                "require_optimality_proof": True,
                "maximum_weight": maximum_weight,
            },
        }
    )


def _safe_run_id(value: object) -> str:
    raw = str(value or "manual-run")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-")
    return safe or "manual-run"


def _security_cache_directory_name(cache_key: str) -> str:
    """Return a short Windows-safe directory name for one full cache key.

    The complete cache key remains in ``cache_key.txt`` and is compared before
    every cache reuse.  The directory name is therefore only a storage key,
    not the cache identity.
    """

    digest = cache_key.removeprefix("sha256:")
    if not _HEX_DIGEST.fullmatch(digest):
        _fail("invalid_cache_key", "cache_key", "expected a sha256 digest")
    return f"c-{digest[:_SECURITY_CACHE_DIRECTORY_PREFIX_LENGTH]}"


def _cache_evidence_exists(report: Mapping[str, Any], root: Path) -> bool:
    """Require every report evidence file before reusing a completed cache."""

    results = report.get("results")
    if not isinstance(results, Mapping):
        return False
    for result in results.values():
        if not isinstance(result, Mapping):
            return False
        paths = [result.get("query_log_path"), result.get("raw_log_path")]
        if result.get("witness_path") is not None:
            paths.append(result.get("witness_path"))
        for value in paths:
            if not isinstance(value, str) or not (root / value).is_file():
                return False
    return True


def _run_analysis(
    candidate: Mapping[str, Any],
    context: Mapping[str, Any],
    source_fingerprint: str,
) -> tuple[dict[str, Any], str]:
    config = _analysis_config(context, int(candidate["num_rounds"]))
    work_dir = Path(str(context.get("work_dir") or Path.cwd())).resolve()
    run_id = _safe_run_id(context.get("run_id"))
    solver_version = cli.detect_solver_version(config["solver"]["executable"])
    cache_key = security_cache_key(
        candidate_hash=str(candidate["candidate_hash"]),
        analysis_config=config,
        solver_version=solver_version,
        model_version=MODEL_VERSION,
        evaluator_version=EVALUATOR_VERSION,
    )
    cache_component = _security_cache_directory_name(cache_key)
    run_directory = (
        work_dir
        / "runs"
        / run_id
        / "security"
        / cache_component
    ).resolve()
    run_directory.mkdir(parents=True, exist_ok=True)
    report_path = run_directory / "security_report.json"
    cache_key_path = run_directory / "cache_key.txt"
    if report_path.is_file() and cache_key_path.is_file():
        try:
            cached = json.loads(report_path.read_text(encoding="utf-8"))
            stored_key = cache_key_path.read_text(encoding="ascii").strip()
            if reusable_cache_hit(
                cached,
                stored_cache_key=stored_key,
                expected_cache_key=cache_key,
            ) and _cache_evidence_exists(cached, run_directory):
                checked_cached = validate_security_report(cached)
                try:
                    display_path = report_path.relative_to(work_dir).as_posix()
                except ValueError:
                    display_path = str(report_path)
                return checked_cached, display_path
        except (OSError, json.JSONDecodeError, ReportValidationError):
            pass
    report = cli.evaluate(
        candidate,
        config,
        run_directory=run_directory,
        runs_root=run_directory / "j",
        run_id="b",
    )
    report = validate_security_report(report)
    report_path = write_security_report(report_path, report)
    atomic_write_text(cache_key_path, cache_key + "\n")
    try:
        display_path = report_path.relative_to(work_dir).as_posix()
    except ValueError:
        display_path = str(report_path)
    return report, display_path


def _map_report(
    candidate_id: str,
    source_fingerprint: str,
    b_candidate_hash: str,
    report: Mapping[str, Any],
    report_path: str,
) -> dict[str, Any]:
    checked = validate_security_report(report)
    results = checked["results"]
    artifacts = {
        "adapter_mode": "full_candidate_3_to_12_rounds",
        "source_fingerprint": source_fingerprint,
        "b_candidate_hash": b_candidate_hash,
        "security_report_path": report_path,
        "security_report": checked,
    }
    optimal_weights: dict[str, float] = {}
    for mode in SUPPORTED_MODES:
        result = results.get(mode)
        if not isinstance(result, Mapping):
            return _plugin_result(
                candidate_id,
                "error",
                errors=[{"code": "missing_mode_result", "field": mode}],
                artifacts=artifacts,
            )
        weight = result.get("best_weight")
        if (
            result.get("execution_status") != "completed"
            or result.get("conclusion") != "optimal"
            or not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(float(weight))
        ):
            status = (
                "error"
                if result.get("execution_status") in {"solver_error", "invalid_input"}
                else "unavailable"
            )
            return _plugin_result(
                candidate_id,
                status,
                warnings=[
                    {
                        "code": "security_not_proven_optimal",
                        "mode": mode,
                        "execution_status": result.get("execution_status"),
                        "conclusion": result.get("conclusion"),
                    }
                ],
                artifacts=artifacts,
            )
        optimal_weights[mode] = float(weight)
    return _plugin_result(
        candidate_id,
        "ok",
        differential_weight=optimal_weights["differential"],
        linear_weight=optimal_weights["linear"],
        artifacts=artifacts,
    )


def evaluate_security(
    candidate: object, context: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Team A Plugin API 1.0 entry point."""

    candidate_id = _candidate_id(candidate)
    if isinstance(candidate, Mapping):
        num_rounds = candidate.get("num_rounds")
        if (
            isinstance(num_rounds, int)
            and not isinstance(num_rounds, bool)
            and num_rounds not in SUPPORTED_ROUNDS
        ):
            return _plugin_result(
                candidate_id,
                "unavailable",
                warnings=[
                    {
                        "code": "unsupported_num_rounds",
                        "received": num_rounds,
                        "supported": list(SUPPORTED_ROUNDS),
                    }
                ],
                artifacts={"adapter_mode": "full_candidate_3_to_12_rounds"},
            )
    try:
        context_value: Mapping[str, Any] = context or {}
        if not isinstance(context_value, Mapping):
            _fail("invalid_context", "context", "must be an object")
        converted = convert_team_a_candidate(candidate)
        source_fingerprint = str(candidate["fingerprint"])  # type: ignore[index]
        report, report_path = _run_analysis(
            converted, context_value, source_fingerprint
        )
        return _map_report(
            candidate_id,
            source_fingerprint,
            converted["candidate_hash"],
            report,
            report_path,
        )
    except (TeamAAdapterError, CandidateValidationError) as exc:
        error = exc.as_dict()
        return _plugin_result(candidate_id, "error", errors=[error])
    except (ReportValidationError, OSError, ValueError) as exc:
        return _plugin_result(
            candidate_id,
            "error",
            errors=[
                {
                    "code": "team_b_evaluation_error",
                    "field": "$",
                    "message": str(exc),
                }
            ],
        )


evaluate = evaluate_security


__all__ = [
    "PLUGIN_API_VERSION",
    "PLUGIN_NAME",
    "SUPPORTED_ROUNDS",
    "TeamAAdapterError",
    "convert_team_a_candidate",
    "evaluate_security",
    "evaluate",
    "team_a_fingerprint",
]
