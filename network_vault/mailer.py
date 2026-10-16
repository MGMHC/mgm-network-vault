"""SMTP mailer and password reset token handling."""
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .models import Setting, User, db, get_setting
from .util import FLASK_SECRET, decrypt, encrypt, log_event

logger = logging.getLogger("mailer")


def get_smtp_config():
    """Return dictionary of current SMTP settings."""
    pass_enc = get_setting("smtp_pass_enc") or ""
    return {
        "enabled": get_setting("smtp_enabled") == "1",
        "host": get_setting("smtp_host") or "smtp.office365.com",
        "port": int(get_setting("smtp_port") or 587),
        "encryption": (get_setting("smtp_encryption") or "starttls").lower(),
        "user": get_setting("smtp_user") or "",
        "password": decrypt(pass_enc) if pass_enc else "",
        "from_addr": get_setting("smtp_from") or "donotreply@mgmhealthcare.in",
        "app_url": (get_setting("app_url") or "").rstrip("/"),
    }


def send_email(to_email: str, subject: str, html_body: str, text_body: str = None) -> tuple[bool, str | None]:
    """Send an HTML email via configured SMTP server."""
    cfg = get_smtp_config()
    if not cfg["enabled"]:
        return False, "SMTP is currently disabled in Settings."
    if not cfg["host"]:
        return False, "SMTP host is not configured."
    if not to_email:
        return False, "Recipient email address is required."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_email

    if not text_body:
        # Fallback plain text representation
        text_body = "Please view this message in an email client that supports HTML."

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        timeout = 20
        enc = cfg["encryption"]
        if enc == "ssl" or cfg["port"] == 465:
            ctx = ssl.create_default_context()
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=timeout, context=ctx)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout)
            server.ehlo()
            if enc == "starttls":
                ctx = ssl.create_default_context()
                server.starttls(context=ctx)
                server.ehlo()

        if cfg["user"] and cfg["password"]:
            server.login(cfg["user"], cfg["password"])

        server.send_message(msg)
        server.quit()
        logger.info("Sent email to %s (subject: %s)", to_email, subject)
        return True, None
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        return False, str(e)


def _get_serializer():
    return URLSafeTimedSerializer(FLASK_SECRET, salt="password-reset-v1")


def generate_reset_token(user: User) -> str:
    """Generate a signed, tamper-proof password reset token tied to the user's current password hash."""
    s = _get_serializer()
    # Incorporate password hash suffix so if password changes, old tokens immediately invalidate
    pwh_frag = (user.password_hash or "")[-20:]
    return s.dumps({"uid": user.id, "u": user.username, "frag": pwh_frag})


def verify_reset_token(token: str, max_age: int = 900) -> tuple[User | None, str | None]:
    """Verify reset token within max_age (default 15 minutes). Returns (user, error_message)."""
    s = _get_serializer()
    try:
        data = s.loads(token, max_age=max_age)
    except SignatureExpired:
        return None, "This password reset link has expired. Please request a new one."
    except (BadSignature, Exception):
        return None, "Invalid password reset link. Please request a new one."

    uid = data.get("uid")
    user = db.session.get(User, uid)
    if not user:
        return None, "User account no longer exists."

    expected_frag = (user.password_hash or "")[-20:]
    if data.get("frag") != expected_frag:
        return None, "This reset link was already used or invalidated by a previous password update."

    return user, None


def send_password_reset_email(user: User, base_url: str) -> tuple[bool, str | None]:
    """Construct and dispatch the password reset email to the user."""
    if not user.email:
        return False, f"User '{user.username}' does not have a registered email address."

    token = generate_reset_token(user)
    clean_base = (base_url or get_smtp_config()["app_url"] or "").rstrip("/")
    reset_url = f"{clean_base}/reset-password?token={token}"

    subject = "MGM Network Vault - Password Reset Request"
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f6f4ef; margin: 0; padding: 24px; color: #1b2547; }}
    .email-container {{ max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e6e0d4; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.06); }}
    .email-header {{ background: #25366C; padding: 24px 30px; text-align: center; }}
    .email-header h1 {{ margin: 0; color: #ffffff; font-size: 20px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; }}
    .email-header p {{ margin: 4px 0 0; color: #d9b98a; font-size: 13px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }}
    .email-body {{ padding: 32px 30px; }}
    .email-body h2 {{ margin: 0 0 16px; font-size: 18px; color: #1b2547; }}
    .email-body p {{ margin: 0 0 16px; font-size: 14px; line-height: 1.6; color: #4a5568; }}
    .btn-wrap {{ text-align: center; margin: 28px 0; }}
    .btn {{ display: inline-block; background: #25366C; color: #ffffff !important; padding: 13px 28px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 700; letter-spacing: 0.02em; box-shadow: 0 2px 6px rgba(37,54,108,0.25); }}
    .btn:hover {{ background: #1C2A55; }}
    .alert-box {{ background: #fdfaf3; border: 1px solid #e9dfcf; border-radius: 8px; padding: 14px; margin: 20px 0; font-size: 13px; color: #7c6031; }}
    .url-fallback {{ word-break: break-all; font-family: ui-monospace, monospace; font-size: 12px; background: #f8fafc; padding: 10px; border-radius: 6px; border: 1px solid #e2e8f0; color: #334155; }}
    .email-footer {{ background: #faf8f4; border-top: 1px solid #e6e0d4; padding: 18px 30px; text-align: center; font-size: 12px; color: #737373; }}
  </style>
</head>
<body>
  <div class="email-container">
    <div class="email-header">
      <h1>MGM Healthcare</h1>
      <p>Network Vault · Security Service</p>
    </div>
    <div class="email-body">
      <h2>Password Reset Request</h2>
      <p>Hello <b>{user.username}</b>,</p>
      <p>We received a request to reset your password for your <b>MGM Network Vault</b> account. Click the button below to choose a new password:</p>
      <div class="btn-wrap">
        <a href="{reset_url}" target="_blank" class="btn">Reset My Password</a>
      </div>
      <div class="alert-box">
        <strong>Security Notice:</strong> This link is valid for <strong>15 minutes</strong> and can only be used once. If you did not request this password reset, you can safely ignore this email — your account remains secure.
      </div>
      <p style="font-size:12px;color:#718096;margin-bottom:6px;">If the button above does not work, copy and paste this link into your browser:</p>
      <div class="url-fallback">{reset_url}</div>
    </div>
    <div class="email-footer">
      MGM Network Vault · Network Infrastructure & Security Team<br>
      This is an automated system email. Please do not reply directly.
    </div>
  </div>
</body>
</html>"""

    text = f"""MGM Healthcare - Network Vault
Password Reset Request

Hello {user.username},

We received a request to reset your password for MGM Network Vault.
Please use the following link within 15 minutes to choose a new password:

{reset_url}

If you did not request a password reset, you can ignore this email.
"""

    return send_email(user.email, subject, html, text)
