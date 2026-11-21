import io
from datetime import date
from decimal import Decimal

import pytest

from auto_reader import read_uidai_html
from extensions import db
from models import CategoryRate, IncomeCategory, IncomeRecord, MonthlyEmailLog, UploadedReport, User
from services import mailer
from services.mailer import is_last_day_of_month
from services.income import calculate_report
from tests.conftest import SAMPLE, login


def test_application_starts_and_login_is_required(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert client.get("/login").status_code == 200


def test_login_and_admin_authorization(client):
    response = login(client, username="viewer", password="test-viewer-password")
    assert response.status_code == 200
    assert client.get("/admin/categories").status_code == 200
    assert client.post("/admin/categories", data={"name": "Unauthorized"}).status_code == 403
    client.post("/logout")
    assert login(client).status_code == 200
    assert client.get("/admin/categories").status_code == 200
    admin_dashboard = client.get("/dashboard").get_data(as_text=True)
    assert 'href="/admin/users"' in admin_dashboard
    assert 'href="/admin/settings"' in admin_dashboard


def test_login_requires_selected_account_type_to_match_credentials(client):
    page = client.get("/login").get_data(as_text=True)
    assert 'value="user"' in page
    assert 'value="admin"' in page

    user_as_admin = client.post(
        "/login",
        data={
            "username": "viewer",
            "password": "test-viewer-password",
            "login_role": "admin",
        },
    )
    assert user_as_admin.status_code == 200
    assert "do not match the selected account type" in user_as_admin.get_data(as_text=True)
    assert client.get("/dashboard").status_code == 302

    assert login(
        client,
        username="viewer",
        password="test-viewer-password",
        role="user",
    ).status_code == 200


def test_regular_user_gets_permitted_dashboard_tabs_and_read_only_configuration(client):
    login(client, username="viewer", password="test-viewer-password")
    dashboard = client.get("/dashboard").get_data(as_text=True)
    assert 'href="/upload"' in dashboard
    assert 'href="/admin/categories"' in dashboard
    assert 'href="/admin/rates"' in dashboard
    assert 'href="/profile"' in dashboard
    assert 'href="/admin/users"' not in dashboard
    assert "Settings &amp; logs" not in dashboard

    categories = client.get("/admin/categories")
    rates = client.get("/admin/rates")
    assert categories.status_code == 200
    assert "Categories are view-only" in categories.get_data(as_text=True)
    assert "Save category" not in categories.get_data(as_text=True)
    assert rates.status_code == 200
    assert "Rates are view-only" in rates.get_data(as_text=True)
    assert "Save effective-dated rate" not in rates.get_data(as_text=True)
    assert client.get("/admin/settings").status_code == 403
    assert client.get("/admin/users").status_code == 403
    assert client.post("/admin/categories", data={"name": "Unauthorized"}).status_code == 403
    assert client.post(
        "/admin/rates",
        data={"category_id": "1", "amount": "30", "effective_from": "2026-11-21"},
    ).status_code == 403


def test_user_can_update_only_their_own_profile(client, app):
    login(client, username="viewer", password="test-viewer-password")
    response = client.post(
        "/profile",
        data={"action": "email", "email": "viewer-updated@example.com"},
    )
    assert response.status_code == 302
    assert client.post(
        "/profile",
        data={
            "action": "password",
            "current_password": "test-viewer-password",
            "new_password": "viewer-new-password-2026",
            "password_confirmation": "viewer-new-password-2026",
        },
    ).status_code == 302
    with app.app_context():
        assert User.query.filter_by(username="viewer").one().email == "viewer-updated@example.com"
        assert User.query.filter_by(username="admin").one().email == "admin@example.com"

    client.post("/logout")
    assert login(
        client,
        username="viewer",
        password="viewer-new-password-2026",
    ).status_code == 200


def test_registration_requires_admin_approval_before_aadhaar_sign_in(client, app):
    registration = client.post(
        "/register",
        data={
            "aadhaar_number": "234567890123",
            "email": "new-member@example.com",
            "password": "member-password-2026",
            "password_confirmation": "member-password-2026",
        },
        follow_redirects=True,
    )
    assert registration.status_code == 200
    assert "An admin must approve it" in registration.get_data(as_text=True)
    with app.app_context():
        member = User.query.filter_by(email="new-member@example.com").one()
        member_id = member.id
        assert member.approval_status == "pending"
        assert member.aadhaar_digest != "234567890123"
        assert member.aadhaar_last4 == "0123"
        assert "234567890123" not in member.username

    pending_login = client.post(
        "/login",
        data={
            "username": "234567890123",
            "password": "member-password-2026",
            "login_role": "user",
        },
    )
    assert pending_login.status_code == 200
    assert "awaiting admin approval" in pending_login.get_data(as_text=True)
    assert client.get("/dashboard").status_code == 302

    login(client)
    assert client.get("/admin/users").status_code == 200
    approved = client.post(
        f"/admin/users/{member_id}/approval",
        data={"approval_status": "approved"},
    )
    assert approved.status_code == 302
    client.post("/logout")
    signed_in = client.post(
        "/login",
        data={
            "username": "234567890123",
            "password": "member-password-2026",
            "login_role": "user",
        },
        follow_redirects=True,
    )
    assert signed_in.status_code == 200
    assert "Member ···· 0123" in signed_in.get_data(as_text=True)
    assert "234567890123" not in signed_in.get_data(as_text=True)


def test_registration_validates_aadhaar_password_and_duplicate(client, app):
    invalid = client.post(
        "/register",
        data={
            "aadhaar_number": "1234",
            "email": "invalid@example.com",
            "password": "member-password-2026",
            "password_confirmation": "member-password-2026",
        },
        follow_redirects=True,
    )
    assert "valid 12-digit Aadhaar" in invalid.get_data(as_text=True)
    assert client.post(
        "/register",
        data={
            "aadhaar_number": "234567890123",
            "email": "member@example.com",
            "password": "member-password-2026",
            "password_confirmation": "member-password-2026",
        },
    ).status_code == 302
    duplicate = client.post(
        "/register",
        data={
            "aadhaar_number": "2345-6789-0123",
            "email": "another@example.com",
            "password": "member-password-2026",
            "password_confirmation": "member-password-2026",
        },
        follow_redirects=True,
    )
    assert "account already exists" in duplicate.get_data(as_text=True)
    with app.app_context():
        assert User.query.filter(User.aadhaar_digest.is_not(None)).count() == 1


def test_revoking_approval_ends_an_existing_user_session(client, app):
    login(client, username="viewer", password="test-viewer-password")
    with app.app_context():
        user = User.query.filter_by(username="viewer").one()
        user.approval_status = "rejected"
        db.session.commit()
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_parser_extracts_all_17_sample_records_and_340_income(app):
    parsed = read_uidai_html(SAMPLE, SAMPLE.parents[2] / "category_rates.json")
    assert parsed["summary"]["total_records"] == 17
    assert parsed["summary"]["total_calculated_income"] == "340.00"
    assert parsed["records"][0]["s_no"] == "1"
    assert parsed["metadata"]["report_date"] == "2026-11-13"

    with app.app_context():
        calculated = calculate_report(parsed["records"], date(2026, 11, 13), ["UPLOADED"])
    assert calculated["total_income"] == "340.00"
    assert calculated["warnings"] == []


def test_upload_preview_confirmation_and_historical_rate_snapshot(client, app, sample_html):
    login(client)
    response = client.post(
        "/upload",
        data={"report": (io.BytesIO(sample_html), "uidai-sample.html")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    preview_url = response.headers["Location"]
    preview = client.get(preview_url)
    assert preview.status_code == 200
    assert "₹340.00" in preview.get_data(as_text=True)
    assert "0 confirmed reports" in client.get("/dashboard").get_data(as_text=True)

    confirmed = client.post(preview_url.replace("/preview", "/confirm"), data={"confirm": "yes"})
    assert confirmed.status_code == 302
    with app.app_context():
        report = UploadedReport.query.one()
        rows = IncomeRecord.query.filter_by(report_id=report.id).all()
        assert report.status == "confirmed"
        assert report.record_count == 17
        assert report.total_income == Decimal("340.00")
        assert len(rows) == 17
        assert all(row.applied_rate == Decimal("20.00") for row in rows)
        rate = CategoryRate.query.one()
        rate.amount = Decimal("25.00")
        db.session.commit()
        assert report.total_income == Decimal("340.00")
        assert all(row.applied_rate == Decimal("20.00") for row in rows)
        assert report.uidai_metadata.extra_metadata["station_id"] == "TEST-STATION-001"


def test_regular_user_can_upload_preview_and_confirm_own_report(client, app, sample_html):
    login(client, username="viewer", password="test-viewer-password")
    response = client.post(
        "/upload",
        data={"report": (io.BytesIO(sample_html), "viewer-report.html")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    preview_url = response.headers["Location"]
    assert client.get(preview_url).status_code == 200
    assert client.post(
        preview_url.replace("/preview", "/confirm"),
        data={"confirm": "yes"},
    ).status_code == 302
    with app.app_context():
        report = UploadedReport.query.one()
        viewer = User.query.filter_by(username="viewer").one()
        report_id = report.id
        assert report.uploader_id == viewer.id
        assert report.status == "confirmed"
        assert report.total_income == Decimal("340.00")
    assert client.post(f"/reports/{report_id}/delete").status_code == 302
    with app.app_context():
        assert UploadedReport.query.count() == 0
        assert IncomeRecord.query.count() == 0


def test_duplicate_report_is_detected_before_second_save(client, app, sample_html):
    login(client)
    for _ in range(2):
        response = client.post(
            "/upload",
            data={"report": (io.BytesIO(sample_html), "same-report.html")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 302
    assert "/reports/1" in response.headers["Location"]
    with app.app_context():
        assert UploadedReport.query.count() == 1


def test_missing_rates_and_unmapped_rows_are_explicit(app):
    with app.app_context():
        category = IncomeCategory(name="New Enrolment", match_rules={"type": "E"})
        db.session.add(category)
        db.session.commit()
        calculated = calculate_report(
            [
                {"type": "E", "status": "UPLOADED"},
                {"type": "X", "status": "UPLOADED"},
            ],
            date(2026, 11, 13),
            ["UPLOADED"],
        )
        assert calculated["missing_rates"] == 1
        assert calculated["unmapped_records"] == 1
        assert len(calculated["warnings"]) == 2
        assert calculated["total_income"] == "0.00"


def test_invalid_html_is_rejected(client, app):
    login(client)
    response = client.post(
        "/upload",
        data={"report": (io.BytesIO(b"<html><body>not a UIDAI report</body></html>"), "invalid.html")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "Unable to parse this UIDAI report" in response.get_data(as_text=True)
    with app.app_context():
        assert UploadedReport.query.count() == 0


def test_user_cannot_access_another_users_report(client, app, sample_html):
    login(client)
    client.post(
        "/upload",
        data={"report": (io.BytesIO(sample_html), "private.html")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        report_id = UploadedReport.query.one().id
    client.post("/logout")
    login(client, username="viewer", password="test-viewer-password")
    assert client.get(f"/reports/{report_id}").status_code == 403
    assert client.get(f"/reports/{report_id}/preview").status_code == 403
    assert client.post(
        f"/reports/{report_id}/delete",
        data={"confirm": "yes"},
    ).status_code == 403
    duplicate = client.post(
        "/upload",
        data={"report": (io.BytesIO(sample_html), "private.html")},
        content_type="multipart/form-data",
    )
    assert duplicate.status_code == 302
    assert duplicate.headers["Location"].endswith("/reports")
    assert "private.html" not in client.get("/reports").get_data(as_text=True)


def test_original_download_and_admin_delete(client, app, sample_html):
    login(client)
    client.post(
        "/upload",
        data={"report": (io.BytesIO(sample_html), "download.html")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        report_id = UploadedReport.query.one().id
    response = client.get(f"/reports/{report_id}/download")
    assert response.status_code == 200
    assert b"Synthetic UIDAI ECMP test report" in response.data
    deleted = client.post(f"/reports/{report_id}/delete")
    assert deleted.status_code == 302
    with app.app_context():
        assert UploadedReport.query.count() == 0


def test_warning_blocks_confirmation_until_rate_or_mapping_is_fixed(client, app, sample_html):
    login(client)
    response = client.post(
        "/upload",
        data={"report": (io.BytesIO(sample_html), "unmapped.html")},
        content_type="multipart/form-data",
    )
    preview_url = response.headers["Location"]
    with app.app_context():
        IncomeCategory.query.filter_by(name="Mandatory Biometric Update").one().enabled = False
        db.session.commit()
    assert "Action required before confirmation" in client.get(preview_url).get_data(as_text=True)
    client.post(preview_url.replace("/preview", "/confirm"), data={"confirm": "yes"})
    with app.app_context():
        report = UploadedReport.query.one()
        assert report.status == "pending"
        assert IncomeRecord.query.count() == 0


def test_scheduled_email_requires_secret_and_checks_calendar_month_end(client):
    assert client.get("/api/monthly-email").status_code == 401
    response = client.get("/api/monthly-email", headers={"Authorization": "Bearer test-cron-secret"})
    assert response.status_code in {200, 204, 503}


def test_admin_pages_render(client):
    login(client)
    for path in ("/dashboard", "/reports", "/admin/categories", "/admin/rates", "/admin/settings", "/admin/users"):
        assert client.get(path).status_code == 200


def test_monthly_email_failure_can_retry_and_sent_month_is_not_resent(app, monkeypatch):
    with app.app_context():
        def fail_send(*_args):
            raise RuntimeError("temporary SMTP failure")

        monkeypatch.setattr(mailer, "_send_email", fail_send)
        with pytest.raises(RuntimeError, match="temporary SMTP failure"):
            mailer.send_monthly_report("2026-10")
        log = MonthlyEmailLog.query.filter_by(month="2026-10").one()
        assert log.status == "failed"
        assert log.attempts == 1

        sent = []
        monkeypatch.setattr(mailer, "_send_email", lambda *args: sent.append(args))
        log, did_send = mailer.send_monthly_report("2026-10")
        assert did_send is True
        assert log.status == "sent"
        assert log.attempts == 2
        _, did_send_again = mailer.send_monthly_report("2026-10")
        assert did_send_again is False
        assert len(sent) == 1


def test_last_calendar_day_handles_february_and_year_boundaries():
    assert is_last_day_of_month(date(2024, 2, 29))
    assert not is_last_day_of_month(date(2024, 2, 28))
    assert is_last_day_of_month(date(2026, 12, 31))


def test_dashboard_calendar_selects_daily_income_and_marks_sunday_holiday(client, app):
    with app.app_context():
        uploader = User.query.filter_by(username="admin").one()
        db.session.add_all(
            [
                UploadedReport(
                    original_filename="confirmed-13-nov.html",
                    report_date=date(2026, 11, 13),
                    record_count=17,
                    total_income=Decimal("340.00"),
                    status="confirmed",
                    sha256="1" * 64,
                    storage_key="reports/confirmed.html",
                    uploader_id=uploader.id,
                ),
                UploadedReport(
                    original_filename="pending-13-nov.html",
                    report_date=date(2026, 11, 13),
                    record_count=2,
                    total_income=Decimal("0.00"),
                    status="pending",
                    sha256="2" * 64,
                    storage_key="reports/pending.html",
                    uploader_id=uploader.id,
                ),
            ]
        )
        db.session.commit()

    login(client)
    selected = client.get("/dashboard?date=2026-11-13")
    selected_html = selected.get_data(as_text=True)
    assert selected.status_code == 200
    assert "Friday, 13 November 2026" in selected_html
    assert "Income · 13 Nov" in selected_html
    assert "₹340.00" in selected_html
    assert "confirmed-13-nov.html" in selected_html
    assert "pending-13-nov.html" in selected_html
    assert "2 uploaded report(s)" in selected_html

    sunday = client.get("/dashboard?date=2026-11-22")
    sunday_html = sunday.get_data(as_text=True)
    assert "Sunday, 22 November 2026" in sunday_html
    assert "Sunday · Holiday" in sunday_html
    assert "22 Nov 2026 - Sunday holiday" in sunday_html
