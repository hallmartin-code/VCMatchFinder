"""Automated results email: gating, payload, retries, and failure isolation (fake transport only)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from helpers import TODAY

from app.cli import main as cli_main
from app.config import Settings
from app.models.company import InvestorPreferences
from app.models.consent import ConsentRecord
from app.pipeline import build_record, generate_report, notify_results, run_end_to_end, run_matching
from app.reporting.json_exporter import validate_record_json
from app.services import notification_service
from app.services.notification_service import RESEND_EMAILS_URL, send_report_email

STAMP = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)
CONSENT = ConsentRecord(analysis_consent=True, data_storage_consent=True, source="test")


class FakeTransport:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None,
                 timeout: float) -> tuple[int, bytes]:
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "payload": json.loads(body or b"{}"), "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def email_settings(tmp_path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {"_env_file": None, "llm_enabled": False, "notify_enabled": True,
                              "resend_api_key": "re_test_key", "output_dir": tmp_path / "out"}
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def generated(lumora_company, dataset, config, settings):
    outcome = run_matching(lumora_company, InvestorPreferences(), dataset, config, CONSENT, today=TODAY)
    record = build_record(outcome, config=config, consent=CONSENT, settings=settings, timestamp=STAMP)
    report, record = generate_report(record)
    return record, report.pdf_bytes


def test_results_email_payload(generated, tmp_path):
    record, pdf = generated
    transport = FakeTransport((200, b'{"id": "msg_123"}'))
    outcome = send_report_email(record, pdf, settings=email_settings(tmp_path), consent=CONSENT,
                                transport=transport)
    assert outcome.sent and outcome.message_id == "msg_123"
    assert outcome.to == ["Info@tencapital.group"]
    call = transport.calls[0]
    assert (call["method"], call["url"]) == ("POST", RESEND_EMAILS_URL)
    assert call["headers"]["Authorization"] == "Bearer re_test_key"
    assert call["headers"]["Idempotency-Key"].startswith(f"investor-matching/{record.analysis_id}/")
    assert "urllib" not in call["headers"]["User-Agent"].lower()
    payload = call["payload"]
    assert payload["to"] == ["Info@tencapital.group"]
    assert payload["from"].endswith("@tencapital.group>")
    assert "Lumora Diagnostics" in payload["subject"] and "SYNTHETIC" in payload["subject"]
    for text in (payload["html"], payload["text"]):
        assert "Aldermoor Health Ventures" in text
        assert "RECOMMENDED NEXT ACTION" in text.upper()
        assert "Confidential – for recipients only." in text
        assert "no investor has been contacted" in text.lower()
    names = [a["filename"] for a in payload["attachments"]]
    assert names[0].endswith("_report.pdf") and names[1].endswith(".json")
    assert base64.b64decode(payload["attachments"][0]["content"]).startswith(b"%PDF")
    validate_record_json(base64.b64decode(payload["attachments"][1]["content"]).decode("utf-8"))


def test_email_follows_storage_consent(generated, tmp_path):
    record, pdf = generated
    transport = FakeTransport()
    outcome = send_report_email(record, pdf, settings=email_settings(tmp_path),
                                consent=ConsentRecord(analysis_consent=True), transport=transport)
    assert not outcome.attempted and "storage consent" in outcome.skipped_reason
    assert transport.calls == []
    # The rule is configurable.
    allowed = email_settings(tmp_path, notify_requires_storage_consent=False)
    transport = FakeTransport((200, b'{"id": "x"}'))
    assert send_report_email(record, pdf, settings=allowed, consent=ConsentRecord(analysis_consent=True),
                             transport=transport).sent


@pytest.mark.parametrize(("overrides", "reason"), [
    ({"notify_enabled": False}, "disabled"),
    ({"resend_api_key": None}, "RESEND_API_KEY"),
    ({"notify_to": " "}, "recipients"),
])
def test_unconfigured_email_is_skipped(generated, tmp_path, overrides, reason):
    record, pdf = generated
    transport = FakeTransport()
    outcome = send_report_email(record, pdf, settings=email_settings(tmp_path, **overrides), consent=CONSENT,
                                transport=transport)
    assert not outcome.sent and reason in outcome.skipped_reason and transport.calls == []


def test_server_errors_are_retried(generated, tmp_path, monkeypatch):
    monkeypatch.setattr(notification_service.time, "sleep", lambda _s: None)
    record, pdf = generated
    transport = FakeTransport((500, b"{}"), OSError("reset"), (200, b'{"id": "ok"}'))
    outcome = send_report_email(record, pdf, settings=email_settings(tmp_path), consent=CONSENT,
                                transport=transport, sleep=lambda _s: None)
    assert outcome.sent and len(transport.calls) == 3
    keys = {c["headers"]["Idempotency-Key"] for c in transport.calls}
    assert len(keys) == 1  # retries can never produce a duplicate email


def test_client_errors_fail_fast_and_never_raise(generated, tmp_path):
    record, pdf = generated
    transport = FakeTransport((422, b'{"message": "The tencapital.group domain is not verified."}'))
    outcome = send_report_email(record, pdf, settings=email_settings(tmp_path), consent=CONSENT,
                                transport=transport, sleep=lambda _s: None)
    assert outcome.attempted and not outcome.sent
    assert "not verified" in outcome.error and len(transport.calls) == 1
    transport = FakeTransport(OSError("down"), OSError("down"), OSError("down"))
    outcome = send_report_email(record, pdf, settings=email_settings(tmp_path), consent=CONSENT,
                                transport=transport, sleep=lambda _s: None)
    assert "could not reach Resend" in outcome.error and len(transport.calls) == 3


def test_end_to_end_records_the_email_outcome(samples, tmp_path):
    transport = FakeTransport((200, b'{"id": "msg_e2e"}'))
    result = run_end_to_end(samples["pdf"], settings=email_settings(tmp_path), consent=CONSENT, today=TODAY,
                            out_dir=tmp_path / "files", timestamp=STAMP, email_transport=transport)
    assert result.record.notification.sent and result.record.notification.message_id == "msg_e2e"
    saved = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert saved["notification"]["sent"] is True and saved["notification"]["to"] == ["Info@tencapital.group"]


def test_email_failure_does_not_fail_the_report(generated, tmp_path):
    record, pdf = generated
    outcome, updated = notify_results(record, pdf, settings=email_settings(tmp_path), consent=CONSENT,
                                      transport=FakeTransport((401, b"{}")))
    assert not outcome.sent and "API key" in outcome.error
    assert updated.notification.error == outcome.error and updated.report.pdf_generated


def test_tests_can_never_send_real_email(generated, tmp_path):
    record, pdf = generated
    outcome = send_report_email(record, pdf, settings=email_settings(tmp_path), consent=CONSENT,
                                sleep=lambda _s: None)
    assert not outcome.sent and "could not reach Resend" in outcome.error  # real transport is blocked


def test_cli_no_email_flag(samples):
    assert cli_main([str(samples["pdf"]), "--consent-analysis", "--no-email"]) == 0
