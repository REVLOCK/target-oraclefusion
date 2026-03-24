"""Oracle Fusion target: CSV → GL format, zip, optional REST upload and ESS polling."""

from __future__ import annotations

import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict

import singer

from target_oracle_fusion.const import (
    DEFAULT_MAX_WAIT_SECONDS,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_POLL_INTERVAL_SECONDS,
    INPUT_FILENAME,
    OUTPUT_FILENAME,
    REQUIRED_CONFIG_KEYS,
    REQUIRED_FLATTENED_CONFIG_KEYS,
    ZIP_FILENAME_PREFIX,
)
from target_oracle_fusion import auth
from target_oracle_fusion.exceptions import ConfigError, OutputError, UploadError
from target_oracle_fusion.client import poll_ess_job_status, upload_zip
from target_oracle_fusion.transformer import transform_csv, TransformResult

logger = singer.get_logger()


def _safe_unlink(path: Path, *, label: str = "file") -> None:
    """Remove a file if it exists; log failures without raising."""
    try:
        path.unlink()
        logger.info("Removed %s: %s", label, path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not remove %s %s: %s", label, path, e)


def _empty_output_workspace() -> None:
    """Remove all contents of ``DEFAULT_OUTPUT_PATH``; keep the directory."""
    root = Path(DEFAULT_OUTPUT_PATH)
    if not root.is_dir():
        return
    children = list(root.iterdir())
    if not children:
        return
    for child in children:
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError as e:
            logger.warning("Could not remove workspace path %s: %s", child, e)
    logger.info("Cleaned workspace directory: %s", root.resolve())


def flatten_config(config: Any) -> Dict[str, Any]:
    """Merge ``custom_fields`` name/value pairs, then top-level keys (top-level wins)."""
    if not isinstance(config, dict):
        raise ConfigError("config must be a JSON object")

    out: Dict[str, Any] = {}

    custom = config.get("custom_fields")
    if isinstance(custom, list):
        for item in custom:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name is None or name == "":
                continue
            out[str(name)] = item.get("value")

    for key, value in config.items():
        if key == "custom_fields":
            continue
        out[key] = value

    return out


def require_flattened_config(config: dict) -> None:
    """Raise ``ConfigError`` if any required key is missing or empty after ``flatten_config``."""
    missing: list[str] = []
    for key in REQUIRED_FLATTENED_CONFIG_KEYS:
        val = config.get(key)
        if val is None:
            missing.append(key)
        elif isinstance(val, str) and not val.strip():
            missing.append(key)
    if missing:
        raise ConfigError(
            "Missing or empty required config (after custom_fields merge): "
            + ", ".join(sorted(missing))
        )


def _zip_output(csv_path: Path, zip_path: Path | None = None) -> Path:
    """Zip the output CSV file. Name: Glinterface_chargebee_<unique_id>.zip"""
    if zip_path is None:
        unique_id = int(time.time() * 1000)
        zip_name = f"{ZIP_FILENAME_PREFIX}_{unique_id}.zip"
        zip_path = csv_path.parent / zip_name
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(csv_path, arcname=csv_path.name)
        logger.info("Created zip file: %s", zip_path)
        return zip_path
    except (OSError, zipfile.BadZipFile) as e:
        _safe_unlink(zip_path, label="partial zip")
        logger.exception("Failed to create zip file: %s", zip_path)
        raise OutputError(f"Failed to create zip: {e}") from e


def _write_target_state(result: TransformResult, output_dir: Path) -> Path:
    """Write target-state.json with summary and errors."""
    state_path = output_dir / "target-state.json"
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2)
        logger.info("Wrote target-state.json to %s", state_path)
        return state_path
    except OSError as e:
        logger.warning("Could not write target-state.json: %s", e)
        return state_path


def load_journal_entries(
    config: dict,
    *,
    include_header: bool = False,
    fail_on_validation_error: bool = True,
) -> TransformResult:
    input_path = Path(config["input_path"]) / INPUT_FILENAME
    output_dir = Path(DEFAULT_OUTPUT_PATH)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv = output_dir / f"{OUTPUT_FILENAME}.csv"

    result = transform_csv(
        input_path,
        output_csv,
        config=config,
        include_header=include_header,
        fail_on_validation_error=fail_on_validation_error,
    )

    if result.errors or result.warnings:
        _write_target_state(result, output_dir)

    return result


def _upload_to_oracle_fusion(zip_path: Path, config: dict) -> None:
    """Upload zip to Oracle Fusion and poll ESS job status until complete."""
    reqst_id = upload_zip(zip_path, config)
    base_url = auth.normalize_base_url(config.get("base_url", ""))

    poll_ess_job_status(
        base_url,
        reqst_id,
        config,
        poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
        max_wait_seconds=DEFAULT_MAX_WAIT_SECONDS,
    )
    logger.info("Oracle Fusion ESS job completed successfully.")


def upload(config: dict) -> TransformResult:
    logger.info("Starting upload.")

    config = flatten_config(config)
    require_flattened_config(config)

    Path(DEFAULT_OUTPUT_PATH).mkdir(parents=True, exist_ok=True)
    _empty_output_workspace()

    try:
        result = load_journal_entries(
            config,
            include_header=False,
            fail_on_validation_error=True,
        )

        zip_path = _zip_output(result.output_path)
        _safe_unlink(result.output_path, label="intermediate CSV")

        _upload_to_oracle_fusion(zip_path, config)

        if result.fail_count > 0:
            logger.warning(
                "Upload finished with %d failed rows (details were logged; workspace will be cleared).",
                result.fail_count,
            )
        else:
            logger.info("Upload completed successfully (%d rows).", result.success_count)

        return result
    finally:
        _empty_output_workspace()


@singer.utils.handle_top_exception(logger)
def main() -> None:
    args = singer.utils.parse_args(REQUIRED_CONFIG_KEYS)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    upload(args.config)


if __name__ == "__main__":
    main()
