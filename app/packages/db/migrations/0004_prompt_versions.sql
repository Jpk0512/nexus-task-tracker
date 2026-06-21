-- M-004: prompt_versions table for versioned prompt history.
--
-- ADDITIVE only. Re-runnable via IF NOT EXISTS guards.
-- Creates the table that prompts.ts snapshots into on bumpVersion=true.
-- The UNIQUE(prompt_id, version) constraint is the conflict target for
-- onConflictDoNothing and enforces append-only history invariant.

BEGIN;

CREATE TABLE IF NOT EXISTS prompt_versions (
  id          TEXT        PRIMARY KEY,
  prompt_id   TEXT        NOT NULL
                            REFERENCES prompts(id) ON DELETE CASCADE,
  version     INTEGER     NOT NULL,
  content     TEXT        NOT NULL,
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by  TEXT
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'prompt_versions_prompt_id_version_unique'
  ) THEN
    ALTER TABLE prompt_versions
      ADD CONSTRAINT prompt_versions_prompt_id_version_unique
      UNIQUE (prompt_id, version);
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt_id ON prompt_versions (prompt_id);

COMMIT;
