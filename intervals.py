"""
Confidence intervals for the rates Alibi and Jenga report.

    wilson(k, n)            95% Wilson score interval for k of n (Wilson, 1927).
                            Stays inside [0, 1] and behaves at k = 0 or k = n,
                            where the textbook p +- 1.96 SE interval collapses.
    newcombe(k1, n1, k2, n2)  95% interval for the difference p1 - p2
                            (Newcombe, 1998, hybrid score method 10).
    rate(k, n)              {"k", "n", "p", "ci"} for the UI and JSON.

Why the intervals are wide: with n runs, the data can only rule out rates far
from what was seen.  0 of 5 means "rarely", not "never": a true rate of 40%
still gives 0 of 5 about 8% of the time, so 40% stays inside the interval.
Halving the width needs about four times the runs.
"""

import math

Z95 = 1.959963984540054


def wilson(k, n, z=Z95):
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def newcombe(k1, n1, k2, n2):
    if not (n1 and n2):
        return None
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    d = p1 - p2
    return [round(max(-1.0, d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)), 3),
            round(min(1.0, d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)), 3)]


def rate(k, n):
    return {"k": k, "n": n, "p": round(k / n, 3) if n else None, "ci": wilson(k, n)}
