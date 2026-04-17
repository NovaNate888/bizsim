import os
from flask import Flask, redirect, url_for, render_template
from flask_login import LoginManager
from flask_migrate import Migrate

from config import config
from models import db, User
from utils.email import mail


def create_app(config_name: str | None = None) -> Flask:
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Ensure upload directories exist
    for folder_key in ("UPLOAD_FOLDER", "GROUND_TRUTH_FOLDER", "DATASET_FOLDER"):
        os.makedirs(app.config[folder_key], exist_ok=True)

    # Extensions
    db.init_app(app)
    mail.init_app(app)
    migrate = Migrate(app, db)

    with app.app_context():
        db.create_all()
        _run_schema_migrations(db)
        _seed_admin(app)

    # Flask-Login
    login_manager = LoginManager(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id: str):
        return User.query.get(int(user_id))

    # Blueprints
    from auth import auth_bp
    from student import student_bp
    from instructor import instructor_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(instructor_bp)

    # Template context: inject current year
    from datetime import datetime as _dt

    @app.context_processor
    def inject_globals():
        return {"current_year": _dt.now().year}

    # Root redirect
    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    # Error handlers
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    # CLI command: initialise DB + create default admin
    @app.cli.command("init-db")
    def init_db():
        """Create all tables and seed an admin instructor account."""
        db.create_all()
        _seed_admin(app)
        print("Database initialised.")

    return app


def _run_schema_migrations(db) -> None:
    """Add columns introduced after initial db.create_all() without Flask-Migrate."""
    from sqlalchemy import text
    new_columns = [
        "ALTER TABLE assignments ADD COLUMN profit_matrix_config TEXT",
        "ALTER TABLE submissions ADD COLUMN score_detail TEXT",
    ]
    for sql in new_columns:
        try:
            db.session.execute(text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()


def _seed_admin(app: Flask) -> None:
    """Create a default admin/instructor account if none exists."""
    from models import Instructor

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@bizsim.us")
    admin_password = os.environ.get("ADMIN_PASSWORD", "changeme123")

    if User.query.filter_by(email=admin_email).first():
        return

    admin = User(
        email=admin_email,
        is_verified=True,
        is_instructor=True,
        is_admin=True,
        alias="Admin",
    )
    admin.set_password(admin_password)
    db.session.add(admin)
    db.session.flush()

    instr = Instructor(
        user_id=admin.id,
        name="Site Administrator",
        department="",
    )
    db.session.add(instr)
    db.session.commit()
    print(f"Admin account created: {admin_email} / {admin_password}")


if __name__ == "__main__":
    app = create_app(os.environ.get("FLASK_ENV", "development"))
    app.run(debug=True)
