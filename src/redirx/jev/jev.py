"""Production adapter for the V1 Jev two-question-pass protocol.

Durable, run-scoped cache replaces the experiment's SQLite cache. The outer
worker owns retries; SDK retries are disabled so every billable attempt first
reserves budget. Provider exceptions never expose request URLs or keys.
"""
from __future__ import annotations
import hashlib
import json
import math
import time
import threading

_RATE_LOCK = threading.Lock()
_NEXT_REQUEST = 0.0

MODEL = 'jev-1.13.0'
PROMPT_VERSION = 'redirx-jev-url-v1'
PRICE_IN_PER_MTOK = 0.042
MAX_REQUEST_BYTES = 32000
MAX_BILLABLE_INPUT_TOKENS = 64000


class ProviderUnavailable(RuntimeError):
    def __init__(self, code='provider_unavailable'):
        self.code = code
        super().__init__(code + ': saved progress can resume with refine_matches; no additional free allowance is consumed.')


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def validate_response(result, questions):
    usage=result.get('usage',{})
    if type(usage.get('input_tokens')) is not int or usage['input_tokens']<0 or result.get('model') != MODEL:
        raise ProviderUnavailable('provider_accounting_unavailable')
    answers=result.get('answers',{})
    if set(answers) != set(questions): raise ProviderUnavailable('provider_response_invalid')
    for key, question in questions.items():
        answer=answers[key]
        if question['type']=='noul':
            value=answer.get('noul')
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
                raise ProviderUnavailable('provider_response_invalid')
        else:
            expected=set(question['criteria']) if question['type']=='choice' else {str(i) for i in range(len(question['criteria']))}
            probs=answer.get('probabilities',{})
            if set(probs)!=expected or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in probs.values()) or abs(sum(probs.values())-1)>.02:
                raise ProviderUnavailable('provider_response_invalid')
            for field in (('confidence',) if question['type']=='choice' else ('confidence','score')):
                value=answer.get(field)
                if type(value) not in (int,float) or not math.isfinite(value): raise ProviderUnavailable('provider_response_invalid')


class JevClient:
    def __init__(self, store, client=None):
        self.store = store
        self.model = MODEL
        self.live_requests = self.cached_requests = self.input_tokens = 0
        if client is None:
            from typesafe_sdk import TypeSafeClient, RetryPolicy
            client = TypeSafeClient(model=MODEL, timeout=45,
                                   retry=RetryPolicy(max_retries=0, timeout=45))
        self.client = client

    def ask(self, state, questions):
        request = {'m': self.model, 'prompt': PROMPT_VERSION, 's': state, 'q': questions}
        blob = encoded(request)
        if len(blob) > MAX_REQUEST_BYTES:
            raise ProviderUnavailable('request_capacity_exceeded')
        key = hashlib.sha256(blob).hexdigest()
        hit = self.store.cache_get(key)
        if hit is not None:
            validate_response(hit,questions)
            self.cached_requests += 1
            return {**hit, 'cached': True}
        # Reserve the model's documented total-input ceiling, then settle to
        # measured input tokens. Unknown/crashed calls retain this reservation.
        reservation = self.store.reserve(math.ceil(MAX_BILLABLE_INPUT_TOKENS * PRICE_IN_PER_MTOK))
        global _NEXT_REQUEST
        with _RATE_LOCK:
            time.sleep(max(0,_NEXT_REQUEST-time.monotonic()))
            _NEXT_REQUEST=time.monotonic()+0.15
        start = time.perf_counter()
        try:
            response = self.client.system_one(state, questions, model=self.model)
            result = {'answers': {key: value.model_dump(mode='json') for key, value in response.answers.items()},
                      'usage': {'input_tokens': response.usage.input_tokens,
                                'output_tokens': response.usage.output_tokens or 0},
                      'latency': time.perf_counter() - start, 'model': response.model}
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            code = 'provider_rate_limited' if status == 429 else 'provider_credit_exhausted' if status == 402 else 'provider_unavailable'
            raise ProviderUnavailable(code) from None
        validate_response(result,questions)
        self.store.cache_put(key, result, reservation, math.ceil(result['usage']['input_tokens']*PRICE_IN_PER_MTOK))
        self.live_requests += 1
        self.input_tokens += result['usage']['input_tokens']
        return {**result, 'cached': False}
