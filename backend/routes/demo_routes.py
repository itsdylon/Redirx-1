import asyncio
import json
from urllib.parse import urlparse

from flask import Blueprint, Response, request, stream_with_context

from backend.services.demo_rate_limiter import rate_limiter
from backend.services.site_auditor import SiteAuditor

demo_blueprint = Blueprint("demo", __name__)


def _validate_url(url: str) -> str | None:
    """Return an error message if the URL is invalid, else None."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL format"

    if parsed.scheme not in ("http", "https"):
        return "URL must use http or https"
    if not parsed.netloc:
        return "URL must include a domain"
    return None


def _get_client_ip() -> str:
    """Get the real client IP, respecting X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _sse_line(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@demo_blueprint.route("/audit", methods=["POST"])
def audit():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()

    if not url:
        return {"error": "URL is required"}, 400

    # Validate URL format
    err = _validate_url(url)
    if err:
        return {"error": err}, 400

    # Rate limit
    ip = _get_client_ip()
    allowed, retry_after = rate_limiter.check(ip)
    if not allowed:
        return {"error": "Rate limit exceeded", "retry_after_seconds": retry_after}, 429

    def generate():
        """Bridge async generator to sync Flask generator."""
        loop = asyncio.new_event_loop()
        auditor = SiteAuditor()
        gen = auditor.run_audit(url)

        try:
            while True:
                try:
                    event = loop.run_until_complete(gen.__anext__())
                    yield _sse_line(event["event"], event["data"])
                except StopAsyncIteration:
                    break
        except GeneratorExit:
            # Client disconnected
            loop.run_until_complete(gen.aclose())
        finally:
            loop.close()

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
