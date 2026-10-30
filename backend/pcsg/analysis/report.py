"""One reporting helper shared by scripts/analysis_v2/*.py.

`rnd` formats a reported metric to a FIXED number of decimals and returns a string.
Fixed-decimal formatting (not bare `round()`) keeps a regenerated value byte-identical
across numpy / scipy / scikit-learn minor versions, whose optimizers and reductions can
otherwise wobble the 4th+ decimal and flip a printed digit (e.g. 0.055 -> 0.0548). Default
is 3 decimals; pass nd=4 for the few metrics the paper reports to 4 decimals.
"""
from __future__ import annotations


def rnd(x, nd: int = 3) -> str:
    x = float(x)
    if x != x:  # NaN -> literal (also keeps the JSON sidecars valid)
        return "nan"
    return f"{x:.{nd}f}"
