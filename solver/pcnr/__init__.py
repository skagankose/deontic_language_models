"""pcnr — proof-carrying non-monotonic reasoner.

A re-implementation of the symbolic layer of Ulizko et al. (2026), "Proof-Carrying
Neuro-Symbolic Reasoning for Non-Monotonic Legal Decision Support with LLMs",
Big Data Cogn. Comput. 10:295. Standard library only.
"""

from .pipeline import Config, Result, run

__all__ = ["Config", "Result", "run"]
