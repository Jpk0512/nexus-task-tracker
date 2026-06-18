from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger("prism")

VECTOR_SIZE = 256


def deterministic_vector(text: str, size: int = VECTOR_SIZE) -> list[float]:
    """Stable, dependency-free hash -> fixed-dim unit vector.

    v1 does not do semantic similarity; this vector exists only to satisfy the
    Qdrant upsert contract. Same text always maps to the same vector, with no
    model and no network.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = (digest * ((size // len(digest)) + 1))[:size]
    vec = [(b / 255.0) - 0.5 for b in raw]
    magnitude = sum(v * v for v in vec) ** 0.5
    if magnitude == 0.0:
        return [0.0] * size
    return [v / magnitude for v in vec]


class BugGenome:
    COLLECTIONS: dict[str, int] = {
        "bug_patterns": VECTOR_SIZE,
        "risk_scores": VECTOR_SIZE,
    }

    def __init__(self, path: str = ".prism/genome", client: QdrantClient | None = None) -> None:
        if client is not None:
            self.client = client
        else:
            self.client = QdrantClient(path=path)
        self._ensure_collections()

    def _ensure_collections(self) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        for name, size in self.COLLECTIONS.items():
            if name not in existing:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=size, distance=Distance.COSINE),
                )

    async def record_finding(
        self,
        technique: str,
        description: str,
        file: str,
        line: int,
        severity: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "technique": technique,
            "description": description,
            "file": file,
            "line": line,
            "severity": severity,
            "ts": time.time(),
        }
        if extra:
            payload.update(extra)
        self.client.upsert(
            collection_name="bug_patterns",
            points=[
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=deterministic_vector(description),
                    payload=payload,
                )
            ],
        )
        self.client.upsert(
            collection_name="risk_scores",
            points=[
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=deterministic_vector(f"{file}:{line}"),
                    payload={"file": file, "line": line, "severity": severity, "ts": time.time()},
                )
            ],
        )

    async def get_highest_risk(self, limit: int = 10) -> list[dict[str, Any]]:
        results, _ = self.client.scroll(
            collection_name="risk_scores",
            limit=1000,
            with_payload=True,
        )
        payloads = [r.payload for r in results if r.payload is not None]
        payloads.sort(key=lambda p: p.get("severity", 0), reverse=True)
        return payloads[:limit]
