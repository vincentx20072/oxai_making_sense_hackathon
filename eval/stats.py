"""Pure-Python statistics for the pilot: Wilson CI and AUROC."""

import math


def wilson_ci(k, n, z=1.959963984540054):
    """95% Wilson score interval for k successes out of n. Returns (lo, hi)."""
    if n <= 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def auroc(scores, labels):
    """P(score of a positive > score of a negative), ties count 1/2.
    labels are truthy for positives. Returns None if a class is empty."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def rate_ci(k, n):
    lo, hi = wilson_ci(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "lo": lo, "hi": hi}
