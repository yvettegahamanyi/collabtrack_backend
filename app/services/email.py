import asyncio
import logging
import os
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "noreply@collabtrack.com")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


def _send_email_sync(*, to_email: str, subject: str, body: str) -> None:
    if not SMTP_HOST:
        logger.info(
            "SMTP not configured; email to %s\nSubject: %s\n%s",
            to_email,
            subject,
            body,
        )
        return

    message = EmailMessage()
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message)


async def send_email(*, to_email: str, subject: str, body: str) -> None:
    await asyncio.to_thread(
        _send_email_sync,
        to_email=to_email,
        subject=subject,
        body=body,
    )


async def send_supervisor_report_notification(
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
