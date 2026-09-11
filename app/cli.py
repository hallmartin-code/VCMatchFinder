"""Headless command line.

    python -m app.cli sample_data/lumora_diagnostics_series_a.pdf \
        --consent-analysis --consent-storage --out output

Writing files to disk requires --consent-storage. The CLI never contacts investors.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from app.config import PROJECT_ROOT, MatchingConfig, get_settings
from app.errors import ConsentError, MatchingEngineError
from app.matching.taxonomy import INVESTOR_TYPES, parse_investor_type
from app.models.company import InvestorPreferences
from app.models.consent import ConsentRecord
from app.models.record import UserInfo
from app.pipeline import run_end_to_end
from app.reporting.template import render_template_pdf
from app.services.investor_data_service import load_investor_dataset
from app.services.llm_service import LLMService
from app.utils.logging import configure_logging

_ASCII = str.maketrans({"—": "-", "–": "-", "·": "|", "…": "...", "›": ">"})


def _plain(text: str) -> str:
    """ASCII-safe console text: Windows consoles often cannot render em dashes or middots."""
    return text.translate(_ASCII)


def _types(values: list[str]) -> list[str]:
    out = []
    for value in values:
        key = parse_investor_type(value)
        if key == "unknown":
            raise SystemExit(f"Unknown investor type '{value}'. Choose from: {', '.join(INVESTOR_TYPES)}")
        out.append(key)
    return out


DEFAULT_TEMPLATE_PDF = PROJECT_ROOT / "docs" / "investor_matching_report_TEMPLATE.pdf"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="TEN Capital Investor Matching Engine")
    p.add_argument("deck", type=Path, nargs="?", help="Pitch deck (.pdf, .pptx or .ppt)")
    p.add_argument("--template", type=Path, nargs="?", const=DEFAULT_TEMPLATE_PDF, default=None,
                   help="Render the blank report template (placeholders only) and exit; "
                        "optional output path, default docs/investor_matching_report_TEMPLATE.pdf")
    p.add_argument("--investors", type=Path, action="append", default=[], help="Investor dataset (.csv/.json); repeatable")
    p.add_argument("--no-default-investors", action="store_true", help="Do not load the configured default dataset")
    p.add_argument("--out", type=Path, default=None, help="Output directory (requires --consent-storage)")
    p.add_argument("--notes", default="", help="Additional company information (treated as user-supplied)")
    p.add_argument("--prefer-type", action="append", default=[], help="Preferred investor type; repeatable")
    p.add_argument("--prefer-geo", action="append", default=[], help="Preferred investor geography; repeatable")
    p.add_argument("--exclude-type", action="append", default=[], help="Excluded investor type; repeatable")
    p.add_argument("--exclude-investor", action="append", default=[], help="Excluded investor name; repeatable")
    p.add_argument("--lead-required", choices=["auto", "yes", "no"], default="auto")
    p.add_argument("--weights", type=Path, default=None, help="Matching config TOML (weights, thresholds)")
    p.add_argument("--today", type=date.fromisoformat, default=None, help="Scoring date (YYYY-MM-DD)")
    p.add_argument("--consent-analysis", action="store_true", help="Consent to analyze the deck (required)")
    p.add_argument("--consent-storage", action="store_true", help="Consent to store outputs on disk")
    p.add_argument("--consent-outreach", action="store_true", help="Record outreach consent (no outreach is performed)")
    p.add_argument("--consent-external", action="store_true", help="Consent to send company data to the external model")
    p.add_argument("--no-email", action="store_true", help="Do not send the automated results email for this run")
    p.add_argument("--user-name", default="")
    p.add_argument("--user-email", default="")
    p.add_argument("--user-org", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.template is not None:
        # The blank template carries placeholders only - no company data, so no consent needed.
        try:
            result = render_template_pdf(args.template)
        except MatchingEngineError as exc:
            print(_plain(f"Error: {exc}"), file=sys.stderr)
            return 1
        print(_plain(f"Template written: {args.template} ({result.page_count} page, placeholders only)"))
        return 0
    if args.deck is None:
        parser.error("a deck path is required (or use --template to render the blank report template)")
    settings = get_settings()
    configure_logging(settings.log_level)
    consent = ConsentRecord(
        analysis_consent=args.consent_analysis, data_storage_consent=args.consent_storage,
        outreach_consent=args.consent_outreach, external_processing_consent=args.consent_external,
        timestamp=datetime.now(UTC), source="cli",
    )
    if args.out is not None and not consent.data_storage_consent:
        print("Refusing to write files: add --consent-storage (or omit --out).", file=sys.stderr)
        return 2
    try:
        config = MatchingConfig.load(args.weights) if args.weights else settings.matching_config()
        paths = ([] if args.no_default_investors else settings.investor_paths()) + list(args.investors)
        dataset = load_investor_dataset(paths)
        preferences = InvestorPreferences(
            preferred_types=_types(args.prefer_type), preferred_geographies=args.prefer_geo,
            excluded_types=_types(args.exclude_type), excluded_investors=args.exclude_investor,
            lead_required={"auto": None, "yes": True, "no": False}[args.lead_required],
        )
        llm = LLMService(settings)
        if consent.external_processing_consent and llm.configured:
            print(_plain(f"Note: {llm.disclosure()}"), file=sys.stderr)
        result = run_end_to_end(
            args.deck, settings=settings, consent=consent, preferences=preferences, dataset=dataset,
            config=config, notes_text=args.notes, today=args.today, out_dir=args.out, llm=llm,
            notify=not args.no_email,
            user=UserInfo(name=args.user_name, email=args.user_email, organization=args.user_org),
        )
    except ConsentError as exc:
        print(_plain(f"Consent required: {exc}"), file=sys.stderr)
        return 2
    except MatchingEngineError as exc:
        print(_plain(f"Error: {exc}"), file=sys.stderr)
        return 1

    record = result.record
    lines = [
        f"\nTEN CAPITAL - INVESTOR MATCHING REPORT: {record.company.name or 'Unnamed company'}",
        f"Investor data: {', '.join(record.investor_dataset.sources) or 'none'}"
        f"{' (SYNTHETIC)' if record.investor_dataset.is_synthetic else ''}",
        f"{'#':>2}  {'Investor':38} {'Fit':>5} {'Adj':>5}  {'Conf':7} Priority",
    ]
    for m in record.top_matches:
        lines.append(f"{m.rank:>2}  {m.investor[:38]:38} {m.fit_score:5.1f} {m.adjusted_score:5.1f}  "
                     f"{m.confidence:7} {m.outreach_priority}")
    lines.append(f"\nRejected by hard filters: {len(record.rejected_investors)}")
    if settings.notify_enabled and not args.no_email:
        lines.append(record.notification.summary())
    lines.append(f"Next action: {record.recommended_next_action}")
    if result.pdf_path:
        lines.append(f"\nPDF:  {result.pdf_path}\nJSON: {result.json_path}")
    elif args.out is None:
        lines.append("\n(No files written. Pass --out DIR --consent-storage to save the PDF and JSON.)")
    print(_plain("\n".join(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
