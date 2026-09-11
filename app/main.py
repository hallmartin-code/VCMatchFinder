"""Streamlit UI in the TEN Capital design: Upload -> Review -> Preferences -> Matches -> Export.

Run from the project root:

    streamlit run app/main.py

The landing page is the design's centered upload card. Once a deck is analyzed the app
switches to a wide workspace with a step indicator and one panel per step. All styling
lives in app/ui/theme.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
_ROOT = _APP_DIR.parent
# `streamlit run app/main.py` puts app/ on sys.path, where names like `config` or `models`
# could shadow other packages. Import everything through the `app` package instead.
sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != _APP_DIR]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import uuid  # noqa: E402
from datetime import UTC, date, datetime  # noqa: E402
from html import escape  # noqa: E402
from typing import Any  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.config import CRITERIA, MatchingConfig, get_settings  # noqa: E402
from app.errors import ConfigError, MatchingEngineError  # noqa: E402
from app.extraction.company_extractor import manual_entry_fields  # noqa: E402
from app.matching.taxonomy import INVESTOR_TYPES, STAGE_LABELS, SUBSECTORS  # noqa: E402
from app.models.company import FIELD_SPECS, CompanyProfile, InvestorPreferences  # noqa: E402
from app.models.consent import ConsentRecord  # noqa: E402
from app.models.match import EVIDENCE_LABELS, TIER_LABELS, InvestorMatch  # noqa: E402
from app.models.record import UserInfo  # noqa: E402
from app.pipeline import (  # noqa: E402
    analyze_deck,
    build_record,
    generate_report,
    notify_results,
    output_basename,
    run_matching,
    save_outputs,
)
from app.reporting.json_exporter import record_to_json  # noqa: E402
from app.services.investor_data_service import InvestorDataset, load_default_dataset  # noqa: E402
from app.services.llm_service import LLMService  # noqa: E402
from app.ui import theme  # noqa: E402
from app.utils.provenance import SOURCE_LABELS  # noqa: E402

_TYPE_KEYS = [k for k in INVESTOR_TYPES if k != "unknown"]
_UPLOAD_TYPES = ["pdf", "pptx", "ppt"]
_MISSING_MARK = "Not in deck"
_ANALYSIS_KEYS = ("session", "company", "outcome", "record", "pdf", "report", "saved", "notification",
                  "analysis_id", "consent_record", "user_info")
_WORKSPACE_CONSENT_KEYS = ("ws_storage", "ws_outreach", "ws_external")


def _init_state() -> None:
    defaults: dict[str, Any] = {key: None for key in _ANALYSIS_KEYS}
    defaults.update(excluded_extra=[], run_requested=False, reviewed=False)
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _reset_analysis() -> None:
    for key in _ANALYSIS_KEYS:
        st.session_state[key] = None
    st.session_state.excluded_extra = []
    st.session_state.reviewed = False
    for key in ("editor", *_WORKSPACE_CONSENT_KEYS):
        st.session_state.pop(key, None)


def _landing_consent() -> ConsentRecord:
    return ConsentRecord(
        analysis_consent=bool(st.session_state.get("c_analysis")),
        data_storage_consent=bool(st.session_state.get("c_storage")),
        outreach_consent=bool(st.session_state.get("c_outreach")),
        external_processing_consent=bool(st.session_state.get("c_external")),
        timestamp=datetime.now(UTC),
        source="streamlit_ui",
    )


def _consent() -> ConsentRecord:
    """Consent for the current analysis.

    Streamlit discards a widget's state once the widget stops rendering, so the landing
    checkboxes cannot be read from the workspace. Consent is therefore captured when the deck
    is analyzed, and changed afterwards only through the workspace's own consent controls.
    """
    stored: ConsentRecord | None = st.session_state.get("consent_record")
    if stored is None:
        return _landing_consent()
    flags = {
        "data_storage_consent": bool(st.session_state.get("ws_storage", stored.data_storage_consent)),
        "outreach_consent": bool(st.session_state.get("ws_outreach", stored.outreach_consent)),
        "external_processing_consent": bool(st.session_state.get("ws_external", stored.external_processing_consent)),
    }
    if any(getattr(stored, key) != value for key, value in flags.items()):
        stored = stored.model_copy(update={**flags, "timestamp": datetime.now(UTC)})
        st.session_state.consent_record = stored
    return stored


def _user() -> UserInfo:
    """The user's details, captured with the deck (the landing inputs do not survive the switch)."""
    stored: UserInfo | None = st.session_state.get("user_info")
    if stored is not None:
        return stored
    return UserInfo(name=st.session_state.get("u_name", ""), email=st.session_state.get("u_email", ""),
                    organization=st.session_state.get("u_org", ""))


def _page_icon() -> Any:
    try:
        from PIL import Image

        return Image.open(theme.BRAND_MARK_PNG)
    except Exception:  # noqa: BLE001 - never block startup over an icon
        return "◆"


def _disclosures(settings: Any, llm: LLMService) -> list[str]:
    """What happens to the deck, stated from live settings so it can never drift."""
    items = [
        "Decks are processed on the server and deleted as soon as they are parsed. Accepted: "
        f"<code>.pdf</code> <code>.pptx</code> <code>.ppt</code>, up to {settings.max_upload_mb}&nbsp;MB."
    ]
    if settings.notify_configured:
        rule = ("when you grant storage consent" if settings.notify_requires_storage_consent
                else "for every analysis")
        recipients = ", ".join(f"<code>{escape(r)}</code>" for r in settings.notify_recipients())
        items.append(f"A copy of each generated report is emailed to {recipients} {rule}.")
    if llm.configured:
        items.append(f"With your consent, deck text is sent to Anthropic's Claude (<code>{escape(llm.model)}</code>) "
                     "to extract facts and draft match explanations. Scores are always computed by the engine.")
    items.append("Recommendations are for human review. This app never contacts investors.")
    return items


# --------------------------------------------------------------------------- sidebar


def render_sidebar(base: MatchingConfig, llm: LLMService, settings: Any) -> MatchingConfig:
    consent: ConsentRecord | None = st.session_state.get("consent_record")
    if consent is not None:
        st.sidebar.header("Consent for this analysis")
        st.sidebar.caption(f"Analysis consent granted {consent.timestamp:%Y-%m-%d %H:%M} UTC. "
                           "The others can be changed at any time.")
        storage = "Store the outputs"
        if settings.notify_configured:
            storage += f" and email a copy to `{', '.join(settings.notify_recipients())}`"
        st.sidebar.checkbox(storage, value=consent.data_storage_consent, key="ws_storage")
        st.sidebar.checkbox("Outreach may be considered later (recorded only)", value=consent.outreach_consent,
                            key="ws_outreach")
        if llm.configured:
            st.sidebar.checkbox("Send company information to Claude", value=consent.external_processing_consent,
                                key="ws_external")
        st.sidebar.divider()
    st.sidebar.header("Scoring weights")
    st.sidebar.caption("Weights are normalized to 100. Scores are computed deterministically.")
    if st.sidebar.button("Reset weights"):
        for key in CRITERIA:
            st.session_state[f"w_{key}"] = int(getattr(base.weights, key))
    weights: dict[str, float] = {}
    for key, label in CRITERIA.items():
        weights[key] = st.sidebar.slider(label, 0, 40, int(getattr(base.weights, key)), key=f"w_{key}")
    st.sidebar.caption(f"Total: {sum(weights.values()):g} (normalized to 100) · scoring date "
                       f"{date.today():%Y-%m-%d}")
    st.sidebar.divider()
    st.sidebar.subheader("External model")
    if llm.configured:
        st.sidebar.success(f"Available: {llm.model}")
        st.sidebar.caption(llm.disclosure())
    else:
        st.sidebar.info("Disabled - fully deterministic mode; no deck text is sent to a model.")
    st.sidebar.subheader("Results email")
    if settings.notify_configured:
        rule = " when storage consent is given" if settings.notify_requires_storage_consent else ""
        st.sidebar.success(f"On: each generated report is emailed to {', '.join(settings.notify_recipients())}{rule}.")
    else:
        st.sidebar.info("Off (set TEN_NOTIFY_ENABLED and RESEND_API_KEY to enable).")
    try:
        return base.with_weights(weights)
    except (ConfigError, ValueError):
        st.sidebar.error("At least one weight must be positive; using defaults.")
        return base


# ---------------------------------------------------------------------- landing (step 1)


def render_landing(settings: Any, llm: LLMService) -> None:
    st.markdown(theme.brand_lockup(), unsafe_allow_html=True)
    with theme.card(
        "Investor Matching Engine",
        'Pitch Deck<span class="arrow">&rarr;</span><span class="to">Ranked Investor Matches</span>',
        lede="Upload a pitch deck and get a ranked, explained list of the investors most likely to fund "
             "the company now, delivered as a one-page TEN Capital report.",
    ):
        upload = st.file_uploader("Pitch deck (PDF, PPTX or PPT)", type=_UPLOAD_TYPES,
                                  accept_multiple_files=False, label_visibility="collapsed",
                                  key=theme.DECK_UPLOADER_KEY)
        with st.expander("Add context and your details (optional)"):
            st.text_area("Additional company information", key="notes",
                         help="Treated as user-supplied information and preferred over deck text.")
            st.text_input("Your name", key="u_name")
            st.text_input("Your email", key="u_email")
            st.text_input("Organization", key="u_org")

        theme.label("Consent · each permission is separate")
        st.checkbox("I consent to analysis of this deck (required)", key="c_analysis")
        storage_label = "I consent to storing the outputs (saved on the server"
        if settings.notify_configured:
            storage_label += f" and a copy emailed to `{', '.join(settings.notify_recipients())}`"
        st.checkbox(storage_label + ")", key="c_storage")
        st.checkbox("I consent to investor outreach being considered later (recorded only - this app never "
                    "contacts investors)", key="c_outreach")
        if llm.configured:
            st.checkbox("I consent to sending company information to Claude for analysis", key="c_external",
                        help=llm.disclosure())

        if st.button("Analyze deck", type="primary", disabled=upload is None):
            consent = _consent()
            if not consent.analysis_consent:
                st.error("Analysis consent is required before the deck can be analyzed.")
            else:
                try:
                    with st.spinner("Reading the deck and extracting company information..."):
                        session = analyze_deck(settings=settings, consent=consent,
                                               upload=(upload.name, upload.getvalue()),
                                               notes_text=st.session_state.get("notes", ""), llm=llm)
                except MatchingEngineError as exc:
                    st.error(str(exc))
                else:
                    user = _user()
                    _reset_analysis()
                    st.session_state.update(session=session, company=session.company,
                                            analysis_id=str(uuid.uuid4()), consent_record=consent,
                                            user_info=user)
                    st.rerun()
        theme.disclosure(_disclosures(settings, llm))
    theme.footer()


# ---------------------------------------------------------------------------- step 2


def _editor_frame(company: CompanyProfile) -> pd.DataFrame:
    manual = set(manual_entry_fields(company))
    rows = []
    for name, spec in FIELD_SPECS.items():
        if not spec.reviewable:
            continue
        item = company.field(name)
        source = SOURCE_LABELS[item.source] if item.known else "Missing"
        if name in manual:
            source = "Missing - enter manually"
        rows.append({"key": name, "Field": spec.label, "Value": company.display_value(name),
                     "Source": source, "Location": item.location or "", "Confidence": round(item.confidence, 2)})
    return pd.DataFrame(rows)


def render_review(*, active: bool) -> None:
    session = st.session_state.session
    company: CompanyProfile = st.session_state.company
    ex = session.extraction
    subtitle = (f"{ex.deck_filename} · {ex.deck_format.upper()} · {ex.deck_pages} pages/slides"
                + (" · scanned" if ex.deck_scanned else "")
                + ". Edit any Value; corrected fields are labeled user-supplied.")
    with theme.panel(2, "Review and correct the extracted data", subtitle, active=active):
        for warning in ex.warnings:
            st.warning(theme.safe_md(warning))
        frame = _editor_frame(company)
        edited = st.data_editor(
            frame, key="editor", hide_index=True,
            disabled=["Field", "Source", "Location", "Confidence"],
            column_config={"key": None, "Confidence": st.column_config.NumberColumn(format="%.2f")},
        )
        st.caption("Stages: " + ", ".join(STAGE_LABELS.values())
                   + ". Subsectors: " + ", ".join(s.label for s in SUBSECTORS.values()) + ".")
        apply_col, confirm_col, _spacer = st.columns([1, 1.3, 2.7])
        if apply_col.button("Apply corrections"):
            overrides: dict[str, Any] = {}
            for (_i, before), (_j, after) in zip(frame.iterrows(), edited.iterrows(), strict=True):
                new = str(after["Value"]).strip()
                if new and new != str(before["Value"]).strip() and new != _MISSING_MARK:
                    overrides[before["key"]] = new
            if overrides:
                st.session_state.company = company.apply_overrides(overrides)
                st.session_state.outcome = None
                st.session_state.reviewed = True
                st.toast(f"Applied {len(overrides)} correction(s).")
                st.rerun()
            else:
                st.info("No changes detected.")
        if confirm_col.button("Looks right — continue"):
            st.session_state.reviewed = True
            st.rerun()


# ---------------------------------------------------------------------------- step 3


def render_preferences(settings: Any, *, active: bool) -> tuple[InvestorPreferences, InvestorDataset]:
    with theme.panel(3, "Investor preferences and data",
                     "Optional targeting. Exclusions become hard filters; preferences add a small, "
                     "visible ranking bonus.", active=active):
        label = lambda key: INVESTOR_TYPES[key]  # noqa: E731
        col1, col2 = st.columns(2)
        with col1:
            preferred = st.multiselect("Preferred investor types", _TYPE_KEYS, format_func=label, key="p_pref")
            excluded = st.multiselect("Excluded investor types", _TYPE_KEYS, format_func=label, key="p_excl")
            lead = st.radio("Lead investor required?", ["Auto (from deck)", "Yes", "No"], horizontal=True,
                            key="p_lead")
        with col2:
            geos = st.text_input("Preferred geographies (comma-separated)", key="p_geo")
            use_default = st.checkbox("Include the configured investor dataset", value=True, key="p_default")
            uploads = st.file_uploader("Additional investor datasets (CSV/JSON)", type=["csv", "json"],
                                       accept_multiple_files=True, key="p_uploads")
        dataset = load_default_dataset(settings, [(f.name, f.getvalue()) for f in uploads or []],
                                       include_defaults=use_default)
        st.caption(f"Investor data: {dataset.label()} · {len(dataset.investors)} records")
        for warning in dataset.warnings:
            (st.error if "SYNTHETIC" in warning else st.caption)(theme.safe_md(warning))
        excluded_names = st.multiselect("Excluded investors", sorted({i.name for i in dataset.investors}),
                                        key="p_excl_names")
        extra = st.session_state.excluded_extra
        if extra:
            st.caption("Excluded from match review: " + ", ".join(extra))
            if st.button("Clear review exclusions"):
                st.session_state.excluded_extra = []
                st.rerun()
    preferences = InvestorPreferences(
        preferred_types=preferred, excluded_types=excluded,
        preferred_geographies=[g.strip() for g in geos.split(",") if g.strip()],
        excluded_investors=sorted(set(excluded_names) | set(extra)),
        lead_required={"Auto (from deck)": None, "Yes": True, "No": False}[lead],
    )
    return preferences, dataset


# ---------------------------------------------------------------------------- step 4


def _match_details(match: InvestorMatch) -> None:
    md = theme.safe_md
    st.markdown(f"**Why it fits:** {md(match.rationale)}")
    st.markdown(f"**Outreach angle:** {md(match.outreach_angle)}")
    st.markdown("**Likely concerns:** " + (md("; ".join(match.likely_concerns)) or "None identified"))
    st.markdown("**Required introduction materials:** " + md(", ".join(match.required_materials)))
    st.markdown(f"**Priority:** {match.tier_label} — {md(match.priority_reason)}")
    st.markdown("**Investment evidence:**\n" + "\n".join(f"- {md(e)}" for e in match.investment_evidence))
    breakdown = pd.DataFrame([{
        "Criterion": c.label, "Weight": round(c.weight, 1), "Raw": c.raw_score,
        "Weighted": round(c.weighted_score, 2), "Evidence": c.evidence, "Level": EVIDENCE_LABELS[c.evidence_level],
        "Confidence": c.confidence, "Missing": ", ".join(c.missing_information), "Source": c.evidence_source,
    } for c in match.breakdown])
    st.dataframe(breakdown, hide_index=True)
    st.caption(f"Fit {match.fit_score} · evidence {EVIDENCE_LABELS[match.evidence_quality]} · confidence "
               f"{match.confidence} ({match.confidence_score:.2f}) · adjusted {match.adjusted_score}"
               + (f" · preference bonus +{match.preference_bonus:g}" if match.preference_bonus else "")
               + (" · wording by Claude, fact-checked" if match.text_source == "llm_polished" else ""))


def render_matches(settings: Any, config: MatchingConfig, prefs: tuple[InvestorPreferences, InvestorDataset],
                   llm: LLMService, *, active: bool) -> None:
    preferences, dataset = prefs
    with theme.panel(4, "Ranked investor matches",
                     "Scored on nine weighted criteria, filtered for hard incompatibilities, and ordered by "
                     "outreach priority.", active=active):
        cols = st.columns([1, 1, 3])
        run = cols[0].button("Run matching", type="primary") or st.session_state.run_requested
        recalc = cols[1].button("Recalculate")
        if run or recalc:
            st.session_state.run_requested = False
            consent = _consent()
            try:
                session = st.session_state.session
                spinner = "Scoring investors and drafting explanations..." if llm.allowed(consent) \
                    else "Scoring investors..."
                with st.spinner(spinner):
                    st.session_state.outcome = run_matching(
                        st.session_state.company, preferences, dataset, config, consent, llm=llm,
                        deck_scanned=bool(session and session.extraction.deck_scanned))
                st.session_state.update(record=None, pdf=None, report=None, saved=None, notification=None)
            except MatchingEngineError as exc:
                st.error(str(exc))
                return
            st.rerun()  # redraw so the stepper and the active panel reflect the new step
        outcome = st.session_state.outcome
        if outcome is None:
            st.caption("Run matching when the extracted data and preferences look right.")
            return

        rows = outcome.run.top(config.ranking.top_n)
        named = [m for m in outcome.run.matches if m.outreach_priority != "do_not_target"]
        tier1 = sum(1 for m in named if m.outreach_priority == "tier_1")
        top_fit = f"{rows[0].fit_score:.0f}" if rows else "-"
        theme.tiles([("Named matches", str(len(named)), theme.TOKENS["teal"]),
                     ("Tier 1 · contact now", str(tier1), theme.TOKENS["amber"]),
                     ("Removed by filters", str(len(outcome.run.rejected)), theme.TOKENS["coral"]),
                     ("Top fit score", top_fit, theme.TOKENS["ink_100"])])
        theme.label("Ideal investor profile")
        st.markdown("\n".join(f"- {theme.safe_md(point)}" for point in outcome.iip.summary_points))
        table = pd.DataFrame([{
            "#": i, "Investor": m.investor_name + (" (profile)" if m.kind == "archetype" else ""),
            "Type": m.investor_type_label, "Priority": TIER_LABELS[m.outreach_priority], "Fit": m.fit_score,
            "Adjusted": m.adjusted_score, "Confidence": m.confidence, "Evidence": EVIDENCE_LABELS[m.evidence_quality],
            "Concern": m.likely_concerns[0] if m.likely_concerns else "",
        } for i, m in enumerate(rows, start=1)])
        st.dataframe(table, hide_index=True)
        if dataset.any_synthetic:
            st.error("These named investors come from a SYNTHETIC test dataset and are fictitious.")

        for i, match in enumerate(rows, start=1):
            with st.expander(f"{i}. {match.investor_name} - {TIER_LABELS[match.outreach_priority]} · "
                             f"fit {match.fit_score:.0f}"):
                _match_details(match)
                if match.kind == "investor" and st.button("Exclude this investor", key=f"exclude_{match.investor_id}"):
                    st.session_state.excluded_extra = sorted(set(st.session_state.excluded_extra) | {match.investor_name})
                    st.session_state.run_requested = True
                    st.rerun()

        others = [m for m in outcome.run.matches if m not in rows]
        if others:
            with st.expander(f"Other scored investors ({len(others)})"):
                st.dataframe(pd.DataFrame([{"Investor": m.investor_name, "Priority": TIER_LABELS[m.outreach_priority],
                                            "Fit": m.fit_score, "Adjusted": m.adjusted_score,
                                            "Reason": m.priority_reason} for m in others]), hide_index=True)
        if outcome.run.rejected:
            with st.expander(f"Rejected by hard filters ({len(outcome.run.rejected)}) - audit"):
                st.dataframe(pd.DataFrame([{"Investor": r.investor_name,
                                            "Reasons": " | ".join(x.detail for x in r.reasons)}
                                           for r in outcome.run.rejected]), hide_index=True)
        st.info(f"**Recommended next action:** {theme.safe_md(outcome.next_action)}")
        for gap in outcome.gaps:
            st.caption(f"Data quality: {theme.safe_md(gap)}")
        for note in outcome.llm_notes:
            st.caption(theme.safe_md(note))


# ---------------------------------------------------------------------------- step 5


def render_export(settings: Any, config: MatchingConfig, *, active: bool) -> None:
    with theme.panel(5, "Generate the report", "The one-page TEN Capital Investor Matching Report and the "
                     "machine-readable JSON record.", active=active):
        outcome = st.session_state.outcome
        if outcome is None:
            st.caption("Available after matching.")
            return
        consent = _consent()

        def current_record() -> Any:
            return build_record(outcome, config=config, consent=consent, settings=settings,
                                session=st.session_state.session, user=_user(),
                                analysis_id=st.session_state.analysis_id)

        cols = st.columns([1, 1, 3])
        if cols[0].button("Generate PDF", type="primary"):
            try:
                with st.spinner("Generating the report..."):
                    report, record = generate_report(current_record(), organization=settings.report_organization)
            except MatchingEngineError as exc:
                st.error(str(exc))
                return
            with st.spinner("Emailing the results..." if settings.notify_configured else "Finishing..."):
                sent, record = notify_results(record, report.pdf_bytes, settings=settings, consent=consent)
            st.session_state.update(record=record, pdf=report.pdf_bytes, report=report, saved=None,
                                    notification=sent)
            if consent.data_storage_consent:
                json_path, pdf_path = save_outputs(record, report.pdf_bytes, settings.resolve(settings.output_dir),
                                                   consent)
                st.session_state.saved = (json_path, pdf_path)
            st.rerun()
        if cols[1].button("Export JSON"):
            st.session_state.record = st.session_state.record or current_record()

        record = st.session_state.record
        if record is None:
            return
        base = output_basename(record)
        if st.session_state.pdf is not None:
            report = st.session_state.report
            st.success(f"One-page report generated ({report.page_count} page).")
            if report.reductions:
                st.caption("To stay on one page: " + "; ".join(report.reductions) + ".")
        download_cols = st.columns([1, 1, 3])
        if st.session_state.pdf is not None:
            download_cols[0].download_button("Download PDF", st.session_state.pdf, file_name=f"{base}_report.pdf",
                                             mime="application/pdf")
        download_cols[1].download_button("Download JSON", record_to_json(record), file_name=f"{base}.json",
                                         mime="application/json")
        if st.session_state.saved:
            json_path, pdf_path = st.session_state.saved
            st.caption(f"Saved on the server (storage consent granted): {pdf_path.name} and {json_path.name}")
        elif not consent.data_storage_consent:
            st.caption("Nothing was stored: storage consent not granted. Use the download buttons.")
        sent = st.session_state.get("notification")
        if sent is not None and settings.notify_configured:
            if sent.sent:
                st.success(sent.summary())
            elif sent.attempted:
                st.warning(sent.summary() + " The report itself is unaffected.")
            else:
                st.caption(sent.summary())


# -------------------------------------------------------------------------- workspace


def _progress() -> tuple[int, int]:
    """(current step, last completed step) for the stepper."""
    if st.session_state.pdf is not None:
        return 5, 5
    if st.session_state.outcome is not None:
        return 4, 3
    if st.session_state.reviewed:
        return 3, 2
    return 2, 1


def _status_chips(settings: Any, llm: LLMService) -> str:
    chips = [("Claude · on" if llm.configured else "Claude · off", "on" if llm.configured else "off"),
             ("Email · " + ", ".join(settings.notify_recipients()) if settings.notify_configured else "Email · off",
              "on" if settings.notify_configured else "off"),
             ("Scores · deterministic", "on")]
    return theme.chips_html(chips)


def render_workspace(settings: Any, llm: LLMService, base_config: MatchingConfig) -> None:
    config = render_sidebar(base_config, llm, settings)
    current, completed = _progress()
    left, right = st.columns([5, 1], vertical_alignment="center")
    with left:
        theme.topbar(_status_chips(settings, llm))
    with right:
        if st.button("New analysis", key="new_analysis"):
            _reset_analysis()
            st.rerun()
    theme.stepper(current, completed)
    company: CompanyProfile = st.session_state.company
    st.markdown(f'<h1 class="tc-h1">{escape(company.display_name)}</h1>', unsafe_allow_html=True)
    render_review(active=current == 2)
    prefs = render_preferences(settings, active=current == 3)
    render_matches(settings, config, prefs, llm, active=current == 4)
    render_export(settings, config, active=current == 5)
    theme.footer()


# ------------------------------------------------------------------------------- gate


def _password_gate(settings: Any) -> bool:
    """Ask for TEN_APP_PASSWORD (when configured) before showing anything else."""
    import hmac

    expected = settings.app_password.get_secret_value() if settings.app_password else ""
    if not expected or st.session_state.get("authenticated"):
        return True
    st.markdown(theme.brand_lockup(), unsafe_allow_html=True)
    with theme.card("Restricted access",
                    'TEN Capital<span class="arrow">&middot;</span><span class="to">Investor Matching</span>',
                    lede="Enter the access password provided by TEN Capital."):
        entered = st.text_input("Access password", type="password", key="access_password")
        if st.button("Enter", type="primary"):
            if hmac.compare_digest(entered.encode(), expected.encode()):
                st.session_state.authenticated = True
                st.session_state.pop("access_password", None)
                st.rerun()
            st.error("Incorrect password.")
    theme.footer()
    return False


def main() -> None:
    workspace = st.session_state.get("session") is not None
    st.set_page_config(page_title="TEN Capital — Investor Matching Engine", page_icon=_page_icon(),
                       layout="wide" if workspace else "centered",
                       initial_sidebar_state="expanded" if workspace else "collapsed")
    _init_state()
    settings = get_settings()
    theme.inject(wide=workspace,
                 upload_hint=f"Click to choose a deck\n.pdf  ·  .pptx  ·  .ppt  ·  up to {settings.max_upload_mb} MB")
    if not _password_gate(settings):
        return
    try:
        base_config = settings.matching_config()
    except ConfigError as exc:
        st.error(f"Matching configuration error: {exc}. Using defaults.")
        base_config = MatchingConfig()
    llm = LLMService(settings)
    if workspace:
        render_workspace(settings, llm, base_config)
    else:
        render_landing(settings, llm)


main()
