"""
Domain URL discovery routes.

Powers the "paste two domains" ingestion path: given a root domain, returns
the site's page URLs via sitemap → CMS API → crawl, capped by plan.
"""
import asyncio
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from backend.extensions import limiter
from backend.services.auth_service import require_auth
from src.redirx.config import Config
from src.redirx.database import UserQuotaDB
from src.redirx.discovery import DiscoveryError, discover_site

discovery_blueprint = Blueprint("discovery", __name__)


@discovery_blueprint.record_once
def _init_limiter(state):
    if "limiter" not in state.app.extensions:
        limiter.init_app(state.app)


@discovery_blueprint.route("/discover", methods=["POST"])
@limiter.limit("30 per hour")
@require_auth
def discover():
    """
    Discover a site's page URLs from a root domain.

    Body: {"url": "example.com"}

    Returns the discovered URL list plus how it was found (sitemap,
    wordpress_api, shopify_api, or crawl), the detected platform, and
    whether the plan cap truncated the result.
    """
    body = request.get_json(silent=True) or {}
    raw_url = str(body.get("url") or "").strip()
    if not raw_url:
        return jsonify({
            "success": False,
            "error": "A 'url' field is required.",
            "code": "missing_url",
        }), 400

    user_id = str(request.user.id)
    plan = UserQuotaDB().get_plan(user_id)
    max_urls = (
        Config.DISCOVERY_MAX_URLS_FREE
        if plan == "free"
        else Config.DISCOVERY_MAX_URLS_PAID
    )

    try:
        result = asyncio.run(discover_site(
            raw_url,
            max_urls=max_urls,
            time_budget=Config.DISCOVERY_TIME_BUDGET_SECONDS,
        ))
    except DiscoveryError as e:
        return jsonify({
            "success": False,
            "error": e.user_message,
            "code": e.code,
            "user_message": e.user_message,
        }), 422
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Discovery failed: {str(e)}",
            "code": "discovery_failed",
        }), 500

    if not result.urls:
        if result.rate_limited:
            minutes = max(1, round(result.retry_after_seconds / 60))
            return jsonify({
                "success": False,
                "code": "rate_limited",
                "error": (
                    f"{urlparse(result.root_url).hostname} is currently rate-limiting "
                    f"automated requests. Try again in about {minutes} "
                    f"minute{'s' if minutes != 1 else ''}, or upload a sitemap or "
                    "CSV export instead."
                ),
                "root_url": result.root_url,
                "retry_after_seconds": result.retry_after_seconds,
                "generator": result.generator,
            }), 429

        return jsonify({
            "success": False,
            "code": "no_urls_found",
            "error": (
                f"Could not find any pages on {result.root_url}. "
                "The site may block automated requests — you can upload a "
                "sitemap or CSV instead."
            ),
            "root_url": result.root_url,
            "generator": result.generator,
            "errors": result.errors[:5],
        }), 422

    return jsonify({
        "success": True,
        "root_url": result.root_url,
        "urls": result.urls,
        "count": len(result.urls),
        "total_found": result.total_found,
        "truncated": result.truncated,
        "max_urls": max_urls,
        "method": result.method,
        "generator": result.generator,
        "duration_ms": result.duration_ms,
        "plan": plan,
    }), 200
