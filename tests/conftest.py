from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from extensions import db
from models import CategoryRate, IncomeCategory, ShareSettings, User


PROJECT = Path(__file__).resolve().parents[1]
SAMPLE = PROJECT / "tests" / "fixtures" / "sample-uidai-report.html"


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "WTF_CSRF_ENABLED": False,
            "UPLOAD_DIR": tmp_path / "uploads",
            "ADMIN_EMAIL": "admin@example.com",
            "CRON_SECRET": "test-cron-secret",
        }
    )
    with application.app_context():
        db.create_all()
        admin = User(
            username="admin",
            email="admin@example.com",
            role="admin",
            approval_status="approved",
        )
        admin.set_password("test-admin-password")
        user = User(
            username="viewer",
            email="viewer@example.com",
            role="user",
            approval_status="approved",
        )
        user.set_password("test-viewer-password")
        category = IncomeCategory(
            name="Mandatory Biometric Update",
            match_rules={"type": "U", "mandatory_biometric_update": "Yes"},
        )
        db.session.add_all([admin, user, category])
        db.session.flush()
        db.session.add(
            CategoryRate(
                category_id=category.id,
                amount=Decimal("20.00"),
                effective_from=date.min,
                created_by_id=admin.id,
            )
        )
        db.session.add(
            ShareSettings(
                id=1,
                eligible_statuses=["UPLOADED"],
                company_percent=Decimal("60"),
                user_percent=Decimal("40"),
                admin_email="admin@example.com",
            )
        )
        db.session.commit()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username="admin", password="test-admin-password", role=None):
    selected_role = role or ("admin" if username == "admin" else "user")
    return client.post(
        "/login",
        data={"username": username, "password": password, "login_role": selected_role},
        follow_redirects=True,
    )


@pytest.fixture()
def sample_html():
    return SAMPLE.read_bytes()
