"""Text embedding model manager."""


import logging
from typing import TYPE_CHECKING, Sequence

from common.config import EMBEDDING_MODEL_NAME

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

# ── Chunking defaults ────────────────────────────────────────────────
# When a document exceeds the model's max_seq_length we split it into
# overlapping chunks, embed each independently, then mean-pool.

_DEFAULT_CHUNK_OVERLAP: int = 50   # tokens of overlap between consecutive chunks

class MyEmbeddings:
    """Thin wrapper around a ``SentenceTransformer`` model.

    The model is loaded once in :meth:`init_db` and reused for the
    lifetime of the process.  Embedding calls are CPU/GPU-bound and
    synchronous; wrap them in :func:`asyncio.to_thread` if needed
    in an async context.
    """

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._model_name: str = EMBEDDING_MODEL_NAME
        self._embed_dim: int
        self._max_seq_len:int

        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415

            log.info("Loading embedding model '%s' …", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            embed_dim = self.get_model().get_embedding_dimension()
            assert embed_dim is not None, log.exception("Unknown Embedding Dimension for model: '%s'", self._model_name)
            self._embed_dim = embed_dim

            max_seq_len = self.get_model().max_seq_length
            assert max_seq_len is not None, log.exception("Unknown Max Sequence Length for model: '%s'", self._model_name)
            self._max_seq_len = max_seq_len
            
            log.info(
                "Model loaded — max_seq_length=%s, dim=%s",
                self._model.max_seq_length,
                self._embed_dim,
            )
            
        except Exception:
            log.exception("Could not load embedding model '%s'", self._model_name)
            
    

    def get_model(self) -> SentenceTransformer:
        """Return the loaded model.  Raises RuntimeError if not yet initialised."""
        if self._model is None:
            raise RuntimeError("Model not loaded — call init_db() first")
        return self._model


    def release_embedding_model(self) -> None:
        """Release the model (However Keeps all the metadata like embed dim and sort)."""
        self._model = None
        log.info("Released embedding model")


    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single *text* string.

        The text is automatically truncated to ``max_seq_length`` tokens
        by the underlying model.  Use :meth:`embed_long_text` for
        documents that exceed that limit.
        """
        model = self.get_model()
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()  # type: ignore[no-any-return]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embedding vectors for a batch of *texts*.

        Much faster than calling :meth:`embed` in a loop — the model
        can vectorise over the batch dimension.
        """
        model = self.get_model()
        vecs = model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vecs.tolist()  # type: ignore[no-any-return]

    # ── chunking (for long documents) ──────────────────────────

    def chunk_text(
        self,
        text: str,
        overlap: int = _DEFAULT_CHUNK_OVERLAP,
    ) -> list[str]:
        """Split *text* into token-aware chunks that fit the model.

        Uses the model's own tokeniser so that token counts are accurate
        (word- or character-based splitting often over- or under-shoots
        the model's actual limit).

        Args:
            text: The input text to split.
            max_tokens: Maximum tokens per chunk.  Defaults to
                ``self.max_seq_length``.
            overlap: Number of tokens to overlap between consecutive
                chunks (preserves context across chunk boundaries).

        Returns:
            List of text chunks, each ≤ *max_tokens* tokens.
        """
        model = self.get_model()
        max_tokens = self._max_seq_len

        tokenizer = model.tokenizer
        tokens = tokenizer.encode(text, add_special_tokens=False)

        if len(tokens) <= max_tokens:
            return [text]

        stride = max(max_tokens - overlap, 1)
        chunks: list[str] = []

        for start in range(0, len(tokens), stride):
            chunk_tokens = tokens[start : start + max_tokens]
            chunk_text = tokenizer.decode(chunk_tokens, skip_special_tokens=True)
            chunk_text = chunk_text.strip()
            if chunk_text:
                chunks.append(chunk_text)
            if start + max_tokens >= len(tokens):
                break

        return chunks

    def embed_long_text(
        self,
        text: str,
        overlap: int = _DEFAULT_CHUNK_OVERLAP,
    ) -> list[float]:
        """Embed a document longer than ``max_seq_length``.

        Strategy:
        1. Split the text into token-aware chunks with :meth:`chunk_text`.
        2. Embed each chunk independently.
        3. Mean-pool the chunk embeddings into a single vector.

        Args:
            text: The long document to embed.
            max_tokens: Max tokens per chunk (default: model's limit).
            overlap: Token overlap between consecutive chunks.

        Returns:
            A single embedding vector representing the full document.
        """
        chunks = self.chunk_text(text, overlap=overlap)

        if len(chunks) == 1:
            return self.embed(chunks[0])

        vecs = self.embed_batch(chunks)
        dim = len(vecs[0])
        pooled = [0.0] * dim
        for vec in vecs:
            for i in range(dim):
                pooled[i] += vec[i]
        n = len(vecs)
        for i in range(dim):
            pooled[i] /= n

        # Re-normalise after mean-pooling.
        norm = sum(v * v for v in pooled) ** 0.5
        if norm > 0:
            pooled = [v / norm for v in pooled]

        return pooled
