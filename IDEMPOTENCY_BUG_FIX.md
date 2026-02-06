# Idempotency Bug Fix

## Problem
Uploading the same CSV twice was not detecting duplicates and was running the full pipeline both times.

## Root Cause
**React onClick event object bug** in `UploadPage.tsx`

The "Begin Matching" button was defined as:
```tsx
<Button onClick={handleBeginMatching}>
```

This passes the **MouseEvent object** as the first parameter to `handleBeginMatching(force)`. Since any object is truthy in JavaScript:
- `handleBeginMatching(MouseEvent)` → `force = MouseEvent` (truthy)
- `if (force) { formData.append("force", "true") }` → Always true!
- Backend receives `force=true` → Bypasses idempotency check

## Solution
Changed to:
```tsx
<Button onClick={() => handleBeginMatching()}>
```

This explicitly calls the function with no arguments, so `force` defaults to `false`.

## Files Changed
- `frontend/src/components/UploadPage.tsx` (line 239)

## Testing the Fix

1. **Restart the frontend** (dev.py will auto-reload, or restart manually):
   ```bash
   python dev.py
   ```

2. **Upload the same CSV twice**:
   - First upload: Should process normally
   - Second upload: Should show **"Duplicate Request Detected"** warning with options to:
     - View Existing Results
     - Proceed Anyway (force new run)

3. **Expected backend logs** (second upload):
   ```
   [API] Found existing session <session-id> with status: completed
   [API] Returning existing session (idempotency key matched)
   ```

## Verification Test
Run the idempotency test script to verify database-level functionality:
```bash
.venv/bin/python test_idempotency_directly.py
```

Should output:
```
✅ DUPLICATE DETECTED! Found existing session: <id>
```

## Additional Notes
- The idempotency system itself was working correctly at the database level
- Migration 006 was properly applied
- The bug was purely in the frontend event handler binding
