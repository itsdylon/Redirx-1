-- Migration 009: Add pipeline_type column to migration_sessions
-- Supports 'content' (default, full 7-stage pipeline with embeddings)
-- and 'url_only' (4-stage pipeline, free tier, no API cost)

ALTER TABLE migration_sessions
ADD COLUMN IF NOT EXISTS pipeline_type TEXT NOT NULL DEFAULT 'content';

-- Add a comment for documentation
COMMENT ON COLUMN migration_sessions.pipeline_type IS 'Pipeline type: content (full with embeddings) or url_only (free tier, URL matching only)';
