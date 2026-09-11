"""Independent recalculation of differential and linear SAT witnesses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .ddt_lat import (
    ddt,
    differential_weight,
    linear_weight,
    validate_sbox,
    walsh_table,
)


@dataclass(frozen=True)
class CheckedTransition:
    """One transition recomputed from state bits, never from SAT cost data."""

    round_index: int
    sbox_index: int
    left: int
    right: int
    table_value: int
    weight: int
    walsh_sign: int | None


@dataclass(frozen=True)
class WitnessCheckResult:
    valid: bool
    recomputed_weight: int | None
    errors: tuple[str, ...]
    round_weights: tuple[int, ...] = ()
    checked_transitions: tuple[CheckedTransition, ...] = ()


def _bits(value: object, size: int, field: str, errors: list[str]) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != size
        or any(bit not in (0, 1) or isinstance(bit, bool) for bit in value)
    ):
        errors.append(f"{field} must contain exactly {size} integer bits")
        return [0] * size
    return value


def _nibbles(bits: Sequence[int]) -> list[int]:
    return [
        (bits[index] << 3)
        | (bits[index + 1] << 2)
        | (bits[index + 2] << 1)
        | bits[index + 3]
        for index in range(0, len(bits), 4)
    ]


def apply_linear_rows(bits: Sequence[int], rows: Sequence[Sequence[int]]) -> list[int]:
    return [sum(bits[index] for index in row) & 1 for row in rows]


def apply_transposed_linear_rows(
    output_side_mask: Sequence[int], rows: Sequence[Sequence[int]]
) -> list[int]:
    """Return M^T beta when rows describe the forward map y=Mx."""

    return [
        sum(
            output_side_mask[output_position]
            for output_position, row in enumerate(rows)
            if input_position in row
        )
        & 1
        for input_position in range(len(output_side_mask))
    ]


def check_witness(
    witness: object,
    sboxes_by_round: Sequence[Sequence[Sequence[int]]],
    linear_rows_by_round: Sequence[Sequence[Sequence[int]] | None],
    *,
    expected_mode: str,
    weight_limit: int | None = None,
    require_nonzero_input: bool = True,
) -> WitnessCheckResult:
    """Recompute feasibility and weight without trusting SAT cost variables."""

    errors: list[str] = []
    if expected_mode not in ("differential", "linear"):
        raise ValueError("expected_mode must be differential or linear")
    if not isinstance(witness, dict):
        return WitnessCheckResult(False, None, ("witness must be an object",))
    if not sboxes_by_round:
        return WitnessCheckResult(False, None, ("network must contain at least one round",))
    if len(sboxes_by_round) != len(linear_rows_by_round):
        return WitnessCheckResult(
            False,
            None,
            ("S-box and linear-layer round counts do not match",),
        )
    box_count = len(sboxes_by_round[0])
    if box_count < 1 or any(len(boxes) != box_count for boxes in sboxes_by_round):
        return WitnessCheckResult(
            False,
            None,
            ("all rounds must contain the same nonzero number of S-boxes",),
        )
    block_size = 4 * box_count
    for round_index, (boxes, rows) in enumerate(
        zip(sboxes_by_round, linear_rows_by_round)
    ):
        for box_index, sbox in enumerate(boxes):
            try:
                validate_sbox(sbox)
            except ValueError as error:
                return WitnessCheckResult(
                    False,
                    None,
                    (f"round {round_index} S-box {box_index} is invalid: {error}",),
                )
        if rows is None:
            if round_index != len(sboxes_by_round) - 1:
                return WitnessCheckResult(
                    False,
                    None,
                    (f"round {round_index} unexpectedly omits its linear layer",),
                )
            continue
        if len(rows) != block_size:
            return WitnessCheckResult(
                False,
                None,
                (f"round {round_index} linear row count must be {block_size}",),
            )
        for row_index, row in enumerate(rows):
            if (
                not isinstance(row, Sequence)
                or isinstance(row, (str, bytes))
                or not row
                or any(
                    isinstance(index, bool)
                    or not isinstance(index, int)
                    or index < 0
                    or index >= block_size
                    for index in row
                )
                or len(set(row)) != len(row)
            ):
                return WitnessCheckResult(
                    False,
                    None,
                    (f"round {round_index} linear row {row_index} is invalid",),
                )
    if weight_limit is not None and (
        isinstance(weight_limit, bool)
        or not isinstance(weight_limit, int)
        or weight_limit < 0
    ):
        return WitnessCheckResult(
            False,
            None,
            ("weight_limit must be a non-negative integer when present",),
        )
    if witness.get("mode") != expected_mode:
        errors.append("witness mode does not match the requested mode")
    rounds = witness.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != len(sboxes_by_round):
        return WitnessCheckResult(False, None, tuple(errors + ["round count mismatch"]))

    prefix = "difference" if expected_mode == "differential" else "mask"
    total_weight = 0
    round_weights: list[int] = []
    checked_transitions: list[CheckedTransition] = []
    previous_after_linear: list[int] | None = None
    for round_index, (round_value, boxes, rows) in enumerate(
        zip(rounds, sboxes_by_round, linear_rows_by_round)
    ):
        if not isinstance(round_value, dict):
            errors.append(f"round {round_index} is not an object")
            continue
        if (
            "round_index" in round_value
            and round_value["round_index"] != round_index
        ):
            errors.append(f"round {round_index} carries an inconsistent round_index")
        before = _bits(
            round_value.get(f"{prefix}_before_sbox"),
            block_size,
            f"rounds[{round_index}].{prefix}_before_sbox",
            errors,
        )
        after_sbox = _bits(
            round_value.get(f"{prefix}_after_sbox"),
            block_size,
            f"rounds[{round_index}].{prefix}_after_sbox",
            errors,
        )
        before_linear = _bits(
            round_value.get(f"{prefix}_before_linear"),
            block_size,
            f"rounds[{round_index}].{prefix}_before_linear",
            errors,
        )
        after_linear = _bits(
            round_value.get(f"{prefix}_after_linear"),
            block_size,
            f"rounds[{round_index}].{prefix}_after_linear",
            errors,
        )
        if previous_after_linear is not None and before != previous_after_linear:
            errors.append(f"round {round_index} input is not linked to prior linear output")
        if after_sbox != before_linear:
            errors.append(f"round {round_index} before-linear state differs from S-box output")

        round_weight = 0
        for box_index, (left, right, sbox) in enumerate(
            zip(_nibbles(before), _nibbles(after_sbox), boxes)
        ):
            if expected_mode == "differential":
                table = ddt(sbox)
                table_value = table[left][right]
                if table_value == 0:
                    errors.append(
                        f"round {round_index} S-box {box_index} has impossible DDT transition"
                    )
                    continue
                weight = differential_weight(table, left, right)
                sign = None
            else:
                table = walsh_table(sbox)
                table_value = table[left][right]
                if table_value == 0:
                    errors.append(
                        f"round {round_index} S-box {box_index} has zero Walsh transition"
                    )
                    continue
                weight = linear_weight(table, left, right)
                sign = 1 if table_value > 0 else -1
            total_weight += weight
            round_weight += weight
            checked_transitions.append(
                CheckedTransition(
                    round_index=round_index,
                    sbox_index=box_index,
                    left=left,
                    right=right,
                    table_value=table_value,
                    weight=weight,
                    walsh_sign=sign,
                )
            )
        round_weights.append(round_weight)

        if rows is None:
            if after_linear != after_sbox:
                errors.append(f"round {round_index} omitted L but changed the state")
        elif expected_mode == "differential":
            if after_linear != apply_linear_rows(after_sbox, rows):
                errors.append(f"round {round_index} violates y=Mx")
        else:
            if before_linear != apply_transposed_linear_rows(after_linear, rows):
                errors.append(f"round {round_index} violates alpha=M^T beta")
        previous_after_linear = after_linear

    first = rounds[0]
    first_bits = first.get(f"{prefix}_before_sbox", []) if isinstance(first, dict) else []
    if require_nonzero_input and not any(first_bits):
        errors.append("first-round input is all zero")
    if weight_limit is not None and total_weight > weight_limit:
        errors.append(f"recomputed weight {total_weight} exceeds limit {weight_limit}")
    encoded = witness.get("encoded_total_weight")
    if encoded is not None:
        if isinstance(encoded, bool) or not isinstance(encoded, int) or encoded < 0:
            errors.append("encoded_total_weight must be a non-negative integer when present")
        elif encoded != total_weight:
            errors.append(
                f"encoded weight {encoded!r} differs from independent value {total_weight}"
            )
    return WitnessCheckResult(
        not errors,
        total_weight,
        tuple(errors),
        tuple(round_weights),
        tuple(checked_transitions),
    )
