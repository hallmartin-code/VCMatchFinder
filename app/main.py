"""Streamlit UI in the TEN Capital design: upload a deck, then work with the matches.

Run from the project root:

    streamlit run app/main.py

The landing page is the design's centered upload card. Analyzing a deck reads it, extracts
the facts and scores the investor list in one go, then switches to a wide workspace: the
key company facts on the left (editing one re-scores), the ranked match cards on the right,
and a single download for the one-page report, which is rebuilt automatically. All styling
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

import json  # noqa: E402
import threading  # noqa: E402
import uuid  # noqa: E402
from datetime import date  # noqa: E402
from html import escape  # noqa: E402
from typing import Any  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.config import CRITERIA, MatchingConfig, get_settings  # noqa: E402
from app.errors import ConfigError, MatchingEngineError  # noqa: E402
from app.intelligence.sample_email import SampleEmail, sample_email  # noqa: E402
from app.matching.taxonomy import (  # noqa: E402
    BUSINESS_MODELS,
    INVESTOR_TYPES,
    STAGE_LABELS,
    STAGES,
    SUBSECTORS,
    family_label,
)
from app.models.company import FIELD_SPECS, CompanyProfile, InvestorPreferences  # noqa: E402
from app.models.match import EVIDENCE_LABELS, TIER_LABELS, InvestorMatch  # noqa: E402
from app.models.record import UserInfo  # noqa: E402
from app.pipeline import (  # noqa: E402
    MatchOutcome,
    analyze_deck,
    build_record,
    generate_report,
    notify_results,
    output_basename,
    run_matching,
    save_outputs,
)
from app.reporting.crm_export import contact_rows, crm_csv  # noqa: E402
from app.reporting.json_exporter import record_to_json  # noqa: E402
from app.services.investor_data_service import (  # noqa: E402
    InvestorDataset,
    load_default_dataset,
    load_investor_dataset,
)
from app.services.llm_service import LLMService  # noqa: E402
from app.ui import theme  # noqa: E402
from app.utils.geography import COUNTRIES, US_STATES  # noqa: E402
from app.utils.money import parse_money  # noqa: E402
from app.utils.provenance import SOURCE_LABELS  # noqa: E402

_TYPE_KEYS = [k for k in INVESTOR_TYPES if k != "unknown"]
_UPLOAD_TYPES = ["pdf", "pptx", "ppt"]
_MISSING_MARK = "Not in deck"
#: The facts that drive matching most, shown up front; every other field sits behind "Show all fields".
_KEY_FIELDS: dict[str, str] = {
    "company_stage": "Stage",
    "subsector": "Sector",
    "raise_amount": "Raise amount",
    "headquarters": "Geography",
    "traction": "Traction",
    "business_model": "Business model",
}
_FACT_PREFIX = "fact_"
_TIER_COLORS = {"tier_1": theme.TOKENS["amber"], "tier_2": theme.TOKENS["teal"], "tier_3": theme.TOKENS["ink_300"],
                "tier_4": theme.TOKENS["ink_500"], "do_not_target": theme.TOKENS["coral"]}
_ANALYSIS_KEYS = ("session", "company", "outcome", "outcome_sig", "report_cache", "analysis_id", "user_info")


def _init_state() -> None:
    defaults: dict[str, Any] = {key: None for key in _ANALYSIS_KEYS}
    defaults.update(excluded_extra=[], contacted=[], intro_list={}, deliveries={}, downloaded=False)
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _reset_analysis() -> None:
    for key in _ANALYSIS_KEYS:
        st.session_state[key] = None
    st.session_state.update(excluded_extra=[], contacted=[], intro_list={}, deliveries={}, downloaded=False)
    for key in [k for k in st.session_state if str(k).startswith(_FACT_PREFIX)]:
        del st.session_state[key]
    st.session_state.pop("editor", None)


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


def _h(text: str | None) -> str:
    """Escape data for an HTML snippet in st.markdown (a pair of ``$`` would otherwise render as LaTeX)."""
    return escape(text or "").replace("$", "&#36;")


def _disclosures(settings: Any, llm: LLMService) -> list[str]:
    """What happens to the deck, stated from live settings so it can never drift."""
    items = [
        "Decks are processed on the server and deleted as soon as they are parsed. Accepted: "
        f"<code>.pdf</code> <code>.pptx</code> <code>.ppt</code>, up to {settings.max_upload_mb}&nbsp;MB."
    ]
    if settings.notify_configured:
        recipients = ", ".join(f"<code>{escape(r)}</code>" for r in settings.notify_recipients())
        items.append(f"Generated reports are saved on the server and a copy of each is emailed to {recipients}.")
    else:
        items.append("Generated reports are saved on the server.")
    if llm.configured:
        items.append(f"Deck text is sent to Anthropic's Claude (<code>{escape(llm.model)}</code>) "
                     "to extract facts and draft match explanations. Scores are always computed by the engine.")
    items.append("Recommendations are for human review. This app never contacts investors.")
    return items


def _file_size(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB" if size >= 1_000_000 else f"{max(size // 1000, 1)} KB"


# --------------------------------------------------------------------------- sidebar


def render_sidebar(base: MatchingConfig, llm: LLMService, settings: Any) -> MatchingConfig:
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
        st.sidebar.success(f"On: each generated report is emailed to {', '.join(settings.notify_recipients())}.")
    else:
        st.sidebar.info("Off (set TEN_NOTIFY_ENABLED and RESEND_API_KEY to enable).")
    try:
        return base.with_weights(weights)
    except (ConfigError, ValueError):
        st.sidebar.error("At least one weight must be positive; using defaults.")
        return base


# ---------------------------------------------------------------------- investor data


@st.cache_resource(show_spinner="Loading the investor list...")
def _cached_dataset(signature: tuple[tuple[str, float, int], ...]) -> InvestorDataset:
    return load_investor_dataset([Path(p) for p, _mtime, _size in signature])


def _configured_dataset(settings: Any) -> InvestorDataset:
    """The configured investor dataset(s), parsed once per file version rather than on every rerun (the TEN
    master list has ~20,000 records)."""
    signature = tuple((str(p), p.stat().st_mtime, p.stat().st_size) if p.exists() else (str(p), 0.0, -1)
                      for p in settings.investor_paths())
    return _cached_dataset(signature)


# ---------------------------------------------------------------------------- landing

_STAGE_INDEX = {"reading": 0, "extracting": 1, "scoring": 2, "explaining": 3}


def render_landing(settings: Any, llm: LLMService, base_config: MatchingConfig) -> None:
    st.markdown(theme.brand_lockup(), unsafe_allow_html=True)
    with theme.card(
        "Investor Matching Engine",
        'Pitch Deck<span class="arrow" aria-hidden="true">&rarr;</span><span class="to">Ranked Investor Matches</span>',
        lede="Upload a pitch deck and get a ranked, explained list of the investors most likely to fund "
             "the company now, delivered as a one-page TEN Capital report.",
    ):
        upload = st.file_uploader("Pitch deck (PDF, PPTX or PPT)", type=_UPLOAD_TYPES,
                                  accept_multiple_files=False, label_visibility="collapsed",
                                  key=theme.DECK_UPLOADER_KEY)
        with st.expander("Add context and your details (optional)"):
            st.text_area("Additional company information", key="notes",
                         placeholder="Anything not in the deck: current commitments, lead investor, "
                                     "updated traction…")
            st.text_input("Your name", key="u_name")
            st.text_input("Your email", key="u_email")
            st.text_input("Organization", key="u_org")

        clicked = st.button("Analyze deck", type="primary", disabled=upload is None)
        if upload is None:
            st.markdown('<p class="tc-hint">Choose a deck to continue.</p>', unsafe_allow_html=True)
        else:
            st.markdown(f'<p class="tc-hint ok">&#10003; {_h(upload.name)} · {_file_size(upload.size)} — ready to '
                        "analyze</p>", unsafe_allow_html=True)
        if clicked and upload is not None:
            _analyze(settings, llm, base_config, upload)
        st.markdown("<p class=\"tc-oneline\">Your deck is deleted after reading; reports are saved for TEN's "
                    "review.</p>", unsafe_allow_html=True)
        with st.expander("How it works"):
            theme.disclosure(_disclosures(settings, llm))
    theme.footer()


def _analyze(settings: Any, llm: LLMService, base_config: MatchingConfig, upload: Any) -> None:
    """Read the deck, extract the facts and score the investor list, showing each stage."""
    dataset = _configured_dataset(settings)
    stages = ["Reading slides", "Extracting facts", f"Scoring {len(dataset.investors):,} investors",
              "Writing explanations"]
    slot = st.empty()

    def progress(stage: str) -> None:
        slot.markdown(theme.progress_html(stages, _STAGE_INDEX[stage]), unsafe_allow_html=True)

    try:
        session = analyze_deck(settings=settings, upload=(upload.name, upload.getvalue()),
                               notes_text=st.session_state.get("notes", ""), llm=llm, progress=progress)
        outcome = run_matching(session.company, InvestorPreferences(), dataset, base_config, llm=llm,
                               deck_scanned=session.extraction.deck_scanned, progress=progress)
    except MatchingEngineError as exc:
        slot.empty()
        st.error(str(exc))
        return
    slot.markdown(theme.progress_html(stages, len(stages)), unsafe_allow_html=True)
    user = _user()
    _reset_analysis()
    # outcome_sig stays None: the workspace adopts this outcome for its first set of inputs.
    st.session_state.update(session=session, company=session.company, outcome=outcome,
                            analysis_id=str(uuid.uuid4()), user_info=user)
    st.rerun()


# ------------------------------------------------------------------------ company facts


def _geo_options() -> list[str]:
    """US states, then countries: every option resolves to a location the scoring understands."""
    states = [f"{name.title()}, United States" for name in US_STATES]
    countries: dict[str, str] = {}
    for name, (iso, _macro) in COUNTRIES.items():
        countries.setdefault(iso, name)
    return states + sorted(name.title() for name in countries.values())


def _subsector_label(key: str) -> str:
    sub = SUBSECTORS[key]
    return f"{sub.label} ({family_label(sub.family)})"


def _fact_input(name: str, label: str, company: CompanyProfile) -> Any:
    """One key-fact widget; returns the override to apply, or None when the deck value stands."""
    item = company.field(name)
    key = _FACT_PREFIX + name
    if name == "company_stage":
        options: list[Any] = [None, *STAGES]
        choice = st.selectbox(label, options, index=options.index(item.value) if item.value in STAGES else 0,
                              format_func=lambda k: "Choose a stage" if k is None else STAGE_LABELS[k], key=key)
        return choice if choice is not None and choice != item.value else None
    if name == "subsector":
        options = [None, *sorted(SUBSECTORS, key=_subsector_label)]
        choice = st.selectbox(label, options, index=options.index(item.value) if item.value in SUBSECTORS else 0,
                              format_func=lambda k: "Choose a sector" if k is None else _subsector_label(k), key=key)
        return choice if choice is not None and choice != item.value else None
    if name == "headquarters":
        places = _geo_options()
        deck_value = item.value if item.known else None
        options = [deck_value, *[p for p in places if p != deck_value]] if deck_value else [None, *places]
        choice = st.selectbox(label, options, index=0, key=key,
                              format_func=lambda p: "Choose a location" if p is None
                              else (f"{p} (from deck)" if p == deck_value else p))
        return choice if choice is not None and choice != deck_value else None
    if name == "business_model":
        current = [k for k in (item.value or []) if k in BUSINESS_MODELS]
        chosen = st.multiselect(label, list(BUSINESS_MODELS), default=current, format_func=BUSINESS_MODELS.get,
                                placeholder="Choose one or more", key=key)
        return chosen if chosen and sorted(chosen) != sorted(current) else None
    if name == "traction":
        text = st.text_area(label, value=item.value or "", height=90, key=key,
                            placeholder="e.g. 12 paying hospitals, $40k MRR, 3 pilots")
        return text if text.strip() and text.strip() != (item.value or "").strip() else None
    # raise_amount
    shown = company.display_value(name) if item.known else ""
    text = st.text_input(label, value=shown, placeholder="e.g. $6M", key=key)
    if not text.strip() or text.strip() == shown:
        return None
    if parse_money(text) is None:
        st.caption("Couldn't read that amount — try $6M or 6,000,000.")
        return None
    return text


def _other_fields_frame(company: CompanyProfile) -> pd.DataFrame:
    rows = []
    for name, spec in FIELD_SPECS.items():
        if not spec.reviewable or name in _KEY_FIELDS:
            continue
        item = company.field(name)
        rows.append({"key": name, "Field": spec.label, "Value": company.display_value(name),
                     "Source": SOURCE_LABELS[item.source] if item.known else "Missing"})
    return pd.DataFrame(rows)


def render_facts(company: CompanyProfile) -> dict[str, Any]:
    """The facts that drive matching, missing ones first; returns the user's overrides."""
    session = st.session_state.session
    ex = session.extraction
    overrides: dict[str, Any] = {}
    missing = [n for n in _KEY_FIELDS if not company.field(n).known]
    for name in sorted(_KEY_FIELDS, key=lambda n: n not in missing):
        label = _KEY_FIELDS[name]
        if name in missing and not st.session_state.get(_FACT_PREFIX + name):
            st.markdown(f'<p class="tc-missing">We couldn\'t find your {escape(label.lower())} – add it?</p>',
                        unsafe_allow_html=True)
        value = _fact_input(name, label, company)
        if value is not None:
            overrides[name] = value

    with st.expander("Show all fields"):
        st.caption("Edit any Value; edited fields are labeled user-supplied.")
        frame = _other_fields_frame(company)
        edited = st.data_editor(frame, key="editor", hide_index=True, disabled=["Field", "Source"],
                                column_config={"key": None})
        for (_i, before), (_j, after) in zip(frame.iterrows(), edited.iterrows(), strict=True):
            new = str(after["Value"]).strip()
            if new and new != str(before["Value"]).strip() and new != _MISSING_MARK:
                overrides[before["key"]] = new
    deck = (f"{ex.deck_filename} · {ex.deck_format.upper()} · {ex.deck_pages} pages/slides"
            + (" · scanned" if ex.deck_scanned else ""))
    if ex.warnings:
        with st.expander(f"Notes from reading the deck ({len(ex.warnings)})"):
            st.caption(deck)
            for warning in ex.warnings:
                st.caption(theme.safe_md(warning))
    else:
        st.caption(deck)
    return overrides


def render_preferences(settings: Any) -> tuple[InvestorPreferences, InvestorDataset]:
    label = lambda key: INVESTOR_TYPES[key]  # noqa: E731
    st.caption("Exclusions become hard filters; preferences add a small, visible ranking bonus.")
    preferred = st.multiselect("Preferred investor types", _TYPE_KEYS, format_func=label, key="p_pref")
    excluded = st.multiselect("Excluded investor types", _TYPE_KEYS, format_func=label, key="p_excl")
    lead = st.radio("Lead investor required?", ["Auto (from deck)", "Yes", "No"], horizontal=True, key="p_lead")
    geos = st.text_input("Preferred geographies (comma-separated)", key="p_geo")
    use_default = st.checkbox("Include the configured investor dataset", value=True, key="p_default")
    uploads = st.file_uploader("Additional investor datasets (CSV/JSON)", type=["csv", "json"],
                               accept_multiple_files=True, key="p_uploads")
    if uploads:
        dataset = load_default_dataset(settings, [(f.name, f.getvalue()) for f in uploads],
                                       include_defaults=use_default)
    elif use_default:
        dataset = _configured_dataset(settings)
    else:
        dataset = load_default_dataset(settings, [], include_defaults=False)
    st.caption(f"Investor data: {dataset.label()} · {len(dataset.investors):,} records")
    for warning in dataset.warnings:
        (st.error if "SYNTHETIC" in warning else st.caption)(theme.safe_md(warning))
    excluded_names = st.multiselect("Excluded investors", sorted({i.name for i in dataset.investors}),
                                    key="p_excl_names")
    extra = st.session_state.excluded_extra
    if extra:
        st.caption("Excluded from the results: " + ", ".join(extra))
        if st.button("Clear result exclusions"):
            st.session_state.excluded_extra = []
            st.rerun()
    preferences = InvestorPreferences(
        preferred_types=preferred, excluded_types=excluded,
        preferred_geographies=[g.strip() for g in geos.split(",") if g.strip()],
        excluded_investors=sorted(set(excluded_names) | set(extra)),
        lead_required={"Auto (from deck)": None, "Yes": True, "No": False}[lead],
    )
    return preferences, dataset


# ------------------------------------------------------------------- scoring and report


def _signature(overrides: dict[str, Any], preferences: InvestorPreferences, config: MatchingConfig,
               dataset: InvestorDataset) -> str:
    """Everything the ranking depends on; a change means re-score."""
    return json.dumps([sorted((k, str(v)) for k, v in overrides.items()), preferences.model_dump(mode="json"),
                       config.weights.as_record(), dataset.label(), len(dataset.investors)],
                      sort_keys=True, default=str)


def _current_outcome(company: CompanyProfile, preferences: InvestorPreferences, dataset: InvestorDataset,
                     config: MatchingConfig, sig: str) -> MatchOutcome:
    """The outcome for these inputs: the analysis-time one first, then a deterministic re-score on change."""
    outcome: MatchOutcome | None = st.session_state.outcome
    if outcome is not None and st.session_state.outcome_sig in (None, sig):
        st.session_state.outcome_sig = sig
        return outcome
    session = st.session_state.session
    with st.spinner(f"Re-scoring {len(dataset.investors):,} investors..."):
        outcome = run_matching(company, preferences, dataset, config,
                               deck_scanned=bool(session and session.extraction.deck_scanned))
    st.session_state.update(outcome=outcome, outcome_sig=sig)
    return outcome


def _current_report(outcome: MatchOutcome, config: MatchingConfig, settings: Any, sig: str) -> tuple[Any, Any]:
    """(record, report) for these inputs, rebuilt only when the inputs change (about 0.1 s)."""
    cached = st.session_state.report_cache
    if cached and cached[0] == sig:
        return cached[1], cached[2]
    record = build_record(outcome, config=config, settings=settings, session=st.session_state.session,
                          user=_user(), analysis_id=st.session_state.analysis_id)
    report, record = generate_report(record, organization=settings.report_organization)
    st.session_state.report_cache = (sig, record, report)
    return record, report


def _deliver(sig: str, record: Any, pdf_bytes: bytes, settings: Any) -> None:
    """Email and save this version of the report in a background thread (once per version)."""
    deliveries: dict[str, dict[str, Any]] = st.session_state.deliveries
    if sig in deliveries:
        return
    holder: dict[str, Any] = {"done": False}
    deliveries[sig] = holder

    def work() -> None:
        try:
            sent, updated = notify_results(record, pdf_bytes, settings=settings)
            holder["notification"] = sent
            holder["saved"] = save_outputs(updated, pdf_bytes, settings.resolve(settings.output_dir))
        except Exception as exc:  # noqa: BLE001 - a background job must report its failure, never crash
            holder["error"] = str(exc)
        finally:
            holder["done"] = True

    holder["thread"] = threading.Thread(target=work, name="tc-deliver", daemon=True)
    holder["thread"].start()


def _on_download(sig: str, record: Any, pdf_bytes: bytes, settings: Any) -> None:
    st.session_state.downloaded = True
    _deliver(sig, record, pdf_bytes, settings)


def _delivery_status(sig: str, settings: Any) -> None:
    holder = st.session_state.deliveries.get(sig)
    if holder is None:
        return
    if not holder["done"]:
        st.caption("Saving a copy for TEN's review...")
    elif holder.get("error"):
        st.caption(f"The report copy for TEN could not be saved: {holder['error']}")
    else:
        sent = holder.get("notification")
        text = "Saved for TEN's review."
        if sent is not None and settings.notify_configured:
            text += " " + sent.summary()
        st.caption(text)


# ---------------------------------------------------------------------------- results


def _first_sentence(text: str, limit: int = 220) -> str:
    sentence = (text or "").split(". ")[0].strip().rstrip(".")
    return (sentence[: limit - 1].rstrip() + "…") if len(sentence) > limit else sentence + "."


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


def _toggle_contacted(investor_id: str) -> None:
    contacted = set(st.session_state.contacted)
    st.session_state.contacted = sorted(contacted ^ {investor_id})


def _toggle_intro(investor_id: str, rows: list[dict[str, Any]]) -> None:
    """Add or remove an investor; the list keeps its spreadsheet rows so they survive later re-scores."""
    intro: dict[str, list[dict[str, Any]]] = dict(st.session_state.intro_list)
    if investor_id in intro:
        del intro[investor_id]
    else:
        intro[investor_id] = rows
    st.session_state.intro_list = intro


def _exclude(name: str) -> None:
    st.session_state.excluded_extra = sorted(set(st.session_state.excluded_extra) | {name})


def _render_card(rank: int, match: InvestorMatch, email: SampleEmail, rows: list[dict[str, Any]]) -> None:
    investor = match.kind == "investor"
    contacted = match.investor_id in st.session_state.contacted
    listed = match.investor_id in st.session_state.intro_list
    with st.container(border=True, key=f"tc-match-{rank}"):
        badges = [theme.badge(TIER_LABELS[match.outreach_priority], _TIER_COLORS[match.outreach_priority])]
        if contacted:
            badges.append(theme.badge("Already contacted", theme.TOKENS["ink_500"]))
        if listed:
            badges.append(theme.badge("On intro list", theme.TOKENS["teal"]))
        name = _h(match.investor_name) + (" (profile)" if not investor else "")
        concern = (f'<p class="tc-concern"><b>Top concern:</b> {_h(match.likely_concerns[0])}</p>'
                   if match.likely_concerns else "")
        st.markdown(
            f'<div class="tc-card-head"><div><div class="tc-card-name"><span class="rank">{rank}.</span>{name}</div>'
            f'<div class="tc-card-meta">{_h(match.investor_type_label)} · fit {match.fit_score:.0f} · '
            f'{_h(match.confidence)} confidence</div></div><div class="tc-badges">{"".join(badges)}</div></div>'
            f'<p class="tc-why">{_h(_first_sentence(match.rationale))}</p>{concern}',
            unsafe_allow_html=True)
        with st.expander("See scoring"):
            _match_details(match)
        with st.expander(f"Sample email for this {match.investor_type_label.lower()}"):
            primary = rows[0] if rows else None
            if primary and (primary["Person name"] or primary["Email"]):
                to = primary["Person name"] + (f" <{primary['Email']}>" if primary["Email"] else "")
                more = f" (+{len(rows) - 1} more in the Pipedrive download)" if len(rows) > 1 else ""
                st.markdown(f"**To:** {theme.safe_md(to.strip())}{more}")
            st.markdown(f"**Subject:** {theme.safe_md(email.subject)}")
            st.text(email.body)
            st.caption("A draft to review and edit: fill in the [bracketed] parts before sending. "
                       "This app never sends it.")
        if not investor:
            theme.copy_button("Copy sample email", email.text())
            return
        cols = st.columns([1.35, 1.1, 1.15, 0.8])
        with cols[0]:
            theme.copy_button("Copy sample email", email.text())
        cols[1].button("Contacted ✓" if contacted else "Mark contacted",
                       help="Mark as already contacted (click again to undo)", key=f"contacted_{match.investor_id}",
                       on_click=_toggle_contacted, args=(match.investor_id,), width="stretch")
        cols[2].button("On intro list ✓" if listed else "Add to intro list",
                       help="Collect intros to export for Pipedrive (click again to remove)",
                       key=f"intro_{match.investor_id}", on_click=_toggle_intro, args=(match.investor_id, rows),
                       width="stretch")
        cols[3].button("Exclude", help="Exclude this investor and re-score", key=f"exclude_{match.investor_id}",
                       on_click=_exclude, args=(match.investor_name,), width="stretch")


def _crm_download(label: str, rows: list[dict[str, Any]], company: CompanyProfile, suffix: str, *,
                  key: str, width: str = "content") -> None:
    data = crm_csv(rows, contacted=st.session_state.contacted, intro_list=st.session_state.intro_list)
    st.download_button(label, data, file_name=f"{company.display_name} {suffix}.csv", mime="text/csv",
                       on_click="ignore", key=key, width=width)


def render_results(outcome: MatchOutcome, config: MatchingConfig, dataset: InvestorDataset,
                   company: CompanyProfile, settings: Any, sig: str) -> None:
    rows = outcome.run.top(config.ranking.top_n)
    needs_lead = company.needs_lead(outcome.preferences.lead_required)
    emails: dict[str, SampleEmail] = {}
    crm_rows: dict[str, list[dict[str, Any]]] = {}
    for match in rows:
        inv = outcome.records.get(match.investor_id)

        def draft(contact: Any, match: InvestorMatch = match, inv: Any = inv) -> SampleEmail:
            return sample_email(match, company, inv=inv, needs_lead=needs_lead, today=outcome.today, contact=contact)

        emails[match.investor_id] = draft(inv.primary_contact() if inv else None)
        if match.kind == "investor":  # profiles are not organizations a CRM can hold
            crm_rows[match.investor_id] = contact_rows(match, company, inv, draft)
    everyone = [row for investor_rows in crm_rows.values() for row in investor_rows]

    # The two downloads, stacked: the report, then the Pipedrive spreadsheet.
    try:
        record, report = _current_report(outcome, config, settings, sig)
    except MatchingEngineError as exc:
        st.error(f"The report could not be generated: {exc}")
        record = report = None
    if record is not None and report is not None:
        base = output_basename(record)
        st.download_button("Download the one-page report (PDF)", report.pdf_bytes, file_name=f"{base}_report.pdf",
                           mime="application/pdf", type="primary", on_click=_on_download,
                           args=(sig, record, report.pdf_bytes, settings), width="stretch")
    if everyone:
        _crm_download("Download all contacts and emails for Pipedrive (CSV)", everyone, company,
                      "Pipedrive contacts and emails", key="crm_all", width="stretch")
        people = sum(1 for row in everyone if row["Person name"] or row["Email"])
        if people:
            st.caption(f"Pipedrive spreadsheet: {people} contacts at {len(crm_rows)} target investors, one row "
                       "per contact with the sample email addressed to that person.")
        else:
            st.caption(f"Pipedrive spreadsheet: {len(crm_rows)} target investors. The investor list has no "
                       "contacts for them, so each row has blank contact columns and a [First name] placeholder.")
    if report is not None:
        if report.reductions:
            st.caption("To stay on one page: " + "; ".join(report.reductions) + ".")
        _delivery_status(sig, settings)

    named = [m for m in outcome.run.matches if m.outreach_priority != "do_not_target"]
    tier1 = sum(1 for m in named if m.outreach_priority == "tier_1")
    top_fit = f"{rows[0].fit_score:.0f}" if rows else "-"
    theme.tiles([("Named matches", str(len(named)), theme.TOKENS["teal"]),
                 ("Tier 1 · contact now", str(tier1), theme.TOKENS["amber"]),
                 ("Removed by filters", str(len(outcome.run.rejected)), theme.TOKENS["coral"]),
                 ("Top fit score", top_fit, theme.TOKENS["ink_100"])])
    if dataset.any_synthetic:
        st.error("These named investors come from a SYNTHETIC test dataset and are fictitious.")
    st.info(f"**Recommended next action:** {theme.safe_md(outcome.next_action)}")

    intro = st.session_state.intro_list
    if intro:
        theme.label(f"Intro list ({len(intro)})")
        st.caption(", ".join(investor_rows[0]["Organization name"] for investor_rows in intro.values()))
        _crm_download("Download intro list for Pipedrive (CSV)",
                      [row for investor_rows in intro.values() for row in investor_rows], company,
                      "Pipedrive intro list", key="crm_intro")

    for rank, match in enumerate(rows, start=1):
        _render_card(rank, match, emails[match.investor_id], crm_rows.get(match.investor_id, []))

    with st.expander("Ideal investor profile"):
        st.markdown("\n".join(f"- {theme.safe_md(point)}" for point in outcome.iip.summary_points))
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
    if record is not None:
        with st.expander("Machine-readable record (JSON)"):
            st.download_button("Download JSON", record_to_json(record), file_name=f"{output_basename(record)}.json",
                               mime="application/json", on_click="ignore")
    for gap in outcome.gaps:
        st.caption(f"Data quality: {theme.safe_md(gap)}")
    for note in outcome.llm_notes:
        st.caption(theme.safe_md(note))


# -------------------------------------------------------------------------- workspace


def _status_chips(settings: Any, llm: LLMService) -> str:
    chips = [("Claude · on" if llm.configured else "Claude · off", "on" if llm.configured else "off"),
             ("Email · " + ", ".join(settings.notify_recipients()) if settings.notify_configured else "Email · off",
              "on" if settings.notify_configured else "off"),
             ("Scores · deterministic", "on")]
    return theme.chips_html(chips)


def render_workspace(settings: Any, llm: LLMService, base_config: MatchingConfig) -> None:
    config = render_sidebar(base_config, llm, settings)
    left, right = st.columns([5, 1], vertical_alignment="center")
    with left:
        theme.topbar(_status_chips(settings, llm))
    with right:
        if st.button("New analysis", key="new_analysis"):
            _reset_analysis()
            st.rerun()
    done = 3 if st.session_state.downloaded else 1
    theme.stepper(3 if st.session_state.downloaded else 2, done)
    title = st.empty()

    base: CompanyProfile = st.session_state.company
    facts_col, results_col = st.columns([2, 3], gap="large")
    with facts_col, theme.panel("Company facts", "What we read from the deck",
                                "Fix anything that's wrong; the matches re-score as you edit."):
        overrides = render_facts(base)
        with st.expander("Targeting and investor data"):
            preferences, dataset = render_preferences(settings)
    company = base.apply_overrides(overrides) if overrides else base
    title.markdown(f'<h1 class="tc-h1">{escape(company.display_name)}</h1>', unsafe_allow_html=True)
    sig = _signature(overrides, preferences, config, dataset)

    with results_col, theme.panel("Investor matches", "Ranked investor matches", "Scored on nine weighted "
                                  "criteria, filtered for hard incompatibilities, and ordered by outreach "
                                  "priority.", active=True):
        try:
            outcome = _current_outcome(company, preferences, dataset, config, sig)
        except MatchingEngineError as exc:
            st.error(str(exc))
            return
        render_results(outcome, config, dataset, company, settings, sig)
    # The first version of every analysis goes to TEN; later versions go when downloaded.
    if not st.session_state.deliveries and st.session_state.report_cache:
        _sig, record, report = st.session_state.report_cache
        _deliver(sig, record, report.pdf_bytes, settings)
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
                    'TEN Capital<span class="arrow" aria-hidden="true">&middot;</span>'
                    '<span class="to">Investor Matching</span>',
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
                 upload_hint="Drag and drop your deck here\nor click to browse  ·  .pdf  ·  .pptx  ·  .ppt  ·  "
                             f"up to {settings.max_upload_mb} MB")
    theme.page_script()
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
        render_landing(settings, llm, base_config)


main()
