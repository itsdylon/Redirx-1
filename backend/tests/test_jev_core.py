"""URL matching contract, independent of the API, database and provider SDK.

Request digests and rankings were captured from release 3ddece1 before cleanup.
Changing the model protocol requires an explicit prompt/cache version decision.
"""
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
import numpy as np
from src.redirx.jev.data import Page, page_view
from src.redirx.jev.examples import ExampleBank
from src.redirx.jev.retrieve import Retriever
from src.redirx.jev.pipeline import Config, map_page

class Embeddings:
    def embed(self, texts):
        values = np.array([list(hashlib.sha256(t.encode()).digest()) for t in texts], dtype=float)
        return values / np.linalg.norm(values, axis=1, keepdims=True)

class Judge:
    def __init__(self, refuse=False):
        self.calls = []
        self.refuse = refuse
    def ask(self, state, questions):
        self.calls.append({'state': state, 'questions': questions})
        answers = {}
        for name, q in questions.items():
            if q['type'] == 'choice':
                keys = list(q['criteria'])
                winner = 'none' if self.refuse else keys[0]
                probs = {key: (0.8 if key == winner else 0.2/(len(keys)-1)) for key in keys}
                answers[name] = {'probabilities': probs, 'confidence': 0.8}
            elif q['type'] == 'noul':
                answers[name] = {'noul': 0.7}
            else:
                answers[name] = {'probabilities': {'0': 0.1, '1': 0.1, '2': 0.1, '3': 0.7}, 'score': 0.8, 'confidence': 0.7}
        return {'answers': answers, 'usage': {'input_tokens': 42}, 'latency': 0.1, 'cached': False}


class UrlCoreContract(unittest.TestCase):
    def test_release_rankings_requests_and_proposals_survive_cleanup(self):
        old = Page(site='fixture', url='https://old.example/v1/Page-0?Q=A%2FB', id='source')
        pages = [Page(site='fixture', url=f'https://new.example/v2/Page-{j}?Q=A%2FB') for j in range(10)]
        seed = Page(site='fixture', url='https://old.example/v1/Page-9?Q=A%2FB', true_new_url=pages[9].url)
        expected = {
            False: ([1, 4, 3, 5, 2], ['f7e00293019321f4b2438bda6f2e7b13efb0cf28074a4851bc241a88363beb89', '732611261ea1e67e36c57c5b899e04c93c8575b4d94b9a06a866aad60dd64734']),
            True: ([1, 0, 4, 3, 2], ['30d55c810db44378e7ef61c15786771670d0b55e5f500e569e1fba6ca508ea17', '88ef40dbd4466216ea2636b3a5ea0c37d5c11426f051dbbaeed5548d44924d8c']),
        }
        for seeded in (False, True):
            retrieval = Retriever(pages, True, embedding_cache=Embeddings())
            retrieval.prime_queries([old])
            if seeded:
                retrieval.calibrate([seed])
            examples = ExampleBank([seed] if seeded else []).nearest(old, 3)
            candidates, _ = retrieval.candidates(old, 5, examples=examples)
            self.assertEqual([p.url for p in candidates], [pages[i].url for i in expected[seeded][0]])
            for refuse in (False, True):
                with self.subTest(seeded=seeded, refuse=refuse):
                    judge = Judge(refuse)
                    record = map_page(old, candidates, judge, Config(), examples)
                    digests = [hashlib.sha256(json.dumps(q, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest() for q in judge.calls]
                    self.assertEqual(digests, expected[seeded][1])
                    self.assertEqual(record['decision'], None if refuse else candidates[0].url)
                    self.assertEqual(record['p_decision'], 0.8)
                    self.assertEqual((record['tokens'], record['latency']), (84, 0.2))

    def test_empty_candidates_make_no_provider_call_and_no_gone_policy(self):
        judge = Judge()
        result = map_page(Page('site', '/a'), [], judge, Config())
        self.assertEqual(judge.calls, [])
        self.assertIsNone(result['decision'])
        self.assertEqual(result['stage1']['top'], [])
        self.assertNotIn('action', result)

    def test_raw_urls_survive_and_confirmed_target_is_not_model_input(self):
        page = Page('site', 'https://old.example/Case%2FPath?q=A&B=2', true_new_url='https://secret.example/answer')
        self.assertEqual(page_view(page), {'url': page.url})
        self.assertEqual(ExampleBank([page]).nearest(page, 3), [])

    def test_algorithm_imports_without_application_or_provider_modules(self):
        code = "import sys; from src.redirx.jev import pipeline, retrieve, examples; assert not any(name in sys.modules for name in ('flask', 'supabase', 'openai', 'typesafe_sdk', 'src.redirx.jev.jev', 'backend.worker')); print('independent core')"
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'independent core')
