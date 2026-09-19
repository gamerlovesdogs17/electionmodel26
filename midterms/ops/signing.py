"""Ed25519 release signing with a public trust root (blueprint §11.3).

Private key: MIDTERMS_SIGNING_PRIVATE_KEY (PEM) or MIDTERMS_SIGNING_KEY_PATH.
Public key:  data/manifests/signing_public_key.pem (committed) or MIDTERMS_SIGNING_PUBLIC_KEY.

When MIDTERMS_REQUIRE_SIGNING=1, refuse to sign with a missing/dev key.
HMAC remains available only as a legacy fallback when Ed25519 is unavailable
and REQUIRE_SIGNING is not set.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from midterms.config import MANIFESTS_DIR, ROOT

PUBLIC_KEY_PATH = MANIFESTS_DIR / "signing_public_key.pem"
PRIVATE_KEY_DEFAULT_PATH = ROOT / "data" / "licensed" / "signing_private_key.pem"


def _require_signing() -> bool:
    return os.environ.get("MIDTERMS_REQUIRE_SIGNING", "").strip() in {"1", "true", "yes"}


def _load_private_pem() -> bytes | None:
    env_pem = os.environ.get("MIDTERMS_SIGNING_PRIVATE_KEY")
    if env_pem:
        return env_pem.replace("\\n", "\n").encode()
    path = os.environ.get("MIDTERMS_SIGNING_KEY_PATH")
    p = Path(path) if path else PRIVATE_KEY_DEFAULT_PATH
    if p.exists():
        return p.read_bytes()
    return None


def _load_public_pem() -> bytes | None:
    env_pem = os.environ.get("MIDTERMS_SIGNING_PUBLIC_KEY")
    if env_pem:
        return env_pem.replace("\\n", "\n").encode()
    if PUBLIC_KEY_PATH.exists():
        return PUBLIC_KEY_PATH.read_bytes()
    return None


def generate_keypair(*, write_private: bool = False) -> dict[str, str]:
    """Generate Ed25519 keypair; write public key to manifests.

    Private key is returned in the response / written only when ``write_private``
    is explicitly True (local ops). Never ship private PEM in handoff archives.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    priv_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_KEY_PATH.write_bytes(pub_pem)
    priv_path = ""
    if write_private:
        PRIVATE_KEY_DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PRIVATE_KEY_DEFAULT_PATH.write_bytes(priv_pem)
        priv_path = str(PRIVATE_KEY_DEFAULT_PATH)
    return {
        "public_key_path": str(PUBLIC_KEY_PATH),
        "private_key_path": priv_path,
        "note": (
            "Commit public key only. Prefer MIDTERMS_SIGNING_PRIVATE_KEY env; "
            "never include data/licensed/*.pem in handoff ZIPs. Rotate if exposed."
        ),
    }


def _payload_bytes(payload: dict[str, Any] | str) -> bytes:
    if isinstance(payload, dict):
        return json.dumps(payload, sort_keys=True, default=str).encode()
    return str(payload).encode()


def sign_payload(payload: dict[str, Any] | str) -> dict[str, str]:
    """Sign with Ed25519 when a private key is configured; else HMAC legacy (unless required)."""
    text = _payload_bytes(payload)
    digest = hashlib.sha256(text).hexdigest()
    priv = _load_private_pem()
    if priv:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = serialization.load_pem_private_key(priv, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError("Signing key must be Ed25519")
        sig = key.sign(text)
        return {
            "alg": "Ed25519",
            "sha256": digest,
            "signature": base64.b64encode(sig).decode(),
            "key_id": "signing_public_key.pem",
            "public_key_path": str(PUBLIC_KEY_PATH),
        }

    if _require_signing():
        raise RuntimeError(
            "MIDTERMS_REQUIRE_SIGNING=1 but no Ed25519 private key found. "
            "Set MIDTERMS_SIGNING_PRIVATE_KEY or run: python -m midterms.cli generate-signing-keys"
        )

    # Legacy HMAC fallback (not a public trust root)
    secret = (os.environ.get("MIDTERMS_SIGNING_KEY") or "midterms-dev-signing-key-not-for-production").encode()
    sig = hmac.new(secret, text, hashlib.sha256).hexdigest()
    return {
        "alg": "HMAC-SHA256",
        "sha256": digest,
        "signature": sig,
        "key_id": "MIDTERMS_SIGNING_KEY" if os.environ.get("MIDTERMS_SIGNING_KEY") else "dev-default",
        "warning": "HMAC legacy — not a public trust root; configure Ed25519 for production",
    }


def verify_signature(
    payload: dict[str, Any] | str,
    signature: str,
    *,
    alg: str | None = None,
    public_key_pem: bytes | None = None,
    public_key_path: Path | str | None = None,
) -> bool:
    """Verify Ed25519 (or legacy HMAC) signature.

    Historical seals must pass the public key that was valid when signed
    (``public_key_pem`` / ``public_key_path`` / sidecar ``key_id``), not only
    today's default trust root.
    """
    text = _payload_bytes(payload)
    alg = (alg or "").upper()
    pub = public_key_pem
    if pub is None and public_key_path:
        p = Path(public_key_path)
        if p.exists():
            pub = p.read_bytes()
    if pub is None:
        pub = _load_public_pem()
    if alg.startswith("ED25519") or (not alg and pub):
        if not pub:
            return False
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = serialization.load_pem_public_key(pub)
        if not isinstance(key, Ed25519PublicKey):
            return False
        try:
            key.verify(base64.b64decode(signature), text)
            return True
        except (InvalidSignature, ValueError):
            return False

    got = sign_payload(payload)
    if got.get("alg") == "HMAC-SHA256":
        return hmac.compare_digest(got["signature"], signature)
    return False


def resolve_historical_public_key(key_id: str | None) -> Path | None:
    """Locate a committed public key by key_id (basename under manifests/keys)."""
    if not key_id:
        return PUBLIC_KEY_PATH if PUBLIC_KEY_PATH.exists() else None
    # key_id may be a basename or relative path
    candidates = [
        MANIFESTS_DIR / key_id,
        MANIFESTS_DIR / "keys" / key_id,
        MANIFESTS_DIR / Path(key_id).name,
        PUBLIC_KEY_PATH,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def write_signature_sidecar(path: Path, payload_text: str) -> Path:
    sig = sign_payload(payload_text)
    out = path.with_suffix(path.suffix + ".sig.json")
    out.write_text(json.dumps(sig, indent=2))
    return out
