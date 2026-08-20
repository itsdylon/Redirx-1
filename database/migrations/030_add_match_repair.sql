-- 030: match-time repair of low-confidence matches
--
-- A quarter of production matches come back flagged for review (2,062 of
-- 8,344), and almost all of them are URL-only matches that never saw page
-- content. Escalating those to content matching is the paid Deep Match
-- upgrade, so the free path has to work from evidence already on hand: the
-- session's own high-confidence matches, which describe how the site renamed
-- things.
--
-- The proposal is stored ALONGSIDE the match rather than replacing it.
-- `new_url` stays exactly what the matcher produced, so nothing ships that a
-- human did not approve, the original is always visible for comparison, and a
-- bad repair is a suggestion to ignore rather than damage to undo. Approving
-- in the review UI is what promotes `repaired_url` into `new_url`.
--
-- Measured on the corpus: 40% of flagged rows get a proposal, rising to 51%
-- on the real e-commerce re-platform, where the matcher had been pairing a
-- compost pail with a double boiler.

ALTER TABLE url_mappings
  -- The published URL we would point this old URL at instead. NULL means we
  -- have no opinion, which is the honest and common answer.
  ADD COLUMN IF NOT EXISTS repaired_url TEXT,
  -- 'exact'   — the learned rule's target is published verbatim.
  -- 'section' — the rule located the section, name similarity picked the leaf.
  ADD COLUMN IF NOT EXISTS repair_method TEXT,
  -- Confidence in the PROPOSAL. Deliberately separate from confidence_score,
  -- which is the original match's confidence: they are two different claims
  -- and collapsing them into one number would misreport both.
  ADD COLUMN IF NOT EXISTS repair_confidence DOUBLE PRECISION,
  -- How many confident matches the rule was learned from. Structured so the
  -- UI can sort and filter on strength of evidence.
  ADD COLUMN IF NOT EXISTS repair_support INTEGER,
  -- One sentence a reviewer can judge without knowing how any of this works,
  -- e.g. "/case-studies/* → /success-stories/* — held on 41 of 41 similar
  -- matches". A suggestion nobody can check is one nobody should accept.
  ADD COLUMN IF NOT EXISTS repair_evidence TEXT;

COMMENT ON COLUMN url_mappings.repaired_url IS
  'Proposed better target from the session''s learned rename rules. Advisory: new_url is unchanged until a human approves.';
COMMENT ON COLUMN url_mappings.repair_confidence IS
  'Confidence in the proposal, not in the original match. See confidence_score for that.';

-- The review query: flagged rows that have a proposal, strongest first.
CREATE INDEX IF NOT EXISTS idx_url_mappings_repaired
  ON url_mappings (session_id, repair_confidence DESC)
  WHERE repaired_url IS NOT NULL;
