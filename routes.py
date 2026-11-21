import calendar
import hashlib
import hmac
import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for as flask_url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename

from auto_reader import read_uidai_html_content
from extensions import csrf, db
from models import (
    ActivityLog,
    CategoryRate,
    IncomeCategory,
    IncomeRecord,
    MonthlyEmailLog,
    ShareSettings,
    UIDAIReportMetadata,
    UploadedReport,
    UploadHistory,
    User,
    utcnow,
)
from services.income import calculate_report
from services.dates import business_date
from services.mailer import is_last_day_of_month, send_monthly_report
from services.storage import delete_original, read_original, save_original


bp = Blueprint("main", __name__)


def url_for(endpoint, *args, **kwargs):
    if "." not in endpoint and endpoint != "static":
        endpoint = f"main.{endpoint}"
    return flask_url_for(endpoint, *args, **kwargs)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def log_activity(action, **details):
    db.session.add(ActivityLog(user_id=current_user.id if current_user.is_authenticated else None,
                               action=action, details=details))


def report_for_view(report_id):
    report = db.get_or_404(UploadedReport, report_id)
    if not current_user.is_admin and report.uploader_id != current_user.id:
        abort(403)
    return report


def _share_totals(total, settings):
    if not settings or settings.company_percent is None or settings.user_percent is None:
        return None, None
    amount = Decimal(total or 0)
    return (
        (amount * Decimal(settings.company_percent) / Decimal("100")).quantize(Decimal("0.01")),
        (amount * Decimal(settings.user_percent) / Decimal("100")).quantize(Decimal("0.01")),
    )


def _aadhaar_digest(aadhaar_number):
    secret = current_app.config.get("AADHAAR_LOOKUP_KEY") or current_app.config["SECRET_KEY"]
    return hmac.new(
        str(secret).encode("utf-8"),
        b"ledgerly-aadhaar:" + aadhaar_number.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    selected_role = request.form.get("login_role", "user").strip()
    if selected_role not in {"admin", "user"}:
        selected_role = "user"
    if request.method == "POST":
        login_role = request.form.get("login_role", "").strip()
        if login_role not in {"admin", "user"}:
            flash("Choose whether you are signing in as an admin or user.", "danger")
            return render_template("login.html", selected_role=selected_role)

        username = request.form.get("username", "").strip()
        aadhaar_number = re.sub(r"[\s-]", "", username)
        password = request.form.get("password", "")
        user = None
        if re.fullmatch(r"[2-9][0-9]{11}", aadhaar_number):
            user = User.query.filter_by(aadhaar_digest=_aadhaar_digest(aadhaar_number)).first()
        if user is None:
            user = User.query.filter_by(username=username).first()
        credentials_valid = bool(user and user.check_password(password))
        if credentials_valid and user.role != login_role:
            flash("Those credentials do not match the selected account type.", "danger")
        elif credentials_valid and user.approval_status != "approved":
            message = (
                "Your account is awaiting admin approval."
                if user.approval_status == "pending"
                else "Your account was not approved. Contact the administrator."
            )
            flash(message, "warning")
        elif credentials_valid and user.is_active:
            login_user(user)
            log_activity("login")
            db.session.commit()
            next_path = request.args.get("next", "")
            parsed_next = urlsplit(next_path)
            if (
                parsed_next.path.startswith("/")
                and not parsed_next.netloc
                and not parsed_next.scheme
                and not next_path.startswith("//")
                and "\\" not in next_path
            ):
                return redirect(next_path)
            return redirect(url_for("dashboard"))
        elif not credentials_valid or not user.is_active:
            flash("The username or password is incorrect.", "danger")
    return render_template("login.html", selected_role=selected_role)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        aadhaar_number = re.sub(
            r"[\s-]", "", request.form.get("aadhaar_number", "")
        )
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")
        if not re.fullmatch(r"[2-9][0-9]{11}", aadhaar_number):
            flash("Enter a valid 12-digit Aadhaar number.", "danger")
            return redirect(url_for("register"))
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address.", "danger")
            return redirect(url_for("register"))
        if len(password) < 12:
            flash("Choose a password with at least 12 characters.", "danger")
            return redirect(url_for("register"))
        if password != password_confirmation:
            flash("The passwords do not match.", "danger")
            return redirect(url_for("register"))
        digest = _aadhaar_digest(aadhaar_number)
        if User.query.filter(
            (User.aadhaar_digest == digest) | (User.email == email)
        ).first():
            flash("An account already exists for those details.", "danger")
            return redirect(url_for("register"))
        user = User(
            username=f"member-{uuid.uuid4().hex[:16]}",
            email=email,
            aadhaar_digest=digest,
            aadhaar_last4=aadhaar_number[-4:],
            role="user",
            approval_status="pending",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        db.session.add(
            ActivityLog(
                user_id=user.id,
                action="account_registered",
                details={"approval_status": "pending"},
            )
        )
        db.session.commit()
        flash(
            "Account created. An admin must approve it before you can sign in.",
            "success",
        )
        return redirect(url_for("login"))
    return render_template("register.html")


@bp.post("/logout")
@login_required
def logout():
    log_activity("logout")
    db.session.commit()
    logout_user()
    return redirect(url_for("login"))


@bp.get("/dashboard")
@login_required
def dashboard():
    today = business_date()
    selected_date_text = request.args.get("date", "")
    try:
        selected_date = date.fromisoformat(selected_date_text) if selected_date_text else today
    except ValueError:
        flash("Choose a valid date.", "warning")
        selected_date = today

    month_text = request.args.get("month", "")
    try:
        if month_text:
            month_start = date.fromisoformat(f"{month_text}-01")
            if month_start.strftime("%Y-%m") != month_text:
                raise ValueError
        else:
            month_start = selected_date.replace(day=1)
    except ValueError:
        flash("Choose a valid calendar month.", "warning")
        month_start = selected_date.replace(day=1)
    if selected_date.strftime("%Y-%m") != month_start.strftime("%Y-%m"):
        month_start = selected_date.replace(day=1)
    next_month = (
        date(month_start.year + 1, 1, 1)
        if month_start.month == 12
        else date(month_start.year, month_start.month + 1, 1)
    )

    query = UploadedReport.query
    if not current_user.is_admin:
        query = query.filter_by(uploader_id=current_user.id)
    month_reports = query.filter(
        UploadedReport.report_date >= month_start,
        UploadedReport.report_date < next_month,
    ).order_by(UploadedReport.report_date, UploadedReport.uploaded_at.desc()).all()
    reports = [report for report in month_reports if report.status == "confirmed"]
    selected_reports = [
        report for report in month_reports if report.report_date == selected_date
    ]
    selected_confirmed_reports = [
        report for report in selected_reports if report.status == "confirmed"
    ]
    today_income = sum(
        (r.total_income for r in selected_confirmed_reports), Decimal("0.00")
    )
    month_income = sum((r.total_income for r in reports), Decimal("0.00"))

    all_confirmed_query = UploadedReport.query.filter_by(status="confirmed")
    if not current_user.is_admin:
        all_confirmed_query = all_confirmed_query.filter_by(uploader_id=current_user.id)
    all_confirmed_reports = all_confirmed_query.all()
    total_income = sum((r.total_income for r in all_confirmed_reports), Decimal("0.00"))
    report_ids = [r.id for r in all_confirmed_reports]
    income_records = IncomeRecord.query.filter(IncomeRecord.report_id.in_(report_ids)).all() if report_ids else []
    category_totals = {}
    for row in income_records:
        if row.category_name:
            category_totals[row.category_name] = category_totals.get(row.category_name, Decimal("0.00")) + row.calculated_income
    chart_days = [selected_date - timedelta(days=offset) for offset in range(13, -1, -1)]
    daily_labels = [day.strftime("%d %b") for day in chart_days]
    daily = {}
    chart_reports_query = UploadedReport.query.filter(
        UploadedReport.status == "confirmed",
        UploadedReport.report_date >= chart_days[0],
        UploadedReport.report_date <= selected_date,
    )
    if not current_user.is_admin:
        chart_reports_query = chart_reports_query.filter_by(uploader_id=current_user.id)
    for report in chart_reports_query.all():
        key = report.report_date.isoformat()
        daily[key] = daily.get(key, Decimal("0.00")) + report.total_income
    daily_values = [float(daily.get(day.isoformat(), 0)) for day in chart_days]
    chart_months = []
    for offset in range(5, -1, -1):
        month_index = month_start.year * 12 + month_start.month - 1 - offset
        year, month_index = divmod(month_index, 12)
        chart_months.append((year, month_index + 1))
    month_buckets = {
        f"{year:04d}-{month:02d}": Decimal("0.00")
        for year, month in chart_months
    }
    all_chart_reports_query = UploadedReport.query.filter(
        UploadedReport.status == "confirmed",
        UploadedReport.report_date >= date(chart_months[0][0], chart_months[0][1], 1),
        UploadedReport.report_date < next_month,
    )
    if not current_user.is_admin:
        all_chart_reports_query = all_chart_reports_query.filter_by(uploader_id=current_user.id)
    for report in all_chart_reports_query.all():
        key = report.report_date.strftime("%Y-%m")
        if key in month_buckets:
            month_buckets[key] += report.total_income
    months = list(month_buckets)

    calendar_data = {}
    for report in month_reports:
        key = report.report_date.isoformat()
        day_data = calendar_data.setdefault(
            key, {"income": Decimal("0.00"), "reports": 0}
        )
        day_data["reports"] += 1
        if report.status == "confirmed":
            day_data["income"] += report.total_income
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
        month_start.year, month_start.month
    )
    calendar_weeks = []
    for week in weeks:
        calendar_week = []
        for day_number in week:
            day_date = (
                date(month_start.year, month_start.month, day_number)
                if day_number
                else None
            )
            day_data = calendar_data.get(day_date.isoformat(), {}) if day_date else {}
            calendar_week.append(
                {
                    "date": day_date,
                    "selected": day_date == selected_date,
                    "sunday": bool(day_date and day_date.weekday() == 6),
                    "income": day_data.get("income", Decimal("0.00")),
                    "reports": day_data.get("reports", 0),
                }
            )
        calendar_weeks.append(calendar_week)
    previous_month = date(
        month_start.year - (month_start.month == 1),
        12 if month_start.month == 1 else month_start.month - 1,
        1,
    )
    settings = db.session.get(ShareSettings, 1)
    company_share, user_share = _share_totals(total_income, settings)
    selected_reports.sort(key=lambda report: report.uploaded_at, reverse=True)
    recent_reports = sorted(
        all_confirmed_reports,
        key=lambda report: report.uploaded_at,
        reverse=True,
    )[:6]
    return render_template(
        "dashboard.html",
        today_income=today_income,
        month_income=month_income,
        total_income=total_income,
        total_records=sum(report.record_count for report in all_confirmed_reports),
        total_reports=len(all_confirmed_reports),
        category_totals=category_totals,
        daily_labels=daily_labels,
        daily_values=daily_values,
        month_labels=months,
        month_values=[float(month_buckets[item]) for item in months],
        selected_date=selected_date,
        selected_date_reports=selected_reports,
        calendar_month=month_start,
        calendar_weeks=calendar_weeks,
        previous_month=previous_month,
        next_month=next_month,
        company_share=company_share,
        user_share=user_share,
        shares_configured=company_share is not None,
        recent_reports=recent_reports,
    )


@bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        file = request.files.get("report")
        if not file or not file.filename:
            flash("Choose a UIDAI HTML report to upload.", "warning")
            return redirect(url_for("upload"))
        filename = secure_filename(file.filename)
        if not filename or Path(filename).suffix.lower() not in {".html", ".htm"}:
            flash("Only .html or .htm report files are accepted.", "danger")
            return redirect(url_for("upload"))
        content = file.read()
        if not content:
            flash("The selected file is empty.", "danger")
            return redirect(url_for("upload"))
        digest = hashlib.sha256(content).hexdigest()
        existing = UploadedReport.query.filter_by(sha256=digest).first()
        if existing:
            flash("This report has already been uploaded.", "warning")
            if current_user.is_admin or existing.uploader_id == current_user.id:
                return redirect(url_for("report_details", report_id=existing.id))
            return redirect(url_for("history"))
        try:
            parsed = read_uidai_html_content(content, filename)
        except (ValueError, UnicodeError) as exc:
            flash(f"Unable to parse this UIDAI report: {exc}", "danger")
            return redirect(url_for("upload"))
        report_day = (parsed.get("metadata") or {}).get("report_date")
        try:
            report_date = date.fromisoformat(report_day)
        except (TypeError, ValueError):
            flash("The report does not contain a valid report date.", "danger")
            return redirect(url_for("upload"))

        key = f"reports/{uuid.uuid4().hex}/{filename}"
        try:
            save_original(key, filename, content)
        except (OSError, RuntimeError) as exc:
            current_app.logger.exception("Report storage failed")
            flash(str(exc), "danger")
            return redirect(url_for("upload"))
        metadata = parsed.get("metadata") or {}
        report = UploadedReport(
            original_filename=filename,
            report_date=report_date,
            operator_id=metadata.get("operator_id"),
            station_id=metadata.get("station_id"),
            record_count=parsed["summary"]["total_records"],
            total_income=Decimal("0.00"),
            status="pending",
            sha256=digest,
            storage_key=key,
            metadata_json=metadata,
            parsed_records=parsed["records"],
            parser_summary=parsed["summary"],
            uploader_id=current_user.id,
        )
        try:
            db.session.add(report)
            db.session.flush()
            db.session.add(
                UIDAIReportMetadata(
                    report_id=report.id,
                    report_created_at=metadata.get("report_created_at"),
                    registrar=metadata.get("registrar"),
                    enrolment_agency=metadata.get("enrolment_agency"),
                    last_registered=metadata.get("last_registered"),
                    last_sync=metadata.get("last_sync"),
                    client_version=metadata.get("client_version"),
                    extra_metadata={
                        key: value
                        for key, value in metadata.items()
                        if key not in {
                            "report_created_at", "registrar", "enrolment_agency",
                            "last_registered", "last_sync", "client_version",
                        }
                    },
                )
            )
            db.session.add(
                UploadHistory(
                    report_id=report.id,
                    original_filename=filename,
                    event="uploaded",
                    user_id=current_user.id,
                )
            )
            log_activity("report_uploaded", report_id=report.id, filename=filename)
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                delete_original(key)
            except (OSError, RuntimeError):
                current_app.logger.exception("Could not clean up report after database failure")
            raise
        return redirect(url_for("preview", report_id=report.id))
    return render_template("upload.html")


@bp.get("/reports/<int:report_id>/preview")
@login_required
def preview(report_id):
    report = report_for_view(report_id)
    if report.status != "pending":
        return redirect(url_for("report_details", report_id=report.id))
    settings = db.session.get(ShareSettings, 1)
    calculation = calculate_report(
        report.parsed_records,
        report.report_date,
        settings.eligible_statuses if settings else ["UPLOADED"],
    )
    return render_template("preview.html", report=report, calculation=calculation)


@bp.post("/reports/<int:report_id>/confirm")
@login_required
def confirm_report(report_id):
    report = report_for_view(report_id)
    if report.status != "pending":
        flash("This report has already been processed.", "warning")
        return redirect(url_for("report_details", report_id=report.id))
    if request.form.get("confirm") != "yes":
        flash("Confirm the report after reviewing the calculated totals.", "warning")
        return redirect(url_for("preview", report_id=report.id))
    settings = db.session.get(ShareSettings, 1)
    calculation = calculate_report(
        report.parsed_records,
        report.report_date,
        settings.eligible_statuses if settings else ["UPLOADED"],
    )
    if calculation["warnings"]:
        flash("Resolve every unmapped category and missing rate before confirming.", "danger")
        return redirect(url_for("preview", report_id=report.id))
    report.total_income = Decimal(calculation["total_income"])
    report.status = "confirmed"
    report.confirmed_at = utcnow()
    db.session.add(
        UploadHistory(
            report_id=report.id,
            original_filename=report.original_filename,
            event="confirmed",
            user_id=current_user.id,
        )
    )
    for row in calculation["records"]:
        db.session.add(
            IncomeRecord(
                report_id=report.id,
                category_id=row["category_id"],
                category_name=row["category"],
                applied_rate=Decimal(row["rate"]) if row["rate"] is not None else None,
                calculated_income=Decimal(row["calculated_income"]),
                record_status=row["status"],
                eligible=row["eligible_for_income"],
                record_reference=row.get("enrolment_no_and_date") or row.get("s_no"),
                operator_id=report.operator_id,
                station_id=report.station_id,
                uploader_id=report.uploader_id,
                source_data=row,
            )
        )
    log_activity("report_confirmed", report_id=report.id, record_count=report.record_count)
    db.session.commit()
    flash("Report confirmed and income records saved.", "success")
    return redirect(url_for("report_details", report_id=report.id))


@bp.get("/reports")
@login_required
def history():
    query = UploadedReport.query
    if not current_user.is_admin:
        query = query.filter_by(uploader_id=current_user.id)
    search = request.args.get("q", "").strip()
    if search:
        query = query.filter(UploadedReport.original_filename.ilike(f"%{search}%"))
    start = request.args.get("from", "")
    end = request.args.get("to", "")
    try:
        if start:
            query = query.filter(UploadedReport.report_date >= date.fromisoformat(start))
        if end:
            query = query.filter(UploadedReport.report_date <= date.fromisoformat(end))
    except ValueError:
        flash("Enter valid date filters.", "warning")
    reports = query.order_by(UploadedReport.uploaded_at.desc()).all()
    return render_template("history.html", reports=reports, search=search, start=start, end=end)


@bp.get("/reports/<int:report_id>")
@login_required
def report_details(report_id):
    report = report_for_view(report_id)
    calculation = None
    if report.status == "pending":
        settings = db.session.get(ShareSettings, 1)
        calculation = calculate_report(
            report.parsed_records,
            report.report_date,
            settings.eligible_statuses if settings else ["UPLOADED"],
        )
    return render_template("report_details.html", report=report, calculation=calculation)


@bp.get("/reports/<int:report_id>/download")
@login_required
def download_original(report_id):
    report = report_for_view(report_id)
    try:
        content = read_original(report.storage_key)
    except (OSError, RuntimeError) as exc:
        current_app.logger.exception("Original report download failed")
        abort(503, description=str(exc))
    return send_file(
        __import__("io").BytesIO(content),
        mimetype="text/html",
        as_attachment=True,
        download_name=report.original_filename,
    )


@bp.post("/reports/<int:report_id>/delete")
@login_required
def delete_report(report_id):
    report = report_for_view(report_id)
    try:
        delete_original(report.storage_key)
    except (OSError, RuntimeError) as exc:
        current_app.logger.exception("Report deletion failed")
        flash(str(exc), "danger")
        return redirect(url_for("report_details", report_id=report.id))
    report_id = report.id
    filename = report.original_filename
    db.session.add(
        UploadHistory(
            report_id=None,
            original_filename=filename,
            event="deleted",
            user_id=current_user.id,
        )
    )
    db.session.flush()
    db.session.delete(report)
    log_activity("report_deleted", report_id=report_id, filename=filename)
    db.session.commit()
    flash("Report and its original HTML were deleted.", "success")
    return redirect(url_for("history"))


@bp.route("/admin/categories", methods=["GET", "POST"])
@login_required
def categories():
    if request.method == "POST":
        if not current_user.is_admin:
            abort(403)
        category_id = request.form.get("category_id", type=int)
        name = request.form.get("name", "").strip()
        record_type = request.form.get("record_type", "").strip().upper()
        mandatory = request.form.get("mandatory_biometric_update", "").strip()
        if not name or len(name) > 120 or record_type not in {"E", "U"}:
            flash("Enter a category name and a valid record type (E or U).", "danger")
            return redirect(url_for("categories"))
        rules = {"type": record_type}
        if record_type == "U" and mandatory in {"Yes", "No"}:
            rules["mandatory_biometric_update"] = mandatory
        category = db.session.get(IncomeCategory, category_id) if category_id else None
        if category_id and not category:
            abort(404)
        if IncomeCategory.query.filter(IncomeCategory.name == name, IncomeCategory.id != (category.id if category else -1)).first():
            flash("A category with that name already exists.", "danger")
            return redirect(url_for("categories"))
        if category:
            category.name = name
            category.match_rules = rules
        else:
            db.session.add(IncomeCategory(name=name, match_rules=rules))
        log_activity("category_saved", category=name)
        db.session.commit()
        flash("Category saved.", "success")
        return redirect(url_for("categories"))
    return render_template("categories.html", categories=IncomeCategory.query.order_by(IncomeCategory.name).all())


@bp.post("/admin/categories/<int:category_id>/toggle")
@admin_required
def toggle_category(category_id):
    category = db.get_or_404(IncomeCategory, category_id)
    category.enabled = not category.enabled
    log_activity("category_toggled", category=category.name, enabled=category.enabled)
    db.session.commit()
    flash("Category status updated.", "success")
    return redirect(url_for("categories"))


@bp.route("/admin/rates", methods=["GET", "POST"])
@login_required
def rates():
    if request.method == "POST":
        if not current_user.is_admin:
            abort(403)
        category = db.get_or_404(IncomeCategory, request.form.get("category_id", type=int))
        try:
            amount = Decimal(request.form.get("amount", "").strip())
            effective_from = date.fromisoformat(request.form.get("effective_from", ""))
        except (InvalidOperation, ValueError):
            flash("Enter a valid non-negative rate and effective date.", "danger")
            return redirect(url_for("rates"))
        if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999.99"):
            flash("Rate must be a non-negative amount.", "danger")
            return redirect(url_for("rates"))
        db.session.add(
            CategoryRate(
                category_id=category.id,
                amount=amount.quantize(Decimal("0.01")),
                effective_from=effective_from,
                created_by_id=current_user.id,
            )
        )
        log_activity("category_rate_added", category=category.name, effective_from=effective_from.isoformat())
        db.session.commit()
        flash("Rate saved. Existing income records retain their original applied rate.", "success")
        return redirect(url_for("rates"))
    rate_rows = CategoryRate.query.order_by(CategoryRate.effective_from.desc(), CategoryRate.id.desc()).all()
    return render_template(
        "rates.html",
        categories=IncomeCategory.query.order_by(IncomeCategory.name).all(),
        rate_rows=rate_rows,
        today=business_date().isoformat(),
    )


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "email":
            email = request.form.get("email", "").strip().lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                flash("Enter a valid email address.", "danger")
                return redirect(url_for("profile"))
            existing = User.query.filter(User.email == email, User.id != current_user.id).first()
            if existing:
                flash("That email address is already in use.", "danger")
                return redirect(url_for("profile"))
            current_user.email = email
            log_activity("profile_email_updated")
            db.session.commit()
            flash("Email address updated.", "success")
            return redirect(url_for("profile"))

        if action == "password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            password_confirmation = request.form.get("password_confirmation", "")
            if not current_user.check_password(current_password):
                flash("Your current password is incorrect.", "danger")
                return redirect(url_for("profile"))
            if len(new_password) < 12:
                flash("New passwords must be at least 12 characters.", "danger")
                return redirect(url_for("profile"))
            if new_password != password_confirmation:
                flash("The new passwords do not match.", "danger")
                return redirect(url_for("profile"))
            current_user.set_password(new_password)
            log_activity("profile_password_updated")
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("profile"))

        abort(400)
    return render_template("profile.html")


@bp.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def settings():
    settings_row = db.session.get(ShareSettings, 1)
    if settings_row is None:
        settings_row = ShareSettings(id=1, eligible_statuses=["UPLOADED"], admin_email=current_app.config["ADMIN_EMAIL"] or None)
        db.session.add(settings_row)
        db.session.flush()
    if request.method == "POST":
        statuses = sorted({item.strip().upper() for item in request.form.get("eligible_statuses", "").split(",") if item.strip()})
        if not statuses:
            flash("At least one eligible status is required.", "danger")
            return redirect(url_for("settings"))
        company_raw = request.form.get("company_percent", "").strip()
        user_raw = request.form.get("user_percent", "").strip()
        email = request.form.get("admin_email", "").strip()
        try:
            company = Decimal(company_raw) if company_raw else None
            user = Decimal(user_raw) if user_raw else None
        except InvalidOperation:
            flash("Share percentages must be valid numbers.", "danger")
            return redirect(url_for("settings"))
        if (company is None) != (user is None):
            flash("Configure both share percentages together, or leave both blank.", "danger")
            return redirect(url_for("settings"))
        if company is not None and (
            not company.is_finite()
            or not user.is_finite()
            or company < 0
            or user < 0
            or company > 100
            or user > 100
            or company + user != Decimal("100")
        ):
            flash("Company and user shares must be between 0 and 100 and total exactly 100%.", "danger")
            return redirect(url_for("settings"))
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid admin email address.", "danger")
            return redirect(url_for("settings"))
        settings_row.eligible_statuses = statuses
        settings_row.company_percent = company
        settings_row.user_percent = user
        settings_row.admin_email = email or None
        log_activity("settings_updated")
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("settings"))
    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(30).all()
    upload_logs = UploadHistory.query.order_by(UploadHistory.created_at.desc()).limit(30).all()
    email_logs = MonthlyEmailLog.query.order_by(MonthlyEmailLog.month.desc()).limit(12).all()
    return render_template(
        "settings.html",
        settings=settings_row,
        logs=logs,
        email_logs=email_logs,
        upload_logs=upload_logs,
    )


@bp.route("/admin/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "user")
        if not username or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 12 or role not in {"admin", "user"}:
            flash("Provide a username, valid email, role, and password of at least 12 characters.", "danger")
            return redirect(url_for("users"))
        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("That username or email is already in use.", "danger")
            return redirect(url_for("users"))
        user = User(
            username=username,
            email=email,
            role=role,
            approval_status="approved",
        )
        user.set_password(password)
        db.session.add(user)
        log_activity("user_created", username=username, role=role)
        db.session.commit()
        flash("User account created.", "success")
        return redirect(url_for("users"))
    return render_template("users.html", users=User.query.order_by(User.username).all())


@bp.post("/admin/users/<int:user_id>/toggle")
@admin_required
def toggle_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("users"))
    if user.is_active and user.is_admin and User.query.filter_by(role="admin", is_active=True).count() <= 1:
        flash("At least one active admin must remain.", "danger")
        return redirect(url_for("users"))
    user.is_active = not user.is_active
    log_activity("user_toggled", username=user.username, active=user.is_active)
    db.session.commit()
    flash("User status updated.", "success")
    return redirect(url_for("users"))


@bp.post("/admin/users/<int:user_id>/approval")
@admin_required
def set_user_approval(user_id):
    user = db.get_or_404(User, user_id)
    status = request.form.get("approval_status", "")
    if user.is_admin or status not in {"approved", "rejected"}:
        abort(400)
    user.approval_status = status
    log_activity(
        "user_approval_changed",
        username=user.display_name,
        approval_status=status,
    )
    db.session.commit()
    flash(
        "Account approved." if status == "approved" else "Account rejected.",
        "success",
    )
    return redirect(url_for("users"))


@bp.post("/admin/monthly-report/<month>/retry")
@admin_required
def retry_monthly_report(month):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        abort(404)
    try:
        _, sent = send_monthly_report(month)
    except Exception as exc:
        current_app.logger.exception("Monthly report email retry failed")
        flash(f"Email delivery failed: {exc}", "danger")
    else:
        flash("Monthly report email sent." if sent else "That month is already sent or currently processing.", "success")
    return redirect(url_for("settings"))


@bp.get("/api/monthly-email")
@csrf.exempt
def scheduled_monthly_email():
    secret = current_app.config.get("CRON_SECRET", "")
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not secret or not hmac.compare_digest(supplied, secret):
        abort(401)
    today = business_date()
    if not is_last_day_of_month(today):
        return "", 204
    month = today.strftime("%Y-%m")
    try:
        log, sent = send_monthly_report(month)
    except Exception as exc:
        current_app.logger.exception("Scheduled monthly report email failed")
        return jsonify(error=str(exc)), 503
    return jsonify(month=month, status=log.status, sent=sent), 200
