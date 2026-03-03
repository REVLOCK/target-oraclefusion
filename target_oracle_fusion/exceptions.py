"""Oracle Fusion target exceptions - per Hotglue/target-intacct patterns."""

from __future__ import annotations


class TargetOracleFusionError(Exception):
    """Base exception for target-oracle-fusion."""

    def __init__(self, msg: str, response: object = None) -> None:
        super().__init__(msg)
        self.message = msg
        self.response = response

    def __str__(self) -> str:
        return repr(self.message)


class ConfigError(TargetOracleFusionError):
    """Invalid or missing config (e.g. missing required keys, bad JSON)."""


class InputError(TargetOracleFusionError):
    """Invalid input (e.g. file not found, missing columns, empty data)."""


class ValidationError(TargetOracleFusionError):
    """Data validation failed (e.g. invalid row, missing required field)."""


class TransformError(TargetOracleFusionError):
    """Transformation failed (e.g. date parse error, type conversion)."""


class OutputError(TargetOracleFusionError):
    """Output write failed (e.g. permission denied, disk full)."""


class UploadError(TargetOracleFusionError):
    """Oracle Fusion API upload or ESS job failed."""
