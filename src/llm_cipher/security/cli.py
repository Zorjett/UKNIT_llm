"""File-level entry point for candidate security evaluation.

Exit code 0 means that a SecurityReport was written, not that the result is
optimal.  Consumers must read each mode's ``execution_status`` and
``conclusion``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, NoReturn

from .candidate_reader import (
    CandidateValidationError,
    load_candidate,
    validate_analysis_window,
)
from .model_adapter import MODEL_VERSION, maximum_trail_weight
from .optimizer import SearchConfig, SpnQueryRunner, optimize_weight
from .reports import (
    EVALUATOR_VERSION,
    EvidencePaths,
    ReportValidationError,
    atomic_write_text,
    build_security_report,
    invalid_mode_report,
    materialize_search_evidence,
    mode_report_from_search,
    unknown_mode_report,
    write_security_report,
)


SUPPORTED_CONFIG_SCHEMA = "security-config-v0.3"
LEGACY_CONFIG_SCHEMA = "security-config-v0.2"
SUPPORTED_CONFIG_SCHEMAS = (LEGACY_CONFIG_SCHEMA, SUPPORTED_CONFIG_SCHEMA)
SUPPORTED_MODES = ("differential", "linear")
SUPPORTED_PROJECT_PROFILES = (
    "project_candidate_4r",
    "project_candidate_multiround",
)
ENDPOINT_CONSTRAINTS = {"input": "nonzero_free", "output": "free"}


@dataclass(frozen=True)
class SecurityConfigError(ValueError):
    code: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} at {self.field}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


def _fail(code: str, field: str, message: str) -> NoReturn:
    raise SecurityConfigError(code, field, message)


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_number(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        _fail("invalid_config_value", field, "must be a finite positive number")
    return float(value)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            _fail("duplicate_json_key", key, f"duplicate JSON key: {key}")
        output[key] = value
    return output


def load_security_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=_reject_duplicate_keys)
    except SecurityConfigError:
        raise
    except FileNotFoundError:
        _fail("file_not_found", "$", f"config file not found: {config_path}")
    except OSError as error:
        _fail("file_read_error", "$", str(error))
    except json.JSONDecodeError as error:
        _fail(
            "invalid_json",
            "$",
            f"line {error.lineno}, column {error.colno}: {error.msg}",
        )
    validated = validate_security_config(value)
    # Keep the checked-in JSON profiles descriptive while allowing a local
    # Windows/WSL installation to select its actual Kissat executable without
    # editing a shared fixture.
    solver_override = os.getenv("UKNIT_B_SOLVER")
    if solver_override:
        validated = dict(validated)
        solver = dict(validated["solver"])
        solver["executable"] = solver_override
        validated["solver"] = solver
        validated = validate_security_config(validated)
    return validated


def validate_security_config(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid_type", "$", "SecurityConfig must be an object")
    required = {
        "schema_version",
        "analysis_profile",
        "modes",
        "start_round",
        "num_rounds",
        "endpoint_constraints",
        "final_linear_policy",
        "solver",
        "optimization",
    }
    missing = sorted(required - value.keys())
    if missing:
        _fail("missing_field", "$", f"missing fields: {missing}")
    schema_version = value["schema_version"]
    if schema_version not in SUPPORTED_CONFIG_SCHEMAS:
        _fail(
            "unsupported_schema_version",
            "schema_version",
            f"expected one of {SUPPORTED_CONFIG_SCHEMAS}",
        )
    if not isinstance(value["analysis_profile"], str) or not value["analysis_profile"]:
        _fail("invalid_analysis_profile", "analysis_profile", "must be a string")

    modes = value["modes"]
    if (
        not isinstance(modes, list)
        or not modes
        or any(mode not in SUPPORTED_MODES for mode in modes)
        or len(set(modes)) != len(modes)
    ):
        _fail(
            "invalid_modes",
            "modes",
            "must contain unique differential/linear mode names",
        )
    if not _is_plain_int(value["start_round"]) or value["start_round"] < 0:
        _fail("invalid_start_round", "start_round", "must be >= 0")
    analysis_scope = value.get("analysis_scope", "window")
    if analysis_scope not in ("full_candidate", "window"):
        _fail(
            "invalid_analysis_scope",
            "analysis_scope",
            "must equal 'full_candidate' or 'window'",
        )
    if schema_version == SUPPORTED_CONFIG_SCHEMA and "analysis_scope" not in value:
        _fail("missing_field", "$", "missing fields: ['analysis_scope']")
    configured_rounds = value["num_rounds"]
    if configured_rounds == "candidate":
        if analysis_scope != "full_candidate":
            _fail(
                "invalid_num_rounds",
                "num_rounds",
                "'candidate' is valid only for full_candidate analysis",
            )
    elif not _is_plain_int(configured_rounds) or configured_rounds < 1:
        _fail("invalid_num_rounds", "num_rounds", "must be >= 1 or 'candidate'")
    if value["endpoint_constraints"] != ENDPOINT_CONSTRAINTS:
        _fail(
            "unsupported_endpoint_constraints",
            "endpoint_constraints",
            f"must equal {ENDPOINT_CONSTRAINTS}",
        )
    if value["final_linear_policy"] != "follow_candidate_spec":
        _fail(
            "unsupported_final_linear_policy",
            "final_linear_policy",
            "must equal 'follow_candidate_spec'",
        )

    solver = value["solver"]
    if not isinstance(solver, dict):
        _fail("invalid_type", "solver", "must be an object")
    for name in ("name", "executable", "single_query_timeout_s", "total_timeout_s_per_mode"):
        if name not in solver:
            _fail("missing_field", "solver", f"missing {name}")
    if not isinstance(solver["name"], str) or not solver["name"]:
        _fail("invalid_solver", "solver.name", "must be a non-empty string")
    if not isinstance(solver["executable"], str) or not solver["executable"]:
        _fail("invalid_solver", "solver.executable", "must be a non-empty path")
    _positive_number(solver["single_query_timeout_s"], "solver.single_query_timeout_s")
    _positive_number(
        solver["total_timeout_s_per_mode"], "solver.total_timeout_s_per_mode"
    )

    optimization = value["optimization"]
    if not isinstance(optimization, dict):
        _fail("invalid_type", "optimization", "must be an object")
    for name in ("require_witness_validation", "require_optimality_proof", "maximum_weight"):
        if name not in optimization:
            _fail("missing_field", "optimization", f"missing {name}")
    if optimization["require_witness_validation"] is not True:
        _fail(
            "unsafe_config",
            "optimization.require_witness_validation",
            "must be true",
        )
    if optimization["require_optimality_proof"] is not True:
        _fail(
            "unsafe_config",
            "optimization.require_optimality_proof",
            "must be true",
        )
    maximum = optimization["maximum_weight"]
    if maximum != "auto" and (not _is_plain_int(maximum) or maximum < 0):
        _fail(
            "invalid_config_value",
            "optimization.maximum_weight",
            "must be a non-negative integer or 'auto'",
        )
    return value


def detect_solver_version(executable: str | Path) -> str:
    """Best-effort provenance only; inability to print a version is explicit."""

    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    text = (completed.stdout or completed.stderr).strip()
    return text.splitlines()[0][:200] if text else "unavailable"


def _candidate_path(args_candidate: Path | None, config_path: Path, config: Mapping[str, Any]) -> Path:
    if args_candidate is not None:
        return args_candidate
    configured = config.get("candidate_path")
    if not isinstance(configured, str) or not configured:
        _fail(
            "missing_candidate_path",
            "candidate_path",
            "supply --candidate or a config candidate_path",
        )
    return (config_path.parent / configured).resolve()


def _require_runs_root_within_report_directory(
    runs_root: Path, run_directory: Path
) -> None:
    try:
        runs_root.resolve().relative_to(run_directory.resolve())
    except ValueError:
        _fail(
            "runs_root_outside_report_directory",
            "runs_root",
            "solver jobs must stay below the report directory for relative evidence paths",
        )


def _empty_evidence(root: Path, mode: str, message: str) -> EvidencePaths:
    query_path = f"queries/{mode}.jsonl"
    raw_path = f"logs/{mode}.log"
    atomic_write_text(root / query_path, "")
    atomic_write_text(root / raw_path, message.rstrip() + "\n")
    return EvidencePaths(None, query_path, raw_path)


def _safe_error_metadata(config: object) -> tuple[list[str], str, int, int, dict[str, str], str, str]:
    value = config if isinstance(config, Mapping) else {}
    raw_modes = value.get("modes")
    modes = (
        [mode for mode in raw_modes if mode in SUPPORTED_MODES]
        if isinstance(raw_modes, list)
        else []
    )
    if not modes:
        modes = list(SUPPORTED_MODES)
    profile = value.get("analysis_profile")
    start = value.get("start_round")
    rounds = value.get("num_rounds")
    policy = value.get("final_linear_policy")
    solver = value.get("solver")
    solver_name = solver.get("name") if isinstance(solver, Mapping) else None
    return (
        modes,
        profile if isinstance(profile, str) and profile else "unknown",
        start if _is_plain_int(start) and start >= 0 else 0,
        rounds if _is_plain_int(rounds) and rounds >= 1 else 1,
        dict(ENDPOINT_CONSTRAINTS),
        policy if isinstance(policy, str) and policy else "follow_candidate_spec",
        solver_name if isinstance(solver_name, str) and solver_name else "unknown",
    )


def _error_report(
    root: Path,
    config: object,
    error: Mapping[str, object] | str,
    *,
    execution_status: str,
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    modes, profile, start, rounds, endpoints, policy, solver_name = _safe_error_metadata(config)
    results: dict[str, dict[str, Any]] = {}
    for mode in modes:
        paths = _empty_evidence(root, mode, json.dumps(error, ensure_ascii=False))
        common = dict(
            start_round=start,
            num_rounds=rounds,
            endpoint_constraints=endpoints,
            final_linear_policy=policy,
            evidence_paths=paths,
            solver_name=solver_name,
            solver_version="unavailable",
            model_version=MODEL_VERSION,
            error=error,
        )
        if execution_status == "invalid_input":
            results[mode] = invalid_mode_report(mode, **common)
        else:
            results[mode] = unknown_mode_report(
                mode, execution_status="solver_error", **common
            )
    return build_security_report(
        candidate_id=None if candidate is None else candidate.get("candidate_id"),
        candidate_hash=None if candidate is None else candidate.get("candidate_hash"),
        analysis_profile=profile,
        results=results,
    )


def evaluate(
    candidate: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    run_directory: Path,
    runs_root: Path,
    run_id: str,
) -> dict[str, Any]:
    if config["analysis_profile"] not in SUPPORTED_PROJECT_PROFILES:
        _fail(
            "unsupported_profile_for_candidate_cli",
            "analysis_profile",
            "CandidateSpec CLI accepts project candidate profiles; paper_baseline uses the locked author adapter",
        )
    scope = config.get("analysis_scope", "window")
    configured_rounds = config["num_rounds"]
    analysis_rounds = (
        candidate["num_rounds"] if configured_rounds == "candidate" else configured_rounds
    )
    start, end = validate_analysis_window(
        candidate, config["start_round"], analysis_rounds
    )
    if scope == "full_candidate" and (start != 0 or end != candidate["num_rounds"]):
        _fail(
            "incomplete_full_candidate_analysis",
            "num_rounds",
            "full_candidate must cover every candidate round from start_round 0",
        )
    rounds = candidate["rounds"][start:end]
    boxes = [round_spec["sboxes"] for round_spec in rounds]
    layers = [round_spec["linear_rows"] for round_spec in rounds]
    if scope == "window" and layers:
        # Project window semantics are r S layers and r-1 L layers.
        layers[-1] = None
    solver = config["solver"]
    optimization = config["optimization"]
    solver_version = detect_solver_version(solver["executable"])
    results: dict[str, dict[str, Any]] = {}

    for mode in config["modes"]:
        request = {
            "schema_version": config["schema_version"],
            "analysis_profile": config["analysis_profile"],
            "analysis_scope": scope,
            "candidate_hash": candidate["candidate_hash"],
            "start_round": start,
            "num_rounds": end - start,
            "endpoint_constraints": dict(config["endpoint_constraints"]),
            "final_linear_policy": config["final_linear_policy"],
        }
        runner = SpnQueryRunner(
            boxes,
            layers,
            mode,
            solver["executable"],
            runs_root,
            run_id,
            candidate["candidate_hash"],
            request,
        )
        search = optimize_weight(
            runner,
            SearchConfig(
                single_query_timeout_s=solver["single_query_timeout_s"],
                total_timeout_s=solver["total_timeout_s_per_mode"],
                maximum_weight=(
                    maximum_trail_weight(boxes, mode)
                    if optimization["maximum_weight"] == "auto"
                    else optimization["maximum_weight"]
                ),
            ),
        )
        paths = materialize_search_evidence(run_directory, mode, search)
        results[mode] = mode_report_from_search(
            mode,
            search,
            start_round=start,
            num_rounds=end - start,
            endpoint_constraints=config["endpoint_constraints"],
            final_linear_policy=config["final_linear_policy"],
            evidence_paths=paths,
            solver_name=solver["name"],
            solver_version=solver_version,
            model_version=MODEL_VERSION,
        )

    return build_security_report(
        candidate_id=candidate["candidate_id"],
        candidate_hash=candidate["candidate_hash"],
        analysis_profile=config["analysis_profile"],
        evaluator_version=EVALUATOR_VERSION,
        results=results,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a CandidateSpec and atomically write SecurityReport JSON; "
            "read the report for optimal/timeout semantics."
        )
    )
    parser.add_argument("--config", required=True, type=Path, help="SecurityConfig JSON")
    parser.add_argument(
        "--candidate",
        type=Path,
        help="CandidateSpec JSON; defaults to config candidate_path",
    )
    parser.add_argument("--output", required=True, type=Path, help="SecurityReport JSON")
    parser.add_argument(
        "--runs-root",
        type=Path,
        help="solver job root; defaults to the report directory",
    )
    parser.add_argument("--run-id", default="security-run", help="portable job namespace")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output.resolve()
    run_directory = output.parent
    runs_root = args.runs_root.resolve() if args.runs_root else run_directory
    raw_config: object = {}
    candidate: dict[str, Any] | None = None

    try:
        config = load_security_config(args.config)
        raw_config = config
        _require_runs_root_within_report_directory(runs_root, run_directory)
        candidate = load_candidate(_candidate_path(args.candidate, args.config, config))
        report = evaluate(
            candidate,
            config,
            run_directory=run_directory,
            runs_root=runs_root,
            run_id=args.run_id,
        )
        write_security_report(output, report)
        return 0
    except (CandidateValidationError, SecurityConfigError) as error:
        try:
            report = _error_report(
                run_directory,
                raw_config,
                error.as_dict(),
                execution_status="invalid_input",
                candidate=candidate,
            )
            write_security_report(output, report)
        except OSError as write_error:
            print(f"cannot write SecurityReport: {write_error}", file=sys.stderr)
            return 5
        print(json.dumps(error.as_dict(), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, ReportValidationError, Exception) as error:
        # An unexpected evaluator failure is never converted to UNSAT or a bound.
        try:
            detail = {
                "code": "evaluator_internal_error",
                "field": "$",
                "message": f"{type(error).__name__}: {error}",
            }
            report = _error_report(
                run_directory,
                raw_config,
                detail,
                execution_status="solver_error",
                candidate=candidate,
            )
            write_security_report(output, report)
        except OSError as write_error:
            print(f"cannot write SecurityReport: {write_error}", file=sys.stderr)
            return 5
        print(json.dumps(detail, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
