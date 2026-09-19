"""Local SDK parser diagnostics, not external Stripe payment acceptance."""
import hashlib
import hmac
import json
import time
import unittest

from scripts.acceptance.stripe_sandbox import safe_frames, verified_event


class ActualSDKParsing(unittest.TestCase):
    secret = 'whsec_local_parser_fixture_not_provider_authority'

    def event(self, **changes):
        timestamp = int(time.time())
        event = {'id': 'evt_local_parser_fixture', 'object': 'event', 'livemode': False,
                 'type': 'checkout.session.completed', 'created': timestamp,
                 'data': {'object': {'id': 'cs_test_local_fixture', 'object': 'checkout.session', 'mode': 'payment'}}}
        event.update(changes)
        raw = json.dumps(event).encode()
        digest = hmac.new(self.secret.encode(), str(timestamp).encode()+b'.'+raw, hashlib.sha256).hexdigest()
        return raw, f't={timestamp},v1={digest}'

    def test_actual_construct_event_output_is_normalized_before_get(self):
        raw, signature = self.event()
        result = verified_event(raw, signature, self.secret)
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result['data'], dict)
        self.assertIsInstance(result['data']['object'], dict)
        self.assertEqual(result.get('type'), 'checkout.session.completed')
        self.assertEqual(result['data']['object'].get('mode'), 'payment')

    def test_wrong_signature_cannot_reach_routing(self):
        raw, signature = self.event()
        with self.assertRaises(Exception): verified_event(raw, signature, 'whsec_wrong_fixture')

    def test_live_and_connect_events_are_rejected(self):
        for changes in ({'livemode': True}, {'account': 'acct_connected_fixture'}):
            raw, signature = self.event(**changes)
            with self.assertRaises(ValueError): verified_event(raw, signature, self.secret)

    def test_diagnostic_frames_omit_exception_text_locals_and_source(self):
        def raises():
            local_secret = 'DO_NOT_EXPOSE_FIXTURE_SECRET'
            raise ValueError(local_secret)
        try: raises()
        except ValueError as exc: frames = safe_frames(exc)
        self.assertTrue(frames)
        self.assertNotIn('DO_NOT_EXPOSE', json.dumps(frames))
        self.assertTrue(all(set(frame) == {'file', 'line', 'function'} for frame in frames))
        self.assertTrue(all('/' not in frame['file'] for frame in frames))


if __name__ == '__main__': unittest.main()
