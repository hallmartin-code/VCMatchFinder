"""Exception hierarchy.

Every error raised to the UI or CLI carries a message a user can act on. Callers catch
``MatchingEngineError`` and show ``str(exc)``; nothing here should need a stack trace
to be understood.
"""

from __future__ import annotations


class MatchingEngineError(Exception):
    """Base class for all expected, user-facing failures."""


class IngestError(MatchingEngineError):
    """The deck could not be read (corrupt, encrypted, empty, unreadable)."""


class UnsupportedFormatError(IngestError):
    """The file is not a supported deck format."""


class UploadRejectedError(IngestError):
    """The upload failed a security check (size, type, archive structure)."""


class InvestorDataError(MatchingEngineError):
    """An investor dataset could not be loaded."""


class ConfigError(MatchingEngineError):
    """Configuration is invalid (e.g. weights that sum to zero)."""


class LLMUnavailableError(MatchingEngineError):
    """The external model is disabled, unconfigured, or failed."""


class ReportOverflowError(MatchingEngineError):
    """The report could not be fitted to one page even at the last degradation rung."""
