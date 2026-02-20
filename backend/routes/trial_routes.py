"""
Trial invite routes for code generation, validation, redemption, and admin management.
"""
from flask import Blueprint, request, jsonify
import logging

from backend.services.auth_service import require_auth, require_admin
from backend.services.trial_service import TrialService
from backend.services.stripe_service import StripeService
from backend.services.demo_rate_limiter import DemoRateLimiter

logger = logging.getLogger(__name__)

trial_blueprint = Blueprint('trials', __name__)

# Rate limiter: 5 redeem attempts per 10 minutes per IP
_redeem_limiter = DemoRateLimiter(max_requests=5, window_seconds=600)

# Rate limiter: 3 waitlist submissions per 10 minutes per IP
_waitlist_limiter = DemoRateLimiter(max_requests=3, window_seconds=600)


def _get_trial_service() -> TrialService:
    """Lazily create TrialService."""
    return TrialService()


def _get_client_ip() -> str:
    """Get the real client IP, respecting X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


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
        return jsonify({'error': 'name and slug are required'}), 400

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
            return jsonify({'error': 'A campaign with this slug already exists'}), 409
        return jsonify({'error': 'Failed to create campaign'}), 500


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
        return jsonify({'error': 'Failed to list campaigns'}), 500


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
                return jsonify({'error': 'campaign_id is required'}), 400

            csv_file = request.files.get('csv')
            if not csv_file:
                return jsonify({'error': 'CSV file is required for CSV mode'}), 400

            csv_content = csv_file.read().decode('utf-8')
            emails = service.parse_recipient_csv(csv_content)
            if not emails:
                return jsonify({'error': 'No valid email addresses found in CSV'}), 400

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
                return jsonify({'error': 'campaign_id is required'}), 400

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
        return jsonify({'error': 'Failed to generate invites'}), 500


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
        return jsonify({'error': 'Failed to list invites'}), 500


@trial_blueprint.route('/admin/trials/mark-sent', methods=['POST'])
@require_auth
@require_admin
def mark_sent():
    """Mark invites as sent by IDs."""
    data = request.get_json()
    if not data or not data.get('invite_ids'):
        return jsonify({'error': 'invite_ids is required'}), 400

    invite_ids = data['invite_ids']
    if not isinstance(invite_ids, list) or len(invite_ids) == 0:
        return jsonify({'error': 'invite_ids must be a non-empty array'}), 400

    try:
        service = _get_trial_service()
        count = service.mark_sent(invite_ids, actor_id=request.user.id)
        return jsonify({'success': True, 'updated': count})
    except Exception as e:
        logger.error(f"Mark sent failed: {e}")
        return jsonify({'error': 'Failed to mark invites as sent'}), 500


@trial_blueprint.route('/admin/trials/revoke', methods=['POST'])
@require_auth
@require_admin
def revoke_invite():
    """Revoke an invite by ID."""
    data = request.get_json()
    if not data or not data.get('invite_id'):
        return jsonify({'error': 'invite_id is required'}), 400

    try:
        service = _get_trial_service()
        revoked = service.revoke_invite(data['invite_id'], request.user.id)
        if revoked:
            return jsonify({'success': True})
        return jsonify({'error': 'Invite not found or already redeemed/revoked'}), 404
    except Exception as e:
        logger.error(f"Invite revocation failed: {e}")
        return jsonify({'error': 'Failed to revoke invite'}), 500


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
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        service = _get_trial_service()
        count = service.expire_trials()
        return jsonify({'expired_count': count})
    except Exception as e:
        logger.error(f"Trial expiry failed: {e}")
        return jsonify({'error': 'Failed to expire trials'}), 500


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
        return jsonify({'error': 'code is required'}), 400

    try:
        service = _get_trial_service()
        is_valid, error_msg, invite = service.validate_code(data['code'])

        if not is_valid:
            return jsonify({'valid': False, 'error': error_msg})

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
        return jsonify({'error': 'Validation failed'}), 500


@trial_blueprint.route('/founder/checkout', methods=['POST'])
@require_auth
def founder_checkout():
    """
    Create a Stripe Checkout session for Founder package purchase via invite code.
    Requires a valid founder invite code.
    """
    data = request.get_json()
    if not data or not data.get('code'):
        return jsonify({'error': 'code is required'}), 400

    try:
        service = _get_trial_service()
        is_valid, error_msg, invite = service.validate_code(data['code'])

        if not is_valid:
            return jsonify({'error': error_msg}), 400

        # Verify this is a founder invite
        campaign = invite.get('trial_campaigns', {})
        invite_type = invite.get('invite_type') or campaign.get('invite_type') or 'trial'
        if invite_type != 'founder':
            return jsonify({'error': 'This invite code is not for the Founder package'}), 400

        # Check recipient email restriction
        if invite.get('recipient_email'):
            if invite['recipient_email'].lower() != request.user.email.lower():
                return jsonify({'error': 'This invite code was issued to a different email address'}), 400

        # Check user is not already on founder plan
        from redirx.database import SupabaseClient
        client = SupabaseClient.get_client()
        profile_result = client.table('user_profiles').select(
            'plan, is_lifetime'
        ).eq('id', request.user.id).single().execute()

        if profile_result.data:
            current_plan = profile_result.data.get('plan', 'launch')
            if current_plan == 'founder':
                return jsonify({'error': 'You are already on the Founder plan'}), 400

        # Create Stripe Checkout session
        from redirx.config import Config
        if not Config.STRIPE_PRICE_ID_FOUNDER:
            return jsonify({'error': 'Founder pricing not configured'}), 500

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
        return jsonify({'error': 'Failed to create checkout session'}), 500


@trial_blueprint.route('/trials/redeem', methods=['POST'])
@require_auth
def redeem_code():
    """Redeem an invite code for the authenticated user."""
    # Rate limit
    ip = _get_client_ip()
    allowed, retry_after = _redeem_limiter.check(ip)
    if not allowed:
        return jsonify({
            'error': 'Too many redemption attempts. Please try again later.',
            'retry_after_seconds': retry_after,
        }), 429

    data = request.get_json()
    if not data or not data.get('code'):
        return jsonify({'error': 'code is required'}), 400

    try:
        service = _get_trial_service()
        success, error_msg, result = service.redeem_invite(
            raw_code=data['code'],
            user_id=request.user.id,
            user_email=request.user.email,
            ip_address=ip,
        )

        if not success:
            return jsonify({'success': False, 'error': error_msg}), 400

        return jsonify({
            'success': True,
            'trial_days': result['trial_days'],
            'credits_granted': result['credits_granted'],
            'trial_expires_at': result['trial_expires_at'],
            'campaign_name': result['campaign_name'],
        })
    except Exception as e:
        logger.error(f"Code redemption failed: {e}")
        return jsonify({'error': 'Redemption failed'}), 500


# ============================================================================
# Public: Founder waitlist
# ============================================================================

@trial_blueprint.route('/founder/waitlist', methods=['POST'])
def submit_waitlist():
    """Submit a founder waitlist request (no auth, rate-limited)."""
    ip = _get_client_ip()
    allowed, retry_after = _waitlist_limiter.check(ip)
    if not allowed:
        return jsonify({
            'error': 'Too many requests. Please try again later.',
            'retry_after_seconds': retry_after,
        }), 429

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is required'}), 400

    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    company = (data.get('company') or '').strip() or None

    if not name:
        return jsonify({'error': 'Name is required'}), 400
    if not email or '@' not in email or '.' not in email:
        return jsonify({'error': 'A valid email address is required'}), 400

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
            return jsonify({'error': 'A request with this email is already pending'}), 409
        logger.error(f"Waitlist submission failed: {e}")
        return jsonify({'error': 'Failed to submit request'}), 500


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
        return jsonify({'error': 'Failed to list waitlist'}), 500


@trial_blueprint.route('/admin/trials/waitlist/approve', methods=['POST'])
@require_auth
@require_admin
def approve_waitlist():
    """Approve a waitlist entry and generate a founder invite code."""
    data = request.get_json()
    if not data or not data.get('waitlist_id') or not data.get('campaign_id'):
        return jsonify({'error': 'waitlist_id and campaign_id are required'}), 400

    try:
        service = _get_trial_service()
        raw_code = service.approve_waitlist_request(
            waitlist_id=data['waitlist_id'],
            admin_user_id=request.user.id,
            campaign_id=data['campaign_id'],
        )
        return jsonify({'success': True, 'raw_code': raw_code})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Waitlist approval failed: {e}")
        return jsonify({'error': 'Failed to approve request'}), 500


@trial_blueprint.route('/admin/trials/waitlist/reject', methods=['POST'])
@require_auth
@require_admin
def reject_waitlist():
    """Reject a waitlist entry with optional admin notes."""
    data = request.get_json()
    if not data or not data.get('waitlist_id'):
        return jsonify({'error': 'waitlist_id is required'}), 400

    try:
        service = _get_trial_service()
        rejected = service.reject_waitlist_request(
            waitlist_id=data['waitlist_id'],
            admin_user_id=request.user.id,
            admin_notes=data.get('admin_notes'),
        )
        if rejected:
            return jsonify({'success': True})
        return jsonify({'error': 'Entry not found or already processed'}), 404
    except Exception as e:
        logger.error(f"Waitlist rejection failed: {e}")
        return jsonify({'error': 'Failed to reject request'}), 500
