import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _database_url() -> str:
    """
    Read DATABASE_URL from the environment.
    Render (and Heroku) supply postgres:// URLs, but SQLAlchemy 2.x
    requires the postgresql:// scheme — fix it automatically. Also force the
    psycopg2 driver explicitly (rather than letting SQLAlchemy pick a default),
    since psycopg2-binary is the driver actually installed via requirements.txt —
    this avoids ModuleNotFoundError if the raw URL ever specifies or defaults to
    a different postgres driver (e.g. psycopg3).
    Falls back to a local SQLite file for development.
    """
    url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'bizsim.db')}")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://") or url.startswith("postgresql+"):
        url = "postgresql+psycopg2://" + url.split("://", 1)[1]
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # Mail (Resend API)
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@bizsim.us")

    # File storage
    UPLOAD_FOLDER = os.path.join(BASE_DIR, os.environ.get("UPLOAD_FOLDER", "uploads"))
    GROUND_TRUTH_FOLDER = os.path.join(
        BASE_DIR, os.environ.get("GROUND_TRUTH_FOLDER", "ground_truth")
    )
    DATASET_FOLDER = os.path.join(
        BASE_DIR, os.environ.get("DATASET_FOLDER", "datasets")
    )
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 52428800))
    ALLOWED_EXTENSIONS = {"csv"}

    # Token expiry (seconds)
    EMAIL_TOKEN_EXPIRY = 3600  # 1 hour


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
