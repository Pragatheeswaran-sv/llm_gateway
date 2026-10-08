import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import settings

logger = logging.getLogger(__name__)


def send_email_with_attachment(
    recipient: str,
    subject: str,
    body: str,
    attachment_bytes: bytes | None = None,
    filename: str | None = None,
) -> bool:
    """Send email via SMTP with optional compressed file attachment."""
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning(
            "SMTP_USER or SMTP_PASSWORD is not set. Email notification to %s skipped.",
            recipient,
        )
        return False

    msg = MIMEMultipart()
    msg["From"] = settings.SMTP_USER
    msg["To"] = recipient
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))

    if attachment_bytes and filename:
        part = MIMEApplication(attachment_bytes, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Successfully sent export email to %s", recipient)
        return True
    except Exception as exc:
        logger.exception("Failed to send email to %s: %s", recipient, exc)
        return False
