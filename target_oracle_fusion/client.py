"""REST bulk import upload and ESS job status via SOAP report."""

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
    DEFAULT_POLL_INTERVAL_SECONDS,
    ERP_INTEGRATIONS_PATH,
    ESS_REPORT_SOAP_PATH,
    ESS_STATUS_FAILURE,
    ESS_STATUS_SUCCESS,
)
from target_oracle_fusion.exceptions import UploadError

logger = logging.getLogger(__name__)


def _format_request_error(prefix: str, exc: requests.RequestException) -> str:
    """Build a short error string from a requests failure."""
    if isinstance(exc, requests.exceptions.SSLError):
        return f"{prefix}: SSL error ({exc}). Check base_url and certs."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"{prefix}: Connection failed ({exc}). Check base_url and network."
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{prefix}: Timeout ({exc})."

    response = getattr(exc, "response", None)
    if response is not None and response.status_code in (401, 403):
        body = ""
        try:
            body = response.text[:500]
        except Exception:
            pass
        return (
            f"{prefix}: HTTP {response.status_code} auth failed. "
            f"Check JWT config and clock. {body}"
        )

    msg = f"{prefix}: {exc}"
    if response is not None:
        try:
            msg += f" Response: {response.text[:500]}"
        except Exception:
            pass
    return msg


def _parameter_list_with_batch_group(raw: str, batch_group_id: str) -> str:
    """Journal Import ParameterList: set 4th comma-separated field to batch GROUP_ID."""
    if not raw.strip():
        return raw
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) < 4:
        return raw
    parts[3] = batch_group_id.strip()
    return ",".join(parts)


def upload_zip(
    zip_path: Path,
    config: dict,
    *,
    batch_group_id: str,
) -> str:
    """POST importBulkData; return ReqstId. Raises UploadError on failure."""
    base_url = auth.require_base_url(config.get("base_url", ""))

    auth_headers = auth.get_auth_headers(config)
    file_name = config.get("file_name") or zip_path.name
    document_account = DEFAULT_DOCUMENT_ACCOUNT
    job_name = config.get("job_name", DEFAULT_JOB_NAME)
    raw_params = auth.optional_config_str(config, "parameter_list")
    parameter_list = _parameter_list_with_batch_group(raw_params, batch_group_id)
    logger.info("ParameterList=%s", parameter_list)

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

    logger.info("Upload zip=%s url=%s batch=%s", zip_path.name, base_url, batch_group_id)
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UploadError(_format_request_error("Upload failed", e), response=e) from e

    data = resp.json()
    reqst_id = data.get("ReqstId")
    if not reqst_id:
        raise UploadError(
            f"Upload response missing ReqstId: {json.dumps(data)[:500]}",
            response=data,
        )

    logger.info("Upload ok ReqstId=%s", reqst_id)
    return str(reqst_id)


def _get_detailed_error_message(
    base_url: str,
    failed_req_id: str,
    failed_status: str,
    config: dict,
) -> str:
    """Best-effort error text from the job error log API."""
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
    """Raise UploadError if any report row is a failure status."""
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
    """All-success → SUCCEEDED; else first non-success status."""
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
    """SOAP report → CSV status. Raises UploadError on HTTP error or failed job state."""
    base_url = auth.normalize_base_url(base_url)
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
        raise UploadError(_format_request_error("ESS status request failed", e), response=e) from e

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
    """Poll get_ess_job_status until success or UploadError (failure or timeout)."""
    base_url = auth.require_base_url(base_url)
    start = time.monotonic()

    while True:
        status = get_ess_job_status(base_url, request_id, config)
        logger.info("ESS status=%s ReqstId=%s", status, request_id)

        if status in ESS_STATUS_SUCCESS:
            return status

        elapsed = time.monotonic() - start
        if max_wait_seconds is not None and elapsed >= max_wait_seconds:
            raise UploadError(
                f"ESS still running after {max_wait_seconds}s (status={status})",
                response={"status": status},
            )

        logger.info("Next ESS poll in %ds", poll_interval_seconds)
        time.sleep(poll_interval_seconds)
