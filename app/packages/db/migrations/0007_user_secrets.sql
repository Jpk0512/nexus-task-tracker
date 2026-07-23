-- FEAT-018: server-backed encrypted secret vault.
-- ADDITIVE only. Idempotent guards match repo convention (see 0005/0006) since
-- meta/_journal.json's tracked snapshot predates those hand-applied migrations.

CREATE TABLE IF NOT EXISTS "user_secrets" (
	"id" text PRIMARY KEY NOT NULL,
	"user_id" text NOT NULL,
	"team_id" text,
	"kind" text NOT NULL,
	"name" text NOT NULL,
	"encrypted_value" text NOT NULL,
	"notes" text,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now()
);

DO $$ BEGIN
  ALTER TABLE "user_secrets"
    ADD CONSTRAINT "unique_user_secret_name" UNIQUE ("user_id", "name");
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
  ALTER TABLE "user_secrets" ADD CONSTRAINT "user_secrets_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "user"("id") ON DELETE cascade;
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
  ALTER TABLE "user_secrets" ADD CONSTRAINT "user_secrets_team_id_fkey"
    FOREIGN KEY ("team_id") REFERENCES "teams"("id") ON DELETE set null;
EXCEPTION WHEN duplicate_object THEN null;
END $$;
