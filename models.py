from datetime import date, datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False)
    aadhaar_digest = db.Column(db.String(64), unique=True, nullable=True)
    aadhaar_last4 = db.Column(db.String(4), nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="user")
    approval_status = db.Column(db.String(16), nullable=False, default="pending")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def display_name(self):
        if self.aadhaar_last4:
            return f"Member ···· {self.aadhaar_last4}"
        return self.username


class IncomeCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    match_rules = db.Column(db.JSON, nullable=False, default=dict)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class CategoryRate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("income_category.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    category = db.relationship(IncomeCategory, backref="rates")

    __table_args__ = (
        db.Index("ix_category_rate_effective", "category_id", "effective_from"),
        db.CheckConstraint("amount >= 0", name="nonnegative_amount"),
    )


class ShareSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True, default=1)
    company_percent = db.Column(db.Numeric(5, 2), nullable=True)
    user_percent = db.Column(db.Numeric(5, 2), nullable=True)
    eligible_statuses = db.Column(db.JSON, nullable=False, default=lambda: ["UPLOADED"])
    admin_email = db.Column(db.String(254), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class UploadedReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(255), nullable=False)
    report_date = db.Column(db.Date, nullable=False)
    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    operator_id = db.Column(db.String(120), nullable=True)
    station_id = db.Column(db.String(120), nullable=True)
    record_count = db.Column(db.Integer, nullable=False, default=0)
    total_income = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    status = db.Column(db.String(16), nullable=False, default="pending")
    sha256 = db.Column(db.String(64), unique=True, nullable=False)
    storage_key = db.Column(db.String(512), nullable=False)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)
    parsed_records = db.Column(db.JSON, nullable=False, default=list)
    parser_summary = db.Column(db.JSON, nullable=False, default=dict)
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    uploader = db.relationship(User, backref="reports")
    uidai_metadata = db.relationship(
        "UIDAIReportMetadata",
        back_populates="report",
        cascade="all, delete-orphan",
        uselist=False,
    )


class IncomeRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("uploaded_report.id", ondelete="CASCADE"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("income_category.id"), nullable=True)
    category_name = db.Column(db.String(120), nullable=True)
    applied_rate = db.Column(db.Numeric(12, 2), nullable=True)
    calculated_income = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    record_status = db.Column(db.String(40), nullable=False, default="")
    eligible = db.Column(db.Boolean, nullable=False, default=False)
    record_reference = db.Column(db.String(120), nullable=True)
    operator_id = db.Column(db.String(120), nullable=True)
    station_id = db.Column(db.String(120), nullable=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    source_data = db.Column(db.JSON, nullable=False, default=dict)
    report = db.relationship(UploadedReport, backref=db.backref("income_records", cascade="all, delete-orphan"))


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship(User)


class UIDAIReportMetadata(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(
        db.Integer,
        db.ForeignKey("uploaded_report.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    report_created_at = db.Column(db.String(80), nullable=True)
    registrar = db.Column(db.String(80), nullable=True)
    enrolment_agency = db.Column(db.String(80), nullable=True)
    last_registered = db.Column(db.String(80), nullable=True)
    last_sync = db.Column(db.String(80), nullable=True)
    client_version = db.Column(db.String(80), nullable=True)
    extra_metadata = db.Column(db.JSON, nullable=False, default=dict)
    report = db.relationship(UploadedReport, back_populates="uidai_metadata")


class UploadHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(
        db.Integer,
        db.ForeignKey("uploaded_report.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_filename = db.Column(db.String(255), nullable=False)
    event = db.Column(db.String(32), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    report = db.relationship(UploadedReport)
    user = db.relationship(User)


class MonthlyEmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.String(7), unique=True, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending")
    recipient = db.Column(db.String(254), nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.String(1000), nullable=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
