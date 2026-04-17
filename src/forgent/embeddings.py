"""Optional embedding layer for hybrid retrieval.

forgent's default recall is BM25 (via SQLite FTS5) -- zero external deps, fast,
great for keyword overlap. But 'I did something similar six weeks ago' is a
semantic query that BM25 misses. This module layers embeddings on top: when
`FORGENT_EMBED_MODEL` is set, we embed new memories on write, store the vector
as a BLOB, and recall ranks by reciprocal-rank fusion of BM25 + cosine sim.

Supported embedding providers (picked by env var):
    voyage-3-lite       Anthropic-affiliated; fast + good for code
    voyage-3            higher quality, slower
    <any>               passes through to `anthropic.embeddings.create` if present

By default no embeddings are computed -- existing DBs and deployments without
the env var work unchanged. This is opt-in; the main value prop is that users
who care about semantic recall can turn it on with one env var, no schema
change or reindex needed.
"""

from __future__ import annotations

import os
import struct
from typing import Any

# Embedding vectors are serialized as little-endian float32 arrays in the
# BLOB column. Plain enough that a pandas user could pull them out by hand.
_FLOAT_STRUCT = struct.Struct("<f")


def pack_vector(vec: list[float]) -> bytes:
    """Serialize a float vector for storage."""
    if not vec:
        return b""
    out = bytearray()
    for v in vec:
        out.extend(_FLOAT_STRUCT.pack(float(v)))
    return bytes(out)


def unpack_vector(blob: bytes) -> list[float]:
    """Deserialize a stored vector."""
    if not blob:
        return []
    n = len(blob) // 4
    return list(_FLOAT_STRUCT.iter_unpack(blob[: n * 4]))  # type: ignore[arg-type]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Dot-product over magnitudes. 1 = identical, 0 = orthogonal, <0 = opposite."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def embed(text: str, model: str | None = None) -> list[float]:
    """Embed `text` using the configured provider.

    Returns [] when embeddings are disabled or the provider call fails.
    Callers should treat [] as 'fall back to BM25'.
    """
    if not text:
        return []
    model = model or os.environ.get("FORGENT_EMBED_MODEL")
    if not model:
        return []
    # We try voyage first (best price/quality for code), then Anthropic's own
    # embeddings endpoint if the SDK exposes one. Failure is silent --
    # retrieval falls back to BM25.
    try:
        if model.startswith("voyage"):
            return _embed_voyage(text, model)
    except Exception:
        pass
    try:
        return _embed_anthropic(text, model)
    except Exception:
        return []


def _embed_voyage(text: str, model: str) -> list[float]:
    api_key = os.environ.get("VOYAGE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []
    try:
        import voyageai  # type: ignore
    except ImportError:
        return []
    client = voyageai.Client(api_key=api_key)
    result = client.embed([text], model=model)
    vectors = getattr(result, "embeddings", None)
    if not vectors:
        return []
    return list(vectors[0]) if vectors[0] else []


def _embed_anthropic(text: str, model: str) -> list[float]:
    """Some SDK versions expose an embeddings resource. Probe defensively."""
    try:
        import anthropic  # noqa: WPS433
    except ImportError:
        return []
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []
    client = anthropic.Anthropic(api_key=api_key)
    emb_res = getattr(client, "embeddings", None)
    if emb_res is None:
        return []
    try:
        resp = emb_res.create(model=model, input=text)  # type: ignore[attr-defined]
    except Exception:
        return []
    data = getattr(resp, "data", None)
    if not data:
        return []
    first = data[0]
    vec = getattr(first, "embedding", None)
    return list(vec) if vec else []


def embeddings_enabled() -> bool:
    """True when the env var opts in and a provider is usable."""
    return bool(os.environ.get("FORGENT_EMBED_MODEL"))
