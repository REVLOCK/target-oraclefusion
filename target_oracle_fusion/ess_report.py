"""ESS job status - SOAP report and Oracle error log parsing."""

from __future__ import annotations

import base64
import csv
import logging
import re
import shutil
import tempfile
import zipfile
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

import requests

from target_oracle_fusion import auth
from target_oracle_fusion.const import (
    DEFAULT_OUTPUT_PATH,
    ERP_INTEGRATIONS_PATH,
    ESS_SCRATCH_DIRNAME,
)
from target_oracle_fusion.exceptions import UploadError

logger = logging.getLogger(__name__)


def _ess_error_log_scratch_dir() -> Path:
    """Directory for ESS error-log zip/extract (under ``DEFAULT_OUTPUT_PATH``)."""
    root = Path(DEFAULT_OUTPUT_PATH)
    root.mkdir(parents=True, exist_ok=True)
    scratch = root / ESS_SCRATCH_DIRNAME
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


# Type alias for ESS report rows: (ESSREQID, REQUESTID, EXECUTABLE_STATUS)
EssReportRow = Tuple[str, str, str]

# Error Key format: "EF04   The account isn't valid. Check your cross validation rules..."
_ERROR_KEY_PATTERN = re.compile(r"^([A-Z]{2}\d{2})\s{2,}(.+)$")
# Error Lines section: first column has "EF04,EP01" or "EF04"
_ERROR_LINE_CODE_PATTERN = re.compile(r"^([A-Z]{2}\d{2})(?:,[A-Z]{2}\d{2})*\s")


def build_ess_report_soap_body(request_id: str, report_path: str) -> str:
    """Build SOAP request body for ESS job details report."""
    rid = escape(str(request_id))
    rpath = escape(str(report_path))
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">
   <soap:Header/>
   <soap:Body>
      <pub:runReport>
         <pub:reportRequest>
            <pub:attributeFormat>csv</pub:attributeFormat>
            <pub:parameterNameValues>
               <pub:item>
                  <pub:name>ESSReqID</pub:name>
                  <pub:values>
                     <pub:item>{rid}</pub:item>
                  </pub:values>
               </pub:item>
            </pub:parameterNameValues>
            <pub:reportAbsolutePath>{rpath}</pub:reportAbsolutePath>
            <pub:sizeOfDataChunkDownload>-1</pub:sizeOfDataChunkDownload>
         </pub:reportRequest>
         <pub:appParams>?</pub:appParams>
      </pub:runReport>
   </soap:Body>
</soap:Envelope>'''


def parse_ess_report_response(resp_text: str) -> List[EssReportRow]:
    """
    Parse SOAP response: extract reportBytes, base64 decode, parse CSV.

    Returns list of (ESSREQID, REQUESTID, EXECUTABLE_STATUS) tuples.
    """
    match = re.search(
        r"<(?:ns2:)?reportBytes>(.*?)</(?:ns2:)?reportBytes>",
        resp_text,
        re.DOTALL,
    )
    if not match:
        raise UploadError("ESS report response missing reportBytes", response=resp_text[:500])

    encoded = match.group(1).strip()
    if not encoded:
        return []

    try:
        decoded = base64.b64decode(encoded).decode("utf-8-sig", errors="replace")
    except Exception as e:
        raise UploadError(f"Failed to decode ESS report bytes: {e}", response=encoded[:200]) from e

    return _parse_ess_report_csv(decoded)


def _parse_ess_report_csv(csv_content: str) -> List[EssReportRow]:
    """Parse ESS report CSV into (ESSREQID, REQUESTID, EXECUTABLE_STATUS) rows."""
    rows: List[EssReportRow] = []
    reader = csv.DictReader(StringIO(csv_content))
    for row in reader:
        req_id = row.get("REQUESTID", row.get("REQUEST ID", ""))
        status = (row.get("EXECUTABLE_STATUS") or row.get("EXECUTABLE STATUS", "")).strip().upper()
        if status:
            rows.append((row.get("ESSREQID", ""), req_id, status))
    return rows


def fetch_ess_job_error_log(
    base_url: str,
    request_id: str,
    config: dict,
) -> Optional[str]:
    """
    Fetch ESS job execution details (error log) for a failed request.

    GET erpintegrations?finder=ESSJobExecutionDetailsRF;requestId=X,fileType=ALL
    Returns DocumentContent (base64 zip) or None if not found.
    """
    base_url = auth.normalize_base_url(base_url)
    url = f"{base_url}{ERP_INTEGRATIONS_PATH}"
    params = {"finder": f"ESSJobExecutionDetailsRF;requestId={request_id},fileType=ALL"}
    headers = auth.get_auth_headers(config)

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Could not fetch ESS job error log for request %s: %s", request_id, e)
        return None

    data = resp.json()
    items = data.get("items") or []
    if not items:
        return None

    doc_content = items[0].get("DocumentContent")
    return doc_content if doc_content else None


def _parse_error_key_mapping(content: str) -> Dict[str, str]:
    """Parse Error Key section and return dict of code -> description."""
    mapping: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        match = _ERROR_KEY_PATTERN.match(line)
        if match:
            code, desc = match.groups()
            desc = desc.strip()
            if desc and len(desc) > 5:
                mapping[code.upper()] = desc
    return mapping


def _get_first_error_code_from_report(content: str) -> Optional[str]:
    """Extract first error code from Error Lines section (e.g. EF04 from 'EF04,EP01')."""
    in_error_lines = False
    for line in content.splitlines():
        stripped = line.strip()
        if "Error Lines" in line:
            in_error_lines = True
            continue
        if in_error_lines:
            if "=" * 20 in stripped:
                break
            if not stripped or stripped.startswith("-"):
                continue
            match = _ERROR_LINE_CODE_PATTERN.match(stripped)
            if match:
                return match.group(1).upper()
            parts = stripped.split()
            if parts:
                first = parts[0].upper()
                if "," in first:
                    codes = [c.strip() for c in first.split(",")]
                    for c in codes:
                        if len(c) == 4 and c[:2].isalpha() and c[2:].isdigit():
                            return c
                elif _ERROR_KEY_PATTERN.match(first):
                    return first
    return None


def _extract_error_from_oracle_report(content: str, request_id: str) -> Optional[str]:
    """
    Parse Oracle Journal Import report: get error code from Error Lines,
    look up description from Error Key, return formatted message.
    """
    error_key_map = _parse_error_key_mapping(content)
    error_code = _get_first_error_code_from_report(content)

    if error_code and error_code in error_key_map:
        description = error_key_map[error_code].strip()
        suffix = "" if description.endswith(".") else "."
        return f"{error_code}: {description}{suffix} (Reference ID: {request_id})"

    if error_code:
        return f"{error_code}: Unknown error. (Reference ID: {request_id})"

    return None


def extract_first_error_from_log(document_content_b64: str, request_id: str) -> str:
    """
    Decode base64 DocumentContent (zip), unzip, read {request_id}.txt,
    extract first error message. Cleans up temp files.
    """
    try:
        zip_bytes = base64.b64decode(document_content_b64)
    except Exception as e:
        return f"Failed to decode error log: {e}"

    extract_dir = None
    tmp_zip_path = None
    scratch = _ess_error_log_scratch_dir()

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=str(scratch)) as tmp_zip:
            tmp_zip.write(zip_bytes)
            tmp_zip_path = tmp_zip.name

        extract_dir = tempfile.mkdtemp(dir=str(scratch))
        with zipfile.ZipFile(tmp_zip_path, "r") as zf:
            zf.extractall(extract_dir)

        txt_path = _find_txt_file_in_dir(Path(extract_dir), request_id)
        if not txt_path:
            return "Error log file not found in download"

        content = txt_path.read_text(encoding="utf-8", errors="replace")
        error_msg = _extract_error_from_oracle_report(content, request_id)
        if error_msg:
            return error_msg
        if content.strip():
            return (
                "Oracle error log did not contain a recognizable error code. "
                f"(Reference ID: {request_id})"
            )
        return f"No error details in log file. (Reference ID: {request_id})"

    except Exception as e:
        return f"Failed to extract error from log: {e}"
    finally:
        _cleanup_temp_files(extract_dir, tmp_zip_path)


def _find_txt_file_in_dir(extract_dir: Path, request_id: str) -> Optional[Path]:
    """Find {request_id}.txt or first .txt file in directory."""
    primary = extract_dir / f"{request_id}.txt"
    if primary.exists():
        return primary
    txt_files = list(extract_dir.rglob("*.txt"))
    return txt_files[0] if txt_files else None


def _cleanup_temp_files(extract_dir: Optional[str], tmp_zip_path: Optional[str]) -> None:
    """Remove temporary zip and extracted directory."""
    if extract_dir and Path(extract_dir).exists():
        try:
            shutil.rmtree(extract_dir)
            logger.debug("Removed ESS error log extract dir: %s", extract_dir)
        except OSError as e:
            logger.warning("Could not remove extract dir %s: %s", extract_dir, e)
    if tmp_zip_path and Path(tmp_zip_path).exists():
        try:
            Path(tmp_zip_path).unlink()
            logger.debug("Removed ESS error log temp zip: %s", tmp_zip_path)
        except OSError as e:
            logger.warning("Could not remove temp zip %s: %s", tmp_zip_path, e)
