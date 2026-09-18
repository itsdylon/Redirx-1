"""
Guards for the one property that makes telemetry safe to sprinkle through
business logic: it cannot break the thing it is measuring.

Every test here is about a failure path, not a happy path. A capture that
works is not worth asserting — a capture that takes down an export is.
"""
import unittest

from backend.services import analytics_service
from backend.services.analytics_service import AppEvent, capture


class _RecordingClient:
    def __init__(self, explode=False):
        self.calls = []
        self.explode = explode

    def capture(self, **kwargs):
        if self.explode:
            raise RuntimeError("posthog is having a day")
        self.calls.append(kwargs)


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self):
        self._saved = (analytics_service._client, analytics_service._client_resolved)

    def tearDown(self):
        analytics_service._client, analytics_service._client_resolved = self._saved

    def _install(self, client):
        analytics_service._client = client
        analytics_service._client_resolved = True

    def test_no_client_is_a_silent_noop(self):
        """Local dev and CI run without POSTHOG_API_KEY. That must be boring."""
        self._install(None)
        capture(AppEvent.REDIRECT_ARTIFACT_EXPORTED, user_id="u1")

    def test_capture_failure_never_reaches_the_caller(self):
        client = _RecordingClient(explode=True)
        self._install(client)
        capture(AppEvent.MIGRATION_PAYMENT_COMPLETED, user_id="u1")

    def test_event_without_user_is_dropped_not_faked(self):
        """
        An ownerless event means a bug upstream. Inventing a distinct_id would
        hide it behind a row that looks fine.
        """
        client = _RecordingClient()
        self._install(client)
        capture(AppEvent.REDIRECT_ARTIFACT_EXPORTED, user_id=None)
        capture(AppEvent.REDIRECT_ARTIFACT_EXPORTED, user_id="")
        self.assertEqual(client.calls, [])

    def test_payload_carries_the_source_split_and_migration_id(self):
        client = _RecordingClient()
        self._install(client)
        capture(
            AppEvent.MIGRATION_QUOTE_PRESENTED,
            user_id="u1",
            migration_id="11111111-2222-3333-4444-555555555555",
            properties={"billable_pages": 500},
        )
        (call,) = client.calls
        self.assertEqual(call["distinct_id"], "u1")
        self.assertEqual(call["event"], "migration_quote_presented")
        props = call["properties"]
        # Three surfaces share one PostHog project; without these two an
        # insight cannot tell a backend outcome from a browser click.
        self.assertEqual(props["source_repo"], "app")
        self.assertEqual(props["source_component"], "backend")
        self.assertEqual(props["migration_id"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(props["billable_pages"], 500)

    def test_catalogue_has_no_duplicate_values(self):
        """
        Two enum members sharing a value would merge two distinct funnel steps
        into one row in PostHog, which is invisible until someone tries to
        build the funnel months later.
        """
        values = [event.value for event in AppEvent]
        self.assertEqual(len(values), len(set(values)))
        for value in values:
            self.assertRegex(value, r"^[a-z][a-z0-9_]*$", f"{value} is not snake_case")


if __name__ == "__main__":
    unittest.main()
