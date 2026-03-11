"""Oracle Fusion client - API client and Hotglue sink.

Per target-intacct pattern: client.py holds the API client for Oracle Fusion.
- upload_zip, get_ess_job_status, poll_ess_job_status: Journal Import API (CSV mode)
- OracleFusionSink: Hotglue sink for Singer mode

Uses JWT auth (per Postman Journal Import collection):
jwt_issuer, jwt_principal, jwt_private_key (or jwt_private_key_path)
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

import requests
from pydantic import BaseModel

from target_hotglue.client import HotglueSink

from target_oracle_fusion.const import (
    DEFAULT_DOCUMENT_ACCOUNT,
    DEFAULT_JOB_NAME,
    DEFAULT_PARAMETER_LIST,
    DEFAULT_POLL_INTERVAL_SECONDS,
)
from target_oracle_fusion.exceptions import UploadError

logger = logging.getLogger(__name__)

# Oracle FSCM REST API path (version may vary per environment)
ERP_INTEGRATIONS_PATH = "/fscmRestApi/resources/11.13.18.05/erpintegrations"


def _normalize_base_url(url: str) -> str:
    """Ensure base_url has no trailing slash."""
    return url.rstrip("/")


def _build_jwt_token(config: dict) -> str:
    """Build RS256 JWT for Oracle Fusion API (per Postman collection)."""
    try:
        import jwt
    except ImportError as e:
        raise UploadError(
            "JWT auth requires PyJWT[crypto]. Install with: pip install 'PyJWT[crypto]'"
        ) from e

    issuer = config.get("jwt_issuer") or config.get("jwt_iss")
    principal = config.get("jwt_principal") or config.get("jwt_prn")
    private_key = config.get("jwt_private_key")
    key_path = config.get("jwt_private_key_path")
    x5t = config.get("jwt_x5t")

    if not issuer or not principal:
        raise UploadError("JWT auth requires jwt_issuer and jwt_principal in config")

    if not private_key and not key_path:
        raise UploadError("JWT auth requires jwt_private_key or jwt_private_key_path in config")

    # Prefer jwt_private_key (string from Hotglue secret) over file path
    if not private_key and key_path:
        with open(Path(key_path).expanduser(), "r") as f:
            private_key = f.read()

    payload = {
        "iss": issuer,
        "prn": principal,
        "iat": int(time.time()) - 60,
        "exp": int(time.time()) + 3600,
    }
    headers = {"alg": "RS256", "typ": "JWT"}
    if x5t:
        headers["x5t"] = x5t

    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers=headers,
    )


def _get_auth_headers(config: dict) -> Dict[str, str]:
    """Return headers with Authorization Bearer token for JWT auth."""
    token = _build_jwt_token(config)
    return {"Authorization": f"Bearer {token}"}


def upload_zip(zip_path: Path, config: dict) -> str:
    """
    Upload zip file to Oracle Fusion via importBulkData.

    Args:
        zip_path: Path to the zip file.
        config: Must include base_url, jwt_issuer, jwt_principal, jwt_private_key (or path).
                Optional: document_account, parameter_list, job_name, file_name.

    Returns:
        ReqstId from the response (used for status polling).

    Raises:
        UploadError: On API failure.
    """
    base_url = _normalize_base_url(config.get("base_url", ""))
    if not base_url:
        raise UploadError("Config missing base_url for Oracle Fusion API")

    auth_headers = _get_auth_headers(config)

    file_name = config.get("file_name") or zip_path.name
    document_account = config.get("document_account", DEFAULT_DOCUMENT_ACCOUNT)
    job_name = config.get("job_name", DEFAULT_JOB_NAME)
    parameter_list = config.get("parameter_list", DEFAULT_PARAMETER_LIST)

    with open(zip_path, "rb") as f:
        zip_bytes = f.read()
    document_content = base64.b64encode(zip_bytes).decode("ascii")

    payload = {
        "OperationName": "importBulkData",
        "DocumentContent": document_content,
        "ContentType": "zip",
        "FileName": file_name,
        "DocumentAccount": document_account,
        "JobName": job_name,
        "ParameterList": parameter_list,
        "CallbackURL": "#NULL",
        "NotificationCode": "10",
        "JobOptions": "ExtractFileType=ALL",
    }

    url = f"{base_url}{ERP_INTEGRATIONS_PATH}"
    headers = {"Content-Type": "application/json", **auth_headers}

    logger.info("Uploading %s to Oracle Fusion (%s)", zip_path.name, base_url)
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        msg = f"Oracle Fusion upload failed: {e}"
        if hasattr(e, "response") and e.response is not None:
            try:
                err_body = e.response.text[:500]
                msg += f" Response: {err_body}"
            except Exception:
                pass
        raise UploadError(msg, response=e) from e

    data = resp.json()
    reqst_id = data.get("ReqstId")
    if not reqst_id:
        raise UploadError(
            f"Oracle Fusion upload response missing ReqstId: {json.dumps(data)[:500]}",
            response=data,
        )

    logger.info("Upload successful. ReqstId=%s", reqst_id)
    return str(reqst_id)


def get_ess_job_status(
    base_url: str,
    request_id: str,
    config: dict,
) -> str:
    """
    Get ESS job execution status for a given request ID.

    Args:
        base_url: Oracle Fusion base URL (no trailing slash).
        request_id: ReqstId from upload response.
        config: Config dict for JWT auth.

    Returns:
        Status string: SUCCEEDED, FAILED, RUNNING, or similar.

    Raises:
        UploadError: On API failure.
    """
    base_url = _normalize_base_url(base_url)
    url = f"{base_url}{ERP_INTEGRATIONS_PATH}"
    params = {"finder": f"ESSExecutionDetailsRF;requestId={request_id}"}
    headers = _get_auth_headers(config)

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UploadError(f"ESS job status check failed: {e}", response=e) from e

    data = resp.json()
    items = data.get("items") or []
    if not items:
        return "UNKNOWN"

    req_status = items[0].get("RequestStatus")
    if not req_status:
        return "UNKNOWN"

    try:
        if isinstance(req_status, str):
            parsed = json.loads(req_status)
        else:
            parsed = req_status
        jobs = parsed.get("JOBS", {})
        return jobs.get("STATUS", "UNKNOWN")
    except (json.JSONDecodeError, TypeError):
        return "UNKNOWN"


def poll_ess_job_status(
    base_url: str,
    request_id: str,
    config: dict,
    *,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    max_wait_seconds: int | None = None,
) -> str:
    """
    Poll ESS job status every poll_interval_seconds until SUCCEEDED or FAILED.

    Args:
        base_url: Oracle Fusion base URL.
        request_id: ReqstId from upload.
        config: Config dict for JWT auth.
        poll_interval_seconds: Seconds between status checks (default 300 = 5 min).
        max_wait_seconds: Optional max total wait; None = wait indefinitely.

    Returns:
        Final status: SUCCEEDED or FAILED.

    Raises:
        UploadError: If status is FAILED or max_wait exceeded.
    """
    terminal_statuses = ("SUCCEEDED", "COMPLETED", "FAILED", "ERROR", "WARNING", "CANCELLED")
    success_statuses = ("SUCCEEDED", "COMPLETED")
    start = time.monotonic()

    while True:
        status = get_ess_job_status(base_url, request_id, config)
        logger.info("ESS job status: %s (ReqstId=%s)", status, request_id)

        if status in terminal_statuses:
            if status in success_statuses:
                return status
            raise UploadError(f"ESS job finished with status: {status}", response={"status": status})

        elapsed = time.monotonic() - start
        if max_wait_seconds is not None and elapsed >= max_wait_seconds:
            raise UploadError(
                f"ESS job still in progress after {max_wait_seconds}s (status={status})",
                response={"status": status},
            )

        logger.info("Waiting %d seconds before next status check...", poll_interval_seconds)
        time.sleep(poll_interval_seconds)


# OracleFusionSink moved to sink.py to avoid importing target_hotglue
# (numpy/joblib segfault on macOS). Import from target_oracle_fusion.sink when needed.
