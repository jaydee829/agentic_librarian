"""KMS-backed BYOK credential crypto (monetization arc 3/3). `KMS_KEY_NAME` (the full
resource name, e.g. `projects/<p>/locations/us-central1/keyRings/librarian/cryptoKeys/
byok-credentials`) is read PER CALL — not cached — so an operator can point at a new key
without a redeploy. `_client()` is the lazy-import/cached-client seam
(enrichment/tasks.py's `_client()` pattern): `google.cloud.kms` is only imported where
BYOK crypto actually runs, and `set_kms_client` lets tests inject a fake without the
dependency installed.

Module is `db_manager`-free by design (spec §1): callers own their own session, keeping
this module pure of pool concerns. Plaintext keys are NEVER logged, never persisted, and
live only in call-scope local variables — every caller must treat the `str` this module
hands back the same way.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ByokNotConfigured(Exception):  # noqa: N818 - name is a binding contract (spec + task brief)
    """`KMS_KEY_NAME` is unset — BYOK crypto is unavailable. The API layer maps this to
    a 503 `byok_unavailable` response (GET still answers; only PUT needs KMS)."""


class ByokKeyError(Exception):
    """A stored credential could not be decrypted (KMS outage, corrupted ciphertext, or
    a revoked key). Callers must surface this — never silently fall back to the app key
    (spec §"No silent fallback")."""


_client_cached = None
_client_lock = threading.Lock()


def _client():
    """Lazy, thread-safe, cached KMS client — mirrors enrichment/tasks.py's `_client()`
    so `google-cloud-kms` is only imported where BYOK crypto runs."""
    global _client_cached
    if _client_cached is None:
        with _client_lock:
            if _client_cached is None:
                from google.cloud import kms

                _client_cached = kms.KeyManagementServiceClient()
    return _client_cached


def set_kms_client(client) -> None:
    """Test seam: inject a fake KMS client, bypassing the lazy `google.cloud.kms`
    import entirely. Pass `None` to reset to the lazy-real-client state."""
    global _client_cached
    _client_cached = client


def _key_name() -> str:
    key_name = os.environ.get("KMS_KEY_NAME", "").strip()
    if not key_name:
        raise ByokNotConfigured("KMS_KEY_NAME is not set")
    return key_name


def encrypt_key(plaintext: str) -> bytes:
    """Encrypt a plaintext Gemini API key for storage. Raises ByokNotConfigured when
    `KMS_KEY_NAME` is unset."""
    key_name = _key_name()
    response = _client().encrypt(request={"name": key_name, "plaintext": plaintext.encode("utf-8")})
    return response.ciphertext


def decrypt_key(ciphertext: bytes) -> str:
    """Decrypt stored ciphertext back to the plaintext key. Raises ByokNotConfigured
    when `KMS_KEY_NAME` is unset; raises ByokKeyError on any KMS-side decrypt failure
    (outage, corrupted ciphertext, revoked key) so callers never fall back silently."""
    key_name = _key_name()
    try:
        response = _client().decrypt(request={"name": key_name, "ciphertext": ciphertext})
    except Exception as e:
        raise ByokKeyError("failed to decrypt stored BYOK credential") from e
    return response.plaintext.decode("utf-8")


def resolve_gemini_key(session: Session, user_id: UUID) -> str | None:
    """Load the caller's `UserCredential(vendor='gemini')` row and decrypt it. Returns
    None when no row exists (not-byok is the common case, not an error). Decrypt
    failure propagates as ByokKeyError — callers surface it, they never fall back to
    the app key."""
    from agentic_librarian.db.models import UserCredential

    row = session.get(UserCredential, (user_id, "gemini"))
    if row is None:
        return None
    return decrypt_key(row.encrypted_key)
