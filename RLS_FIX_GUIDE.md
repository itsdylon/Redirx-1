# 🔧 Fix for RLS Policy Violation Error

## Problem

You're seeing this error when trying to create a new mapping:

```json
{
  "error": "Unexpected error: {'message': 'new row violates row-level security policy for table \"migration_sessions\"', 'code': '42501', ...}",
  "success": false
}
```

## Root Cause

The backend is using the **anon/public Supabase key** instead of the **service role key**.

When the authentication system was implemented, Row-Level Security (RLS) policies were created. These policies check if `auth.uid()` matches the user, but the anon key doesn't have admin privileges to bypass RLS.

## Solution

Update your `.env` file to use the **service role key** instead of the anon key.

### Step 1: Get Your Service Role Key

1. Go to your Supabase project dashboard
2. Navigate to: **Settings → API**
3. Find the section labeled **"Project API keys"**
4. Copy the **`service_role` secret** key (starts with `eyJ...`)

**⚠️ IMPORTANT:**
- The service role key is SECRET and should NEVER be exposed to the frontend
- Only use it in backend/server environments
- Never commit it to git

### Step 2: Update Your .env File

Open `/Users/dylonshattuck/Documents/Redirx/.env` and update the `SUPABASE_KEY`:

```bash
# BEFORE (using anon key - subject to RLS):
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InB...

# AFTER (using service role key - bypasses RLS):
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0...
```

### Step 3: Restart Your Backend Server

```bash
# Stop the current Flask server (Ctrl+C)

# Restart it
cd /Users/dylonshattuck/Documents/Redirx
source venv/bin/activate
python backend/app.py
```

### Step 4: Test the Fix

1. Open your frontend
2. Login with your account
3. Try to upload CSV files and create a new mapping
4. It should now work without the RLS error! ✅

## Why This Works

### Backend Architecture (What We Have)

```
Backend (Flask)
  ↓
SupabaseClient using SERVICE_ROLE_KEY
  ↓
Database (PostgreSQL with RLS enabled)
```

- **Service role key** → Trusted admin access → **Bypasses RLS**
- Backend validates users with JWT (`@require_auth`)
- Backend enforces user isolation in code (`user_id = request.user.id`)
- RLS is still active for frontend clients using the anon key

### Frontend Architecture (For Future)

```
Frontend (React)
  ↓
Supabase Client using ANON_KEY
  ↓
Database (PostgreSQL with RLS enabled)
```

- **Anon key** → Untrusted client → **Subject to RLS**
- User must be authenticated
- RLS policies enforce data isolation at database level

## Verification

After restarting, test that it works:

```bash
# Should succeed now
curl -X POST http://127.0.0.1:5001/api/process \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -F "old_csv=@tests/csv_test/old.csv" \
  -F "new_csv=@tests/csv_test/new.csv"
```

Expected response:
```json
{
  "success": true,
  "message": "Pipeline completed successfully",
  "session_id": "uuid-here"
}
```

## Security Notes

### ✅ Safe Practices

- ✅ Service role key used only in backend (trusted environment)
- ✅ Backend validates users with JWT tokens
- ✅ Backend enforces `user_id = request.user.id` in code
- ✅ RLS still protects against direct database access
- ✅ Frontend would use anon key (if directly calling Supabase)

### ❌ What NOT To Do

- ❌ Never expose service role key in frontend code
- ❌ Never commit service role key to git
- ❌ Never send service role key to clients

## Alternative Solutions (Not Recommended)

### Option B: Disable RLS (NOT RECOMMENDED)

If you want to remove RLS entirely (not recommended for production):

```sql
-- In Supabase SQL Editor
ALTER TABLE migration_sessions DISABLE ROW LEVEL SECURITY;
ALTER TABLE webpage_embeddings DISABLE ROW LEVEL SECURITY;
ALTER TABLE url_mappings DISABLE ROW LEVEL SECURITY;
```

**Why not recommended:** Removes database-level security for multi-tenancy.

### Option C: Modify RLS Policies (Complex)

You could modify RLS policies to allow service role, but this is more complex and unnecessary since service role bypasses RLS by default.

## Troubleshooting

### Still Getting RLS Error?

1. **Verify you copied the correct key:**
   - Should be the **service_role** key, not **anon** key
   - Should start with `eyJ` and be very long (~200+ characters)

2. **Check the key is loaded:**
   ```python
   # Add this temporarily to backend/app.py
   from src.redirx.config import Config
   print("Using key:", Config.SUPABASE_KEY[:20] + "...")
   ```

3. **Restart the server completely:**
   - Kill the Python process
   - Restart the terminal
   - Reactivate venv
   - Run server again

4. **Verify the .env file is in the right place:**
   ```bash
   ls -la /Users/dylonshattuck/Documents/Redirx/.env
   # Should exist and contain SUPABASE_KEY=...
   ```

### Other Errors?

- **"Invalid API key"** → Double-check you copied the full key
- **"Project not found"** → Verify SUPABASE_URL is correct
- **"Unauthorized"** → Make sure you're logged in and have a valid JWT token

## Need Help?

Check these files:
- `.env.example` - Template with updated comments
- `src/redirx/config.py` - Configuration loading (now with comments)
- `QA_AUTHENTICATION_REPORT.md` - Full authentication system documentation

---

**Fixed by:** QA Agent
**Date:** December 4, 2025
**Issue:** RLS policy violation on migration_sessions table
**Solution:** Use service role key instead of anon key in backend
