import json
import os
from decimal import Decimal
from pathlib import Path

import click
from datetime import date, datetime
from flask import Flask, flash, redirect, render_template, url_for
from flask_login import current_user, logout_user

from config import BASE_DIR, DevelopmentConfig, ProductionConfig
from extensions import csrf, db, login_manager, migrate
from models import CategoryRate, IncomeCategory, ShareSettings, User


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    production = os.getenv("FLASK_ENV") == "production" or bool(os.getenv("VERCEL"))
    app.config.from_object(ProductionConfig if production else DevelopmentConfig)
    app.config["IS_PRODUCTION"] = production
    app.config["GMAIL_USERNAME"] = os.getenv("GMAIL_USERNAME", "")
    app.config["GMAIL_APP_PASSWORD"] = os.getenv("GMAIL_APP_PASSWORD", "")
    app.config["MAIL_FROM"] = os.getenv("MAIL_FROM", "")
    if test_config:
        app.config.update(test_config)
        app.config["IS_PRODUCTION"] = test_config.get("IS_PRODUCTION", False)

    if app.config["IS_PRODUCTION"]:
        if not app.config.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY is required in production.")
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise RuntimeError("DATABASE_URL is required in production.")
        if not app.config.get("S3_BUCKET"):
            raise RuntimeError("AWS_S3_BUCKET is required in production for persistent uploads.")
    database_url = app.config["SQLALCHEMY_DATABASE_URI"]
    if database_url.startswith("postgres://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif database_url.startswith("postgresql://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    if not production:
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "main.login"
    login_manager.login_message_category = "warning"
    csrf.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.before_request
    def enforce_user_approval():
        if (
            current_user.is_authenticated
            and not current_user.is_admin
            and current_user.approval_status != "approved"
        ):
            logout_user()
            flash("Your account is no longer approved for dashboard access.", "warning")
            return redirect(url_for("main.login"))

    @app.context_processor
    def inject_template_helpers():
        from flask_wtf.csrf import generate_csrf

        from services.dates import BUSINESS_TIMEZONE

        return {
            "csrf_token": generate_csrf,
            "now": lambda: datetime.now(BUSINESS_TIMEZONE),
        }

    @app.errorhandler(413)
    def too_large(_error):
        flash("The HTML file exceeds the 10 MB upload limit.", "danger")
        return redirect(url_for("main.upload")), 413

    @app.cli.command("seed-config")
    def seed_config():
        """Insert missing categories from category_rates.json without overwriting existing settings."""
        config_path = BASE_DIR / "category_rates.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        for name, definition in data.get("categories", {}).items():
            category = IncomeCategory.query.filter_by(name=name).first()
            if not category:
                category = IncomeCategory(name=name, match_rules=definition.get("match", {}))
                db.session.add(category)
                db.session.flush()
            configured_rate = definition.get("rate")
            if configured_rate not in (None, "") and not category.rates:
                db.session.add(
                    CategoryRate(
                        category_id=category.id,
                        amount=Decimal(str(configured_rate)),
                        effective_from=date.min,
                    )
                )
        settings = db.session.get(ShareSettings, 1)
        if settings is None:
            statuses = data.get("eligible_statuses", ["UPLOADED"])
            company_raw = os.getenv("COMPANY_SHARE_PERCENT", "").strip()
            user_raw = os.getenv("USER_SHARE_PERCENT", "").strip()
            if bool(company_raw) != bool(user_raw):
                raise click.ClickException(
                    "Set both COMPANY_SHARE_PERCENT and USER_SHARE_PERCENT, or leave both unset."
                )
            company = Decimal(company_raw) if company_raw else None
            user = Decimal(user_raw) if user_raw else None
            if company is not None and (
                not company.is_finite()
                or not user.is_finite()
                or company < 0
                or user < 0
                or company > 100
                or user > 100
                or company + user != Decimal("100")
            ):
                raise click.ClickException(
                    "Configured company and user shares must be between 0 and 100 and total 100%."
                )
            settings = ShareSettings(
                id=1,
                eligible_statuses=statuses,
                company_percent=company,
                user_percent=user,
                admin_email=app.config["ADMIN_EMAIL"] or None,
            )
            db.session.add(settings)
        db.session.commit()
        click.echo("Missing categories and default eligibility settings were inserted.")

    @app.cli.command("init-db")
    def init_db():
        """Create tables for a local development database and seed configuration."""
        db.create_all()
        seed_config.callback()

    @app.cli.command("create-admin")
    def create_admin():
        """Create the initial admin account from ADMIN_USERNAME/EMAIL/PASSWORD."""
        username = os.getenv("ADMIN_USERNAME")
        email = os.getenv("ADMIN_EMAIL")
        password = os.getenv("ADMIN_PASSWORD")
        if not username or not email or not password:
            raise click.ClickException("ADMIN_USERNAME, ADMIN_EMAIL and ADMIN_PASSWORD are required.")
        if len(password) < 12:
            raise click.ClickException("ADMIN_PASSWORD must contain at least 12 characters.")
        if User.query.filter((User.username == username) | (User.email == email)).first():
            raise click.ClickException("That username or email already exists.")
        user = User(
            username=username,
            email=email,
            role="admin",
            approval_status="approved",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin account created for {username}.")

    from routes import bp

    app.register_blueprint(bp)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("main.dashboard"))
        return redirect(url_for("main.login"))

    return app


app = create_app()
