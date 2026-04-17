"""Upload ESS failure artifacts to S3 (zip of journal input, GL CSV, Oracle error log)."""

from __future__ import annotations

import importlib
import logging
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping, Optional

from target_oracle_fusion.const import (
    DEFAULT_OUTPUT_PATH,
    ENV_AWS_ACCESS_KEY_ID,
    ENV_AWS_S3_BUCKET,
    ENV_AWS_SECRET_ACCESS_KEY,
    ESS_ERROR_LOG_S3_PREFIX_TEMPLATE,
    HOTGLUE_ENV_FLOW,
    HOTGLUE_ENV_JOB_ID,
    HOTGLUE_ENV_TENANT,
    INPUT_FILENAME,
    OUTPUT_FILENAME,
)

logger = logging.getLogger(__name__)


def _env(name: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return ""
    return str(raw).strip()


def _str_from_cfg(cfg: Mapping[str, Any], key: str) -> str:
    v = cfg.get(key)
    if v is None:
        return ""
    return str(v).strip()


def _s3_value(cfg: Mapping[str, Any], key: str) -> str:
    """Get a required S3 value from config first, then env fallback."""
    by_cfg = _str_from_cfg(cfg, key)
    if by_cfg:
        return by_cfg

    return _env(key)


def _missing_hotglue_envs() -> list[str]:
    required = (HOTGLUE_ENV_TENANT, HOTGLUE_ENV_FLOW, HOTGLUE_ENV_JOB_ID)
    return [f"env.{name}" for name in required if not _env(name)]


def _import_boto3():
    """Import boto3 from current runtime, with a fallback to the system site-packages path."""
    try:
        return importlib.import_module("boto3")
    except ImportError:
        pass

    pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
    system_site = f"/usr/local/lib/python{pyver}/site-packages"
    if system_site not in sys.path:
        sys.path.append(system_site)

    return importlib.import_module("boto3")


def format_output_path_prefix() -> str:
    """Build the fixed output prefix from ``TENANT``/``FLOW``/``JOB_ID`` env vars.

    Returns an empty string if any required env var is missing.
    """
    tenant = _env(HOTGLUE_ENV_TENANT)
    flow_id = _env(HOTGLUE_ENV_FLOW)
    job_id = _env(HOTGLUE_ENV_JOB_ID)
    if not (tenant and flow_id and job_id):
        logger.debug(
            "ESS error log S3: skip prefix format (env): %s=%s %s=%s %s=%s",
            HOTGLUE_ENV_TENANT,
            "set" if tenant else "missing",
            HOTGLUE_ENV_FLOW,
            "set" if flow_id else "missing",
            HOTGLUE_ENV_JOB_ID,
            "set" if job_id else "missing",
        )
        return ""
    return ESS_ERROR_LOG_S3_PREFIX_TEMPLATE.format(tenant=tenant, flow_id=flow_id, job_id=job_id)


def build_s3_object_key(prefix: str, filename: str) -> str:
    """Build ``{prefix}/{filename}`` (or bare ``filename``) for S3."""
    p = prefix.strip().strip("/")
    fn = Path(filename).name.strip()
    if not fn:
        return p
    return f"{p}/{fn}" if p else fn


def resolve_error_log_s3_key(local_filename: str) -> str:
    """Return the final S3 object key for a local filename."""
    prefix = format_output_path_prefix()
    key = build_s3_object_key(prefix, local_filename)
    return key


def s3_config_gaps(cfg: Mapping[str, Any]) -> list[str]:
    """Return human-readable missing requirements (without secret values)."""
    gaps: list[str] = []
    if not _s3_value(cfg, ENV_AWS_ACCESS_KEY_ID):
        gaps.append(f"config.{ENV_AWS_ACCESS_KEY_ID}")
    if not _s3_value(cfg, ENV_AWS_SECRET_ACCESS_KEY):
        gaps.append(f"config.{ENV_AWS_SECRET_ACCESS_KEY}")
    if not _s3_value(cfg, ENV_AWS_S3_BUCKET):
        gaps.append(f"config.{ENV_AWS_S3_BUCKET}")
    gaps.extend(_missing_hotglue_envs())
    if gaps:
        return gaps
    formatted = format_output_path_prefix()
    if not formatted:
        gaps.append(
            "output_path_prefix_expanded_empty "
            "(use {tenant}/{flow_id}/{job_id} in template; env TENANT, FLOW, JOB_ID must be set)"
        )
    return gaps


def _collect_bundle_members(local_txt_path: Path) -> list[tuple[Path, str]]:
    """Return (path, arcname) for journal + GL CSV under ``DEFAULT_OUTPUT_PATH`` plus Oracle ``.txt``."""
    members: list[tuple[Path, str]] = []
    used_names: set[str] = set()
    root = Path(DEFAULT_OUTPUT_PATH)

    def _add(path: Path, label: str) -> None:
        if not path.is_file():
            logger.info("ESS failure bundle: skip %s (not a file): %s", label, path)
            return
        name = path.name
        if name in used_names:
            name = f"{label}_{name}"
        used_names.add(name)
        members.append((path, name))

    _add(root / INPUT_FILENAME, "input")
    _add(root / f"{OUTPUT_FILENAME}.csv", "transformed")
    _add(Path(local_txt_path), "error_log")
    return members


def upload_ess_failure_bundle_zip(
    local_txt_path: Path,
    request_id: str,
    *,
    source_config: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Zip journal input, transformed GL CSV, and Oracle error ``.txt``, upload to S3.

    Returns ``s3://...`` or ``None`` when skipped or upload fails.
    """
    cfg = dict(source_config or {})
    gaps = s3_config_gaps(cfg)
    if gaps:
        gap_txt = "; ".join(gaps)
        logger.info(
            "ESS failure bundle S3 skipped: %s",
            gap_txt or "unknown gap (see DEBUG)",
        )
        return None

    txt_path = Path(local_txt_path)
    if not txt_path.is_file():
        logger.warning("ESS failure bundle S3 skipped: error log not a file: %s", txt_path)
        return None

    members = _collect_bundle_members(txt_path)
    if not members:
        logger.warning("ESS failure bundle S3 skipped: no files to zip")
        return None

    access = _s3_value(cfg, ENV_AWS_ACCESS_KEY_ID)
    secret = _s3_value(cfg, ENV_AWS_SECRET_ACCESS_KEY)
    bucket = _s3_value(cfg, ENV_AWS_S3_BUCKET)

    try:
        boto3 = _import_boto3()
    except ImportError:
        logger.info(
            "ESS failure bundle S3 skipped: boto3 unavailable in target runtime; verify deploy dependencies",
        )
        return None

    bundle_name = f"{request_id}-ess-failure-bundle.zip"
    key = resolve_error_log_s3_key(bundle_name)
    tmp_zip: str | None = None
    try:
        fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, arcname in members:
                zf.write(path, arcname=arcname)

        logger.info(
            "ESS failure bundle S3 uploading bucket=%s key=%s members=%s",
            bucket,
            key,
            [a for _, a in members],
        )
        client = boto3.client(
            "s3",
            aws_access_key_id=access,
            aws_secret_access_key=secret,
        )
        client.upload_file(
            tmp_zip,
            bucket,
            key,
            ExtraArgs={"ContentType": "application/zip"},
        )
    except Exception as e:
        logger.info("ESS failure bundle S3 upload failed: %s", e)
        return None
    finally:
        if tmp_zip and Path(tmp_zip).exists():
            try:
                Path(tmp_zip).unlink()
            except OSError:
                pass

    uri = f"s3://{bucket}/{key}"
    logger.info("ESS failure bundle S3 done (request_id=%s) → %s", request_id, uri)
    return uri
