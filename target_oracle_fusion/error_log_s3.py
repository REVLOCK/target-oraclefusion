"""Upload a local text file to S3 using config + environment settings."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from target_oracle_fusion.const import (
    ENV_AWS_ACCESS_KEY_ID,
    ENV_AWS_S3_BUCKET,
    ENV_AWS_SECRET_ACCESS_KEY,
    ESS_ERROR_LOG_S3_PREFIX_TEMPLATE,
    HOTGLUE_ENV_FLOW,
    HOTGLUE_ENV_JOB_ID,
    HOTGLUE_ENV_TENANT,
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


def upload_ess_error_log_txt(
    local_txt_path: Path,
    request_id: str,
    *,
    source_config: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Upload ``local_txt_path`` to S3 and return ``s3://...`` or ``None``."""
    cfg = dict(source_config or {})
    gaps = s3_config_gaps(cfg)
    if gaps:
        gap_txt = "; ".join(gaps)
        logger.info(
            "ESS error log S3 skipped: %s",
            gap_txt or "unknown gap (see DEBUG)",
        )
        return None

    access = _s3_value(cfg, ENV_AWS_ACCESS_KEY_ID)
    secret = _s3_value(cfg, ENV_AWS_SECRET_ACCESS_KEY)
    bucket = _s3_value(cfg, ENV_AWS_S3_BUCKET)
    path = Path(local_txt_path)
    if not path.is_file():
        logger.warning("ESS error log S3 upload skipped: not a file: %s", path)
        return None

    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError:
        logger.info(
            "ESS error log S3 skipped: boto3 not installed; install target-oracle-fusion[s3] in the target env",
        )
        return None

    key = resolve_error_log_s3_key(path.name)
    logger.info(
        "ESS error log S3 uploading bucket=%s key=%s file=%s",
        bucket,
        key,
        path.name,
    )

    try:
        client = boto3.client(
            "s3",
            aws_access_key_id=access,
            aws_secret_access_key=secret,
        )
        client.upload_file(
            str(path),
            bucket,
            key,
            ExtraArgs={"ContentType": "text/plain; charset=utf-8"},
        )
    except Exception as e:
        logger.info("ESS error log S3 upload failed: %s", e)
        return None

    uri = f"s3://{bucket}/{key}"
    logger.info("ESS error log S3 done (request_id=%s, file=%s) → %s", request_id, path.name, uri)
    return uri
