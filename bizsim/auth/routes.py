import random
import string
from datetime import datetime, timezone

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from models import Enrollment, Section, Semester, Course, Instructor, User, db
from utils.email import (
    send_password_reset_email,
    send_verification_email,
    verify_email_token,
    verify_reset_token,
)

from . import auth_bp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_alias(length: int = 10) -> str:
    """Generate a random unique alias like 'Wolf42Eagle'."""
    adjectives = [
        "Swift", "Bold", "Clever", "Bright", "Calm", "Daring", "Epic",
        "Fast", "Grand", "Happy", "Iron", "Jade", "Kind", "Loyal",
        "Mystic", "Noble", "Proud", "Quiet", "Rapid", "Sharp",
    ]
    nouns = [
        "Eagle", "Tiger", "Falcon", "Wolf", "Hawk", "Bear", "Fox",
        "Lion", "Puma", "Lynx", "Raven", "Drake", "Viper", "Cobra",
        "Storm", "Blaze", "Frost", "Shadow", "Ember", "Comet",
    ]
    for _ in range(20):
        alias = (
            random.choice(adjectives)
            + random.choice(nouns)
            + str(random.randint(10, 99))
        )
        if not User.query.filter_by(alias=alias).first():
            return alias
    # Fallback: append more random chars
    return "Player" + "".join(random.choices(string.digits, k=6))


def _active_sections():
    """Return sections grouped for dropdown selection."""
    return (
        Section.query
        .join(Course)
        .join(Instructor)
        .join(Semester)
        .filter(Section.is_active == True, Semester.is_active == True)
        .all()
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("student.dashboard"))

    sections = _active_sections()

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        section_id = request.form.get("section_id", type=int)

        # --- Validation ---
        errors = []

        if not email or "@" not in email:
            errors.append("A valid email address is required.")

        if User.query.filter_by(email=email).first():
            errors.append("An account with that email already exists.")

        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")

        if password != confirm:
            errors.append("Passwords do not match.")

        if not section_id:
            errors.append("Please select your semester, instructor, course, and section.")
        else:
            section = Section.query.get(section_id)
            if not section or not section.is_active:
                errors.append("Selected section is not valid.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/register.html", sections=sections)

        # --- Create user ---
        user = User(email=email, is_verified=False)
        user.set_password(password)
        user.alias = _generate_alias()
        db.session.add(user)
        db.session.flush()  # get user.id before committing

        # Enroll user in selected section
        enrollment = Enrollment(user_id=user.id, section_id=section_id)
        db.session.add(enrollment)

        # Send verification email before committing — roll back if it fails
        try:
            send_verification_email(email)
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to send verification email: %s", exc)
            flash(
                "We could not send a verification email. "
                "Please check your email address and try again.",
                "danger",
            )
            return render_template("auth/register.html", sections=sections)

        db.session.commit()
        flash(
            "Account created! Please check your email to verify your account.",
            "success",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", sections=sections)


@auth_bp.route("/verify/<token>")
def verify_email(token):
    email = verify_email_token(token)
    if not email:
        flash("The verification link is invalid or has expired.", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("No account found for that email.", "danger")
        return redirect(url_for("auth.register"))

    if user.is_verified:
        flash("Your email is already verified. Please log in.", "info")
        return redirect(url_for("auth.login"))

    user.is_verified = True
    db.session.commit()
    flash("Email verified! You can now log in.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and not user.is_verified:
            try:
                send_verification_email(email)
            except Exception as exc:
                current_app.logger.error("Resend verification failed: %s", exc)
        # Always show the same message to avoid user enumeration
        flash(
            "If that email is registered and unverified, a new link has been sent.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/resend_verification.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return _post_login_redirect(current_user)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html")

        if not user.is_verified:
            flash(
                "Please verify your email before logging in. "
                '<a href="/auth/resend-verification">Resend verification email</a>',
                "warning",
            )
            return render_template("auth/login.html")

        login_user(user, remember=remember)
        next_page = request.args.get("next")
        return redirect(next_page or _post_login_redirect_url(user))

    return render_template("auth/login.html")


def _post_login_redirect(user):
    return redirect(_post_login_redirect_url(user))


def _post_login_redirect_url(user):
    if user.is_instructor or user.is_admin:
        return url_for("instructor.dashboard")
    return url_for("student.dashboard")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.is_verified:
            try:
                send_password_reset_email(email)
            except Exception as exc:
                current_app.logger.error("Password reset email failed: %s", exc)
        flash(
            "If that email is registered, a password reset link has been sent.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    email = verify_reset_token(token)
    if not email:
        flash("The reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("auth/reset_password.html", token=token)

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("auth/reset_password.html", token=token)

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("No account found.", "danger")
            return redirect(url_for("auth.login"))

        user.set_password(password)
        db.session.commit()
        flash("Password updated. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if not current_user.check_password(current_pw):
            flash("Current password is incorrect.", "danger")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new_pw != confirm_pw:
            flash("New passwords do not match.", "danger")
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for("auth.change_password"))

    return render_template("auth/change_password.html")


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        new_alias = request.form.get("alias", "").strip()

        if not new_alias:
            flash("Alias cannot be empty.", "danger")
        elif len(new_alias) > 64:
            flash("Alias must be 64 characters or fewer.", "danger")
        elif (
            User.query.filter(
                User.alias == new_alias, User.id != current_user.id
            ).first()
        ):
            flash("That alias is already taken.", "danger")
        else:
            current_user.alias = new_alias
            db.session.commit()
            flash("Alias updated successfully!", "success")

        return redirect(url_for("auth.profile"))

    return render_template("auth/profile.html")
