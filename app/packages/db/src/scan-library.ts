// Skill / Agent / Orchestration Library — scan from disk.
//
// Walks each library_sources.root_path under LIBRARY_ALLOWED_ROOT, parses YAML
// frontmatter from every *.md (or files matching the source's include glob),
// classifies as skill / agent / orchestration, upserts into library_entries.
// Idempotent — uses file_sha to skip unchanged files; entries whose file no
// longer exists under the source are deleted.
//
// Disk is the source of truth. Nexus DB is a denormalized index.
//
// Run with the standard one-off pattern:
//
//   docker run --rm --network supabase_default \
//     -e DATABASE_URL=postgresql://postgres:your-super-secret-and-long-postgres-password@db:5432/postgres \
//     -e LIBRARY_ALLOWED_ROOT=/host-home \
//     -v /Users/john.keeney:/host-home:ro \
//     -v /Users/john.keeney/nexus-task-tracker/app/packages/db/src/schema.ts:/app/packages/db/src/schema.ts:ro \
//     -v /Users/john.keeney/nexus-task-tracker/app/packages/db/src/scan-library.ts:/app/packages/db/src/scan-library.ts:ro \
//     -w /app/packages/db \
//     app-api \
//     /usr/local/bin/bun run src/scan-library.ts

import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { and, eq, notInArray } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";
import { libraryEntries, librarySources } from "./schema";

// Minimal YAML frontmatter parser. Handles the shapes that skill/agent files
// actually use:
//   key: value
//   key: "quoted value"
//   key: [a, b, c]
//   key:
//     - item
//     - item
//   key: |
//     multiline
//     string
// Anything more exotic falls through as a string. Good enough for the library.
function parseSimpleYaml(text: string): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	const lines = text.split(/\r?\n/);
	for (let i = 0; i < lines.length; i++) {
		const line = lines[i];
		if (!line.trim() || line.trim().startsWith("#")) continue;
		const m = line.match(/^([A-Za-z0-9_-]+)\s*:\s*(.*)$/);
		if (!m) continue;
		const [, key, rest] = m;
		// inline array: [a, b, c]
		if (/^\[.*\]$/.test(rest)) {
			out[key] = rest
				.slice(1, -1)
				.split(",")
				.map((s) => s.trim().replace(/^["']|["']$/g, ""))
				.filter(Boolean);
			continue;
		}
		// block scalar: | or >
		if (rest === "|" || rest === ">" || rest === "|-" || rest === ">-") {
			const buf: string[] = [];
			let j = i + 1;
			while (
				j < lines.length &&
				(lines[j].startsWith("  ") || lines[j] === "")
			) {
				buf.push(lines[j].replace(/^ {0,2}/, ""));
				j++;
			}
			out[key] = buf.join("\n").trim();
			i = j - 1;
			continue;
		}
		// indented list
		if (rest === "") {
			const buf: string[] = [];
			let j = i + 1;
			while (
				j < lines.length &&
				(lines[j].startsWith("  -") || lines[j].startsWith("    "))
			) {
				const it = lines[j].replace(/^\s*-\s*/, "").trim();
				if (it) buf.push(it.replace(/^["']|["']$/g, ""));
				j++;
			}
			out[key] = buf;
			i = j - 1;
			continue;
		}
		// quoted string
		const trimmed = rest.trim();
		if (
			(trimmed.startsWith('"') && trimmed.endsWith('"')) ||
			(trimmed.startsWith("'") && trimmed.endsWith("'"))
		) {
			out[key] = trimmed.slice(1, -1);
			continue;
		}
		// booleans / numbers
		if (trimmed === "true") out[key] = true;
		else if (trimmed === "false") out[key] = false;
		else if (/^-?\d+(\.\d+)?$/.test(trimmed)) out[key] = Number(trimmed);
		else out[key] = trimmed;
	}
	return out;
}

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required");

const ALLOWED_ROOT = process.env.LIBRARY_ALLOWED_ROOT;
if (!ALLOWED_ROOT)
	throw new Error(
		"LIBRARY_ALLOWED_ROOT is required (host bind-mount root, e.g. /host-home)",
	);

const TEAM_ID = "local-dev-team";

const pool = new Pool({ connectionString: databaseUrl });
const db = drizzle(pool);

// Default sources to seed on first run. Treats "Nexus" as ONE consolidated
// source pointing at the canonical orchestrator template (user said: "treat
// nexus as one").
const DEFAULT_SOURCES: Array<{
	label: string;
	rootPath: string;
	kindHint: string | null;
}> = [
	{
		label: "nexus-task-tracker .claude",
		rootPath: `${ALLOWED_ROOT}/nexus-task-tracker/.claude`,
		kindHint: null,
	},
	{
		label: "AI Interaction Dashboard",
		rootPath: `${ALLOWED_ROOT}/ai-interaction-dash/.claude`,
		kindHint: null,
	},
	{
		label: "Voice Agent Studio",
		rootPath: `${ALLOWED_ROOT}/elevenlabs-eval-dash/.claude`,
		kindHint: null,
	},
	{
		label: "Nexus",
		rootPath: `${ALLOWED_ROOT}/nexus-task-tracker/.claude`,
		kindHint: "orchestration",
	},
];

// ── Helpers ────────────────────────────────────────────────────────────────

// Guard every path operation: realpath must stay under ALLOWED_ROOT.
function safeResolve(absPath: string): string {
	const real = resolve(absPath);
	const allowed = resolve(ALLOWED_ROOT!);
	if (!(real === allowed || real.startsWith(allowed + sep))) {
		throw new Error(`path escapes allowed root: ${absPath} -> ${real}`);
	}
	return real;
}

function* walk(root: string): Generator<string> {
	const real = safeResolve(root);
	if (!existsSync(real)) return;
	const stack: string[] = [real];
	while (stack.length) {
		const dir = stack.pop()!;
		let entries: ReturnType<typeof readdirSync>;
		try {
			entries = readdirSync(dir, { withFileTypes: true });
		} catch {
			continue;
		}
		for (const ent of entries) {
			const full = join(dir, ent.name);
			if (ent.isDirectory()) {
				// Skip noise: deps, vcs, build output, in-process state, and
				// Claude Code derivative trees (worktrees + archives + agent
				// memory dumps that copy skills/agents and create huge fanout).
				if (
					ent.name === "node_modules" ||
					ent.name === ".git" ||
					ent.name === "out" ||
					ent.name === "dist" ||
					ent.name === ".next" ||
					ent.name === ".turbo" ||
					ent.name === ".memory" ||
					ent.name === ".pytest_cache" ||
					ent.name === "__pycache__" ||
					ent.name === "worktrees" ||
					ent.name === "_archive" ||
					ent.name === "agent-memory" ||
					ent.name === "cache"
				)
					continue;
				stack.push(full);
			} else if (ent.isFile() && full.endsWith(".md")) {
				yield safeResolve(full);
			}
		}
	}
}

function sha(content: string): string {
	return createHash("sha256").update(content).digest("hex");
}

function splitFrontmatter(content: string): {
	frontmatter: Record<string, unknown> | null;
	body: string;
} {
	if (!content.startsWith("---")) return { frontmatter: null, body: content };
	const end = content.indexOf("\n---", 3);
	if (end < 0) return { frontmatter: null, body: content };
	const yaml = content.slice(3, end).replace(/^\r?\n/, "");
	const body = content.slice(end + 4).replace(/^\r?\n/, "");
	const parsed = parseSimpleYaml(yaml);
	return { frontmatter: parsed, body };
}

function classify(
	absPath: string,
	relPath: string,
	frontmatter: Record<string, unknown> | null,
	kindHint: string | null,
): "skill" | "agent" | "orchestration" {
	if (
		kindHint === "skill" ||
		kindHint === "agent" ||
		kindHint === "orchestration"
	) {
		return kindHint;
	}
	const lower = `${absPath} ${relPath}`.toLowerCase();
	// Strongest: path segments.
	if (lower.includes("/skills/") || lower.endsWith("/skill.md")) return "skill";
	if (lower.includes("/agents/")) return "agent";
	if (
		lower.includes("nexus-orchestrator") ||
		lower.includes("nexus-config") ||
		lower.includes("/orchestrator")
	)
		return "orchestration";
	// Frontmatter shape: agents typically have `model:` + `effort:`.
	if (frontmatter) {
		if (frontmatter.model && frontmatter.effort) return "agent";
		if (frontmatter.model) return "agent";
		if (typeof frontmatter.description === "string") return "skill";
	}
	return "skill";
}

function deriveName(
	frontmatter: Record<string, unknown> | null,
	relPath: string,
): string {
	if (frontmatter && typeof frontmatter.name === "string") {
		return frontmatter.name as string;
	}
	// Fall back to filename stem; for SKILL.md, use the parent dir name.
	const base = relPath.split(sep).pop()!.replace(/\.md$/i, "");
	if (base.toLowerCase() === "skill") {
		const parts = relPath.split(sep);
		return parts[parts.length - 2] ?? base;
	}
	return base;
}

// ── Main ───────────────────────────────────────────────────────────────────

async function ensureSources(): Promise<
	Array<{
		id: string;
		label: string;
		rootPath: string;
		kindHint: string | null;
	}>
> {
	for (const s of DEFAULT_SOURCES) {
		await db
			.insert(librarySources)
			.values({
				teamId: TEAM_ID,
				label: s.label,
				rootPath: s.rootPath,
				kindHint: s.kindHint,
				globInclude: "**/*.md",
				globExclude: "**/node_modules/**,**/.git/**",
			})
			.onConflictDoNothing({
				target: [librarySources.label, librarySources.teamId],
			});
	}
	const rows = await db
		.select()
		.from(librarySources)
		.where(eq(librarySources.teamId, TEAM_ID));
	return rows.map((r) => ({
		id: r.id,
		label: r.label,
		rootPath: r.rootPath,
		kindHint: r.kindHint,
	}));
}

async function scanSource(src: {
	id: string;
	label: string;
	rootPath: string;
	kindHint: string | null;
}) {
	console.log(`[scan] source "${src.label}" @ ${src.rootPath}`);
	let real: string;
	try {
		real = safeResolve(src.rootPath);
	} catch (e) {
		console.log(`  ! ${(e as Error).message}`);
		return;
	}
	if (!existsSync(real)) {
		console.log("  ! root does not exist (skipping)");
		return;
	}

	const seenRelativePaths: string[] = [];
	let inserted = 0;
	let updated = 0;
	let unchanged = 0;

	for (const abs of walk(real)) {
		const rel = relative(real, abs);
		seenRelativePaths.push(rel);
		const raw = readFileSync(abs, "utf8");
		const fileSha = sha(raw);

		const existing = (
			await db
				.select()
				.from(libraryEntries)
				.where(
					and(
						eq(libraryEntries.sourceId, src.id),
						eq(libraryEntries.relativePath, rel),
					),
				)
				.limit(1)
		)[0];

		if (existing && existing.fileSha === fileSha) {
			await db
				.update(libraryEntries)
				.set({ lastSeenAt: new Date().toISOString() })
				.where(eq(libraryEntries.id, existing.id));
			unchanged++;
			continue;
		}

		const { frontmatter, body } = splitFrontmatter(raw);
		const kind = classify(abs, rel, frontmatter, src.kindHint);
		const name = deriveName(frontmatter, rel);
		const description =
			frontmatter && typeof frontmatter.description === "string"
				? (frontmatter.description as string)
				: null;

		if (existing) {
			await db
				.update(libraryEntries)
				.set({
					kind,
					name,
					description,
					frontmatter,
					body,
					fileSha,
					lastSeenAt: new Date().toISOString(),
				} as any)
				.where(eq(libraryEntries.id, existing.id));
			updated++;
		} else {
			await db.insert(libraryEntries).values({
				sourceId: src.id,
				relativePath: rel,
				absolutePath: abs,
				kind,
				name,
				description,
				frontmatter,
				body,
				fileSha,
				lastSeenAt: new Date().toISOString(),
			} as any);
			inserted++;
		}
	}

	// Tombstone entries we didn't see this scan.
	if (seenRelativePaths.length > 0) {
		const stale = await db
			.select({ id: libraryEntries.id })
			.from(libraryEntries)
			.where(
				and(
					eq(libraryEntries.sourceId, src.id),
					notInArray(libraryEntries.relativePath, seenRelativePaths),
				),
			);
		if (stale.length > 0) {
			await db
				.delete(libraryEntries)
				.where(
					and(
						eq(libraryEntries.sourceId, src.id),
						notInArray(libraryEntries.relativePath, seenRelativePaths),
					),
				);
		}
		console.log(
			`  + ${inserted}  ~ ${updated}  =${unchanged}  − ${stale.length}`,
		);
	} else {
		console.log("  (no files matched)");
	}

	await db
		.update(librarySources)
		.set({ lastScannedAt: new Date().toISOString() })
		.where(eq(librarySources.id, src.id));
}

async function main() {
	const sources = await ensureSources();
	console.log(`[scan] ${sources.length} sources for team ${TEAM_ID}`);
	for (const src of sources) {
		await scanSource(src);
	}
	console.log("[scan] done.");
	await pool.end();
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
