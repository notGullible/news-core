"""Text embedding model manager.

Mirrors the pattern in myredis.py / mypostgres.py / myqdrant.py: a shared
model instance with init/close lifecycle and high-level embed / chunk /
batch operations for the NG pipeline.

Usage::

    emb = MyEmbeddings()
    if not await emb.init_db():
        log.critical("Embedding model failed to load")
    ...
    vec = emb.embed("Some article headline")
    vecs = emb.embed_batch(["text one", "text two"])
    long_vec = emb.embed_long_text("A very long document ...")
    ...
    await emb.close_db()
"""

from __future__ import annotations

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

# ── Embedding-dimension lookup ───────────────────────────────────────
# Known dimensions for popular sentence-transformers models.
# This lets :func:`get_embedding_dim` answer instantly without loading
# the full model.  When :class:`MyEmbeddings` loads a model, the cache
# is updated with the actual dimension.

_MODEL_DIMS: dict[str, int] = {
    "all-MiniLM-L6-v2": 384,
    "all-MiniLM-L12-v2": 384,
    "all-mpnet-base-v2": 768,
    "multi-qa-MiniLM-L6-cos-v1": 384,
    "multi-qa-mpnet-base-dot-v1": 768,
    "all-distilroberta-v1": 768,
    "paraphrase-MiniLM-L6-v2": 384,
    "paraphrase-multilingual-MiniLM-L12-v2": 384,
    "clip-ViT-B-32": 512,
}


def get_embedding_dim(model_name: str | None = None) -> int:
    """Return the embedding dimension for *model_name*.

    Looks up a built-in table of known sentence-transformers models
    first (instant, no I/O).  For unknown models returns 384 — a safe
    default for most BERT-derived sentence encoders — and logs a
    warning.  Pass ``vector_size`` explicitly to
    :meth:`MyQdrant.ensure_collection` if you need a different value.

    When :meth:`MyEmbeddings.init_db` successfully loads a model the
    cache is updated with the real dimension, so subsequent calls
    always return the exact value.
    """
    name = model_name or EMBEDDING_MODEL_NAME
    if name in _MODEL_DIMS:
        return _MODEL_DIMS[name]

    log.warning(
        "Unknown embedding dimension for model '%s' — "
        "returning default 384.  Pass vector_size explicitly "
        "to MyQdrant.ensure_collection() to override.",
        name,
    )
    return 384


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

    # ── model access ──────────────────────────────────────────

    def get_model(self) -> SentenceTransformer:
        """Return the loaded model.  Raises RuntimeError if not yet initialised."""
        if self._model is None:
            raise RuntimeError("Model not loaded — call init_db() first")
        return self._model

    # ── lifecycle ─────────────────────────────────────────────

    async def init_db(self) -> bool:
        """Load the embedding model into memory.

        The first call downloads the model from HuggingFace Hub
        (~90 MB for all-MiniLM-L6-v2) and may take a few seconds.

        Returns True on success, False on failure.
        """
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415

            log.info("Loading embedding model '%s' …", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            dim = self._model.get_sentence_embedding_dimension()
            _MODEL_DIMS[self._model_name] = dim  # update shared cache
            log.info(
                "Model loaded — max_seq_length=%s, dim=%s",
                self._model.max_seq_length,
                dim,
            )
            return True

        except Exception:
            log.exception("Could not load embedding model '%s'", self._model_name)
            return False

    async def close_db(self) -> None:
        """Release the model (no-op — kept for lifecycle symmetry)."""
        self._model = None
        log.info("Released embedding model")

    @property
    def vector_size(self) -> int:
        """Dimensionality of the embeddings produced by this model."""
        return self.get_model().get_sentence_embedding_dimension()

    @property
    def max_seq_length(self) -> int:
        """Maximum token length the model accepts per input."""
        return self.get_model().max_seq_length

    # ── embedding ──────────────────────────────────────────────

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
        max_tokens: int | None = None,
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
        if max_tokens is None:
            max_tokens = model.max_seq_length

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
        max_tokens: int | None = None,
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
        chunks = self.chunk_text(text, max_tokens=max_tokens, overlap=overlap)

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
