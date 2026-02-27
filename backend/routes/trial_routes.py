"""
Trial invite routes for code generation, validation, redemption, and admin management.
"""
from flask import Blueprint, request, jsonify
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend.services.auth_service import require_auth, require_admin
from backend.services.trial_service import TrialService
from backend.services.stripe_service import StripeService
from backend.extensions import limiter
from backend.routes.error_utils import error_response
from redirx.database import SupabaseClient

logger = logging.getLogger(__name__)

trial_blueprint = Blueprint('trials', __name__)


@trial_blueprint.record_once
def _init_limiter(state):
    if "limiter" not in state.app.extensions:
        limiter.init_app(state.app)


def _get_trial_service() -> TrialService:
    """Lazily create TrialService."""
    return TrialService()


def _get_client_ip() -> str:
    """Get the real client IP, respecting X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _parse_utc_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp into a timezone-aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _map_trial_validation_error(error_message: str) -> tuple[str, str]:
    normalized = (error_message or "").lower()
    if "different email address" in normalized:
        return "trial_code_email_mismatch", "This invite code was issued to a different email address."
    if (
        "invalid code format" in normalized
        or "invalid or expired" in normalized
        or "has expired" in normalized
        or "already been used" in normalized
    ):
        return "trial_code_invalid_or_expired", "This invite code is invalid or expired."
    return "trial_code_invalid_or_expired", "This invite code is invalid or expired."


# ============================================================================
# Admin: Campaign management
# ============================================================================

@trial_blueprint.route('/admin/trials/campaigns', methods=['POST'])
@require_auth
@require_admin
def create_campaign():
    """Create a new trial campaign."""
    data = request.get_json()
    if not data or not data.get('name') or not data.get('slug'):
        return error_response(
            code="trial_campaign_name_slug_required",
            user_message="Campaign name and slug are required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        campaign = service.create_campaign(
            name=data['name'],
            slug=data['slug'],
            channel=data.get('channel'),
            template_version=data.get('template_version'),
            owner_user_id=request.user.id,
            invite_type=data.get('invite_type', 'trial'),
        )
        return jsonify({'campaign': campaign}), 201
    except Exception as e:
        logger.error(f"Campaign creation failed: {e}")
        if 'duplicate key' in str(e).lower() or 'unique' in str(e).lower():
            return error_response(
                code="trial_campaign_slug_conflict",
                user_message="A campaign with this slug already exists.",
                status=409,
                retryable=False,
                next_action="edit_slug",
            )
        return error_response(
            code="trial_campaign_create_failed",
            user_message="Unable to create campaign right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/admin/trials/campaigns', methods=['GET'])
@require_auth
@require_admin
def list_campaigns():
    """List all campaigns with stats."""
    try:
        service = _get_trial_service()
        campaigns = service.list_campaigns()

        # Attach stats to each campaign
        for campaign in campaigns:
            campaign['stats'] = service.get_campaign_stats(campaign['id'])

        return jsonify({'campaigns': campaigns})
    except Exception as e:
        logger.error(f"Campaign listing failed: {e}")
        return error_response(
            code="trial_campaign_list_failed",
            user_message="Unable to load campaigns right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/admin/onboarding/report', methods=['GET'])
@require_auth
@require_admin
def get_onboarding_report():
    """
    Return onboarding funnel + stuck-user report for operator workflows.

    Query params:
      - stuck_hours: inactivity threshold for "stuck" in-progress users (default: 24)
      - limit: max stuck rows returned (default: 100)
    """
    stuck_hours_raw = request.args.get('stuck_hours', '24')
    limit_raw = request.args.get('limit', '100')

    try:
        stuck_hours = int(stuck_hours_raw)
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return error_response(
            code="trial_onboarding_report_invalid_params",
            user_message="stuck_hours and limit must be integers.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    if stuck_hours < 1 or stuck_hours > 24 * 30:
        return error_response(
            code="trial_onboarding_report_stuck_hours_range",
            user_message="stuck_hours must be between 1 and 720.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    if limit < 1 or limit > 500:
        return error_response(
            code="trial_onboarding_report_limit_range",
            user_message="limit must be between 1 and 500.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=stuck_hours)

    try:
        client = SupabaseClient.get_admin_client()
        profiles_result = client.table('user_profiles').select(
            'id, email, full_name, company, created_at, onboarding_status, onboarding_state, '
            'onboarding_started_at, onboarding_completed_at, onboarding_last_seen_at'
        ).execute()
        profiles = profiles_result.data or []

        status_counts = {
            'not_started': 0,
            'in_progress': 0,
            'completed': 0,
            'dismissed': 0,
            'unknown': 0,
        }
        funnel_counts = {
            'started': 0,
            'path_selected': 0,
            'mapping_generated': 0,
            'review_opened': 0,
            'export_downloaded': 0,
        }
        stuck_rows = []

        for profile in profiles:
            status = profile.get('onboarding_status') or 'not_started'
            if status not in status_counts:
                status_counts['unknown'] += 1
            else:
                status_counts[status] += 1

            onboarding_state = profile.get('onboarding_state')
            if not isinstance(onboarding_state, dict):
                onboarding_state = {}

            if profile.get('onboarding_started_at'):
                funnel_counts['started'] += 1
            if onboarding_state.get('path_selected_at'):
                funnel_counts['path_selected'] += 1
            if onboarding_state.get('mapping_generated_at'):
                funnel_counts['mapping_generated'] += 1
            if onboarding_state.get('review_opened_at'):
                funnel_counts['review_opened'] += 1
            if onboarding_state.get('export_downloaded_at'):
                funnel_counts['export_downloaded'] += 1

            if status != 'in_progress':
                continue

            last_seen_dt = _parse_utc_timestamp(profile.get('onboarding_last_seen_at'))
            started_dt = _parse_utc_timestamp(profile.get('onboarding_started_at'))
            created_dt = _parse_utc_timestamp(profile.get('created_at'))
            last_activity_dt = last_seen_dt or started_dt or created_dt

            if last_activity_dt and last_activity_dt > cutoff:
                continue

            path = onboarding_state.get('path')
            steps = onboarding_state.get('steps')
            if not isinstance(steps, dict):
                steps = {}

            completed_steps = []
            for step_name, step_state in steps.items():
                if isinstance(step_state, dict) and step_state.get('completed'):
                    completed_steps.append(step_name)

            hours_since_last_activity = None
            if last_activity_dt:
                hours_since_last_activity = round((now - last_activity_dt).total_seconds() / 3600, 1)

            hours_in_progress = None
            if started_dt:
                hours_in_progress = round((now - started_dt).total_seconds() / 3600, 1)

            stuck_rows.append({
                'id': profile.get('id'),
                'email': profile.get('email'),
                'full_name': profile.get('full_name'),
                'company': profile.get('company'),
                'onboarding_status': status,
                'path': path if path in ('sample', 'real') else None,
                'completed_steps': completed_steps,
                'onboarding_started_at': profile.get('onboarding_started_at'),
                'onboarding_last_seen_at': profile.get('onboarding_last_seen_at'),
                'onboarding_completed_at': profile.get('onboarding_completed_at'),
                'hours_since_last_activity': hours_since_last_activity,
                'hours_in_progress': hours_in_progress,
            })

        # Add non-tutorial session counts to prioritize users who never reached real output.
        stuck_user_ids = [row['id'] for row in stuck_rows if row.get('id')]
        session_counts: Dict[str, int] = {}
        if stuck_user_ids:
            sessions_result = client.table('migration_sessions').select(
                'user_id'
            ).in_(
                'user_id', stuck_user_ids
            ).eq(
                'is_tutorial', False
            ).eq(
                'is_preview', False
            ).execute()

            for row in (sessions_result.data or []):
                user_id = row.get('user_id')
                if not user_id:
                    continue
                session_counts[user_id] = session_counts.get(user_id, 0) + 1

        for row in stuck_rows:
            user_id = row.get('id')
            row['non_tutorial_sessions'] = session_counts.get(user_id, 0)

        stuck_rows.sort(
            key=lambda row: (
                row['hours_since_last_activity'] is None,
                -(row['hours_since_last_activity'] or 0.0),
            )
        )

        total_stuck = len(stuck_rows)
        report_rows = stuck_rows[:limit]

        return jsonify({
            'success': True,
            'generated_at': now.isoformat(),
            'filters': {
                'stuck_hours': stuck_hours,
                'limit': limit,
                'stuck_before_utc': cutoff.isoformat(),
            },
            'summary': {
                'total_users': len(profiles),
                'status_counts': status_counts,
                'funnel_counts': funnel_counts,
                'stuck_in_progress': total_stuck,
            },
            'total_stuck_users': total_stuck,
            'returned_rows': len(report_rows),
            'stuck_users': report_rows,
        }), 200

    except Exception as e:
        logger.error(f"Onboarding report failed: {e}")
        return error_response(
            code="trial_onboarding_report_failed",
            user_message="Unable to generate onboarding report right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


# ============================================================================
# Admin: Invite management
# ============================================================================

@trial_blueprint.route('/admin/trials/invites', methods=['POST'])
@require_auth
@require_admin
def generate_invites():
    """
    Bulk generate invite codes.
    Accepts JSON body or multipart form with CSV file.
    """
    try:
        service = _get_trial_service()

        # Check if multipart (CSV upload)
        if request.content_type and 'multipart' in request.content_type:
            campaign_id = request.form.get('campaign_id')
            if not campaign_id:
                return error_response(
                    code="trial_campaign_required",
                    user_message="Campaign is required.",
                    status=400,
                    retryable=False,
                    next_action="fill_form",
                )

            csv_file = request.files.get('csv')
            if not csv_file:
                return error_response(
                    code="trial_csv_required",
                    user_message="CSV file is required for CSV mode.",
                    status=400,
                    retryable=False,
                    next_action="fill_form",
                )

            csv_content = csv_file.read().decode('utf-8')
            emails = service.parse_recipient_csv(csv_content)
            if not emails:
                return error_response(
                    code="trial_csv_no_valid_emails",
                    user_message="No valid email addresses were found in the CSV.",
                    status=400,
                    retryable=False,
                    next_action="fix_csv",
                )

            invites = service.generate_invites(
                campaign_id=campaign_id,
                recipient_emails=emails,
                expires_days=int(request.form.get('expires_days', 90)),
                credits_granted=int(request.form.get('credits_granted', 50000)),
                trial_days=int(request.form.get('trial_days', 14)),
                created_by_user_id=request.user.id,
                invite_type=request.form.get('invite_type', 'trial'),
            )
        else:
            data = request.get_json()
            if not data or not data.get('campaign_id'):
                return error_response(
                    code="trial_campaign_required",
                    user_message="Campaign is required.",
                    status=400,
                    retryable=False,
                    next_action="fill_form",
                )

            invites = service.generate_invites(
                campaign_id=data['campaign_id'],
                count=data.get('count', 1),
                recipient_emails=data.get('recipient_emails'),
                expires_days=data.get('expires_days', 90),
                credits_granted=data.get('credits_granted', 50000),
                trial_days=data.get('trial_days', 14),
                created_by_user_id=request.user.id,
                invite_type=data.get('invite_type', 'trial'),
            )

        return jsonify({'invites': invites}), 201
    except Exception as e:
        logger.error(f"Invite generation failed: {e}")
        return error_response(
            code="trial_invite_generate_failed",
            user_message="Unable to generate invites right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/admin/trials/invites', methods=['GET'])
@require_auth
@require_admin
def list_invites():
    """List invites with optional campaign_id and status filters."""
    try:
        service = _get_trial_service()
        invites = service.list_invites(
            campaign_id=request.args.get('campaign_id'),
            status=request.args.get('status'),
        )
        return jsonify({'invites': invites})
    except Exception as e:
        logger.error(f"Invite listing failed: {e}")
        return error_response(
            code="trial_invite_list_failed",
            user_message="Unable to load invites right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/admin/trials/mark-sent', methods=['POST'])
@require_auth
@require_admin
def mark_sent():
    """Mark invites as sent by IDs."""
    data = request.get_json()
    if not data or not data.get('invite_ids'):
        return error_response(
            code="trial_invite_ids_required",
            user_message="invite_ids is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    invite_ids = data['invite_ids']
    if not isinstance(invite_ids, list) or len(invite_ids) == 0:
        return error_response(
            code="trial_invite_ids_invalid",
            user_message="invite_ids must be a non-empty array.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        count = service.mark_sent(invite_ids, actor_id=request.user.id)
        return jsonify({'success': True, 'updated': count})
    except Exception as e:
        logger.error(f"Mark sent failed: {e}")
        return error_response(
            code="trial_invite_mark_sent_failed",
            user_message="Unable to mark invites as sent right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/admin/trials/revoke', methods=['POST'])
@require_auth
@require_admin
def revoke_invite():
    """Revoke an invite by ID."""
    data = request.get_json()
    if not data or not data.get('invite_id'):
        return error_response(
            code="trial_invite_id_required",
            user_message="invite_id is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        revoked = service.revoke_invite(data['invite_id'], request.user.id)
        if revoked:
            return jsonify({'success': True})
        return error_response(
            code="trial_invite_not_found",
            user_message="Invite not found or already redeemed/revoked.",
            status=404,
            retryable=False,
            next_action="refresh",
        )
    except Exception as e:
        logger.error(f"Invite revocation failed: {e}")
        return error_response(
            code="trial_invite_revoke_failed",
            user_message="Unable to revoke invite right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/admin/trials/expire', methods=['POST'])
def run_expiry():
    """
    Run nightly trial expiry. Authenticated via X-Cron-Secret header
    for Render Cron Job compatibility.
    """
    from redirx.config import Config
    cron_secret = Config.CRON_SECRET
    provided = request.headers.get('X-Cron-Secret', '')

    if not cron_secret or provided != cron_secret:
        return error_response(
            code="trial_admin_unauthorized",
            user_message="Unauthorized.",
            status=401,
            retryable=False,
            next_action="authenticate",
        )

    try:
        service = _get_trial_service()
        count = service.expire_trials()
        return jsonify({'expired_count': count})
    except Exception as e:
        logger.error(f"Trial expiry failed: {e}")
        return error_response(
            code="trial_expiry_failed",
            user_message="Unable to expire trials right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


# ============================================================================
# Public/User: Code validation & redemption
# ============================================================================

@trial_blueprint.route('/trials/validate', methods=['POST'])
def validate_code():
    """
    Validate an invite code (no auth required, for UI preview).
    Returns campaign info and trial details if valid.
    """
    data = request.get_json()
    if not data or not data.get('code'):
        return error_response(
            code="trial_code_required",
            user_message="Invite code is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        is_valid, error_msg, invite = service.validate_code(data['code'])

        if not is_valid:
            code, user_message = _map_trial_validation_error(error_msg)
            next_action = 'request_new_code'
            if code == 'trial_code_email_mismatch':
                next_action = 'use_matching_account'
            return error_response(
                code=code,
                user_message=user_message,
                status=400,
                retryable=False,
                next_action=next_action,
                extra={'valid': False},
            )

        campaign = invite.get('trial_campaigns', {})
        invite_type = invite.get('invite_type') or campaign.get('invite_type') or 'trial'
        return jsonify({
            'valid': True,
            'invite_type': invite_type,
            'campaign_name': campaign.get('name', ''),
            'campaign_slug': campaign.get('slug', ''),
            'trial_days': invite.get('trial_days', 14),
            'credits_granted': invite.get('credits_granted', 50000),
            'requires_email': bool(invite.get('recipient_email')),
        })
    except Exception as e:
        logger.error(f"Code validation failed: {e}")
        return error_response(
            code="trial_validation_failed",
            user_message="Unable to validate this invite code right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/founder/checkout', methods=['POST'])
@require_auth
def founder_checkout():
    """
    Create a Stripe Checkout session for Founder package purchase via invite code.
    Requires a valid founder invite code.
    """
    data = request.get_json()
    if not data or not data.get('code'):
        return error_response(
            code="trial_code_required",
            user_message="Invite code is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        is_valid, error_msg, invite = service.validate_code(data['code'])

        if not is_valid:
            code, user_message = _map_trial_validation_error(error_msg)
            next_action = "request_new_code"
            if code == "trial_code_email_mismatch":
                next_action = "use_matching_account"
            return error_response(
                code=code,
                user_message=user_message,
                status=400,
                retryable=False,
                next_action=next_action,
            )

        # Verify this is a founder invite
        campaign = invite.get('trial_campaigns', {})
        invite_type = invite.get('invite_type') or campaign.get('invite_type') or 'trial'
        if invite_type != 'founder':
            return error_response(
                code="trial_founder_invite_required",
                user_message="This invite code is not for the Founder package.",
                status=400,
                retryable=False,
                next_action="request_new_code",
            )

        # Check recipient email restriction
        if invite.get('recipient_email'):
            if invite['recipient_email'].lower() != request.user.email.lower():
                return error_response(
                    code="trial_code_email_mismatch",
                    user_message="This invite code was issued to a different email address.",
                    status=400,
                    retryable=False,
                    next_action="use_matching_account",
                )

        # Check user is not already on founder plan
        from redirx.database import SupabaseClient
        client = SupabaseClient.get_client()
        profile_result = client.table('user_profiles').select(
            'plan, is_lifetime'
        ).eq('id', request.user.id).single().execute()

        if profile_result.data:
            current_plan = profile_result.data.get('plan', 'launch')
            if current_plan == 'founder':
                return error_response(
                    code="trial_already_founder",
                    user_message="You are already on the Founder plan.",
                    status=400,
                    retryable=False,
                    next_action="open_dashboard",
                )

        # Create Stripe Checkout session
        from redirx.config import Config
        if not Config.STRIPE_PRICE_ID_FOUNDER:
            return error_response(
                code="trial_founder_pricing_unavailable",
                user_message="Founder pricing is currently unavailable.",
                status=500,
                retryable=True,
                next_action="contact_support",
            )

        origin = request.headers.get('Origin', 'http://localhost:3000')
        stripe_service = StripeService()
        url, session_id = stripe_service.create_checkout_session(
            user_id=request.user.id,
            email=request.user.email,
            price_id=Config.STRIPE_PRICE_ID_FOUNDER,
            success_url=f'{origin}/founder/success',
            cancel_url=f'{origin}/founder?code={data["code"]}',
            extra_metadata={'invite_id': invite['id']},
        )

        # Mark invite as pending_payment
        service.initiate_founder_checkout(invite['id'], session_id, request.user.id)

        return jsonify({'url': url})
    except Exception as e:
        logger.error(f"Founder checkout failed: {e}")
        return error_response(
            code="trial_founder_checkout_failed",
            user_message="Unable to create checkout session right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/trials/redeem', methods=['POST'])
@limiter.limit("5 per 10 minutes")
@require_auth
def redeem_code():
    """Redeem an invite code for the authenticated user."""
    ip = _get_client_ip()

    data = request.get_json()
    if not data or not data.get('code'):
        return error_response(
            code="trial_code_required",
            user_message="Invite code is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        success, error_msg, result = service.redeem_invite(
            raw_code=data['code'],
            user_id=request.user.id,
            user_email=request.user.email,
            ip_address=ip,
        )

        if not success:
            code, user_message = _map_trial_validation_error(error_msg)
            next_action = "request_new_code"
            if code == "trial_code_email_mismatch":
                next_action = "use_matching_account"
            return error_response(
                code=code,
                user_message=user_message,
                status=400,
                retryable=False,
                next_action=next_action,
            )

        return jsonify({
            'success': True,
            'trial_days': result['trial_days'],
            'credits_granted': result['credits_granted'],
            'trial_expires_at': result['trial_expires_at'],
            'campaign_name': result['campaign_name'],
        })
    except Exception as e:
        logger.error(f"Code redemption failed: {e}")
        return error_response(
            code="trial_redeem_failed",
            user_message="We couldn't redeem this code right now. Please try again.",
            status=500,
            retryable=True,
            next_action="retry",
        )


# ============================================================================
# Public: Founder waitlist
# ============================================================================

@trial_blueprint.route('/founder/waitlist', methods=['POST'])
@limiter.limit("3 per 10 minutes")
def submit_waitlist():
    """Submit a founder waitlist request (no auth, rate-limited)."""
    ip = _get_client_ip()

    data = request.get_json()
    if not data:
        return error_response(
            code="trial_waitlist_body_required",
            user_message="Request body is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    company = (data.get('company') or '').strip() or None

    if not name:
        return error_response(
            code="trial_waitlist_name_required",
            user_message="Name is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )
    if not email or '@' not in email or '.' not in email:
        return error_response(
            code="trial_waitlist_email_invalid",
            user_message="A valid email address is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        entry = service.submit_waitlist_request(
            name=name,
            email=email,
            company=company,
            ip_address=ip,
        )
        return jsonify({'success': True, 'entry': entry}), 201
    except Exception as e:
        error_str = str(e).lower()
        if 'duplicate' in error_str or 'unique' in error_str:
            return error_response(
                code="trial_waitlist_duplicate",
                user_message="A request with this email is already pending.",
                status=409,
                retryable=False,
                next_action="check_email",
            )
        logger.error(f"Waitlist submission failed: {e}")
        return error_response(
            code="trial_waitlist_submit_failed",
            user_message="Unable to submit your request right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


# ============================================================================
# Admin: Founder waitlist management
# ============================================================================

@trial_blueprint.route('/admin/trials/waitlist', methods=['GET'])
@require_auth
@require_admin
def list_waitlist():
    """List waitlist entries with optional status filter."""
    try:
        service = _get_trial_service()
        entries = service.list_waitlist(status=request.args.get('status'))
        return jsonify({'entries': entries})
    except Exception as e:
        logger.error(f"Waitlist listing failed: {e}")
        return error_response(
            code="trial_waitlist_list_failed",
            user_message="Unable to load waitlist entries right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/admin/trials/waitlist/approve', methods=['POST'])
@require_auth
@require_admin
def approve_waitlist():
    """Approve a waitlist entry and generate a founder invite code."""
    data = request.get_json()
    if not data or not data.get('waitlist_id') or not data.get('campaign_id'):
        return error_response(
            code="trial_waitlist_approve_fields_required",
            user_message="waitlist_id and campaign_id are required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        raw_code = service.approve_waitlist_request(
            waitlist_id=data['waitlist_id'],
            admin_user_id=request.user.id,
            campaign_id=data['campaign_id'],
        )
        return jsonify({'success': True, 'raw_code': raw_code})
    except ValueError as e:
        return error_response(
            code="trial_waitlist_not_found",
            user_message="Waitlist entry not found.",
            status=404,
            retryable=False,
            next_action="refresh",
        )
    except Exception as e:
        logger.error(f"Waitlist approval failed: {e}")
        return error_response(
            code="trial_waitlist_approve_failed",
            user_message="Unable to approve request right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )


@trial_blueprint.route('/admin/trials/waitlist/reject', methods=['POST'])
@require_auth
@require_admin
def reject_waitlist():
    """Reject a waitlist entry with optional admin notes."""
    data = request.get_json()
    if not data or not data.get('waitlist_id'):
        return error_response(
            code="trial_waitlist_id_required",
            user_message="waitlist_id is required.",
            status=400,
            retryable=False,
            next_action="fill_form",
        )

    try:
        service = _get_trial_service()
        rejected = service.reject_waitlist_request(
            waitlist_id=data['waitlist_id'],
            admin_user_id=request.user.id,
            admin_notes=data.get('admin_notes'),
        )
        if rejected:
            return jsonify({'success': True})
        return error_response(
            code="trial_waitlist_not_found",
            user_message="Entry not found or already processed.",
            status=404,
            retryable=False,
            next_action="refresh",
        )
    except Exception as e:
        logger.error(f"Waitlist rejection failed: {e}")
        return error_response(
            code="trial_waitlist_reject_failed",
            user_message="Unable to reject request right now.",
            status=500,
            retryable=True,
            next_action="retry",
        )
