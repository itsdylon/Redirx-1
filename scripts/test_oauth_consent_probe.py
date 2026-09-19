"""Offline tests: fake provider responses; HTTP tests bind only loopback."""
import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from http.client import HTTPConnection
from io import StringIO
import json
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from scripts import oauth_consent_probe as probe


ISSUER = "https://provider.example/auth/v1"
RESOURCE = "https://mcp.example/"
CLIENT = "test-client"


def token(**overrides):
    claims = dict(iss=ISSUER, aud=RESOURCE, sub="user", client_id=CLIENT, exp=2000)
    claims.update(overrides)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "e30." + body + ".c2ln"


class AuthorizationTests(unittest.TestCase):
    def test_pkce_resource_state(self):
        url, state, verifier = probe.new_authorization(ISSUER, RESOURCE, CLIENT)
        query = parse_qs(urlsplit(url).query)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["state"], [state])
        self.assertEqual(query["resource"], [RESOURCE])
        self.assertEqual(query["redirect_uri"], [probe.CALLBACK])
        self.assertNotIn(verifier, url)
        self.assertNotEqual(state, probe.new_authorization(ISSUER, RESOURCE, CLIENT)[1])

    def test_configuration_urls(self):
        self.assertEqual(probe.https_url(ISSUER), ISSUER)
        for url in ["http://remote.example", "https://user:secret@host/", "https://host/?x=y",
                    "https://host/#hash", "https://host:99999", "https://host/\n"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.https_url(url)


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.server = probe.CallbackServer("expected", ("127.0.0.1", 0))
        self.addCleanup(self.server.server_close)

    def request(self, path, *, headers=None, method="GET"):
        thread = threading.Thread(target=self.server.handle_request)
        thread.start()
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    def test_capture_and_replay_without_secrets_or_logs(self):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status, body, headers = self.request("/callback?state=expected&code=secret-code")
            self.assertEqual(status, 200)
            self.assertEqual(self.server.wait_for_code(1), "secret-code")
            self.assertEqual(self.request("/callback?state=expected&code=replay")[0], 409)
        self.assertEqual(self.server.result, ("secret-code", False))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn(b"secret-code", body)
        self.assertEqual(out.getvalue() + err.getvalue(), "")

    def test_invalid_callbacks_do_not_consume_attempt(self):
        paths = ["/callback?state=wrong&code=x", "/callback?code=x",
                 "/callback?state=expected&state=expected&code=x",
                 "/callback?state=expected&code=x&code=y",
                 "/callback?state=expected&code=x&error=denied",
                 "/callback?state=expected&code=", "/callback?state=expected",
                 "/other?state=expected&code=x", "http://evil/callback?state=expected&code=x",
                 "/callback?state=expected&code=x#fragment",
                 "/callback?state=expected&code=x&error=",
                 "/callback?state=expected&code=" + "x" * 8200]
        for path in paths:
            with self.subTest(path=path[:100]):
                self.assertEqual(self.request(path)[0], 400)
                self.assertIsNone(self.server.result)
        self.assertEqual(self.request("/callback?state=expected&code=ok")[0], 200)

    def test_wrong_host_or_body(self):
        for headers in [{"Host": "evil.example"}, {"Content-Length": "1"},
                        {"Transfer-Encoding": "chunked"}]:
            self.assertEqual(self.request("/callback?state=expected&code=x", headers=headers)[0], 400)
            self.assertIsNone(self.server.result)

    def test_post_rejected(self):
        self.assertEqual(self.request("/callback?state=expected&code=x", method="POST")[0], 501)
        self.assertIsNone(self.server.result)

    def test_denial_is_generic(self):
        self.request("/callback?state=expected&error=secret-upstream-error")
        with self.assertRaisesRegex(probe.ProbeError, "^Consent was denied or failed$"):
            self.server.wait_for_code(1)

    def test_timeout(self):
        with self.assertRaisesRegex(probe.ProbeError, "timed out"):
            self.server.wait_for_code(0)

    def test_non_loopback_refused(self):
        with self.assertRaises(ValueError):
            probe.CallbackServer("state", ("0.0.0.0", 0))


class TokenTests(unittest.TestCase):
    def checks(self, **overrides):
        return probe.claim_checks(token(**overrides), {"id": "user"}, ISSUER, RESOURCE, CLIENT, now=1000)

    def test_valid(self):
        self.assertTrue(all(self.checks().values()))
        self.assertTrue(all(self.checks(aud=["authenticated", RESOURCE], nbf=1000, iat=999).values()))

    def test_gateway_constraints(self):
        invalid = [dict(iss="wrong"), dict(sub="other"), dict(aud="authenticated"),
                   dict(aud=RESOURCE.rstrip("/")), dict(aud=[RESOURCE, 1]),
                   dict(client_id="other"), dict(client_id=""), dict(exp=1000),
                   dict(exp=True), dict(exp="2000"), dict(exp=1e30),
                   dict(nbf=1001), dict(iat=1001), dict(nbf=True), dict(scope=[])]
        for claims in invalid:
            with self.subTest(claims=claims):
                self.assertFalse(all(self.checks(**claims).values()))

    def test_malformed_tokens(self):
        for value in ["secret", "a.W10.b", "a.@@@@.b", "x" * 16385]:
            with self.assertRaisesRegex(probe.ProbeError, "Malformed access token"):
                probe.claim_checks(value, {}, ISSUER, RESOURCE, CLIENT)

    @patch.object(probe, "request_json")
    def test_exchange_and_provider_verify_precede_claim_checks(self, request):
        bearer = token(exp=9999999999)
        request.side_effect = [{"access_token": bearer, "token_type": "Bearer"}, {"id": "user"}]
        self.assertTrue(all(probe.verify_code("code", "verifier", ISSUER, RESOURCE, CLIENT, "public-key").values()))
        first, second = request.call_args_list
        self.assertEqual(first.kwargs["form"]["resource"], RESOURCE)
        self.assertEqual(first.kwargs["form"]["code_verifier"], "verifier")
        self.assertEqual(first.kwargs["form"]["redirect_uri"], probe.CALLBACK)
        self.assertEqual(second.args, (ISSUER + "/user",))
        self.assertEqual(second.kwargs["headers"], {"Authorization": "Bearer " + bearer, "apikey": "public-key"})

    @patch.object(probe, "claim_checks")
    @patch.object(probe, "request_json")
    def test_failed_provider_cannot_authorize(self, request, checks):
        request.side_effect = [{"access_token": token(), "token_type": "bearer"}, probe.ProbeError("Rejected")]
        with self.assertRaises(probe.ProbeError):
            probe.verify_code("code", "verifier", ISSUER, RESOURCE, CLIENT, "public-key")
        checks.assert_not_called()

    @patch.object(probe, "request_json")
    def test_invalid_token_response(self, request):
        for response in [{}, {"access_token": "x", "token_type": "MAC"},
                         {"access_token": "x" * 16385, "token_type": "bearer"}]:
            request.return_value = response
            with self.assertRaises(probe.ProbeError):
                probe.verify_code("code", "verifier", ISSUER, RESOURCE, CLIENT, "public-key")


class TransportTests(unittest.TestCase):
    def test_redirect_refused(self):
        with self.assertRaises(probe.ProbeError):
            probe.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil/")

    @patch.object(probe, "build_opener")
    def test_response_bounds_and_redaction(self, opener):
        response = MagicMock()
        opener.return_value.open.return_value.__enter__.return_value = response
        for body in [b"x" * (probe.MAX_RESPONSE + 1), b"secret-token-not-json", b"[]"]:
            response.read.return_value = body
            with self.assertRaisesRegex(probe.ProbeError, "^Upstream request failed "):
                probe.request_json(ISSUER + "/user")
        response.read.assert_called_with(probe.MAX_RESPONSE + 1)
        opener.return_value.open.side_effect = RuntimeError("secret-token")
        with self.assertRaisesRegex(probe.ProbeError, "^Upstream request failed "):
            probe.request_json(ISSUER + "/user")


class ConsoleTests(unittest.TestCase):
    def run_main(self, listener, verify):
        output = StringIO()
        args = ["probe", "--issuer", ISSUER, "--resource", RESOURCE, "--client-id", CLIENT]
        with patch("sys.argv", args), patch.dict("os.environ", {"SUPABASE_ANON_KEY": "public-key"}), \
                patch.object(probe, "CallbackServer", listener), patch.object(probe, "verify_code", verify), \
                redirect_stdout(output):
            status = probe.main()
        return status, output.getvalue()

    def test_bind_failure_prints_no_auth_url_or_exception(self):
        status, output = self.run_main(MagicMock(side_effect=OSError("secret-error")), MagicMock())
        self.assertEqual(status, 1)
        self.assertNotIn("secret-error", output)
        self.assertNotIn("https://", output)

    def test_only_sanitized_result_printed(self):
        listener = MagicMock()
        listener.return_value.__enter__.return_value.wait_for_code.return_value = "secret-code"
        status, output = self.run_main(listener, MagicMock(return_value={"resource_audience": True}))
        self.assertEqual(status, 0)
        self.assertNotIn("secret-code", output)
        self.assertNotIn("public-key", output)
        self.assertEqual(json.loads(output.splitlines()[-1]), {"provider_verified": True, "resource_audience": True})

    def test_bad_claims_exit_nonzero(self):
        status, output = self.run_main(MagicMock(), MagicMock(return_value={"resource_audience": False}))
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(output.splitlines()[-1])["resource_audience"])

    def test_arbitrary_exception_redacted(self):
        status, output = self.run_main(MagicMock(), MagicMock(side_effect=RuntimeError("secret-token")))
        self.assertEqual(status, 1)
        self.assertNotIn("secret-token", output)


if __name__ == "__main__":
    unittest.main()
