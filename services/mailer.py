import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

from flask import current_app
from sqlalchemy.exc import IntegrityError
from extensions import db
from models import MonthlyEmailLog, ShareSettings, UploadedReport


def month_bounds(month):
    year, number = (int(part) for part in month.split("-"))
    start = date(year, number, 1)
    next_month = date(year + (number == 12), 1 if number == 12 else number + 1, 1)
    return start, next_month


def _send_email(recipient, subject, body):
    username = current_app.config.get("GMAIL_USERNAME")
    password = current_app.config.get("GMAIL_APP_PASSWORD")
    if not username or not password:
        raise RuntimeError("GMAIL_USERNAME and GMAIL_APP_PASSWORD are required to send monthly email.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = current_app.config.get("MAIL_FROM") or username
    message["To"] = recipient
    message.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
        server.login(username, password)
        server.send_message(message)


def send_monthly_report(month):
    try:
        start, end = month_bounds(month)
    except (ValueError, TypeError) as exc:
        raise ValueError("Month must use YYYY-MM format.") from exc
    settings = db.session.get(ShareSettings, 1)
    recipient = (settings.admin_email if settings else None) or current_app.config.get("ADMIN_EMAIL")
    if not recipient:
        raise RuntimeError("Configure an admin email address before sending monthly reports.")

    log = MonthlyEmailLog.query.filter_by(month=month).with_for_update().first()
    if log and log.status == "sent":
        return log, False
    if log and log.status == "sending":
        updated = log.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated > datetime.now(timezone.utc) - timedelta(minutes=15):
            return log, False
    if not log:
        log = MonthlyEmailLog(month=month, recipient=recipient, status="sending", attempts=1)
        db.session.add(log)
    else:
        log.status = "sending"
        log.recipient = recipient
        log.error = None
        log.attempts += 1
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = MonthlyEmailLog.query.filter_by(month=month).first()
        if existing and existing.status in {"sending", "sent"}:
            return existing, False
        raise

    reports = UploadedReport.query.filter(
        UploadedReport.status == "confirmed",
        UploadedReport.report_date >= start,
        UploadedReport.report_date < end,
    ).all()
    total = sum((report.total_income for report in reports), start=0)
    category_income = {}
    for report in reports:
        for row in report.income_records:
            if row.category_name:
                category_income[row.category_name] = category_income.get(row.category_name, 0) + row.calculated_income
    company_share = "Not configured"
    user_share = "Not configured"
    if settings and settings.company_percent is not None and settings.user_percent is not None:
        company_share = f"₹{total * settings.company_percent / 100:.2f}"
        user_share = f"₹{total * settings.user_percent / 100:.2f}"
    lines = [
        f"Income report for {month}",
        f"Total calculated income: ₹{total:.2f}",
        "Category-wise income:",
        *(f"- {name}: ₹{amount:.2f}" for name, amount in sorted(category_income.items())),
        f"Company share: {company_share}",
        f"User share: {user_share}",
        f"Confirmed reports: {len(reports)}",
    ]
    try:
        _send_email(recipient, f"Income report - {month}", "\n".join(lines))
    except Exception as exc:
        log.status = "failed"
        log.error = str(exc)[:1000]
        log.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        raise
    log.status = "sent"
    log.sent_at = datetime.now(timezone.utc)
    log.error = None
    db.session.commit()
    return log, True


def is_last_day_of_month(today):
    return (today + timedelta(days=1)).month != today.month
