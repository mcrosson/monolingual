"""External-consumer test harness (M0).

Subprocess wrappers around independent tools used as correctness oracles.
Each wrapper raises ``RuntimeError`` with a milestone pointer if the tool
is not on PATH; auto-activates when the tool lands in its owning milestone.
"""
