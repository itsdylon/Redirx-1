# Testing Guide

## Database Connection Test

Before working with Supabase features, verify your setup is correct:

```bash
# From project root
python tests/test_database_connection.py
```

This test will:
- ✓ Verify your `.env` file is configured correctly
- ✓ Test connection to Supabase
- ✓ Test creating sessions, embeddings, and mappings
- ✓ Test vector similarity search
- ✓ Clean up all test data automatically

**Expected output:**
```
============================================================
Redirx Database Connection Test Suite
============================================================

✓ Configuration validated successfully
  Supabase URL: https://xxxxx.supabase.co
  OpenAI API Key: Set (optional)

test_01_client_connection ...
✓ Successfully connected to Supabase
ok

test_02_create_session ...
✓ Created migration session: 12345...
ok

... (more tests)

----------------------------------------------------------------------
Ran 7 tests in 2.5s

OK
```

**If tests fail:**
1. Check your `.env` file has correct `SUPABASE_URL` and `SUPABASE_KEY`
2. Verify you ran the SQL schema setup in Supabase (tables, indexes, functions)
3. Check your Supabase project is active and not paused

## Running All Tests

```bash
# Run all tests including stages
python tests/driver.py
```
