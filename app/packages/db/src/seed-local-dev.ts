// Local-dev seed: a single user + team + membership, dev-workflow statuses,
// two seed coding projects with tasks + docs (one each containing a Mermaid
// architecture diagram). Idempotent — safe to re-run. Intended to be executed
// once after `drizzle-kit push`:
//
//   docker run --rm --network supabase_default \
//     -e DATABASE_URL=postgresql://postgres:your-super-secret-and-long-postgres-password@db:5432/postgres \
//     -e MIMRAI_LOCAL_DEV=1 \
//     -w /app/packages/db \
//     app-api \
//     /usr/local/bin/bun run src/seed-local-dev.ts

import { eq } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";
import {
	documents,
	labels,
	projects,
	statuses,
	tasks,
	teams,
	users,
	usersOnTeams,
} from "./schema";

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required");

const pool = new Pool({ connectionString: databaseUrl });
const db = drizzle(pool);

// ── Canonical IDs (must agree with apps/dashboard/src/lib/get-session.ts
//    and apps/api/src/lib/context.ts) ────────────────────────────────────────
const USER_ID = "local-dev-user";
const TEAM_ID = "local-dev-team";
const TEAM_SLUG = "local-dev";
const EMAIL = "dev@mimrai.local";

// Dev-workflow statuses — Linear-style names tuned for coding projects.
const STATUS_IDS = {
	backlog: "ld-status-backlog",
	planning: "ld-status-planning",
	building: "ld-status-building",
	review: "ld-status-review",
	shipped: "ld-status-shipped",
} as const;

// Two seed projects.
const PROJECT_AI = "ld-project-ai-interaction-dash";
const PROJECT_EL = "ld-project-elevenlabs-eval-dash";

const now = new Date();
const nowIso = now.toISOString();

// ── Helpers ────────────────────────────────────────────────────────────────

function permalink(prefix: string, n: number): string {
	return `${prefix}-${String(n).padStart(3, "0")}`;
}

// Mermaid diagram embedded as a markdown fenced block. The frontend renders
// these via the Mermaid Tiptap node added in apps/dashboard/src/components/editor.
function mermaidBlock(src: string): string {
	return "\n\n```mermaid\n" + src.trim() + "\n```\n\n";
}

const AI_DASH_DOC = [
	"# AI Interaction Dashboard",
	"",
	"Self-hosted leadership analytics dashboard for AI chat + voice interaction data. Pulls from Tableau VizQL Data Service, processes with Python + DuckDB, surfaces metrics through a Next.js 15 dashboard powered by Claude.",
	"",
	"## Stack",
	"",
	"- Next.js 15 (App Router) — dashboard",
	"- Python 3.12 + DuckDB — ingestion + analytics",
	"- Tableau VizQL Data Service — source of truth",
	"- Claude (Anthropic) — natural-language layer",
	"- Docker Compose — local dev orchestration",
	"",
	"## Architecture",
	mermaidBlock(`
flowchart LR
  T["Tableau VizQL Data Service"]
  I["Python ingestion (uv)"]
  D[("DuckDB")]
  A["Next.js dashboard"]
  C["Claude API"]
  T -->|VDS queries| I
  I -->|writes parquet| D
  D -->|reads| A
  A -->|prompts| C
  C -->|responses| A
`),
	"## Local dev",
	"",
	"`docker compose -f docker-compose.dev.yml up -d --build` then open http://localhost:5177.",
	"",
	"## Next milestones",
	"",
	"- Lock datapoint mapping (replace STUB_* LUIDs)",
	"- Wire ingestion → dashboard end-to-end",
	"- Ship the leadership-metrics view",
].join("\n");

const EL_STUDIO_DOC = [
	"# Voice Agent Studio (elevenlabs-eval-dash)",
	"",
	"Local TDD studio for ElevenLabs voice agent workflows. Plays back fixtures, runs evals, captures dashboards for PROD vs UAT agents.",
	"",
	"## Stack",
	"",
	"- Next.js dashboard + Hono API (TRPC)",
	"- Postgres + Drizzle",
	"- LM Studio (host) — local bge-m3 embeddings",
	"- ElevenLabs API — voice agents",
	"- MCP server (stdio) — host-side Claude integration",
	"",
	"## Flow",
	mermaidBlock(`
sequenceDiagram
  participant U as User
  participant A as API
  participant E as ElevenLabs
  participant P as Postgres
  participant L as LM Studio
  U->>A: trigger eval run
  A->>E: call voice agent
  E-->>A: transcript + audio
  A->>L: embed bge-m3
  L-->>A: vectors
  A->>P: persist run + scores
  A-->>U: dashboard update
`),
	"## Local dev",
	"",
	"`docker compose up -d`, then open http://localhost:3000.",
	"",
	"## Open work",
	"",
	"- Lock decisions in `docs/handoff.md`",
	"- Build the eval scoring rubric UI",
	"- MCP harness for live PROD agent",
].join("\n");

// ── Main ───────────────────────────────────────────────────────────────────

async function main() {
	console.log("[seed-local-dev] team…");
	await db
		.insert(teams)
		.values({
			id: TEAM_ID,
			name: "Local Dev",
			slug: TEAM_SLUG,
			prefix: "DEV",
			email: EMAIL,
			plan: "team",
			timezone: "UTC",
			locale: "en-US",
			createdAt: now,
			updatedAt: now,
		})
		.onConflictDoNothing({ target: teams.id });

	console.log("[seed-local-dev] user…");
	await db
		.insert(users)
		.values({
			id: USER_ID,
			name: "Local Dev",
			email: EMAIL,
			emailVerified: true,
			image: null,
			locale: "en-US",
			teamId: TEAM_ID,
			teamSlug: TEAM_SLUG,
			isMentionable: true,
			color: "#888888",
			isSystemUser: false,
			dateFormat: "MM/dd/yyyy",
			createdAt: now,
			updatedAt: now,
		})
		.onConflictDoNothing({ target: users.id });

	await db
		.update(users)
		.set({ teamId: TEAM_ID, teamSlug: TEAM_SLUG, updatedAt: now })
		.where(eq(users.id, USER_ID));

	console.log("[seed-local-dev] membership…");
	await db
		.insert(usersOnTeams)
		.values({ userId: USER_ID, teamId: TEAM_ID, role: "owner" })
		.onConflictDoNothing({
			target: [usersOnTeams.userId, usersOnTeams.teamId],
		});

	console.log("[seed-local-dev] statuses…");
	const statusRows = [
		{
			id: STATUS_IDS.backlog,
			name: "Backlog",
			order: 0,
			type: "backlog" as const,
			isFinalState: false,
		},
		{
			id: STATUS_IDS.planning,
			name: "Planning",
			order: 1,
			type: "to_do" as const,
			isFinalState: false,
		},
		{
			id: STATUS_IDS.building,
			name: "Building",
			order: 2,
			type: "in_progress" as const,
			isFinalState: false,
		},
		{
			id: STATUS_IDS.review,
			name: "Review",
			order: 3,
			type: "review" as const,
			isFinalState: false,
		},
		{
			id: STATUS_IDS.shipped,
			name: "Shipped",
			order: 4,
			type: "done" as const,
			isFinalState: true,
		},
	];
	for (const s of statusRows) {
		await db
			.insert(statuses)
			.values({
				id: s.id,
				name: s.name,
				teamId: TEAM_ID,
				order: s.order,
				type: s.type,
				isFinalState: s.isFinalState,
				projectIds: [],
				createdAt: nowIso as any,
				updatedAt: nowIso as any,
			})
			.onConflictDoNothing({ target: statuses.id });
	}

	console.log("[seed-local-dev] labels…");
	// Standard Linear-style workspace labels. Colors mirror Linear defaults.
	const labelRows = [
		{ id: "ld-label-bug", name: "bug", color: "#eb5757" },
		{ id: "ld-label-feature", name: "feature", color: "#5e6ad2" },
		{ id: "ld-label-improvement", name: "improvement", color: "#27a644" },
		{ id: "ld-label-research", name: "research", color: "#e9c46a" },
		{ id: "ld-label-design", name: "design", color: "#f59e0b" },
		{ id: "ld-label-infra", name: "infra", color: "#7a7fad" },
	];
	for (const l of labelRows) {
		await db
			.insert(labels)
			.values({
				id: l.id,
				name: l.name,
				color: l.color,
				teamId: TEAM_ID,
				createdAt: nowIso,
				updatedAt: nowIso,
			} as any)
			.onConflictDoNothing({ target: labels.id });
	}

	console.log("[seed-local-dev] projects…");
	await db
		.insert(projects)
		.values([
			{
				id: PROJECT_AI,
				name: "AI Interaction Dashboard",
				description:
					"Leadership analytics for AI chat + voice interactions. Tableau → DuckDB → Next.js + Claude.",
				color: "#7c3aed",
				prefix: "AI",
				archived: false,
				teamId: TEAM_ID,
				userId: USER_ID,
				leadId: USER_ID,
				visibility: "team",
				status: "in_progress",
				createdAt: nowIso as any,
				updatedAt: nowIso as any,
			},
			{
				id: PROJECT_EL,
				name: "Voice Agent Studio",
				description:
					"TDD studio for ElevenLabs voice agents. Eval runs, scoring, PROD vs UAT compare.",
				color: "#10b981",
				prefix: "EL",
				archived: false,
				teamId: TEAM_ID,
				userId: USER_ID,
				leadId: USER_ID,
				visibility: "team",
				status: "in_progress",
				createdAt: nowIso as any,
				updatedAt: nowIso as any,
			},
		])
		.onConflictDoNothing({ target: projects.id });
	// also force-update prefix in case projects already existed without it
	await db
		.update(projects)
		.set({ prefix: "AI" })
		.where(eq(projects.id, PROJECT_AI));
	await db
		.update(projects)
		.set({ prefix: "EL" })
		.where(eq(projects.id, PROJECT_EL));

	console.log("[seed-local-dev] tasks…");
	const taskRows = [
		// AI Interaction Dashboard
		{
			project: PROJECT_AI,
			prefix: "AI",
			seq: 1,
			title: "Lock datapoint mapping (replace STUB_* LUIDs)",
			status: STATUS_IDS.planning,
			priority: "high" as const,
			order: 1,
		},
		{
			project: PROJECT_AI,
			prefix: "AI",
			seq: 2,
			title: "Wire ingestion → dashboard end-to-end",
			status: STATUS_IDS.building,
			priority: "high" as const,
			order: 2,
		},
		{
			project: PROJECT_AI,
			prefix: "AI",
			seq: 3,
			title: "Ship leadership-metrics view",
			status: STATUS_IDS.building,
			priority: "medium" as const,
			order: 3,
		},
		{
			project: PROJECT_AI,
			prefix: "AI",
			seq: 4,
			title: "Refresh ingestion on a 15-min cadence",
			status: STATUS_IDS.backlog,
			priority: "medium" as const,
			order: 4,
		},
		{
			project: PROJECT_AI,
			prefix: "AI",
			seq: 5,
			title: "Claude prompt: hand off raw DuckDB summary as context",
			status: STATUS_IDS.review,
			priority: "low" as const,
			order: 5,
		},
		{
			project: PROJECT_AI,
			prefix: "AI",
			seq: 6,
			title: "Docker compose: tear-down doc + .env.example audit",
			status: STATUS_IDS.shipped,
			priority: "low" as const,
			order: 6,
		},
		// Voice Agent Studio
		{
			project: PROJECT_EL,
			prefix: "EL",
			seq: 1,
			title: "Lock decisions in docs/handoff.md",
			status: STATUS_IDS.planning,
			priority: "high" as const,
			order: 1,
		},
		{
			project: PROJECT_EL,
			prefix: "EL",
			seq: 2,
			title: "Build eval scoring rubric UI",
			status: STATUS_IDS.building,
			priority: "high" as const,
			order: 2,
		},
		{
			project: PROJECT_EL,
			prefix: "EL",
			seq: 3,
			title: "MCP harness for live PROD agent",
			status: STATUS_IDS.building,
			priority: "medium" as const,
			order: 3,
		},
		{
			project: PROJECT_EL,
			prefix: "EL",
			seq: 4,
			title: "PROD vs UAT side-by-side compare view",
			status: STATUS_IDS.backlog,
			priority: "medium" as const,
			order: 4,
		},
		{
			project: PROJECT_EL,
			prefix: "EL",
			seq: 5,
			title: "Capture audio fixtures into Postgres+pgvector",
			status: STATUS_IDS.review,
			priority: "low" as const,
			order: 5,
		},
		{
			project: PROJECT_EL,
			prefix: "EL",
			seq: 6,
			title: "Initial bun + drizzle scaffolding",
			status: STATUS_IDS.shipped,
			priority: "low" as const,
			order: 6,
		},
	];

	for (const t of taskRows) {
		await db
			.insert(tasks)
			.values({
				id: `ld-task-${t.project}-${t.seq}`,
				permalinkId: permalink(t.prefix, t.seq),
				title: t.title,
				priority: t.priority,
				teamId: TEAM_ID,
				order: t.order * 1000,
				statusId: t.status,
				projectId: t.project,
				createdBy: USER_ID,
				assigneeId: USER_ID,
				score: 1,
				subscribers: [USER_ID],
				mentions: [],
				isTemplate: false,
				createdAt: nowIso,
				updatedAt: nowIso,
				statusChangedAt: now,
			} as any)
			.onConflictDoNothing({ target: tasks.id });
	}

	console.log("[seed-local-dev] documents…");
	const docs = [
		{
			id: "ld-doc-ai-interaction-dash",
			projectId: PROJECT_AI,
			name: "AI Interaction Dashboard — README",
			icon: "📊",
			content: AI_DASH_DOC,
		},
		{
			id: "ld-doc-elevenlabs-eval-dash",
			projectId: PROJECT_EL,
			name: "Voice Agent Studio — README",
			icon: "🎙️",
			content: EL_STUDIO_DOC,
		},
	];
	for (let i = 0; i < docs.length; i++) {
		const d = docs[i];
		await db
			.insert(documents)
			.values({
				id: d.id,
				name: d.name,
				icon: d.icon,
				content: d.content,
				teamId: TEAM_ID,
				projectId: d.projectId,
				parentId: null,
				order: i,
				createdBy: USER_ID,
				updatedBy: USER_ID,
				createdAt: nowIso,
				updatedAt: nowIso,
			} as any)
			.onConflictDoNothing({ target: documents.id });
		// link existing docs to project too
		await db
			.update(documents)
			.set({ projectId: d.projectId })
			.where(eq(documents.id, d.id));
	}

	console.log("[seed-local-dev] done.");
	await pool.end();
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
