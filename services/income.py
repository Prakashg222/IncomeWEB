from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from extensions import db
from models import CategoryRate, IncomeCategory


CENT = Decimal("0.01")


def _normalize(value):
    return " ".join(str(value or "").split()).casefold()


def calculate_report(records, report_date: date, eligible_statuses):
    categories = IncomeCategory.query.filter_by(enabled=True).order_by(IncomeCategory.id).all()
    applicable_rates = {}
    if categories:
        rows = db.session.execute(
            select(CategoryRate)
            .where(
                CategoryRate.category_id.in_([item.id for item in categories]),
                CategoryRate.effective_from <= report_date,
            )
            .order_by(CategoryRate.category_id, CategoryRate.effective_from.desc(), CategoryRate.id.desc())
        ).scalars()
        for row in rows:
            applicable_rates.setdefault(row.category_id, row)
    status_set = {_normalize(value) for value in eligible_statuses}
    result_records = []
    category_totals = defaultdict(lambda: {"records": 0, "eligible_records": 0, "income": Decimal("0.00")})
    warnings = set()
    total = Decimal("0.00")

    for source in records:
        category = next(
            (
                item for item in categories
                if all(_normalize(source.get(key)) == _normalize(expected)
                       for key, expected in (item.match_rules or {}).items())
            ),
            None,
        )
        status = str(source.get("status", "")).strip().upper()
        eligible = _normalize(status) in status_set
        rate = None
        if category:
            rate_row = applicable_rates.get(category.id)
            if rate_row:
                rate = Decimal(rate_row.amount)
        else:
            warnings.add("One or more records have no enabled category mapping.")

        amount = rate if eligible and rate is not None else Decimal("0.00")
        if category:
            totals = category_totals[category.name]
            totals["records"] += 1
            if eligible:
                totals["eligible_records"] += 1
                if rate is None:
                    warnings.add(f"No rate is configured for {category.name} on {report_date.isoformat()}.")
            totals["income"] += amount
        total += amount
        result_records.append(
            {
                **source,
                "category_id": category.id if category else None,
                "category": category.name if category else None,
                "rate": str(rate.quantize(CENT)) if rate is not None else None,
                "calculated_income": str(amount.quantize(CENT)),
                "eligible_for_income": eligible,
            }
        )

    return {
        "records": result_records,
        "categories": {
            name: {
                "records": values["records"],
                "eligible_records": values["eligible_records"],
                "income": str(values["income"].quantize(CENT)),
            }
            for name, values in sorted(category_totals.items())
        },
        "total_income": str(total.quantize(CENT)),
        "unmapped_records": sum(1 for item in result_records if item["category"] is None),
        "missing_rates": sum(
            1 for item in result_records
            if item["category"] and item["eligible_for_income"] and item["rate"] is None
        ),
        "warnings": sorted(warnings),
    }
