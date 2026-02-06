#!/usr/bin/env python3
"""
Direct test of idempotency logic without running the full backend.
"""
import hashlib
from dotenv import load_dotenv

load_dotenv()

from src.redirx.database import MigrationSessionDB

# Test data
test_user_id = "test-user-123"
test_old_urls = ["https://oldsite.com/page1", "https://oldsite.com/page2", "https://oldsite.com/page3"]
test_new_urls = ["https://newsite.com/page1", "https://newsite.com/page2"]

# Generate idempotency key (same logic as pipeline_runner.py)
sorted_old = sorted(test_old_urls)
sorted_new = sorted(test_new_urls)
key_data = f"{test_user_id}|{','.join(sorted_old)}|{','.join(sorted_new)}"
idempotency_key = hashlib.sha256(key_data.encode('utf-8')).hexdigest()

print("=" * 70)
print("IDEMPOTENCY TEST")
print("=" * 70)
print(f"\nTest user_id: {test_user_id}")
print(f"Generated key: {idempotency_key[:16]}...{idempotency_key[-8:]}")

session_db = MigrationSessionDB()

# Test 1: Check for existing session (should be None initially)
print("\n--- Test 1: Check for existing session ---")
existing = session_db.find_session_by_idempotency_key(test_user_id, idempotency_key)
if existing:
    print(f"✅ Found existing session: {existing['id']}")
    print(f"   Status: {existing['status']}")
    print(f"   Idempotency key: {existing.get('idempotency_key', 'N/A')[:16]}...")
else:
    print("❌ No existing session found (expected for first run)")

# Test 2: Create a new session with idempotency key
print("\n--- Test 2: Create new session ---")
session_id = session_db.create_session(
    user_id=test_user_id,
    project_name="Test Idempotency Project",
    old_urls=test_old_urls,
    new_urls=test_new_urls,
    idempotency_key=idempotency_key
)
print(f"✅ Created session: {session_id}")

# Test 3: Verify the session was created with the key
print("\n--- Test 3: Verify session has idempotency_key ---")
session = session_db.get_session(session_id)
stored_key = session.get('idempotency_key')
if stored_key:
    print(f"✅ Session has idempotency_key: {stored_key[:16]}...{stored_key[-8:]}")
    if stored_key == idempotency_key:
        print("✅ Key matches expected value!")
    else:
        print(f"❌ Key MISMATCH!")
        print(f"   Expected: {idempotency_key}")
        print(f"   Got:      {stored_key}")
else:
    print("❌ Session has NULL idempotency_key!")
    print("   This means the database column exists but the value is not being stored.")
    print("   Check if there's a constraint or trigger preventing the insert.")

# Test 4: Try to find the session again
print("\n--- Test 4: Find session by idempotency_key ---")
existing = session_db.find_session_by_idempotency_key(test_user_id, idempotency_key)
if existing:
    print(f"✅ Successfully found session: {existing['id']}")
    print(f"   Matches created session: {existing['id'] == str(session_id)}")
else:
    print("❌ Could not find session by idempotency_key!")
    print("   This means either:")
    print("   1. The key was not stored correctly")
    print("   2. The query is not finding it")

# Test 5: Try to create duplicate (should find existing)
print("\n--- Test 5: Simulate duplicate request ---")
print("Checking for existing session before creating...")
existing = session_db.find_session_by_idempotency_key(test_user_id, idempotency_key)
if existing:
    print(f"✅ DUPLICATE DETECTED! Found existing session: {existing['id']}")
    print("   In a real request, this would return is_duplicate=True")
else:
    print("❌ Did not detect duplicate! Would create a second session.")

print("\n" + "=" * 70)
print("TEST COMPLETE")
print("=" * 70)
print(f"\nCleanup: You can delete the test session manually from Supabase:")
print(f"Session ID: {session_id}")
