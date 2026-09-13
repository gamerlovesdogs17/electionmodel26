"""API authentication + CORS hardening for production."""

from __future__ import annotations

import os
import secrets
from typing import Callable

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def api_key_configured() -> bool:
    return bool(os.environ.get("MIDTERMS_API_KEY", "").strip())


def require_api_key(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """
    When MIDTERMS_API_KEY is set, require Authorization: Bearer <key>.
    When unset (local research), endpoints stay open.
    """
    expected = os.environ.get("MIDTERMS_API_KEY", "").strip()
    if not expected:
        return
    if creds is None or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


def cors_origins() -> list[str]:
    raw = os.environ.get("MIDTERMS_CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    # Local research default
    return ["*"]
