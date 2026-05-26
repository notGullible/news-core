"""
Article extraction → embedding → Qdrant storage pipeline.

Called by each extractor worker for every article dequeued from
the ``extractor`` Redis stream.

Parent–child embedding model (see ``README.md``):
1. The **full article** is embedded as a *parent* point (summary-level).
2. The article is split on ``<section>…</section>`` tags; each section
   is embedded as a *child* point that points back to the parent.
3. Everything is upserted into Qdrant in a single batch.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING

from common.config import QDRANT_DEFAULT_COLLECTION

if TYPE_CHECKING:
    from common.myembeddings import MyEmbeddings
    from common.myqdrant import MyQdrant
    from qdrant_client.models import PointStruct

log = logging.getLogger(__name__)

# ── Section-splitting regex ──────────────────────────────────────────
# Matches the *contents* between a ``<section>`` and ``</section>``
# tag pair.  The DOTALL flag is used so that multi-line sections
# (which are common in article HTML) are captured as one chunk.
_SECTION_RE = re.compile(r"<section>(.*?)</section>", re.DOTALL)


def split_article(content: str) -> list[str]:
    """Split *content* on ``<section>…</section>`` tag pairs.

    Returns a list of section-body strings (tags stripped, whitespace
    trimmed).  Sections that are empty after stripping are discarded.

    If no ``<section>`` tags are found, the entire *content* is
    returned as a single-element list so that the caller always
    has at least one child chunk.
    """
    sections = [
        match.group(1).strip()
        for match in _SECTION_RE.finditer(content)
    ]
    sections = [s for s in sections if s]  # discard empty

    if not sections:
        # No section tags — treat the whole article as one chunk.
        stripped = content.strip()
        return [stripped] if stripped else []

    return sections


async def process(
    *,
    article_id: int,
    url: str,
    source_domain: str,
    headline: str | None,
    content: str,
    myembeddings: MyEmbeddings,
    myqdrant: MyQdrant,
    collection_name: str = QDRANT_DEFAULT_COLLECTION,
) -> tuple[str, list[str]]:
    """Embed a single article and its sections, then store in Qdrant.

    1. Split *content* into sections via :func:`split_article`.
    2. Embed the **full article** (headline + content) as the *parent*.
    3. Embed **each section** as a *child* pointing back to the parent.
    4. Upsert parent + children into *collection_name* in one batch.

    Args:
        article_id: The ``articles.id`` primary key from PostgreSQL.
        url: Canonical article URL.
        source_domain: e.g. ``"reuters.com"``.
        headline: Article headline (may be ``None``).
        content: Full article body text (including ``<section>`` tags).
        myembeddings: Initialised embedding model wrapper.
        myqdrant: Initialised Qdrant client wrapper.
        collection_name: Qdrant collection to upsert into.

    Returns:
        A ``(parent_point_id, [child_point_id, …])`` tuple.
        Point IDs are stable UUID strings derived from the article
        identity, making upserts idempotent.
    """
    # ── 1. Split into sections ──────────────────────────────────
    sections = split_article(content)

    # ── 2. Build the parent embedding ───────────────────────────

    # Full-article text used for the parent embedding: headline
    # (if present) followed by the raw content.  ``embed_long_text``
    # internally handles token-aware chunking + mean-pooling.
    full_text = f"{headline}\n\n{content}" if headline else content

    log.debug(
        "Embedding parent for article %s (%d chars → sections=%d)",
        article_id, len(full_text), len(sections),
    )

    parent_vector = myembeddings.embed_long_text(full_text)  # type: ignore[attr-defined]

    parent_id = _point_id(article_id, "parent")

    from qdrant_client.models import PointStruct  # noqa: PLC0415

    points: list[PointStruct] = [
        PointStruct(
            id=parent_id,
            vector=parent_vector,
            payload={
                "article_id": article_id,
                "url": url,
                "source_domain": source_domain,
                "headline": headline,
                "chunk_type": "parent",
                "content_preview": full_text[:500],
            },
        )
    ]

    # ── 3. Embed each section (children) ────────────────────────
    child_ids: list[str] = []

    if sections:
        # Batch-embed all sections for throughput.
        log.debug("Embedding %d child section(s) for article %s", len(sections), article_id)
        child_vectors = myembeddings.embed_batch(sections)  # type: ignore[attr-defined]

        for i, (section_text, vec) in enumerate(zip(sections, child_vectors)):
            child_id = _point_id(article_id, f"child-{i}")
            child_ids.append(child_id)
            points.append(
                PointStruct(
                    id=child_id,
                    vector=vec,
                    payload={
                        "article_id": article_id,
                        "url": url,
                        "source_domain": source_domain,
                        "headline": headline,
                        "chunk_type": "child",
                        "chunk_index": i,
                        "parent_id": parent_id,
                        "section_text": section_text[:1000],
                    },
                )
            )
    else:
        log.info("No sections found for article %s — only parent stored", article_id)

    # ── 4. Upsert into Qdrant ───────────────────────────────────
    await myqdrant.upsert(collection_name, points)

    log.info(
        "Stored article %s in Qdrant: parent=%s  children=%d",
        article_id, parent_id, len(child_ids),
    )

    return parent_id, child_ids


# ── internal helpers ──────────────────────────────────────────────────


def _point_id(article_id: int, suffix: str) -> str:
    """Generate a stable, deterministic Qdrant point ID.

    Uses a UUIDv5 seeded with the article identity so that re-runs
    produce the same point IDs (upsert-safe).  The *suffix*
    distinguishes parent from individual children.
    """
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS namespace
    return str(uuid.uuid5(namespace, f"ng:article:{article_id}:{suffix}"))
