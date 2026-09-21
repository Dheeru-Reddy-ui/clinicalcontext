-- Runs once at first boot of the local dev database.
-- Supabase ships pgvector pre-installed; this keeps local parity.
CREATE EXTENSION IF NOT EXISTS vector;
