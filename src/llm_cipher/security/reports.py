"""Strict, auditable SecurityReport construction and persistence.

The report keeps process execution status separate from the mathematical
conclusion.  A SAT witness is only an upper bound; ``best_weight`` is emitted
only when a validated witness meets a proven lower bound.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NoReturn, Sequence

from .optimizer import QueryEvidence, SearchResult


REPORT_SCHEMA_VERSION = "security-report-v0.1"
EVALUATOR_VERSION = "b-security-evaluator-v0.2"
MODE_METRICS = {
    "differential": "neg_log2_trail_probability",
    "linear": "neg_log2_absolute_correlation",
}
EXECUTION_STATUSES = {
    "completed",
    "timeout",
    "solver_error",
    "invalid_input",
}
CONCLUSIONS = {"optimal", "bounded", "feasible", "unknown"}
DEFAULT_ASSUMPTIONS = (
    "standard_single_key_trail_model",
    "round_product_assumption",
    "differential_hull_not_evaluated",
    "linear_hull_not_evaluated",
    "key_recovery_not_evaluated",
)


@dataclass(frozen=True)
class ReportValidationError(ValueError):
    """A stable, field-addressable report validation failure."""

    code: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} at {self.field}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class EvidencePaths:
    witness_path: str | None
    query_log_path: str
    raw_log_path: str


def _fail(code: str, field: str, message: str) -> NoReturn:
    raise ReportValidationError(code, field, message)


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_nonnegative(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        _fail("invalid_number", field, "must be a finite non-negative number")
    return float(value)


def _relative_posix_path(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        _fail("invalid_evidence_path", field, "must be a non-empty relative path")
    if "\\" in value:
        _fail("invalid_evidence_path", field, "must use '/' separators")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        _fail(
            "invalid_evidence_path",
            field,
            "must stay within the report run directory",
        )
    return value


def _validate_endpoint_constraints(value: object, field: str) -> dict[str, str]:
    expected = {"input": "nonzero_free", "output": "free"}
    if value != expected:
        _fail(
            "unsupported_endpoint_constraints",
            field,
            f"must equal {expected}",
        )
    return dict(expected)


def _validate_error(value: object, field: str) -> None:
    if value is None:
        return
    if isinstance(value, str) and value:
        return
    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            return
    _fail("invalid_error", field, "must be null, a non-empty string, or an object")


def validate_mode_report(value: object, *, field: str = "result") -> dict[str, Any]:
    """Validate one differential/linear result and return a plain copy."""

    if not isinstance(value, Mapping):
        _fail("invalid_type", field, "mode result must be an object")
    required = {
        "execution_status",
        "conclusion",
        "metric",
        "best_weight",
        "proven_lower_bound",
        "found_upper_bound",
        "start_round",
        "num_rounds",
        "endpoint_constraints",
        "final_linear_policy",
        "witness_path",
        "query_log_path",
        "raw_log_path",
        "elapsed_s",
        "solver_name",
        "solver_version",
        "model_version",
        "assumptions",
        "error",
    }
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required)
    if missing:
        _fail("missing_field", field, f"missing fields: {missing}")
    if unknown:
        _fail("unknown_field", field, f"unknown fields: {unknown}")

    execution = value["execution_status"]
    conclusion = value["conclusion"]
    if execution not in EXECUTION_STATUSES:
        _fail("invalid_execution_status", f"{field}.execution_status", repr(execution))
    if conclusion not in CONCLUSIONS:
        _fail("invalid_conclusion", f"{field}.conclusion", repr(conclusion))
    if execution in {"solver_error", "invalid_input"} and conclusion != "unknown":
        _fail(
            "inconsistent_status",
            f"{field}.conclusion",
            f"{execution} requires conclusion 'unknown'",
        )
    if execution == "timeout" and conclusion == "optimal":
        _fail(
            "inconsistent_status",
            f"{field}.conclusion",
            "timeout cannot claim optimal",
        )

    metric = value["metric"]
    if metric not in MODE_METRICS.values():
        _fail("invalid_metric", f"{field}.metric", repr(metric))

    lower = value["proven_lower_bound"]
    upper = value["found_upper_bound"]
    best = value["best_weight"]
    if lower is not None and (not _is_plain_int(lower) or lower < 0):
        _fail("invalid_bound", f"{field}.proven_lower_bound", "must be null or >= 0")
    if upper is not None and (not _is_plain_int(upper) or upper < 0):
        _fail("invalid_bound", f"{field}.found_upper_bound", "must be null or >= 0")
    if best is not None and (not _is_plain_int(best) or best < 0):
        _fail("invalid_bound", f"{field}.best_weight", "must be null or >= 0")
    if lower is not None and upper is not None and lower > upper:
        _fail("contradictory_bounds", field, "lower bound exceeds upper bound")

    witness_path = _relative_posix_path(
        value["witness_path"], f"{field}.witness_path", nullable=True
    )
    _relative_posix_path(value["query_log_path"], f"{field}.query_log_path")
    _relative_posix_path(value["raw_log_path"], f"{field}.raw_log_path")

    if conclusion == "optimal":
        if (
            execution != "completed"
            or best is None
            or lower != best
            or upper != best
            or witness_path is None
        ):
            _fail(
                "false_optimal_claim",
                field,
                "optimal requires completed, best=lower=upper, and a witness",
            )
    elif best is not None:
        _fail("unexpected_best_weight", f"{field}.best_weight", "only optimal may set it")

    if conclusion == "bounded":
        if lower is None or upper is None or lower >= upper or witness_path is None:
            _fail(
                "invalid_bounded_result",
                field,
                "bounded requires lower < upper and a validated witness",
            )
    elif conclusion == "feasible":
        if upper is None or witness_path is None:
            _fail(
                "invalid_feasible_result",
                field,
                "feasible requires an upper bound and a validated witness",
            )
    elif conclusion == "unknown":
        if upper is not None or witness_path is not None:
            _fail(
                "invalid_unknown_result",
                field,
                "unknown cannot carry an upper bound or witness",
            )

    if upper is not None and witness_path is None:
        _fail("missing_witness", f"{field}.witness_path", "every upper needs a witness")

    start = value["start_round"]
    rounds = value["num_rounds"]
    if not _is_plain_int(start) or start < 0:
        _fail("invalid_start_round", f"{field}.start_round", "must be >= 0")
    if not _is_plain_int(rounds) or rounds < 1:
        _fail("invalid_num_rounds", f"{field}.num_rounds", "must be >= 1")
    _validate_endpoint_constraints(
        value["endpoint_constraints"], f"{field}.endpoint_constraints"
    )
    if not isinstance(value["final_linear_policy"], str) or not value["final_linear_policy"]:
        _fail("invalid_policy", f"{field}.final_linear_policy", "must be a string")
    _finite_nonnegative(value["elapsed_s"], f"{field}.elapsed_s")
    for name in ("solver_name", "solver_version", "model_version"):
        if not isinstance(value[name], str) or not value[name]:
            _fail("invalid_metadata", f"{field}.{name}", "must be a non-empty string")
    assumptions = value["assumptions"]
    if (
        not isinstance(assumptions, list)
        or not assumptions
        or any(not isinstance(item, str) or not item for item in assumptions)
        or len(set(assumptions)) != len(assumptions)
    ):
        _fail("invalid_assumptions", f"{field}.assumptions", "need unique strings")
    _validate_error(value["error"], f"{field}.error")
    if execution == "completed" and value["error"] is not None:
        _fail("unexpected_error", f"{field}.error", "completed requires null")
    if execution != "completed" and value["error"] is None:
        _fail("missing_error", f"{field}.error", f"{execution} requires an explanation")
    return dict(value)


def mode_report_from_search(
    mode: str,
    result: SearchResult,
    *,
    start_round: int,
    num_rounds: int,
    endpoint_constraints: Mapping[str, str],
    final_linear_policy: str,
    evidence_paths: EvidencePaths,
    solver_name: str,
    solver_version: str,
    model_version: str,
    assumptions: Sequence[str] = DEFAULT_ASSUMPTIONS,
) -> dict[str, Any]:
    """Convert optimizer output without strengthening its conclusion."""

    if mode not in MODE_METRICS:
        _fail("invalid_mode", "mode", repr(mode))
    if not isinstance(result, SearchResult):
        _fail("invalid_type", "result", "expected SearchResult")
    report = {
        "execution_status": result.execution_status,
        "conclusion": result.conclusion,
        "metric": MODE_METRICS[mode],
        "best_weight": result.best_weight,
        "proven_lower_bound": result.proven_lower_bound,
        "found_upper_bound": result.found_upper_bound,
        "start_round": start_round,
        "num_rounds": num_rounds,
        "endpoint_constraints": dict(endpoint_constraints),
        "final_linear_policy": final_linear_policy,
        "witness_path": evidence_paths.witness_path,
        "query_log_path": evidence_paths.query_log_path,
        "raw_log_path": evidence_paths.raw_log_path,
        "elapsed_s": result.elapsed_s,
        "solver_name": solver_name,
        "solver_version": solver_version,
        "model_version": model_version,
        "assumptions": list(assumptions),
        "error": result.error,
    }
    return validate_mode_report(report, field=f"results.{mode}")


def invalid_mode_report(
    mode: str,
    *,
    start_round: int,
    num_rounds: int,
    endpoint_constraints: Mapping[str, str],
    final_linear_policy: str,
    evidence_paths: EvidencePaths,
    solver_name: str,
    solver_version: str,
    model_version: str,
    error: Mapping[str, object] | str,
) -> dict[str, Any]:
    """Create a mode-shaped invalid-input result so A needs no special parser."""

    return unknown_mode_report(
        mode,
        execution_status="invalid_input",
        start_round=start_round,
        num_rounds=num_rounds,
        endpoint_constraints=endpoint_constraints,
        final_linear_policy=final_linear_policy,
        evidence_paths=evidence_paths,
        solver_name=solver_name,
        solver_version=solver_version,
        model_version=model_version,
        error=error,
    )


def unknown_mode_report(
    mode: str,
    *,
    execution_status: str,
    start_round: int,
    num_rounds: int,
    endpoint_constraints: Mapping[str, str],
    final_linear_policy: str,
    evidence_paths: EvidencePaths,
    solver_name: str,
    solver_version: str,
    model_version: str,
    error: Mapping[str, object] | str,
) -> dict[str, Any]:
    """Create an invalid-input or internal-error result with no fake bounds."""

    if mode not in MODE_METRICS:
        _fail("invalid_mode", "mode", repr(mode))
    if execution_status not in {"invalid_input", "solver_error"}:
        _fail("invalid_execution_status", "execution_status", repr(execution_status))

    value = {
        "execution_status": execution_status,
        "conclusion": "unknown",
        "metric": MODE_METRICS[mode],
        "best_weight": None,
        "proven_lower_bound": None,
        "found_upper_bound": None,
        "start_round": start_round,
        "num_rounds": num_rounds,
        "endpoint_constraints": dict(endpoint_constraints),
        "final_linear_policy": final_linear_policy,
        "witness_path": None,
        "query_log_path": evidence_paths.query_log_path,
        "raw_log_path": evidence_paths.raw_log_path,
        "elapsed_s": 0.0,
        "solver_name": solver_name,
        "solver_version": solver_version,
        "model_version": model_version,
        "assumptions": list(DEFAULT_ASSUMPTIONS),
        "error": dict(error) if isinstance(error, Mapping) else error,
    }
    return validate_mode_report(value, field=f"results.{mode}")


def build_security_report(
    *,
    candidate_id: str | None,
    candidate_hash: str | None,
    analysis_profile: str,
    results: Mapping[str, Mapping[str, object]],
    evaluator_version: str = EVALUATOR_VERSION,
) -> dict[str, Any]:
    """Build and validate the stable top-level report envelope."""

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "candidate_hash": candidate_hash,
        "analysis_profile": analysis_profile,
        "evaluator_version": evaluator_version,
        "results": {mode: dict(value) for mode, value in sorted(results.items())},
    }
    return validate_security_report(report)


def validate_security_report(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_type", "$", "SecurityReport must be an object")
    required = {
        "schema_version",
        "candidate_id",
        "candidate_hash",
        "analysis_profile",
        "evaluator_version",
        "results",
    }
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required)
    if missing:
        _fail("missing_field", "$", f"missing fields: {missing}")
    if unknown:
        _fail("unknown_field", "$", f"unknown fields: {unknown}")
    if value["schema_version"] != REPORT_SCHEMA_VERSION:
        _fail("unsupported_schema_version", "schema_version", REPORT_SCHEMA_VERSION)
    for name in ("analysis_profile", "evaluator_version"):
        if not isinstance(value[name], str) or not value[name]:
            _fail("invalid_metadata", name, "must be a non-empty string")
    if value["candidate_id"] is not None and (
        not isinstance(value["candidate_id"], str) or not value["candidate_id"]
    ):
        _fail("invalid_candidate_id", "candidate_id", "must be null or a string")
    candidate_hash = value["candidate_hash"]
    if candidate_hash is not None and (
        not isinstance(candidate_hash, str)
        or not candidate_hash.startswith("sha256:")
        or len(candidate_hash) != 71
        or any(character not in "0123456789abcdef" for character in candidate_hash[7:])
    ):
        _fail("invalid_candidate_hash", "candidate_hash", "must be canonical sha256")
    results = value["results"]
    if not isinstance(results, Mapping) or not results:
        _fail("invalid_results", "results", "must contain at least one mode")
    if any(mode not in MODE_METRICS for mode in results):
        _fail("invalid_mode", "results", "only differential and linear are supported")
    checked = {
        mode: validate_mode_report(result, field=f"results.{mode}")
        for mode, result in sorted(results.items())
    }
    for mode, result in checked.items():
        if result["metric"] != MODE_METRICS[mode]:
            _fail(
                "metric_mode_mismatch",
                f"results.{mode}.metric",
                f"expected {MODE_METRICS[mode]}",
            )
    output = dict(value)
    output["results"] = checked
    return output


def _json_text(value: object, *, compact: bool = False) -> str:
    separators = (",", ":") if compact else None
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=None if compact else 2,
        separators=separators,
        allow_nan=False,
    ) + "\n"


def security_cache_key(
    *,
    candidate_hash: str,
    analysis_config: Mapping[str, object],
    solver_version: str,
    model_version: str,
    evaluator_version: str = EVALUATOR_VERSION,
) -> str:
    """Return the complete section-12.3 cache identity.

    The whole analysis configuration is included, so mode, window, endpoints,
    final-layer policy, and budgets cannot silently share a cache entry.
    """

    if (
        not isinstance(candidate_hash, str)
        or not candidate_hash.startswith("sha256:")
        or len(candidate_hash) != 71
        or any(character not in "0123456789abcdef" for character in candidate_hash[7:])
    ):
        _fail("invalid_candidate_hash", "candidate_hash", "must be canonical sha256")
    for field, value in (
        ("solver_version", solver_version),
        ("model_version", model_version),
        ("evaluator_version", evaluator_version),
    ):
        if not isinstance(value, str) or not value:
            _fail("invalid_metadata", field, "must be a non-empty string")
    if not isinstance(analysis_config, Mapping):
        _fail("invalid_type", "analysis_config", "must be an object")
    identity = {
        "candidate_hash": candidate_hash,
        "analysis_config": dict(analysis_config),
        "evaluator_version": evaluator_version,
        "solver_version": solver_version,
        "model_version": model_version,
    }
    try:
        payload = _json_text(identity, compact=True).encode("utf-8")
    except (TypeError, ValueError) as error:
        _fail("invalid_cache_identity", "analysis_config", str(error))
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def reusable_cache_hit(
    report: Mapping[str, object],
    *,
    stored_cache_key: str,
    expected_cache_key: str,
) -> bool:
    """Only exact-identity, fully completed reports are final cache hits."""

    try:
        checked = validate_security_report(report)
    except ReportValidationError:
        return False
    if stored_cache_key != expected_cache_key:
        return False
    return all(
        result["execution_status"] == "completed"
        for result in checked["results"].values()
    )


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Atomically replace a UTF-8 text file using a same-directory temporary."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def write_security_report(path: str | Path, report: Mapping[str, object]) -> Path:
    checked = validate_security_report(report)
    return atomic_write_text(path, _json_text(checked))


def _path_relative_to_run(path: Path, run_directory: Path, field: str) -> str:
    try:
        relative = path.resolve().relative_to(run_directory.resolve())
    except ValueError:
        _fail("evidence_outside_run", field, str(path))
    return relative.as_posix()


def _query_record(query: QueryEvidence, run_directory: Path) -> dict[str, object]:
    record = asdict(query)
    record.pop("witness", None)
    job_directory = query.job_directory
    record["job_directory"] = (
        None
        if job_directory is None
        else _path_relative_to_run(Path(job_directory), run_directory, "job_directory")
    )
    if job_directory is not None:
        source = Path(job_directory) / "queries.jsonl"
        if source.is_file():
            try:
                persisted = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                persisted = None
            if isinstance(persisted, dict):
                record["cnf_sha256"] = persisted.get("cnf_sha256")
                record["witness_validation_status"] = persisted.get(
                    "witness_validation_status"
                )
    return record


def materialize_search_evidence(
    run_directory: str | Path,
    mode: str,
    result: SearchResult,
) -> EvidencePaths:
    """Write aggregate query, witness, and raw logs beside the final report."""

    if mode not in MODE_METRICS:
        _fail("invalid_mode", "mode", repr(mode))
    root = Path(run_directory)
    query_relative = f"queries/{mode}.jsonl"
    raw_relative = f"logs/{mode}.log"
    witness_relative = f"witness/{mode}.json" if result.witness is not None else None

    query_lines = "".join(
        _json_text(_query_record(query, root), compact=True) for query in result.queries
    )
    atomic_write_text(root / query_relative, query_lines)

    log_parts: list[str] = []
    for index, query in enumerate(result.queries):
        log_parts.append(
            f"===== query {index} threshold={query.threshold} status={query.status} =====\n"
        )
        if query.job_directory is None:
            log_parts.append("no solver job directory recorded\n")
            continue
        job = Path(query.job_directory)
        _path_relative_to_run(job, root, "job_directory")
        for filename in ("solver.stdout", "solver.stderr"):
            log_parts.append(f"--- {filename} ---\n")
            source = job / filename
            if source.is_file():
                log_parts.append(source.read_text(encoding="utf-8", errors="replace"))
                if log_parts[-1] and not log_parts[-1].endswith("\n"):
                    log_parts.append("\n")
            else:
                log_parts.append("not recorded\n")
    atomic_write_text(root / raw_relative, "".join(log_parts))

    if witness_relative is not None:
        atomic_write_text(root / witness_relative, _json_text(result.witness))
    return EvidencePaths(witness_relative, query_relative, raw_relative)
