import asyncio
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_BACKEND_ROOT / ".env")

logger = logging.getLogger(__name__)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_email: str


def _load_smtp_config() -> SmtpConfig:
    load_dotenv(_BACKEND_ROOT / ".env", override=True)
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").replace(" ", "").strip()
    configured_from = os.getenv("SMTP_FROM_EMAIL", "").strip()
    default_from = "noreply@collabtrack.com"
    if not configured_from or configured_from == default_from:
        from_email = user or default_from
    else:
        from_email = configured_from
    return SmtpConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        from_email=from_email,
    )


def _send_email_sync(*, to_email: str, subject: str, body: str) -> None:
    smtp = _load_smtp_config()

    if not smtp.host:
        logger.info(
            "SMTP not configured; email to %s\nSubject: %s\n%s",
            to_email,
            subject,
            body,
        )
        return

    if not smtp.user or not smtp.password:
        raise smtplib.SMTPException(
            "SMTP credentials are missing. Set SMTP_USER and SMTP_PASSWORD in .env."
        )

    message = EmailMessage()
    message["From"] = smtp.from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
            server.starttls()
            server.login(smtp.user, smtp.password)
            server.send_message(message)
        logger.info(
            "Email sent to %s via %s:%s", to_email, smtp.host, smtp.port
        )
    except Exception:
        logger.exception(
            "Failed to send email to %s via %s:%s",
            to_email,
            smtp.host,
            smtp.port,
        )
        raise


async def _send_email_via_resend(
    *, api_key: str, to_email: str, subject: str, body: str
) -> None:
    import httpx

    smtp = _load_smtp_config()
    from_email = os.getenv("RESEND_FROM_EMAIL", "").strip() or smtp.from_email
    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        resp.raise_for_status()
        logger.info("Email sent to %s via Resend API", to_email)
    except Exception:
        logger.exception("Failed to send email to %s via Resend API", to_email)
        raise


async def send_email(*, to_email: str, subject: str, body: str) -> None:
    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
    if resend_api_key:
        await _send_email_via_resend(
            api_key=resend_api_key,
            to_email=to_email,
            subject=subject,
            body=body,
        )
        return

    await asyncio.to_thread(
        _send_email_sync,
        to_email=to_email,
        subject=subject,
        body=body,
    )


async def send_report_ready_notification(
    *,
    to_email: str,
    assignment_title: str,
    group_name: str,
    assignment_id: str,
    group_id: str,
) -> None:
    report_url = (
        f"{FRONTEND_URL}/instructor/assignments/{assignment_id}/reports/{group_id}"
        "?tab=contribution"
    )
    subject = f"Contribution report ready — {assignment_title} / {group_name}"
    body = (
        f"Hello,\n\n"
        f"The contribution report for {group_name} in assignment "
        f'"{assignment_title}" is ready.\n\n'
        f"View the report: {report_url}\n\n"
        f"— CollabTrack"
    )
    await send_email(to_email=to_email, subject=subject, body=body)


async def send_supervisor_report_notification(
    *,
    to_email: str,
    assignment_title: str,
    group_name: str,
    assignment_id: str,
    group_id: str,
) -> None:
    await send_report_ready_notification(
        to_email=to_email,
        assignment_title=assignment_title,
        group_name=group_name,
        assignment_id=assignment_id,
        group_id=group_id,
    )
