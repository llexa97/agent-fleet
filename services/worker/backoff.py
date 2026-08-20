"""Backoff exponentiel borné avec jitter injectable pour des tests déterministes."""

from __future__ import annotations

import random
from collections.abc import Callable


class ExponentialBackoff:
    def __init__(
        self,
        initial: float,
        maximum: float,
        jitter_ratio: float,
        *,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self._initial = initial
        self._maximum = maximum
        self._jitter_ratio = jitter_ratio
        self._random = random_value
        self._attempt = 0

    def next_delay(self) -> float:
        base = min(self._maximum, self._initial * (2**self._attempt))
        self._attempt += 1
        # Facteur uniforme dans [1-jitter, 1+jitter].
        factor = 1 - self._jitter_ratio + 2 * self._jitter_ratio * self._random()
        return float(max(0.0, base * factor))

    def reset(self) -> None:
        self._attempt = 0
