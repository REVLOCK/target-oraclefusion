"""Tests for target-oracle-fusion CSV transform flow."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from target_oracle_fusion import flatten_config, require_flattened_config
from target_oracle_fusion.exceptions import ConfigError
from target_oracle_fusion.ess_report import build_ess_report_soap_body, _extract_error_from_oracle_report
from target_oracle_fusion.transformer import transform_csv


def test_transform_csv_success() -> None:
    """Test transform_csv with valid input."""
    with tempfile.TemporaryDirectory() as tmp:
        input_csv = Path(tmp) / "input.csv"
        input_csv.write_text(
            "Transaction Date,Journal Entry Id,Account Number,Account Name,Description,Amount,Posting Type,Currency,Department,Location,Discord Channel\n"
            "2025-12-31,JE-001,120015,Unbilled Receivable,Test,100.50,Debit,USD,,,\n"
            "2025-12-31,JE-001,230010,Deferred Revenue,Test,100.50,Credit,USD,,,\n",
            encoding="utf-8",
        )
        output_csv = Path(tmp) / "output.csv"
        config = {"ledger_id": "123", "source_name": "Test", "category_name": "Manual"}

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


def test_flatten_config_custom_fields() -> None:
    """custom_fields merged; source_name and category_name preserved."""
    raw = {
        "input_path": ".",
        "source_name": "Src",
        "category_name": "Cat",
        "private_key": "x",
        "custom_fields": [
            {"name": "ledger_id", "value": "999"},
            {"name": "jwt_issuer", "value": "iss"},
            {"name": "jwt_principal", "value": "prn"},
        ],
    }
    flat = flatten_config(raw)
    assert flat["ledger_id"] == "999"
    assert flat["jwt_issuer"] == "iss"
    assert flat["jwt_principal"] == "prn"
    assert flat["source_name"] == "Src"
    assert flat["category_name"] == "Cat"
    assert "custom_fields" not in flat


def test_flatten_config_top_level_overrides_custom_fields() -> None:
    """Top-level keys win over custom_fields with the same name."""
    flat = flatten_config(
        {
            "custom_fields": [{"name": "ledger_id", "value": "111"}],
            "ledger_id": "222",
        }
    )
    assert flat["ledger_id"] == "222"


def _minimal_valid_flat_config() -> dict:
    return {
        "input_path": ".",
        "source_name": "S",
        "category_name": "C",
        "base_url": "https://example.fa.ocs.oraclecloud.com",
        "private_key": "k",
        "ledger_id": "1",
        "ledger_name": "L",
        "segment_1": "110",
        "segment_6": "000",
        "parameter_list": "a,b,c",
        "jwt_issuer": "iss",
        "jwt_principal": "p",
        "jwt_x5t": "x",
    }


def test_require_flattened_config_accepts_full_config() -> None:
    require_flattened_config(_minimal_valid_flat_config())


def test_require_flattened_config_rejects_missing_keys() -> None:
    cfg = _minimal_valid_flat_config()
    del cfg["jwt_x5t"]
    with pytest.raises(ConfigError, match="jwt_x5t"):
        require_flattened_config(cfg)


def test_require_flattened_config_rejects_blank_string() -> None:
    cfg = _minimal_valid_flat_config()
    cfg["ledger_id"] = "   "
    with pytest.raises(ConfigError, match="ledger_id"):
        require_flattened_config(cfg)


def test_build_ess_report_soap_body_escapes_interpolated_values() -> None:
    body = build_ess_report_soap_body("1&2<3", "/path/to&Rpt.xdo")
    assert "<pub:item>1&amp;2&lt;3</pub:item>" in body
    assert "<pub:reportAbsolutePath>/path/to&amp;Rpt.xdo</pub:reportAbsolutePath>" in body


def test_extract_error_from_unbalanced_journal_eu02() -> None:
    """EU02 lives under Unbalanced Journal Entries; Error Lines block can be empty."""
    sample = """
=================================================   Unbalanced Journal Entries**   =================================================

Error                                                                            Total
Code  Journal Entry Name                    Batch Name                           Lines Period Name    Total Debits    Total Credits
----- ------------------------------------ ------------------------------------ ----- ----------- ---------------- ----------------
EU02  202512 Unbilled Receivable Reclass R 202512 Unbilled Receivable Reclass C     2 Mar-26            103,900.00       103,910.00

=========================================================   Error Lines   ==========================================================

Unbalanced Journal Error Codes
------------------------------
EU02   The journal entry is unbalanced and suspense posting isn't allowed in the ledger.
"""
    msg = _extract_error_from_oracle_report(sample, "4360991")
    assert msg is not None
    assert "EU02" in msg
    assert "unbalanced" in msg.lower()
    assert "4360991" in msg
