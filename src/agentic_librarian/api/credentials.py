"""BYOK credential endpoints (monetization arc 3/3) — mirrors api/libraries.py's
router/db/auth shape. The table stores KMS ciphertext ONLY: GET never returns the key,
never a fragment of it, and PUT's request body is never logged. A saved/removed
credential is the ONLY writer of `user_credentials`, so it is also the thing that flips
a user's tier free<->byok (core/tiers.effective_tier reads row existence, not a flag
here — single source of truth)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.core import byok
from agentic_librarian.db.models import UserCredential
from agentic_librarian.db.session import DatabaseManager

router = APIRouter()
db_manager = DatabaseManager()

_MAX_KEY_LENGTH = 200


def set_db_manager(new_manager: DatabaseManager) -> None:
    global db_manager
    db_manager = new_manager


def _validate_key(api_key: str) -> bool:
    """Live probe seam: one free/fast `count_tokens` call proves the key actually
    authenticates against Gemini before we store it. A fresh `genai.Client` per call
    (never the shared app-key client). ANY exception (auth failure, malformed key,
    network hiccup) maps to False — tests patch THIS function directly rather than
    mocking genai.Client."""
    from google import genai

    try:
        client = genai.Client(api_key=api_key)
        client.models.count_tokens(model="gemini-3.1-flash-lite", contents="ping")
        return True
    except Exception:  # noqa: BLE001 - any failure means "not a usable key"
        return False


class CredentialIn(BaseModel):
    api_key: str


def _invalid_key(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": "invalid_api_key", "message": message})


@router.get("/me/credentials")
def get_credentials(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        row = session.get(UserCredential, (user.id, "gemini"))
        if row is None:
            return {"configured": False, "updated_at": None}
        return {"configured": True, "updated_at": row.updated_at.isoformat()}


@router.put("/me/credentials")
def put_credentials(
    body: CredentialIn = Body(...),  # noqa: B008
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    api_key = body.api_key.strip()
    if not api_key or len(api_key) > _MAX_KEY_LENGTH:
        raise _invalid_key("API key must be non-empty and no more than 200 characters.")

    if not _validate_key(api_key):
        raise _invalid_key("That key did not authenticate with Gemini — check it and try again.")

    try:
        ciphertext = byok.encrypt_key(api_key)
    except byok.ByokNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "byok_unavailable", "message": "BYOK is not available right now — try again later."},
        ) from e

    kms_key_name = os.environ.get("KMS_KEY_NAME", "").strip()
    with db_manager.get_session() as session:
        row = session.get(UserCredential, (user.id, "gemini"))
        if row is None:
            session.add(
                UserCredential(user_id=user.id, vendor="gemini", encrypted_key=ciphertext, kms_key_name=kms_key_name)
            )
        else:
            row.encrypted_key = ciphertext
            row.kms_key_name = kms_key_name
        session.flush()
    return {"configured": True}


@router.delete("/me/credentials")
def delete_credentials(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        row = session.get(UserCredential, (user.id, "gemini"))
        if row is not None:
            session.delete(row)
            session.flush()
    return {"configured": False}
