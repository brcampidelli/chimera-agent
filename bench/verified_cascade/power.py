"""The power numbers PREREGISTRATION.md §6 quotes, from exact enumeration. US$ 0, stdlib only.

The primary test is an exact two-sided McNemar on wrong answers shipped, arm X against arm A, over
the same items. Its discordant pairs are b (A shipped a wrong answer, X did not) and c (X shipped a
wrong answer, A did not). The model behind the numbers, stated so it can be wrong:

* each item: A ships a wrong answer with probability ``p_a``;
* X removes that wrong answer with probability ``r`` (the verifier rejects it, and what replaces it
  is not wrong: a hand-off, or a correct escalation);
* X ships a new wrong answer on an item A got right with probability ``q`` (a false rejection whose
  escalation comes back wrong and is passed), fixed at 0.003.

The alpha is 0.025: the first step of Holm over the two deciding comparisons (§8).

    python bench/verified_cascade/power.py
"""

from __future__ import annotations

import math
from math import comb

ALPHA = 0.025
Q = 0.003


def mcnemar_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)


def power(n: int, p_a: float, r: float, q: float = Q, alpha: float = ALPHA) -> float:
    p1, p2 = p_a * r, (1 - p_a) * q
    p0 = 1 - p1 - p2
    total = 0.0
    for b in range(min(n, 120) + 1):
        pb = comb(n, b) * p1**b
        for c in range(min(n - b, 40) + 1):
            pr = pb * comb(n - b, c) * p2**c * p0 ** (n - b - c)
            if pr > 1e-14 and b > c and mcnemar_p(b, c) < alpha:
                total += pr
    return total


def power_given_w(w: int, r: float, lam: float, alpha: float = ALPHA) -> float:
    """Power once A's wrong answers are counted (w): b ~ Bin(w, r), c ~ Poisson(lam)."""
    total = 0.0
    for b in range(w + 1):
        pb = comb(w, b) * r**b * (1 - r) ** (w - b)
        for c in range(30):
            pc = math.exp(-lam) * lam**c / math.factorial(c)
            if b > c and mcnemar_p(b, c) < alpha:
                total += pb * pc
    return total


def main() -> None:
    print("smallest possible p with c = 0:", {b: round(mcnemar_p(b, 0), 4) for b in range(3, 9)})
    print(f"\npower, alpha {ALPHA}, q {Q}  (rows p_a, r; columns n)")
    ns = (50, 200, 400, 600, 800)
    print("p_a   r    " + "  ".join(f"n={n:<4}" for n in ns))
    for p_a in (0.02, 0.03, 0.05, 0.08):
        for r in (0.4, 0.6, 0.8):
            print(f"{p_a:<5} {r:<4} " + "  ".join(f"{power(n, p_a, r):<6.2f}" for n in ns))
    print("\npower given A's wrong count w, c ~ Poisson(1.5)  (columns r = 0.4, 0.6, 0.8)")
    for w in (8, 10, 12, 15, 18, 20, 25, 30, 40):
        print(f"w={w:<3} " + "  ".join(f"{power_given_w(w, r, 1.5):.2f}" for r in (0.4, 0.6, 0.8)))


if __name__ == "__main__":
    main()
