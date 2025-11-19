class HtmlMatcher:
    """
    Matching / retrieval on top of precomputed embeddings.

    Usage:
      - Construct with urls + embeddings (from HtmlEmbedder.embed_corpus)
      - Call match_html / match_many, passing an HtmlEmbedder to embed queries.
    """

    def __init__(self, urls: List[str], embeddings: EmbeddingMatrix) -> None:
        """
        urls: list of document URLs (length N)
        embeddings: np.ndarray of shape (N, D)
        """
        if len(urls) != embeddings.shape[0]:
            raise ValueError("urls and embeddings size mismatch")
        self.urls: List[str] = urls
        self.embeddings: EmbeddingMatrix = embeddings.astype(np.float32)

    @staticmethod
    def _cosine_sim_matrix(
        A: EmbeddingMatrix, B: EmbeddingMatrix, eps: float = 1e-12
    ) -> EmbeddingMatrix:
        """
        Compute cosine similarity matrix between rows of A and rows of B.
        A: (M, D)
        B: (N, D)
        Returns: (M, N)
        """
        dot: EmbeddingMatrix = A @ B.T  # (M, N)
        normA: EmbeddingMatrix = np.linalg.norm(A, axis=1, keepdims=True)  # (M, 1)
        normB: EmbeddingMatrix = np.linalg.norm(B, axis=1, keepdims=True)  # (N, 1)
        sims: EmbeddingMatrix = dot / ((normA * normB.T) + eps)
        return sims

    # ---- Matching APIs ----

    def match_html(
        self,
        html: str,
        embedder: HtmlEmbedder,
    ) -> Optional[Tuple[str, float]]:
        """
        Embed a single HTML string with the given embedder and
        return (best_matching_url, confidence_score).

        confidence_score is cosine similarity in [-1, 1] (~0..1 in practice).
        """
        if len(self.urls) == 0:
            return None

        q_emb: EmbeddingMatrix = embedder.embed_html(html).reshape(1, -1)  # (1, D)
        sims: EmbeddingMatrix = self._cosine_sim_matrix(q_emb, self.embeddings)  # (1, N)
        sims_row: EmbeddingVector = sims[0]
        best_idx: int = int(np.argmax(sims_row))
        best_sim: float = float(sims_row[best_idx])
        best_url: str = self.urls[best_idx]
        return best_url, best_sim

    def match_many(
        self,
        new_docs: List[Dict[str, str]],
        embedder: HtmlEmbedder,
        batch_size: int = 32,
    ) -> List[Dict[str, Any]]:
        """
        Given many new docs: [{"html": "...", "url": "source_url"}, ...]
        Embed them using the embedder, then match to the indexed corpus.

        Returns list of:
          {
            "source_url": <new doc url>,
            "matched_url": <closest stored url>,
            "score": <cosine similarity>
          }
        """
        if len(self.urls) == 0:
            return []

        results: List[Dict[str, Any]] = []

        for i in range(0, len(new_docs), batch_size):
            batch: List[Dict[str, str]] = new_docs[i:i + batch_size]

            # Embed batch
            batch_embs_list: List[EmbeddingVector] = []
            for d in batch:
                emb: EmbeddingVector = embedder.embed_html(d["html"])
                batch_embs_list.append(emb)
            batch_embs: EmbeddingMatrix = np.stack(batch_embs_list, axis=0)  # (B, D)

            # Compute cosine similarities to all indexed docs
            sims: EmbeddingMatrix = self._cosine_sim_matrix(batch_embs, self.embeddings)  # (B, N)

            for j, doc in enumerate(batch):
                row: EmbeddingVector = sims[j]
                best_idx: int = int(np.argmax(row))
                best_sim: float = float(row[best_idx])

                results.append(
                    {
                        "source_url": doc["url"],            # URL of the new (query) HTML
                        "matched_url": self.urls[best_idx],  # Best matching stored URL
                        "score": best_sim,                   # Confidence
                    }
                )

        return results