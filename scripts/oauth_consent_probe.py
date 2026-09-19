#!/usr/bin/env python3
"""One-shot Supabase OAuth diagnostic. No dependencies, token files, or HTTP logs.

This verifies provider identity plus the gateway's resource constraints; it does
not prove an authenticated MCP tool call. See docs/oauth-consent-probe.md.
"""

import argparse
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


CALLBACK = "http://127.0.0.1:8765/callback"
MAX_RESPONSE = 65536
SAFE_ERROR_CODES = frozenset({
    "invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
    "unsupported_grant_type", "invalid_scope", "invalid_target", "access_denied",
    "server_error", "temporarily_unavailable", "bad_jwt", "no_authorization",
    "session_not_found", "user_not_found", "insufficient_scope", "unexpected_audience",
})


class ProbeError(Exception):
    """Only fixed, credential-free messages may reach the console."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError("Upstream redirect refused")


def request_json(url, *, headers=None, form=None):
    request = Request(url, headers=headers or {},
                      data=None if form is None else urlencode(form).encode())
    if form is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        # Do not route bearer credentials through ambient HTTP proxy settings.
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=10) as response:
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise ProbeError("Upstream response too large")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ProbeError("Invalid upstream response")
            return result
    except HTTPError as error:
        # Never emit arbitrary provider messages, URLs, headers, or descriptions.
        code = None
        try:
            body = error.read(MAX_RESPONSE + 1)
            if len(body) <= MAX_RESPONSE:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    candidate = payload.get("error") or payload.get("error_code") or payload.get("code")
                    if isinstance(candidate, str) and candidate in SAFE_ERROR_CODES:
                        code = candidate
        except Exception:
            pass
        finally:
            error.close()
        status = error.code if type(error.code) is int and 100 <= error.code <= 599 else "unknown"
        suffix = "; " + code if code else ""
        raise ProbeError(f"Upstream request failed (HTTP {status}{suffix}; details suppressed)") from None
    except URLError:
        raise ProbeError("Upstream request failed (connection or TLS error; details suppressed)") from None
    except TimeoutError:
        raise ProbeError("Upstream request failed (timeout; details suppressed)") from None
    except Exception:
        # HTTPError/JSON errors can contain URLs, codes, or response bodies.
        raise ProbeError("Upstream request failed (details suppressed)") from None


def https_url(value):
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or
            parts.password or parts.query or parts.fragment or
            any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("Expected HTTPS URL without credentials, query, or fragment")
    # Accessing port validates malformed/out-of-range values.
    parts.port
    return value


def new_authorization(issuer, resource, client_id):
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    query = urlencode(dict(response_type="code", client_id=client_id,
                           redirect_uri=CALLBACK, scope="openid email profile",
                           resource=resource, state=state, code_challenge=challenge,
                           code_challenge_method="S256"))
    return issuer.rstrip("/") + "/oauth/authorize?" + query, state, verifier


class CallbackServer(HTTPServer):
    allow_reuse_address = False

    def __init__(self, state, address=("127.0.0.1", 8765)):
        if address[0] != "127.0.0.1":
            raise ValueError("Loopback only")
        self.state = state
        self.result = None
        super().__init__(address, CallbackHandler)
        self.timeout = 0.25

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(1)
        return connection, address

    def handle_error(self, request, client_address):
        pass  # Never print callback tracebacks/URLs.

    def wait_for_code(self, timeout):
        deadline = time.monotonic() + timeout
        while self.result is None and time.monotonic() < deadline:
            self.handle_request()
        if self.result is None:
            raise ProbeError("Consent timed out; run again for fresh PKCE/state")
        code, error = self.result
        if error:
            raise ProbeError("Consent was denied or failed")
        return code


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, b"Invalid callback request.")

    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        try:
            parts = urlsplit(self.path)
            expected_host = "127.0.0.1:" + str(self.server.server_port)
            if (len(self.path) > 8192 or parts.scheme or parts.netloc or
                    parts.path != "/callback" or parts.fragment or
                    self.headers.get_all("Host") != [expected_host] or
                    self.headers.get("Transfer-Encoding") is not None or
                    self.headers.get("Content-Length", "0") != "0"):
                raise ValueError()
            fields = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True,
                              max_num_fields=12)
            if any(len(values) != 1 for values in fields.values()):
                raise ValueError()
            state = fields.get("state", [""])[0]
            if not hmac.compare_digest(state.encode(), self.server.state.encode()):
                raise ValueError()
            code, error = fields.get("code", [""])[0], fields.get("error", [""])[0]
            if ("code" in fields) == ("error" in fields) or not (code or error):
                raise ValueError()
            if self.server.result is not None:
                self.reply(409, b"Callback already received.")
                return
            self.server.result = (code, bool(error))
            self.reply(200, b"Callback received. You may close this tab. Check the probe terminal for verification.")
        except ValueError:
            self.reply(400, b"Invalid callback request.")


def claim_checks(token, user, issuer, resource, client_id, now=None):
    """Call ONLY after /user has verified this identical token successfully."""
    now = int(time.time()) if now is None else now
    try:
        if len(token) > 16384 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
            raise ValueError()
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        aud = claims.get("aud")
        audiences = [aud] if isinstance(aud, str) else aud
        integer = lambda value: type(value) is int and abs(value) <= 9007199254740991
        return dict(
            issuer_matches=claims.get("iss") == issuer,
            subject_matches=isinstance(user.get("id"), str) and bool(user["id"]) and claims.get("sub") == user["id"],
            resource_audience=isinstance(audiences, list) and all(isinstance(a, str) for a in audiences) and resource in audiences,
            client_matches=claims.get("client_id") == client_id,
            not_expired=integer(claims.get("exp")) and claims["exp"] > now,
            times_valid=all(k not in claims or (integer(claims[k]) and claims[k] <= now) for k in ("nbf", "iat")),
            scope_valid="scope" not in claims or isinstance(claims["scope"], str),
        )
    except Exception:
        raise ProbeError("Malformed access token (contents suppressed)") from None


def verify_code(code, verifier, issuer, resource, client_id, public_key):
    try:
        response = request_json(issuer.rstrip("/") + "/oauth/token", form=dict(
            grant_type="authorization_code", code=code, code_verifier=verifier,
            client_id=client_id, redirect_uri=CALLBACK, resource=resource))
    except ProbeError as error:
        raise ProbeError("Token exchange: " + str(error)) from None
    token = response.get("access_token")
    if (not isinstance(token, str) or not token or len(token) > 16384 or
            str(response.get("token_type", "")).lower() != "bearer"):
        raise ProbeError("Token response missing a bounded Bearer access token")
    try:
        user = request_json(issuer.rstrip("/") + "/user",
                            headers={"Authorization": "Bearer " + token, "apikey": public_key})
    except ProbeError as error:
        raise ProbeError("Provider verification: " + str(error)) from None
    return claim_checks(token, user, issuer, resource, client_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issuer", required=True, type=https_url)
    parser.add_argument("--resource", required=True, type=https_url)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    public_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not public_key or not args.client_id.strip() or not 1 <= args.timeout <= 600:
        parser.error("Set SUPABASE_ANON_KEY (public key), nonempty client ID, timeout 1..600")
    # URL.href in the gateway canonicalizes an origin to a trailing slash.
    resource = args.resource if urlsplit(args.resource).path else args.resource + "/"
    url, state, verifier = new_authorization(args.issuer, resource, args.client_id)
    try:
        with CallbackServer(state) as listener:
            print("Open this URL in your browser and approve only the expected test client. Do not paste codes/tokens into chat:", flush=True)
            print(url, flush=True)
            code = listener.wait_for_code(args.timeout)
        checks = verify_code(code, verifier, args.issuer, resource, args.client_id, public_key)
        print(json.dumps({"provider_verified": True, **checks}, sort_keys=True))
        return 0 if all(checks.values()) else 1
    except ProbeError as error:
        print(str(error))
    except KeyboardInterrupt:
        print("Probe cancelled")
    except Exception:
        print("Probe failed (details suppressed; check configuration and loopback port)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
