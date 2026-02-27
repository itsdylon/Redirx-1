import asyncio
import json
from urllib.parse import urlparse

from flask import Blueprint, Response, request, stream_with_context

from backend.extensions import limiter
from backend.services.site_auditor import SiteAuditor

demo_blueprint = Blueprint("demo", __name__)


@demo_blueprint.record_once
def _init_limiter(state):
    if "limiter" not in state.app.extensions:
        limiter.init_app(state.app)


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


def _sse_line(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@demo_blueprint.route("/audit", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def audit():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()

    if not url:
        return {"error": "URL is required"}, 400

    # Validate URL format
    err = _validate_url(url)
    if err:
        return {"error": err}, 400

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
