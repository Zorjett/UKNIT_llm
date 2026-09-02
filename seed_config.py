"""Shared random seed configuration.

Set ``UKNIT_SEED`` to reproduce a different run without editing source files.
"""

import os
import random

import numpy as np


DEFAULT_SEED = 20260831


def _read_seed() -> int:
    raw_seed = os.getenv("UKNIT_SEED", str(DEFAULT_SEED))
    try:
        seed = int(raw_seed)
    except ValueError as exc:
        raise ValueError("UKNIT_SEED must be an integer") from exc
    if not 0 <= seed < 2**32:
        raise ValueError("UKNIT_SEED must be between 0 and 2**32 - 1")
    return seed


SEED = _read_seed()


def set_global_seed(seed: int = SEED):
    random.seed(seed)
    np.random.seed(seed)
    return seed
