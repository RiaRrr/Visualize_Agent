"""Independent numerical spot checks for two holdout answer ranges."""
from __future__ import annotations

import math


def parabola_ratio(t: float) -> float:
    return (t * t + 4 * t + 1) / (3 * (t + 2) * (2 * t + 1))


def conic_product(k: float) -> float:
    return 3 * (1 + k * k) / (1 + 2 * k * k)


def main() -> None:
    print("triangle hypotenuse:", math.hypot(3, 4))
    print("parabola samples t=2.001,3,1000:", *(round(parabola_ratio(t), 9) for t in (2.001, 3, 1000)))
    upper_k = 1 + 3 * math.sqrt(2) / 2
    positive = [conic_product(1 + (upper_k - 1) * i / 100000) for i in range(1, 100000)
                if abs(1 + (upper_k - 1) * i / 100000 - 3) > 1e-12]
    negative = [conic_product(-1 + .8 * i / 100000) for i in range(1, 100000)]
    print("conic sampled min/max:", min(positive + negative), max(positive + negative))
    print("conic analytic endpoints:", (14 - math.sqrt(2)) / 8, 26 / 9)
    print("excluded interior values:", 2, 30 / 19)


if __name__ == "__main__":
    main()
