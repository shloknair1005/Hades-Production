"""
Email alert service — stub. Prints to console until SMTP_HOST is configured.
Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO in .env to activate.
"""
import os
from typing import Optional

ALERT_EMAIL_TO: str = os.getenv("ALERT_EMAIL_TO", "superadmin@yourdomain.com")
SMTP_HOST: str = os.getenv("SMTP_HOST", "")


async def send_flag_alert(flag_type: str, severity: str,
                          user_id: Optional[str], org_id: Optional[str], detail: str) -> None:
    subject = f"[Hades Alert] {severity.upper()} — {flag_type}"
    body = (f"A new monitoring flag has been raised.\n\n"
            f"Type:     {flag_type}\nSeverity: {severity}\n"
            f"User:     {user_id or 'unknown'}\nOrg:      {org_id or 'unknown'}\n"
            f"Detail:   {detail}\n\nLog in to the super admin dashboard to review.")
    _send(subject, body)


async def send_ban_notice(banned_user_email: str, org_name: str, reason: Optional[str]) -> None:
    subject = "[Hades] Your account has been suspended"
    body = (f"Your account in '{org_name}' has been suspended.\n"
            f"Reason: {reason or 'No reason provided.'}\n"
            f"Contact your organisation admin if you believe this is an error.")
    _send(subject, body, to=banned_user_email)


def _send(subject: str, body: str, to: str = ALERT_EMAIL_TO) -> None:
    if SMTP_HOST:
        # TODO: swap in real smtplib / SendGrid implementation
        pass
    else:
        print(f"\n[email_stub] TO: {to}")
        print(f"[email_stub] SUBJECT: {subject}")
        print(f"[email_stub] BODY:\n{body}\n")