import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.services.deep_preview_service import CandidateRow, DeepPreviewService


class DeepPreviewServiceTests(unittest.TestCase):
    def test_score_candidates_prioritizes_high_risk_rows(self):
        service = DeepPreviewService.__new__(DeepPreviewService)

        source_mappings = [
            {
                'old_url': 'https://old.example.com/pricing/agency',
                'new_url': 'https://new.example.com/contact',
                'confidence_score': 0.50,
                'needs_review': True,
                'match_type': 'semantic_low',
            },
            {
                'old_url': 'https://old.example.com/about',
                'new_url': 'https://new.example.com/about',
                'confidence_score': 0.88,
                'needs_review': False,
                'match_type': 'semantic_medium',
            },
            {
                'old_url': 'https://old.example.com/products/alpha',
                'new_url': 'https://new.example.com/products',
                'confidence_score': 0.72,
                'needs_review': False,
                'match_type': 'semantic_medium',
            },
            {
                'old_url': 'https://old.example.com/faq',
                'new_url': 'https://new.example.com/support',
                'confidence_score': 0.65,
                'needs_review': False,
                'match_type': 'semantic_low',
            },
            {
                'old_url': 'https://old.example.com/exact',
                'new_url': 'https://new.example.com/exact',
                'confidence_score': 1.0,
                'needs_review': False,
                'match_type': 'exact_url',
            },
        ]

        candidates = service._score_candidates(source_mappings)

        self.assertEqual(len(candidates), 4)
        self.assertEqual(candidates[0].old_url, 'https://old.example.com/pricing/agency')
        self.assertTrue(all(c.old_url != 'https://old.example.com/exact' for c in candidates))

    def test_build_convincing_fixes_filters_non_persuasive_deltas(self):
        service = DeepPreviewService.__new__(DeepPreviewService)
        service.mapping_db = Mock()

        source_session_id = uuid4()
        preview_session_id = uuid4()

        source_rows = [
            {
                'old_url': 'https://old.example.com/page-1',
                'new_url': 'https://new.example.com/wrong-1',
                'confidence_score': 0.60,
                'match_type': 'semantic_low',
            },
            {
                'old_url': 'https://old.example.com/page-2',
                'new_url': 'https://new.example.com/right-2',
                'confidence_score': 0.80,
                'match_type': 'semantic_medium',
            },
            {
                'old_url': 'https://old.example.com/page-3',
                'new_url': 'https://new.example.com/wrong-3',
                'confidence_score': 0.50,
                'match_type': 'semantic_low',
            },
        ]

        preview_rows = [
            {
                'old_url': 'https://old.example.com/page-1',
                'new_url': 'https://new.example.com/right-1',
                'confidence_score': 0.91,
                'needs_review': False,
                'match_type': 'semantic_high',
            },
            {
                'old_url': 'https://old.example.com/page-2',
                'new_url': 'https://new.example.com/right-2',  # same target, should be dropped
                'confidence_score': 0.95,
                'needs_review': False,
                'match_type': 'semantic_high',
            },
            {
                'old_url': 'https://old.example.com/page-3',
                'new_url': 'https://new.example.com/right-3',
                'confidence_score': 0.85,  # below required 0.86
                'needs_review': False,
                'match_type': 'semantic_medium',
            },
        ]

        service.mapping_db.get_mappings_by_session.side_effect = [source_rows, preview_rows]
        service._compute_deep_gaps = Mock(return_value={
            'https://old.example.com/page-1': 0.11,
            'https://old.example.com/page-2': 0.20,
            'https://old.example.com/page-3': 0.20,
        })

        convincing = service._build_convincing_fixes(source_session_id, preview_session_id)

        self.assertEqual(len(convincing), 1)
        self.assertEqual(convincing[0]['old_url'], 'https://old.example.com/page-1')
        self.assertEqual(convincing[0]['confidence_gain_points'], 31)

    def test_build_convincing_fixes_returns_all_qualifying_rows(self):
        service = DeepPreviewService.__new__(DeepPreviewService)
        service.mapping_db = Mock()

        source_session_id = uuid4()
        preview_session_id = uuid4()

        source_rows = []
        preview_rows = []
        deep_gaps = {}

        for i in range(9):
            old_url = f"https://old.example.com/page-{i}"
            source_rows.append({
                'old_url': old_url,
                'new_url': f"https://new.example.com/wrong-{i}",
                'confidence_score': 0.60,
                'match_type': 'semantic_low',
            })
            preview_rows.append({
                'old_url': old_url,
                'new_url': f"https://new.example.com/right-{i}",
                'confidence_score': 0.92,
                'needs_review': False,
                'match_type': 'semantic_high',
            })
            deep_gaps[old_url] = 0.10

        service.mapping_db.get_mappings_by_session.side_effect = [source_rows, preview_rows]
        service._compute_deep_gaps = Mock(return_value=deep_gaps)

        convincing = service._build_convincing_fixes(source_session_id, preview_session_id)

        self.assertEqual(len(convincing), 9)


class DeepPreviewSklearnImportTests(unittest.TestCase):
    """scikit-learn must stay out of the worker until a preview needs it."""

    def test_importing_the_service_does_not_import_sklearn(self):
        # A fresh interpreter, so no earlier test in this process can hide a
        # module-level sklearn import that the worker would pay for at startup.
        probe = (
            "import sys;"
            "import backend.services.deep_preview_service as service;"
            "print(sys.modules.get('sklearn') is None and sys.modules.get('scipy') is None)"
        )
        completed = subprocess.run(
            [sys.executable, '-c', probe], cwd=BASE_DIR, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip().splitlines()[-1], 'True')

    def test_subset_selection_loads_sklearn_and_ranks_the_matching_target(self):
        service = DeepPreviewService.__new__(DeepPreviewService)
        candidates = [
            CandidateRow(
                old_url='https://old.example.com/pricing/agency',
                quick_new_url='https://new.example.com/contact',
                quick_confidence=0.5,
                needs_review=True,
                path_similarity_normalized=0.2,
                risk_score=0.9,
            )
        ]
        new_urls = [f'https://new.example.com/filler-{index}' for index in range(6)]
        new_urls.append('https://new.example.com/pricing/agency')
        new_urls.append('https://new.example.com/contact')

        with patch('backend.services.deep_preview_service.Config', SimpleNamespace(
            PREVIEW_MAX_NEW_URLS_FULL_SCAN=4, PREVIEW_MAX_NEW_URLS_CAPPED=3,
        )):
            selected = service._select_new_url_subset(new_urls, candidates)

        self.assertIn('sklearn.feature_extraction.text', sys.modules)
        self.assertEqual(selected[0], 'https://new.example.com/contact')
        self.assertIn('https://new.example.com/pricing/agency', selected)
        self.assertEqual(len(selected), 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
