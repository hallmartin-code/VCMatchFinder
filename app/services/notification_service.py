"""Automated results email via Resend.

After each report generation, a summary of the analysis - with the one-page PDF and the
JSON record attached - is emailed to TEN Capital's inbox (``TEN_NOTIFY_TO``, default
Info@tencapital.group). This is internal distribution of results, never investor
outreach: recipients come only from configuration, never from the UI.

Rules:

* Off unless ``TEN_NOTIFY_ENABLED=true`` and ``RESEND_API_KEY`` is set.
* Sending never raises and never fails a report; the outcome is recorded in the JSON.
* The API key travels only in the Authorization header and is never logged.

Uses Resend's documented REST API through the standard library, so the deployment needs
no extra dependency and tests can swap the transport for a fake.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape
from typing import Any

from app.config import Settings
from app.models.record import NotificationOutcome, TenCapitalRecord
from app.reporting.pdf_generator import ReportContent
from app.utils.logging import get_logger
from app.utils.text import slugify

LOGGER = get_logger(__name__)

PROVIDER = "Resend"
RESEND_EMAILS_URL = "https://api.resend.com/emails"
#: Resend sits behind Cloudflare, which rejects the default Python-urllib user agent.
USER_AGENT = "ten-capital-investor-matching/1.0"
#: Resend rejects messages above 40 MB; stay well below.
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_RETRIES = 2

#: (method, url, headers, body, timeout) -> (status, response body)
Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None,
                     timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# --------------------------------------------------------------------------- render

_NAVY = "#1F3864"
_GREY = "#555555"
_CELL = "padding:6px 8px;border-bottom:1px solid #D9D9D9;vertical-align:top;font-size:13px;"
_HEAD = f"padding:6px 8px;background:{_NAVY};color:#FFFFFF;text-align:left;font-size:12px;"


def _date_text(value: datetime) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def _section(title: str) -> str:
    return (f'<h3 style="color:{_NAVY};font-size:15px;letter-spacing:.3px;margin:22px 0 6px;'
            f'border-bottom:1px solid {_NAVY};padding-bottom:3px">{escape(title.upper())}</h3>')


def render_email(record: TenCapitalRecord, *, subject_prefix: str) -> tuple[str, str, str]:
    """(subject, html, text) for a results email, built from the same record as the PDF."""
    content = ReportContent.from_record(record)
    company = record.company.name or "Unnamed company"
    stage = record.company.stage or "stage not established"
    matches = record.top_matches
    tier1 = sum(1 for m in matches if m.outreach_priority_key == "tier_1")
    synthetic = record.investor_dataset.is_synthetic
    subject = (f"{subject_prefix} Investor Matching — {company} · {stage} · "
               f"{len(matches)} matches, {tier1} Tier 1" + (" · SYNTHETIC DATA" if synthetic else ""))
    prepared = _date_text(record.timestamp)
    run_by = ", ".join(p for p in (record.user.name, record.user.email, record.user.organization) if p) or "not recorded"
    claude = (f"Claude ({record.external_processing.model}) used for "
              f"{', '.join(record.external_processing.purposes).replace('_', ' ')}"
              if record.external_processing.llm_used else "Deterministic engine only (Claude not used)")

    html_parts = [
        f'<div style="font-family:Arial,Helvetica,sans-serif;color:#222;max-width:820px">',
        f'<h2 style="color:{_NAVY};margin:0 0 4px;font-size:20px">TEN CAPITAL — INVESTOR MATCHING REPORT</h2>',
        f'<p style="color:{_GREY};margin:0 0 10px">{escape(company)} · Prepared {escape(prepared)} · '
        f'Analysis {escape(record.analysis_id[:8])}</p>',
    ]
    if synthetic:
        html_parts.append('<p style="color:#8C1D18;font-weight:bold;margin:0 0 10px">Investor data: SYNTHETIC '
                          'test dataset — named firms are fictitious and must not be used for outreach.</p>')
    html_parts.append(
        f'<p style="color:{_GREY};font-size:12px;margin:0 0 4px">Deck: {escape(record.source_deck.filename or "n/a")}'
        f' · Run by: {escape(run_by)} · {escape(claude)}</p>')

    html_parts.append(_section("Company Snapshot"))
    html_parts.append('<table style="border-collapse:collapse;width:100%">')
    for item in content.snapshot:
        value = item.value or "Not in deck"
        marker = {"inference": " (inferred)", "user_supplied": " (user)"}.get(item.source, "")
        html_parts.append(f'<tr><td style="{_CELL}color:{_GREY};width:150px">{escape(item.label)}</td>'
                          f'<td style="{_CELL}">{escape(value)}<span style="color:#8A8A8A">{marker}</span></td></tr>')
    html_parts.append("</table>")

    html_parts.append(_section("Ideal Investor Profile"))
    html_parts.append("<ul>" + "".join(f"<li>{escape(p)}</li>" for p in content.iip_points) + "</ul>")

    html_parts.append(_section("Top Investor Matches"))
    if matches:
        html_parts.append('<table style="border-collapse:collapse;width:100%"><tr>' + "".join(
            f'<th style="{_HEAD}">{h}</th>' for h in
            ("#", "Investor", "Fit", "Confidence", "Why It Fits", "Concern", "Outreach Angle")) + "</tr>")
        for m in matches:
            name = f"<i>{escape(m.investor)}</i> (profile)" if m.kind == "archetype" else f"<b>{escape(m.investor)}</b>"
            concern = m.likely_concerns[0] if m.likely_concerns else "None identified"
            html_parts.append(
                f'<tr><td style="{_CELL}">{m.rank}</td>'
                f'<td style="{_CELL}">{name}<br><span style="color:{_GREY};font-size:11px">'
                f'{escape(m.outreach_priority)} · {escape(m.investor_type)}</span></td>'
                f'<td style="{_CELL}"><b>{m.fit_score:.0f}</b><br><span style="color:{_GREY};font-size:11px">'
                f'adj {m.adjusted_score:.0f}</span></td>'
                f'<td style="{_CELL}">{escape(m.confidence)}<br><span style="color:{_GREY};font-size:11px">'
                f'{escape(m.evidence_quality.title())}</span></td>'
                f'<td style="{_CELL}">{escape(m.rationale)}</td><td style="{_CELL}">{escape(concern)}</td>'
                f'<td style="{_CELL}">{escape(m.outreach_angle)}</td></tr>')
        html_parts.append("</table>")
    else:
        html_parts.append("<p>No investors could be matched.</p>")

    html_parts.append(_section("Recommended Next Action"))
    html_parts.append(f'<p style="background:#EEF3FA;border-left:3px solid #2E75B6;padding:8px 10px;'
                      f'color:{_NAVY};font-weight:bold">{escape(record.recommended_next_action)}</p>')
    html_parts.append(_section("Data Quality"))
    html_parts.append("<ul>" + "".join(f"<li>{escape(g)}</li>" for g in record.data_gaps) + "</ul>")
    html_parts.append(
        f'<p style="color:{_GREY};font-size:12px;margin-top:22px">Attached: the one-page PDF report and the '
        f'JSON record. {len(record.rejected_investors)} investor(s) were removed by hard filters (see the JSON '
        'audit). This email is an internal copy of the analysis - no investor has been contacted.</p>'
        f'<p style="color:{_GREY};font-size:12px">Compiled on {escape(prepared)} by TEN Capital Network · '
        'Confidential – for recipients only.</p></div>')

    text_lines = [
        "TEN CAPITAL — INVESTOR MATCHING REPORT",
        f"{company} · Prepared {prepared} · Analysis {record.analysis_id[:8]}",
    ]
    if synthetic:
        text_lines.append("Investor data: SYNTHETIC test dataset — named firms are fictitious; not for outreach.")
    text_lines += [f"Deck: {record.source_deck.filename or 'n/a'} · Run by: {run_by} · {claude}", "",
                   "COMPANY SNAPSHOT"]
    text_lines += [f"  {i.label}: {i.value or 'Not in deck'}" for i in content.snapshot]
    text_lines += ["", "IDEAL INVESTOR PROFILE"] + [f"  - {p}" for p in content.iip_points]
    text_lines += ["", "TOP INVESTOR MATCHES"]
    for m in matches:
        concern = m.likely_concerns[0] if m.likely_concerns else "None identified"
        text_lines += [f"  {m.rank}. {m.investor} — {m.outreach_priority} · fit {m.fit_score:.0f} "
                       f"(adj {m.adjusted_score:.0f}) · {m.confidence}",
                       f"     Why: {m.rationale}", f"     Concern: {concern}", f"     Angle: {m.outreach_angle}"]
    text_lines += ["", "RECOMMENDED NEXT ACTION", f"  {record.recommended_next_action}", "", "DATA QUALITY"]
    text_lines += [f"  - {g}" for g in record.data_gaps]
    text_lines += ["", "Attached: one-page PDF report and JSON record. Internal copy - no investor has been contacted.",
                   "Confidential – for recipients only."]
    return subject, "".join(html_parts), "\n".join(text_lines)


# ----------------------------------------------------------------------------- send


def _json(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _error_text(status: int, raw: bytes) -> str:
    detail = str(_json(raw).get("message") or raw[:200].decode("utf-8", "replace")).strip()
    if status == 401:
        return "Resend rejected the API key (401). Check RESEND_API_KEY."
    if status == 403:
        return f"Resend refused the request (403): {detail}. The sender domain must be verified in Resend."
    if status == 422:
        return f"Resend rejected the message (422): {detail}."
    if status == 429:
        return "Resend rate limit reached (429)."
    return f"Resend returned {status}: {detail[:200]}"


def _safe_tag(value: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in value)
    return cleaned[:60] or "unknown"


def send_report_email(
    record: TenCapitalRecord,
    pdf_bytes: bytes | None,
    *,
    settings: Settings,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> NotificationOutcome:
    """Email the results to the configured inbox. Never raises."""
    recipients = settings.notify_recipients()
    base = NotificationOutcome(provider=PROVIDER, to=recipients, sender=settings.notify_from)
    key = settings.resend_api_key.get_secret_value().strip() if settings.resend_api_key else ""
    if not settings.notify_enabled:
        return base.model_copy(update={"skipped_reason": "email notifications are disabled (TEN_NOTIFY_ENABLED)"})
    if not key:
        return base.model_copy(update={"skipped_reason": "no RESEND_API_KEY configured"})
    if not recipients:
        return base.model_copy(update={"skipped_reason": "no recipients configured (TEN_NOTIFY_TO)"})

    subject, html_body, text_body = render_email(record, subject_prefix=settings.notify_subject_prefix)
    stem = f"{slugify(record.company.name or 'company')}_{record.analysis_id[:8]}_investor_matching"
    files: list[tuple[str, bytes]] = []
    if pdf_bytes:
        files.append((f"{stem}_report.pdf", pdf_bytes))
    files.append((f"{stem}.json", record.model_dump_json(indent=2).encode("utf-8")))
    if sum(len(data) for _name, data in files) > MAX_ATTACHMENT_BYTES:
        LOGGER.warning("Results email attachments exceed %d bytes; sending without them", MAX_ATTACHMENT_BYTES)
        files = []

    payload: dict[str, Any] = {
        "from": settings.notify_from,
        "to": recipients,
        "subject": subject,
        "html": html_body,
        "text": text_body,
        "tags": [{"name": "app", "value": "investor-matching"},
                 {"name": "analysis", "value": _safe_tag(record.analysis_id)}],
    }
    if files:
        payload["attachments"] = [{"filename": name, "content": base64.b64encode(data).decode("ascii")}
                                  for name, data in files]
    reply_to = [p.strip() for p in settings.notify_reply_to.split(",") if p.strip()]
    if reply_to:
        payload["reply_to"] = reply_to
    digest = hashlib.sha256(pdf_bytes or b"").hexdigest()[:16]
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        # A retried request can never produce a second copy of the same report.
        "Idempotency-Key": f"investor-matching/{record.analysis_id}/{digest}",
    }
    body = json.dumps(payload).encode("utf-8")
    send = transport or urllib_transport
    attempted = base.model_copy(update={"attempted": True, "attachments": [n for n, _d in files],
                                        "attempted_at": datetime.now(UTC)})

    last_error = "unknown error"
    for attempt in range(MAX_RETRIES + 1):
        try:
            status, raw = send("POST", RESEND_EMAILS_URL, headers, body, settings.notify_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - network failures must not fail a report
            last_error = f"could not reach Resend ({exc.__class__.__name__})"
        else:
            if 200 <= status < 300:
                LOGGER.info("Results email sent for analysis %s", record.analysis_id[:8])
                return attempted.model_copy(update={"sent": True, "message_id": _json(raw).get("id")})
            last_error = _error_text(status, raw)
            if status != 429 and 400 <= status < 500:
                break  # a client error will not succeed on retry
        if attempt < MAX_RETRIES:
            sleep(1.5 * (attempt + 1))
    LOGGER.warning("Results email not sent for analysis %s: %s", record.analysis_id[:8], last_error)
    return attempted.model_copy(update={"error": last_error})
