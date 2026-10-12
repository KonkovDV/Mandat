"""Статистика оценки: интервал Вилсона, размер выборки, κ Коэна.

Перенесено из AeroBIM (``study_design.wilson_interval``, ``eval_statistics.cohen_kappa``).
Источник формул: Brown, Cai, DasGupta (2001), Statistical Science 16(2); Cohen (1960).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

Z95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lower = 0.0 if successes == 0 else max(0.0, centre - half)
    upper = 1.0 if successes == n else min(1.0, centre + half)
    return (lower, upper)


def required_n(p: float, half_width: float, z: float = Z95) -> int:
    """Объём выборки для доли p с заданной полушириной (нормальное приближение)."""
    return math.ceil(z * z * p * (1 - p) / (half_width * half_width))


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("нужны две разметки одинаковой ненулевой длины")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)
