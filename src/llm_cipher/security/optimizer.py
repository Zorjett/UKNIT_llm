"""Proof-oriented integer-weight search for bounded SPN trail models.

The optimizer never equates SAT with optimality.  A SAT query contributes an
upper bound only after independent witness validation; only an explicit UNSAT
query contributes a lower bound.  Timeout and error contribute neither.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence

from .model_adapter import TrailMode, build_spn_model, execute_solver_job
from .witness_checker import check_witness


QueryStatus = Literal["sat", "unsat", "timeout", "error"]
ExecutionStatus = Literal["completed", "timeout", "solver_error"]
Conclusion = Literal["optimal", "bounded", "feasible", "unknown"]


@dataclass(frozen=True)
class SearchConfig:
    """Budgets and the inclusive maximum threshold for one analysis mode."""

    single_query_timeout_s: float
    total_timeout_s: float
    maximum_weight: int

    def validate(self) -> None:
        for field, value in (
            ("single_query_timeout_s", self.single_query_timeout_s),
            ("total_timeout_s", self.total_timeout_s),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{field} must be a finite positive number")
        if (
            isinstance(self.maximum_weight, bool)
            or not isinstance(self.maximum_weight, int)
            or self.maximum_weight < 0
        ):
            raise ValueError("maximum_weight must be a non-negative integer")


@dataclass(frozen=True)
class QueryEvidence:
    """Auditable evidence returned by exactly one inclusive ``<= k`` query."""

    threshold: int
    status: QueryStatus
    elapsed_s: float
    witness: dict[str, object] | None = None
    witness_valid: bool = False
    verified_weight: int | None = None
    job_directory: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SearchResult:
    execution_status: ExecutionStatus
    conclusion: Conclusion
    best_weight: int | None
    proven_lower_bound: int
    found_upper_bound: int | None
    witness: dict[str, object] | None
    queries: tuple[QueryEvidence, ...]
    elapsed_s: float
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["queries"] = [asdict(query) for query in self.queries]
        return value


ThresholdQuery = Callable[[int, float], QueryEvidence]
Clock = Callable[[], float]


def _growth_thresholds(maximum_weight: int) -> tuple[int, ...]:
    """Return 0, 1, 2, 4, ... with the configured maximum included once."""

    if maximum_weight == 0:
        return (0,)
    values = [0]
    threshold = 1
    while threshold < maximum_weight:
        values.append(threshold)
        threshold *= 2
    if values[-1] != maximum_weight:
        values.append(maximum_weight)
    return tuple(values)


def _conclusion(
    lower: int,
    upper: int | None,
    witness: dict[str, object] | None,
    saw_unsat: bool,
) -> tuple[Conclusion, int | None]:
    if upper is not None and lower == upper and witness is not None:
        return "optimal", upper
    if upper is not None and saw_unsat:
        return "bounded", None
    if upper is not None:
        return "feasible", None
    return "unknown", None


def _result(
    execution_status: ExecutionStatus,
    lower: int,
    upper: int | None,
    witness: dict[str, object] | None,
    saw_unsat: bool,
    queries: list[QueryEvidence],
    started_at: float,
    clock: Clock,
    error: str | None = None,
) -> SearchResult:
    conclusion, best = _conclusion(lower, upper, witness, saw_unsat)
    return SearchResult(
        execution_status=execution_status,
        conclusion=conclusion,
        best_weight=best,
        proven_lower_bound=lower,
        found_upper_bound=upper,
        witness=witness,
        queries=tuple(queries),
        elapsed_s=max(0.0, clock() - started_at),
        error=error,
    )


def _validate_evidence(evidence: QueryEvidence, threshold: int) -> str | None:
    if evidence.threshold != threshold:
        return (
            f"query returned threshold {evidence.threshold}; expected {threshold}"
        )
    if evidence.status not in ("sat", "unsat", "timeout", "error"):
        return f"invalid query status: {evidence.status!r}"
    if (
        isinstance(evidence.elapsed_s, bool)
        or not isinstance(evidence.elapsed_s, (int, float))
        or not math.isfinite(evidence.elapsed_s)
        or evidence.elapsed_s < 0
    ):
        return "query elapsed_s must be a finite non-negative number"
    if evidence.status == "sat":
        if not evidence.witness_valid or evidence.witness is None:
            return "SAT result has no independently validated witness"
        weight = evidence.verified_weight
        if (
            isinstance(weight, bool)
            or not isinstance(weight, int)
            or weight < 0
        ):
            return "SAT result has no valid independently recomputed weight"
        if weight > threshold:
            return (
                f"validated witness weight {weight} exceeds inclusive threshold "
                f"{threshold}"
            )
    else:
        if evidence.witness_valid or evidence.verified_weight is not None:
            return f"{evidence.status} result unexpectedly carries witness evidence"
    return None


def optimize_weight(
    query: ThresholdQuery,
    config: SearchConfig,
    *,
    clock: Clock = time.monotonic,
) -> SearchResult:
    """Prove the smallest feasible integer weight within configured budgets.

    Search first obtains an upper bound with exponential thresholds, then uses
    binary refinement.  The query callback must implement inclusive ``<= k``.
    """

    config.validate()
    if not callable(query):
        raise TypeError("query must be callable")
    started_at = clock()
    lower = 0
    upper: int | None = None
    best_witness: dict[str, object] | None = None
    saw_unsat = False
    queries: list[QueryEvidence] = []

    def invoke(threshold: int) -> tuple[QueryEvidence | None, SearchResult | None]:
        elapsed = clock() - started_at
        remaining = config.total_timeout_s - elapsed
        if remaining <= 0:
            return None, _result(
                "timeout",
                lower,
                upper,
                best_witness,
                saw_unsat,
                queries,
                started_at,
                clock,
                "total search budget exhausted",
            )
        timeout_s = min(config.single_query_timeout_s, remaining)
        try:
            evidence = query(threshold, timeout_s)
        except Exception as error:  # A query adapter failure is not UNSAT.
            synthetic = QueryEvidence(
                threshold=threshold,
                status="error",
                elapsed_s=max(0.0, clock() - started_at - elapsed),
                error=f"query raised {type(error).__name__}: {error}",
            )
            queries.append(synthetic)
            return None, _result(
                "solver_error",
                lower,
                upper,
                best_witness,
                saw_unsat,
                queries,
                started_at,
                clock,
                synthetic.error,
            )
        if not isinstance(evidence, QueryEvidence):
            return None, _result(
                "solver_error",
                lower,
                upper,
                best_witness,
                saw_unsat,
                queries,
                started_at,
                clock,
                "query did not return QueryEvidence",
            )
        queries.append(evidence)
        validation_error = _validate_evidence(evidence, threshold)
        if validation_error is not None:
            return None, _result(
                "solver_error",
                lower,
                upper,
                best_witness,
                saw_unsat,
                queries,
                started_at,
                clock,
                validation_error,
            )
        if evidence.status == "timeout":
            return None, _result(
                "timeout",
                lower,
                upper,
                best_witness,
                saw_unsat,
                queries,
                started_at,
                clock,
                evidence.error or f"query at k={threshold} timed out",
            )
        if evidence.status == "error":
            return None, _result(
                "solver_error",
                lower,
                upper,
                best_witness,
                saw_unsat,
                queries,
                started_at,
                clock,
                evidence.error or f"query at k={threshold} failed",
            )
        return evidence, None

    for threshold in _growth_thresholds(config.maximum_weight):
        evidence, terminal = invoke(threshold)
        if terminal is not None:
            return terminal
        assert evidence is not None
        if evidence.status == "unsat":
            lower = max(lower, threshold + 1)
            saw_unsat = True
        else:
            assert evidence.status == "sat"
            assert evidence.verified_weight is not None
            if evidence.verified_weight < lower:
                return _result(
                    "solver_error",
                    lower,
                    upper,
                    best_witness,
                    saw_unsat,
                    queries,
                    started_at,
                    clock,
                    (
                        f"validated witness weight {evidence.verified_weight} "
                        f"contradicts proven lower bound {lower}"
                    ),
                )
            upper = evidence.verified_weight
            best_witness = evidence.witness
            break
        if clock() - started_at >= config.total_timeout_s:
            return _result(
                "timeout",
                lower,
                upper,
                best_witness,
                saw_unsat,
                queries,
                started_at,
                clock,
                "total search budget exhausted",
            )

    if upper is None:
        return _result(
            "completed",
            lower,
            None,
            None,
            saw_unsat,
            queries,
            started_at,
            clock,
            "no feasible trail was found within maximum_weight",
        )

    while lower < upper:
        threshold = (lower + upper - 1) // 2
        evidence, terminal = invoke(threshold)
        if terminal is not None:
            return terminal
        assert evidence is not None
        if evidence.status == "unsat":
            lower = max(lower, threshold + 1)
            saw_unsat = True
        else:
            assert evidence.status == "sat"
            assert evidence.verified_weight is not None
            if evidence.verified_weight < lower:
                return _result(
                    "solver_error",
                    lower,
                    upper,
                    best_witness,
                    saw_unsat,
                    queries,
                    started_at,
                    clock,
                    (
                        f"validated witness weight {evidence.verified_weight} "
                        f"contradicts proven lower bound {lower}"
                    ),
                )
            if evidence.verified_weight < upper:
                upper = evidence.verified_weight
                best_witness = evidence.witness
        if clock() - started_at >= config.total_timeout_s and lower < upper:
            return _result(
                "timeout",
                lower,
                upper,
                best_witness,
                saw_unsat,
                queries,
                started_at,
                clock,
                "total search budget exhausted",
            )

    return _result(
        "completed",
        lower,
        upper,
        best_witness,
        saw_unsat,
        queries,
        started_at,
        clock,
    )


def _augment_job_validation(
    job_directory: Path,
    validation_status: str,
    verified_weight: int | None,
    validation_errors: Sequence[str],
) -> None:
    update = {
        "witness_validation_status": validation_status,
        "verified_weight": verified_weight,
        "witness_validation_errors": list(validation_errors),
    }
    query_path = job_directory / "queries.jsonl"
    query_value = json.loads(query_path.read_text(encoding="utf-8"))
    query_value.update(update)
    query_path.write_text(
        json.dumps(query_value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result_path = job_directory / "result.json"
    result_value = json.loads(result_path.read_text(encoding="utf-8"))
    result_value.update(update)
    result_path.write_text(
        json.dumps(result_value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@dataclass
class SpnQueryRunner:
    """Bridge one optimizer query to CNF, strict solver, and witness checker."""

    sboxes_by_round: Sequence[Sequence[Sequence[int]]]
    linear_rows_by_round: Sequence[Sequence[Sequence[int]] | None]
    mode: TrailMode
    solver_executable: str | Path
    runs_root: str | Path
    run_id: str
    candidate_hash: str
    request: Mapping[str, object]

    def __call__(self, threshold: int, timeout_s: float) -> QueryEvidence:
        model = build_spn_model(
            self.sboxes_by_round,
            self.linear_rows_by_round,
            self.mode,
            threshold,
        )
        job = execute_solver_job(
            model,
            self.solver_executable,
            self.runs_root,
            run_id=self.run_id,
            candidate_hash=self.candidate_hash,
            request=self.request,
            timeout_s=timeout_s,
        )
        result = job.solver_result
        if result.status != "sat":
            _augment_job_validation(
                job.job_directory,
                "not_applicable",
                None,
                (),
            )
            return QueryEvidence(
                threshold=threshold,
                status=result.status,
                elapsed_s=result.elapsed_s,
                job_directory=str(job.job_directory),
                error=result.error,
            )

        if job.witness is None:
            errors = ("SAT solver result did not decode to a witness",)
            _augment_job_validation(job.job_directory, "failed", None, errors)
            return QueryEvidence(
                threshold=threshold,
                status="sat",
                elapsed_s=result.elapsed_s,
                job_directory=str(job.job_directory),
                error=errors[0],
            )

        checked = check_witness(
            job.witness,
            self.sboxes_by_round,
            self.linear_rows_by_round,
            expected_mode=self.mode,
            weight_limit=threshold,
        )
        _augment_job_validation(
            job.job_directory,
            "passed" if checked.valid else "failed",
            checked.recomputed_weight,
            checked.errors,
        )
        return QueryEvidence(
            threshold=threshold,
            status="sat",
            elapsed_s=result.elapsed_s,
            witness=job.witness,
            witness_valid=checked.valid,
            verified_weight=checked.recomputed_weight if checked.valid else None,
            job_directory=str(job.job_directory),
            error=None if checked.valid else "; ".join(checked.errors),
        )
