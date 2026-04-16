"""ESS error log .txt → S3 from merged config + Hotglue env; ``pip install 'target-oracle-fusion[s3]'``.

Singer **targets** get settings from the flattened target ``config`` (Hotglue); transforms also have
``source-config.json`` under ``ROOT_DIR`` when set. We merge: file (if present) then target config
overrides. Docs: https://docs.hotglue.com/transformation/writing-a-basic-script#configuration-files
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from target_oracle_fusion.const import (
    DEFAULT_ESS_ERROR_LOG_S3_REGION,
    HOTGLUE_ENV_FLOW,
    HOTGLUE_ENV_JOB_ID,
    HOTGLUE_ENV_TENANT,
    SOURCE_CONFIG_FILENAME,
    SOURCE_CONFIG_KEY_AWS_ACCESS_KEY_ID,
    SOURCE_CONFIG_KEY_AWS_SECRET_ACCESS_KEY,
    SOURCE_CONFIG_KEY_AWS_REGION,
    SOURCE_CONFIG_KEY_BUCKET,
    SOURCE_CONFIG_KEY_OUTPUT_PATH_PREFIX,
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


def load_source_config(config_path: Optional[Path] = None) -> dict[str, Any]:
    """Load ``source-config.json`` from job root per Hotglue: ``{ROOT_DIR}/source-config.json`` (default ``.``)."""
    if config_path is not None:
        path = Path(config_path).resolve()
    else:
        # Hotglue: ROOT_DIR is the job workspace; config files sit next to catalog.json (see docs).
        root = Path(os.environ.get("ROOT_DIR", ".")).expanduser()
        path = (root / SOURCE_CONFIG_FILENAME).resolve()

    logger.debug(
        "ESS error log S3: source-config path=%s exists=%s ROOT_DIR=%r cwd=%s",
        path,
        path.is_file(),
        os.environ.get("ROOT_DIR"),
        os.getcwd(),
    )

    if not path.is_file():
        logger.debug("ESS error log S3: no source config at %s", path)
        return {}

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("ESS error log S3: could not read %s: %s", path, e)
        return {}

    # Full body at DEBUG only — file may contain secrets; enable DEBUG briefly when troubleshooting.
    logger.debug("ESS error log S3: source-config full raw from %s:\n%s", path, raw_text)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.warning("ESS error log S3: invalid JSON in %s: %s", path, e)
        return {}

    if not isinstance(data, dict):
        logger.info("ESS error log S3: source-config JSON root is not an object at %s", path)
        logger.debug("ESS error log S3: source-config raw was:\n%s", raw_text)
        return {}

    logger.debug(
        "ESS error log S3: parsed %s (aws_id=%s aws_secret=%s bucket=%s output_path_prefix=%s)",
        path,
        bool(_str_from_cfg(data, SOURCE_CONFIG_KEY_AWS_ACCESS_KEY_ID)),
        bool(_str_from_cfg(data, SOURCE_CONFIG_KEY_AWS_SECRET_ACCESS_KEY)),
        bool(_str_from_cfg(data, SOURCE_CONFIG_KEY_BUCKET)),
        bool(_str_from_cfg(data, SOURCE_CONFIG_KEY_OUTPUT_PATH_PREFIX)),
    )
    logger.info("ESS error log S3: loaded source-config from %s (%d top-level keys)", path, len(data))
    return data


def merged_s3_config(pipeline_config: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """``source-config.json`` (if any) merged with Singer/Hotglue target ``config`` (pipeline wins on overlap)."""
    base = load_source_config()
    merged: dict[str, Any] = {**base, **dict(pipeline_config or {})}
    logger.info(
        "ESS error log S3: merged config keys=%d (from_file=%d pipeline_overlay=%s)",
        len(merged),
        len(base),
        bool(pipeline_config),
    )
    return merged


def format_output_path_prefix(template: str) -> str:
    """Fill ``{tenant}``, ``{flow_id}``, ``{job_id}`` from Hotglue env; empty if env incomplete or template invalid."""
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
    try:
        out = template.strip().format(tenant=tenant, flow_id=flow_id, job_id=job_id)
    except (KeyError, ValueError) as e:
        logger.warning("ESS error log S3: bad output_path_prefix template: %s", e)
        return ""
    return out


def s3_upload_configured(cfg: Mapping[str, Any]) -> bool:
    """True when ``source-config`` has S3 fields, Hotglue env is set, and ``output_path_prefix`` formats to a path."""
    has_id = bool(_str_from_cfg(cfg, SOURCE_CONFIG_KEY_AWS_ACCESS_KEY_ID))
    has_secret = bool(_str_from_cfg(cfg, SOURCE_CONFIG_KEY_AWS_SECRET_ACCESS_KEY))
    has_bucket = bool(_str_from_cfg(cfg, SOURCE_CONFIG_KEY_BUCKET))
    has_tmpl = bool(_str_from_cfg(cfg, SOURCE_CONFIG_KEY_OUTPUT_PATH_PREFIX))
    has_tenant = bool(_env(HOTGLUE_ENV_TENANT))
    has_flow = bool(_env(HOTGLUE_ENV_FLOW))
    has_job = bool(_env(HOTGLUE_ENV_JOB_ID))
    if not (has_id and has_secret and has_bucket and has_tmpl and has_tenant and has_flow and has_job):
        logger.debug(
            "ESS error log S3: not configured (cfg aws_id=%s aws_secret=%s bucket=%s prefix_tmpl=%s "
            "env tenant=%s flow=%s job=%s)",
            has_id,
            has_secret,
            has_bucket,
            has_tmpl,
            has_tenant,
            has_flow,
            has_job,
        )
        return False
    formatted = format_output_path_prefix(_str_from_cfg(cfg, SOURCE_CONFIG_KEY_OUTPUT_PATH_PREFIX))
    if not formatted:
        logger.debug("ESS error log S3: not configured (formatted prefix empty after template)")
        return False
    logger.debug("ESS error log S3: configured formatted_prefix=%s", formatted)
    return True


def build_s3_object_key(prefix: str, filename: str) -> str:
    """Build S3 key ``{prefix}/{filename}`` (or bare ``filename``); ``filename`` is the object basename."""
    p = prefix.strip().strip("/")
    fn = Path(filename).name.strip()
    if not fn:
        return p
    return f"{p}/{fn}" if p else fn


def resolve_error_log_s3_key(cfg: Mapping[str, Any], local_filename: str) -> str:
    """S3 object key: formatted ``output_path_prefix`` + original log basename (``local_filename``)."""
    prefix = format_output_path_prefix(_str_from_cfg(cfg, SOURCE_CONFIG_KEY_OUTPUT_PATH_PREFIX))
    key = build_s3_object_key(prefix, local_filename)
    logger.debug("ESS error log S3: resolved key=%s (local_filename=%s)", key, local_filename)
    return key


def upload_ess_error_log_txt(
    local_txt_path: Path,
    request_id: str,
    *,
    source_config: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Upload ``local_txt_path`` to S3 using merged file + optional target ``source_config``; return ``s3://…`` or ``None``."""
    cfg = merged_s3_config(source_config)
    logger.debug(
        "ESS error log S3: upload start request_id=%s pipeline_overlay=%s merged_keys=%d",
        request_id,
        source_config is not None,
        len(cfg),
    )
    if not s3_upload_configured(cfg):
        logger.info(
            "ESS error log S3 skipped: add aws_access_key_id, aws_secret_access_key, bucket, output_path_prefix "
            "to Hotglue target config (or source-config.json) and set env TENANT, FLOW, JOB_ID (merged keys=%d)",
            len(cfg),
        )
        logger.debug("ESS error log S3 upload skipped (source-config or Hotglue env incomplete)")
        return None

    access = _str_from_cfg(cfg, SOURCE_CONFIG_KEY_AWS_ACCESS_KEY_ID)
    secret = _str_from_cfg(cfg, SOURCE_CONFIG_KEY_AWS_SECRET_ACCESS_KEY)
    bucket = _str_from_cfg(cfg, SOURCE_CONFIG_KEY_BUCKET)
    region = _str_from_cfg(cfg, SOURCE_CONFIG_KEY_AWS_REGION) or DEFAULT_ESS_ERROR_LOG_S3_REGION

    path = Path(local_txt_path)
    if not path.is_file():
        logger.warning("ESS error log S3 upload skipped: not a file: %s", path)
        return None

    key = resolve_error_log_s3_key(cfg, path.name)
    logger.info(
        "ESS error log S3 uploading bucket=%s key=%s region=%s file=%s",
        bucket,
        key,
        region,
        path.name,
    )
    logger.debug(
        "ESS error log S3: upload_file bucket=%s key=%s region=%s local_path=%s size=%s",
        bucket,
        key,
        region,
        path.resolve(),
        path.stat().st_size,
    )

    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError:
        logger.info(
            "ESS error log S3 skipped: boto3 not installed; install target-oracle-fusion[s3] in the target env",
        )
        return None

    try:
        client = boto3.client(
            "s3",
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            region_name=region,
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
