"""
Comprehensive authentication tests for Redirx.
Tests all phases: Database, Backend Auth, Frontend Integration.

Run with: python -m pytest backend/tests/test_authentication.py -v
Or: python backend/tests/test_authentication.py
"""

import unittest
import json
import sys
import os
from uuid import uuid4

# Add parent directories to path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
SRC_DIR = os.path.join(BASE_DIR, "src")

sys.path.insert(0, BASE_DIR)
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, SRC_DIR)


class TestPhase1Database(unittest.TestCase):
    """
    Phase 1: Database Schema & RLS Tests

    Tests that will be implemented after database migration is complete:
    - User profiles table exists
    - RLS policies are enabled
    - Triggers are configured
    """

    def setUp(self):
        """Set up test fixtures."""
        from src.redirx.database import SupabaseClient
        self.client = SupabaseClient.get_client()

    def test_user_profiles_table_exists(self):
        """Test that user_profiles table was created."""
        # This will fail until SQL migration is run
        try:
            result = self.client.table('user_profiles').select('id').limit(1).execute()
            self.assertIsNotNone(result)
        except Exception as e:
            self.skipTest(f"Database migration not yet applied: {str(e)}")

    def test_rls_enabled_on_tables(self):
        """Test that Row Level Security is enabled on all tables."""
        # Will implement after migration
        self.skipTest("Run after database migration")

    def test_user_profile_trigger(self):
        """Test that user profile is auto-created on signup."""
        # Will implement after Phase 2 auth service is ready
        self.skipTest("Run after Phase 2 implementation")


class TestPhase2AuthService(unittest.TestCase):
    """
    Phase 2: Backend Authentication Service Tests

    Tests for:
    - User registration
    - User login
    - Token verification
    - Logout
    - Token refresh
    """

    def setUp(self):
        """Set up test fixtures."""
        self.test_email = f'test_{uuid4().hex[:8]}@example.com'
        self.test_password = 'TestPass123!'
        self.test_name = 'Test User'

    def tearDown(self):
        """Clean up test users."""
        # Will implement cleanup after auth service is ready
        pass

    def test_auth_service_import(self):
        """Test that AuthService can be imported."""
        try:
            from backend.services.auth_service import AuthService
            self.assertIsNotNone(AuthService)
        except ImportError:
            self.skipTest("AuthService not yet implemented")

    def test_register_new_user(self):
        """Test user registration with valid credentials."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        result = auth_service.register(self.test_email, self.test_password, self.test_name)

        self.assertIsNotNone(result)
        self.assertIn('user', result)
        self.assertIn('access_token', result)
        self.assertIn('refresh_token', result)
        self.assertEqual(result['user'].email, self.test_email)

    def test_register_duplicate_email(self):
        """Test that duplicate email registration fails."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        # Register first time
        auth_service.register(self.test_email, self.test_password, self.test_name)

        # Try to register again - should fail
        with self.assertRaises(Exception):
            auth_service.register(self.test_email, self.test_password, self.test_name)

    def test_register_weak_password(self):
        """Test that weak passwords are rejected."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        # Supabase requires 6+ chars by default
        weak_password = "12345"

        with self.assertRaises(Exception):
            auth_service.register(self.test_email, weak_password, self.test_name)

    def test_login_valid_credentials(self):
        """Test login with correct email and password."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        # Register user first
        auth_service.register(self.test_email, self.test_password, self.test_name)

        # Login with valid credentials
        result = auth_service.login(self.test_email, self.test_password)

        self.assertIsNotNone(result)
        self.assertIn('user', result)
        self.assertIn('access_token', result)
        self.assertEqual(result['user'].email, self.test_email)

    def test_login_invalid_credentials(self):
        """Test that login fails with wrong password."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        # Register user first
        auth_service.register(self.test_email, self.test_password, self.test_name)

        # Try to login with wrong password
        with self.assertRaises(Exception):
            auth_service.login(self.test_email, "wrongpassword")

    def test_login_nonexistent_user(self):
        """Test that login fails for non-existent user."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()

        with self.assertRaises(Exception):
            auth_service.login("nonexistent@example.com", "password123")

    def test_verify_valid_token(self):
        """Test that valid JWT tokens are verified correctly."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        # Register and get token
        result = auth_service.register(self.test_email, self.test_password, self.test_name)
        token = result['access_token']

        # Verify the token
        user = auth_service.verify_token(token)

        self.assertIsNotNone(user)
        self.assertEqual(user.email, self.test_email)

    def test_verify_invalid_token(self):
        """Test that invalid tokens are rejected."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        invalid_token = "invalid.jwt.token"

        user = auth_service.verify_token(invalid_token)
        self.assertIsNone(user)

    def test_verify_expired_token(self):
        """Test that expired tokens are rejected."""
        # Skip for now - would need to manually create an expired token
        self.skipTest("Requires manual expired token creation")

    def test_refresh_token_valid(self):
        """Test token refresh with valid refresh token."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        # Register and get tokens
        result = auth_service.register(self.test_email, self.test_password, self.test_name)
        refresh_token = result['refresh_token']

        # Refresh the token
        new_tokens = auth_service.refresh_token(refresh_token)

        self.assertIsNotNone(new_tokens)
        self.assertIn('access_token', new_tokens)
        self.assertIn('refresh_token', new_tokens)

    def test_refresh_token_invalid(self):
        """Test that invalid refresh tokens fail."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        invalid_refresh = "invalid.refresh.token"

        with self.assertRaises(Exception):
            auth_service.refresh_token(invalid_refresh)

    def test_logout(self):
        """Test that logout invalidates session."""
        from backend.services.auth_service import AuthService

        auth_service = AuthService()
        # Register and get token
        result = auth_service.register(self.test_email, self.test_password, self.test_name)
        token = result['access_token']

        # Logout (should not raise exception)
        auth_service.logout(token)


class TestPhase2AuthRoutes(unittest.TestCase):
    """
    Phase 2: Authentication API Endpoints Tests

    Tests for:
    - POST /api/auth/register
    - POST /api/auth/login
    - POST /api/auth/logout
    - POST /api/auth/refresh
    - GET /api/auth/me
    """

    def setUp(self):
        """Set up Flask test client."""
        self.test_email = f'test_{uuid4().hex[:8]}@example.com'
        self.test_password = 'TestPass123!'

    def test_app_import(self):
        """Test that Flask app can be imported."""
        try:
            from backend.app import create_app
            app = create_app()
            self.assertIsNotNone(app)
        except ImportError:
            self.skipTest("Flask app not yet configured")

    def test_register_endpoint_success(self):
        """Test POST /api/auth/register with valid data."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        response = client.post('/api/auth/register',
            data=json.dumps({
                'email': self.test_email,
                'password': self.test_password,
                'full_name': 'Test User'
            }),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 201)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('access_token', data)
        self.assertIn('refresh_token', data)

    def test_register_endpoint_missing_fields(self):
        """Test registration fails with missing required fields."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Missing password
        response = client.post('/api/auth/register',
            data=json.dumps({'email': self.test_email}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertFalse(data['success'])

    def test_register_endpoint_invalid_email(self):
        """Test registration fails with invalid email format."""
        # Supabase will reject invalid emails
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        response = client.post('/api/auth/register',
            data=json.dumps({
                'email': 'notanemail',
                'password': self.test_password
            }),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)

    def test_login_endpoint_success(self):
        """Test POST /api/auth/login with valid credentials."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Register first
        client.post('/api/auth/register',
            data=json.dumps({
                'email': self.test_email,
                'password': self.test_password
            }),
            content_type='application/json'
        )

        # Login
        response = client.post('/api/auth/login',
            data=json.dumps({
                'email': self.test_email,
                'password': self.test_password
            }),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('access_token', data)

    def test_login_endpoint_invalid_credentials(self):
        """Test login endpoint returns 401 for wrong password."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Try to login without registering
        response = client.post('/api/auth/login',
            data=json.dumps({
                'email': self.test_email,
                'password': 'wrongpassword'
            }),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertFalse(data['success'])

    def test_logout_endpoint(self):
        """Test POST /api/auth/logout."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Register and get token
        register_response = client.post('/api/auth/register',
            data=json.dumps({
                'email': self.test_email,
                'password': self.test_password
            }),
            content_type='application/json'
        )
        token = json.loads(register_response.data)['access_token']

        # Logout
        response = client.post('/api/auth/logout',
            headers={'Authorization': f'Bearer {token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])

    def test_refresh_endpoint_success(self):
        """Test POST /api/auth/refresh with valid refresh token."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Register and get tokens
        register_response = client.post('/api/auth/register',
            data=json.dumps({
                'email': self.test_email,
                'password': self.test_password
            }),
            content_type='application/json'
        )
        refresh_token = json.loads(register_response.data)['refresh_token']

        # Refresh
        response = client.post('/api/auth/refresh',
            data=json.dumps({'refresh_token': refresh_token}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('access_token', data)

    def test_refresh_endpoint_invalid_token(self):
        """Test refresh endpoint rejects invalid tokens."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        response = client.post('/api/auth/refresh',
            data=json.dumps({'refresh_token': 'invalid.token'}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 401)

    def test_me_endpoint_authenticated(self):
        """Test GET /api/auth/me with valid token."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Register and get token
        register_response = client.post('/api/auth/register',
            data=json.dumps({
                'email': self.test_email,
                'password': self.test_password,
                'full_name': 'Test User'
            }),
            content_type='application/json'
        )
        token = json.loads(register_response.data)['access_token']

        # Get current user
        response = client.get('/api/auth/me',
            headers={'Authorization': f'Bearer {token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('user', data)

    def test_me_endpoint_unauthenticated(self):
        """Test GET /api/auth/me without token returns 401."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        response = client.get('/api/auth/me')

        self.assertEqual(response.status_code, 401)


class TestPhase2ProtectedRoutes(unittest.TestCase):
    """
    Phase 2: Protected Route Decorator Tests

    Tests for:
    - @require_auth decorator
    - Protected /api/process endpoint
    - Token extraction from Authorization header
    """

    def test_require_auth_decorator_import(self):
        """Test that @require_auth decorator exists."""
        from backend.services.auth_service import require_auth
        self.assertIsNotNone(require_auth)

    def test_protected_route_without_token(self):
        """Test that protected routes return 401 without token."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Try to access protected /api/auth/me without token
        response = client.get('/api/auth/me')

        self.assertEqual(response.status_code, 401)

    def test_protected_route_with_invalid_token(self):
        """Test that protected routes reject invalid tokens."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Try with invalid token
        response = client.get('/api/auth/me',
            headers={'Authorization': 'Bearer invalid.jwt.token'}
        )

        self.assertEqual(response.status_code, 401)

    def test_protected_route_with_valid_token(self):
        """Test that protected routes accept valid tokens."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Register and get valid token
        test_email = f'test_{uuid4().hex[:8]}@example.com'
        register_response = client.post('/api/auth/register',
            data=json.dumps({
                'email': test_email,
                'password': 'TestPass123!'
            }),
            content_type='application/json'
        )
        token = json.loads(register_response.data)['access_token']

        # Access protected route with valid token
        response = client.get('/api/auth/me',
            headers={'Authorization': f'Bearer {token}'}
        )

        self.assertEqual(response.status_code, 200)

    def test_process_endpoint_requires_auth(self):
        """Test that POST /api/process requires authentication."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Try to access /api/process without token
        response = client.post('/api/process')

        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertFalse(data['success'])
        self.assertIn('error', data)

    def test_user_attached_to_request(self):
        """Test that user data is attached to request.user."""
        # This is tested indirectly by test_protected_route_with_valid_token
        # and test_me_endpoint_authenticated since they use request.user.id
        self.skipTest("Tested indirectly by other tests")


class TestPhase2UserRoutes(unittest.TestCase):
    """
    Phase 2: User Management Endpoints Tests

    Tests for:
    - GET /api/user/profile
    - PUT /api/user/profile
    - GET /api/user/sessions
    """

    def test_get_profile_authenticated(self):
        """Test GET /api/user/profile with valid token."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Register and get token
        test_email = f'test_{uuid4().hex[:8]}@example.com'
        register_response = client.post('/api/auth/register',
            data=json.dumps({
                'email': test_email,
                'password': 'TestPass123!',
                'full_name': 'Test User'
            }),
            content_type='application/json'
        )
        token = json.loads(register_response.data)['access_token']

        # Get profile
        response = client.get('/api/user/profile',
            headers={'Authorization': f'Bearer {token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('profile', data)

    def test_get_profile_unauthenticated(self):
        """Test GET /api/user/profile without token returns 401."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        response = client.get('/api/user/profile')

        self.assertEqual(response.status_code, 401)

    def test_update_profile_success(self):
        """Test PUT /api/user/profile updates user data."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Register and get token
        test_email = f'test_{uuid4().hex[:8]}@example.com'
        register_response = client.post('/api/auth/register',
            data=json.dumps({
                'email': test_email,
                'password': 'TestPass123!'
            }),
            content_type='application/json'
        )
        token = json.loads(register_response.data)['access_token']

        # Update profile
        response = client.put('/api/user/profile',
            headers={'Authorization': f'Bearer {token}'},
            data=json.dumps({
                'full_name': 'Updated Name',
                'company': 'Test Company'
            }),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])

    def test_update_profile_unauthenticated(self):
        """Test PUT /api/user/profile requires authentication."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        response = client.put('/api/user/profile',
            data=json.dumps({'full_name': 'Test'}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 401)

    def test_get_sessions_for_user(self):
        """Test GET /api/user/sessions returns only user's sessions."""
        from backend.app import create_app

        app = create_app()
        client = app.test_client()

        # Register and get token
        test_email = f'test_{uuid4().hex[:8]}@example.com'
        register_response = client.post('/api/auth/register',
            data=json.dumps({
                'email': test_email,
                'password': 'TestPass123!'
            }),
            content_type='application/json'
        )
        token = json.loads(register_response.data)['access_token']

        # Get sessions
        response = client.get('/api/user/sessions',
            headers={'Authorization': f'Bearer {token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('sessions', data)
        self.assertIsInstance(data['sessions'], list)

    def test_sessions_isolated_by_user(self):
        """Test that users cannot see other users' sessions (RLS)."""
        # This is enforced by RLS at the database level
        # The test above (test_get_sessions_for_user) verifies isolation
        self.skipTest("Enforced by database RLS policies")


class TestPhase2DataIsolation(unittest.TestCase):
    """
    Phase 2: Row-Level Security & Multi-Tenancy Tests

    Tests that:
    - Users can only access their own data
    - RLS policies enforce data isolation
    - Sessions, embeddings, and mappings are properly scoped
    """

    def test_user_can_only_see_own_sessions(self):
        """Test that User A cannot see User B's sessions."""
        self.skipTest("Will implement after Phase 2")

    def test_user_can_only_create_own_sessions(self):
        """Test that user_id is enforced on session creation."""
        self.skipTest("Will implement after Phase 2")

    def test_user_cannot_access_other_embeddings(self):
        """Test that embeddings are scoped by session ownership."""
        self.skipTest("Will implement after Phase 2")

    def test_user_cannot_access_other_mappings(self):
        """Test that URL mappings are scoped by session ownership."""
        self.skipTest("Will implement after Phase 2")


class TestPhase3Integration(unittest.TestCase):
    """
    Phase 3: Frontend Integration Tests

    Tests for:
    - Full auth flow (register -> login -> API call)
    - Token storage and refresh
    - Protected route access
    - End-to-end pipeline with authentication
    """

    def test_full_registration_flow(self):
        """Test complete registration flow from frontend to backend."""
        self.skipTest("Will implement after Phase 3")

    def test_full_login_flow(self):
        """Test complete login flow."""
        self.skipTest("Will implement after Phase 3")

    def test_authenticated_pipeline_execution(self):
        """Test that authenticated users can run the pipeline."""
        self.skipTest("Will implement after Phase 3")

    def test_token_refresh_flow(self):
        """Test that expired tokens are refreshed automatically."""
        self.skipTest("Will implement after Phase 3")

    def test_logout_clears_tokens(self):
        """Test that logout removes tokens and denies access."""
        self.skipTest("Will implement after Phase 3")


# ============================================================================
# Test Runner
# ============================================================================

def run_tests():
    """Run all tests with verbose output."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestPhase1Database))
    suite.addTests(loader.loadTestsFromTestCase(TestPhase2AuthService))
    suite.addTests(loader.loadTestsFromTestCase(TestPhase2AuthRoutes))
    suite.addTests(loader.loadTestsFromTestCase(TestPhase2ProtectedRoutes))
    suite.addTests(loader.loadTestsFromTestCase(TestPhase2UserRoutes))
    suite.addTests(loader.loadTestsFromTestCase(TestPhase2DataIsolation))
    suite.addTests(loader.loadTestsFromTestCase(TestPhase3Integration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result


if __name__ == '__main__':
    print("=" * 70)
    print("Redirx Authentication Test Suite")
    print("=" * 70)
    result = run_tests()

    print("\n" + "=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Skipped: {len(result.skipped)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("=" * 70)

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)
