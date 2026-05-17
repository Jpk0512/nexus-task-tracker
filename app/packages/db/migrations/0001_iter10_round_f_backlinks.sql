-- Iter-10 Round F — Backlinks schema.
--
-- Adds the 5 relationship structures identified in
-- .memory/iter10/design/codex-review.md (amendments #2 + #5) and the
-- iter-7-deferred `projects.pinned` column.
--
-- Strictly ADDITIVE. No DROP / RENAME. Re-runnable via IF NOT EXISTS guards.
-- task↔knowledge and task↔document join tables already exist
-- (`knowledge_notes_on_tasks`, `documents_on_tasks`); we reuse them rather
-- than create parallel `task_knowledge_links` tables. The brief permits this
-- name shift since the cardinality + semantics are identical and adding a
-- second join would violate the Senior-Dev override on duplicated state.

BEGIN;

-- ── 1. prompts.project_id FK (codex amendment #2) ──────────────────────────
-- Pick FK on prompts rather than JSONB array on projects: indexable, no
-- migration on add/remove, and reverse lookup is a single index probe.
ALTER TABLE prompts
  ADD COLUMN IF NOT EXISTS project_id text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'prompts_project_id_fkey'
  ) THEN
    ALTER TABLE prompts
      ADD CONSTRAINT prompts_project_id_fkey
      FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL;
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS prompts_project_id_index ON prompts (project_id);

-- ── 2. milestones.owner_agent_id FK ────────────────────────────────────────
ALTER TABLE milestones
  ADD COLUMN IF NOT EXISTS owner_agent_id text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'milestones_owner_agent_id_fkey'
  ) THEN
    ALTER TABLE milestones
      ADD CONSTRAINT milestones_owner_agent_id_fkey
      FOREIGN KEY (owner_agent_id) REFERENCES agents(id) ON DELETE SET NULL;
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS milestones_owner_agent_id_index ON milestones (owner_agent_id);

-- ── 3. task_skills join table ──────────────────────────────────────────────
-- Skills live in `library_entries` with kind='skill'. We constrain the FK
-- target to `library_entries` (the kind filter applies at write time in the
-- tRPC procedure) rather than introducing a separate `library_skills` table.
CREATE TABLE IF NOT EXISTS task_skills (
  task_id    text NOT NULL,
  skill_id   text NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT task_skills_pkey PRIMARY KEY (task_id, skill_id),
  CONSTRAINT task_skills_task_id_fkey
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
  CONSTRAINT task_skills_skill_id_fkey
    FOREIGN KEY (skill_id) REFERENCES library_entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS task_skills_task_id_index ON task_skills (task_id);
CREATE INDEX IF NOT EXISTS task_skills_skill_id_index ON task_skills (skill_id);

-- ── 4. document_subscriptions join table ───────────────────────────────────
-- Per-user follow on a document; emits notifications on edit when the
-- notifications package is wired up. Cascades on user/doc deletion.
CREATE TABLE IF NOT EXISTS document_subscriptions (
  user_id       text NOT NULL,
  document_id   text NOT NULL,
  subscribed_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT document_subscriptions_pkey PRIMARY KEY (user_id, document_id),
  CONSTRAINT document_subscriptions_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE,
  CONSTRAINT document_subscriptions_document_id_fkey
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS document_subscriptions_document_id_index
  ON document_subscriptions (document_id);

-- ── 5. projects.pinned (iter-7 deferred) ───────────────────────────────────
-- Replaces the localStorage-only `nexus.projects.pinned` set with a server
-- column. The localStorage hook remains as a fallback when offline.
ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS pinned boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS projects_pinned_index
  ON projects (team_id, pinned) WHERE pinned = true;

COMMIT;
