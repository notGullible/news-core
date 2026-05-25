"""Asynchronous Qdrant (vector database) connection manager.

Mirrors the pattern in myredis.py and mypostgres.py: a shared async client
with init/close lifecycle methods and high-level collection & vector
operations for the NG pipeline.

Usage::

    qdrant = MyQdrant()
    if not await qdrant.init_db():
        log.critical("Qdrant unreachable")
    ...
    await qdrant.upsert("articles", points=[...])
    results = await qdrant.search("articles", query_vector=[...], limit=10)
    ...
    await qdrant.close_db()

In DEBUG mode, the default collection is created automatically on init
if it does not already exist.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from common.config import (
    QDRANT_HOST,
    QDRANT_PORT,
    QDRANT_API_KEY,
    QDRANT_USE_TLS,
    QDRANT_DEFAULT_COLLECTION,
    EMBEDDING_DEFAULT_DISTANCE
    DEBUG,
)
from common.myembeddings import get_embedding_dim

if TYPE_CHECKING:
    from qdrant_client.http.models import (
        PointStruct,
        Record,
        ScoredPoint,
        VectorParams,
    )

log = logging.getLogger(__name__)


# ── Default collection parameters ────────────────────────────────────
# The vector size is resolved lazily via :func:`get_embedding_dim` so
# that the embedding model (which may not be loaded yet) determines the
# correct dimension.  The distance metric is fixed to Cosine — the
# standard for normalised sentence embeddings.

_DEFAULT_DISTANCE = EMBEDDING_DEFAULT_DISTANCE


class MyQdrant:
    """Thin wrapper around a Qdrant ``AsyncQdrantClient``.

    The client is created once in :meth:`__init__` and reused for the
    lifetime of the process.  Qdrant's async client uses a persistent
    HTTP connection pool internally — there is no separate pool object
    to manage.
    """

    def __init__(self) -> None:
        # Build the connection URL.  When TLS is off (the default for
        # local dev), qdrant-client expects a plain‑HTTP URL; when TLS
        # is on it switches to HTTPS.
        scheme = "https" if QDRANT_USE_TLS else "http"
        url = f"{scheme}://{QDRANT_HOST}:{QDRANT_PORT}"

        self._client = AsyncQdrantClient(
            url=url,
            api_key=QDRANT_API_KEY,
        )

    # ── client access ──────────────────────────────────────────

    def get_client(self) -> AsyncQdrantClient:
        """Return the shared async Qdrant client.

        Unlike Redis/PG where a fresh wrapper is returned per call,
        the Qdrant async client is designed to be reused directly.
        """
        return self._client

    # ── lifecycle ─────────────────────────────────────────────

    async def init_db(self) -> bool:
        """Verify Qdrant is reachable and optionally create the default collection.

        Returns True on success, False if the connection fails.
        """
        try:
            # Qdrant's async client lazily connects; forcing a
            # lightweight API call confirms reachability.
            await self._client.get_collections()

            if DEBUG:
                await self.ensure_collection(
                    collection_name=QDRANT_DEFAULT_COLLECTION,
                    vector_size=get_embedding_dim(),
                    distance=_DEFAULT_DISTANCE,
                )
                log.info(
                    "DEBUG mode: ensured collection '%s' exists",
                    QDRANT_DEFAULT_COLLECTION,
                )

            return True

        except Exception:
            log.exception("Could not connect to Qdrant")
            return False

    async def close_db(self) -> None:
        """Gracefully close the Qdrant client."""
        try:
            await self._client.close()
            log.info("Closed Qdrant client")
        except Exception:
            log.warning("Could not close Qdrant client")

    # ── collection management ──────────────────────────────────

    async def ensure_collection(
        self,
        collection_name: str,
        vector_size: int | None = None,
        distance: qmodels.Distance = _DEFAULT_DISTANCE,
    ) -> None:
        """Create *collection_name* if it does not already exist.

        Idempotent — if the collection already exists and its vector
        config matches, this is a no-op.

        Args:
            collection_name: Name of the collection to create/verify.
            vector_size: Dimensionality of vectors to be stored.
            distance: Distance metric (Cosine, Dot, Euclidean).
        """
        if vector_size is None:
            vector_size = get_embedding_dim()

        try:
            await self._client.get_collection(collection_name)
            # Collection exists — nothing to do.
            log.debug("Collection '%s' already exists", collection_name)
        except (UnexpectedResponse, ValueError):
            await self._client.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(
                    size=vector_size,
                    distance=distance,
                ),
            )
            log.info(
                "Created collection '%s' (size=%s, distance=%s)",
                collection_name,
                vector_size,
                distance,
            )

    async def collection_exists(self, collection_name: str) -> bool:
        """Return True if *collection_name* exists."""
        try:
            await self._client.get_collection(collection_name)
            return True
        except (UnexpectedResponse, ValueError):
            return False

    async def delete_collection(self, collection_name: str) -> None:
        """Delete *collection_name* and all its points.  Irreversible."""
        try:
            await self._client.delete_collection(collection_name)
            log.info("Deleted collection '%s'", collection_name)
        except (UnexpectedResponse, ValueError):
            log.warning("Collection '%s' not found — nothing to delete", collection_name)

    # ── point operations ───────────────────────────────────────

    async def upsert(
        self,
        collection_name: str,
        points: Sequence[PointStruct],
    ) -> None:
        """Insert or update *points* in *collection_name*.

        Each ``PointStruct`` carries an ``id``, ``vector``, and optional
        ``payload`` (arbitrary JSON-serialisable metadata).

        Example::

            from qdrant_client.models import PointStruct

            await qdrant.upsert("articles", [
                PointStruct(id=1, vector=[0.1, 0.2, ...], payload={"url": "..."}),
            ])
        """
        await self._client.upsert(
            collection_name=collection_name,
            points=points,
        )

    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int = 10,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[ScoredPoint]:
        """Return the *limit* most similar points to *query_vector*.

        Args:
            collection_name: Collection to search.
            query_vector: The embedding vector to compare against.
            limit: Maximum number of results to return.
            score_threshold: If set, only return results with score ≥ this
                value (useful for filtering out irrelevant matches).
            **kwargs: Forwarded to :meth:`AsyncQdrantClient.search`.

        Returns:
            List of ``ScoredPoint`` objects with ``id``, ``score``,
            ``payload``, and ``vector`` fields.
        """
        results = await self._client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
            score_threshold=score_threshold,
            **kwargs,
        )
        return results

    async def delete_points(
        self,
        collection_name: str,
        point_ids: Sequence[int | str],
    ) -> None:
        """Delete one or more points by their *point_ids*."""
        await self._client.delete(
            collection_name=collection_name,
            points_selector=qmodels.PointIdsList(
                points=point_ids,
            ),
        )

    async def get_points(
        self,
        collection_name: str,
        point_ids: Sequence[int | str],
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> list[Record]:
        """Retrieve points by *point_ids*.

        Args:
            collection_name: Collection to query.
            point_ids: IDs of the points to fetch.
            with_payload: Include the stored payload in the result.
            with_vectors: Include the stored vector in the result.

        Returns:
            List of ``Record`` objects.
        """
        records = await self._client.retrieve(
            collection_name=collection_name,
            ids=point_ids,
            with_payload=with_payload,
            with_vectors=with_vectors,
        )
        return records
