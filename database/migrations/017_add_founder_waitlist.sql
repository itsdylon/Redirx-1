-- 017_add_founder_waitlist.sql
-- Adds founder_waitlist table for "Request Invite" flow.
-- Visitors without an invite code can submit their info to request access.
-- Admins review, approve (generating an invite code), or reject requests.

CREATE TABLE IF NOT EXISTS founder_waitlist (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  company TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  admin_notes TEXT,
  approved_by_user_id UUID REFERENCES auth.users(id),
  generated_invite_id UUID REFERENCES trial_invites(id),
  ip_address TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prevent duplicate pending requests from the same email
CREATE UNIQUE INDEX IF NOT EXISTS idx_founder_waitlist_email_pending
  ON founder_waitlist (email) WHERE status = 'pending';

-- Index for admin listing by status
CREATE INDEX IF NOT EXISTS idx_founder_waitlist_status
  ON founder_waitlist (status, created_at DESC);

-- Enable RLS
ALTER TABLE founder_waitlist ENABLE ROW LEVEL SECURITY;

-- Service role has full access (backend uses service_role key)
CREATE POLICY "Service role full access on founder_waitlist"
  ON founder_waitlist
  FOR ALL
  USING (true)
  WITH CHECK (true);
