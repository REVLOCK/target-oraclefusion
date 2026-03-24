"""Oracle Fusion REST/SOAP client: journal import upload and ESS job status polling.

JWT config: jwt_issuer, jwt_principal, private_key (optional jwt_x5t).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import requests

from target_oracle_fusion import auth
from target_oracle_fusion import ess_report
from target_oracle_fusion.const import (
    DEFAULT_DOCUMENT_ACCOUNT,
    DEFAULT_ESS_JOB_REPORT_PATH,
    DEFAULT_JOB_NAME,
    DEFAULT_PARAMETER_LIST,
    DEFAULT_POLL_INTERVAL_SECONDS,
    ERP_INTEGRATIONS_PATH,
    ESS_REPORT_SOAP_PATH,
    ESS_STATUS_FAILURE,
    ESS_STATUS_SUCCESS,
)
from target_oracle_fusion.exceptions import UploadError

logger = logging.getLogger(__name__)


def _format_request_error(prefix: str, exc: requests.RequestException) -> str:
    """Format RequestException with optional response body and common diagnoses."""
    if isinstance(exc, requests.exceptions.SSLError):
        return (
            f"{prefix}: SSL error connecting to Oracle Fusion. "
            f"Check base_url and certificates. ({exc})"
        )
    if isinstance(exc, requests.exceptions.ConnectionError):
        return (
            f"{prefix}: Could not reach host. Verify base_url, DNS, and network/VPN. ({exc})"
        )
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{prefix}: Request timed out. ({exc})"

    response = getattr(exc, "response", None)
    if response is not None and response.status_code in (401, 403):
        body = ""
        try:
            body = response.text[:500]
        except Exception:
            pass
        return (
            f"{prefix}: HTTP {response.status_code}. "
            "Authentication failed: check jwt_issuer, jwt_principal, private_key, "
            f"jwt_x5t (if required), and server clock skew. Response: {body}"
        )

    msg = f"{prefix}: {exc}"
    if response is not None:
        try:
            msg += f" Response: {response.text[:500]}"
        except Exception:
            pass
    return msg


def upload_zip(zip_path: Path, config: dict) -> str:
    """
    Upload zip file to Oracle Fusion via importBulkData.

    Args:
        zip_path: Path to the zip file.
        config: Must include base_url, jwt_issuer, jwt_principal, private_key.
                Optional: document_account, parameter_list, job_name, file_name.

    Returns:
        ReqstId from the response (used for status polling).

    Raises:
        UploadError: On API failure.
    """
    base_url = auth.normalize_base_url(config.get("base_url", ""))
    if not base_url:
        raise UploadError("Config missing base_url for Oracle Fusion API")
    auth.validate_base_url(base_url)

    auth_headers = auth.get_auth_headers(config)
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
        raise UploadError(_format_request_error("Oracle Fusion upload failed", e), response=e) from e

    data = resp.json()
    reqst_id = data.get("ReqstId")
    if not reqst_id:
        raise UploadError(
            f"Oracle Fusion upload response missing ReqstId: {json.dumps(data)[:500]}",
            response=data,
        )

    logger.info("Upload successful. ReqstId=%s", reqst_id)
    return str(reqst_id)


def _get_detailed_error_message(
    base_url: str,
    failed_req_id: str,
    failed_status: str,
    config: dict,
) -> str:
    """
    Fetch error log for the failed request and extract detailed message.
    Uses failed row's REQUESTID to call error log API.
    """
    base_msg = f"ESS job failed: REQUEST_ID={failed_req_id} status={failed_status}"

    doc_content = ess_report.fetch_ess_job_error_log(base_url, failed_req_id, config)
    if not doc_content:
        return base_msg

    extracted = ess_report.extract_first_error_from_log(doc_content, failed_req_id)
    if extracted and "Failed to" not in extracted:
        return extracted

    return base_msg


def _check_for_failures_and_raise(
    base_url: str,
    rows: list[ess_report.EssReportRow],
    config: dict,
) -> None:
    """Raise UploadError if any row has failure status."""
    for _, req_id, status in rows:
        if status in ESS_STATUS_FAILURE:
            error_msg = _get_detailed_error_message(
                base_url, req_id, status, config
            )
            raise UploadError(
                error_msg,
                response={"request_id": req_id, "status": status},
            )


def _aggregate_ess_status(rows: list[ess_report.EssReportRow]) -> str:
    """Return aggregated status: SUCCEEDED if all success, else first in-progress status."""
    if not rows:
        return "UNKNOWN"

    if all(status in ESS_STATUS_SUCCESS for _, _, status in rows):
        return "SUCCEEDED"

    for _, _, status in rows:
        if status not in ESS_STATUS_SUCCESS:
            return status

    return "UNKNOWN"


def get_ess_job_status(
    base_url: str,
    request_id: str,
    config: dict,
) -> str:
    """
    Get ESS job execution status via SOAP report API.

    Calls ExternalReportWSSService runReport with ESS job details report,
    base64-decodes the response CSV, and returns aggregated status.

    Status semantics:
    - Terminal success: SUCCEEDED, SUCCEEDED_WITH_WARNINGS, COMPLETED
    - Terminal failure: ERROR, FAILED, CANCELLED, WARNING → raises UploadError
    - In progress: READY, RUNNING, PAUSED, WAITING, BLOCKED

    Args:
        base_url: Oracle Fusion base URL (no trailing slash).
        request_id: ReqstId from upload response (ESSReqID).
        config: Config dict for JWT auth.

    Returns:
        Status string: SUCCEEDED, SUCCEEDED_WITH_WARNINGS, or in-progress status.

    Raises:
        UploadError: On API failure or if any job has ERROR/FAILED/CANCELLED.
    """
    base_url = auth.normalize_base_url(base_url)
    auth.validate_base_url(base_url)
    url = f"{base_url}{ESS_REPORT_SOAP_PATH}"
    report_path = config.get("ess_job_report_path", DEFAULT_ESS_JOB_REPORT_PATH)

    headers = {
        "Content-Type": "application/soap+xml;charset=UTF-8",
        "SOAPAction": "runReport",
        **auth.get_auth_headers(config),
    }
    body = ess_report.build_ess_report_soap_body(request_id, report_path)

    try:
        resp = requests.post(url, data=body, headers=headers, timeout=90)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UploadError(
            _format_request_error("ESS job status (SOAP) failed", e),
            response=e,
        ) from e

    rows = ess_report.parse_ess_report_response(resp.text)
    _check_for_failures_and_raise(base_url, rows, config)
    return _aggregate_ess_status(rows)


def poll_ess_job_status(
    base_url: str,
    request_id: str,
    config: dict,
    *,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    max_wait_seconds: int | None = None,
) -> str:
    """
    Poll ESS job status every poll_interval_seconds (default 5 min) until terminal status.

    Uses SOAP report API. get_ess_job_status raises on ERROR/FAILED/CANCELLED/WARNING.
    Terminal success: SUCCEEDED, SUCCEEDED_WITH_WARNINGS, COMPLETED.

    Args:
        base_url: Oracle Fusion base URL.
        request_id: ReqstId from upload.
        config: Config dict for JWT auth.
        poll_interval_seconds: Seconds between status checks (default 300 = 5 min).
        max_wait_seconds: Optional max total wait; None = wait indefinitely.

    Returns:
        Final status: SUCCEEDED or SUCCEEDED_WITH_WARNINGS.

    Raises:
        UploadError: If any job has ERROR/FAILED/CANCELLED or max_wait exceeded.
    """
    start = time.monotonic()

    while True:
        status = get_ess_job_status(base_url, request_id, config)
        logger.info("ESS job status: %s (ReqstId=%s)", status, request_id)

        if status in ESS_STATUS_SUCCESS:
            return status

        elapsed = time.monotonic() - start
        if max_wait_seconds is not None and elapsed >= max_wait_seconds:
            raise UploadError(
                f"ESS job still in progress after {max_wait_seconds}s (status={status})",
                response={"status": status},
            )

        logger.info("Waiting %d seconds before next status check...", poll_interval_seconds)
        time.sleep(poll_interval_seconds)
