"""
Test the GET /api/results/<session_id> endpoint
"""
import sys
import os
import unittest
from uuid import uuid4

# Add parent directory to path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.app import create_app
from src.redirx.database import MigrationSessionDB, URLMappingDB, SupabaseClient


class TestResultsEndpoint(unittest.TestCase):
    """Test the results endpoint"""

    @classmethod
    def setUpClass(cls):
        """Set up Flask test client"""
        cls.app = create_app()
        cls.client = cls.app.test_client()
        cls.session_db = MigrationSessionDB()
        cls.mapping_db = URLMappingDB()

    def test_get_results_with_valid_session(self):
        """Test GET /api/results/<session_id> with a valid session"""
        # Create a test session
        session_id = self.session_db.create_session(user_id="test_user")

        # Create some test mappings
        mappings = [
            {
                'old_url': 'https://oldsite.com/page1',
                'new_url': 'https://newsite.com/page1',
                'confidence_score': 0.95,
                'match_type': 'exact_html',
                'needs_review': False
            },
            {
                'old_url': 'https://oldsite.com/page2',
                'new_url': 'https://newsite.com/page2',
                'confidence_score': 0.72,
                'match_type': 'semantic',
                'needs_review': True
            },
            {
                'old_url': 'https://oldsite.com/page3',
                'new_url': 'https://newsite.com/page3',
                'confidence_score': 0.45,
                'match_type': 'semantic',
                'needs_review': True
            }
        ]

        for mapping in mappings:
            self.mapping_db.insert_mapping(
                session_id=session_id,
                **mapping
            )

        # Call the endpoint
        response = self.client.get(f'/api/results/{session_id}')

        # Verify response
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertIn('mappings', data)
        self.assertIn('stats', data)

        # Verify mappings count
        self.assertEqual(len(data['mappings']), 3)

        # Verify data transformation
        first_mapping = data['mappings'][0]
        self.assertIn('id', first_mapping)
        self.assertIn('oldUrl', first_mapping)
        self.assertIn('newUrl', first_mapping)
        self.assertIn('confidence', first_mapping)
        self.assertIn('confidenceBand', first_mapping)
        self.assertIn('approved', first_mapping)
        self.assertIn('warnings', first_mapping)

        # Verify confidence conversion (0.95 -> 95)
        high_conf_mapping = [m for m in data['mappings'] if m['confidence'] == 95][0]
        self.assertEqual(high_conf_mapping['confidenceBand'], 'high')
        self.assertEqual(high_conf_mapping['approved'], True)  # needs_review=False -> approved=True

        # Verify medium confidence band
        medium_conf_mapping = [m for m in data['mappings'] if m['confidence'] == 72][0]
        self.assertEqual(medium_conf_mapping['confidenceBand'], 'medium')
        self.assertEqual(medium_conf_mapping['approved'], False)  # needs_review=True -> approved=False

        # Verify low confidence band
        low_conf_mapping = [m for m in data['mappings'] if m['confidence'] == 45][0]
        self.assertEqual(low_conf_mapping['confidenceBand'], 'low')

        # Verify stats
        stats = data['stats']
        self.assertEqual(stats['total'], 3)
        self.assertEqual(stats['high'], 1)
        self.assertEqual(stats['medium'], 1)
        self.assertEqual(stats['low'], 1)
        self.assertEqual(stats['approved'], 1)

        print("✅ Test passed: GET /api/results/<session_id> works correctly")

    def test_get_results_with_invalid_session_id(self):
        """Test GET /api/results/<session_id> with invalid session ID format"""
        response = self.client.get('/api/results/invalid-uuid')

        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertFalse(data['success'])
        self.assertIn('error', data)

        print("✅ Test passed: Invalid session ID returns 400")

    def test_get_results_with_nonexistent_session(self):
        """Test GET /api/results/<session_id> with non-existent session"""
        fake_uuid = str(uuid4())
        response = self.client.get(f'/api/results/{fake_uuid}')

        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertFalse(data['success'])
        self.assertIn('error', data)

        print("✅ Test passed: Non-existent session returns 404")


if __name__ == '__main__':
    print("\n🧪 Testing Results Endpoint\n")
    unittest.main(verbosity=2)
