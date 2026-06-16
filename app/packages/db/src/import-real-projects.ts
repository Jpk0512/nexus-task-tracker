// Import the user's two real coding projects (ai-interaction-dash +
// elevenlabs-eval-dash) into Nexus: tasks parsed from their TASKS.md /
// plan.md / user-stories.md, and docs lifted from their on-disk markdown
// files (mermaid in those docs renders automatically via the Tiptap node).
//
// Usage from host, after the standard seed has set up team/user/statuses:
//
//   docker run --rm --network supabase_default \
//     -e DATABASE_URL=postgresql://postgres:your-super-secret-and-long-postgres-password@db:5432/postgres \
//     -e NEXUS_LOCAL_DEV=1 \
//     -w /app/packages/db \
//     -v /Users/john.keeney/nexus-task-tracker/app/packages/db/src/schema.ts:/app/packages/db/src/schema.ts:ro \
//     -v /Users/john.keeney/nexus-task-tracker/app/packages/db/src/import-real-projects.ts:/app/packages/db/src/import-real-projects.ts:ro \
//     -v /Users/john.keeney/ai-interaction-dash:/sources/aid:ro \
//     -v /Users/john.keeney/elevenlabs-eval-dash:/sources/eed:ro \
//     app-api \
//     /usr/local/bin/bun run src/import-real-projects.ts

import { existsSync, readFileSync } from "node:fs";
import { and, eq } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";
import { documents, projects, statuses, tasks } from "./schema";

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required");

const pool = new Pool({ connectionString: databaseUrl });
const db = drizzle(pool);

const TEAM_ID = "local-dev-team";
const USER_ID = "local-dev-user";

const PROJECT_AI = "ld-project-ai-interaction-dash";
const PROJECT_EL = "ld-project-elevenlabs-eval-dash";

const STATUS = {
	backlog: "ld-status-backlog",
	planning: "ld-status-planning",
	building: "ld-status-building",
	review: "ld-status-review",
	shipped: "ld-status-shipped",
} as const;

// Map heterogeneous status words from upstream task lists onto our 5 statuses.
function mapStatus(raw: string): string {
	const v = raw.toLowerCase().trim();
	if (["done", "complete", "completed", "shipped", "merged"].includes(v))
		return STATUS.shipped;
	if (["review", "in review", "qa"].includes(v)) return STATUS.review;
	if (
		[
			"in_progress",
			"in progress",
			"wip",
			"doing",
			"building",
			"active",
		].includes(v)
	)
		return STATUS.building;
	if (
		["todo", "to do", "to_do", "planning", "planned", "next", "ready"].includes(
			v,
		)
	)
		return STATUS.planning;
	if (
		[
			"cancelled",
			"canceled",
			"deferred",
			"blocked",
			"later",
			"backlog",
		].includes(v)
	)
		return STATUS.backlog;
	return STATUS.planning;
}

function readIf(path: string): string | null {
	try {
		if (!existsSync(path)) return null;
		return readFileSync(path, "utf8");
	} catch {
		return null;
	}
}

// ── Parsers ────────────────────────────────────────────────────────────────

// ai-interaction-dash style: `| **TASK-NNN** — title | status | owner | date |`
function parseTasksMd(content: string): Array<{
	permalinkId: string;
	title: string;
	status: string;
	owner?: string;
}> {
	const out: Array<{
		permalinkId: string;
		title: string;
		status: string;
		owner?: string;
	}> = [];
	const row =
		/^\|\s*\*\*([A-Z]+-\d+)\*\*\s*—\s*(.+?)\s*\|\s*([a-zA-Z_ ]+?)\s*\|\s*([^|]*?)\s*\|/gm;
	let m: RegExpExecArray | null;
	// biome-ignore lint/suspicious/noAssignInExpressions: standard regex loop
	while ((m = row.exec(content)) !== null) {
		const [, permalinkId, title, status, owner] = m;
		out.push({
			permalinkId,
			title: title.trim(),
			status,
			owner: owner.trim() || undefined,
		});
	}
	return out;
}

// elevenlabs-eval-dash plan.md style: `### T-N.M: Title` (status inferred from heading position; treat all as planning if not marked done elsewhere).
function parsePlanMd(content: string): Array<{
	permalinkId: string;
	title: string;
	status: string;
}> {
	const out: Array<{ permalinkId: string; title: string; status: string }> = [];
	const row = /^###\s+(T-[\d.]+):\s*(.+?)$/gm;
	let m: RegExpExecArray | null;
	// biome-ignore lint/suspicious/noAssignInExpressions: standard regex loop
	while ((m = row.exec(content)) !== null) {
		const [, permalinkId, title] = m;
		out.push({ permalinkId, title: title.trim(), status: "todo" });
	}
	return out;
}

// ── Source manifests ───────────────────────────────────────────────────────

const SOURCES = {
	[PROJECT_AI]: {
		root: "/sources/aid",
		docs: [
			"README.md",
			"CLAUDE.md",
			"docs/PRD.md",
			"docs/ARCHITECTURE.md",
			"docs/CONSTITUTION.md",
			"docs/TASKS.md",
			"docs/AUDIT-2026-05-13.md",
			"docs/drift-report.md",
			"docs/cascade-router.md",
			"design/spec.md",
			"design/design.md",
			"design/HANDOFF_CHECKLIST.md",
		],
		tasksFrom: { path: "docs/TASKS.md", parser: parseTasksMd },
	},
	[PROJECT_EL]: {
		root: "/sources/eed",
		docs: [
			"README.md",
			"CLAUDE.md",
			"CONTEXT.md",
			"docs/PRD.md",
			"docs/ARCHITECTURE.md",
			"docs/USER_GUIDE.md",
			"docs/plan.md",
			"docs/user-stories.md",
			"docs/handoff.md",
			"docs/tech-spec.md",
			"docs/design/DESIGN.md",
			"docs/design/TOKENS.md",
			"docs/adr/0001-routing-trace-from-api.md",
			"docs/adr/0002-environment-is-agent-not-branch.md",
			"docs/adr/0003-test-storage-mixed-by-type.md",
			"docs/adr/0004-single-repo-agent-configs.md",
			"docs/adr/0005-bge-m3-via-lm-studio.md",
			"docs/adr/0006-pg-boss-job-queue.md",
		],
		tasksFrom: { path: "docs/plan.md", parser: parsePlanMd },
	},
} as const;

function iconForName(name: string): string {
	const n = name.toLowerCase();
	if (n.includes("readme")) return "📘";
	if (n.includes("claude")) return "🤖";
	if (n.includes("context")) return "📚";
	if (n.includes("prd")) return "📋";
	if (n.includes("architecture")) return "🏗";
	if (n.includes("constitution")) return "📜";
	if (n.includes("tasks") || n.includes("plan")) return "✅";
	if (n.includes("user-stories") || n.includes("user_guide")) return "👥";
	if (n.includes("handoff")) return "🤝";
	if (n.includes("audit") || n.includes("report")) return "🔎";
	if (n.includes("design") || n.includes("tokens")) return "🎨";
	if (n.includes("adr")) return "📌";
	return "📄";
}

function slugify(s: string): string {
	return s
		.replace(/^\/+/, "")
		.replace(/\.md$/i, "")
		.replace(/[\\/]+/g, "-")
		.replace(/[^a-zA-Z0-9-]/g, "-")
		.toLowerCase()
		.slice(0, 60);
}

// ── Main ───────────────────────────────────────────────────────────────────

async function main() {
	console.log("[import] wiping previously-imported docs + tasks…");
	// Keep the seed user/team/statuses; only clear what this importer owns.
	await db.delete(tasks).where(eq(tasks.teamId, TEAM_ID));
	await db.delete(documents).where(eq(documents.teamId, TEAM_ID));

	for (const [projectId, src] of Object.entries(SOURCES)) {
		console.log(`[import] project ${projectId}`);

		// Make sure the project row exists (the standard seed creates these,
		// but be defensive in case this is run standalone).
		const projectRow = (
			await db
				.select()
				.from(projects)
				.where(and(eq(projects.id, projectId), eq(projects.teamId, TEAM_ID)))
				.limit(1)
		)[0];
		if (!projectRow) {
			console.log(
				`  ! project ${projectId} not found — run seed-local-dev first`,
			);
			continue;
		}

		// Docs.
		let docOrder = 0;
		for (const rel of src.docs) {
			const abs = `${src.root}/${rel}`;
			const content = readIf(abs);
			if (!content) {
				console.log(`    – skip (missing): ${rel}`);
				continue;
			}
			const name = rel.replace(/\.md$/i, "");
			const id = `ld-doc-${projectId.replace(/^ld-project-/, "")}-${slugify(rel)}`;
			await db
				.insert(documents)
				.values({
					id,
					name,
					icon: iconForName(rel),
					content,
					teamId: TEAM_ID,
					projectId,
					parentId: null,
					order: docOrder++,
					createdBy: USER_ID,
					updatedBy: USER_ID,
				} as any)
				.onConflictDoNothing({ target: documents.id });
			console.log(`    ✓ doc: ${rel}`);
		}

		// Tasks.
		const tasksContent = readIf(`${src.root}/${src.tasksFrom.path}`);
		if (!tasksContent) {
			console.log(`    – no task source: ${src.tasksFrom.path}`);
			continue;
		}
		const parsed = src.tasksFrom.parser(tasksContent);
		console.log(
			`    • ${parsed.length} tasks parsed from ${src.tasksFrom.path}`,
		);

		let i = 0;
		for (const t of parsed) {
			i++;
			const statusId = mapStatus(t.status);
			const id = `ld-task-${projectId.replace(/^ld-project-/, "")}-${slugify(
				t.permalinkId,
			)}`;
			const permalinkId = `${projectId === PROJECT_AI ? "AI" : "EL"}-${String(i).padStart(3, "0")}`;
			await db
				.insert(tasks)
				.values({
					id,
					permalinkId,
					title: t.title,
					sequence: i,
					priority: "medium",
					teamId: TEAM_ID,
					order: i * 1000,
					statusId,
					projectId,
					createdBy: USER_ID,
					assigneeId: USER_ID,
					score: 1,
					subscribers: [USER_ID],
					mentions: [],
					isTemplate: false,
				} as any)
				.onConflictDoNothing({ target: tasks.id });
		}
		console.log(`    ✓ tasks: ${i} inserted`);
	}

	console.log("[import] done.");
	await pool.end();
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
