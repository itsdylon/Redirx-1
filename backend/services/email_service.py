"""
Email service using Resend for transactional and drip emails.

All sends are fire-and-forget: errors are caught and logged, never raised.
This ensures email failures never block critical paths (login, job processing, etc.).
"""

import logging
import os
from typing import Optional, Dict, Any

import resend
from jinja2 import Environment, FileSystemLoader

from redirx.config import Config
from redirx.database import SupabaseClient

logger = logging.getLogger(__name__)

# Template directory relative to this file
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "email")


class EmailType:
    """Constants for email types (used in email_preferences and email_log)."""
    WELCOME = "welcome"
    MAPPING_COMPLETE = "mapping_complete"
    MAPPING_FAILED = "mapping_failed"
    ONBOARD_NUDGE = "onboard_nudge"

    # Types that ignore preference opt-outs (always send)
    MANDATORY_TYPES = {WELCOME}


class EmailService:
    """Core email service backed by Resend."""

    def __init__(self):
        api_key = Config.RESEND_API_KEY
        if api_key:
            resend.api_key = api_key
        else:
            logger.warning("RESEND_API_KEY not set — emails will not be sent")

        self._jinja = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            autoescape=True,
        )
        self._from_address = Config.EMAIL_FROM_ADDRESS
        self._app_url = Config.APP_BASE_URL.rstrip("/")

    # ------------------------------------------------------------------
    # Core send
    # ------------------------------------------------------------------

    def send_email(
        self,
        user_id: Optional[str],
        to_email: str,
        email_type: str,
        subject: str,
        template_name: str,
        context: Optional[Dict[str, Any]] = None,
        skip_preference_check: bool = False,
    ) -> Optional[str]:
        """
        Render and send an email. Returns the Resend message ID on success, None on failure.

        Never raises — all errors are caught and logged.
        """
        try:
            if not Config.RESEND_API_KEY:
                logger.info(f"Email skipped (no API key): {email_type} -> {to_email}")
                return None

            # Check opt-out preference (unless mandatory or explicitly skipped)
            if (
                not skip_preference_check
                and email_type not in EmailType.MANDATORY_TYPES
                and user_id
                and self._is_opted_out(user_id, email_type)
            ):
                self._log_send(user_id, to_email, email_type, subject, status="skipped")
                logger.info(f"Email skipped (opted out): {email_type} -> {to_email}")
                return None

            # Build context
            ctx = {
                "app_url": self._app_url,
                "unsubscribe_url": self._unsubscribe_url(user_id, email_type) if user_id else None,
                **(context or {}),
            }

            html = self._jinja.get_template(template_name).render(ctx)

            # Build headers
            headers: Dict[str, str] = {}
            if ctx.get("unsubscribe_url"):
                headers["List-Unsubscribe"] = f"<{ctx['unsubscribe_url']}>"

            params: Dict[str, Any] = {
                "from": self._from_address,
                "to": [to_email],
                "subject": subject,
                "html": html,
            }
            if headers:
                params["headers"] = headers

            result = resend.Emails.send(params)
            message_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)

            self._log_send(user_id, to_email, email_type, subject, resend_message_id=message_id)
            logger.info(f"Email sent: {email_type} -> {to_email} (id={message_id})")
            return message_id

        except Exception:
            import traceback
            err = traceback.format_exc()
            logger.exception(f"Email send failed: {email_type} -> {to_email}")
            try:
                self._log_send(
                    user_id, to_email, email_type, subject,
                    status="failed", error=err[:2000],
                )
            except Exception:
                pass
            return None

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def send_welcome(self, user_id: str, to_email: str, user_name: str = "") -> Optional[str]:
        return self.send_email(
            user_id=user_id,
            to_email=to_email,
            email_type=EmailType.WELCOME,
            subject="Welcome to Redirx",
            template_name="welcome.html",
            context={"user_name": user_name},
        )

    def send_mapping_complete(
        self,
        user_id: str,
        to_email: str,
        project_name: str,
        total_mappings: int,
        session_id: str,
        old_site_domain: str = "",
        new_site_domain: str = "",
    ) -> Optional[str]:
        return self.send_email(
            user_id=user_id,
            to_email=to_email,
            email_type=EmailType.MAPPING_COMPLETE,
            subject=f"Your redirects are ready — {project_name}",
            template_name="mapping_complete.html",
            context={
                "project_name": project_name,
                "total_mappings": total_mappings,
                "session_id": session_id,
                "old_site_domain": old_site_domain,
                "new_site_domain": new_site_domain,
            },
        )

    def send_mapping_failed(
        self,
        user_id: str,
        to_email: str,
        project_name: str,
        error_summary: str = "",
        session_id: str = "",
    ) -> Optional[str]:
        return self.send_email(
            user_id=user_id,
            to_email=to_email,
            email_type=EmailType.MAPPING_FAILED,
            subject=f"Mapping job failed — {project_name}",
            template_name="mapping_failed.html",
            context={
                "project_name": project_name,
                "error_summary": error_summary[:500] if error_summary else "",
                "session_id": session_id,
            },
        )

    def send_nudge(self, user_id: str, to_email: str, day: int) -> Optional[str]:
        template_map = {1: "nudge_day1.html", 3: "nudge_day3.html", 7: "nudge_day7.html"}
        subject_map = {
            1: "Quick tip: upload your first CSV",
            3: "Don't miss out on automated redirects",
            7: "Your Redirx account is waiting",
        }
        template = template_map.get(day)
        subject = subject_map.get(day)
        if not template or not subject:
            logger.warning(f"Unknown nudge day: {day}")
            return None

        return self.send_email(
            user_id=user_id,
            to_email=to_email,
            email_type=EmailType.ONBOARD_NUDGE,
            subject=subject,
            template_name=template,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _unsubscribe_url(self, user_id: str, email_type: str) -> str:
        return f"{self._app_url}/api/email/unsubscribe?user_id={user_id}&type={email_type}"

    def _is_opted_out(self, user_id: str, email_type: str) -> bool:
        try:
            client = SupabaseClient.get_client()
            result = (
                client.table("email_preferences")
                .select("opted_out")
                .eq("user_id", user_id)
                .eq("email_type", email_type)
                .maybe_single()
                .execute()
            )
            if result and result.data:
                return result.data.get("opted_out", False)
            return False
        except Exception:
            logger.exception("Failed to check email preferences")
            return False

    def _log_send(
        self,
        user_id: Optional[str],
        to_email: str,
        email_type: str,
        subject: str,
        resend_message_id: Optional[str] = None,
        status: str = "sent",
        error: Optional[str] = None,
    ) -> None:
        try:
            client = SupabaseClient.get_client()
            row: Dict[str, Any] = {
                "to_email": to_email,
                "email_type": email_type,
                "subject": subject,
                "status": status,
            }
            if user_id:
                row["user_id"] = user_id
            if resend_message_id:
                row["resend_message_id"] = resend_message_id
            if error:
                row["error"] = error[:5000]
            client.table("email_log").insert(row).execute()
        except Exception:
            logger.exception("Failed to log email send")
