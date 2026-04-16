"""ESS error log .txt → S3 from merged config + Hotglue env; ``pip install 'target-oracle-fusion[s3]'``.

Singer **targets** get settings from the flattened target ``config`` (Hotglue). ``source-config.json``
normally lives at the **job / flow workspace root** next to ``catalog.json``; the export process often
has ``cwd`` under ``targets/<connector>/`` with ``ROOT_DIR`` unset, so we also search upward from cwd
and honor ``ESS_SOURCE_CONFIG_PATH``. We merge: file (if present) then target config overrides.
Docs: https://docs.hotglue.com/transformation/writing-a-basic-script#configuration-files
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from target_oracle_fusion.const import (
    DEFAULT_ESS_ERROR_LOG_S3_REGION,
    ENV_ESS_PRINT_SOURCE_CONFIG_FULL,
    ENV_ESS_SOURCE_CONFIG_PATH,
    HOTGLUE_ENV_FLOW,
    HOTGLUE_ENV_JOB_ID,
    HOTGLUE_ENV_TENANT,
    SOURCE_CONFIG_FILENAME,
    SOURCE_CONFIG_PARENT_WALK_MAX,
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


def _truthy_env(name: str) -> bool:
    v = os.environ.get(name)
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _str_from_cfg(cfg: Mapping[str, Any], key: str) -> str:
    v = cfg.get(key)
    if v is None:
        return ""
    return str(v).strip()


def _source_config_candidate_paths(config_path: Optional[Path] = None) -> list[Path]:
    """Ordered search locations for ``source-config.json`` (first existing file wins)."""
    if config_path is not None:
        return [Path(config_path).resolve()]

    out: list[Path] = []
    seen: set[str] = set()

    def _push(p: Path) -> None:
        r = p.resolve()
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)

    explicit = _env(ENV_ESS_SOURCE_CONFIG_PATH)
    if explicit:
        _push(Path(explicit).expanduser())

    raw_root = os.environ.get("ROOT_DIR")
    if raw_root is not None and str(raw_root).strip():
        _push(Path(raw_root).expanduser() / SOURCE_CONFIG_FILENAME)

    cw = Path.cwd().resolve()
    _push(cw / SOURCE_CONFIG_FILENAME)

    d = cw
    for _ in range(SOURCE_CONFIG_PARENT_WALK_MAX):
        parent = d.parent
        if parent == d:
            break
        d = parent
        _push(d / SOURCE_CONFIG_FILENAME)

    return out


def load_source_config(config_path: Optional[Path] = None) -> dict[str, Any]:
    """Load ``source-config.json`` from Hotglue job root, optional env path, or by walking up from cwd."""
    candidates = _source_config_candidate_paths(config_path)
    path: Optional[Path] = None
    for cand in candidates:
        if cand.is_file():
            path = cand
            break

    logger.debug(
        "ESS error log S3: source-config chosen=%s exists=%s ROOT_DIR=%r cwd=%s tried_first=%s",
        path,
        path is not None,
        os.environ.get("ROOT_DIR"),
        os.getcwd(),
        candidates[0] if candidates else None,
    )

    if path is None:
        if _truthy_env(ENV_ESS_PRINT_SOURCE_CONFIG_FULL):
            sample = ", ".join(str(p) for p in candidates[:5])
            more = f" (+{len(candidates) - 5} more)" if len(candidates) > 5 else ""
            logger.warning(
                "ESS error log S3: env %s is set but no file found (candidates: %s%s)",
                ENV_ESS_PRINT_SOURCE_CONFIG_FULL,
                sample,
                more,
            )
        logger.debug(
            "ESS error log S3: no source config (candidates n=%d: %s)",
            len(candidates),
            [str(p) for p in candidates],
        )
        return {}

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("ESS error log S3: could not read %s: %s", path, e)
        return {}

    if _truthy_env(ENV_ESS_PRINT_SOURCE_CONFIG_FULL):
        logger.warning(
            "ESS error log S3: full %s body (env %s=1; may contain secrets):\n%s",
            path.name,
            ENV_ESS_PRINT_SOURCE_CONFIG_FULL,
            raw_text,
        )

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
    """Fill ``{tenant}``, ``{flow_id}``, ``{job_id}`` from **process env only** (``TENANT``, ``FLOW``, ``JOB_ID``).

    These are never read from ``source-config.json`` or merged target config—only ``os.environ``, as Hotglue sets for jobs.
    Returns empty if any of those env vars is missing or the template is invalid.
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
    try:
        out = template.strip().format(tenant=tenant, flow_id=flow_id, job_id=job_id)
    except (KeyError, ValueError) as e:
        logger.warning("ESS error log S3: bad output_path_prefix template: %s", e)
        return ""
    return out


def s3_config_gaps(cfg: Mapping[str, Any]) -> list[str]:
    """Human-readable missing pieces for logs (no secret values)."""
    gaps: list[str] = []
    if not _str_from_cfg(cfg, SOURCE_CONFIG_KEY_AWS_ACCESS_KEY_ID):
        gaps.append("config.aws_access_key_id")
    if not _str_from_cfg(cfg, SOURCE_CONFIG_KEY_AWS_SECRET_ACCESS_KEY):
        gaps.append("config.aws_secret_access_key")
    if not _str_from_cfg(cfg, SOURCE_CONFIG_KEY_BUCKET):
        gaps.append("config.bucket")
    if not _str_from_cfg(cfg, SOURCE_CONFIG_KEY_OUTPUT_PATH_PREFIX):
        gaps.append("config.output_path_prefix")
    if not _env(HOTGLUE_ENV_TENANT):
        gaps.append(f"env.{HOTGLUE_ENV_TENANT}")
    if not _env(HOTGLUE_ENV_FLOW):
        gaps.append(f"env.{HOTGLUE_ENV_FLOW}")
    if not _env(HOTGLUE_ENV_JOB_ID):
        gaps.append(f"env.{HOTGLUE_ENV_JOB_ID}")
    if gaps:
        return gaps
    formatted = format_output_path_prefix(_str_from_cfg(cfg, SOURCE_CONFIG_KEY_OUTPUT_PATH_PREFIX))
    if not formatted:
        gaps.append(
            "output_path_prefix_expanded_empty "
            "(use {tenant}/{flow_id}/{job_id} in template; env TENANT, FLOW, JOB_ID must be set)"
        )
    return gaps


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
        gap_txt = "; ".join(s3_config_gaps(cfg))
        logger.info(
            "ESS error log S3 skipped (merged keys=%d): %s",
            len(cfg),
            gap_txt or "unknown gap (see DEBUG)",
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
