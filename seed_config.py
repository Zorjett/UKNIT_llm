"""Shared random seed configuration.

Set ``UKNIT_SEED`` to reproduce a run.  Without it, each process gets a fresh
seed so independent searches do not start from the same candidate population.
"""

import os
import random
import secrets

import numpy as np


DEFAULT_SEED = None


def _read_seed() -> int:
    raw_seed = os.getenv("UKNIT_SEED")
    if raw_seed is None or not raw_seed.strip():
        return secrets.randbits(32)
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
