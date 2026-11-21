"""
UIDAI ECMP HTML Auto Reader
- Extracts report metadata and enrolment/update detail rows.
- Maps each row to a configurable income category.
- Calculates income from configured category rates, NOT from HTML amount-charged fields.

Install:
    pip install beautifulsoup4

Usage:
    from auto_reader import read_uidai_html
    result = read_uidai_html("report.html", "category_rates.json")
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


DEFAULT_CONFIG = {
    "currency": "INR",
    "eligible_statuses": ["UPLOADED"],
    "categories": {
        "Mandatory Biometric Update": {
            "rate": "20.00",
            "match": {"type": "U", "mandatory_biometric_update": "Yes"}
        },
        "Other Update": {
            "rate": None,
            "match": {"type": "U", "mandatory_biometric_update": "No"}
        },
        "New Enrolment": {
            "rate": None,
            "match": {"type": "E"}
        }
    }
}

# HTML labels that may vary slightly between report versions.
HEADER_ALIASES = {
    "s_no": ["S.No", "S No", "Serial No"],
    "enrolment_no_and_date": ["Enrolment No. and Date", "Enrolment No and Date"],
    "appointment_id": ["Appointment Id", "Appointment ID"],
    "type": ["Type"],
    "mandatory_biometric_update": ["Mandatory biometric update", "Mandatory Biometric Update"],
    "is_nri": ["IS_NRI", "Is NRI"],
    "tin_number": ["Tin Number", "TIN Number"],
    "operator_id": ["Operator ID", "Operator Id"],
    "reviewer_id": ["Reviewer ID", "Reviewer Id"],
    "introducer": ["Introducer"],
    "proof": ["Proof"],
    "resident": ["Resident"],
    "status": ["Status"],
    "introducer_review_status": ["Introducer Review Status"],
    "user_review_status": ["User Review Status"],
    "gst_applied_percent": ["GST Applied(%)", "GST Applied (%)"],
    "amount_new_enrolment": ["Amount charged for New Enrolment"],
    "amount_update_enrolment": ["Amount charged for Update Enrolment"],
    "html_total_amount": ["Total amount charged"]
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _date_from_text(value: str):
    value = (value or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _load_config(config_path: str | Path | None) -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if config_path:
        supplied = json.loads(Path(config_path).read_text(encoding="utf-8"))
        config.update({k: v for k, v in supplied.items() if k != "categories"})
        config["categories"] = supplied.get("categories", config["categories"])
    return config


def _metadata_from_tables(soup: BeautifulSoup) -> dict:
    metadata = {}
    wanted = {
        "date": "report_created_at",
        "registrar": "registrar",
        "enrolment agency": "enrolment_agency",
        "operator": "operator_id",
        "station id": "station_id",
        "last registered": "last_registered",
        "last synch": "last_sync",
        "version no. of client": "client_version",
        "client version": "client_version",
    }
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key = _norm(cells[0].get_text(" ", strip=True)).rstrip(":")
                val = cells[1].get_text(" ", strip=True)
                if key in wanted:
                    metadata[wanted[key]] = val
    full_text = soup.get_text(" ", strip=True)
    match = re.search(
        r"Report Generated for Date\s*:\s*(\d{2}[/-]\d{2}[/-]\d{4})\s*to\s*(\d{2}[/-]\d{2}[/-]\d{4})",
        full_text, re.I
    )
    if match:
        metadata["report_date_from"] = _date_from_text(match.group(1))
        metadata["report_date_to"] = _date_from_text(match.group(2))
        metadata["report_date"] = metadata["report_date_from"]
    elif metadata.get("report_created_at"):
        metadata["report_date"] = _date_from_text(metadata["report_created_at"].split()[0])
    return metadata


def _find_detail_table(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        # UIDAI's sample places <th> cells directly inside <thead>, not inside a <tr>.
        thead = table.find("thead")
        header_cells = thead.find_all(["th", "td"]) if thead else []
        headers = [_norm(c.get_text(" ", strip=True)) for c in header_cells]
        if not headers:
            rows = table.find_all("tr")
            if rows:
                headers = [_norm(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        if "type" in headers and "resident" in headers and "status" in headers:
            return table, headers
    raise ValueError("Detail table not found. Expected columns including Type, Resident and Status.")


def _map_headers(headers: list[str]) -> dict[str, int]:
    mapped = {}
    for field, aliases in HEADER_ALIASES.items():
        alias_set = {_norm(a) for a in aliases}
        for idx, header in enumerate(headers):
            if header in alias_set:
                mapped[field] = idx
                break
    for required in ("type", "resident", "status"):
        if required not in mapped:
            raise ValueError(f"Required column is missing: {required}")
    return mapped


def _cell(cells, indexes, field):
    idx = indexes.get(field)
    return cells[idx].get_text(" ", strip=True) if idx is not None and idx < len(cells) else ""


def _classify(record: dict, config: dict) -> str | None:
    for category, definition in config.get("categories", {}).items():
        rules = definition.get("match", {})
        if all(_norm(record.get(field, "")) == _norm(expected)
               for field, expected in rules.items()):
            return category
    return None


def _decimal(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid configured category rate: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"Invalid configured category rate: {value!r}")
    return amount


def read_uidai_html_content(
    content: str | bytes,
    source_name: str = "upload.html",
    config_path: str | Path | None = None,
) -> dict:
    """Read UIDAI ECMP HTML content and return JSON-serializable extracted data."""
    raw_html = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    soup = BeautifulSoup(raw_html, "html.parser")
    config = _load_config(config_path)

    metadata = _metadata_from_tables(soup)
    table, headers = _find_detail_table(soup)
    indexes = _map_headers(headers)
    eligible = {_norm(x) for x in config.get("eligible_statuses", ["UPLOADED"])}

    records = []
    for row in table.find_all("tr"):
        if row.find("th"):
            continue
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        record = {field: _cell(cells, indexes, field) for field in indexes}
        if not any(record.values()):
            continue

        record["type"] = record.get("type", "").upper()
        record["mandatory_biometric_update"] = record.get("mandatory_biometric_update", "")
        record["status"] = record.get("status", "").upper()
        record["category"] = _classify(record, config)
        record["eligible_for_income"] = _norm(record["status"]) in eligible

        category_config = config.get("categories", {}).get(record["category"], {}) if record["category"] else {}
        configured_rate = category_config.get("rate")
        record["rate"] = str(configured_rate) if configured_rate not in (None, "") else None
        if record["eligible_for_income"] and record["rate"] is not None:
            record["calculated_income"] = str(_decimal(record["rate"]).quantize(Decimal("0.01")))
        else:
            record["calculated_income"] = "0.00"
        # Keep the source's charged amount for audit only; never use it as calculated income.
        record["html_total_amount"] = record.get("html_total_amount", "")
        records.append(record)

    category_totals = defaultdict(lambda: {"records": 0, "eligible_records": 0, "rate": None, "income": Decimal("0.00")})
    unmapped_count = 0
    ineligible_count = 0
    total_income = Decimal("0.00")

    for record in records:
        category = record["category"]
        if not category:
            unmapped_count += 1
            continue
        item = category_totals[category]
        item["records"] += 1
        item["rate"] = record["rate"]
        if not record["eligible_for_income"]:
            ineligible_count += 1
            continue
        item["eligible_records"] += 1
        if record["rate"] is not None:
            amount = _decimal(record["rate"])
            item["income"] += amount
            total_income += amount

    categories_output = {}
    for category, values in category_totals.items():
        categories_output[category] = {
            "records": values["records"],
            "eligible_records": values["eligible_records"],
            "rate_per_record": values["rate"],
            "income": str(values["income"].quantize(Decimal("0.01")))
        }

    return {
        "source_file": source_name,
        "metadata": metadata,
        "summary": {
            "total_records": len(records),
            "eligible_records": sum(1 for r in records if r["eligible_for_income"]),
            "unmapped_records": unmapped_count,
            "ineligible_records": ineligible_count,
            "total_calculated_income": str(total_income.quantize(Decimal("0.01"))),
            "currency": config.get("currency", "INR"),
            "categories": categories_output
        },
        "records": records,
        "warnings": (
            ["Some records have no category mapping. Configure a category rule before confirming income."]
            if unmapped_count else []
        ) + (
            ["Some mapped categories have no configured rate. Their income is currently ₹0.00 until a rate is added."]
            if any(v["rate_per_record"] is None for v in categories_output.values()) else []
        )
    }


def read_uidai_html(file_path: str | Path, config_path: str | Path | None = None) -> dict:
    """Read one UIDAI ECMP HTML report from disk."""
    file_path = Path(file_path)
    return read_uidai_html_content(
        file_path.read_bytes(),
        source_name=file_path.name,
        config_path=config_path,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read UIDAI ECMP HTML and calculate category-wise income.")
    parser.add_argument("html_file")
    parser.add_argument("--config", default="category_rates.json")
    parser.add_argument("--output", default="auto_reader_output.json")
    args = parser.parse_args()
    result = read_uidai_html(args.html_file, args.config if Path(args.config).exists() else None)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"Saved full result to: {args.output}")
