-- Agent Config: registered disk roots tagged by agent (Option B)

DO $$ BEGIN
  CREATE TYPE "public"."agent_config_agent" AS ENUM('claude', 'codex', 'cursor', 'pi', 'oh', 'custom');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS "agent_config_roots" (
  "id" text PRIMARY KEY NOT NULL,
  "team_id" text NOT NULL,
  "agent" "agent_config_agent" NOT NULL,
  "label" text NOT NULL,
  "path" text NOT NULL,
  "enabled" boolean DEFAULT true NOT NULL,
  "sort_order" integer DEFAULT 0 NOT NULL,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS "agent_config_roots_team_id_index" ON "agent_config_roots" USING btree ("team_id");
CREATE INDEX IF NOT EXISTS "agent_config_roots_agent_index" ON "agent_config_roots" USING btree ("agent");

DO $$ BEGIN
  ALTER TABLE "agent_config_roots" ADD CONSTRAINT "agent_config_roots_team_id_fkey"
    FOREIGN KEY ("team_id") REFERENCES "teams"("id") ON DELETE cascade;
EXCEPTION WHEN duplicate_object THEN null;
END $$;
