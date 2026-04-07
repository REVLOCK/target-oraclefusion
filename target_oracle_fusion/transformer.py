"""Journal CSV to GL interface CSV."""

from __future__ import annotations

import csv
import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from target_oracle_fusion.const import (
    INPUT_FILENAME,
    ORACLE_OUTPUT_COLUMNS,
    REQUIRED_INPUT_COLUMNS,
)

logger = logging.getLogger(__name__)


@dataclass
class TransformResult:
    """Transform outcome: paths, counts, batch id, errors, warnings."""

    output_path: Path
    batch_group_id: str = ""
    success_count: int = 0
    fail_count: int = 0
    warning_count: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Dict for target-state.json."""
        return {
            "batch_group_id": self.batch_group_id,
            "summary": {
                "JournalEntries": {
                    "success": self.success_count,
                    "fail": self.fail_count,
                    "existing": 0,
                    "updated": 0,
                }
            },
            "bookmarks": {"JournalEntries": []},
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _format_accounting_date(value: Any) -> str:
    """Input date → YYYY-MM-DD when parseable."""
    if value is None or value == "":
        return ""
    s = str(value).strip()
    if not s:
        return ""
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return s


def _format_date_created() -> str:
    """Today as DD/MM/YY."""
    return datetime.now().strftime("%d/%m/%y")


def _generate_group_id() -> str:
    """16-digit numeric GROUP_ID (time-based with random suffix)."""
    # Unix time as whole milliseconds since 1970-01-01 (typically 13 digits).
    milliseconds_since_epoch = int(time.time() * 1000)

    # Extra 0–999 so batches in the same millisecond get different IDs.
    random_suffix = secrets.randbelow(1000)

    # Pack time + suffix into one integer (still ≤ 16 digits for many years).
    packed = (milliseconds_since_epoch * 1000) + random_suffix

    sixteen_digits = str(packed)
    if len(sixteen_digits) > 16:
        sixteen_digits = sixteen_digits[-16:]

    return sixteen_digits.zfill(16)


def _str_from_config(config: dict[str, Any], key: str) -> str:
    """Stripped config value or empty."""
    v = config.get(key)
    if v is None:
        return ""
    return str(v).strip()


def _safe_str(value: Any, default: str = "") -> str:
    """Strip to string; None/empty/NaN → default."""
    if value is None or value == "":
        return default
    s = str(value).strip()
    if s.lower() in ("nan", "none"):
        return default
    return s


def _validate_row(row: dict[str, Any], row_num: int, je_id: str) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one row."""
    errors: list[str] = []
    warnings: list[str] = []

    # Critical: Account Number
    acct = row.get("Account Number")
    if acct is None or (isinstance(acct, str) and not acct.strip()):
        errors.append(f"Row {row_num}: Account Number is required (Journal Entry: {je_id})")

    # Critical: Posting Type
    posting_type = _safe_str(row.get("Posting Type", "")).upper()
    if posting_type and posting_type not in ("DEBIT", "CREDIT"):
        errors.append(
            f"Row {row_num}: Posting Type must be Debit or Credit, got '{posting_type}' "
            f"(Journal Entry: {je_id})"
        )

    # Critical: Amount
    amount = row.get("Amount")
    try:
        if amount not in (None, ""):
            float(amount)
    except (ValueError, TypeError):
        errors.append(f"Row {row_num}: Invalid Amount '{amount}' (Journal Entry: {je_id})")

    # Warning: Transaction Date format
    tx_date = row.get("Transaction Date")
    if tx_date and tx_date not in (None, ""):
        try:
            datetime.strptime(str(tx_date).strip(), "%Y-%m-%d")
        except (ValueError, TypeError):
            warnings.append(
                f"Row {row_num}: Transaction Date '{tx_date}' may not parse correctly "
                f"(expected YYYY-MM-DD)"
            )

    return errors, warnings


def _build_empty_oracle_row() -> dict[str, str]:
    """Empty GL row (all columns "")."""
    return dict.fromkeys(ORACLE_OUTPUT_COLUMNS, "")


def transform_row(
    row: dict[str, Any],
    config: dict[str, Any],
    group_id: str,
) -> dict[str, str]:
    """Map one CSV row to GL columns; group_id is shared for the whole file."""
    out = _build_empty_oracle_row()

    # Fixed Oracle defaults
    out["STATUS"] = "NEW"
    out["ACTUAL_FLAG"] = "A"
    out["CREATION_DATE"] = "END"

    # Config-driven values (no const fallbacks; omit in config → "")
    out["LEDGER_ID"] = _str_from_config(config, "LEDGER_ID")
    out["LEDGER_NAME"] = _str_from_config(config, "LEDGER_NAME")
    out["USER_JE_SOURCE_NAME"] = _str_from_config(config, "source_name")
    out["USER_JE_CATEGORY_NAME"] = _str_from_config(config, "category_name")

    # Amount and Debit/Credit
    posting_type = _safe_str(row.get("Posting Type", "")).upper()
    try:
        amount_val = float(row.get("Amount") or 0)
    except (ValueError, TypeError):
        amount_val = 0
    amount_str = str(round(amount_val, 2))
    out["ENTERED_DR"] = amount_str if posting_type == "DEBIT" else ""
    out["ENTERED_CR"] = amount_str if posting_type == "CREDIT" else ""

    # Input → Output mapping (populated fields only)
    description = _safe_str(row.get("Description", ""))
    entity_default = _str_from_config(config, "Entity")
    intercompany_default = _str_from_config(config, "Intercompany")

    out["ACCOUNTING_DATE"] = _format_accounting_date(row.get("Transaction Date"))
    out["CURRENCY_CODE"] = _safe_str(row.get("Currency", "USD"))
    out["DATE_CREATED"] = _format_date_created()

    acct = _safe_str(row.get("Account Number", ""))
    out["SEGMENT1"] = _safe_str(row.get("Entity", ""), entity_default)
    out["SEGMENT2"] = _safe_str(row.get("Location", ""))
    if acct == "420010":
        out["SEGMENT3"] = "1000"
    elif acct == "520010":
        out["SEGMENT3"] = "1600"
    else:
        out["SEGMENT3"] = "0000"
    out["SEGMENT4"] = acct
    out["SEGMENT5"] = _safe_str(row.get("Discord Channel", ""))
    out["SEGMENT6"] = _safe_str(row.get("Intercompany", ""), intercompany_default)
    out["SEGMENT7"] = _safe_str(row.get("Future1", "0"))
    out["SEGMENT8"] = _safe_str(row.get("Future2", "0"))

    out["REFERENCE1"] = out["REFERENCE2"] = out["REFERENCE3"] = out["REFERENCE4"] = out["REFERENCE5"] = description
    out["GROUP_ID"] = group_id

    return out


def transform_csv(
    input_path: str | Path,
    output_path: str | Path,
    config: dict[str, Any] | None = None,
    *,
    include_header: bool = False,
    fail_on_validation_error: bool = True,
) -> TransformResult:
    from target_oracle_fusion.exceptions import InputError, TransformError, ValidationError

    config = config or {}
    input_path = Path(input_path)
    output_path = Path(output_path)

    if input_path.is_dir():
        input_file = input_path / INPUT_FILENAME
    else:
        input_file = input_path

    if not input_file.exists():
        raise InputError(f"Input file not found: {input_file}")

    with open(input_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        if not rows:
            raise InputError("Input CSV has no data rows")
        cols = list(rows[0].keys())

    missing = [c for c in REQUIRED_INPUT_COLUMNS if c not in cols]
    if missing:
        raise InputError(f"Input CSV missing required columns: {missing}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        suffix=".csv",
        prefix=".glinterface_",
        dir=str(output_path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    wrote_output = False
    batch_group_id = _generate_group_id()
    logger.info("Batch GROUP_ID=%s", batch_group_id)
    result = TransformResult(output_path=output_path, batch_group_id=batch_group_id)

    try:
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ORACLE_OUTPUT_COLUMNS)
            if include_header:
                writer.writeheader()

            for row_num, row in enumerate(rows, start=2):
                je_id = _safe_str(row.get("Journal Entry Id", ""))
                errors, warnings = _validate_row(row, row_num, je_id)

                for w in warnings:
                    result.warnings.append({"row": row_num, "journal_entry_id": je_id, "message": w})
                    logger.warning(w)

                if errors:
                    for e in errors:
                        result.errors.append({"row": row_num, "journal_entry_id": je_id, "message": e})
                        logger.error(e)
                    result.fail_count += 1
                    if fail_on_validation_error:
                        raise ValidationError(
                            f"Validation failed at row {row_num}: {errors[0]}",
                            response={"errors": result.errors, "warnings": result.warnings},
                        )
                    continue

                try:
                    out_row = transform_row(row, config, batch_group_id)
                    writer.writerow(out_row)
                    result.success_count += 1
                except Exception as e:
                    result.fail_count += 1
                    err_msg = f"Row {row_num}: Transform failed - {e}"
                    result.errors.append({"row": row_num, "journal_entry_id": je_id, "message": err_msg})
                    logger.exception(err_msg)
                    if fail_on_validation_error:
                        raise TransformError(err_msg, response=e) from e

        try:
            if output_path.exists():
                output_path.unlink()
        except OSError as e:
            logger.warning("Could not remove prior output file %s: %s", output_path, e)
        os.replace(str(tmp_path), str(output_path))
        wrote_output = True

        logger.info(
            "Transform rows=%d ok=%d fail=%d warn=%d → %s",
            len(rows),
            result.success_count,
            result.fail_count,
            result.warning_count,
            output_path,
        )
        result.warning_count = len(result.warnings)
        return result
    finally:
        if not wrote_output and tmp_path.exists():
            try:
                tmp_path.unlink()
                logger.debug("Removed temp file: %s", tmp_path)
            except OSError as e:
                logger.warning("Could not remove temp %s: %s", tmp_path, e)
