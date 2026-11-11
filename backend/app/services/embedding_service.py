from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, device="cpu")

def build_embeddings(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.empty((0, 0), dtype=np.float32)
    return _model().encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )


def build_embedding(text: str) -> np.ndarray:
    embeddings = build_embeddings([text])
    return embeddings[0] if len(embeddings) else np.empty((0,), dtype=np.float32)


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if not left.size or not right.size:
        return 0.0
    return float(np.dot(left, right))
