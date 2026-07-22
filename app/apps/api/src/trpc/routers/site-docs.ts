// Site Docs — disk is source of truth for project documentation.
// Paths live under LIBRARY_ALLOWED_ROOT (/host/…). Nexus Maps are DB-only.

import { createHash, randomUUID } from "node:crypto";
import {
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	renameSync,
	statSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@nexus-app/db/client";
import { projects, siteMaps } from "@nexus-app/db/schema";
import { TRPCError } from "@trpc/server";
import { and, asc, eq } from "drizzle-orm";
import { z } from "zod/v3";

const ALLOWED_ROOT = process.env.LIBRARY_ALLOWED_ROOT;
/** Host home → container sites mount (docker-compose.local.yaml). */
const HOST_HOME = process.env.NEXUS_HOST_HOME ?? "/Users/john.keeney";
const SITES_MOUNT = "/host/sites";

function safeResolve(absPath: string): string {
	if (!ALLOWED_ROOT) throw new Error("LIBRARY_ALLOWED_ROOT not configured");
	const real = resolve(absPath);
	const allowed = resolve(ALLOWED_ROOT);
	if (!(real === allowed || real.startsWith(allowed + sep))) {
		throw new Error(`path escapes allowed root: ${absPath}`);
	}
	return real;
}

/** Accept `/host/...` or host paths under NEXUS_HOST_HOME → `/host/sites/...`. */
export function normalizeSitePath(input: string): string {
	const trimmed = input.trim().replace(/\/+$/, "");
	if (!trimmed) throw new Error("empty path");
	if (trimmed.startsWith("/host/") || trimmed === "/host") {
		return safeResolve(trimmed);
	}
	const home = resolve(HOST_HOME);
	const abs = resolve(trimmed);
	if (abs === home || abs.startsWith(home + sep)) {
		const rel = relative(home, abs);
		return safeResolve(join(SITES_MOUNT, rel));
	}
	return safeResolve(abs);
}

const SKIP_DIRS = new Set([
	".git",
	"node_modules",
	".next",
	"dist",
	"build",
	".turbo",
	".obsidian",
	".trash",
]);

type TreeNode = {
	name: string;
	relativePath: string;
	type: "file" | "dir";
	children?: TreeNode[];
};

function buildTree(root: string, dir: string): TreeNode[] {
	const nodes: TreeNode[] = [];
	let entries: ReturnType<typeof readdirSync>;
	try {
		entries = readdirSync(dir, { withFileTypes: true });
	} catch {
		return nodes;
	}
	const sorted = entries.sort((a, b) => {
		if (a.isDirectory() !== b.isDirectory()) return a.isDirectory() ? -1 : 1;
		return a.name.localeCompare(b.name);
	});
	for (const ent of sorted) {
		if (ent.name.startsWith(".") && ent.name !== ".nexus") continue;
		if (ent.isDirectory() && SKIP_DIRS.has(ent.name)) continue;
		const full = join(dir, ent.name);
		const rel = relative(root, full).split(sep).join("/");
		if (ent.isDirectory()) {
			nodes.push({
				name: ent.name,
				relativePath: rel,
				type: "dir",
				children: buildTree(root, full),
			});
		} else if (ent.isFile() && /\.(md|mdx|txt|markdown)$/i.test(ent.name)) {
			nodes.push({ name: ent.name, relativePath: rel, type: "file" });
		}
	}
	return nodes;
}

async function requireProject(projectId: string, teamId: string) {
	const [project] = await db
		.select()
		.from(projects)
		.where(and(eq(projects.id, projectId), eq(projects.teamId, teamId)))
		.limit(1);
	if (!project)
		throw new TRPCError({ code: "NOT_FOUND", message: "project not found" });
	return project;
}

export const siteDocsRouter = router({
	listSites: protectedProcedure.query(async ({ ctx }) => {
		const rows = await db
			.select({
				id: projects.id,
				name: projects.name,
				color: projects.color,
				prefix: projects.prefix,
				rootPath: projects.rootPath,
				docsPath: projects.docsPath,
				status: projects.status,
				updatedAt: projects.updatedAt,
			})
			.from(projects)
			.where(eq(projects.teamId, ctx.user.teamId!))
			.orderBy(asc(projects.name));
		return rows.filter((r) => !!r.docsPath);
	}),

	listTree: protectedProcedure
		.input(z.object({ projectId: z.string() }))
		.query(async ({ ctx, input }) => {
			const project = await requireProject(input.projectId, ctx.user.teamId!);
			if (!project.docsPath) {
				return {
					docsPath: null as string | null,
					tree: [] as TreeNode[],
					maps: [],
				};
			}
			let root: string;
			try {
				root = normalizeSitePath(project.docsPath);
			} catch (err) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message: (err as Error).message,
				});
			}
			if (!existsSync(root)) {
				return { docsPath: root, tree: [], maps: [] };
			}
			const tree = buildTree(root, root);
			const maps = await db
				.select({
					id: siteMaps.id,
					kind: siteMaps.kind,
					title: siteMaps.title,
					stale: siteMaps.stale,
					updatedAt: siteMaps.updatedAt,
				})
				.from(siteMaps)
				.where(
					and(
						eq(siteMaps.projectId, project.id),
						eq(siteMaps.teamId, ctx.user.teamId!),
					),
				)
				.orderBy(asc(siteMaps.kind));
			return { docsPath: root, tree, maps };
		}),

	readFile: protectedProcedure
		.input(
			z.object({
				projectId: z.string(),
				relativePath: z.string().min(1),
			}),
		)
		.query(async ({ ctx, input }) => {
			const project = await requireProject(input.projectId, ctx.user.teamId!);
			if (!project.docsPath) {
				throw new TRPCError({ code: "BAD_REQUEST", message: "no docs path" });
			}
			const root = normalizeSitePath(project.docsPath);
			const rel = input.relativePath.replace(/\.\.\//g, "").replace(/^\/+/, "");
			const abs = safeResolve(join(root, rel));
			if (!abs.startsWith(root + sep) && abs !== root) {
				throw new TRPCError({ code: "BAD_REQUEST", message: "path escape" });
			}
			if (!existsSync(abs) || !statSync(abs).isFile()) {
				throw new TRPCError({ code: "NOT_FOUND", message: "file not found" });
			}
			const content = readFileSync(abs, "utf8");
			const sha = createHash("sha256").update(content).digest("hex");
			return { relativePath: rel, content, sha, absolutePath: abs };
		}),

	writeFile: protectedProcedure
		.input(
			z.object({
				projectId: z.string(),
				relativePath: z.string().min(1),
				content: z.string(),
				expectedSha: z.string().optional(),
			}),
		)
		.mutation(async ({ ctx, input }) => {
			const project = await requireProject(input.projectId, ctx.user.teamId!);
			if (!project.docsPath) {
				throw new TRPCError({ code: "BAD_REQUEST", message: "no docs path" });
			}
			const root = normalizeSitePath(project.docsPath);
			const rel = input.relativePath.replace(/\.\.\//g, "").replace(/^\/+/, "");
			const abs = safeResolve(join(root, rel));
			if (!abs.startsWith(root + sep) && abs !== root) {
				throw new TRPCError({ code: "BAD_REQUEST", message: "path escape" });
			}
			if (existsSync(abs) && input.expectedSha) {
				const onDisk = readFileSync(abs, "utf8");
				const diskSha = createHash("sha256").update(onDisk).digest("hex");
				if (diskSha !== input.expectedSha) {
					throw new TRPCError({
						code: "CONFLICT",
						message: "file changed on disk since you opened it",
					});
				}
			}
			mkdirSync(dirname(abs), { recursive: true });
			const tmp = `${abs}.tmp-${Date.now()}`;
			try {
				writeFileSync(tmp, input.content, "utf8");
				renameSync(tmp, abs);
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
			return { relativePath: rel, sha };
		}),

	probePath: protectedProcedure
		.input(z.object({ path: z.string().min(1) }))
		.query(async ({ input }) => {
			let root: string;
			try {
				root = normalizeSitePath(input.path);
			} catch (err) {
				return {
					ok: false as const,
					error: (err as Error).message,
					resolved: null as string | null,
					candidates: [] as string[],
				};
			}
			if (!existsSync(root)) {
				return {
					ok: false as const,
					error: "path does not exist (is it mounted under /host/sites?)",
					resolved: root,
					candidates: [] as string[],
				};
			}
			const candidates: string[] = [];
			if (statSync(root).isDirectory()) {
				const docsJoin = join(root, "docs");
				if (existsSync(docsJoin) && statSync(docsJoin).isDirectory()) {
					candidates.push(docsJoin);
				}
				candidates.push(root);
				try {
					for (const ent of readdirSync(root, { withFileTypes: true })) {
						if (!ent.isDirectory() || ent.name.startsWith(".")) continue;
						if (SKIP_DIRS.has(ent.name)) continue;
						if (/doc/i.test(ent.name)) {
							candidates.push(join(root, ent.name));
						}
					}
				} catch {}
			}
			return {
				ok: true as const,
				error: null as string | null,
				resolved: root,
				candidates: [...new Set(candidates)],
			};
		}),

	listMaps: protectedProcedure
		.input(z.object({ projectId: z.string() }))
		.query(async ({ ctx, input }) => {
			await requireProject(input.projectId, ctx.user.teamId!);
			return db
				.select()
				.from(siteMaps)
				.where(
					and(
						eq(siteMaps.projectId, input.projectId),
						eq(siteMaps.teamId, ctx.user.teamId!),
					),
				)
				.orderBy(asc(siteMaps.kind));
		}),

	getMap: protectedProcedure
		.input(z.object({ id: z.string() }))
		.query(async ({ ctx, input }) => {
			const [row] = await db
				.select()
				.from(siteMaps)
				.where(
					and(eq(siteMaps.id, input.id), eq(siteMaps.teamId, ctx.user.teamId!)),
				)
				.limit(1);
			if (!row) throw new TRPCError({ code: "NOT_FOUND" });
			return row;
		}),

	upsertMap: protectedProcedure
		.input(
			z.object({
				id: z.string().optional(),
				projectId: z.string(),
				kind: z.enum(["architecture", "flow", "graph"]),
				title: z.string().min(1).max(200),
				content: z.string(),
			}),
		)
		.mutation(async ({ ctx, input }) => {
			await requireProject(input.projectId, ctx.user.teamId!);
			const now = new Date().toISOString();
			if (input.id) {
				const [updated] = await db
					.update(siteMaps)
					.set({
						title: input.title,
						content: input.content,
						kind: input.kind,
						stale: false,
						updatedAt: now,
					})
					.where(
						and(
							eq(siteMaps.id, input.id),
							eq(siteMaps.teamId, ctx.user.teamId!),
						),
					)
					.returning();
				if (!updated) throw new TRPCError({ code: "NOT_FOUND" });
				return updated;
			}
			const [created] = await db
				.insert(siteMaps)
				.values({
					id: `sm-${randomUUID().slice(0, 12)}`,
					projectId: input.projectId,
					teamId: ctx.user.teamId!,
					kind: input.kind,
					title: input.title,
					content: input.content,
					stale: false,
					createdAt: now,
					updatedAt: now,
				})
				.returning();
			return created;
		}),

	ensureDefaultMaps: protectedProcedure
		.input(z.object({ projectId: z.string() }))
		.mutation(async ({ ctx, input }) => {
			const project = await requireProject(input.projectId, ctx.user.teamId!);
			const existing = await db
				.select({ kind: siteMaps.kind })
				.from(siteMaps)
				.where(eq(siteMaps.projectId, input.projectId));
			const have = new Set(existing.map((e) => e.kind));
			const defaults: {
				kind: "architecture" | "flow" | "graph";
				title: string;
				content: string;
			}[] = [
				{
					kind: "architecture",
					title: "Architecture",
					content:
						"```mermaid\ngraph TD\n  A[" +
						project.name.replace(/[[\]]/g, "") +
						"] --> B[Docs]\n  A --> C[App]\n```\n",
				},
				{
					kind: "flow",
					title: "Request flow",
					content:
						"```mermaid\nflowchart LR\n  User --> UI --> API --> Data\n```\n",
				},
				{
					kind: "graph",
					title: "Module graph",
					content:
						"```mermaid\ngraph LR\n  Core --- Features\n  Features --- Integrations\n```\n",
				},
			];
			const now = new Date().toISOString();
			const created = [];
			for (const d of defaults) {
				if (have.has(d.kind)) continue;
				const [row] = await db
					.insert(siteMaps)
					.values({
						id: `sm-${randomUUID().slice(0, 12)}`,
						projectId: input.projectId,
						teamId: ctx.user.teamId!,
						kind: d.kind,
						title: d.title,
						content: d.content,
						stale: true,
						createdAt: now,
						updatedAt: now,
					})
					.returning();
				created.push(row);
			}
			return created;
		}),
});
