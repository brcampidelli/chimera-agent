"""Reference email tool — send email over SMTP (Python stdlib, no dependency).

Config-gated: :func:`~chimera.tools.builtin.default_registry` registers it only when
``CHIMERA_SMTP_HOST`` / ``_USER`` / ``_PASSWORD`` are set. The credentials come from the
user's own environment (never hard-coded) and are used only to authenticate to their own
SMTP server. Sending is an outward-facing side effect, so it stays opt-in.
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any

from chimera.config import get_settings
from chimera.tools.base import Tool


class SendEmailTool(Tool):
    name = "send_email"
    description = "Send an email via the configured SMTP server (to, subject, body)."
    parameters = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Email subject."},
            "body": {"type": "string", "description": "Plain-text body."},
        },
        "required": ["to", "subject", "body"],
    }

    def run(self, **kwargs: Any) -> str:
        import smtplib  # lazy (stdlib)

        settings = get_settings()
        if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
            return "error: send_email needs CHIMERA_SMTP_HOST / _USER / _PASSWORD (set them in .env)."
        to = str(kwargs.get("to", "")).strip()
        if not to:
            return "error: send_email requires 'to'"
        message = EmailMessage()
        message["From"] = settings.smtp_from or settings.smtp_user
        message["To"] = to
        message["Subject"] = str(kwargs.get("subject", ""))
        message.set_content(str(kwargs.get("body", "")))
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            return f"error: send_email failed: {exc}"
        return f"sent email to {to}"
