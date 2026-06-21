// Knowledge — Obsidian-compatible vault. Disk is source of truth; this
// router reads from a denormalized index and writes back atomically.

import { createHash } from "node:crypto";
import {
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	renameSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { extractLinks, resolveLinks } from "@api/lib/wiki-link-parser";
import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@nexus-app/db/client";
import { TRPCError } from "@trpc/server";
import { and, asc, desc, eq, inArray, sql } from "drizzle-orm";
import { boolean, jsonb, pgTable, text, timestamp } from "drizzle-orm/pg-core";
import { z } from "zod/v3";

// Inline table definitions (mirror the raw SQL DDL we ran). Kept here
// instead of in schema.ts so the api can be reasoned about without touching
// the big shared schema file each iteration.
const knowledgeVaults = pgTable("knowledge_vaults", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
	label: text("label").notNull(),
	rootPath: text("root_path").notNull(),
	isDefault: boolean("is_default").notNull().default(true),
	lastScannedAt: timestamp("last_scanned_at", {
		withTimezone: true,
		mode: "string",
	}),
	createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
});

const knowledgeNotes = pgTable("knowledge_notes", {
	id: text("id").primaryKey(),
	vaultId: text("vault_id").notNull(),
	relativePath: text("relative_path").notNull(),
	absolutePath: text("absolute_path").notNull(),
	name: text("name").notNull(),
	parentDir: text("parent_dir"),
	content: text("content"),
	frontmatter: jsonb("frontmatter"),
	fileSha: text("file_sha").notNull(),
	lastSeenAt: timestamp("last_seen_at", {
		withTimezone: true,
		mode: "string",
	}),
	lastEditedAt: timestamp("last_edited_at", {
		withTimezone: true,
		mode: "string",
	}),
	createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
});

// Wiki-link graph edges (M-002 / FEAT-002 Wave 1).
// to_note_id is nullable — unresolved links and post-delete SET NULL edges.
// Exported so the Wave-2 scanner and backlinks endpoint can import it.
export const knowledgeLinks = pgTable("knowledge_links", {
	id: text("id").primaryKey(),
	fromNoteId: text("from_note_id").notNull(),
	toNoteId: text("to_note_id"),
	linkText: text("link_text").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
});

// Local refs for the reverse-direction backlinks query (iter-10 Round F).
// Mirror only the columns we read.
const knowledgeNotesOnTasksRef = pgTable("knowledge_notes_on_tasks", {
	id: text("id").primaryKey(),
	taskId: text("task_id").notNull(),
	noteId: text("note_id").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
});

const tasksRef = pgTable("tasks", {
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
		const t = rest.trim();
		if (/^\[.*\]$/.test(t)) {
			out[key] = t
				.slice(1, -1)
				.split(",")
				.map((s) => s.trim().replace(/^["']|["']$/g, ""))
				.filter(Boolean);
		} else if (
			(t.startsWith('"') && t.endsWith('"')) ||
			(t.startsWith("'") && t.endsWith("'"))
		) {
			out[key] = t.slice(1, -1);
		} else if (t === "true") out[key] = true;
		else if (t === "false") out[key] = false;
		else if (/^-?\d+(\.\d+)?$/.test(t)) out[key] = Number(t);
		else out[key] = t;
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

const SKIP_DIRS = new Set([".obsidian", ".trash", "node_modules", ".git"]);

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

async function scanVault(vaultId: string) {
	const [v] = await db
		.select()
		.from(knowledgeVaults)
		.where(eq(knowledgeVaults.id, vaultId))
		.limit(1);
	if (!v) return { inserted: 0, updated: 0, unchanged: 0, deleted: 0 };
	const real = safeResolve(v.rootPath);
	if (!existsSync(real)) {
		mkdirSync(real, { recursive: true });
	}
	let inserted = 0;
	let updated = 0;
	let unchanged = 0;
	let deleted = 0;
	const seen: string[] = [];
	// Track which note ids had content changes so we can re-index their links.
	const changedNoteIds: string[] = [];

	for (const abs of walk(real)) {
		const rel = relative(real, abs);
		seen.push(rel);
		const raw = readFileSync(abs, "utf8");
		const sha = createHash("sha256").update(raw).digest("hex");
		const existing = (
			await db
				.select()
				.from(knowledgeNotes)
				.where(
					and(
						eq(knowledgeNotes.vaultId, vaultId),
						eq(knowledgeNotes.relativePath, rel),
					),
				)
				.limit(1)
		)[0];
		if (existing && existing.fileSha === sha) {
			await db
				.update(knowledgeNotes)
				.set({ lastSeenAt: new Date().toISOString() })
				.where(eq(knowledgeNotes.id, existing.id));
			unchanged++;
			continue;
		}
		const { frontmatter, body } = splitFrontmatter(raw);
		const segs = rel.split(sep);
		const name = (segs[segs.length - 1] ?? rel).replace(/\.md$/i, "");
		const parentDir = segs.length > 1 ? segs.slice(0, -1).join(sep) : null;
		if (existing) {
			await db
				.update(knowledgeNotes)
				.set({
					name,
					parentDir,
					content: body,
					frontmatter,
					fileSha: sha,
					absolutePath: abs,
					lastSeenAt: new Date().toISOString(),
				} as any)
				.where(eq(knowledgeNotes.id, existing.id));
			changedNoteIds.push(existing.id);
			updated++;
		} else {
			const newId = `kn-${createHash("sha256").update(`${vaultId}:${rel}`).digest("hex").slice(0, 16)}`;
			await db.insert(knowledgeNotes).values({
				id: newId,
				vaultId,
				relativePath: rel,
				absolutePath: abs,
				name,
				parentDir,
				content: body,
				frontmatter,
				fileSha: sha,
				lastSeenAt: new Date().toISOString(),
			} as any);
			changedNoteIds.push(newId);
			inserted++;
		}
	}

	// Re-index wiki-links for every note whose content changed.
	// Fetch all note refs once so resolution is O(notes) not O(notes²).
	if (changedNoteIds.length > 0) {
		const allNotes = await db
			.select({
				id: knowledgeNotes.id,
				relativePath: knowledgeNotes.relativePath,
			})
			.from(knowledgeNotes)
			.where(eq(knowledgeNotes.vaultId, vaultId));

		for (const noteId of changedNoteIds) {
			const [note] = await db
				.select({ id: knowledgeNotes.id, content: knowledgeNotes.content })
				.from(knowledgeNotes)
				.where(eq(knowledgeNotes.id, noteId))
				.limit(1);
			if (!note) continue;
			const body = note.content ?? "";
			const parsed = extractLinks(body);
			const resolved = resolveLinks(parsed, allNotes);

			// Remove stale outbound edges for this note, then insert fresh ones.
			await db
				.delete(knowledgeLinks)
				.where(eq(knowledgeLinks.fromNoteId, noteId));

			if (resolved.length > 0) {
				await db.insert(knowledgeLinks).values(
					resolved.map(({ linkText, toNoteId }, i) => ({
						id: `kl-${createHash("sha256").update(`${noteId}:${i}:${linkText}`).digest("hex").slice(0, 16)}`,
						fromNoteId: noteId,
						toNoteId: toNoteId ?? null,
						linkText,
					})),
				);
			}
		}
	}

	if (seen.length > 0) {
		const stale = await db
			.select({ id: knowledgeNotes.id, abs: knowledgeNotes.absolutePath })
			.from(knowledgeNotes)
			.where(
				and(
					eq(knowledgeNotes.vaultId, vaultId),
					sql`${knowledgeNotes.relativePath} NOT IN (${sql.join(
						seen.map((s) => sql`${s}`),
						sql`, `,
					)})`,
				),
			);
		if (stale.length > 0) {
			await db.delete(knowledgeNotes).where(
				inArray(
					knowledgeNotes.id,
					stale.map((s) => s.id),
				),
			);
			deleted = stale.length;
		}
	}
	await db
		.update(knowledgeVaults)
		.set({ lastScannedAt: new Date().toISOString() })
		.where(eq(knowledgeVaults.id, vaultId));
	return { inserted, updated, unchanged, deleted };
}

export const knowledgeRouter = router({
	getVaults: protectedProcedure.query(async ({ ctx }) => {
		return db
			.select({
				id: knowledgeVaults.id,
				label: knowledgeVaults.label,
				rootPath: knowledgeVaults.rootPath,
				isDefault: knowledgeVaults.isDefault,
				lastScannedAt: knowledgeVaults.lastScannedAt,
				noteCount:
					sql<number>`(SELECT count(*)::int FROM knowledge_notes WHERE knowledge_notes.vault_id = knowledge_vaults.id)`.as(
						"note_count",
					),
			})
			.from(knowledgeVaults)
			.where(eq(knowledgeVaults.teamId, ctx.user.teamId!));
	}),

	get: protectedProcedure
		.input(
			z
				.object({
					vaultId: z.string().optional(),
					search: z.string().optional(),
				})
				.optional(),
		)
		.query(async ({ input, ctx }) => {
			let vaultId = input?.vaultId;
			if (!vaultId) {
				const [first] = await db
					.select({ id: knowledgeVaults.id })
					.from(knowledgeVaults)
					.where(eq(knowledgeVaults.teamId, ctx.user.teamId!))
					.limit(1);
				vaultId = first?.id;
			}
			if (!vaultId) return { vaultId: null, notes: [] };

			const vaultFilter = eq(knowledgeNotes.vaultId, vaultId);
			const notes = await (input?.search
				? db
						.select({
							id: knowledgeNotes.id,
							name: knowledgeNotes.name,
							relativePath: knowledgeNotes.relativePath,
							parentDir: knowledgeNotes.parentDir,
							updatedAt: knowledgeNotes.updatedAt,
							frontmatter: knowledgeNotes.frontmatter,
							rank: sql<number>`ts_rank(content_fts, websearch_to_tsquery('english', ${input.search}))`,
						})
						.from(knowledgeNotes)
						.where(
							and(
								vaultFilter,
								sql`content_fts @@ websearch_to_tsquery('english', ${input.search})`,
							),
						)
						.orderBy(
							desc(
								sql`ts_rank(content_fts, websearch_to_tsquery('english', ${input.search}))`,
							),
						)
						.limit(500)
				: db
						.select({
							id: knowledgeNotes.id,
							name: knowledgeNotes.name,
							relativePath: knowledgeNotes.relativePath,
							parentDir: knowledgeNotes.parentDir,
							updatedAt: knowledgeNotes.updatedAt,
							frontmatter: knowledgeNotes.frontmatter,
						})
						.from(knowledgeNotes)
						.where(vaultFilter)
						.orderBy(asc(knowledgeNotes.relativePath))
						.limit(500));
			return { vaultId, notes };
		}),

	getById: protectedProcedure
		.input(z.object({ id: z.string() }))
		.query(async ({ input, ctx }) => {
			const [row] = await db
				.select({
					id: knowledgeNotes.id,
					vaultId: knowledgeNotes.vaultId,
					name: knowledgeNotes.name,
					relativePath: knowledgeNotes.relativePath,
					absolutePath: knowledgeNotes.absolutePath,
					parentDir: knowledgeNotes.parentDir,
					content: knowledgeNotes.content,
					frontmatter: knowledgeNotes.frontmatter,
					fileSha: knowledgeNotes.fileSha,
					lastEditedAt: knowledgeNotes.lastEditedAt,
					updatedAt: knowledgeNotes.updatedAt,
					vaultLabel: knowledgeVaults.label,
				})
				.from(knowledgeNotes)
				.innerJoin(
					knowledgeVaults,
					eq(knowledgeVaults.id, knowledgeNotes.vaultId),
				)
				.where(
					and(
						eq(knowledgeNotes.id, input.id),
						eq(knowledgeVaults.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			return row ?? null;
		}),

	create: protectedProcedure
		.input(
			z.object({
				vaultId: z.string().optional(),
				relativePath: z.string().min(1),
				content: z.string().default(""),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			let vaultId = input.vaultId;
			if (!vaultId) {
				const [first] = await db
					.select({
						id: knowledgeVaults.id,
						rootPath: knowledgeVaults.rootPath,
					})
					.from(knowledgeVaults)
					.where(eq(knowledgeVaults.teamId, ctx.user.teamId!))
					.limit(1);
				if (!first)
					throw new TRPCError({
						code: "NOT_FOUND",
						message: "no vault configured",
					});
				vaultId = first.id;
			}
			const [vault] = await db
				.select()
				.from(knowledgeVaults)
				.where(eq(knowledgeVaults.id, vaultId))
				.limit(1);
			if (!vault) throw new TRPCError({ code: "NOT_FOUND" });

			const rel = `${input.relativePath
				.replace(/\.\.\//g, "")
				.replace(/^\/+/, "")
				.replace(/\.md$/i, "")}.md`;
			const abs = safeResolve(join(vault.rootPath, rel));
			if (existsSync(abs)) {
				throw new TRPCError({
					code: "CONFLICT",
					message: "note already exists at that path",
				});
			}
			mkdirSync(dirname(abs), { recursive: true });
			const now = new Date().toISOString();
			const initial =
				input.content || `# ${rel.replace(/\.md$/, "").split(sep).pop()}\n\n`;
			writeFileSync(abs, initial, "utf8");
			const sha = createHash("sha256").update(initial).digest("hex");
			const segs = rel.split(sep);
			const name = (segs[segs.length - 1] ?? rel).replace(/\.md$/i, "");
			const parentDir = segs.length > 1 ? segs.slice(0, -1).join(sep) : null;
			const [row] = await db
				.insert(knowledgeNotes)
				.values({
					id: `kn-${createHash("sha256").update(`${vaultId}:${rel}:${now}`).digest("hex").slice(0, 16)}`,
					vaultId,
					relativePath: rel,
					absolutePath: abs,
					name,
					parentDir,
					content: initial,
					frontmatter: null,
					fileSha: sha,
					lastSeenAt: now,
				} as any)
				.returning();
			return row;
		}),

	update: protectedProcedure
		.input(
			z.object({
				id: z.string(),
				content: z.string(),
				expectedSha: z.string().optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			const [entry] = await db
				.select()
				.from(knowledgeNotes)
				.innerJoin(
					knowledgeVaults,
					eq(knowledgeVaults.id, knowledgeNotes.vaultId),
				)
				.where(
					and(
						eq(knowledgeNotes.id, input.id),
						eq(knowledgeVaults.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!entry) throw new TRPCError({ code: "NOT_FOUND" });
			const n = entry.knowledge_notes;
			const real = safeResolve(n.absolutePath);
			if (existsSync(real) && input.expectedSha) {
				const onDisk = readFileSync(real, "utf8");
				const diskSha = createHash("sha256").update(onDisk).digest("hex");
				if (diskSha !== input.expectedSha) {
					throw new TRPCError({
						code: "CONFLICT",
						message: "file changed on disk since you opened it",
					});
				}
			}
			mkdirSync(dirname(real), { recursive: true });
			const tmp = `${real}.tmp-${Date.now()}`;
			try {
				writeFileSync(tmp, input.content, "utf8");
				renameSync(tmp, real);
			} catch (err) {
				try {
					unlinkSync(tmp);
				} catch {}
				throw new TRPCError({
					code: "INTERNAL_SERVER_ERROR",
					message: `failed to write: ${(err as Error).message}`,
				});
			}
			const sha = createHash("sha256").update(input.content).digest("hex");
			const { frontmatter, body } = splitFrontmatter(input.content);
			const [updated] = await db
				.update(knowledgeNotes)
				.set({
					content: body,
					frontmatter,
					fileSha: sha,
					lastEditedAt: new Date().toISOString(),
					updatedAt: new Date().toISOString(),
				} as any)
				.where(eq(knowledgeNotes.id, n.id))
				.returning();
			return updated;
		}),

	delete: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ input, ctx }) => {
			const [entry] = await db
				.select()
				.from(knowledgeNotes)
				.innerJoin(
					knowledgeVaults,
					eq(knowledgeVaults.id, knowledgeNotes.vaultId),
				)
				.where(
					and(
						eq(knowledgeNotes.id, input.id),
						eq(knowledgeVaults.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!entry) throw new TRPCError({ code: "NOT_FOUND" });
			const real = safeResolve(entry.knowledge_notes.absolutePath);
			try {
				if (existsSync(real)) unlinkSync(real);
			} catch (err) {
				throw new TRPCError({
					code: "INTERNAL_SERVER_ERROR",
					message: `failed to delete: ${(err as Error).message}`,
				});
			}
			await db
				.delete(knowledgeNotes)
				.where(eq(knowledgeNotes.id, entry.knowledge_notes.id));
			return { ok: true };
		}),

	scan: protectedProcedure
		.input(z.object({ vaultId: z.string().optional() }).optional())
		.mutation(async ({ input, ctx }) => {
			const vaults = input?.vaultId
				? await db
						.select()
						.from(knowledgeVaults)
						.where(
							and(
								eq(knowledgeVaults.id, input.vaultId),
								eq(knowledgeVaults.teamId, ctx.user.teamId!),
							),
						)
				: await db
						.select()
						.from(knowledgeVaults)
						.where(eq(knowledgeVaults.teamId, ctx.user.teamId!));
			const results: any[] = [];
			for (const v of vaults) {
				const r = await scanVault(v.id);
				results.push({ label: v.label, ...r });
			}
			return { results };
		}),

	// iter-10 Round F: reverse backlink — every task that links this note.
	// Relevance per codex amendment #5: link-recency, then task-recency.
	listLinkedTasks: protectedProcedure
		.input(
			z.object({
				noteId: z.string(),
				limit: z.number().int().min(1).max(50).default(50),
			}),
		)
		.query(async ({ input, ctx }) => {
			const rows = await db
				.select({
					id: tasksRef.id,
					title: tasksRef.title,
					permalinkId: tasksRef.permalinkId,
					projectId: tasksRef.projectId,
					updatedAt: tasksRef.updatedAt,
					linkedAt: knowledgeNotesOnTasksRef.createdAt,
				})
				.from(knowledgeNotesOnTasksRef)
				.innerJoin(tasksRef, eq(knowledgeNotesOnTasksRef.taskId, tasksRef.id))
				.where(
					and(
						eq(knowledgeNotesOnTasksRef.noteId, input.noteId),
						eq(tasksRef.teamId, ctx.user.teamId!),
					),
				)
				.orderBy(
					desc(knowledgeNotesOnTasksRef.createdAt),
					desc(tasksRef.updatedAt),
				)
				.limit(input.limit);
			return rows;
		}),

	listBacklinks: protectedProcedure
		.input(z.object({ noteId: z.string() }))
		.query(async ({ input, ctx }) => {
			const rows = await db
				.select({
					linkId: knowledgeLinks.id,
					linkText: knowledgeLinks.linkText,
					fromNoteId: knowledgeNotes.id,
					fromNoteName: knowledgeNotes.name,
					fromNoteRelativePath: knowledgeNotes.relativePath,
					linkedAt: knowledgeLinks.createdAt,
				})
				.from(knowledgeLinks)
				.innerJoin(
					knowledgeNotes,
					eq(knowledgeNotes.id, knowledgeLinks.fromNoteId),
				)
				.innerJoin(
					knowledgeVaults,
					eq(knowledgeVaults.id, knowledgeNotes.vaultId),
				)
				.where(
					and(
						eq(knowledgeLinks.toNoteId, input.noteId),
						eq(knowledgeVaults.teamId, ctx.user.teamId!),
					),
				)
				.orderBy(desc(knowledgeLinks.createdAt));
			return rows;
		}),

	updateVault: protectedProcedure
		.input(
			z.object({
				vaultId: z.string(),
				root_path: z.string().min(1),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			const [existing] = await db
				.select({ id: knowledgeVaults.id })
				.from(knowledgeVaults)
				.where(
					and(
						eq(knowledgeVaults.id, input.vaultId),
						eq(knowledgeVaults.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!existing) throw new TRPCError({ code: "NOT_FOUND" });
			const normalizedPath = safeResolve(input.root_path);
			const [updated] = await db
				.update(knowledgeVaults)
				.set({ rootPath: normalizedPath })
				.where(eq(knowledgeVaults.id, input.vaultId))
				.returning();
			return updated;
		}),
});
