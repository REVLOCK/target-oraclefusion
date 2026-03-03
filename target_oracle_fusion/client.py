"""Oracle Fusion client - API client and Hotglue sink.

Per target-intacct pattern: client.py holds the API client for Oracle Fusion.
- upload_zip, get_ess_job_status, poll_ess_job_status: Journal Import API (CSV mode)
- OracleFusionSink: Hotglue sink for Singer mode
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


def upload_zip(zip_path: Path, config: dict) -> str:
    """
    Upload zip file to Oracle Fusion via importBulkData.

    Args:
        zip_path: Path to the zip file.
        config: Must include base_url, api_username, api_password.
                Optional: document_account, parameter_list, job_name, file_name.

    Returns:
        ReqstId from the response (used for status polling).

    Raises:
        UploadError: On API failure.
    """
    base_url = _normalize_base_url(config.get("base_url", ""))
    if not base_url:
        raise UploadError("Config missing base_url for Oracle Fusion API")

    username = config.get("api_username") or config.get("username")
    password = config.get("api_password") or config.get("password")
    if not username or not password:
        raise UploadError("Config missing api_username and api_password for Oracle Fusion API")

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
    auth = (username, password)
    headers = {"Content-Type": "application/json"}

    logger.info("Uploading %s to Oracle Fusion (%s)", zip_path.name, base_url)
    try:
        resp = requests.post(url, json=payload, auth=auth, headers=headers, timeout=120)
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
    username: str,
    password: str,
) -> str:
    """
    Get ESS job execution status for a given request ID.

    Args:
        base_url: Oracle Fusion base URL (no trailing slash).
        request_id: ReqstId from upload response.
        username: API username.
        password: API password.

    Returns:
        Status string: SUCCEEDED, FAILED, RUNNING, or similar.

    Raises:
        UploadError: On API failure.
    """
    base_url = _normalize_base_url(base_url)
    url = f"{base_url}{ERP_INTEGRATIONS_PATH}"
    params = {"finder": f"ESSExecutionDetailsRF;requestId={request_id}"}
    auth = (username, password)

    try:
        resp = requests.get(url, params=params, auth=auth, timeout=60)
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
    username: str,
    password: str,
    *,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    max_wait_seconds: int | None = None,
) -> str:
    """
    Poll ESS job status every poll_interval_seconds until SUCCEEDED or FAILED.

    Args:
        base_url: Oracle Fusion base URL.
        request_id: ReqstId from upload.
        username: API username.
        password: API password.
        poll_interval_seconds: Seconds between status checks (default 300 = 5 min).
        max_wait_seconds: Optional max total wait; None = wait indefinitely.

    Returns:
        Final status: SUCCEEDED or FAILED.

    Raises:
        UploadError: If status is FAILED or max_wait exceeded.
    """
    terminal_statuses = ("SUCCEEDED", "FAILED", "ERROR", "WARNING", "CANCELLED")
    start = time.monotonic()

    while True:
        status = get_ess_job_status(base_url, request_id, username, password)
        logger.info("ESS job status: %s (ReqstId=%s)", status, request_id)

        if status in terminal_statuses:
            if status == "SUCCEEDED":
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


# --- Hotglue sink (Singer mode) ---


class JournalEntrySchema(BaseModel):
    """Unified schema for Journal Entry records (optional validation)."""

    class Config:
        extra = "allow"


class OracleFusionSink(HotglueSink):
    """Base sink for Oracle Fusion - extends HotglueSink (per record).

    Per Hotglue docs: preprocess_record builds payload, upsert_record sends to API.
    """

    auto_validate_unified_schema = False  # Skip strict schema validation

    @property
    def base_url(self) -> str:
        return self._config.get("base_url", "https://api.oracle-fusion.example.com")

    @property
    def endpoint(self) -> str:
        return self._config.get("endpoint", "/journal-entries")

    @property
    def unified_schema(self) -> type[BaseModel]:
        return JournalEntrySchema

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Transform record to API payload format. Override in sinks."""
        return record

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Send record to API. Override in sinks for custom logic."""
        response = self.request_api("POST", request_data=record)
        record_id = response.json().get("id") if response.content else None
        return record_id, response.ok, {}

    @property
    def authenticator(self) -> None:
        """No OAuth authenticator; use static Bearer token via http_headers."""
        return None

    @property
    def http_headers(self) -> Dict[str, Any]:
        """Add Bearer token when access_token is in config."""
        headers = dict(getattr(super(), "http_headers", {}))
        if self._config.get("access_token"):
            headers["Authorization"] = f"Bearer {self._config.get('access_token')}"
        return headers
