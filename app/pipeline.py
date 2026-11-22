"""End-to-end orchestration shared by the Streamlit UI, the CLI and the tests.

    analyze_deck  ->  (user review / corrections)  ->  run_matching  ->  build_record
                  ->  generate_report  ->  save_outputs

Each step is separately callable so the UI can pause for human review between them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.config import MatchingConfig, Settings
from app.errors import LLMUnavailableError
from app.extraction.company_extractor import ExtractionResult, extract_company, manual_entry_fields
from app.ingestion.deck_parser import notes_document, parse_deck, parse_uploaded_deck
from app.ingestion.models import DeckDocument
from app.ingestion.pdf_parser import render_pages_png
from app.intelligence.analyzer import data_gaps, explain_matches, recommended_next_action
from app.matching.investor_profile import build_ideal_investor_profile
from app.matching.ranking import rank_investors
from app.models.company import CompanyProfile, InvestorPreferences
from app.models.investor import InvestorRecord
from app.models.match import IdealInvestorProfile, MatchRun
from app.models.record import (
    CompanyRecord,
    DatasetSummary,
    DeckInfo,
    ExternalProcessing,
    NotificationOutcome,
    ReportMeta,
    TenCapitalRecord,
    TopMatchRecord,
    UserInfo,
)
from app.reporting.json_exporter import export_json
from app.reporting.pdf_generator import ReportContent, ReportResult, render_report
from app.services.investor_data_service import InvestorDataset, load_default_dataset
from app.services.llm_service import PROVIDER, LLMService
from app.services.notification_service import Transport, send_report_email
from app.utils.logging import get_logger
from app.utils.text import slugify

LOGGER = get_logger(__name__)


@dataclass
class AnalysisSession:
    deck: DeckDocument
    extraction: ExtractionResult
    llm_notes: list[str] = field(default_factory=list)
    llm_used: bool = False

    @property
    def company(self) -> CompanyProfile:
        return self.extraction.company


@dataclass
class MatchOutcome:
    company: CompanyProfile
    preferences: InvestorPreferences
    iip: IdealInvestorProfile
    run: MatchRun
    records: dict[str, InvestorRecord]
    dataset: InvestorDataset
    next_action: str
    gaps: list[str]
    today: date
    polished: int = 0
    llm_notes: list[str] = field(default_factory=list)


@dataclass
class EndToEndResult:
    record: TenCapitalRecord
    pdf_bytes: bytes
    report: ReportResult
    session: AnalysisSession
    outcome: MatchOutcome
    json_path: Path | None = None
    pdf_path: Path | None = None


# -------------------------------------------------------------------------- analyze


def analyze_deck(
    *,
    settings: Settings,
    deck_path: Path | None = None,
    upload: tuple[str, bytes] | None = None,
    notes_text: str = "",
    llm: LLMService | None = None,
) -> AnalysisSession:
    """Parse a deck and extract the normalized company record."""
    if upload is not None:
        filename, data = upload
        doc = parse_uploaded_deck(filename, data, max_bytes=settings.max_upload_bytes)
        raw_bytes: bytes | None = data
    elif deck_path is not None:
        doc = parse_deck(deck_path, max_bytes=settings.max_upload_bytes)
        raw_bytes = deck_path.read_bytes() if doc.source_format == "pdf" and doc.is_scanned else None
    else:
        raise ValueError("analyze_deck needs a deck_path or an upload.")

    notes = notes_document(notes_text)
    llm_notes: list[str] = []
    extra: list[dict[str, Any]] = []
    llm_used = False
    if llm is not None and llm.configured:
        images = None
        if doc.is_scanned and doc.source_format == "pdf" and raw_bytes is not None:
            images = render_pages_png(raw_bytes, [p.number for p in doc.pages])
        try:
            extraction = llm.extract_fields(doc, images=images)
            extra.append(extraction.fields)
            llm_notes.extend(extraction.warnings)
            llm_used = True
        except LLMUnavailableError as exc:
            llm_notes.append(f"External extraction skipped: {exc} Deterministic extraction was used.")

    result = extract_company(doc, notes, extra_fields=extra)
    result.warnings.extend(llm_notes)
    return AnalysisSession(deck=doc, extraction=result, llm_notes=llm_notes, llm_used=llm_used)


# --------------------------------------------------------------------------- match


def run_matching(
    company: CompanyProfile,
    preferences: InvestorPreferences,
    dataset: InvestorDataset,
    config: MatchingConfig,
    *,
    today: date | None = None,
    llm: LLMService | None = None,
    deck_scanned: bool = False,
) -> MatchOutcome:
    today = today or date.today()
    iip = build_ideal_investor_profile(company, preferences)
    ranking = rank_investors(dataset.investors, company, preferences, config, iip, today=today)
    needs_lead = company.needs_lead(preferences.lead_required)
    explain_matches(ranking.run, company, ranking.records, config, needs_lead=needs_lead, today=today)

    polished = 0
    notes: list[str] = []
    if llm is not None and llm.configured:
        try:
            polished = llm.polish_matches(ranking.run.top(config.ranking.top_n), company)
        except LLMUnavailableError as exc:
            notes.append(f"Wording polish skipped: {exc} Deterministic text was used.")
        rejected = getattr(llm, "last_rejections", [])
        if rejected:
            notes.append(f"Claude's wording for {len(rejected)} match(es) used terms not found in the evidence "
                         f"and was replaced by the engine's own text: {'; '.join(rejected[:4])}.")

    next_action = recommended_next_action(ranking.run, company, preferences,
                                          manual_fields=manual_entry_fields(company))
    gaps = data_gaps(company, ranking.run, dataset, deck_scanned=deck_scanned)
    return MatchOutcome(company=company, preferences=preferences, iip=iip, run=ranking.run,
                        records=ranking.records, dataset=dataset, next_action=next_action, gaps=gaps,
                        today=today, polished=polished, llm_notes=notes)


# -------------------------------------------------------------------------- record


def build_record(
    outcome: MatchOutcome,
    *,
    config: MatchingConfig,
    settings: Settings,
    session: AnalysisSession | None = None,
    user: UserInfo | None = None,
    analysis_id: str | None = None,
    timestamp: datetime | None = None,
) -> TenCapitalRecord:
    top = outcome.run.top(config.ranking.top_n)
    top_records = []
    for rank, match in enumerate(top, start=1):
        record = TopMatchRecord.from_match(match)
        record.rank = rank
        top_records.append(record)

    warnings: list[str] = []
    if session is not None:
        warnings.extend(session.extraction.warnings)
    warnings.extend(outcome.dataset.warnings)
    warnings.extend(outcome.run.warnings)
    warnings.extend(outcome.llm_notes)

    llm_used = bool((session and session.llm_used) or outcome.polished)
    purposes = []
    if session is not None and session.llm_used:
        purposes.append("field_extraction")
    if outcome.polished:
        purposes.append("wording_polish")
    external = ExternalProcessing(
        llm_enabled=settings.llm_configured,
        llm_used=llm_used,
        provider=PROVIDER if llm_used else None,
        model=settings.llm_model if llm_used else None,
        purposes=purposes,
        notes=[*(session.llm_notes if session else []), *outcome.llm_notes],
    )
    deck = DeckInfo()
    if session is not None:
        deck = DeckInfo(filename=session.extraction.deck_filename, format=session.extraction.deck_format,
                        pages=session.extraction.deck_pages, scanned=session.extraction.deck_scanned)

    return TenCapitalRecord(
        analysis_id=analysis_id or str(uuid.uuid4()),
        timestamp=timestamp or datetime.now(UTC),
        user=user or UserInfo(),
        source_deck=deck,
        company=CompanyRecord(**outcome.company.summary()),
        company_provenance=outcome.company.provenance(),
        investor_preferences=outcome.preferences,
        match_criteria=config.weights.as_record(),
        scoring_policy={
            "fit_formula": "sum(raw_score x weight / 100), weights normalized to 100",
            "adjusted_formula": "fit x (1 - confidence_blend + confidence_blend x confidence)",
            "unknown_raw_score": config.scoring.unknown_raw_score,
            "confidence_blend": config.scoring.confidence_blend,
            "preference_bonus_per_match": config.scoring.preference_bonus_per_match,
            "preference_bonus_cap": config.scoring.preference_bonus_cap,
            "verification_max_age_days": config.scoring.verification_max_age_days,
            "stale_activity_months": config.scoring.stale_activity_months,
            "tier_thresholds": config.ranking.model_dump(),
            "scored_on": outcome.today.isoformat(),
        },
        ideal_investor_profile=outcome.iip,
        top_matches=top_records,
        rejected_investors=outcome.run.rejected,
        recommended_next_action=outcome.next_action,
        data_gaps=outcome.gaps,
        investor_dataset=DatasetSummary(**outcome.dataset.summary()),
        warnings=list(dict.fromkeys(warnings)),
        external_processing=external,
    )


# -------------------------------------------------------------------------- outputs


def generate_report(record: TenCapitalRecord, *, organization: str = "TEN Capital Network"
                    ) -> tuple[ReportResult, TenCapitalRecord]:
    """Render the one-page PDF and return the record updated with report metadata."""
    content = ReportContent.from_record(record, organization=organization)
    result = render_report(content, generated_on=record.timestamp.date())
    updated = record.model_copy(update={"report": ReportMeta(
        pdf_generated=True, page_count=result.page_count, layout_rung=result.rung,
        content_reductions=result.reductions)})
    return result, updated


def notify_results(record: TenCapitalRecord, pdf_bytes: bytes | None, *, settings: Settings,
                   transport: Transport | None = None) -> tuple[NotificationOutcome, TenCapitalRecord]:
    """Email the results to the configured inbox and record what happened. Never raises."""
    outcome = send_report_email(record, pdf_bytes, settings=settings, transport=transport)
    return outcome, record.model_copy(update={"notification": outcome})


def output_basename(record: TenCapitalRecord) -> str:
    return f"{slugify(record.company.name or 'company')}_{record.analysis_id[:8]}_investor_matching"


def save_outputs(record: TenCapitalRecord, pdf_bytes: bytes | None, out_dir: Path) -> tuple[Path, Path | None]:
    """Write the JSON (and PDF) to disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base = output_basename(record)
    pdf_path = None
    if pdf_bytes is not None:
        pdf_path = out_dir / f"{base}_report.pdf"
        pdf_path.write_bytes(pdf_bytes)
        record = record.model_copy(update={"report": record.report.model_copy(
            update={"pdf_filename": pdf_path.name})})
    json_path = export_json(record, out_dir / f"{base}.json")
    LOGGER.info("Saved outputs for analysis %s", record.analysis_id[:8])
    return json_path, pdf_path


def run_end_to_end(
    deck_path: Path,
    *,
    settings: Settings,
    preferences: InvestorPreferences | None = None,
    dataset: InvestorDataset | None = None,
    config: MatchingConfig | None = None,
    notes_text: str = "",
    user: UserInfo | None = None,
    today: date | None = None,
    out_dir: Path | None = None,
    llm: LLMService | None = None,
    timestamp: datetime | None = None,
    notify: bool = True,
    email_transport: Transport | None = None,
) -> EndToEndResult:
    config = config or settings.matching_config()
    preferences = preferences or InvestorPreferences()
    dataset = dataset if dataset is not None else load_default_dataset(settings)
    session = analyze_deck(settings=settings, deck_path=deck_path, notes_text=notes_text, llm=llm)
    outcome = run_matching(session.company, preferences, dataset, config, today=today, llm=llm,
                           deck_scanned=session.extraction.deck_scanned)
    record = build_record(outcome, config=config, settings=settings, session=session,
                          user=user, timestamp=timestamp)
    report, record = generate_report(record, organization=settings.report_organization)
    if notify:
        _outcome, record = notify_results(record, report.pdf_bytes, settings=settings,
                                          transport=email_transport)
    json_path = pdf_path = None
    if out_dir is not None:
        json_path, pdf_path = save_outputs(record, report.pdf_bytes, out_dir)
    return EndToEndResult(record=record, pdf_bytes=report.pdf_bytes, report=report, session=session,
                          outcome=outcome, json_path=json_path, pdf_path=pdf_path)
