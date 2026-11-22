"""Convert the TEN Capital Master Investor List (.xlsx) into this app's investor dataset (JSON).

    python scripts/import_ten_master.py "TEN_Capital_Master_Investor_List_2026-10-21_fix24.xlsx" \
        --out data/investors/ten_master_investors.json

* One record per firm (contacts that share an organization are merged), or per person for angels
  with no organization. Contact names are kept in the notes; emails and phone numbers are NOT copied.
* Excluded: Unsubscribed / Do Not Contact / Bounced = Y, Is Investor = N, anyone on the Suppression or
  Removed tabs, and records with no investment criteria at all (no type, sector, stage, check size,
  geography or thesis), which the matching engine could not score.
* The output is real contact-derived data: keep it out of Git (data/investors/ is gitignored).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook  # noqa: E402

from app.utils.text import normalize_name  # noqa: E402

YES = {"y", "yes", "true", "1"}
SUPPRESSION_TABS = re.compile(r"suppress|removed|unsub", re.I)
_URL = re.compile(r"^(?:https?://)?[\w.-]+\.[a-z]{2,}(?:/\S*)?$", re.I)


def _domain(url: str) -> str:
    return re.sub(r"^https?://", "", url.strip().lower()).split("/")[0].removeprefix("www.")


def _rows(ws):
    it = ws.iter_rows(values_only=True)
    head = [str(h or "").strip() for h in next(it)]
    for values in it:
        yield {h: ("" if v is None else str(v).strip()) for h, v in zip(head, values)}


def _split(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,|\n]", text or "") if p.strip()]


def _uniq(items):
    seen, out = set(), []
    for item in items:
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def convert(path: Path) -> tuple[dict, Counter]:
    wb = load_workbook(path, read_only=True)
    stats: Counter = Counter()
    suppressed = set()
    for ws in wb.worksheets:
        if SUPPRESSION_TABS.search(ws.title):
            suppressed |= {r.get("Email", "").lower() for r in _rows(ws) if r.get("Email")}
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in _rows(wb["Master"]):
        stats["rows"] += 1
        email = r.get("Email", "").lower()
        if any(r.get(c, "").lower() in YES for c in ("Unsubscribed", "Do Not Contact", "Bounced")) or (
                email and email in suppressed):
            stats["excluded: unsubscribed / do not contact / bounced"] += 1
            continue
        if r.get("Is Investor", "").upper() == "N":
            stats["excluded: not an investor"] += 1
            continue
        crit = {
            "type": r.get("Investor Type") or r.get("Investor Type (Std)"),
            "sector": r.get("Sector/Industries"),
            "stage": r.get("Investment Stage"),
            "check": r.get("Check/Intro Size"),
            "geo": r.get("Research Geo Focus"),
            "thesis": r.get("Description/Thesis"),
        }
        if not any(crit.values()):
            stats["excluded: no investment criteria"] += 1
            continue
        person = " ".join(p for p in (r.get("First Name"), r.get("Last Name")) if p)
        org = r.get("Organization", "")
        name = org or person
        if not name:
            stats["excluded: no name"] += 1
            continue
        groups[normalize_name(name) or name.lower()].append({**r, "_name": name, "_person": person, "_crit": crit})

    investors = []
    for key, members in groups.items():
        first = members[0]
        def pick(field):
            return next((m["_crit"][field] for m in members if m["_crit"][field]), "")
        people = _uniq(m["_person"] for m in members if m["_person"] and m["_person"] != first["_name"])
        notes = [pick("thesis")]
        if people:
            notes.append("TEN contacts: " + ", ".join(people[:8]) + (f" (+{len(people) - 8} more)" if len(people) > 8 else ""))
        rec = {
            "investor_id": f"ten-{re.sub(r'[^a-z0-9]+', '-', key)[:60].strip('-') or 'investor'}",
            "name": first["_name"],
            "investor_type": pick("type"),
            "website": next((m.get("Website") for m in members if m.get("Website")), None),
            "headquarters": next((m.get("Location") for m in members if m.get("Location")), None),
            "investment_geographies": _uniq(g for m in members for g in _split(m.get("Research Geo Focus", ""))),
            "sector_preferences": _uniq(s for m in members for s in _split(m.get("Sector/Industries", ""))),
            "stage_preferences": _uniq(s for m in members for s in _split(m.get("Investment Stage", ""))),
            "check_size": pick("check") or None,
            "lead_capability": next((m.get("Research Lead/Follow") for m in members
                                     if m.get("Research Lead/Follow")), None),
            "lead_history": "; ".join(_uniq(m.get("Research Recent Deals", "") for m in members)) or None,
            "last_known_investment": next((m.get("Research Last Deal Date") for m in members
                                           if m.get("Research Last Deal Date")), None),
            "fund_status": next((m.get("Research Active") for m in members if m.get("Research Active")), None),
            "source_urls": _uniq(u for m in members for u in _split(m.get("Research Sources", ""))),
            "last_verified": next((m.get("Research Date") for m in members if m.get("Research Date")), None),
            "notes": " | ".join(n for n in notes if n) or None,
        }
        investors.append({k: v for k, v in rec.items() if v not in (None, "", [])})
        stats["contacts kept"] += len(members)
    # The app merges records that share a website domain. On this list a shared site is usually a profile or
    # network page (linkedin.com, medium.com, an angel network) rather than the same firm, so keep a website
    # only when it is a URL that no other record uses.
    domains = Counter(_domain(r["website"]) for r in investors if r.get("website"))
    for r in investors:
        site = r.get("website")
        if site and (not _URL.match(site) or domains[_domain(site)] > 1):
            del r["website"]
            stats["websites dropped (shared or not a URL)"] += 1
    stats["investor records"] = len(investors)
    dataset = {
        "dataset": {
            "name": f"TEN Capital Master Investor List ({_date_from(path)})",
            "is_synthetic": False,
            "description": "Converted from the TEN Capital master list: one record per firm (or per angel), "
                           "suppressed and non-investor contacts removed. No emails or phone numbers.",
            "last_updated": _date_from(path),
        },
        "investors": investors,
    }
    return dataset, stats


def _date_from(path: Path) -> str:
    m = re.search(r"20\d\d-\d\d-\d\d", path.name)
    return m.group(0) if m else date.today().isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("master", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "investors" / "ten_master_investors.json")
    args = parser.parse_args()
    dataset, stats = convert(args.master)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dataset, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for k, v in stats.items():
        print(f"{k}: {v:,}")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
