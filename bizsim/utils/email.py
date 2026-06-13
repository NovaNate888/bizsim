import resend
from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def generate_verification_token(email: str) -> str:
    return _serializer().dumps(email, salt="email-verify")


def verify_email_token(token: str, expiry: int | None = None) -> str | None:
    """Return the email address encoded in *token*, or None if invalid/expired."""
    max_age = expiry or current_app.config.get("EMAIL_TOKEN_EXPIRY", 3600)
    try:
        email = _serializer().loads(token, salt="email-verify", max_age=max_age)
    except Exception:
        return None
    return email


def generate_reset_token(email: str) -> str:
    return _serializer().dumps(email, salt="password-reset")


def verify_reset_token(token: str, expiry: int = 1800) -> str | None:
    try:
        email = _serializer().loads(token, salt="password-reset", max_age=expiry)
    except Exception:
        return None
    return email


# ---------------------------------------------------------------------------
# Email senders
# ---------------------------------------------------------------------------

def _dev_mode() -> bool:
    return current_app.config.get("DEBUG", False)


def _print_dev_link(label: str, url: str, recipient: str) -> None:
    border = "=" * 60
    print(f"\n{border}", flush=True)
    print(f"  [DEV] {label}", flush=True)
    print(f"  To: {recipient}", flush=True)
    print(f"  {url}", flush=True)
    print(f"{border}\n", flush=True)


def _send_email(subject: str, recipient: str, html: str) -> None:
    resend.api_key = current_app.config["RESEND_API_KEY"]
    resend.Emails.send({
        "from": current_app.config["MAIL_DEFAULT_SENDER"],
        "to": [recipient],
        "subject": subject,
        "html": html,
    })


def send_verification_email(user_email: str) -> None:
    token = generate_verification_token(user_email)
    verify_url = url_for("auth.verify_email", token=token, _external=True)

    if _dev_mode():
        _print_dev_link("Email Verification Link", verify_url, user_email)
        return

    _send_email(
        subject="Verify your BizSim account",
        recipient=user_email,
        html=f"""
        <p>Welcome to <strong>BizSim</strong>!</p>
        <p>Please click the link below to verify your email address.
           This link expires in 1 hour.</p>
        <p><a href="{verify_url}">{verify_url}</a></p>
        <p>If you did not create an account, you can safely ignore this email.</p>
        """,
    )


def send_password_reset_email(user_email: str) -> None:
    token = generate_reset_token(user_email)
    reset_url = url_for("auth.reset_password", token=token, _external=True)

    if _dev_mode():
        _print_dev_link("Password Reset Link", reset_url, user_email)
        return

    _send_email(
        subject="Reset your BizSim password",
        recipient=user_email,
        html=f"""
        <p>You requested a password reset for your <strong>BizSim</strong> account.</p>
        <p>Click the link below to set a new password. This link expires in 30 minutes.</p>
        <p><a href="{reset_url}">{reset_url}</a></p>
        <p>If you did not request this, you can safely ignore this email.</p>
        """,
    )
