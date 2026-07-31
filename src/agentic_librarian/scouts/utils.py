import os
import threading
import time
from functools import lru_cache

from google import genai
from google.genai import types

from agentic_librarian.core.usage import record_llm_call

# Current GA Gemini embedding model — the single source of truth for the model name (managers
# and the #123 warm-before-session callers all reference this instead of copying the string).
EMBED_MODEL = "gemini-embedding-001"

# Match the pgvector column dimension (Vector(1536) on Style/Trope). gemini-embedding-001
# defaults to 3072, so we must request 1536 explicitly.
EMBEDDING_DIMENSIONS = 1536

# Optional client-side pacing for the embedding endpoint, controlled by EMBED_MIN_INTERVAL (seconds
# between cache-MISS embed calls; 0/unset = no throttle). The interactive recommendation path leaves
# it unset, so live lookups pay zero added latency; the Flow-1 ETL sets a small value so a chunk's
# embedding burst during persist stays under the Gemini embedding RPM instead of 429-ing mid-run
# (the SDK retry in llm_retry.py can't outlast a sustained over-rate burst). lru_cache hits skip this.
_EMBED_MIN_INTERVAL = float(os.environ.get("EMBED_MIN_INTERVAL", "0") or "0")
_embed_lock = threading.Lock()
_last_embed = 0.0


def _throttle_embedding() -> None:
    if _EMBED_MIN_INTERVAL <= 0:
        return
    global _last_embed
    # Reserve the next wake-up slot atomically, then sleep OUTSIDE the lock so
    # concurrent callers can schedule themselves immediately instead of
    # serializing on the full sleep of whoever acquired the lock first.
    now = time.monotonic()
    with _embed_lock:
        scheduled = max(now, _last_embed + _EMBED_MIN_INTERVAL)
        _last_embed = scheduled
    wait = scheduled - now
    if wait > 0:
        time.sleep(wait)


# Process-wide genai client (GH #101). One client per process means the lru_cache below
# keys purely on (model_name, text) and actually hits across manager instances / tool
# calls — previously each TropeManager/StyleManager built its own client and the client
# identity in the cache key defeated the cache. Double-checked lock: build at most one
# client under concurrency (same pattern api/analysis_style.py pioneered).
_shared_client: genai.Client | None = None
_client_lock = threading.Lock()


def get_shared_genai_client() -> genai.Client:
    global _shared_client
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                from agentic_librarian.llm_retry import genai_http_options

                key = os.environ.get("GOOGLE_SEARCH_API_KEY")
                if not key:
                    raise ValueError("GOOGLE_SEARCH_API_KEY is not set — cannot build the shared genai client.")
                _shared_client = genai.Client(api_key=key, http_options=genai_http_options())
    return _shared_client


# 1024 × ~12KB/vector ≈ 13MB — sized so a bulk import's trope churn doesn't evict the analysis anchors.
@lru_cache(maxsize=1024)
def get_cached_embedding(model_name: str, text: str) -> list[float]:
    """Shared embed chokepoint for ETL ingestion, MCP tools, and the recommendation flow.
    Cached on (model_name, text) so identical tags embed over the network once per process
    (GH #101). task_type SEMANTIC_SIMILARITY keeps stored vectors and query vectors in one
    representation space; changing task_type invalidates previously-stored vectors."""
    _throttle_embedding()
    client = get_shared_genai_client()
    response = client.models.embed_content(
        model=model_name,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="SEMANTIC_SIMILARITY",
            output_dimensionality=EMBEDDING_DIMENSIONS,
        ),
    )
    if not response or not response.embeddings:
        raise ValueError(f"Embedding generation returned no result for text: {text!r}")
    # #100: embed responses expose no token counts and lru_cache means only MISSES reach
    # here — record per network call with a documented chars//4 estimate (visibility, not
    # billing-grade; embeddings are $0.15/M).
    record_llm_call("gemini", model_name, len(text) // 4, 0)
    return response.embeddings[0].values
