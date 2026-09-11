"""The report template: valid, free of company data, and actually driving the renderer."""

from __future__ import annotations

import json

import pymupdf
import pytest

from app.cli import main as cli_main
from app.config import CRITERIA, MatchWeights
from app.errors import ConfigError
from app.matching import taxonomy
from app.models.company import FIELD_SPECS
from app.models.match import EVIDENCE_LABELS, TIER_LABELS, TIER_SHORT, CriterionScore, IdealInvestorProfile
from app.models.record import TenCapitalRecord, TopMatchRecord
from app.reporting import pdf_generator
from app.reporting.template import (
    TEMPLATE_PATH,
    ReportTemplate,
    load_template,
    placeholder_content,
    render_template_pdf,
)
from app.utils.provenance import SOURCE_LABELS
from conftest import DATASET_PATH, ROOT

TEMPLATE_DOC = ROOT / "docs" / "INVESTOR_MATCHING_REPORT_TEMPLATE.md"
#: Words and figures that exist only in the sample run; none may appear in the template.
_SAMPLE_ONLY = {"Lumora", "LumoraDx", "Sepsiq", "Tessary", "Austin", "Nimbus", "sepsis",
                "$6M", "$18M", "$420K", "$3.2M", "140%"}


def sample_terms() -> set[str]:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    terms = set(_SAMPLE_ONLY)
    for investor in data["investors"]:
        terms.add(investor["name"])
        terms.update(investor.get("portfolio_companies", []))
        terms.update(deal["company"] for deal in investor.get("historical_investments", []))
    return {t for t in terms if t}


def assert_no_sample_data(text: str) -> None:
    lowered = text.lower()
    leaked = sorted(t for t in sample_terms() if t.lower() in lowered)
    assert not leaked, f"company/investor data found in template: {leaked}"


def test_template_loads_and_validates():
    t = load_template()
    assert [s.id for s in t.ordered_sections()] == [
        "header", "company_snapshot", "ideal_investor_profile", "top_investor_matches",
        "recommended_next_action", "data_quality"]
    assert sum(c.width_fraction for c in t.section("top_investor_matches").columns) == pytest.approx(1.0)
    assert t.page.pages == 1


def test_template_files_contain_no_company_data():
    for path in (TEMPLATE_PATH, TEMPLATE_DOC):
        assert_no_sample_data(path.read_text(encoding="utf-8"))


def test_rendered_template_is_one_page_of_placeholders(tmp_path):
    out = tmp_path / "template.pdf"
    assert render_template_pdf(out).page_count == 1
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
    assert_no_sample_data(text)
    t = load_template()
    headings = [s.heading.upper() for s in t.ordered_sections() if s.heading]
    positions = [text.index(h) for h in headings]
    assert positions == sorted(positions)  # rendered in template order
    for field in t.section("company_snapshot").fields:
        assert field.label in text
    for column in t.section("top_investor_matches").columns:
        assert column.header in text
    for slot in ("[COMPANY NAME]", "[INVESTOR NAME]", "[FIT]", t.document_title, t.footer.confidentiality):
        assert slot in text


def test_renderer_reads_structure_from_template():
    t = load_template()
    matches = t.section("top_investor_matches")
    assert pdf_generator.REPORT_TITLE == t.document_title
    assert pdf_generator.CONFIDENTIALITY == t.footer.confidentiality
    assert pdf_generator.TABLE_CAPTION == matches.caption
    assert pdf_generator.BASE.max_matches == matches.max_items
    assert pdf_generator.BASE.angle_chars == matches.column("angle").max_chars
    assert pdf_generator.BASE.body_size == t.typography.body_pt
    assert pdf_generator.BODY_FLOOR == t.typography.body_floor_pt
    assert pdf_generator.TABLE_FLOOR == t.typography.table_floor_pt
    assert pdf_generator.FRAME_W == t.page.width_pt - t.page.margins_pt.left - t.page.margins_pt.right
    assert min(rung.max_matches for rung in pdf_generator.LADDER) == matches.min_items
    # Claude's target lengths must survive the ladder's trimming, so its text is never cut off.
    for key, trimmed in (("why", "why_chars"), ("concern", "concern_chars"), ("angle", "angle_chars")):
        column = matches.column(key)
        assert column.target_chars and column.target_chars <= pdf_generator._TRIMMED[trimmed] <= column.max_chars


def test_vocabularies_match_the_code():
    v = load_template().vocabularies
    assert v["stages"] == list(taxonomy.STAGE_LABELS.values())
    assert v["product_stages"] == list(taxonomy.PRODUCT_STAGES.values())
    assert v["revenue_stages"] == list(taxonomy.REVENUE_STAGES.values())
    assert v["sector_families"] == list(taxonomy.SECTOR_FAMILIES.values())
    assert v["subsectors"] == [s.label for s in taxonomy.SUBSECTORS.values()]
    assert v["business_models"] == list(taxonomy.BUSINESS_MODELS.values())
    assert v["strategic_needs"] == list(taxonomy.STRATEGIC_TAGS.values())
    assert v["investor_types"] == [label for key, label in taxonomy.INVESTOR_TYPES.items() if key != "unknown"]
    assert v["outreach_priority"] == list(TIER_LABELS.values())
    assert v["tier_short"] == list(TIER_SHORT.values())
    assert v["evidence_quality"] == list(EVIDENCE_LABELS.values())
    assert v["provenance"] == list(SOURCE_LABELS.values())
    assert v["confidence"] == ["High", "Medium", "Low"]


def test_analysis_fields_match_the_code():
    a = load_template().analysis_fields
    assert [(f["key"], f["label"], f["type"], f["ranking_impact"]) for f in a["company_fields"]] == \
        [(key, spec.label, spec.kind, spec.ranking_impact) for key, spec in FIELD_SPECS.items()]
    weights = MatchWeights()
    assert [(c["key"], c["label"], c["default_weight"]) for c in a["scoring_criteria"]] == \
        [(key, label, getattr(weights, key)) for key, label in CRITERIA.items()]
    assert a["criterion_fields"] == list(CriterionScore.model_fields)
    assert a["match_fields"] == list(TopMatchRecord.model_fields)
    assert a["ideal_investor_profile_fields"] == list(IdealInvestorProfile.model_fields)
    assert a["record_sections"] == list(TenCapitalRecord.model_fields)


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda d: d["sections"].pop(), "sections must be exactly"),
    (lambda d: d["sections"][3]["columns"][0].update(width_fraction=0.3), "sum to 1.0"),
    (lambda d: d["sections"][1]["fields"].pop(), "Company Snapshot fields"),
    (lambda d: d["typography"].update(body_floor_pt=12), "Type floors"),
])
def test_invalid_templates_are_rejected(mutate, message):
    data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    mutate(data)
    with pytest.raises(ConfigError, match=message):
        ReportTemplate.model_validate(data)


def test_placeholder_content_has_no_real_values():
    content = placeholder_content()
    assert content.company_name == "[COMPANY NAME]"
    assert all("[" in item.value for item in content.snapshot)
    assert all(isinstance(row.fit, str) and row.name.startswith("[") for row in content.matches)


def test_cli_renders_template_without_a_deck(tmp_path, capsys):
    out = tmp_path / "blank_template.pdf"
    assert cli_main(["--template", str(out)]) == 0
    assert out.exists()
    assert "placeholders only" in capsys.readouterr().out


def test_cli_requires_a_deck_otherwise():
    with pytest.raises(SystemExit):
        cli_main([])
