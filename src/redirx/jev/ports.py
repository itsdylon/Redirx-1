"""The two external capabilities the URL matching algorithm needs.

Implementations own provider calls, caching and accounting. The algorithm has no
knowledge of Flask, queue leases, PostgreSQL or a particular provider SDK.
"""

from typing import Protocol

import numpy as np


class JudgmentProvider(Protocol):
    def ask(self, state: dict, questions: dict) -> dict:
        """Return validated answers, input-token usage, latency and cache status."""
        ...


class EmbeddingCache(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return normalized vectors in input order, including duplicate inputs."""
        ...
