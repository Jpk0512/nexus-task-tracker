-- Drop credit tables (FK to teams, must drop before altering teams)
DROP TABLE IF EXISTS "credit_ledger";
DROP TABLE IF EXISTS "credit_balance";

-- Drop billing enums
DROP TYPE IF EXISTS "credit_movement_type";
DROP TYPE IF EXISTS "plans";

-- Remove billing columns from teams
ALTER TABLE "teams" DROP COLUMN IF EXISTS "plan";
ALTER TABLE "teams" DROP COLUMN IF EXISTS "subscription_id";
ALTER TABLE "teams" DROP COLUMN IF EXISTS "customer_id";
ALTER TABLE "teams" DROP COLUMN IF EXISTS "canceled_at";
