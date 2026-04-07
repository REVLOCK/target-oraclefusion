"""JWT auth and PEM normalization."""

from __future__ import annotations

import time
from typing import List
from urllib.parse import urlparse

from target_oracle_fusion.exceptions import UploadError


def normalize_base_url(url: str) -> str:
    """Ensure base_url has no trailing slash."""
    return url.rstrip("/")


def validate_base_url(url: str) -> None:
    """Require scheme and host or raise UploadError."""
    if not url or not url.strip():
        raise UploadError("base_url is empty")
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise UploadError(
            "Invalid base_url: must include scheme and host "
            "(e.g. https://your-pod.fa.ocs.oraclecloud.com). "
            f"Got: {url!r}"
        )
    if parsed.scheme not in ("https", "http"):
        raise UploadError(
            f"Invalid base_url scheme {parsed.scheme!r}; use https (or http for non-prod only)"
        )


def require_base_url(raw: str) -> str:
    """Return normalized base_url or raise UploadError."""
    url = normalize_base_url((raw or "").strip())
    if not url:
        raise UploadError("Config missing base_url")
    validate_base_url(url)
    return url


def optional_config_str(config: dict, key: str) -> str:
    """Stripped string from config or empty."""
    v = config.get(key)
    if v is None:
        return ""
    return str(v).strip()


def normalize_pem_key(pem: str) -> str:
    """Fix PEM stored without newlines or with literal \\n."""
    if not pem:
        return pem
    pem = pem.strip().replace("\\n", "\n")

    if " " not in pem or "\n" in pem:
        return pem

    # Space-separated format: split and reconstruct header, base64 body, footer
    tokens = pem.split()
    header_end = _find_pem_header_end(tokens)
    footer_start = _find_pem_footer_start(tokens, header_end)

    if header_end >= len(tokens) or footer_start >= len(tokens):
        return pem

    header = " ".join(tokens[: header_end + 1])
    base64_tokens = tokens[header_end + 1 : footer_start]
    footer = " ".join(tokens[footer_start:])

    return f"{header}\n" + "\n".join(base64_tokens) + f"\n{footer}"


def _find_pem_header_end(tokens: List[str]) -> int:
    """Last header token index."""
    i = 0
    while i < len(tokens) and not tokens[i].endswith("KEY-----"):
        i += 1
    return i


def _find_pem_footer_start(tokens: List[str], after_header: int) -> int:
    """First footer token index."""
    j = after_header + 1
    while j < len(tokens) and tokens[j] != "-----END":
        j += 1
    return j


def build_jwt_token(config: dict) -> str:
    """Sign RS256 JWT from config."""
    try:
        import jwt
    except ImportError as e:
        raise UploadError(
            "JWT auth requires PyJWT[crypto]. Install with: pip install 'PyJWT[crypto]'"
        ) from e

    issuer = config.get("jwt_issuer") or config.get("jwt_iss")
    principal = config.get("jwt_principal") or config.get("jwt_prn")
    private_key = config.get("private_key")
    x5t = config.get("jwt_x5t")

    if not issuer or not principal:
        raise UploadError("JWT auth requires jwt_issuer and jwt_principal in config")
    if not private_key:
        raise UploadError("JWT auth requires private_key (PEM string in config)")

    if isinstance(private_key, bytes):
        private_key = private_key.decode("utf-8")
    private_key = normalize_pem_key(private_key)

    payload = {
        "iss": issuer,
        "prn": principal,
        "iat": int(time.time()) - 60,
        "exp": int(time.time()) + 3600,
    }
    headers = {"alg": "RS256", "typ": "JWT"}
    if x5t:
        headers["x5t"] = x5t

    try:
        return jwt.encode(payload, private_key, algorithm="RS256", headers=headers)
    except Exception as e:
        raise UploadError(
            "JWT signing failed: check private_key (valid PEM, matching algorithm RS256)"
        ) from e


def get_auth_headers(config: dict) -> dict:
    """Authorization: Bearer … from JWT."""
    token = build_jwt_token(config)
    return {"Authorization": f"Bearer {token}"}
