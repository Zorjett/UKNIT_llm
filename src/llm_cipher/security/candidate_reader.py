"""Read, validate, hash, and summarize v0.2/v0.3 CandidateSpec documents.

This module is deliberately standard-library only so milestone 0 can be
verified before the SAT and numerical dependencies are introduced.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn


SCHEMA_VERSION = "candidate-spec-v0.3"
LEGACY_SCHEMA_VERSION = "candidate-spec-v0.2"
SUPPORTED_SCHEMA_VERSIONS = (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION)
BLOCK_SIZE = 64
MIN_NUM_ROUNDS = 3
MAX_NUM_ROUNDS = 12
SUPPORTED_ROUNDS = tuple(range(MIN_NUM_ROUNDS, MAX_NUM_ROUNDS + 1))
LEGACY_NUM_ROUNDS = 4
SBOXES_PER_ROUND = 16
SBOX_SIZE = 16
ROUND_LAYOUT = "ark-s-l_except_last-final_ark"
STATE_ENCODING = {
    "bit_numbering": "msb0",
    "nibble_order": "left_to_right",
    "external_state_format": "hex16",
}
MANTIS = [12, 10, 13, 3, 14, 11, 15, 7, 8, 9, 1, 5, 0, 2, 4, 6]

TOP_LEVEL_FIELDS = {
    "schema_version",
    "candidate_id",
    "candidate_hash",
    "block_size",
    "num_rounds",
    "state_encoding",
    "round_layout",
    "rounds",
}
ROUND_FIELDS = {"sboxes", "linear_rows"}
HASH_FIELDS = (
    "block_size",
    "num_rounds",
    "state_encoding",
    "round_layout",
    "rounds",
)


@dataclass(frozen=True)
class CandidateValidationError(ValueError):
    """A field-addressable CandidateSpec validation failure."""

    code: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} at {self.field}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


def _fail(code: str, field: str, message: str) -> NoReturn:
    raise CandidateValidationError(code=code, field=field, message=message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key", key, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_exact_fields(
    value: dict[str, Any], expected: set[str], field: str
) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing:
        _fail("missing_field", field, f"missing required fields: {missing}")
    if unknown:
        _fail("unknown_field", field, f"unknown fields: {unknown}")


def _bit_at_msb0(value: int, position: int) -> int:
    return (value >> (3 - position)) & 1


def _permute_nibble_msb0(value: int, permutation: tuple[int, ...]) -> int:
    result = 0
    for output_position, input_position in enumerate(permutation):
        result |= _bit_at_msb0(value, input_position) << (3 - output_position)
    return result


def _allowed_mantis_variants() -> frozenset[tuple[int, ...]]:
    variants: set[tuple[int, ...]] = set()
    permutations = tuple(itertools.permutations(range(4)))
    for input_permutation in permutations:
        for output_permutation in permutations:
            table = tuple(
                _permute_nibble_msb0(
                    MANTIS[_permute_nibble_msb0(x, input_permutation)],
                    output_permutation,
                )
                for x in range(SBOX_SIZE)
            )
            variants.add(table)
    return frozenset(variants)


ALLOWED_MANTIS_VARIANTS = _allowed_mantis_variants()


def _validate_sbox(sbox: Any, field: str) -> None:
    if not isinstance(sbox, list):
        _fail("invalid_type", field, "S-box must be a JSON array")
    if len(sbox) != SBOX_SIZE:
        _fail("invalid_sbox_length", field, "S-box must contain 16 entries")
    if any(not _is_plain_int(item) for item in sbox):
        _fail("invalid_sbox_value", field, "S-box entries must be integers")
    if sorted(sbox) != list(range(SBOX_SIZE)):
        _fail(
            "invalid_sbox_permutation",
            field,
            "S-box must be a permutation of 0..15",
        )
    if tuple(sbox) not in ALLOWED_MANTIS_VARIANTS:
        _fail(
            "sbox_outside_design_space",
            field,
            "S-box is not a MANTIS input/output MSB0 bit-permutation variant",
        )


def gf2_rank(linear_rows: list[list[int]], size: int = BLOCK_SIZE) -> int:
    """Return the rank of a row-index matrix over GF(2)."""

    packed_rows = [sum(1 << column for column in row) for row in linear_rows]
    rank = 0
    for column in range(size):
        pivot = next(
            (
                row_index
                for row_index in range(rank, size)
                if (packed_rows[row_index] >> column) & 1
            ),
            None,
        )
        if pivot is None:
            continue
        packed_rows[rank], packed_rows[pivot] = (
            packed_rows[pivot],
            packed_rows[rank],
        )
        for row_index in range(size):
            if row_index != rank and ((packed_rows[row_index] >> column) & 1):
                packed_rows[row_index] ^= packed_rows[rank]
        rank += 1
        if rank == size:
            break
    return rank


def _validate_linear_rows(linear_rows: Any, field: str) -> None:
    if not isinstance(linear_rows, list):
        _fail("invalid_type", field, "linear_rows must be a JSON array")
    if len(linear_rows) != BLOCK_SIZE:
        _fail(
            "invalid_linear_row_count",
            field,
            f"linear layer must contain {BLOCK_SIZE} rows",
        )
    for row_index, row in enumerate(linear_rows):
        row_field = f"{field}[{row_index}]"
        if not isinstance(row, list):
            _fail("invalid_type", row_field, "matrix row must be a JSON array")
        if any(not _is_plain_int(column) for column in row):
            _fail("invalid_linear_index", row_field, "indices must be integers")
        if any(column < 0 or column >= BLOCK_SIZE for column in row):
            _fail(
                "linear_index_out_of_range",
                row_field,
                "indices must be in the range 0..63",
            )
        if len(set(row)) != len(row):
            _fail(
                "duplicate_linear_index",
                row_field,
                "indices within one matrix row must be unique",
            )
    rank = gf2_rank(linear_rows)
    if rank != BLOCK_SIZE:
        _fail(
            "singular_linear_matrix",
            field,
            f"matrix rank over GF(2) is {rank}; expected {BLOCK_SIZE}",
        )


def semantic_spec(candidate: dict[str, Any]) -> dict[str, Any]:
    """Build the exact semantic object covered by candidate_hash."""

    semantic = {field: deepcopy(candidate[field]) for field in HASH_FIELDS}
    for round_spec in semantic["rounds"]:
        if round_spec["linear_rows"] is not None:
            round_spec["linear_rows"] = [
                sorted(row) for row in round_spec["linear_rows"]
            ]
    return semantic


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def candidate_hash(candidate: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes(semantic_spec(candidate))).hexdigest()
    return "sha256:" + digest


def validate_analysis_window(
    candidate: Any,
    start_round: Any,
    num_rounds: Any,
) -> tuple[int, int]:
    """Validate a project-candidate analysis window and return its bounds.

    The candidate itself is validated first so callers cannot use a plausible
    window to bypass CandidateSpec checks.  The returned tuple is
    ``(start_round, exclusive_end_round)``.
    """

    validated = validate_candidate(candidate)
    if not _is_plain_int(start_round) or start_round < 0:
        _fail(
            "invalid_analysis_start_round",
            "start_round",
            "must be a non-negative integer",
        )
    if not _is_plain_int(num_rounds) or num_rounds < 1:
        _fail(
            "invalid_analysis_num_rounds",
            "num_rounds",
            "must be a positive integer",
        )

    candidate_rounds = validated["num_rounds"]
    if start_round >= candidate_rounds:
        _fail(
            "analysis_window_out_of_range",
            "start_round",
            (
                f"start_round {start_round} is outside candidate round "
                f"range [0, {candidate_rounds})"
            ),
        )

    end_round = start_round + num_rounds
    if end_round > candidate_rounds:
        _fail(
            "analysis_window_out_of_range",
            "num_rounds",
            (
                f"window [{start_round}, {end_round}) exceeds candidate "
                f"round range [0, {candidate_rounds})"
            ),
        )
    return start_round, end_round


def validate_candidate(candidate: Any) -> dict[str, Any]:
    """Validate a CandidateSpec without silently changing it."""

    if not isinstance(candidate, dict):
        _fail("invalid_type", "$", "CandidateSpec must be a JSON object")
    _require_exact_fields(candidate, TOP_LEVEL_FIELDS, "$")

    schema_version = candidate["schema_version"]
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        _fail(
            "unsupported_schema_version",
            "schema_version",
            f"expected one of {SUPPORTED_SCHEMA_VERSIONS}",
        )
    if not isinstance(candidate["candidate_id"], str) or not candidate["candidate_id"]:
        _fail("invalid_candidate_id", "candidate_id", "must be a non-empty string")
    if not isinstance(candidate["candidate_hash"], str):
        _fail("invalid_candidate_hash", "candidate_hash", "must be a string")
    if not _is_plain_int(candidate["block_size"]) or candidate["block_size"] != BLOCK_SIZE:
        _fail("invalid_block_size", "block_size", f"must equal {BLOCK_SIZE}")
    num_rounds = candidate["num_rounds"]
    if not _is_plain_int(num_rounds):
        _fail("invalid_num_rounds", "num_rounds", "must be an integer")
    if schema_version == LEGACY_SCHEMA_VERSION:
        if num_rounds != LEGACY_NUM_ROUNDS:
            _fail(
                "invalid_num_rounds",
                "num_rounds",
                f"legacy {LEGACY_SCHEMA_VERSION} must equal {LEGACY_NUM_ROUNDS}",
            )
    elif num_rounds not in SUPPORTED_ROUNDS:
        _fail(
            "invalid_num_rounds",
            "num_rounds",
            f"must be in the inclusive range {MIN_NUM_ROUNDS}..{MAX_NUM_ROUNDS}",
        )

    if candidate["state_encoding"] != STATE_ENCODING:
        _fail(
            "unsupported_state_encoding",
            "state_encoding",
            f"must equal {STATE_ENCODING}",
        )
    if candidate["round_layout"] != ROUND_LAYOUT:
        _fail(
            "unsupported_round_layout",
            "round_layout",
            f"must equal {ROUND_LAYOUT}",
        )

    rounds = candidate["rounds"]
    if not isinstance(rounds, list):
        _fail("invalid_type", "rounds", "must be a JSON array")
    if len(rounds) != candidate["num_rounds"]:
        _fail(
            "round_count_mismatch",
            "rounds",
            "length must equal num_rounds",
        )

    for round_index, round_spec in enumerate(rounds):
        round_field = f"rounds[{round_index}]"
        if not isinstance(round_spec, dict):
            _fail("invalid_type", round_field, "round must be a JSON object")
        _require_exact_fields(round_spec, ROUND_FIELDS, round_field)
        sboxes = round_spec["sboxes"]
        if not isinstance(sboxes, list):
            _fail("invalid_type", f"{round_field}.sboxes", "must be a JSON array")
        if len(sboxes) != SBOXES_PER_ROUND:
            _fail(
                "invalid_sbox_count",
                f"{round_field}.sboxes",
                f"each round must contain {SBOXES_PER_ROUND} S-boxes",
            )
        for sbox_index, sbox in enumerate(sboxes):
            _validate_sbox(sbox, f"{round_field}.sboxes[{sbox_index}]")

        linear_rows = round_spec["linear_rows"]
        if round_index == num_rounds - 1:
            if linear_rows is not None:
                _fail(
                    "unexpected_final_linear_layer",
                    f"{round_field}.linear_rows",
                    "the final project-candidate round must omit the linear layer",
                )
        else:
            _validate_linear_rows(linear_rows, f"{round_field}.linear_rows")

    supplied_hash = candidate["candidate_hash"]
    recomputed_hash = candidate_hash(candidate)
    if supplied_hash != recomputed_hash:
        _fail(
            "candidate_hash_mismatch",
            "candidate_hash",
            f"supplied {supplied_hash}; recomputed {recomputed_hash}",
        )
    return candidate


def load_candidate(path: str | Path) -> dict[str, Any]:
    candidate_path = Path(path)
    try:
        with candidate_path.open("r", encoding="utf-8") as input_file:
            candidate = json.load(input_file, object_pairs_hook=_reject_duplicate_keys)
    except CandidateValidationError:
        raise
    except FileNotFoundError:
        _fail("file_not_found", "$", f"candidate file not found: {candidate_path}")
    except OSError as error:
        _fail("file_read_error", "$", str(error))
    except json.JSONDecodeError as error:
        _fail(
            "invalid_json",
            "$",
            f"line {error.lineno}, column {error.colno}: {error.msg}",
        )
    return validate_candidate(candidate)


def _sha256_of_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the stable milestone-0 summary required by section 3.4."""

    validate_candidate(candidate)
    round_sbox_hashes = [
        [_sha256_of_json(sbox) for sbox in round_spec["sboxes"]]
        for round_spec in candidate["rounds"]
    ]
    linear_layer_hashes = [
        None
        if round_spec["linear_rows"] is None
        else _sha256_of_json([sorted(row) for row in round_spec["linear_rows"]])
        for round_spec in candidate["rounds"]
    ]
    return {
        "schema_version": candidate["schema_version"],
        "candidate_hash": candidate["candidate_hash"],
        "num_rounds": candidate["num_rounds"],
        "round_sbox_sha256": round_sbox_hashes,
        "linear_layer_sha256": linear_layer_hashes,
        "state_encoding": deepcopy(candidate["state_encoding"]),
        "round_layout": candidate["round_layout"],
    }


def load_summary(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as input_file:
            value = json.load(input_file, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as error:
        _fail("summary_read_error", "$", str(error))
    if not isinstance(value, dict):
        _fail("invalid_summary", "$", "expected summary must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and summarize a candidate-spec-v0.2 JSON file."
    )
    parser.add_argument("candidate", type=Path, help="path to CandidateSpec JSON")
    parser.add_argument(
        "--expect-summary",
        type=Path,
        help="fail unless the generated summary equals this JSON fixture",
    )
    args = parser.parse_args(argv)

    try:
        summary = candidate_summary(load_candidate(args.candidate))
        if args.expect_summary is not None:
            expected = load_summary(args.expect_summary)
            if summary != expected:
                _fail(
                    "summary_mismatch",
                    "$",
                    f"generated summary differs from {args.expect_summary}",
                )
    except CandidateValidationError as error:
        print(
            json.dumps(error.as_dict(), ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
        return 2

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
