"""Deck in, validated JSON and one-page PDF out - through the pipeline, CLI and UI."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pymupdf
from helpers import TODAY

from app.cli import main as cli_main
from app.pipeline import run_end_to_end
from app.reporting.json_exporter import validate_record_json

STAMP = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)


def test_pdf_deck_end_to_end(samples, settings, tmp_path):
    result = run_end_to_end(samples["pdf"], settings=settings, today=TODAY,
                            out_dir=tmp_path, timestamp=STAMP)
    assert result.json_path is not None and result.pdf_path is not None
    record = validate_record_json(result.json_path.read_text(encoding="utf-8"))
    assert record.company.name == "Lumora Diagnostics"
    assert record.top_matches[0].investor == "Aldermoor Health Ventures"
    assert record.report.pdf_generated and record.report.page_count == 1
    assert record.report.pdf_filename == result.pdf_path.name
    with pymupdf.open(result.pdf_path) as doc:
        assert doc.page_count == 1


def test_pptx_deck_gives_the_same_top_match(samples, settings):
    result = run_end_to_end(samples["pptx"], settings=settings, today=TODAY, timestamp=STAMP)
    assert result.record.top_matches[0].investor == "Aldermoor Health Ventures"
    assert result.json_path is None  # no out_dir: nothing written


def test_scanned_deck_degrades_gracefully(samples, settings):
    result = run_end_to_end(samples["scanned"], settings=settings, today=TODAY, timestamp=STAMP)
    assert result.record.source_deck.scanned
    assert result.record.data_gaps[0].startswith("Deck text was not extractable")
    assert result.report.page_count == 1


def test_cli_writes_outputs(samples, tmp_path, capsys):
    code = cli_main([str(samples["pdf"]), "--out", str(tmp_path), "--today", "2026-09-11"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Aldermoor Health Ventures" in out and "SYNTHETIC" in out
    written = sorted(p.suffix for p in tmp_path.iterdir())
    assert written == [".json", ".pdf"]
    assert "consent" not in json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))


def test_cli_writes_nothing_without_out(samples, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli_main([str(samples["pdf"])]) == 0
    assert not any(tmp_path.iterdir())


def test_cli_reports_bad_files(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    assert cli_main([str(bad)]) == 1


def test_streamlit_app_loads():
    from conftest import ROOT
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60).run()
    assert not app.exception
    assert any("Ranked Investor Matches" in m.value for m in app.markdown)  # the design's hero card
    assert any(b.label == "Analyze deck" for b in app.button)


def test_streamlit_password_gate(monkeypatch):
    from conftest import ROOT
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TEN_APP_PASSWORD", "correct horse battery staple")
    app = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60).run()
    assert not app.exception
    assert not any(b.label == "Analyze deck" for b in app.button)  # nothing behind the gate

    app.text_input(key="access_password").input("wrong").run()
    next(b for b in app.button if b.label == "Enter").click().run()
    assert any("Incorrect password" in e.value for e in app.error)

    app.text_input(key="access_password").input("correct horse battery staple").run()
    next(b for b in app.button if b.label == "Enter").click().run()
    assert not app.exception
    assert any(b.label == "Analyze deck" for b in app.button)


def test_landing_asks_no_consent_questions(monkeypatch):
    from conftest import ROOT
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("TEN_APP_PASSWORD", raising=False)
    app = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60).run()
    assert not app.exception
    assert any(b.label == "Analyze deck" for b in app.button)
    assert not [c.label for c in app.checkbox if "consent" in c.label.lower()]
    assert not app.checkbox                      # no consent controls in the sidebar either


def test_streamlit_workspace_shows_matches_and_rescores(samples, settings, tmp_path, monkeypatch):
    """Drive the post-upload workspace with a real analyzed deck (AppTest cannot upload files)."""
    from conftest import ROOT
    from streamlit.testing.v1 import AppTest

    from app.pipeline import analyze_deck

    monkeypatch.setenv("TEN_OUTPUT_DIR", str(tmp_path))
    session = analyze_deck(settings=settings, deck_path=samples["pdf"])
    app = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=90)
    app.session_state["session"] = session
    app.session_state["company"] = session.company
    app.session_state["analysis_id"] = "00000000-test-analysis"
    app.run()
    assert not app.exception

    # Matches, cards and the report are there without any further click.
    text = "\n".join(m.value for m in app.markdown)
    assert "Aldermoor Health Ventures" in text and "Top concern:" in text
    assert any("SYNTHETIC" in e.value for e in app.error)
    labels = {b.label for b in app.button}
    assert not labels & {"Apply corrections", "Looks right — continue", "Run matching", "Generate PDF"}
    assert app.session_state["report_cache"] is not None
    downloads = [d.proto.label for d in app.get("download_button")]
    assert "Download the one-page report (PDF)" in downloads
    assert "Download all contacts and emails for Pipedrive (CSV)" in downloads
    # The first version is saved for TEN in the background.
    [holder] = app.session_state["deliveries"].values()
    holder["thread"].join(10)
    json_path, pdf_path = holder["saved"]
    assert json_path.parent == tmp_path and pdf_path.exists()

    # Key facts are dropdowns; changing one re-scores straight away.
    first = app.session_state["outcome_sig"]
    app.selectbox(key="fact_company_stage").set_value("seed").run()
    assert not app.exception
    assert app.session_state["outcome_sig"] != first
    assert app.session_state["outcome"].company.company_stage.value == "seed"
    assert app.session_state["outcome"].company.company_stage.source == "user_supplied"

    # Card actions: intro list (exportable for Pipedrive), contacted, exclude.
    top = app.session_state["outcome"].run.top(8)[0]
    app.button(key=f"intro_{top.investor_id}").click().run()
    app.button(key=f"contacted_{top.investor_id}").click().run()
    assert not app.exception
    assert list(app.session_state["intro_list"]) == [top.investor_id]
    assert app.session_state["contacted"] == [top.investor_id]
    text = "\n".join(m.value for m in app.markdown)
    assert "Intro list (1)" in text and "Already contacted" in text
    app.button(key=f"exclude_{top.investor_id}").click().run()
    assert not app.exception
    assert top.investor_name in app.session_state["outcome"].preferences.excluded_investors
    assert all(m.investor_id != top.investor_id for m in app.session_state["outcome"].run.top(8))
