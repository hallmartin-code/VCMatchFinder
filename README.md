# TEN Capital Investor Matching Engine

Upload an investor pitch deck (PDF or PowerPoint), review what was extracted, and get a
ranked, explained list of the investors most likely to fund the company **now** — as a
polished one-page **TEN Capital Investor Matching Report** (PDF) plus a validated,
machine-readable JSON record.

The engine is built around a few non-negotiable rules:

| Principle | How it is enforced |
|---|---|
| Evidence over speculation | Investor names, deals and check sizes only ever come from an investor dataset. Nothing is generated. |
| Separate facts from inference | Every company value is tagged `pitch_deck` (with slide/page), `user_supplied`, `external_source`, `inference`, or `missing`. |
| Explain every score | Scores are computed by code from nine rules. Each criterion keeps its raw score, weighted score, evidence, source, evidence level, confidence and missing information. |
| Missing data reduces confidence | A criterion that cannot be judged scores 40/100 (below any positive evidence) and contributes zero confidence. |
| Categories over fabricated names | When named-investor evidence is thin, the engine ranks investor **archetypes** (e.g. "Diagnostics-focused venture capital fund"), clearly labeled as profiles. |
| Human review before outreach | The application never emails, messages or otherwise contacts anyone. Outreach consent is recorded separately and only changes the report's caveat. |

---

## Contents

1. [Quick start](#quick-start)
2. [Architecture](#architecture)
3. [Installation and configuration](#installation-and-configuration)
4. [Running the application](#running-the-application)
5. [Supported deck formats](#supported-deck-formats)
6. [Investor data format](#investor-data-format)
7. [Matching methodology](#matching-methodology)
8. [Scoring methodology](#scoring-methodology)
9. [PDF report](#pdf-report)
10. [JSON record](#json-record)
11. [Privacy, consent and security](#privacy-consent-and-security)
12. [Testing](#testing)
13. [Known limitations](#known-limitations)

---

## Quick start

```bash
pip install -r requirements.txt
python sample_data/make_samples.py          # regenerate the synthetic sample decks (optional)
streamlit run app/main.py                   # web UI
# or, headless:
python -m app.cli sample_data/lumora_diagnostics_series_a.pdf \
    --consent-analysis --consent-storage --out output
```

The bundled investor dataset (`sample_data/investors_synthetic.json`) is **synthetic**: every
firm, deal and URL in it is fictitious. It exists for testing and demonstration, and every
report generated from it carries a red "SYNTHETIC test dataset" banner. Load your own
dataset (see [Investor data format](#investor-data-format)) for real work.

---

## Architecture

```text
app/
  main.py                 Streamlit UI (Upload → Review → Preferences → Match → Export)
  cli.py                  Headless command line
  pipeline.py             Orchestration: analyze_deck → run_matching → build_record → generate_report
  config.py               Settings (env / .env) and MatchingConfig (weights, thresholds)
  errors.py               User-facing exception hierarchy
  ingestion/              pdf_parser.py, ppt_parser.py, deck_parser.py, models.py
  extraction/             patterns.py (cue engine), financial_, traction_, company_extractor.py
  models/                 company.py, investor.py, match.py, consent.py, record.py (Pydantic)
  matching/               taxonomy.py, investor_profile.py, filters.py, scoring.py,
                          confidence.py, ranking.py
  intelligence/           outreach.py, concerns.py, analyzer.py
  reporting/              pdf_generator.py, report_layout.py, template.py, theme.py, json_exporter.py
    templates/            investor_matching_report.template.json (report structure - source of truth)
  data/                   investor_repository.py (JSON, CSV, remote/CRM base, composite)
  services/               investor_data_service.py, llm_service.py (optional)
  utils/                  provenance.py, validation.py, logging.py, money.py, geography.py, text.py
  assets/                 Open Sans (SIL OFL) and the TEN Capital footer logo
config/matching.toml      Criterion weights, unknown-data policy, tier thresholds
docs/                     Report structure template (Markdown spec + blank rendered PDF)
sample_data/              Synthetic investor dataset and sample-deck generator
tests/                    pytest suite (decks are generated fresh per test session)
```

Data flows one way, and each layer is replaceable:

```text
deck ─► ingestion ─► DeckDocument ─► extraction ─► CompanyProfile (Sourced fields)
                                                        │   ▲ user corrections
                                                        ▼   │
investor repositories ─► InvestorRecord[] ─► filters ─► scoring ─► confidence ─► ranking
                                                                       │
                              intelligence (why / concerns / angle / materials)
                                                                       ▼
                                      TenCapitalRecord (JSON) ─► one-page PDF
```

---

## Installation and configuration

**Python:** 3.11 or newer (developed and tested on 3.14).

**Dependencies** (`requirements.txt`): `pydantic`, `pydantic-settings`, `python-dotenv`,
`pymupdf`, `python-pptx`, `reportlab`, `streamlit`, `anthropic` (only used when the optional
external model is enabled), plus `pytest` for development.

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows (use source .venv/bin/activate elsewhere)
pip install -r requirements.txt
copy .env.example .env                               # then edit as needed
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TEN_LLM_ENABLED` | `false` | Allow the optional external model. The engine is fully functional without it. |
| `ANTHROPIC_API_KEY` | — | API key for the external model. Never hard-code it. |
| `TEN_LLM_MODEL` | `claude-opus-5` | Model used when the external model is enabled. |
| `TEN_LLM_TIMEOUT_SECONDS` | `60` | Per-request timeout. |
| `TEN_INVESTOR_DATA_PATHS` | `sample_data/investors_synthetic.json` | Semicolon-separated CSV/JSON datasets loaded by default. |
| `TEN_MAX_UPLOAD_MB` | `50` | Deck upload limit. |
| `TEN_OUTPUT_DIR` | `output` | Where outputs are written (only with storage consent). |
| `TEN_WEIGHTS_FILE` | `config/matching.toml` | Matching configuration. |
| `TEN_LOG_LEVEL` | `INFO` | Log level. Logs never contain deck text. |
| `TEN_REPORT_ORGANIZATION` | `TEN Capital Network` | Name in the report footer. |

### Matching configuration (`config/matching.toml`)

Weights (normalized to 100), the unknown-data score, the confidence blend, the preference
bonus, verification age, and tier thresholds. Every key is optional. The UI sidebar can
override weights per run.

---

## Running the application

### Streamlit UI

```bash
streamlit run app/main.py
```

The UI uses the TEN Capital dark-navy design: brand lockup, tri-colour mark, and Sora / Inter /
JetBrains Mono.
- **Landing:** a centered upload card.
- **Workspace:** once a deck is analyzed, a wide workspace with a five-step indicator and one
  panel per step.
- **Where the styling lives:** all of it is in `app/ui/theme.py`. Fonts are self-hosted from
  `app/static/fonts` (SIL OFL) through `[[theme.fontFaces]]` in `.streamlit/config.toml`, so no
  font CDN is contacted.
- **Favicon:** the TEN Capital icon lives in the public directory as `app/static/favicon.png`
  (served at `/app/static/favicon.png`). The app references it in three places:
  - `st.set_page_config(page_icon=…)` in `app/main.py`;
  - the `<link rel="icon">` tags in `app/ui/theme.py`;
  - `app/static/site.webmanifest`.

  The 16–192 px PNGs, `favicon.ico`, the Apple touch icon (`favicon-180.png`) and the maskable
  app icon are all derived from it. To change the icon, replace `favicon.png` and regenerate the
  other sizes.
- **Disclosures:** the upload limit, email recipient and Claude model shown on the landing card
  are read from live settings.

1. **Upload deck and consent.** Choose a PDF/PPTX/PPT and optionally add company information
   (treated as user-supplied and preferred over deck text). Analysis consent is required;
   storage, outreach and external-processing consent are separate checkboxes.
2. **Review and correct.** Every extracted field is shown with its source, slide/page and
   confidence. Fields that materially affect the ranking and were not found are marked
   *Missing – enter manually*. Edited values become `user_supplied`.
3. **Preferences and data.** Preferred and excluded investor types, preferred geographies,
   excluded investors, whether a lead is required, and additional investor datasets.
4. **Run matching and review.** The Ideal Investor Profile, ranked matches, per-criterion
   score breakdowns, the hard-filter audit, and **Recalculate** / **Exclude investor** actions.
   The sidebar adjusts weights.
5. **Export.** **Generate PDF** and **Download JSON record**. With storage consent the
   files are also written to `TEN_OUTPUT_DIR`.

### Command line

```bash
python -m app.cli DECK [--investors FILE ...] [--no-default-investors]
                  [--prefer-type TYPE ...] [--exclude-type TYPE ...] [--prefer-geo GEO ...]
                  [--exclude-investor NAME ...] [--lead-required auto|yes|no]
                  [--notes TEXT] [--weights TOML] [--today YYYY-MM-DD]
                  --consent-analysis [--consent-storage --out DIR]
                  [--consent-outreach] [--consent-external]
```

Exit codes: `0` success, `1` actionable error (bad file, unreadable dataset), `2` missing consent.

### Deploying to Railway

The repository deploys to Railway as-is. It uses `railway.json` (Nixpacks build, Streamlit
start command, `/_stcore/health` health check), `Procfile`, `.python-version` (3.12) and
`requirements.txt`, which is pinned to the versions verified on Python 3.12.

```bash
railway up --service investor-matching-engine      # build and deploy from this folder
railway logs --service investor-matching-engine    # follow the running app
```

Secrets are Railway service variables, never files. `.env` is git-ignored and listed in
`.railwayignore`, so it is never uploaded.

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Claude Console API key (set with `railway variable set ANTHROPIC_API_KEY --stdin`) |
| `TEN_LLM_ENABLED` | `true` — Claude extracts deck fields and writes the match narratives |
| `TEN_LLM_MODEL` | `claude-opus-5` |
| `TEN_LLM_TIMEOUT_SECONDS` | `300` |
| `TEN_APP_PASSWORD` | Access password for the web app — **required** for a public URL, since the app spends your API credits and handles confidential decks |
| `TEN_MAX_UPLOAD_MB` | `50` |

| `TEN_NOTIFY_ENABLED` | `true` — email each generated report to TEN Capital |
| `RESEND_API_KEY` | Resend API key (set with `railway variable set RESEND_API_KEY --stdin`) |
| `TEN_NOTIFY_TO` | `Info@tencapital.group` (comma-separated for several) |
| `TEN_NOTIFY_FROM` | Sender on a Resend-verified domain, e.g. `TEN Capital Investor Matching <reports@tencapital.group>` |

**Automated results email.** Every time a report is generated (web UI or CLI), a summary is
emailed to `TEN_NOTIFY_TO` through Resend. It contains the company snapshot, ideal investor
profile, ranked matches, next action and data quality, with the one-page PDF and JSON record
attached.
- **Internal only.** Recipients come only from configuration; nothing in the UI can change
  them, and no investor is ever emailed.
- **Follows storage consent.** An emailed copy is a retained copy, so an email is sent only
  when the user grants storage consent. Set `TEN_NOTIFY_REQUIRES_STORAGE_CONSENT=false` to
  email every run.
- **Never fails a report.** Failures are retried, then recorded under `notification` in the
  JSON record. The CLI's `--no-email` skips the email for one run.

Notes:

- **Claude is used only with consent.** Users still tick the external-processing consent
  box per run before deck text is sent to Claude. Without it, the app runs fully
  deterministically.
- **Railway's disk is ephemeral.** Download the PDF and JSON from the Export step; files
  written under storage consent do not survive a redeploy.
- **Changing the password.** Set a new value in the Railway dashboard (Variables); the
  service redeploys automatically.

---

## Supported deck formats

| Format | Handling |
|---|---|
| PDF | Text page by page with PyMuPDF, plus table detection. Citations read `Page N`. Encrypted, empty, and corrupt files produce actionable errors. |
| PPTX | Text frames, grouped shapes, tables, chart titles/series, and speaker notes with python-pptx. Citations read `Slide N` (notes: `Slide N (speaker notes)`). |
| PPT (97–2003) | Converted with LibreOffice when installed; otherwise the user is told to *Save As .pptx* or export to PDF. |
| Scanned / image-only | Detected when at least 60% of pages have no text layer. The user is told which fields need manual entry. With the external model enabled and consented, page images can be read by the model; those values are labeled unverified and low-confidence. |

---

## Investor data format

Investor data sits behind the `InvestorRepository` interface (`app/data/investor_repository.py`):

* `JsonInvestorRepository` — a list of investors, or `{"dataset": {...}, "investors": [...]}`.
  Set `"dataset": {"is_synthetic": true}` for test data.
* `CsvInvestorRepository` — one investor per row. Column names are matched flexibly
  (e.g. `Investor`/`Name`/`Fund`, `Check Size`, `Stage Focus`, `Sector Focus`, `Leads Rounds`,
  `Lead History`, `Fund Status`, `Portfolio`).
* `RemoteInvestorRepository` — base class for future API or TEN Capital CRM integrations.
  Implement `fetch_rows()`; normalization, validation and de-duplication are shared.
* `CompositeInvestorRepository` — merges sources and de-duplicates by normalized name or
  website domain, so "ALDERMOOR HEALTH VENTURES, LLC" and "Aldermoor Health Ventures"
  become one record. The richer record wins, and list fields are unioned.

The core application does not depend on any paid data provider.

### Fields

| Field | Example | Notes |
|---|---|---|
| `name` *(required)* | `"Aldermoor Health Ventures"` | |
| `investor_type` | `"Venture capital"`, `"Corporate / CVC"`, `"Angel group"` | Mapped to 18 canonical types. |
| `website`, `headquarters` | `"https://…"`, `"Boston, MA"` | |
| `investment_geographies` / `excluded_geographies` | `["United States"]`, `["Europe"]` | Countries, US states/regions, macro regions, `Global`. |
| `sector_preferences` / `excluded_sectors` | `["diagnostics", "medical devices"]` | Mapped to 21 subsectors in 5 families; `generalist` allowed. |
| `stage_preferences` | `["Seed", "Series A"]` or `"Seed–Series B"` | |
| `check_size` or `min_check` / `max_check` / `typical_check` | `"$1.5M-$4M"` | |
| `lead_capability` | `"Yes"`, `"Sometimes"`, `"No - follows a lead"` | |
| `historical_investments` | `[{"company", "sector", "stage", "year", "led", "source_url"}]` | Or free text: `"Led Vessl Dx Series A 2025"`. |
| `portfolio_companies` | `["Cortexa Diagnostics"]` | Used for portfolio-conflict checks. |
| `last_known_investment` | `"2026-05-12"` | Stale activity lowers priority. |
| `strategic_capabilities` | `["regulatory strategy", "hospital network introductions"]` | |
| `business_model_preferences` / `excluded_business_models` | `["device + consumables"]` | |
| `min_revenue`, `fund_size`, `fund_vintage` | `"$10M"`, `"$180M"`, `2024` | |
| `fund_status` | `"active"` or free text such as `"Fund III raising, no dry powder"` | `between_funds` and `inactive` are hard-filtered. |
| `source_urls`, `last_verified` | `["https://…"]`, `"2026-07-15"` | Required for **Verified** evidence. |

Incomplete records are kept. Missing values stay missing and score as unknown.

---

## Matching methodology

1. **Normalize the company.** A cue-pattern extractor reads the deck. Its cues carry guards so
   that, for example, a *revenue target* is never read as revenue and a *raised-to-date* figure
   is never read as the round size. Sector, business model, product stage and strategic needs
   are **classified**, so they are labeled as inference. User notes and corrections outrank
   the deck.
2. **Ideal Investor Profile.** The engine derives the target stage, lead/follow check ranges
   (a lead takes 25–60% of the round; a meaningful follower 5–25%), sector specialization,
   geography, lead requirement, strategic-versus-financial fit, and required technical,
   commercial, regulatory and cross-border expertise. It recommends investor categories that
   fit the stage, check size, sector and revenue floor, and records why other categories
   were excluded.
3. **Hard filters.** Clearly incompatible investors are removed before scoring, and every
   reason is kept in the audit (`rejected_investors`). A filter fires only when data is
   known on both sides; missing data never eliminates an investor.

   | Code | Rule |
   |---|---|
   | `stage_mismatch` | The investor's nearest stage is two or more rounds away. |
   | `mandate_mismatch` / `sector_exclusion` | The sector mandate does not cover the company's sector family, or excludes it. |
   | `geography_restriction` / `excluded_geography` | Outside a stated, non-global mandate. |
   | `check_size_incompatible` | Minimum check exceeds the whole round. |
   | `fund_not_investing` | Between funds or inactive. |
   | `portfolio_conflict` | Portfolio holds a competitor named in the deck. |
   | `excluded_type` / `excluded_by_user` / `business_model_exclusion` | User or investor exclusions. |

4. **Score, assess confidence, and rank** (below).
5. **Explain.** For each match the engine writes why it fits, its investment evidence, likely
   concerns (severity-ranked), a specific outreach angle, and the required introduction
   materials. All of this is assembled from the company's evidenced proof points and the
   investor's recorded data.
6. **Archetypes.** The top recommended categories are scored as investor profiles alongside
   named investors. They fill the report when fewer than five named investors are viable. They
   are shown in italics, marked "profile, not a named firm", always have **Inferred** evidence,
   and are capped at Tier 2.

---

## Scoring methodology

Each criterion yields a raw score from 0–100. Its weighted score is `raw × weight / 100`, with
weights normalized to total 100. **Fit = Σ weighted scores.**

| Criterion | Weight | Rule (raw score) |
|---|---:|---|
| Stage fit | 15 | Same stage 100 · one round away 60 · further 0 |
| Sector/subsector fit | 20 | Exact subsector 100 · same family 75 · sibling subsector 65 · generalist 50 · none 0 |
| Check-size/round fit | 15 | Overlaps lead band 100 · ≥10% of round 75 · ≥3% 40 · smaller 15 · would take most of round 60 |
| Geography fit | 10 | Same state/region/country 100 · macro region 85 · global 70 · none 0 |
| Business-model fit | 10 | Stated preference overlaps 100 · no overlap 25 |
| Traction fit | 10 | Meets revenue bar 100 · one step below 55 (70 with customer/regulatory validation) · two below 20 |
| Historical investment evidence | 10 | `100 × (1 − 0.5^points)`, where each relevant deal counts 1.0 (same subsector) or 0.6 (same family), discounted for stage distance and age; none recorded → 0 |
| Strategic value | 5 | 40 + 30 per capability matching a company need (max 100) · no match 15 |
| Lead/follow capability | 5 | If a lead is needed: leads 100, sometimes 60, follows only 10 |

**Unknown data.** If either side's data is missing, the criterion scores 40, marks
`known: false`, lists the missing information, and contributes zero confidence. Historical
evidence is the exception: no recorded history scores 0.

**Evidence quality** (per investor):

- *Verified*: a recently verified source (`source_urls` + `last_verified` within 365 days) **and** relevant deals.
- *Strong*: two or more relevant deals, a deal plus a matching stated mandate, or a verified mandate.
- *Moderate*: a stated mandate only.
- *Inferred*: category characteristics only (all archetypes).
- *Unknown*: too little data.

**Confidence** = `0.5 × quality factor + 0.5 × weighted criterion confidence`. The label is
High ≥ 0.75, Medium ≥ 0.55, otherwise Low.

**Confidence-adjusted score** = `fit × (1 − blend + blend × confidence)` (default blend 0.5).
This is why an assumption-heavy 90 does not outrank an evidence-backed 85. User preferences
add a small, explicit ranking bonus (+2 per match, capped at +4), shown in the breakdown and
never mixed into the fit score.

**Outreach priority**:

| Tier | Assigned when |
|---|---|
| Tier 1 — Contact Now | Adjusted ≥ 68, exact stage, check fit ≥ 60, can lead if a lead is needed, confidence not Low. |
| Tier 2 — Strong Target | Adjusted ≥ 56. |
| Tier 3 — Develop Relationship | Enters at a later round, has no recent activity, or has a partial fit. |
| Tier 4 — Conditional Target | Follows only while the round needs a lead, is an existing investor, or has Low confidence. |
| Do Not Target | Adjusted < 42. |

Matches are ordered by tier, then by ranking score. Every threshold is configurable.

---

## PDF report

`app/reporting/pdf_generator.py` renders **TEN CAPITAL — INVESTOR MATCHING REPORT** on one
US-Letter page with these sections:

- Company Snapshot
- Ideal Investor Profile
- Top Investor Matches (5–8 rows: Investor, Fit, Confidence, Why It Fits, Concern, Outreach Angle)
- Recommended Next Action
- Data Quality

The footer follows the TEN Capital standard: title · page · "Compiled on [date] by TEN Capital
Network" · logo (0.67" × 0.25"), in centered Open Sans 7pt, with **Confidential – for
recipients only.** beneath it.

Typography is Open Sans throughout (static TTFs bundled under `app/assets/fonts`, SIL OFL),
with a 9pt body, 10.5pt ALL-CAPS section headers, and compact 8pt tables. Components
(`report_layout.py`) are reusable. Snapshot values are marked *(inferred)* or *(user)* when
not read directly from the deck, and *Not in deck* when missing.

**One-page guarantee.** The story is measured against the frame and, if it overflows, walks
a degradation ladder that reduces content before typography. It drops bullets first, then
match rows (down to 6, keeping narratives whole, because the outreach angle is the most
actionable text), then tightens spacing, then shortens narratives, and only then reduces
type — never below 8pt body and 7.2pt table text. Each rung is verified by counting the
rendered pages, and whatever was given up is recorded in `report.content_reductions` in
the JSON.

**Report template.** The report's structure lives in
`app/reporting/templates/investor_matching_report.template.json`. This covers section order,
headings, the ten snapshot slots, table columns and widths, caption, footer text, length
limits, type sizes, controlled vocabularies, and the fields analyzed for every report. The
generator reads it at startup, and it contains no company or investor data. The human-readable
specification is [`docs/INVESTOR_MATCHING_REPORT_TEMPLATE.md`](docs/INVESTOR_MATCHING_REPORT_TEMPLATE.md).
Render the blank template (placeholders only) with `python -m app.cli --template`. Edit the
JSON rather than the code to change the report's structure; an invalid template is rejected
on load.

---

## JSON record

`TenCapitalRecord` (`app/models/record.py`) is validated with Pydantic on export and
round-trips losslessly. It contains:

- `analysis_id`, `timestamp`, and `user`
- `source_deck`
- `company` (the flat block from the specification) and `company_provenance` (value, source, location, confidence, method for every field)
- `investor_preferences` and `match_criteria` (`stage_weight` … `lead_capability_weight`)
- `scoring_policy`
- `ideal_investor_profile`
- `top_matches`, each with its full `score_breakdown`
- `rejected_investors` (the filter audit)
- `recommended_next_action` and `data_gaps`
- `investor_dataset` (including the synthetic flag), `warnings`, and `consent`
- `external_processing` (whether an external model was used, which one, and for what)
- `report` (page count, layout rung, and content reductions)

---

## Privacy, consent and security

* **Four independent consents**, recorded with timestamp and source:
  - analysis (required to process a deck)
  - data storage (required to write any file)
  - investor outreach (recorded only)
  - external processing (required before anything is sent to a model API)

  Analysis consent never implies outreach consent.
* **No outreach.** The application contains no email, messaging or CRM-write code paths.
* **External model is off by default.** It needs `TEN_LLM_ENABLED=true`, an API key, *and*
  per-run consent. The UI shows exactly what will be sent and to whom.

  Model output is checked locally:
  - An extracted value is kept only if its quoted evidence is found on the cited slide and its figures match. Otherwise it is discarded.
  - Polished wording is rejected if it introduces any name or number not present in its inputs.
  - Server-side refusal fallback is enabled, and API failures fall back to the deterministic engine.
* **Uploads** are validated by magic bytes (not just the extension), size-limited, and checked
  for PPTX archive structure and zip-bomb size. Filenames are sanitized. Decks are parsed from a
  private temporary directory that is deleted immediately.
* **Logging** records events and counts, never deck contents. A confidentiality filter
  truncates any unexpectedly long log message. Streamlit usage statistics are disabled
  (`.streamlit/config.toml`).
* **Secrets** live only in environment variables (`.env` is git-ignored; see `.env.example`).

---

## Testing

```bash
python -m pytest
```

The suite covers:

- PDF/PPTX extraction (including notes, tables, and charts)
- Scanned, corrupt, mismatched, legacy-PPT and oversized uploads
- Company normalization and provenance; forward-looking figures; missing-data handling and user corrections
- JSON/CSV repositories, aliases, de-duplication and incomplete records
- Every hard filter
- Scoring rules and weight normalization
- Evidence quality, confidence and adjusted ranking
- Tiers and archetype fallback; no invented investor names
- Concerns, outreach angles and next action
- The external-model service (with a fake client): grounding, fabrication rejection and failure fallback
- JSON validation and consent separation
- PDF one-page overflow handling and font embedding
- End-to-end runs through the pipeline, the CLI, and a Streamlit `AppTest` smoke test

Sample decks are generated fresh for each test session from `sample_data/make_samples.py`.

---

## Known limitations

* **Extraction is pattern-based.** It is conservative by design, so unusual phrasings are
  reported as missing rather than guessed. The review step exists for this reason; the
  optional external model can fill further gaps with verified quotes.
* **Charts and images** in PDFs are not read, except by the optional vision path on scanned
  decks. PPTX chart data *is* read.
* **Legacy `.ppt`** requires LibreOffice.
* **Sector, geography and investor-type vocabularies** are curated lists
  (`app/matching/taxonomy.py`, `app/utils/geography.py`). Uncommon sectors or places may
  resolve to *unknown* until added.
* **Category heuristics** (typical check sizes and stages by investor type) in
  `investor_profile.py` are generic market conventions, not claims about specific investors.
* **Match quality is bounded by the investor data.** Verification is only as current as each
  record's `last_verified` date. The bundled dataset is synthetic.
* **Currency.** Amounts are treated as USD. Other currency symbols are parsed but not converted.
