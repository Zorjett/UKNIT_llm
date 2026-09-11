"""Independent 4-bit S-box DDT, Walsh, weight, and bit-permutation tools.

The implementation intentionally depends only on the Python standard library.
It is the trusted mathematical baseline for later SAT witness validation.
"""

from __future__ import annotations

from math import log2
from typing import Sequence


SBOX_BITS = 4
SBOX_SIZE = 1 << SBOX_BITS
MANTIS = [12, 10, 13, 3, 14, 11, 15, 7, 8, 9, 1, 5, 0, 2, 4, 6]


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parity(value: int) -> int:
    """Return the XOR of all bits in a non-negative integer."""

    if not _is_plain_int(value) or value < 0:
        raise ValueError("parity input must be a non-negative integer")
    return value.bit_count() & 1


def validate_sbox(sbox: Sequence[int]) -> None:
    """Require a 4-bit permutation S-box represented by 16 integers."""

    if isinstance(sbox, (str, bytes)) or not isinstance(sbox, Sequence):
        raise ValueError("S-box must be a sequence of 16 integers")
    if len(sbox) != SBOX_SIZE:
        raise ValueError("S-box must contain exactly 16 entries")
    if any(not _is_plain_int(value) for value in sbox):
        raise ValueError("S-box entries must be integers")
    if sorted(sbox) != list(range(SBOX_SIZE)):
        raise ValueError("S-box must be a permutation of 0..15")


def ddt(sbox: Sequence[int]) -> list[list[int]]:
    """Return DDT[a][b] for every 4-bit input/output difference."""

    validate_sbox(sbox)
    table = [[0] * SBOX_SIZE for _ in range(SBOX_SIZE)]
    for input_difference in range(SBOX_SIZE):
        for x in range(SBOX_SIZE):
            output_difference = sbox[x] ^ sbox[x ^ input_difference]
            table[input_difference][output_difference] += 1
    return table


def walsh_table(sbox: Sequence[int]) -> list[list[int]]:
    """Return the full Walsh table W[u][v], not the author's half-Walsh LAT."""

    validate_sbox(sbox)
    table = [[0] * SBOX_SIZE for _ in range(SBOX_SIZE)]
    for input_mask in range(SBOX_SIZE):
        for output_mask in range(SBOX_SIZE):
            table[input_mask][output_mask] = sum(
                1
                if parity(input_mask & x)
                == parity(output_mask & sbox[x])
                else -1
                for x in range(SBOX_SIZE)
            )
    return table


def exact_weight(numerator: int, denominator: int = SBOX_SIZE) -> int:
    """Return -log2(numerator/denominator) only when it is finite/integer."""

    if not _is_plain_int(numerator) or not _is_plain_int(denominator):
        raise ValueError("weight numerator and denominator must be integers")
    if denominator <= 0:
        raise ValueError("weight denominator must be positive")
    if numerator <= 0:
        raise ValueError("impossible or zero-correlation transition")
    if numerator > denominator:
        raise ValueError("transition magnitude cannot exceed the denominator")

    value = -log2(numerator / denominator)
    rounded = round(value)
    if abs(value - rounded) > 1e-12:
        raise ValueError("non-integer weight is outside v0.1 design space")
    return int(rounded)


def differential_weight(table: Sequence[Sequence[int]], a: int, b: int) -> int:
    """Return the exact weight of one DDT transition."""

    _validate_table_indices(a, b)
    return exact_weight(table[a][b], SBOX_SIZE)


def linear_weight(table: Sequence[Sequence[int]], u: int, v: int) -> int:
    """Return the exact weight based on the absolute full-Walsh coefficient."""

    _validate_table_indices(u, v)
    return exact_weight(abs(table[u][v]), SBOX_SIZE)


def _validate_table_indices(left: int, right: int) -> None:
    if (
        not _is_plain_int(left)
        or not _is_plain_int(right)
        or not 0 <= left < SBOX_SIZE
        or not 0 <= right < SBOX_SIZE
    ):
        raise ValueError("table indices must be integers in the range 0..15")


def validate_bit_permutation(permutation: Sequence[int]) -> tuple[int, ...]:
    """Validate a four-position MSB0 bit permutation and return a tuple."""

    if (
        isinstance(permutation, (str, bytes))
        or not isinstance(permutation, Sequence)
        or len(permutation) != SBOX_BITS
        or any(not _is_plain_int(position) for position in permutation)
        or sorted(permutation) != list(range(SBOX_BITS))
    ):
        raise ValueError("bit permutation must be a permutation of [0, 1, 2, 3]")
    return tuple(permutation)


def permute_nibble_msb0(value: int, permutation: Sequence[int]) -> int:
    """Apply P_p(x)[j] = x[p[j]], where bit position 0 is the MSB."""

    if not _is_plain_int(value) or not 0 <= value < SBOX_SIZE:
        raise ValueError("nibble value must be an integer in the range 0..15")
    checked = validate_bit_permutation(permutation)
    result = 0
    for output_position, input_position in enumerate(checked):
        input_bit = (value >> (SBOX_BITS - 1 - input_position)) & 1
        result |= input_bit << (SBOX_BITS - 1 - output_position)
    return result


def permuted_sbox(
    base_sbox: Sequence[int],
    input_permutation: Sequence[int],
    output_permutation: Sequence[int],
) -> list[int]:
    """Return S_new(x) = P_out(S_base(P_in(x))) using MSB0 positions."""

    validate_sbox(base_sbox)
    input_checked = validate_bit_permutation(input_permutation)
    output_checked = validate_bit_permutation(output_permutation)
    result = [
        permute_nibble_msb0(
            base_sbox[permute_nibble_msb0(x, input_checked)], output_checked
        )
        for x in range(SBOX_SIZE)
    ]
    validate_sbox(result)
    return result


def ddt_spectrum(sbox: Sequence[int]) -> tuple[int, ...]:
    """Return the sorted multiset of all DDT entries."""

    return tuple(sorted(value for row in ddt(sbox) for value in row))


def walsh_absolute_spectrum(sbox: Sequence[int]) -> tuple[int, ...]:
    """Return the sorted multiset of absolute full-Walsh entries."""

    return tuple(
        sorted(abs(value) for row in walsh_table(sbox) for value in row)
    )
