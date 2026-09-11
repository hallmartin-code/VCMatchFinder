"""Consent capture.

Four independent permissions. Granting one never implies another - in particular,
permission to analyze a deck is not permission to contact investors, and the
application never contacts investors under any consent state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.errors import ConsentError


def _now() -> datetime:
    return datetime.now(UTC)


class ConsentRecord(BaseModel):
    analysis_consent: bool = False
    data_storage_consent: bool = False
    outreach_consent: bool = False
    external_processing_consent: bool = Field(
        False, description="Permission to send company information to an external model/API."
    )
    timestamp: datetime = Field(default_factory=_now)
    source: str = Field("unspecified", description="Where consent was captured: streamlit_ui, cli, api, test")

    def require_analysis(self) -> None:
        if not self.analysis_consent:
            raise ConsentError(
                "Analysis consent is required before a deck can be analyzed. "
                "Confirm the analysis consent checkbox (or pass --consent-analysis)."
            )

    def require_storage(self) -> None:
        if not self.data_storage_consent:
            raise ConsentError(
                "Storage consent was not granted, so outputs cannot be written to disk. "
                "Download them from the session instead, or grant storage consent."
            )

    def for_record(self) -> dict[str, object]:
        return {
            "analysis_consent": self.analysis_consent,
            "data_storage_consent": self.data_storage_consent,
            "outreach_consent": self.outreach_consent,
            "external_processing_consent": self.external_processing_consent,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
        }
