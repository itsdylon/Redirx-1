"""
Trial invite service for premium trial code generation, validation, and redemption.
"""
import base64
import csv
import hashlib
import io
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import sys
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

from redirx.config import Config
from redirx.database import SupabaseClient

logger = logging.getLogger(__name__)


class TrialService:
    """Handles trial invite code lifecycle: generation, validation, redemption."""

    def __init__(self):
        self.client = SupabaseClient.get_client()
        self.pepper = Config.TRIAL_INVITE_PEPPER or ''

    # ========================================================================
    # Code generation helpers
    # ========================================================================

    @staticmethod
    def _generate_raw_code() -> str:
        """Generate a raw invite code: rx_ + base32-encoded random bytes."""
        raw_bytes = secrets.token_bytes(15)
        encoded = base64.b32encode(raw_bytes).decode('ascii').lower().rstrip('=')
        return f"rx_{encoded}"

    def _hash_code(self, raw_code: str) -> str:
        """Hash a raw code with the pepper for storage."""
        return hashlib.sha256((raw_code + self.pepper).encode()).hexdigest()

    @staticmethod
    def _extract_prefix(raw_code: str) -> str:
        """Extract the first 8 characters after 'rx_' for indexed lookup."""
        return raw_code[3:11] if len(raw_code) >= 11 else raw_code[3:]

    # ========================================================================
    # Campaign CRUD
    # ========================================================================

    def create_campaign(self, name: str, slug: str, channel: str = None,
                        template_version: str = None, owner_user_id: str = None,
                        invite_type: str = 'trial') -> dict:
        """Create a new trial campaign."""
        data = {
            'name': name,
            'slug': slug,
            'invite_type': invite_type,
        }
        if channel:
            data['channel'] = channel
        if template_version:
            data['template_version'] = template_version
        if owner_user_id:
            data['owner_user_id'] = owner_user_id

        result = self.client.table('trial_campaigns').insert(data).execute()
        return result.data[0] if result.data else {}

    def list_campaigns(self) -> list:
        """List all campaigns ordered by creation date (newest first)."""
        result = self.client.table('trial_campaigns').select('*').order(
            'created_at', desc=True
        ).execute()
        return result.data or []

    def get_campaign_stats(self, campaign_id: str) -> dict:
        """Get invite counts grouped by status for a campaign."""
        result = self.client.table('trial_invites').select(
            'status'
        ).eq('campaign_id', campaign_id).execute()

        stats = {'created': 0, 'sent': 0, 'pending_payment': 0, 'redeemed': 0, 'expired': 0, 'revoked': 0}
        for row in (result.data or []):
            status = row.get('status', '')
            if status in stats:
                stats[status] += 1
        stats['total'] = sum(stats.values())
        return stats

    # ========================================================================
    # Invite operations
    # ========================================================================

    def generate_invites(self, campaign_id: str, count: int = 1,
                         recipient_emails: list[str] = None,
                         expires_days: int = 90,
                         credits_granted: int = 50000,
                         trial_days: int = 14,
                         created_by_user_id: str = None,
                         invite_type: str = 'trial') -> list[dict]:
        """
        Generate invite codes for a campaign.

        If recipient_emails is provided, generates one per email (ignores count).
        Returns list of dicts with raw_code (shown once, never stored).
        """
        if recipient_emails:
            targets = [(email.strip().lower(), None) for email in recipient_emails if email.strip()]
        else:
            targets = [(None, None) for _ in range(count)]

        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()

        results = []
        invite_rows = []
        event_rows = []

        for email, _ in targets:
            raw_code = self._generate_raw_code()
            code_hash = self._hash_code(raw_code)
            code_prefix = self._extract_prefix(raw_code)

            invite_data = {
                'code': raw_code,
                'code_hash': code_hash,
                'code_prefix': code_prefix,
                'campaign_id': campaign_id,
                'status': 'created',
                'credits_granted': credits_granted,
                'trial_days': trial_days,
                'max_redemptions': 1,
                'redemptions': 0,
                'expires_at': expires_at,
                'invite_type': invite_type,
            }
            if email:
                invite_data['recipient_email'] = email
            if created_by_user_id:
                invite_data['created_by_user_id'] = created_by_user_id

            invite_rows.append(invite_data)
            results.append({
                'raw_code': raw_code,
                'recipient_email': email,
                'code_prefix': code_prefix,
            })

        # Batch insert invites
        inserted = self.client.table('trial_invites').insert(invite_rows).execute()

        # Log creation events
        if inserted.data:
            for row in inserted.data:
                event_rows.append({
                    'invite_id': row['id'],
                    'event': 'created',
                    'actor_id': created_by_user_id,
                    'meta': {},
                })
            self.client.table('invite_events').insert(event_rows).execute()

            # Attach invite IDs to results
            for i, invite in enumerate(inserted.data):
                if i < len(results):
                    results[i]['id'] = invite['id']
                    results[i]['created_at'] = invite['created_at']

        return results

    def mark_sent(self, invite_ids: list[str], actor_id: str = None) -> int:
        """Mark invites as sent. Returns count of updated rows."""
        now = datetime.now(timezone.utc).isoformat()
        result = self.client.table('trial_invites').update({
            'status': 'sent',
            'sent_at': now,
            'updated_at': now,
        }).in_('id', invite_ids).eq('status', 'created').execute()

        updated = result.data or []
        # Log events
        if updated:
            events = [{
                'invite_id': row['id'],
                'event': 'sent',
                'actor_id': actor_id,
                'meta': {},
            } for row in updated]
            self.client.table('invite_events').insert(events).execute()

        return len(updated)

    def list_invites(self, campaign_id: str = None, status: str = None) -> list:
        """List invites with optional filtering."""
        query = self.client.table('trial_invites').select(
            '*, trial_campaigns(name, slug)'
        ).order('created_at', desc=True)

        if campaign_id:
            query = query.eq('campaign_id', campaign_id)
        if status:
            query = query.eq('status', status)

        result = query.execute()
        return result.data or []

    @staticmethod
    def parse_recipient_csv(csv_content: str) -> list[str]:
        """Extract and deduplicate emails from CSV content."""
        emails = set()
        reader = csv.reader(io.StringIO(csv_content))
        for row in reader:
            for cell in row:
                cell = cell.strip().lower()
                if '@' in cell and '.' in cell:
                    emails.add(cell)
        return sorted(emails)

    # ========================================================================
    # Validation & Redemption
    # ========================================================================

    def validate_code(self, raw_code: str) -> tuple[bool, str, Optional[dict]]:
        """
        Validate an invite code.

        Returns:
            (is_valid, error_message, invite_row_with_campaign)
        """
        if not raw_code or not raw_code.startswith('rx_'):
            return False, 'Invalid code format', None

        prefix = self._extract_prefix(raw_code)
        code_hash = self._hash_code(raw_code)

        # Look up candidates by prefix (indexed)
        # Also accept 'pending_payment' so users can retry abandoned checkouts
        result = self.client.table('trial_invites').select(
            '*, trial_campaigns(name, slug, invite_type)'
        ).eq('code_prefix', prefix).in_(
            'status', ['created', 'sent', 'pending_payment']
        ).execute()

        candidates = result.data or []

        # Find the matching hash
        invite = None
        for candidate in candidates:
            if candidate.get('code_hash') == code_hash:
                invite = candidate
                break

        if not invite:
            return False, 'Invalid or expired invite code', None

        # Check expiry
        if invite.get('expires_at'):
            expires = datetime.fromisoformat(invite['expires_at'].replace('Z', '+00:00'))
            if expires < datetime.now(timezone.utc):
                return False, 'This invite code has expired', None

        # Check redemptions
        if invite.get('redemptions', 0) >= invite.get('max_redemptions', 1):
            return False, 'This invite code has already been used', None

        return True, '', invite

    def redeem_invite(self, raw_code: str, user_id: str, user_email: str,
                      ip_address: str = None) -> tuple[bool, str, Optional[dict]]:
        """
        Redeem an invite code for a user.

        Returns:
            (success, error_message, invite_data)
        """
        is_valid, error_msg, invite = self.validate_code(raw_code)
        if not is_valid:
            return False, error_msg, None

        # Check recipient email restriction
        if invite.get('recipient_email'):
            if invite['recipient_email'].lower() != user_email.lower():
                return False, 'This invite code was issued to a different email address', None

        # Check if user is already on a non-launch plan
        profile_result = self.client.table('user_profiles').select(
            'plan'
        ).eq('id', user_id).single().execute()

        if profile_result.data:
            current_plan = profile_result.data.get('plan', 'launch')
            if current_plan not in ('launch',):
                return False, f'You are already on the {current_plan} plan', None

        # Provision trial on user profile
        trial_days = invite.get('trial_days', 14)
        credits_granted = invite.get('credits_granted', 50000)
        trial_expires = (datetime.now(timezone.utc) + timedelta(days=trial_days)).isoformat()
        now = datetime.now(timezone.utc).isoformat()

        self.client.table('user_profiles').update({
            'plan': 'premium_trial',
            'trial_expires_at': trial_expires,
            'credits_limit': credits_granted,
            'credits_used': 0,
            'quick_match_limit': None,  # unlimited
            'quick_match_used': 0,
            'max_concurrent_projects': 3,
            'acquisition_campaign_id': invite.get('campaign_id'),
            'acquisition_invite_id': invite.get('id'),
            'plan_started_at': now,
        }).eq('id', user_id).execute()

        # Update invite
        new_redemptions = invite.get('redemptions', 0) + 1
        invite_update = {
            'redemptions': new_redemptions,
            'redeemed_at': now,
            'redeemed_by_user_id': user_id,
            'updated_at': now,
        }
        if new_redemptions >= invite.get('max_redemptions', 1):
            invite_update['status'] = 'redeemed'

        self.client.table('trial_invites').update(invite_update).eq(
            'id', invite['id']
        ).execute()

        # Log redemption event
        self.client.table('invite_events').insert({
            'invite_id': invite['id'],
            'event': 'redeemed',
            'actor_id': user_id,
            'meta': {
                'user_email': user_email,
                'ip_address': ip_address,
                'trial_days': trial_days,
                'credits_granted': credits_granted,
            },
        }).execute()

        return True, '', {
            'trial_days': trial_days,
            'credits_granted': credits_granted,
            'trial_expires_at': trial_expires,
            'campaign_name': invite.get('trial_campaigns', {}).get('name', ''),
        }

    # ========================================================================
    # Founder checkout operations
    # ========================================================================

    def initiate_founder_checkout(self, invite_id: str, stripe_session_id: str,
                                  user_id: str) -> None:
        """Set invite to pending_payment and store the Stripe session ID."""
        now = datetime.now(timezone.utc).isoformat()
        self.client.table('trial_invites').update({
            'status': 'pending_payment',
            'stripe_checkout_session_id': stripe_session_id,
            'updated_at': now,
        }).eq('id', invite_id).execute()

        self.client.table('invite_events').insert({
            'invite_id': invite_id,
            'event': 'pending_payment',
            'actor_id': user_id,
            'meta': {'stripe_session_id': stripe_session_id},
        }).execute()

    def redeem_founder_invite(self, invite_id: str, user_id: str,
                              email: str) -> None:
        """Mark a founder invite as redeemed after Stripe payment completes."""
        now = datetime.now(timezone.utc).isoformat()

        # Update invite status
        self.client.table('trial_invites').update({
            'status': 'redeemed',
            'redeemed_at': now,
            'redeemed_by_user_id': user_id,
            'redemptions': 1,
            'updated_at': now,
        }).eq('id', invite_id).execute()

        # Get invite to find campaign_id for acquisition tracking
        invite_result = self.client.table('trial_invites').select(
            'campaign_id'
        ).eq('id', invite_id).execute()

        campaign_id = None
        if invite_result.data:
            campaign_id = invite_result.data[0].get('campaign_id')

        # Set acquisition tracking on user_profiles
        self.client.table('user_profiles').update({
            'acquisition_campaign_id': campaign_id,
            'acquisition_invite_id': invite_id,
        }).eq('id', user_id).execute()

        # Log redemption event
        self.client.table('invite_events').insert({
            'invite_id': invite_id,
            'event': 'redeemed',
            'actor_id': user_id,
            'meta': {'user_email': email, 'type': 'founder'},
        }).execute()

        logger.info(f"Founder invite {invite_id} redeemed by user {user_id}")

    # ========================================================================
    # Admin operations
    # ========================================================================

    def revoke_invite(self, invite_id: str, admin_id: str) -> bool:
        """Revoke an invite. Returns True if updated."""
        now = datetime.now(timezone.utc).isoformat()
        result = self.client.table('trial_invites').update({
            'status': 'revoked',
            'updated_at': now,
        }).eq('id', invite_id).in_(
            'status', ['created', 'sent']
        ).execute()

        if result.data:
            self.client.table('invite_events').insert({
                'invite_id': invite_id,
                'event': 'revoked',
                'actor_id': admin_id,
                'meta': {},
            }).execute()
            return True
        return False

    def expire_trials(self) -> int:
        """Run the expire_premium_trials RPC. Returns count of expired users."""
        result = self.client.rpc('expire_premium_trials').execute()
        count = result.data if isinstance(result.data, int) else 0
        logger.info(f"Expired {count} premium trials")
        return count

    # ========================================================================
    # Founder waitlist
    # ========================================================================

    def submit_waitlist_request(self, name: str, email: str,
                                company: str = None,
                                ip_address: str = None) -> dict:
        """Insert a founder waitlist request. Returns the created row."""
        data = {
            'name': name,
            'email': email.strip().lower(),
            'status': 'pending',
        }
        if company:
            data['company'] = company
        if ip_address:
            data['ip_address'] = ip_address

        result = self.client.table('founder_waitlist').insert(data).execute()
        return result.data[0] if result.data else {}

    def list_waitlist(self, status: str = None) -> list:
        """List waitlist entries with optional status filter."""
        query = self.client.table('founder_waitlist').select(
            '*, trial_invites:generated_invite_id(code)'
        ).order('created_at', desc=True)

        if status:
            query = query.eq('status', status)

        result = query.execute()
        return result.data or []

    def approve_waitlist_request(self, waitlist_id: str, admin_user_id: str,
                                 campaign_id: str) -> str:
        """
        Approve a waitlist request: generate a founder invite and link it.

        Returns the raw invite code for the admin to share.
        """
        # Fetch the pending entry
        entry_result = self.client.table('founder_waitlist').select('*').eq(
            'id', waitlist_id
        ).eq('status', 'pending').execute()

        if not entry_result.data:
            raise ValueError('Waitlist entry not found or already processed')

        entry = entry_result.data[0]

        # Generate a founder invite code for this email
        invites = self.generate_invites(
            campaign_id=campaign_id,
            recipient_emails=[entry['email']],
            invite_type='founder',
            created_by_user_id=admin_user_id,
        )

        if not invites:
            raise RuntimeError('Failed to generate invite code')

        invite = invites[0]
        now = datetime.now(timezone.utc).isoformat()

        # Update waitlist entry to approved
        self.client.table('founder_waitlist').update({
            'status': 'approved',
            'approved_by_user_id': admin_user_id,
            'generated_invite_id': invite['id'],
            'updated_at': now,
        }).eq('id', waitlist_id).execute()

        return invite['raw_code']

    def reject_waitlist_request(self, waitlist_id: str, admin_user_id: str,
                                admin_notes: str = None) -> bool:
        """Reject a waitlist request. Returns True if updated."""
        now = datetime.now(timezone.utc).isoformat()
        update_data = {
            'status': 'rejected',
            'approved_by_user_id': admin_user_id,
            'updated_at': now,
        }
        if admin_notes:
            update_data['admin_notes'] = admin_notes

        result = self.client.table('founder_waitlist').update(
            update_data
        ).eq('id', waitlist_id).eq('status', 'pending').execute()

        return bool(result.data)
