-- M-002: knowledge_links table + content_fts generated column on knowledge_notes.
--
-- ADDITIVE only. Re-runnable via IF NOT EXISTS / IF NOT EXISTS guards.
-- Applies FEAT-002 Wave 1 schema: wiki-link graph edge table + GIN-backed FTS.

BEGIN;

-- 1. Wiki-link graph edge table.
--    One row per [[...]] occurrence in a source note.
--    from_note_id CASCADE  — no edges without a source.
--    to_note_id   SET NULL — unresolved / post-delete edges survive as NULL (render red).
CREATE TABLE IF NOT EXISTS knowledge_links (
  id           TEXT        PRIMARY KEY,
  from_note_id TEXT        NOT NULL
                             REFERENCES knowledge_notes(id) ON DELETE CASCADE,
  to_note_id   TEXT
                             REFERENCES knowledge_notes(id) ON DELETE SET NULL,
  link_text    TEXT        NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_links_from_note_id ON knowledge_links (from_note_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_links_to_note_id   ON knowledge_links (to_note_id);

-- 2. FTS column on knowledge_notes.
--    STORED generated tsvector: name weighted A, content weighted B.
--    Idempotent: the column already existing is a no-op via DO block.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'knowledge_notes'
      AND column_name = 'content_fts'
  ) THEN
    ALTER TABLE knowledge_notes
      ADD COLUMN content_fts tsvector
      GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(content, '')), 'B')
      ) STORED;
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_knowledge_notes_content_fts
  ON knowledge_notes USING gin (content_fts);

COMMIT;
