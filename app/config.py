"""Application settings and matching configuration.

Two layers:

* ``Settings`` - deployment configuration from environment variables / ``.env``
  (API keys, data paths, upload limits). Secrets live only here, never in code.
* ``MatchingConfig`` - the analytical model (criterion weights, unknown-data policy,
  tier thresholds), loaded from ``config/matching.toml`` and overridable per run from
  the UI. Weights are validated and normalized so they always total 100.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
LOGO_PATH = ASSETS_DIR / "TEN_Capital_logo_footer.png"

#: Criterion key -> display label, in report order. The single source of truth for the
#: nine scoring criteria; scoring.py dispatches on these keys.
CRITERIA: dict[str, str] = {
    "stage": "Stage fit",
    "sector": "Sector/subsector fit",
    "check_size": "Check-size/round fit",
    "geography": "Geography fit",
    "business_model": "Business-model fit",
    "traction": "Traction fit",
    "historical_evidence": "Historical investment evidence",
    "strategic_value": "Strategic value",
    "lead_capability": "Lead/follow capability",
}


class MatchWeights(BaseModel):
    """Criterion weights. Any non-negative values; normalized to 100 when used."""

    stage: float = Field(15, ge=0)
    sector: float = Field(20, ge=0)
    check_size: float = Field(15, ge=0)
    geography: float = Field(10, ge=0)
    business_model: float = Field(10, ge=0)
    traction: float = Field(10, ge=0)
    historical_evidence: float = Field(10, ge=0)
    strategic_value: float = Field(5, ge=0)
    lead_capability: float = Field(5, ge=0)

    @model_validator(mode="after")
    def _positive_total(self) -> MatchWeights:
        if self.total <= 0:
            raise ConfigError("Criterion weights sum to zero; at least one weight must be positive.")
        return self

    @property
    def total(self) -> float:
        return sum(getattr(self, key) for key in CRITERIA)

    def normalized(self) -> dict[str, float]:
        """Weights rescaled to total exactly 100, keyed by criterion."""
        total = self.total
        return {key: getattr(self, key) * 100.0 / total for key in CRITERIA}

    def as_record(self) -> dict[str, float]:
        """The ``match_criteria`` block of the TEN Capital JSON record."""
        return {f"{key}_weight": round(value, 2) for key, value in self.normalized().items()}


class ScoringConfig(BaseModel):
    unknown_raw_score: float = Field(40, ge=0, le=100)
    confidence_blend: float = Field(0.5, ge=0, le=1)
    preference_bonus_per_match: float = Field(2.0, ge=0, le=10)
    preference_bonus_cap: float = Field(4.0, ge=0, le=20)
    verification_max_age_days: int = Field(365, ge=1)
    stale_activity_months: int = Field(24, ge=1)


class RankingConfig(BaseModel):
    top_n: int = Field(8, ge=1, le=25)
    tier1_min_adjusted: float = Field(68, ge=0, le=100)
    tier2_min_adjusted: float = Field(56, ge=0, le=100)
    tier3_min_adjusted: float = Field(42, ge=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> RankingConfig:
        if not (self.tier1_min_adjusted >= self.tier2_min_adjusted >= self.tier3_min_adjusted):
            raise ConfigError("Tier thresholds must satisfy tier1 >= tier2 >= tier3.")
        return self


class MatchingConfig(BaseModel):
    weights: MatchWeights = Field(default_factory=MatchWeights)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> MatchingConfig:
        """Load from a TOML file; a missing file yields the defaults."""
        if path is None or not path.exists():
            return cls()
        try:
            data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Could not read matching config {path.name}: {exc}") from exc
        try:
            return cls.model_validate(data)
        except ConfigError:
            raise
        except Exception as exc:  # pydantic ValidationError, with field detail
            raise ConfigError(f"Invalid matching config {path.name}: {exc}") from exc

    def with_weights(self, weights: dict[str, float]) -> MatchingConfig:
        """A copy with the given weights (e.g. from UI sliders)."""
        return self.model_copy(update={"weights": MatchWeights(**weights)})


class Settings(BaseSettings):
    """Deployment settings, read from ``TEN_*`` environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="TEN_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    llm_enabled: bool = False
    llm_model: str = "claude-opus-5"
    #: Opus-class extraction of a full deck with adaptive thinking can take minutes.
    llm_timeout_seconds: float = 300.0
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "TEN_ANTHROPIC_API_KEY")
    )
    investor_data_paths: str = "sample_data/investors_synthetic.json"
    #: Postgres connection string for the InvestorMatch investor database. When set, its
    #: investors are loaded alongside (or, with TEN_INVESTOR_DATA_PATHS empty, instead of)
    #: the file datasets. Railway provides it via a ${{Postgres.DATABASE_URL}} reference.
    database_url: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL", "TEN_DATABASE_URL")
    )
    max_upload_mb: int = Field(50, ge=1, le=500)
    output_dir: Path = Path("output")
    weights_file: Path = Path("config/matching.toml")
    log_level: str = "INFO"
    report_organization: str = "TEN Capital Network"
    #: When set, the web UI asks for this password before showing anything.
    app_password: SecretStr | None = None

    # --- Automated results email (Resend) ------------------------------------------
    notify_enabled: bool = False
    resend_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("RESEND_API_KEY", "TEN_RESEND_API_KEY")
    )
    #: Comma-separated. Configuration only - recipients are never taken from the UI.
    notify_to: str = "Info@tencapital.group"
    notify_from: str = "TEN Capital Investor Matching <reports@tencapital.group>"
    notify_reply_to: str = ""
    notify_subject_prefix: str = "[TEN Capital]"
    notify_timeout_seconds: float = 30.0

    def notify_recipients(self) -> list[str]:
        return [p.strip() for p in self.notify_to.replace(";", ",").split(",") if p.strip()]

    @property
    def notify_configured(self) -> bool:
        key = self.resend_api_key.get_secret_value() if self.resend_api_key else ""
        return bool(self.notify_enabled and key.strip() and self.notify_recipients())

    @staticmethod
    def resolve(path: Path | str) -> Path:
        """Resolve a configured path relative to the project root."""
        candidate = Path(path)
        return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate

    def investor_paths(self) -> list[Path]:
        return [self.resolve(p.strip()) for p in self.investor_data_paths.split(";") if p.strip()]

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url and self.database_url.get_secret_value().strip())

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def llm_configured(self) -> bool:
        """True when the external model is enabled and has an API key."""
        key = self.anthropic_api_key.get_secret_value() if self.anthropic_api_key else ""
        return bool(self.llm_enabled and key.strip())

    def matching_config(self) -> MatchingConfig:
        return MatchingConfig.load(self.resolve(self.weights_file))


def get_settings() -> Settings:
    return Settings()
