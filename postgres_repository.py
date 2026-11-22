"""Investor data from the InvestorMatch Postgres database (schema ``im``).

The database keeps one row per contact; this repository returns one record per
organization, so a group with six contacts on the list becomes one investor. Rows the
database marks ``exclude_from_outreach`` (unsubscribed, removed, non-investors, bad
emails, duplicates) are never loaded. Like every repository, nothing is filled in: an
unknown sector, stage or check size stays unknown and scores as unknown.

The connection string comes from ``DATABASE_URL`` (Railway sets it through a reference
variable). ``psycopg`` is imported only when this repository is used, so the rest of the
app and the test suite run without it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from typing import Any

from app.data.investor_repository import RemoteInvestorRepository
from app.errors import InvestorDataError

#: InvestorMatch ``investor_type`` labels -> text the app's type parser understands.
_TYPE_TEXT = {
    "Angel": "angel", "Angel Group": "angel group", "VC": "venture capital",
    "Micro VC": "micro vc", "Private Equity": "private equity",
    "Family Office / HNI": "family office", "Corporate VC": "corporate venture",
}
_REF = re.compile(r"\s*\[\d+\]")
#: Streamlit reruns the page on every click; reuse query results for this many seconds.
CACHE_SECONDS = 300
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}

QUERY = """
WITH rows AS (
    SELECT i.*,
           COALESCE(NULLIF(TRIM(i.org_name), ''),
                    NULLIF(TRIM(CONCAT_WS(' ', i.first_name, i.last_name)), ''),
                    i.email) AS display_name
    FROM im.investor i
    WHERE NOT i.exclude_from_outreach
)
SELECT
    MIN(r.display_name)                                         AS name,
    MODE() WITHIN GROUP (ORDER BY r.investor_type::text)        AS investor_type,
    MAX(r.website)                                              AS website,
    MAX(CONCAT_WS(', ', r.city, r.state))                       AS headquarters,
    MAX(COALESCE(r.research_geography, r.state))                AS geography,
    ARRAY(SELECT DISTINCT s.name FROM im.investor_sector x JOIN im.sector s USING (sector_id)
          WHERE x.investor_id = ANY(ARRAY_AGG(r.investor_id)))  AS sectors,
    STRING_AGG(DISTINCT r.industries_raw, '; ')                 AS industries_raw,
    ARRAY(SELECT DISTINCT t.stage::text FROM im.investor_stage t
          WHERE t.investor_id = ANY(ARRAY_AGG(r.investor_id)))  AS stages,
    MIN(r.check_min_usd)                                        AS min_check,
    MAX(r.check_max_usd)                                        AS max_check,
    MAX(r.check_typical_usd)                                    AS typical_check,
    BOOL_OR(r.can_lead)                                         AS can_lead,
    ARRAY_AGG(DISTINCT r.status::text)                          AS statuses,
    MAX(r.last_deal_date)                                       AS last_deal_date,
    MAX(r.researched_on)                                        AS researched_on,
    STRING_AGG(DISTINCT r.research_sources, '; ')               AS research_sources,
    MAX(r.research_summary)                                     AS research_summary,
    STRING_AGG(NULLIF(TRIM(CONCAT_WS(' ', r.first_name, r.last_name)), ''), ', '
               ORDER BY r.source_row)                           AS contacts,
    COUNT(*)                                                    AS contact_count
FROM rows r
GROUP BY LOWER(r.display_name)
ORDER BY 1
"""


def _strip_refs(text: str | None) -> str | None:
    return _REF.sub("", text).strip() if text else None


def row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    """One aggregated database row -> a raw row in the app's investor column names."""
    statuses = set(row.get("statuses") or [])
    lead = row.get("can_lead")
    # If any contact's record says the organization has paused, treat it as not investing.
    if "Paused" in statuses:
        fund_status = "between_funds"
    elif statuses & {"Active", "Selective"}:
        fund_status = "active"
    else:
        fund_status = "unknown"
    sectors = list(row.get("sectors") or [])
    if row.get("industries_raw"):
        sectors.append(row["industries_raw"])
    notes = [f"{row['contact_count']} contact(s) on the TEN list"
             + (f": {row['contacts']}" if row.get("contacts") else "")]
    if row.get("research_summary"):
        notes.append("Research: " + _strip_refs(row["research_summary"]))
    return {
        "name": row["name"],
        "investor_type": _TYPE_TEXT.get(row.get("investor_type") or "", ""),
        "website": row.get("website"),
        "headquarters": row.get("headquarters") or None,
        # Kept as one item: the app would otherwise split "Chadds Ford, PA" at the comma.
        # Parenthetical examples ("e.g. a Wisconsin company") are not a mandate, so drop them.
        "geographies": [re.sub(r"\s*\([^)]*\)", "", _strip_refs(row["geography"]))]
        if row.get("geography") else [],
        "sector_preferences": sectors,
        "stage_preferences": list(row.get("stages") or []),
        "min_check": row.get("min_check"),
        "max_check": row.get("max_check"),
        "typical_check": row.get("typical_check"),
        "lead_capability": "unknown" if lead is None else ("yes" if lead else "no"),
        "fund_status": fund_status,
        "last_known_investment": row.get("last_deal_date"),
        "source_urls": [u.strip() for u in (row.get("research_sources") or "").split(";") if u.strip()],
        "last_verified": row.get("researched_on"),
        "notes": " | ".join(notes),
    }


class PostgresInvestorRepository(RemoteInvestorRepository):
    """TEN Capital investor list from the InvestorMatch database."""

    def __init__(self, database_url: str, *, name: str = "TEN Capital investor database") -> None:
        super().__init__(name)
        if not database_url.strip():
            raise InvestorDataError("DATABASE_URL is empty.")
        self._url = database_url

    def _query(self) -> list[dict[str, Any]]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - only when the dependency is missing
            raise InvestorDataError("psycopg is not installed; add psycopg[binary] to requirements.txt.") from exc
        try:
            with psycopg.connect(self._url, connect_timeout=10, row_factory=dict_row) as conn:
                return list(conn.execute(QUERY).fetchall())
        except psycopg.Error as exc:
            raise InvestorDataError(f"Could not read investors from the database: {exc}") from exc

    def fetch_rows(self) -> Iterator[dict[str, Any]]:
        cached = _CACHE.get(self._url)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            rows = cached[1]
        else:
            rows = self._query()
            _CACHE[self._url] = (time.monotonic(), rows)
        for row in rows:
            yield row_to_record(row)
