"""Transform RevRec journal entries CSV to Oracle Fusion GL format."""

from __future__ import annotations

import csv
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from target_oracle_fusion.const import (
    DEFAULT_LEDGER_ID,
    DEFAULT_LEDGER_NAME,
    DEFAULT_USER_JE_CATEGORY_NAME,
    DEFAULT_USER_JE_SOURCE_NAME,
    INPUT_FILENAME,
    ORACLE_OUTPUT_COLUMNS,
    REQUIRED_INPUT_COLUMNS,
)

logger = logging.getLogger(__name__)


@dataclass
class TransformResult:
    """Result of transform with success/fail counts and error details."""

    output_path: Path
    success_count: int = 0
    fail_count: int = 0
    warning_count: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Export as target-state style dict (Hotglue compatible)."""
        return {
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
    """Convert Transaction Date (YYYY-MM-DD) to Oracle format DD/MM/YY."""
    if value is None or value == "":
        return ""
    s = str(value).strip()
    if not s:
        return ""
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%d/%m/%y")
    except (ValueError, TypeError):
        return s


def _format_date_created() -> str:
    """Return current date in DD/MM/YY format."""
    return datetime.now().strftime("%d/%m/%y")


def _generate_group_id() -> str:
    """Generate a unique 16-digit ID for journal batch."""
    return str(uuid.uuid4().int % (10**16)).zfill(16)


def _safe_str(value: Any, default: str = "") -> str:
    """Convert value to string, handling None and empty."""
    if value is None or value == "":
        return default
    s = str(value).strip()
    if s.lower() in ("nan", "none"):
        return default
    return s


def _validate_row(row: dict[str, Any], row_num: int, je_id: str) -> tuple[list[str], list[str]]:
    """Validate row. Returns (errors, warnings). Errors are critical; warnings are non-blocking."""
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
    """Build Oracle GL row with all columns set to empty string."""
    return dict.fromkeys(ORACLE_OUTPUT_COLUMNS, "")


def transform_row(
    row: dict[str, Any],
    config: dict[str, Any],
    group_id: str,
) -> dict[str, str]:
    """Transform a single input row to Oracle Fusion output format."""
    out = _build_empty_oracle_row()

    # Fixed Oracle defaults
    out["STATUS"] = "NEW"
    out["ACTUAL_FLAG"] = "A"
    out["SEGMENT7"] = out["SEGMENT8"] = "0"
    out["CREATION_DATE"] = "END"

    # Config-driven values
    out["LEDGER_ID"] = config.get("ledger_id", DEFAULT_LEDGER_ID)
    out["USER_JE_SOURCE_NAME"] = config.get("user_je_source_name", DEFAULT_USER_JE_SOURCE_NAME)
    out["USER_JE_CATEGORY_NAME"] = config.get("user_je_category_name", DEFAULT_USER_JE_CATEGORY_NAME)
    out["LEDGER_NAME"] = config.get("ledger_name", DEFAULT_LEDGER_NAME)

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
    out["ACCOUNTING_DATE"] = _format_accounting_date(row.get("Transaction Date"))
    out["CURRENCY_CODE"] = _safe_str(row.get("Currency", "USD"))
    out["DATE_CREATED"] = _format_date_created()
    out["SEGMENT2"] = _safe_str(row.get("Location", ""), "0")
    out["SEGMENT3"] = _safe_str(row.get("Department", ""), "0")
    out["SEGMENT4"] = _safe_str(row.get("Account Number", ""))
    out["SEGMENT5"] = _safe_str(row.get("Discord Channel", ""), "0")
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
    """
    Transform input CSV to Oracle Fusion format and write to output_path.

    Args:
        input_path: Path to input CSV (or directory containing JournalEntries.csv).
        output_path: Path for output CSV file.
        config: Optional config with ledger_id, user_je_source_name, etc.
        include_header: If True, write column headers. Default False (data rows only).
        fail_on_validation_error: If True, raise on first critical error. Default True.

    Returns:
        TransformResult with success/fail counts and error details.
    """
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

    group_ids: dict[str, str] = {}
    result = TransformResult(output_path=output_path)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
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

            if je_id not in group_ids:
                group_ids[je_id] = _generate_group_id()

            try:
                out_row = transform_row(row, config, group_ids[je_id])
                writer.writerow(out_row)
                result.success_count += 1
            except Exception as e:
                result.fail_count += 1
                err_msg = f"Row {row_num}: Transform failed - {e}"
                result.errors.append({"row": row_num, "journal_entry_id": je_id, "message": err_msg})
                logger.exception(err_msg)
                if fail_on_validation_error:
                    raise TransformError(err_msg, response=e) from e

    logger.info(
        "Transformed %d rows from %s to %s (success=%d, fail=%d, warnings=%d)",
        len(rows),
        input_file,
        output_path,
        result.success_count,
        result.fail_count,
        result.warning_count,
    )
    result.warning_count = len(result.warnings)
    return result
