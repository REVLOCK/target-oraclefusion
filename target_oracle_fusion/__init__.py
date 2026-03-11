"""Target for Oracle-Fusion.

Transforms RevRec journal entries CSV to Oracle Fusion GL format and zips output.
Follows the same approach as target-intacct: main() -> upload() -> load_journal_entries().
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import zipfile
from pathlib import Path

import singer

from target_oracle_fusion.const import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    INPUT_FILENAME,
    OUTPUT_FILENAME,
    REQUIRED_CONFIG_KEYS,
    ZIP_FILENAME_PREFIX,
)
from target_oracle_fusion.exceptions import ConfigError, OutputError, UploadError
from target_oracle_fusion.client import poll_ess_job_status, upload_zip
from target_oracle_fusion.transformer import transform_csv, TransformResult

logger = singer.get_logger()


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Transform RevRec journal entries CSV to Oracle Fusion format and zip."
    )
    parser.add_argument(
        "--config",
        "-c",
        required=True,
        help="Path to JSON config file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()


def _load_config(config_path: str) -> dict:
    """Load config from JSON file."""
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config: {e}") from e


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
        logger.exception("Failed to create zip file: %s", zip_path)
        raise OutputError(f"Failed to create zip: {e}") from e


def _write_target_state(result: TransformResult, output_dir: Path) -> Path:
    """Write target-state.json (Hotglue compatible) with summary and errors."""
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
    """
    Load journal entries from input CSV, transform to Oracle Fusion format, write output.

    Args:
        config: Config dict with input_path, output_path, ledger_id, etc.
        include_header: If True, write column headers. Default False (data rows only).
        fail_on_validation_error: If True, raise on first validation error. Default True.

    Returns:
        TransformResult with success/fail counts and error details.
    """
    input_path = Path(config["input_path"]) / INPUT_FILENAME
    output_path = Path(config["output_path"])

    if output_path.suffix.lower() == ".csv":
        output_csv = output_path
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_path.mkdir(parents=True, exist_ok=True)
        output_csv = output_path / f"{OUTPUT_FILENAME}.csv"
        output_dir = output_path

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
    base_url = config.get("base_url", "").rstrip("/")
    poll_interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    max_wait = config.get("max_wait_seconds")

    poll_ess_job_status(
        base_url,
        reqst_id,
        config,
        poll_interval_seconds=poll_interval,
        max_wait_seconds=max_wait,
    )
    logger.info("Oracle Fusion ESS job completed successfully.")


def upload(config: dict) -> TransformResult:
    """
    Transform input CSV to Oracle Fusion format, zip, and upload to Oracle.

    Args:
        config: Config dict with input_path, output_path, base_url, jwt_*, etc.

    Returns:
        TransformResult with success/fail counts.
    """
    logger.info("Starting upload.")

    result = load_journal_entries(
        config,
        include_header=False,
        fail_on_validation_error=True,
    )

    zip_path = _zip_output(result.output_path)
    result.output_path.unlink()
    logger.info("Removed intermediate CSV: %s", result.output_path)

    if config.get("base_url"):
        _upload_to_oracle_fusion(zip_path, config)
        zip_path.unlink()
        logger.info("Removed zip after upload: %s", zip_path)
    else:
        logger.warning("Skipping Oracle upload: base_url not in config.")

    if result.fail_count > 0:
        logger.warning("Upload completed with %d failed rows. See target-state.json for details.", result.fail_count)
    else:
        logger.info("Upload completed successfully (%d rows).", result.success_count)

    return result


@singer.utils.handle_top_exception(logger)
def main() -> None:
    """
    Main entry point. Parses config and runs upload.
    """
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    config = _load_config(args.config)

    missing = [k for k in REQUIRED_CONFIG_KEYS if not config.get(k)]
    if missing:
        raise ConfigError(f"Config missing required keys: {missing}")

    upload(config)


if __name__ == "__main__":
    main()
