"""Independent bounded single-trail CNF model for 4-bit SP networks.

The public threshold is always inclusive: ``total_weight <= weight_limit``.
State bit lists and ``linear_rows`` use the project's MSB0 convention.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence

from .ddt_lat import ddt, differential_weight, linear_weight, validate_sbox, walsh_table
from .solver_runner import SolverResult, run_solver


TrailMode = Literal["differential", "linear"]
MODEL_VERSION = "bounded-trail-cnf-v0.1"


class CNF:
    """Small DIMACS builder with explicit XOR and cardinality encodings."""

    def __init__(self) -> None:
        self.variable_count = 0
        self.clauses: list[tuple[int, ...]] = []

    def new_variable(self) -> int:
        self.variable_count += 1
        return self.variable_count

    def new_variables(self, count: int) -> tuple[int, ...]:
        return tuple(self.new_variable() for _ in range(count))

    def add_clause(self, *literals: int) -> None:
        if any(literal == 0 for literal in literals):
            raise ValueError("zero is not a DIMACS literal")
        self.clauses.append(tuple(literals))

    def add_exactly_one(self, variables: Sequence[int]) -> None:
        values = tuple(variables)
        if not values:
            self.add_clause()
            return
        self.add_clause(*values)
        if len(values) == 1:
            return

        # Sinz sequential at-most-one encoding.
        sequential = self.new_variables(len(values) - 1)
        self.add_clause(-values[0], sequential[0])
        for index in range(1, len(values) - 1):
            self.add_clause(-values[index], sequential[index])
            self.add_clause(-sequential[index - 1], sequential[index])
            self.add_clause(-values[index], -sequential[index - 1])
        self.add_clause(-values[-1], -sequential[-1])

    def add_at_most(self, variables: Sequence[int], limit: int) -> None:
        """Encode ``sum(variables) <= limit`` with a sequential counter."""

        values = tuple(variables)
        if limit < 0:
            self.add_clause()
            return
        if limit >= len(values):
            return
        if limit == 0:
            for variable in values:
                self.add_clause(-variable)
            return

        # s[i][j] means at least j+1 of x[0:i+1] are true.  Defining the
        # complete recurrence makes the inclusive boundary easy to audit.
        previous: tuple[int, ...] = ()
        for index, variable in enumerate(values):
            current = self.new_variables(min(limit + 1, index + 1))
            for count, output in enumerate(current, start=1):
                if count == 1:
                    # output <-> variable OR previous[0]
                    self.add_clause(-variable, output)
                    if previous:
                        self.add_clause(-previous[0], output)
                        self.add_clause(-output, variable, previous[0])
                    else:
                        self.add_clause(-output, variable)
                elif count <= len(previous):
                    lower = previous[count - 2]
                    same = previous[count - 1]
                    # output <-> same OR (variable AND lower)
                    self.add_clause(-same, output)
                    self.add_clause(-variable, -lower, output)
                    self.add_clause(-output, same, variable)
                    self.add_clause(-output, same, lower)
                else:
                    lower = previous[count - 2]
                    # output <-> variable AND lower
                    self.add_clause(-output, variable)
                    self.add_clause(-output, lower)
                    self.add_clause(-variable, -lower, output)
            previous = current
        self.add_clause(-previous[limit])

    def add_xor(self, output: int, inputs: Sequence[int]) -> None:
        """Encode ``output == XOR(inputs)`` for any fan-in."""

        values = tuple(inputs)
        if not values:
            self.add_clause(-output)
            return
        if len(values) == 1:
            self.add_clause(-output, values[0])
            self.add_clause(output, -values[0])
            return

        left = values[0]
        for index, right in enumerate(values[1:], start=1):
            result = output if index == len(values) - 1 else self.new_variable()
            self.add_clause(left, right, -result)
            self.add_clause(-left, -right, -result)
            self.add_clause(left, -right, result)
            self.add_clause(-left, right, result)
            left = result

    def set_unsigned_msb0(self, variables: Sequence[int], value: int) -> None:
        if value < 0 or value >= 1 << len(variables):
            raise ValueError("fixed state does not fit its bit width")
        for position, variable in enumerate(variables):
            bit = (value >> (len(variables) - 1 - position)) & 1
            self.add_clause(variable if bit else -variable)

    def to_dimacs(self) -> str:
        lines = [f"p cnf {self.variable_count} {len(self.clauses)}"]
        lines.extend(" ".join(map(str, clause)) + " 0" for clause in self.clauses)
        return "\n".join(lines) + "\n"

    def write_dimacs(self, path: str | Path) -> Path:
        output = Path(path)
        output.write_text(self.to_dimacs(), encoding="ascii")
        return output


@dataclass(frozen=True)
class TransitionVariable:
    selector: int
    left: int
    right: int
    weight: int
    walsh_sign: int | None


@dataclass(frozen=True)
class SboxVariables:
    input_bits: tuple[int, ...]
    output_bits: tuple[int, ...]
    transitions: tuple[TransitionVariable, ...]


@dataclass(frozen=True)
class RoundVariables:
    before_sbox: tuple[int, ...]
    after_sbox: tuple[int, ...]
    after_linear: tuple[int, ...]
    sboxes: tuple[SboxVariables, ...]


@dataclass(frozen=True)
class SpnModel:
    cnf: CNF
    mode: TrailMode
    weight_limit: int
    sboxes_by_round: tuple[tuple[tuple[int, ...], ...], ...]
    linear_rows_by_round: tuple[tuple[tuple[int, ...], ...] | None, ...]
    rounds: tuple[RoundVariables, ...]
    cost_variables: tuple[int, ...]
    model_version: str = MODEL_VERSION

    @property
    def block_size(self) -> int:
        return len(self.rounds[0].before_sbox)


@dataclass(frozen=True)
class SolverJob:
    """One isolated, auditable SAT invocation and its decoded witness."""

    job_directory: Path
    solver_result: SolverResult
    witness: dict[str, object] | None


def _validate_network(
    sboxes_by_round: Sequence[Sequence[Sequence[int]]],
    linear_rows_by_round: Sequence[Sequence[Sequence[int]] | None],
) -> tuple[
    tuple[tuple[tuple[int, ...], ...], ...],
    tuple[tuple[tuple[int, ...], ...] | None, ...],
]:
    if not sboxes_by_round or len(sboxes_by_round) != len(linear_rows_by_round):
        raise ValueError("S-box and linear-layer round counts must match and be nonzero")
    box_count = len(sboxes_by_round[0])
    if box_count < 1:
        raise ValueError("each round must contain at least one S-box")
    block_size = 4 * box_count

    frozen_boxes: list[tuple[tuple[int, ...], ...]] = []
    frozen_rows: list[tuple[tuple[int, ...], ...] | None] = []
    for round_index, boxes in enumerate(sboxes_by_round):
        if len(boxes) != box_count:
            raise ValueError("all rounds must use the same number of S-boxes")
        checked_boxes = []
        for box in boxes:
            validate_sbox(box)
            checked_boxes.append(tuple(box))
        frozen_boxes.append(tuple(checked_boxes))

        rows = linear_rows_by_round[round_index]
        if rows is None:
            if round_index != len(sboxes_by_round) - 1:
                raise ValueError("only the last modeled round may omit its linear layer")
            frozen_rows.append(None)
            continue
        if len(rows) != block_size:
            raise ValueError("linear row count must equal the block size")
        checked_rows = []
        for row in rows:
            if (
                not row
                or len(set(row)) != len(row)
                or any(not isinstance(index, int) or isinstance(index, bool) for index in row)
                or any(index < 0 or index >= block_size for index in row)
            ):
                raise ValueError("linear rows need unique in-range integer indices")
            checked_rows.append(tuple(row))
        frozen_rows.append(tuple(checked_rows))
    return tuple(frozen_boxes), tuple(frozen_rows)


def _transition_table(
    sbox: Sequence[int], mode: TrailMode
) -> tuple[tuple[tuple[int, ...], ...], Callable[..., int]]:
    if mode == "differential":
        table = ddt(sbox)
        return tuple(tuple(row) for row in table), differential_weight
    if mode == "linear":
        table = walsh_table(sbox)
        return tuple(tuple(row) for row in table), linear_weight
    raise ValueError("mode must be 'differential' or 'linear'")


def maximum_trail_weight(
    sboxes_by_round: Sequence[Sequence[Sequence[int]]], mode: TrailMode
) -> int:
    """Return a conservative finite upper bound for the supplied network.

    The bound is derived from the actual S-box tables instead of assuming a
    fixed round count or a fixed MANTIS spectrum.  It is intentionally an
    absolute encoding bound, not a claimed security result.
    """

    if not sboxes_by_round or any(not boxes for boxes in sboxes_by_round):
        raise ValueError("network must contain at least one S-box per round")
    total = 0
    for boxes in sboxes_by_round:
        for sbox in boxes:
            table, weight_function = _transition_table(sbox, mode)
            total += max(
                weight_function(table, left, right)
                for left in range(16)
                for right in range(16)
                if table[left][right] != 0
            )
    return total


def _add_sbox(
    cnf: CNF,
    input_bits: tuple[int, ...],
    sbox: Sequence[int],
    mode: TrailMode,
    cost_variables: list[int],
) -> SboxVariables:
    output_bits = cnf.new_variables(4)
    table, weight_function = _transition_table(sbox, mode)
    transitions: list[TransitionVariable] = []
    by_minimum_weight: dict[int, list[int]] = {}

    for left in range(16):
        for right in range(16):
            magnitude = table[left][right]
            if magnitude == 0:
                continue
            weight = weight_function(table, left, right)
            selector = cnf.new_variable()
            sign = None if mode == "differential" else (1 if magnitude > 0 else -1)
            transitions.append(TransitionVariable(selector, left, right, weight, sign))
            for position, variable in enumerate(input_bits):
                bit = (left >> (3 - position)) & 1
                cnf.add_clause(-selector, variable if bit else -variable)
            for position, variable in enumerate(output_bits):
                bit = (right >> (3 - position)) & 1
                cnf.add_clause(-selector, variable if bit else -variable)
            for threshold in range(1, weight + 1):
                by_minimum_weight.setdefault(threshold, []).append(selector)

    cnf.add_exactly_one([item.selector for item in transitions])
    maximum_weight = max((item.weight for item in transitions), default=0)
    for threshold in range(1, maximum_weight + 1):
        cost = cnf.new_variable()
        matching = by_minimum_weight.get(threshold, [])
        for selector in matching:
            cnf.add_clause(-selector, cost)
        cnf.add_clause(-cost, *matching)
        cost_variables.append(cost)
    return SboxVariables(input_bits, output_bits, tuple(transitions))


def build_spn_model(
    sboxes_by_round: Sequence[Sequence[Sequence[int]]],
    linear_rows_by_round: Sequence[Sequence[Sequence[int]] | None],
    mode: TrailMode,
    weight_limit: int,
    *,
    enforce_nonzero_input: bool = True,
    fixed_input: int | None = None,
    fixed_output: int | None = None,
) -> SpnModel:
    """Build one bounded differential or linear single-trail query."""

    if not isinstance(weight_limit, int) or isinstance(weight_limit, bool):
        raise ValueError("weight_limit must be an integer")
    frozen_boxes, frozen_rows = _validate_network(
        sboxes_by_round, linear_rows_by_round
    )
    block_size = 4 * len(frozen_boxes[0])
    cnf = CNF()
    first_state = cnf.new_variables(block_size)
    if enforce_nonzero_input:
        cnf.add_clause(*first_state)
    if fixed_input is not None:
        cnf.set_unsigned_msb0(first_state, fixed_input)

    current_state = first_state
    cost_variables: list[int] = []
    modeled_rounds: list[RoundVariables] = []
    for round_index, boxes in enumerate(frozen_boxes):
        before_sbox = current_state
        modeled_boxes = []
        for box_index, box in enumerate(boxes):
            begin = 4 * box_index
            modeled_boxes.append(
                _add_sbox(
                    cnf,
                    before_sbox[begin : begin + 4],
                    box,
                    mode,
                    cost_variables,
                )
            )
        after_sbox = tuple(
            bit for modeled_box in modeled_boxes for bit in modeled_box.output_bits
        )
        rows = frozen_rows[round_index]
        if rows is None:
            after_linear = after_sbox
        elif mode == "differential":
            after_linear = cnf.new_variables(block_size)
            for output_position, row in enumerate(rows):
                cnf.add_xor(
                    after_linear[output_position],
                    [after_sbox[input_position] for input_position in row],
                )
        else:
            # Data is y=Mx, hence alpha=M^T beta for masks.  after_sbox is
            # alpha (before L); after_linear is beta (after L / next S input).
            after_linear = cnf.new_variables(block_size)
            for input_position in range(block_size):
                contributing_outputs = [
                    after_linear[output_position]
                    for output_position, row in enumerate(rows)
                    if input_position in row
                ]
                cnf.add_xor(after_sbox[input_position], contributing_outputs)
        modeled_rounds.append(
            RoundVariables(
                before_sbox,
                after_sbox,
                after_linear,
                tuple(modeled_boxes),
            )
        )
        current_state = after_linear

    if fixed_output is not None:
        cnf.set_unsigned_msb0(modeled_rounds[-1].after_sbox, fixed_output)
    cnf.add_at_most(cost_variables, weight_limit)
    return SpnModel(
        cnf=cnf,
        mode=mode,
        weight_limit=weight_limit,
        sboxes_by_round=frozen_boxes,
        linear_rows_by_round=frozen_rows,
        rounds=tuple(modeled_rounds),
        cost_variables=tuple(cost_variables),
    )


def _decode_bits(variables: Sequence[int], assignment: Mapping[int, bool]) -> list[int]:
    try:
        return [int(assignment[variable]) for variable in variables]
    except KeyError as error:
        raise ValueError(f"SAT assignment is missing variable {error.args[0]}") from error


def decode_witness(model: SpnModel, assignment: Mapping[int, bool]) -> dict[str, object]:
    """Decode named round states; encoded cost is retained only for auditing."""

    rounds: list[dict[str, object]] = []
    prefix = "difference" if model.mode == "differential" else "mask"
    for round_index, round_variables in enumerate(model.rounds):
        transitions = []
        for box_index, box in enumerate(round_variables.sboxes):
            selected = [item for item in box.transitions if assignment.get(item.selector, False)]
            if len(selected) != 1:
                raise ValueError(
                    f"round {round_index} S-box {box_index} has {len(selected)} selectors"
                )
            item = selected[0]
            transitions.append(
                {
                    "sbox_index": box_index,
                    "left": item.left,
                    "right": item.right,
                    "encoded_weight": item.weight,
                    "walsh_sign": item.walsh_sign,
                }
            )
        before = _decode_bits(round_variables.before_sbox, assignment)
        after_sbox = _decode_bits(round_variables.after_sbox, assignment)
        after_linear = _decode_bits(round_variables.after_linear, assignment)
        rounds.append(
            {
                "round_index": round_index,
                f"{prefix}_before_sbox": before,
                f"{prefix}_after_sbox": after_sbox,
                f"{prefix}_before_linear": after_sbox,
                f"{prefix}_after_linear": after_linear,
                "sbox_transitions": transitions,
            }
        )
    return {
        "model_version": model.model_version,
        "mode": model.mode,
        "bit_numbering": "msb0",
        "weight_limit": model.weight_limit,
        "encoded_total_weight": sum(
            1 for variable in model.cost_variables if assignment.get(variable, False)
        ),
        "rounds": rounds,
    }


_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_JOB_HASH_PREFIX_LENGTH = 16
_JOB_ID_PREFIX_LENGTH = 16


def _safe_path_component(value: str, field: str) -> str:
    # A canonical sha256 prefix contains ':' which is invalid in Windows path
    # names.  Preserve the digest and use a portable directory spelling.
    if field == "candidate_hash" and value.startswith("sha256:"):
        value = "sha256-" + value.removeprefix("sha256:")
    if not value or not _SAFE_PATH_COMPONENT.fullmatch(value):
        raise ValueError(f"{field} is not a safe portable path component")
    return value


def _short_candidate_path_component(candidate_hash: str) -> str:
    """Use a short storage key while retaining the full hash in request.json."""

    safe_hash = _safe_path_component(candidate_hash, "candidate_hash")
    digest = safe_hash.removeprefix("sha256-")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("candidate_hash must be a canonical sha256 digest")
    return "h-" + digest[:_JOB_HASH_PREFIX_LENGTH]


def execute_solver_job(
    model: SpnModel,
    solver_executable: str | Path,
    runs_root: str | Path,
    *,
    run_id: str,
    candidate_hash: str,
    request: Mapping[str, object],
    timeout_s: float,
) -> SolverJob:
    """Run a model in the section-9.6 unique job directory layout.

    The caller still validates a returned witness independently; this function
    only decodes named states and preserves every solver artefact.
    """

    safe_run = _safe_path_component(run_id, "run_id")
    safe_hash = _short_candidate_path_component(candidate_hash)
    job_id = "q-" + uuid.uuid4().hex[:_JOB_ID_PREFIX_LENGTH]
    job_directory = (
        Path(runs_root)
        / safe_run
        / "s"
        / safe_hash
        / model.mode[0]
        / job_id
    )
    job_directory.mkdir(parents=True, exist_ok=False)

    request_value = dict(request)
    request_value.update(
        {
            "model_version": model.model_version,
            "mode": model.mode,
            "weight_limit": model.weight_limit,
            "candidate_hash": candidate_hash,
            "job_id": job_id,
        }
    )
    (job_directory / "request.json").write_text(
        json.dumps(request_value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    dimacs = model.cnf.to_dimacs()
    cnf_path = job_directory / "model_or_query.cnf"
    cnf_path.write_text(dimacs, encoding="ascii")
    cnf_hash = "sha256:" + hashlib.sha256(dimacs.encode("ascii")).hexdigest()

    result = run_solver(
        [solver_executable, cnf_path],
        timeout_s=timeout_s,
        require_witness=True,
        cwd=job_directory,
    )
    (job_directory / "solver.stdout").write_text(result.stdout, encoding="utf-8")
    (job_directory / "solver.stderr").write_text(result.stderr, encoding="utf-8")
    witness = (
        decode_witness(model, result.assignment_map()) if result.status == "sat" else None
    )
    (job_directory / "witness.json").write_text(
        json.dumps(witness, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    query_record = {
        "cnf_sha256": cnf_hash,
        "elapsed_s": result.elapsed_s,
        "status": result.status,
        "weight_limit": model.weight_limit,
        "witness_decoded": witness is not None,
    }
    (job_directory / "queries.jsonl").write_text(
        json.dumps(query_record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result_record = {
        **query_record,
        "error": result.error,
        "returncode": result.returncode,
    }
    (job_directory / "result.json").write_text(
        json.dumps(result_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return SolverJob(job_directory, result, witness)
