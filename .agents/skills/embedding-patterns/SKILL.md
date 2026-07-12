---
name: embedding-patterns
description: "INTERNAL — invoke by explicit name only via `Skill embedding-patterns`. Do NOT auto-load. Embedding generation via Azure-routed OpenAI, batch sizing, DuckDB HNSW storage, similarity query patterns."
---

# Embedding Patterns (canonical for `ingestion/src/`)

## Auth (DEC-005)

Azure-routed embeddings. Same `ANTHROPIC_API_KEY` (Azure resource key) used for both Anthropic and OpenAI endpoints. Embeddings endpoint: `{AI_API_BASE_URL_BASE}/openai/deployments/{EMBEDDING_MODEL}/embeddings` where the base is derived from `AI_API_BASE_URL` by stripping `/anthropic/v1/messages`.

Use `httpx` async, not the OpenAI SDK.

## Embedding call pattern

```python
import httpx
import os

async def embed_texts(texts: list[str]) -> list[list[float]]:
    base = os.environ["AI_API_BASE_URL"].replace("/anthropic/v1/messages", "")
    model = os.environ["EMBEDDING_MODEL"]
    url = f"{base}/openai/deployments/{model}/embeddings"
    headers = {"api-key": os.environ["ANTHROPIC_API_KEY"]}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={"input": texts}, headers=headers)
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]
```

## Batch sizing

- Max 512 texts per call (Azure deployment limit; verify against your deployment config).
- Default batch: 100. Adjust based on token count, not just text count.
- Use `asyncio.gather` with semaphore for concurrent batches:

```python
sem = asyncio.Semaphore(4)
async def bounded_embed(batch: list[str]) -> list[list[float]]:
    async with sem:
        return await embed_texts(batch)
```

## DuckDB storage

Store in a `FLOAT[N]` column (fixed-dimension array). Create HNSW index after bulk load:

```sql
LOAD vss;
ALTER TABLE docs ADD COLUMN embedding FLOAT[1536];
CREATE INDEX idx_embedding ON docs USING HNSW (embedding) WITH (metric='cosine');
```

## Similarity query

```sql
SELECT id, content, array_cosine_similarity(embedding, $1::FLOAT[1536]) AS score
FROM docs
ORDER BY score DESC
LIMIT 10
```

Pass the query vector as a parameterized `FLOAT[N]` array.

## Forbidden

- Synchronous embedding calls in Dramatiq workers — use `async` + `asyncio.run()` or an async-aware actor pattern.
- Storing embeddings as `VARCHAR` (JSON) — use `FLOAT[N]` for HNSW indexing.
- Different embedding dimensions in the same column — fix the model version, fix the dimension.
