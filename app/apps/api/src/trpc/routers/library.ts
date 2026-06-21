// Skill / Agent / Orchestration Library — tRPC router.
// Schema + ingestion in packages/db. Disk is source of truth; this router
// reads from the indexed library_entries table + can trigger a re-scan that
// re-walks each source's root.

import { createHash } from "node:crypto";
import {
	existsSync,
	readdirSync,
	readFileSync,
	renameSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@nexus-app/db/client";
import {
	libraryEntries,
	libraryEntryProjects,
	libraryEntryTags,
	librarySources,
} from "@nexus-app/db/schema";
import { TRPCError } from "@trpc/server";
import { and, desc, eq, ilike, inArray, or, sql } from "drizzle-orm";
import { pgTable, text, timestamp } from "drizzle-orm/pg-core";
import { z } from "zod/v3";

// iter-10 Round F: local refs for the reverse backlink (task <- skill).
const taskSkillsRef = pgTable("task_skills", {
	taskId: text("task_id").notNull(),
	skillId: text("skill_id").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
});

const libTasksRef = pgTable("tasks", {
	id: text("id").primaryKey(),
	title: text("title").notNull(),
	permalinkId: text("permalink_id").notNull(),
	teamId: text("team_id").notNull(),
	projectId: text("project_id"),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
});

const ALLOWED_ROOT = process.env.LIBRARY_ALLOWED_ROOT;

function safeResolve(absPath: string): string {
	if (!ALLOWED_ROOT) throw new Error("LIBRARY_ALLOWED_ROOT not configured");
	const real = resolve(absPath);
	const allowed = resolve(ALLOWED_ROOT);
	if (!(real === allowed || real.startsWith(allowed + sep))) {
		throw new Error(`path escapes allowed root: ${absPath}`);
	}
	return real;
}

function parseSimpleYaml(text: string): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	const lines = text.split(/\r?\n/);
	for (let i = 0; i < lines.length; i++) {
		const line = lines[i];
		if (!line.trim() || line.trim().startsWith("#")) continue;
		const m = line.match(/^([A-Za-z0-9_-]+)\s*:\s*(.*)$/);
		if (!m) continue;
		const [, key, rest] = m;
		if (/^\[.*\]$/.test(rest)) {
			out[key] = rest
				.slice(1, -1)
				.split(",")
				.map((s) => s.trim().replace(/^["']|["']$/g, ""))
				.filter(Boolean);
			continue;
		}
		const trimmed = rest.trim();
		if (
			(trimmed.startsWith('"') && trimmed.endsWith('"')) ||
			(trimmed.startsWith("'") && trimmed.endsWith("'"))
		) {
			out[key] = trimmed.slice(1, -1);
			continue;
		}
		if (trimmed === "true") out[key] = true;
		else if (trimmed === "false") out[key] = false;
		else if (/^-?\d+(\.\d+)?$/.test(trimmed)) out[key] = Number(trimmed);
		else if (trimmed === "") out[key] = "";
		else out[key] = trimmed;
	}
	return out;
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
	return { frontmatter: parseSimpleYaml(yaml), body };
}

function classify(
	abs: string,
	rel: string,
	fm: Record<string, unknown> | null,
	hint: string | null,
): "skill" | "agent" | "orchestration" {
	if (hint === "skill" || hint === "agent" || hint === "orchestration")
		return hint;
	const lower = `${abs} ${rel}`.toLowerCase();
	if (lower.includes("/skills/") || lower.endsWith("/skill.md")) return "skill";
	if (lower.includes("/agents/")) return "agent";
	if (
		lower.includes("nexus-orchestrator") ||
		lower.includes("nexus-config") ||
		lower.includes("/orchestrator")
	)
		return "orchestration";
	if (fm) {
		if (fm.model && fm.effort) return "agent";
		if (fm.model) return "agent";
		if (typeof fm.description === "string") return "skill";
	}
	return "skill";
}

const SKIP_DIRS = new Set([
	"node_modules",
	".git",
	"out",
	"dist",
	".next",
	".turbo",
	".memory",
	".pytest_cache",
	"__pycache__",
	"worktrees",
	"_archive",
	"agent-memory",
	"cache",
]);

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
				if (SKIP_DIRS.has(ent.name)) continue;
				stack.push(full);
			} else if (ent.isFile() && full.endsWith(".md")) {
				yield safeResolve(full);
			}
		}
	}
}

async function scanOneSource(srcId: string) {
	const [src] = await db
		.select()
		.from(librarySources)
		.where(eq(librarySources.id, srcId))
		.limit(1);
	if (!src) return { inserted: 0, updated: 0, unchanged: 0, deleted: 0 };

	let inserted = 0;
	let updated = 0;
	let unchanged = 0;
	let deleted = 0;
	const seen: string[] = [];
	const real = safeResolve(src.rootPath);
	if (!existsSync(real)) return { inserted, updated, unchanged, deleted };

	for (const abs of walk(real)) {
		const rel = relative(real, abs);
		seen.push(rel);
		const raw = readFileSync(abs, "utf8");
		const sha = createHash("sha256").update(raw).digest("hex");
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
		if (existing && existing.fileSha === sha) {
			await db
				.update(libraryEntries)
				.set({ lastSeenAt: new Date().toISOString() })
				.where(eq(libraryEntries.id, existing.id));
			unchanged++;
			continue;
		}
		const { frontmatter, body } = splitFrontmatter(raw);
		const kind = classify(abs, rel, frontmatter, src.kindHint);
		const name =
			frontmatter && typeof frontmatter.name === "string"
				? (frontmatter.name as string)
				: (rel.split(sep).pop() ?? rel).replace(/\.md$/i, "");
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
					fileSha: sha,
					lastSeenAt: new Date().toISOString(),
					// biome-ignore lint/suspicious/noExplicitAny: drizzle set() partial patch requires any cast
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
				fileSha: sha,
				lastSeenAt: new Date().toISOString(),
				// biome-ignore lint/suspicious/noExplicitAny: drizzle values() insert requires any cast
			} as any);
			inserted++;
		}
	}
	// tombstone
	if (seen.length > 0) {
		const stale = await db
			.select({ id: libraryEntries.id })
			.from(libraryEntries)
			.where(
				and(
					eq(libraryEntries.sourceId, src.id),
					sql`${libraryEntries.relativePath} NOT IN ${seen}`,
				),
			);
		if (stale.length > 0) {
			await db.delete(libraryEntries).where(
				inArray(
					libraryEntries.id,
					stale.map((s) => s.id),
				),
			);
			deleted = stale.length;
		}
	}
	await db
		.update(librarySources)
		.set({ lastScannedAt: new Date().toISOString() })
		.where(eq(librarySources.id, src.id));
	return { inserted, updated, unchanged, deleted };
}

export const libraryRouter = router({
	// List sources (for filter dropdown + settings page).
	getSources: protectedProcedure.query(async ({ ctx }) => {
		const rows = await db
			.select({
				id: librarySources.id,
				label: librarySources.label,
				rootPath: librarySources.rootPath,
				kindHint: librarySources.kindHint,
				lastScannedAt: librarySources.lastScannedAt,
				entryCount:
					sql<number>`(SELECT count(*)::int FROM ${libraryEntries} WHERE ${libraryEntries.sourceId} = ${librarySources.id})`.as(
						"entry_count",
					),
			})
			.from(librarySources)
			.where(eq(librarySources.teamId, ctx.user.teamId!))
			.orderBy(librarySources.label);
		return rows;
	}),

	// All tags currently in use, for the tag filter chip / autocomplete.
	getTags: protectedProcedure.query(async () => {
		const rows = await db
			.select({
				tag: libraryEntryTags.tag,
				count: sql<number>`count(*)::int`.as("count"),
			})
			.from(libraryEntryTags)
			.groupBy(libraryEntryTags.tag)
			.orderBy(sql`count(*) desc`);
		return rows;
	}),

	// Paginated list with filters.
	get: protectedProcedure
		.input(
			z.object({
				kind: z.enum(["skill", "agent", "orchestration"]).optional(),
				sourceId: z.string().optional(),
				projectId: z.string().optional(),
				tag: z.string().optional(),
				search: z.string().optional(),
				pageSize: z.number().min(1).max(200).default(50),
				cursor: z.number().int().min(0).default(0),
			}),
		)
		.query(async ({ input, ctx }) => {
			const filters = [sql`${librarySources.teamId} = ${ctx.user.teamId!}`];
			if (input.kind) filters.push(eq(libraryEntries.kind, input.kind));
			if (input.sourceId)
				filters.push(eq(libraryEntries.sourceId, input.sourceId));
			if (input.search) {
				const s = `%${input.search.replace(/%/g, "\\%")}%`;
				filters.push(
					or(
						ilike(libraryEntries.name, s),
						ilike(libraryEntries.description, s),
					)!,
				);
			}
			if (input.projectId) {
				filters.push(
					sql`EXISTS (SELECT 1 FROM ${libraryEntryProjects} WHERE ${libraryEntryProjects.entryId} = ${libraryEntries.id} AND ${libraryEntryProjects.projectId} = ${input.projectId})`,
				);
			}
			if (input.tag) {
				filters.push(
					sql`EXISTS (SELECT 1 FROM ${libraryEntryTags} WHERE ${libraryEntryTags.entryId} = ${libraryEntries.id} AND ${libraryEntryTags.tag} = ${input.tag})`,
				);
			}

			const rows = await db
				.select({
					id: libraryEntries.id,
					name: libraryEntries.name,
					description: libraryEntries.description,
					kind: libraryEntries.kind,
					relativePath: libraryEntries.relativePath,
					sourceId: libraryEntries.sourceId,
					sourceLabel: librarySources.label,
					lastEditedAt: libraryEntries.lastEditedAt,
					updatedAt: libraryEntries.updatedAt,
				})
				.from(libraryEntries)
				.innerJoin(
					librarySources,
					eq(librarySources.id, libraryEntries.sourceId),
				)
				.where(and(...filters))
				.orderBy(libraryEntries.name)
				.limit(input.pageSize + 1)
				.offset(input.cursor);

			const hasMore = rows.length > input.pageSize;
			const data = hasMore ? rows.slice(0, input.pageSize) : rows;
			return {
				data,
				nextCursor: hasMore ? input.cursor + input.pageSize : null,
			};
		}),

	// Full entry with frontmatter, body, tags, project links.
	getById: protectedProcedure
		.input(z.object({ id: z.string() }))
		.query(async ({ input, ctx }) => {
			const [entry] = await db
				.select({
					id: libraryEntries.id,
					name: libraryEntries.name,
					description: libraryEntries.description,
					kind: libraryEntries.kind,
					relativePath: libraryEntries.relativePath,
					absolutePath: libraryEntries.absolutePath,
					sourceId: libraryEntries.sourceId,
					sourceLabel: librarySources.label,
					sourceRootPath: librarySources.rootPath,
					frontmatter: libraryEntries.frontmatter,
					body: libraryEntries.body,
					fileSha: libraryEntries.fileSha,
					readOnly: libraryEntries.readOnly,
					lastEditedAt: libraryEntries.lastEditedAt,
					updatedAt: libraryEntries.updatedAt,
				})
				.from(libraryEntries)
				.innerJoin(
					librarySources,
					eq(librarySources.id, libraryEntries.sourceId),
				)
				.where(
					and(
						eq(libraryEntries.id, input.id),
						eq(librarySources.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);

			if (!entry) return null;

			const tags = await db
				.select({ tag: libraryEntryTags.tag })
				.from(libraryEntryTags)
				.where(eq(libraryEntryTags.entryId, entry.id));
			const projects = await db
				.select({
					projectId: libraryEntryProjects.projectId,
					note: libraryEntryProjects.note,
				})
				.from(libraryEntryProjects)
				.where(eq(libraryEntryProjects.entryId, entry.id));
			return {
				...entry,
				tags: tags.map((t) => t.tag),
				projects,
			};
		}),

	// Trigger a re-scan. Optional sourceId restricts to one source; otherwise
	// rescans every source for the team.
	scan: protectedProcedure
		.input(z.object({ sourceId: z.string().optional() }).optional())
		.mutation(async ({ input, ctx }) => {
			const sources = input?.sourceId
				? await db
						.select()
						.from(librarySources)
						.where(
							and(
								eq(librarySources.id, input.sourceId),
								eq(librarySources.teamId, ctx.user.teamId!),
							),
						)
				: await db
						.select()
						.from(librarySources)
						.where(eq(librarySources.teamId, ctx.user.teamId!));

			const results: Array<{
				label: string;
				inserted: number;
				updated: number;
				unchanged: number;
				deleted: number;
			}> = [];
			for (const s of sources) {
				const r = await scanOneSource(s.id);
				results.push({ label: s.label, ...r });
			}
			return { results };
		}),

	// ── Phase C: write-back to disk + DB ───────────────────────────────────
	//
	// Edit a library entry. Either yaml or body (or both) — server serializes
	// to disk with an atomic .tmp + rename. fileSha returned to client so it
	// can pass it back on the next save for conflict detection.
	update: protectedProcedure
		.input(
			z.object({
				id: z.string(),
				yaml: z.string().optional(), // raw YAML frontmatter (no --- fences)
				body: z.string().optional(),
				expectedSha: z.string().optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			const [entry] = await db
				.select()
				.from(libraryEntries)
				.innerJoin(
					librarySources,
					eq(librarySources.id, libraryEntries.sourceId),
				)
				.where(
					and(
						eq(libraryEntries.id, input.id),
						eq(librarySources.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!entry) {
				throw new TRPCError({
					code: "NOT_FOUND",
					message: "library entry not found",
				});
			}
			const e = entry.library_entries;
			if (e.readOnly) {
				throw new TRPCError({
					code: "FORBIDDEN",
					message: "this entry is read-only",
				});
			}

			// Re-resolve the absolute path against the allowed root in case the
			// scoped bind-mount has been rotated since the last scan.
			let realPath: string;
			try {
				realPath = safeResolve(e.absolutePath);
			} catch (err) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message: (err as Error).message,
				});
			}
			if (!existsSync(realPath)) {
				throw new TRPCError({
					code: "NOT_FOUND",
					message: `file no longer exists on disk: ${e.relativePath}`,
				});
			}

			// Conflict detection: read current disk content, compare sha.
			const onDisk = readFileSync(realPath, "utf8");
			const diskSha = createHash("sha256").update(onDisk).digest("hex");
			if (input.expectedSha && input.expectedSha !== diskSha) {
				throw new TRPCError({
					code: "CONFLICT",
					message:
						"file changed on disk since you opened it — reload before saving",
				});
			}

			// Build the new file content.
			const { frontmatter: oldFm, body: oldBody } = splitFrontmatter(onDisk);
			const yamlText =
				input.yaml !== undefined
					? input.yaml.replace(/^---\s*\n?/, "").replace(/\n?---\s*$/, "")
					: serializeYaml(oldFm ?? {});
			const newBody = input.body !== undefined ? input.body : oldBody;
			const newContent = `---\n${yamlText.trim()}\n---\n\n${newBody.replace(/^\n+/, "")}`;

			// Validate that the new YAML at least has a name + description.
			const newFm = parseSimpleYaml(yamlText);
			if (
				typeof newFm.name !== "string" ||
				typeof newFm.description !== "string"
			) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message: "frontmatter must include `name` and `description`",
				});
			}

			// Atomic write: .tmp then rename.
			const tmp = `${realPath}.tmp-${Date.now()}`;
			try {
				writeFileSync(tmp, newContent, "utf8");
				renameSync(tmp, realPath);
			} catch (err) {
				try {
					unlinkSync(tmp);
				} catch {}
				throw new TRPCError({
					code: "INTERNAL_SERVER_ERROR",
					message: `failed to write: ${(err as Error).message}`,
				});
			}

			const newSha = createHash("sha256").update(newContent).digest("hex");
			const kind = classify(
				realPath,
				e.relativePath,
				newFm,
				entry.library_sources.kindHint,
			);
			const name = typeof newFm.name === "string" ? newFm.name : e.name;
			const description =
				typeof newFm.description === "string" ? newFm.description : null;

			const [updated] = await db
				.update(libraryEntries)
				.set({
					kind,
					name,
					description,
					frontmatter: newFm,
					body: newBody,
					fileSha: newSha,
					lastEditedAt: new Date().toISOString(),
					lastEditedBy: ctx.user.id,
					// biome-ignore lint/suspicious/noExplicitAny: drizzle set() partial patch requires any cast
				} as any)
				.where(eq(libraryEntries.id, e.id))
				.returning();
			return updated;
		}),

	// ── Phase D: tags + project links ──────────────────────────────────────

	addTag: protectedProcedure
		.input(z.object({ entryId: z.string(), tag: z.string().min(1).max(50) }))
		.mutation(async ({ input, ctx }) => {
			const tag = input.tag.trim().toLowerCase();
			if (!tag) return { ok: true };
			// Verify the entry belongs to this team via its source before mutating.
			const [e] = await db
				.select({ id: libraryEntries.id })
				.from(libraryEntries)
				.innerJoin(librarySources, eq(librarySources.id, libraryEntries.sourceId))
				.where(
					and(
						eq(libraryEntries.id, input.entryId),
						eq(librarySources.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!e) throw new TRPCError({ code: "NOT_FOUND" });
			await db
				.insert(libraryEntryTags)
				.values({ entryId: input.entryId, tag })
				.onConflictDoNothing();
			return { ok: true };
		}),

	removeTag: protectedProcedure
		.input(z.object({ entryId: z.string(), tag: z.string() }))
		.mutation(async ({ input, ctx }) => {
			// Verify the entry belongs to this team via its source before mutating.
			const [e] = await db
				.select({ id: libraryEntries.id })
				.from(libraryEntries)
				.innerJoin(librarySources, eq(librarySources.id, libraryEntries.sourceId))
				.where(
					and(
						eq(libraryEntries.id, input.entryId),
						eq(librarySources.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!e) throw new TRPCError({ code: "NOT_FOUND" });
			await db
				.delete(libraryEntryTags)
				.where(
					and(
						eq(libraryEntryTags.entryId, input.entryId),
						eq(libraryEntryTags.tag, input.tag),
					),
				);
			return { ok: true };
		}),

	linkProject: protectedProcedure
		.input(
			z.object({
				entryId: z.string(),
				projectId: z.string(),
				note: z.string().optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			// Verify the entry belongs to this team via its source before mutating.
			const [e] = await db
				.select({ id: libraryEntries.id })
				.from(libraryEntries)
				.innerJoin(librarySources, eq(librarySources.id, libraryEntries.sourceId))
				.where(
					and(
						eq(libraryEntries.id, input.entryId),
						eq(librarySources.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!e) throw new TRPCError({ code: "NOT_FOUND" });
			await db
				.insert(libraryEntryProjects)
				.values({
					entryId: input.entryId,
					projectId: input.projectId,
					note: input.note ?? null,
					// biome-ignore lint/suspicious/noExplicitAny: drizzle values() insert requires any cast
				} as any)
				.onConflictDoNothing();
			return { ok: true };
		}),

	unlinkProject: protectedProcedure
		.input(z.object({ entryId: z.string(), projectId: z.string() }))
		.mutation(async ({ input, ctx }) => {
			// Verify the entry belongs to this team via its source before mutating.
			const [e] = await db
				.select({ id: libraryEntries.id })
				.from(libraryEntries)
				.innerJoin(librarySources, eq(librarySources.id, libraryEntries.sourceId))
				.where(
					and(
						eq(libraryEntries.id, input.entryId),
						eq(librarySources.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!e) throw new TRPCError({ code: "NOT_FOUND" });
			await db
				.delete(libraryEntryProjects)
				.where(
					and(
						eq(libraryEntryProjects.entryId, input.entryId),
						eq(libraryEntryProjects.projectId, input.projectId),
					),
				);
			return { ok: true };
		}),

	// ── Phase E: source directory management ────────────────────────────────

	addSource: protectedProcedure
		.input(
			z.object({
				label: z.string().min(1).max(120),
				rootPath: z.string().min(1),
				kindHint: z
					.enum(["skill", "agent", "orchestration"])
					.optional()
					.nullable(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			// Validate path stays under allowed root before persisting.
			try {
				safeResolve(input.rootPath);
			} catch (err) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message: (err as Error).message,
				});
			}
			const [row] = await db
				.insert(librarySources)
				.values({
					teamId: ctx.user.teamId!,
					label: input.label.trim(),
					rootPath: input.rootPath.trim(),
					kindHint: input.kindHint ?? null,
				})
				.returning();
			return row;
		}),

	updateSource: protectedProcedure
		.input(
			z.object({
				id: z.string(),
				label: z.string().min(1).max(120).optional(),
				kindHint: z
					.enum(["skill", "agent", "orchestration"])
					.optional()
					.nullable(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			const patch: Record<string, unknown> = {};
			if (input.label !== undefined) patch.label = input.label;
			if (input.kindHint !== undefined) patch.kindHint = input.kindHint;
			if (Object.keys(patch).length === 0) return { ok: true };
			await db
				.update(librarySources)
				// biome-ignore lint/suspicious/noExplicitAny: drizzle set() partial patch requires any cast
				.set(patch as any)
				.where(
					and(
						eq(librarySources.id, input.id),
						eq(librarySources.teamId, ctx.user.teamId!),
					),
				);
			return { ok: true };
		}),

	removeSource: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ input, ctx }) => {
			await db
				.delete(librarySources)
				.where(
					and(
						eq(librarySources.id, input.id),
						eq(librarySources.teamId, ctx.user.teamId!),
					),
				);
			return { ok: true };
		}),

	// iter-10 Round F: tasks linked to a skill (reverse direction).
	// Relevance per codex amendment #5: link-recency then task-recency.
	listLinkedTasks: protectedProcedure
		.input(
			z.object({
				skillId: z.string(),
				limit: z.number().int().min(1).max(50).default(50),
			}),
		)
		.query(async ({ ctx, input }) => {
			const rows = await db
				.select({
					id: libTasksRef.id,
					title: libTasksRef.title,
					permalinkId: libTasksRef.permalinkId,
					projectId: libTasksRef.projectId,
					updatedAt: libTasksRef.updatedAt,
					linkedAt: taskSkillsRef.createdAt,
				})
				.from(taskSkillsRef)
				.innerJoin(libTasksRef, eq(taskSkillsRef.taskId, libTasksRef.id))
				.where(
					and(
						eq(taskSkillsRef.skillId, input.skillId),
						eq(libTasksRef.teamId, ctx.user.teamId!),
					),
				)
				.orderBy(desc(taskSkillsRef.createdAt), desc(libTasksRef.updatedAt))
				.limit(input.limit);
			return rows;
		}),
});

// Re-serialize a flat YAML object back to text. Mirrors the parser shape:
// strings, booleans, numbers, and single-level arrays.
function serializeYaml(obj: Record<string, unknown>): string {
	const lines: string[] = [];
	for (const [k, v] of Object.entries(obj)) {
		if (Array.isArray(v)) {
			const inline = v
				.map((x) => (typeof x === "string" ? JSON.stringify(x) : String(x)))
				.join(", ");
			lines.push(`${k}: [${inline}]`);
		} else if (typeof v === "string") {
			// Quote when there are special chars or it would be misread.
			if (/[:#]/.test(v) || /^\s|\s$/.test(v)) {
				lines.push(`${k}: ${JSON.stringify(v)}`);
			} else {
				lines.push(`${k}: ${v}`);
			}
		} else {
			lines.push(`${k}: ${v}`);
		}
	}
	return lines.join("\n");
}
