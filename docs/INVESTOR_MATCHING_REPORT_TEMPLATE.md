# Investor Matching Report — Document Structure Template

The canonical structure of every TEN Capital Investor Matching Report the application
produces. It defines the page geometry, the sections and their order, every field slot,
the fixed vocabulary each slot may draw from, the fields analyzed for every report, and the
rules governing what may and may not appear.

No company, investor, round or figure from any analysis appears here. Slots are written
as `[FIELD NAME]`.

| Artifact | Role |
| --- | --- |
| [`app/reporting/templates/investor_matching_report.template.json`](../app/reporting/templates/investor_matching_report.template.json) | **Machine-readable template — the source of truth.** The PDF generator reads section order, headings, snapshot labels, table columns and widths, footer text, length limits and type sizes from it. |
| `docs/INVESTOR_MATCHING_REPORT_TEMPLATE.md` | This human-readable specification. |
| `docs/investor_matching_report_TEMPLATE.pdf` | The blank template, rendered by the application itself. |

Render the blank template (placeholders only; no deck required):

```bash
python -m app.cli --template                          # writes docs/investor_matching_report_TEMPLATE.pdf
python -m app.cli --template my_folder/blank.pdf
```

The rendered template goes through the ordinary renderer
([`app/reporting/template.py`](../app/reporting/template.py) → `pdf_generator.render_report`),
so it can never show a layout the application does not actually produce. The JSON template is
validated on load and by `tests/test_template.py`, which also checks that its vocabularies and
analyzed fields match the code and that it contains no company or investor data.

---

## 1. Page

| Property | Value |
| --- | --- |
| Page size | US Letter portrait (612 × 792 pt) |
| Page count | Exactly 1 — enforced by the fitting ladder (§11) and asserted in tests |
| Margins | Left 36 pt · Right 36 pt · Top 32 pt · Bottom 48 pt (footer zone) |
| Content width | 540 pt |
| Background | White; no charts or decorative graphics |
| Colour role | Emphasis only — every status is also carried by a word |

### Type scale (Open Sans; Helvetica fallback)

| Element | Size | Weight / colour |
| --- | --- | --- |
| Document title | 15 pt | Bold, navy `#1F3864` |
| Meta line | body − 0.5 pt | Regular, grey `#555555` |
| Synthetic-data warning | body − 1 pt | SemiBold, dark red `#8C1D18` |
| Section headings | 10.5 pt | Bold, navy, UPPER CASE, 0.6 pt navy rule beneath |
| Body / bullets | 9 pt (floor 8 pt) | Regular, ink `#222222` |
| Snapshot labels | body − 1 pt | SemiBold, grey |
| Table cells | 8 pt (floor 7.2 pt) | Regular; header row bold white on navy; zebra rows `#F4F6F9` |
| Table secondary lines | table − 1.2 pt | Regular, grey |
| Fit score | table + 2.5 pt | Bold, navy, centered |
| Callout | body | SemiBold, navy on `#EEF3FA` with a 2.5 pt blue `#2E75B6` left bar |
| Footer | 7 pt | Regular, grey, centered |

---

## 2. Section order

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 1  HEADER                  title · meta line · [synthetic-data warning]  │
├──────────────────────────────────────────────────────────────────────────┤
│ 2  COMPANY SNAPSHOT        10 label/value pairs, 2 per row               │
├──────────────────────────────────────────────────────────────────────────┤
│ 3  IDEAL INVESTOR PROFILE  3–5 bullets                                   │
├──────────────────────────────────────────────────────────────────────────┤
│ 4  TOP INVESTOR MATCHES    5–8 rows × 6 columns + caption                │
├──────────────────────────────────────────────────────────────────────────┤
│ 5  RECOMMENDED NEXT ACTION single callout                                │
├──────────────────────────────────────────────────────────────────────────┤
│ 6  DATA QUALITY            up to 4 bullets                               │
├──────────────────────────────────────────────────────────────────────────┤
│    FOOTER                  title · page · compiled-on · logo             │
│                            Confidential – for recipients only.           │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Header

| Slot | Content | Binding | Rule |
| --- | --- | --- | --- |
| Document title | `TEN CAPITAL — INVESTOR MATCHING REPORT` | fixed | Never changes |
| Meta line | `[COMPANY NAME] · Prepared [MONTH D, YYYY] · Analysis [ANALYSIS ID]` | `company.name`, `timestamp`, `analysis_id` (first 8 characters) | `Unnamed company` when no name was established |
| Synthetic-data warning | `Investor data: SYNTHETIC test dataset — named firms are fictitious and must not be used for outreach.` | `investor_dataset.is_synthetic` | Shown only when any investor record is synthetic |

---

## 4. Company Snapshot

Ten label/value pairs laid out two per row (label column 74 pt), read left to right. Values
are limited to 80 characters.

| # | Label | Slot | Binding | Provenance from |
| --- | --- | --- | --- | --- |
| 1 | Company | `[COMPANY NAME] · [WEBSITE]` | `company.name`, `company_provenance.website.value` | name |
| 2 | Sector | `[SECTOR FAMILY] / [SUBSECTOR]` | `company.sector`, `company.subsector` | subsector, sector |
| 3 | Stage | `[COMPANY STAGE] · product [PRODUCT STAGE]` | `company.stage`, `company.product_stage` | company_stage |
| 4 | Geography | `[CITY, STATE (COUNTRY)]` | `company.geography` | headquarters |
| 5 | Business Model | `[BUSINESS MODEL]` | `company.business_model` | business_model |
| 6 | Traction | `[CUSTOMERS / PILOTS] · [$ REVENUE] revenue · [GROWTH]% growth · [LOIs]` | `company.traction` | traction |
| 7 | Round | `[$ RAISE] [ROUND TYPE] · [$ COMMITTED] committed · lead: [LEAD INVESTOR \| open]` | `company.raise_amount`, `round_type`, `round_committed`, `lead_investor` | raise_amount, round_type |
| 8 | Valuation | `[$ VALUATION] [pre-money \| post-money \| valuation cap]` | `company.valuation`, `company.valuation_type` | valuation |
| 9 | Capital Raised | `[$ CAPITAL RAISED] to date` | `company.amount_raised` | amount_raised |
| 10 | Strategic Need | `[STRATEGIC NEED 1], [STRATEGIC NEED 2], [STRATEGIC NEED 3]` | `company.strategic_needs` (max 3) | strategic_needs |

**Rules**

- A slot with no established value reads *Not in deck* (grey italic). Slots are never left
  blank and never estimated.
- Provenance markers follow the value in small grey type: *(inferred)* for analyst
  inference, *(user)* for user-supplied corrections, *(external)* for approved external data.
  Values read directly from the deck carry no marker.
- Money renders as `$1.2K`, `$3.4M` or `$5.6B`.

---

## 5. Ideal Investor Profile

Three to five bullets (118 characters each), bound to `ideal_investor_profile.summary_points`.
All are analyst inference derived from the company record.

| # | Slot | Condition |
| --- | --- | --- |
| 1 | `[LEAD-CAPABLE] [STAGE] investor able to write [$ LEAD CHECK RANGE] into the [$ ROUND SIZE] round` | Always. The lead check range is 25–60% of the round; "Lead-capable" appears only while the round needs a lead |
| 2 | `[SUBSECTOR] specialist with [TECHNICAL EXPERTISE] depth` | When a sector is established |
| 3 | `Comfortable with regulatory risk: [REGULATORY EXPERTISE]` | Regulated subsectors only |
| 4 | `[FINANCIAL LEAD \| FINANCIAL LEAD PLUS A STRATEGIC CO-INVESTOR FOR STRATEGIC NEEDS]` (or commercialization expertise) | When strategic or commercial needs exist |
| 5 | `Mandate covering [STATE / COUNTRY]` | When a location is established |

Empty: *Not enough company data to profile the ideal investor.*

---

## 6. Top Investor Matches

A ranked table of **5–8 rows**, bound to `top_matches`. Rows are ordered by outreach
priority tier (Tier 1 first), then by the confidence-adjusted ranking score.

| Column | Width | Primary line | Secondary line | Limit |
| --- | --- | --- | --- | --- |
| Investor | 17% | `[INVESTOR NAME]` — bold; italic for profiles | `[TIER] · [INVESTOR TYPE]` — tier label in its tier colour | — |
| Fit | 6.2% | `[FIT]` — integer 0–100, bold navy, centered | `adj [ADJ]` — confidence-adjusted score | — |
| Confidence | 10.8% | `[LEVEL]` — High / Medium / Low, bold in its colour | `[EVIDENCE]` — evidence quality | — |
| Why It Fits | 21% | `[WHY IT FITS — the top two or three criteria that scored as a fit]` | — | 130 chars |
| Concern | 16.5% | `[MOST LIKELY CONCERN]` | — | 80 chars; *None identified* when empty |
| Outreach Angle | 28.5% | `[OUTREACH ANGLE — the company's strongest proof point tied to the investor's strongest criterion]` | — | 165 chars |

When Claude writes the narrative cells, it aims for target lengths of 95 (Why It Fits), 55
(Concern) and 125 (Outreach Angle) characters. These sit below the fitting ladder's
trimmed limits, so Claude-written text is never cut off; the limits above remain hard
ceilings.

Caption beneath the table, in small grey type:
*Fit = deterministic 0–100 score; adj = confidence-adjusted. Evidence: Verified › Strong ›
Moderate › Inferred › Unknown. Italic rows are investor profiles, not named firms.*

**Row rules**

- Named investors come first. Archetype profiles (investor categories) fill the remaining
  rows only when fewer than five named investors are viable. They are rendered in italics
  with the subtitle *profile, not a named firm*.
- Rows with outreach priority *Do Not Target* are never shown.
- Investor names, deals and check sizes come only from an investor dataset — never generated.
- Empty table: *No investors could be matched. Add investor data or complete the missing
  company fields, then re-run.*

**Colour coding** — Tier: T1 green `#2E7D32` · T2 blue `#2E75B6` · T3 grey `#555555` ·
T4 amber `#B26A00`. Confidence: High green · Medium amber · Low dark red.

---

## 7. Recommended Next Action

A single callout (320 characters), bound to `recommended_next_action`:

`[RECOMMENDED NEXT ACTION — the single most important outreach step]`

Selection order: confirm critical missing company fields first; otherwise approach Tier 1
investors (with a lead package when the round needs a lead); otherwise verify Tier 2
mandates; otherwise source named investors matching the top profile.

---

## 8. Data Quality

Up to four bullets (150 characters each), bound to `data_gaps`, most material first:

| # | Slot |
| --- | --- |
| 1 | `[DATA GAP — missing company fields that change stage or check-size fit]` |
| 2 | `[DATA GAP — values inferred rather than stated in the deck]` |
| 3 | `[DATA GAP — ranked investors resting on moderate or weaker evidence]` |
| 4 | `[DATA GAP — investors removed by hard filters]` |

The synthetic-data line is excluded here because it already appears in the header.
Empty: *No material data gaps identified.*

---

## 9. Footer (every page)

```
[DOCUMENT TITLE]   [PAGE #]   Compiled on [MONTH D, YYYY] by TEN Capital Network   [TEN CAPITAL LOGO]
                       Confidential – for recipients only.
```

| Property | Value |
| --- | --- |
| Document title | `TEN Capital — Investor Matching Report` |
| Font | Open Sans 7 pt, grey, centered |
| Logo | `app/assets/TEN_Capital_logo_footer.png` at 0.67" × 0.25", inline after the text |
| Rule | 0.5 pt light rule above the footer |
| Confidentiality line | Mandatory; must not be altered or removed |

---

## 10. Provenance and empty values

| Source | Marker in the report | Meaning |
| --- | --- | --- |
| Pitch deck | none | Read from the deck; slide/page kept in the JSON record |
| User supplied | *(user)* | Entered or corrected in review |
| External data | *(external)* | From an approved external data source |
| Analyst inference | *(inferred)* | Classified or derived by the engine |
| Missing | *Not in deck* | Not established — never estimated |

---

## 11. One-page fitting ladder

When content overflows, the renderer walks this order and records every reduction in
`report.content_reductions`:

1. Ideal Investor Profile to 4 bullets; Data Quality to 3 notes
2. Match rows to 7
3. Spacing −15%
4. Match rows to 6
5. Shorter narratives (Why It Fits 105 · Concern 65 · Outreach Angle 135 · snapshot values 70 characters)
6. Type 8.5 pt body / 7.6 pt table
7. Last rung: 5 rows, 3 bullets, 2 notes, 8 pt body / 7.2 pt table

Type never goes below the floors (8 pt body, 7.2 pt table).

---

## 12. Controlled vocabularies

| Vocabulary | Values |
| --- | --- |
| Stages | Pre-Seed · Seed · Series A · Series B · Series C+ · Growth |
| Product stages | Concept / research · Prototype / MVP · Pilot / beta · Commercially available · Scaling |
| Revenue stages | Pre-revenue · Early revenue (<$1M) · Revenue ($1M–$10M) · Scaling revenue ($10M+) |
| Sector families | Healthcare & Life Sciences · Software & Internet · Climate & Energy · Industrial & Hardware · Consumer |
| Subsectors (21) | Diagnostics · Medical devices · Digital health · Therapeutics / biotech · Life science tools · Healthcare services · Enterprise software · Fintech · Cybersecurity · AI / machine learning · Edtech · Proptech · Energy · Climate tech · Agtech & food · Advanced manufacturing · Robotics & hardware · Mobility & logistics · Aerospace & defense · Consumer products · E-commerce & retail |
| Business models | SaaS / subscription software · Subscription · Marketplace · Hardware sales · Device + recurring consumables · Licensing / royalties · Services · Transaction fees · Direct-to-consumer · Drug development / partnering · Reimbursed testing |
| Strategic needs | Regulatory expertise · Clinical validation · Distribution & channels · Customer access · Manufacturing scale-up · Go-to-market · International expansion · Follow-on capital · Technical / R&D · Recruiting · Reimbursement strategy · Partnering / M&A |
| Investor types (18) | Angel investor · Angel group · Syndicate · Seed fund · Micro-VC · Venture capital fund · Growth equity fund · Family office · Corporate venture capital · Strategic investor · Healthcare system · Pharmaceutical company · Medical-device strategic · Industrial strategic · Private equity · Impact investor · Government-backed fund · Sovereign wealth-related investor |
| Outreach priority | Tier 1 — Contact Now · Tier 2 — Strong Target · Tier 3 — Develop Relationship · Tier 4 — Conditional Target · Do Not Target |
| Tier short labels | T1 · Contact now · T2 · Strong target · T3 · Develop · T4 · Conditional |
| Confidence | High · Medium · Low |
| Evidence quality | Verified · Strong · Moderate · Inferred · Unknown |

---

## 13. Fields analyzed for every report

The PDF shows a subset of the analysis; the JSON record carries all of it.

### 13.1 Company record (31 fields, each with provenance)

Every field carries `value`, `source`, `location` (slide/page), `confidence`, `method`, `note`.
*Impact* is how much a gap in the field can move the ranking (0–3); fields with impact ≥ 2 are
flagged for manual entry when missing.

| Field | Type | Impact | Field | Type | Impact |
| --- | --- | ---: | --- | --- | ---: |
| Company name | text | 1 | Market size (TAM, USD) | money | 0 |
| Website | text | 0 | Round | text | 1 |
| Headquarters | text | 2 | Amount being raised (USD) | money | 3 |
| Sector | text | 0 | Capital raised to date (USD) | money | 1 |
| Subsector | subsector | 3 | Committed in this round (USD) | money | 1 |
| Business model | business model | 2 | Valuation (USD) | money | 1 |
| Business model (as described) | text | 0 | Valuation basis (pre/post/cap) | text | 0 |
| Company stage | stage | 3 | Use of funds | list | 1 |
| Product stage | product stage | 1 | Strategic needs | strategic | 1 |
| Revenue stage | revenue stage | 2 | Regulatory status | text | 1 |
| Revenue (annual, USD) | money | 2 | Intellectual property | text | 0 |
| Revenue growth (%) | percent | 1 | Existing investors | list | 1 |
| Customers / pilots | text | 1 | Lead investor (this round) | text | 2 |
| Customer / site count | int | 0 | Seeking a lead investor | bool | 1 |
| Traction summary | text | 1 | Named competitors | list | 1 |
| | | | Partnerships | text | 0 |

### 13.2 Scoring criteria (per investor)

| Criterion | Default weight | Rule (raw score 0–100) |
| --- | ---: | --- |
| Stage fit | 15 | Same stage 100 · one round away 60 · further 0 |
| Sector/subsector fit | 20 | Exact subsector 100 · same family 75 · sibling subsector 65 · generalist 50 · none 0 |
| Check-size/round fit | 15 | Overlaps lead band (25–60% of round) 100 · ≥10% 75 · ≥3% 40 · smaller 15 · would take most of the round 60 |
| Geography fit | 10 | Same state/region/country 100 · macro region 85 · global 70 · none 0 |
| Business-model fit | 10 | Stated preference overlaps 100 · no overlap 25 |
| Traction fit | 10 | Meets revenue bar 100 · one step below 55 (70 with validation) · two below 20 |
| Historical investment evidence | 10 | 100 × (1 − 0.5^points) over relevant recorded deals; none recorded 0 |
| Strategic value | 5 | 40 + 30 per capability matching a company need (max 100) · no match 15 |
| Lead/follow capability | 5 | Lead needed: leads 100 · sometimes 60 · follows only 10 |

Fit = Σ (raw × weight / 100). A criterion that cannot be judged scores 40, is marked
unknown, and contributes zero confidence. Confidence-adjusted score =
fit × (1 − blend + blend × confidence).

Each criterion keeps: `criterion`, `label`, `weight`, `raw_score`, `weighted_score`,
`evidence`, `evidence_source`, `evidence_level`, `confidence`, `missing_information`, `known`.

### 13.3 Per-match fields

`rank` · `investor` · `investor_id` · `investor_type` · `kind` (investor | archetype) ·
`fit_score` · `adjusted_score` · `ranking_score` · `confidence` · `confidence_score` ·
`evidence_quality` · `rationale` · `investment_evidence` · `likely_concerns` ·
`outreach_angle` · `required_materials` · `outreach_priority` · `outreach_priority_key` ·
`priority_reason` · `preference_matches` · `score_breakdown` · `data_source` · `is_synthetic` ·
`text_source`

Required introduction materials are drawn from: Executive summary · Pitch deck · Financial
model · Cap table · Investment terms · Regulatory roadmap · Clinical results · IP summary ·
Customer references · Technical validation · Use-of-funds schedule · Market analysis · Data room.

### 13.4 Ideal Investor Profile fields

`target_stages` · `recommended_categories` · `excluded_categories` · `sector_specialization` ·
`geographic_fit` · `lead_check_range` · `follow_check_range` · `lead_required` · `lead_basis` ·
`strategic_vs_financial` · `technical_expertise` · `commercialization_expertise` ·
`regulatory_expertise` · `cross_border_capability` · `follow_on_capacity` ·
`strategic_partnership_potential` · `customer_channel_access` · `summary_points` · `basis`

### 13.5 JSON record sections

`schema_version` · `analysis_id` · `timestamp` · `user` · `source_deck` · `company` ·
`company_provenance` · `investor_preferences` · `match_criteria` · `scoring_policy` ·
`ideal_investor_profile` · `top_matches` · `rejected_investors` · `recommended_next_action` ·
`data_gaps` · `investor_dataset` · `warnings` · `external_processing` · `report` ·
`notification` (outcome of the automated results email)

---

## 14. Rules

1. The report renders on exactly one page with no clipping or overflow.
2. Content is reduced before typography; type never drops below 8 pt body / 7.2 pt table.
3. A slot with no established value renders its empty text, never an estimate.
4. Inferred and user-supplied values carry a provenance marker; deck facts carry none.
5. Investor names, deals and check sizes come only from an investor dataset; categories are
   shown as profiles, never as named firms.
6. Fit scores are deterministic and always shown with the confidence-adjusted score.
7. Rows are ordered by outreach priority tier, then ranking score.
8. The synthetic-data warning appears whenever any investor record is synthetic.
9. The footer, including the confidentiality line, is mandatory.

---

## 15. Changing the template

Edit the JSON template, not the code. Headings, labels, placeholders, column headers and
widths, caption, footer text, length limits and type sizes all flow into the renderer.
On load, the template is validated: all six sections present with unique order, all ten
snapshot slots, all six columns with widths summing to 1.0, and type floors no larger than
base sizes. An invalid template raises a configuration error rather than producing a
malformed report.

After a change, run `python -m pytest tests/test_template.py` and regenerate the blank
template with `python -m app.cli --template`.
