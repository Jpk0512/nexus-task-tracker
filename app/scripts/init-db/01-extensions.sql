-- Mimrai local-dev DB init. Runs once on first postgres start.
CREATE EXTENSION IF NOT EXISTS vector;
-- unaccent is used by tasks.fts / documents.fts search via
-- websearch_to_tsquery('english', unaccent(...)). Without it,
-- trpc.tasks.get and trpc.documents.get throw on any text search.
CREATE EXTENSION IF NOT EXISTS unaccent;
