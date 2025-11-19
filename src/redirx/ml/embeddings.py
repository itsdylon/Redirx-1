from __future__ import annotations

from typing import List, Dict, Tuple, Optional, Any
import re
import numpy as np
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer


# Type alias for float32 embedding matrices/vectors
EmbeddingMatrix = NDArray[np.float32]
EmbeddingVector = NDArray[np.float32]


# =========================
#  EMBEDDING SIDE
# =========================

class HtmlEmbedder:
    """
    Handles:
      - HTML -> cleaned text
      - chunking
      - embedding single docs or corpora

    You can reuse this instance for many corpora and queries.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        chunk_size: int = 256,
        chunk_overlap: int = 64,
    ) -> None:
        self.model: SentenceTransformer = SentenceTransformer(model_name)
        self.chunk_size: int = chunk_size
        self.chunk_overlap: int = chunk_overlap

    # ---- HTML utilities ----

    @staticmethod
    def strip_html_tags(html: str) -> str:
        """
        Simple HTML -> text cleaner.
        Replace with BeautifulSoup if you want higher fidelity.
        """
        # Remove script/style blocks
        html_cleaned: str = re.sub(
            r"<(script|style).*?>.*?</\1>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Remove tags
        text: str = re.sub(r"<[^>]+>", " ", html_cleaned)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def chunk_text(self, text: str) -> List[str]:
        """
        Split text into overlapping word-based chunks.
        Works for both small and very large texts.

        - Short texts: 1 chunk.
        - Huge texts: many overlapping chunks.
        """
        words: List[str] = text.split()
        if not words:
            return []

        if len(words) <= self.chunk_size:
            return [" ".join(words)]

        chunks: List[str] = []
        start: int = 0
        while start < len(words):
            end: int = start + self.chunk_size
            chunk: List[str] = words[start:end]
            chunks.append(" ".join(chunk))
            if end >= len(words):
                break
            start = max(end - self.chunk_overlap, start + 1)

        return chunks

    # ---- Embedding APIs ----

    def embed_html(self, html: str) -> EmbeddingVector: # type: ignore
        """
        Clean, chunk, embed a single HTML string and
        return a single document-level embedding (mean of chunk embeddings).
        """
        clean_text: str = self.strip_html_tags(html)
        chunks: List[str] = self.chunk_text(clean_text)
        if not chunks:
            chunks = [""]

        chunk_embs: EmbeddingMatrix = self.model.encode( # type: ignore
            chunks,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ).astype(np.float32)  # (K, D)

        emb: EmbeddingVector = chunk_embs.mean(axis=0) # type: ignore
        return emb

    def embed_corpus(self, docs: List[Dict[str, str]]) -> Tuple[List[str], EmbeddingMatrix]: # type: ignore
        """
        Embed a corpus of documents.

        docs: list of {"html": "<html...>", "url": "https://..."}
        Returns:
          - urls: List[str]
          - embeddings: np.ndarray of shape (N, D)
        """
        urls: List[str] = []
        corpus_chunks: List[List[str]] = []

        for d in docs:
            html: str = d["html"]
            url: str = d["url"]
            clean_text: str = self.strip_html_tags(html)
            chunks: List[str] = self.chunk_text(clean_text)
            if not chunks:
                chunks = [""]

            urls.append(url)
            corpus_chunks.append(chunks)

        # Flatten for batch encoding
        flat_chunks: List[str] = [c for chunks in corpus_chunks for c in chunks]
        if flat_chunks:
            flat_embs: EmbeddingMatrix = self.model.encode( # type: ignore
                flat_chunks,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            ).astype(np.float32)  # (total_chunks, D)
        else:
            # Fallback dims; in practice you won't hit this if docs is non-empty
            flat_embs = np.zeros((0, 384), dtype=np.float32)

        # Aggregate back to doc-level embeddings
        embeddings_list: List[EmbeddingVector] = [] # type: ignore
        idx: int = 0
        for chunks in corpus_chunks:
            n: int = len(chunks)
            if n == 0:
                emb = np.zeros(flat_embs.shape[1], dtype=np.float32)
            else:
                doc_chunk_embs: EmbeddingMatrix = flat_embs[idx:idx + n] # type: ignore
                emb = doc_chunk_embs.mean(axis=0)
            embeddings_list.append(emb)
            idx += n

        embeddings_arr: EmbeddingMatrix = np.stack(embeddings_list, axis=0) # type: ignore
        return urls, embeddings_arr