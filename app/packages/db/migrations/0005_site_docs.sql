-- Site Docs: disk paths on projects + Nexus-owned maps
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "root_path" text;
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "docs_path" text;

DO $$ BEGIN
  CREATE TYPE "public"."site_map_kind" AS ENUM('architecture', 'flow', 'graph');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS "site_maps" (
  "id" text PRIMARY KEY NOT NULL,
  "project_id" text NOT NULL,
  "team_id" text NOT NULL,
  "kind" "site_map_kind" NOT NULL,
  "title" text NOT NULL,
  "content" text DEFAULT '' NOT NULL,
  "stale" boolean DEFAULT false NOT NULL,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS "site_maps_project_id_index" ON "site_maps" USING btree ("project_id");
CREATE INDEX IF NOT EXISTS "site_maps_team_id_index" ON "site_maps" USING btree ("team_id");

DO $$ BEGIN
  ALTER TABLE "site_maps" ADD CONSTRAINT "site_maps_project_id_fkey"
    FOREIGN KEY ("project_id") REFERENCES "projects"("id") ON DELETE cascade;
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
  ALTER TABLE "site_maps" ADD CONSTRAINT "site_maps_team_id_fkey"
    FOREIGN KEY ("team_id") REFERENCES "teams"("id") ON DELETE cascade;
EXCEPTION WHEN duplicate_object THEN null;
END $$;
