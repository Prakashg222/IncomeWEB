import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    AADHAAR_LOOKUP_KEY = os.getenv("AADHAAR_LOOKUP_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    WTF_CSRF_TIME_LIMIT = 3600
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
    CRON_SECRET = os.getenv("CRON_SECRET", "")
    S3_BUCKET = os.getenv("AWS_S3_BUCKET", "")
    S3_REGION = os.getenv("AWS_REGION", "")
    S3_ENDPOINT_URL = (
        os.getenv("AWS_S3_ENDPOINT_URL") or os.getenv("AWS_ENDPOINT_URL_S3") or None
    )
    S3_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    UPLOAD_DIR = BASE_DIR / "instance" / "uploads"
    TESTING = False


class DevelopmentConfig(Config):
    SECRET_KEY = Config.SECRET_KEY or "local-development-key-change-me"
    SQLALCHEMY_DATABASE_URI = Config.SQLALCHEMY_DATABASE_URI or (
        "sqlite:///" + str(BASE_DIR / "instance" / "income-tracker.sqlite3")
    )


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
