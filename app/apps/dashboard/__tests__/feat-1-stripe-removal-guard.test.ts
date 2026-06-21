/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-006 (P5) guard: Delete billing/Stripe subsystem + all call sites.
 *
 * RED stubs — fail before implementation lands; go GREEN automatically once
 * Forge removes the billing package, stripe calls, and generates the
 * Drizzle migration.
 *
 * Acceptance criteria tested:
 *   1. app/packages/billing/ directory is DELETED
 *   2. stripe npm dep removed from all workspace package.json (api + billing)
 *   3. payments.ts + stripe webhook + billing router + plan-feature imports DELETED
 *   4. billingRouter removed from routers/index.ts
 *   5. All stripeClient/billing calls removed from teams router + job types
 *   6. Gating replaced with open access (no @nexus-app/billing imports anywhere in app/)
 *   7. Drizzle migration exists: drops credit_ledger + credit_balance + billing columns
 *   8. Guard: grep -rn stripeClient | @nexus-app/billing | new Stripe across app/ = 0 production refs
 *
 * Excluded from scans:
 *   - node_modules, .next, dist, build, .turbo, .pre-nexus* dirs
 *   - bun.lock / bun.lockb / yarn.lock
 *   - This file itself
 *   - __tests__ directory (test files may reference these symbols as patterns)
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Root paths
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app
const APP_ROOT = join(
	import.meta.dirname ?? __dirname,
	"..", // dashboard
	"..", // apps
	"..", // app
);

const MONOREPO_ROOT = join(APP_ROOT, "..");

/** Absolute path of this test file — always excluded from scans */
const THIS_FILE = join(
	import.meta.dirname ?? __dirname,
	"feat-1-stripe-removal-guard.test.ts",
);

// ---------------------------------------------------------------------------
// Filesystem walk utility
// ---------------------------------------------------------------------------

const EXCLUDED_DIRS = new Set([
	"node_modules",
	".next",
	"dist",
	"build",
	".turbo",
	"__tests__",
]);

function isExcludedDir(name: string): boolean {
	return EXCLUDED_DIRS.has(name) || name.startsWith(".pre-nexus");
}

const EXCLUDED_FILE_NAMES = new Set(["bun.lock", "bun.lockb", "yarn.lock"]);

function* walkText(dir: string): Generator<string> {
	let entries: import("node:fs").Dirent<string>[];
	try {
		entries = readdirSync(dir, { withFileTypes: true, encoding: "utf8" });
	} catch {
		return;
	}

	for (const entry of entries) {
		if (entry.isDirectory()) {
			if (isExcludedDir(entry.name)) continue;
			yield* walkText(join(dir, entry.name));
		} else if (entry.isFile()) {
			if (EXCLUDED_FILE_NAMES.has(entry.name)) continue;
			const filePath = join(dir, entry.name);
			if (filePath === THIS_FILE) continue;
			yield filePath;
		}
	}
}

function scanForPattern(
	rootDir: string,
	pattern: RegExp,
): Array<{ file: string; lines: number[] }> {
	const hits: Array<{ file: string; lines: number[] }> = [];

	for (const filePath of walkText(rootDir)) {
		let content: string;
		try {
			content = readFileSync(filePath, "utf8");
		} catch {
			continue;
		}

		if (!pattern.test(content)) continue;

		const matchedLines: number[] = [];
		content.split("\n").forEach((line, idx) => {
			if (pattern.test(line)) matchedLines.push(idx + 1);
		});

		if (matchedLines.length > 0) {
			hits.push({ file: filePath, lines: matchedLines });
		}
	}

	return hits;
}

function formatHits(hits: Array<{ file: string; lines: number[] }>): string {
	return hits
		.map(
			({ file, lines }) =>
				`  ${relative(APP_ROOT, file)} (line${lines.length > 1 ? "s" : ""} ${lines.join(", ")})`,
		)
		.join("\n");
}

// ---------------------------------------------------------------------------
// 1. billing package directory must not exist
// ---------------------------------------------------------------------------

describe("TASK-006 Stripe removal guard", () => {
	test("app/packages/billing/ directory is deleted", () => {
		const billingPkgPath = join(APP_ROOT, "packages", "billing");
		const exists = existsSync(billingPkgPath);
		expect(
			exists,
			`app/packages/billing/ still exists at ${billingPkgPath} — delete it`,
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 2. stripe npm dep removed from all workspace package.json files
	// ---------------------------------------------------------------------------

	test("stripe npm dependency removed from app/apps/api/package.json", () => {
		const pkgPath = join(APP_ROOT, "apps", "api", "package.json");
		const content = readFileSync(pkgPath, "utf8");
		const pkg = JSON.parse(content) as {
			dependencies?: Record<string, string>;
			devDependencies?: Record<string, string>;
		};
		const hasDep = Boolean(
			pkg.dependencies?.["stripe"] ?? pkg.devDependencies?.["stripe"],
		);
		expect(
			hasDep,
			`stripe still listed in ${relative(APP_ROOT, pkgPath)} — remove it`,
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 3. payments.ts lib file deleted from api/src/lib/
	// ---------------------------------------------------------------------------

	test("app/apps/api/src/lib/payments.ts is deleted", () => {
		const paymentsPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"lib",
			"payments.ts",
		);
		const exists = existsSync(paymentsPath);
		expect(
			exists,
			"app/apps/api/src/lib/payments.ts still exists — delete it",
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 4. stripe webhook handler deleted
	// ---------------------------------------------------------------------------

	test("app/apps/api/src/rest/webhooks/stripe.ts is deleted", () => {
		const webhookPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"rest",
			"webhooks",
			"stripe.ts",
		);
		const exists = existsSync(webhookPath);
		expect(
			exists,
			"app/apps/api/src/rest/webhooks/stripe.ts still exists — delete it",
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 5. billing router file deleted
	// ---------------------------------------------------------------------------

	test("app/apps/api/src/trpc/routers/billing.ts is deleted", () => {
		const billingRouterPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"trpc",
			"routers",
			"billing.ts",
		);
		const exists = existsSync(billingRouterPath);
		expect(
			exists,
			"app/apps/api/src/trpc/routers/billing.ts still exists — delete it",
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 6. billingRouter removed from routers/index.ts
	// ---------------------------------------------------------------------------

	test("billingRouter not imported/used in app/apps/api/src/trpc/routers/index.ts", () => {
		const indexPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"trpc",
			"routers",
			"index.ts",
		);
		const content = readFileSync(indexPath, "utf8");
		const hasBillingRef = /billing/i.test(content);
		expect(
			hasBillingRef,
			`billing still referenced in ${relative(APP_ROOT, indexPath)} — remove billingRouter import and usage`,
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 7. stripe webhook removed from webhooks index
	// ---------------------------------------------------------------------------

	test("stripeWebhook not referenced in app/apps/api/src/rest/webhooks/index.ts", () => {
		const indexPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"rest",
			"webhooks",
			"index.ts",
		);
		const content = readFileSync(indexPath, "utf8");
		const hasStripeRef = /stripe/i.test(content);
		expect(
			hasStripeRef,
			`stripe still referenced in ${relative(APP_ROOT, indexPath)} — remove stripeWebhook import and route`,
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 8. @nexus-app/billing removed from integration package.json
	// ---------------------------------------------------------------------------

	test("@nexus-app/billing dependency removed from app/packages/integration/package.json", () => {
		const pkgPath = join(APP_ROOT, "packages", "integration", "package.json");
		const content = readFileSync(pkgPath, "utf8");
		const pkg = JSON.parse(content) as {
			dependencies?: Record<string, string>;
			devDependencies?: Record<string, string>;
		};
		const hasDep = Boolean(
			pkg.dependencies?.["@nexus-app/billing"] ??
				pkg.devDependencies?.["@nexus-app/billing"],
		);
		expect(
			hasDep,
			`@nexus-app/billing still listed in ${relative(APP_ROOT, pkgPath)} — remove it`,
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 9. Guard: zero stripeClient | @nexus-app/billing | new Stripe production refs in app/
	//    (mirrors the acceptance criterion: grep -rn returns 0)
	// ---------------------------------------------------------------------------

	test("zero stripeClient references across app/ production source", () => {
		const STRIPE_CLIENT_RE = /\bstripeClient\b/;
		const hits = scanForPattern(APP_ROOT, STRIPE_CLIENT_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} file(s) with stripeClient references:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});

	test("zero @nexus-app/billing import references across app/ production source", () => {
		const BILLING_IMPORT_RE = /@mimir\/billing/;
		const hits = scanForPattern(APP_ROOT, BILLING_IMPORT_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} file(s) with @nexus-app/billing references:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});

	test("zero `new Stripe(` constructor calls across app/ production source", () => {
		const NEW_STRIPE_RE = /new Stripe\s*\(/;
		const hits = scanForPattern(APP_ROOT, NEW_STRIPE_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} file(s) with new Stripe( references:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});

	// ---------------------------------------------------------------------------
	// 10. teams.ts: no stripeClient calls remain (all 3 unconditional call sites gone)
	// ---------------------------------------------------------------------------

	test("app/apps/api/src/trpc/routers/teams.ts has no stripeClient calls", () => {
		const teamsPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"trpc",
			"routers",
			"teams.ts",
		);
		const content = readFileSync(teamsPath, "utf8");
		const hasStripe = /\bstripeClient\b/.test(content);
		expect(
			hasStripe,
			"teams.ts still contains stripeClient calls — remove all 3 unconditional call sites (create.70, getCurrent.100, update.142) and delete.subscriptionId block",
		).toBe(false);
	});

	test("app/apps/api/src/trpc/routers/teams.ts has no @nexus-app/billing imports", () => {
		const teamsPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"trpc",
			"routers",
			"teams.ts",
		);
		const content = readFileSync(teamsPath, "utf8");
		const hasBilling = /@mimir\/billing/.test(content);
		expect(
			hasBilling,
			"teams.ts still imports from @nexus-app/billing — remove checkLimit, createTrialSubscription, updateSubscriptionUsage imports and all call sites",
		).toBe(false);
	});

	test("agent job files have no @nexus-app/billing imports", () => {
		const agentJobsDir = join(
			APP_ROOT,
			"..",
			"app",
			"packages",
			"jobs",
			"src",
			"jobs",
			"agent-jobs",
		);
		// Scan just the agent-jobs dir (contains the 2 job types with billing refs)
		const BILLING_RE = /@mimir\/billing/;
		const hits = scanForPattern(agentJobsDir, BILLING_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} agent job file(s) with @nexus-app/billing references:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});

	// ---------------------------------------------------------------------------
	// 11. Drizzle migration for billing schema removal must exist
	//     Must contain DROP TABLE for credit_ledger and credit_balance,
	//     and ALTER TABLE teams dropping billing columns.
	// ---------------------------------------------------------------------------

	test("a Drizzle migration exists that drops credit_ledger table", () => {
		const migrationsDir = join(APP_ROOT, "packages", "db", "migrations");
		let entries: import("node:fs").Dirent<string>[];
		try {
			entries = readdirSync(migrationsDir, {
				withFileTypes: true,
				encoding: "utf8",
			});
		} catch {
			entries = [];
		}

		const sqlFiles = entries
			.filter((e) => e.isFile() && e.name.endsWith(".sql"))
			.map((e) => join(migrationsDir, e.name));

		const foundDropCreditLedger = sqlFiles.some((filePath) => {
			try {
				const content = readFileSync(filePath, "utf8");
				return /DROP TABLE.*credit_ledger/i.test(content);
			} catch {
				return false;
			}
		});

		expect(
			foundDropCreditLedger,
			"No migration found that drops the credit_ledger table. Run: bun drizzle-kit generate and include DROP TABLE credit_ledger in a new migration under app/packages/db/migrations/",
		).toBe(true);
	});

	test("a Drizzle migration exists that drops credit_balance table", () => {
		const migrationsDir = join(APP_ROOT, "packages", "db", "migrations");
		let entries: import("node:fs").Dirent<string>[];
		try {
			entries = readdirSync(migrationsDir, {
				withFileTypes: true,
				encoding: "utf8",
			});
		} catch {
			entries = [];
		}

		const sqlFiles = entries
			.filter((e) => e.isFile() && e.name.endsWith(".sql"))
			.map((e) => join(migrationsDir, e.name));

		const foundDropCreditBalance = sqlFiles.some((filePath) => {
			try {
				const content = readFileSync(filePath, "utf8");
				return /DROP TABLE.*credit_balance/i.test(content);
			} catch {
				return false;
			}
		});

		expect(
			foundDropCreditBalance,
			"No migration found that drops the credit_balance table. Run: bun drizzle-kit generate and include DROP TABLE credit_balance in a new migration under app/packages/db/migrations/",
		).toBe(true);
	});

	test("a Drizzle migration exists that removes billing columns from teams", () => {
		const migrationsDir = join(APP_ROOT, "packages", "db", "migrations");
		let entries: import("node:fs").Dirent<string>[];
		try {
			entries = readdirSync(migrationsDir, {
				withFileTypes: true,
				encoding: "utf8",
			});
		} catch {
			entries = [];
		}

		const sqlFiles = entries
			.filter((e) => e.isFile() && e.name.endsWith(".sql"))
			.map((e) => join(migrationsDir, e.name));

		// Must drop at least one of: plan, customer_id, subscription_id, canceled_at
		const BILLING_COL_RE =
			/ALTER TABLE.*teams.*DROP COLUMN.*(plan|customer_id|subscription_id|canceled_at)/is;

		const foundBillingColDrop = sqlFiles.some((filePath) => {
			try {
				const content = readFileSync(filePath, "utf8");
				return BILLING_COL_RE.test(content);
			} catch {
				return false;
			}
		});

		expect(
			foundBillingColDrop,
			"No migration found that removes billing columns (plan, customer_id, subscription_id, canceled_at) from teams table. Generate migration after updating app/packages/db/src/schema.ts",
		).toBe(true);
	});

	// ---------------------------------------------------------------------------
	// 12. schema.ts: billing-related table exports must be removed
	// ---------------------------------------------------------------------------

	test("app/packages/db/src/schema.ts no longer exports creditBalance table", () => {
		const schemaPath = join(APP_ROOT, "packages", "db", "src", "schema.ts");
		const content = readFileSync(schemaPath, "utf8");
		// After deletion: neither export declaration nor pgTable("credit_balance") should remain
		const hasCreditBalance =
			/export const creditBalance\b/.test(content) ||
			/pgTable\s*\(\s*["']credit_balance["']/.test(content);
		expect(
			hasCreditBalance,
			"schema.ts still exports creditBalance table — remove creditBalance pgTable definition",
		).toBe(false);
	});

	test("app/packages/db/src/schema.ts no longer exports creditLedger table", () => {
		const schemaPath = join(APP_ROOT, "packages", "db", "src", "schema.ts");
		const content = readFileSync(schemaPath, "utf8");
		const hasCreditLedger =
			/export const creditLedger\b/.test(content) ||
			/pgTable\s*\(\s*["']credit_ledger["']/.test(content);
		expect(
			hasCreditLedger,
			"schema.ts still exports creditLedger table — remove creditLedger pgTable definition",
		).toBe(false);
	});

	test("app/packages/db/src/schema.ts no longer defines billing columns on teams", () => {
		const schemaPath = join(APP_ROOT, "packages", "db", "src", "schema.ts");
		const content = readFileSync(schemaPath, "utf8");
		// Check that none of the billing columns remain in the teams table
		const hasCustomerId = /customerId.*customer_id/.test(content);
		const hasSubscriptionId = /subscriptionId.*subscription_id/.test(content);
		const hasCanceledAt = /canceledAt.*canceled_at/.test(content);
		const hasPlanEnum = /plansEnum\s*\(\s*["']plan["']/.test(content);

		const remaining = [
			hasCustomerId && "customerId",
			hasSubscriptionId && "subscriptionId",
			hasCanceledAt && "canceledAt",
			hasPlanEnum && "plansEnum(plan)",
		]
			.filter(Boolean)
			.join(", ");

		expect(
			remaining,
			`schema.ts teams table still has billing columns: ${remaining} — remove them`,
		).toBe("");
	});
});
