"""Tests for target-oracle-fusion CSV transform flow."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from target_oracle_fusion.transformer import transform_csv


def test_transform_csv_success() -> None:
    """Test transform_csv with valid input."""
    with tempfile.TemporaryDirectory() as tmp:
        input_csv = Path(tmp) / "input.csv"
        input_csv.write_text(
            "Transaction Date,Journal Entry Id,Account Number,Account Name,Description,Amount,Posting Type,Currency\n"
            "2025-12-31,JE-001,120015,Unbilled Receivable,Test,100.50,Debit,USD\n"
            "2025-12-31,JE-001,230010,Deferred Revenue,Test,100.50,Credit,USD\n",
            encoding="utf-8",
        )
        output_csv = Path(tmp) / "output.csv"
        config = {"ledger_id": "123", "user_je_source_name": "Test", "user_je_category_name": "Manual"}

        result = transform_csv(input_csv, output_csv, config=config)

        assert result.success_count == 2
        assert result.fail_count == 0
        assert output_csv.exists()
        assert "STATUS" in output_csv.read_text() or output_csv.stat().st_size > 0


def test_transform_csv_missing_columns() -> None:
    """Test transform_csv raises on missing required columns."""
    from target_oracle_fusion.exceptions import InputError

    with tempfile.TemporaryDirectory() as tmp:
        input_csv = Path(tmp) / "bad.csv"
        input_csv.write_text("Col1,Col2\n1,2\n", encoding="utf-8")
        output_csv = Path(tmp) / "out.csv"

        with pytest.raises(InputError, match="missing required columns"):
            transform_csv(input_csv, output_csv)
