"""Target exception types."""

from __future__ import annotations


class TargetOracleFusionError(Exception):
    """Base error with optional response payload."""

    def __init__(self, msg: str, response: object = None) -> None:
        super().__init__(msg)
        self.message = msg
        self.response = response

    def __str__(self) -> str:
        return repr(self.message)


class ConfigError(TargetOracleFusionError):
    """Bad or incomplete config."""


class InputError(TargetOracleFusionError):
    """Missing or unusable input file or columns."""


class ValidationError(TargetOracleFusionError):
    """Row or field validation failed."""


class TransformError(TargetOracleFusionError):
    """Row transform failed."""


class OutputError(TargetOracleFusionError):
    """Could not write output artifact."""


class UploadError(TargetOracleFusionError):
    """API upload or job status failure."""
