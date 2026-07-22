// Agent Config — Option B: multi-root disk mirror with agent filter chips.
// Paths live under LIBRARY_ALLOWED_ROOT (/host/…). Defaults map host home → /host/home/…

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
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@nexus-app/db/client";
import { agentConfigRoots } from "@nexus-app/db/schema";
import { TRPCError } from "@trpc/server";
import { and, asc, eq } from "drizzle-orm";
import { z } from "zod/v3";

const ALLOWED_ROOT = process.env.LIBRARY_ALLOWED_ROOT;
const HOST_HOME = process.env.NEXUS_HOST_HOME ?? "/Users/john.keeney";
const HOME_MOUNT = "/host/home";

const agentEnum = z.enum(["claude", "codex", "cursor", "pi", "oh", "custom"]);

function safeResolve(absPath: string): string {
	if (!ALLOWED_ROOT) throw new Error("LIBRARY_ALLOWED_ROOT not configured");
	const real = resolve(absPath);
	const allowed = resolve(ALLOWED_ROOT);
	if (!(real === allowed || real.startsWith(allowed + sep))) {
		throw new Error(`path escapes allowed root: ${absPath}`);
	}
	return real;
}

/** Accept `/host/...`, `~/...`, or host-home absolute paths → `/host/home/...`. */
export function normalizeAgentPath(input: string): string {
	const trimmed = input.trim().replace(/\/+$/, "");
	if (!trimmed) throw new Error("empty path");
	if (trimmed.startsWith("/host/") || trimmed === "/host") {
		return safeResolve(trimmed);
	}
	const home = resolve(HOST_HOME);
	let abs = trimmed;
	if (trimmed === "~" || trimmed.startsWith("~/")) {
		abs = join(home, trimmed.slice(2));
	} else {
		abs = resolve(trimmed);
	}
	if (abs === home || abs.startsWith(home + sep)) {
		const rel = relative(home, abs);
		return safeResolve(join(HOME_MOUNT, rel));
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
	"Cache",
	"Code Cache",
	"GPUCache",
	"DawnGraphiteCache",
	"DawnWebGPUCache",
	"blob_storage",
	"IndexedDB",
	"Session Storage",
	"Local Storage",
	"sessions",
	"archived_sessions",
	"acp-sessions",
	"claude-code-sessions",
	"claude-code-vm",
	"vm_bundles",
	".tmp",
	"backups",
	"debug",
	"cache",
	"Cookies",
	"Network",
	"logs",
	"log",
	"tasks",
	"router_capture",
	"usage-cache",
	"Crashpad",
	"Service Worker",
	"VideoDecodeStats",
	"shared_proto_db",
	"WebStorage",
]);

const FILE_RE =
	/\.(md|mdx|txt|markdown|json|toml|ya?ml|ts|js|mjs|cjs|sh|zsh)$/i;

const SECRET_RE =
	/(^|\/)(auth\.json|\.env|.*credentials.*|.*secret.*|.*token.*|.*\.pem|Cookies)(\.|$)/i;

type TreeNode = {
	name: string;
	relativePath: string;
	type: "file" | "dir";
	/** Present only after lazy expand; dirs omit until loaded. */
	children?: TreeNode[];
	expandable?: boolean;
};

/** One directory level only — full recursive trees are multi-MB and freeze the UI. */
function listDirLevel(root: string, dir: string): TreeNode[] {
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
		if (ent.name === ".DS_Store") continue;
		if (/\.(tmp|bak|wal|shm)(\b|$)/i.test(ent.name)) continue;
		if (/\.tmp\.\d+$/i.test(ent.name)) continue;
		if (ent.isDirectory() && SKIP_DIRS.has(ent.name)) continue;
		const full = join(dir, ent.name);
		const rel = relative(root, full).split(sep).join("/");
		if (ent.isDirectory()) {
			nodes.push({
				name: ent.name,
				relativePath: rel,
				type: "dir",
				expandable: true,
			});
		} else if (ent.isFile() && FILE_RE.test(ent.name)) {
			nodes.push({ name: ent.name, relativePath: rel, type: "file" });
		}
	}
	return nodes;
}

function isSecretPath(rel: string): boolean {
	return SECRET_RE.test(rel);
}

function maskContent(content: string): string {
	try {
		const parsed = JSON.parse(content) as unknown;
		return `${JSON.stringify(redactJson(parsed), null, 2)}\n`;
	} catch {
		return content
			.replace(
				/(["']?(?:api[_-]?key|token|secret|password|key)["']?\s*[:=]\s*)(["']?)[^"',\n]+/gi,
				"$1$2***",
			)
			.replace(/sk-[a-zA-Z0-9_-]{8,}/g, "sk-***")
			.replace(/Bearer\s+\S+/gi, "Bearer ***");
	}
}

function redactJson(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(redactJson);
	if (value && typeof value === "object") {
		const out: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
			if (
				/key|token|secret|password|auth|credential/i.test(k) &&
				typeof v === "string"
			) {
				out[k] = "***";
			} else {
				out[k] = redactJson(v);
			}
		}
		return out;
	}
	return value;
}

const DEFAULT_ROOTS: Array<{
	agent: z.infer<typeof agentEnum>;
	label: string;
	path: string;
	sortOrder: number;
}> = [
	{
		agent: "claude",
		label: "Code",
		path: `${HOME_MOUNT}/.claude`,
		sortOrder: 10,
	},
	{
		agent: "claude",
		label: "Desktop",
		path: `${HOME_MOUNT}/Library/Application Support/Claude`,
		sortOrder: 20,
	},
	{
		agent: "codex",
		label: "Codex",
		path: `${HOME_MOUNT}/.codex`,
		sortOrder: 30,
	},
	{
		agent: "cursor",
		label: "Cursor",
		path: `${HOME_MOUNT}/.cursor`,
		sortOrder: 40,
	},
	{ agent: "pi", label: "Pi", path: `${HOME_MOUNT}/.pi`, sortOrder: 50 },
];

async function requireRoot(rootId: string, teamId: string) {
	const [row] = await db
		.select()
		.from(agentConfigRoots)
		.where(
			and(eq(agentConfigRoots.id, rootId), eq(agentConfigRoots.teamId, teamId)),
		)
		.limit(1);
	if (!row)
		throw new TRPCError({ code: "NOT_FOUND", message: "root not found" });
	return row;
}

async function ensureDefaultRoots(teamId: string) {
	const existing = await db
		.select({ path: agentConfigRoots.path })
		.from(agentConfigRoots)
		.where(eq(agentConfigRoots.teamId, teamId));
	const have = new Set(existing.map((e) => e.path));
	const toInsert = DEFAULT_ROOTS.filter((d) => !have.has(d.path));
	if (toInsert.length === 0) return 0;
	await db.insert(agentConfigRoots).values(
		toInsert.map((d) => ({
			id: randomUUID(),
			teamId,
			agent: d.agent,
			label: d.label,
			path: d.path,
			enabled: true,
			sortOrder: d.sortOrder,
		})),
	);
	return toInsert.length;
}

export const agentConfigRouter = router({
	listRoots: protectedProcedure
		.input(
			z
				.object({
					agent: agentEnum.or(z.literal("all")).optional(),
				})
				.optional(),
		)
		.query(async ({ ctx, input }) => {
			const teamId = ctx.user.teamId!;
			let rows = await db
				.select()
				.from(agentConfigRoots)
				.where(eq(agentConfigRoots.teamId, teamId))
				.orderBy(asc(agentConfigRoots.sortOrder), asc(agentConfigRoots.label));

			if (rows.length === 0) {
				await ensureDefaultRoots(teamId);
				rows = await db
					.select()
					.from(agentConfigRoots)
					.where(eq(agentConfigRoots.teamId, teamId))
					.orderBy(
						asc(agentConfigRoots.sortOrder),
						asc(agentConfigRoots.label),
					);
			}

			const agent = input?.agent ?? "all";
			const filtered =
				agent === "all" ? rows : rows.filter((r) => r.agent === agent);

			return filtered.map((r) => {
				let exists = false;
				let resolved: string | null = null;
				try {
					resolved = normalizeAgentPath(r.path);
					exists = existsSync(resolved);
				} catch {
					exists = false;
				}
				return { ...r, resolved, exists };
			});
		}),

	ensureDefaults: protectedProcedure.mutation(async ({ ctx }) => {
		const teamId = ctx.user.teamId!;
		const inserted = await ensureDefaultRoots(teamId);
		return { inserted };
	}),

	listTree: protectedProcedure
		.input(z.object({ rootId: z.string() }))
		.query(async ({ ctx, input }) => {
			const row = await requireRoot(input.rootId, ctx.user.teamId!);
			let root: string;
			try {
				root = normalizeAgentPath(row.path);
			} catch (err) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message: (err as Error).message,
				});
			}
			if (!existsSync(root)) {
				return { path: root, tree: [] as TreeNode[], exists: false };
			}
			const st = statSync(root);
			if (st.isFile()) {
				return {
					path: root,
					exists: true,
					tree: [
						{
							name: basename(root),
							relativePath: basename(root),
							type: "file" as const,
						},
					],
				};
			}
			return { path: root, exists: true, tree: listDirLevel(root, root) };
		}),

	listChildren: protectedProcedure
		.input(
			z.object({
				rootId: z.string(),
				relativePath: z.string().default(""),
			}),
		)
		.query(async ({ ctx, input }) => {
			const row = await requireRoot(input.rootId, ctx.user.teamId!);
			const root = normalizeAgentPath(row.path);
			if (!existsSync(root) || !statSync(root).isDirectory()) {
				return { children: [] as TreeNode[] };
			}
			const rel = input.relativePath.replace(/\.\.\//g, "").replace(/^\/+/, "");
			const dir = rel ? safeResolve(join(root, rel)) : root;
			if (!dir.startsWith(root + sep) && dir !== root) {
				throw new TRPCError({ code: "BAD_REQUEST", message: "path escape" });
			}
			if (!existsSync(dir) || !statSync(dir).isDirectory()) {
				return { children: [] as TreeNode[] };
			}
			return { children: listDirLevel(root, dir) };
		}),

	readFile: protectedProcedure
		.input(
			z.object({
				rootId: z.string(),
				relativePath: z.string().min(1),
				revealSecrets: z.boolean().optional(),
			}),
		)
		.query(async ({ ctx, input }) => {
			const row = await requireRoot(input.rootId, ctx.user.teamId!);
			const root = normalizeAgentPath(row.path);
			const rel = input.relativePath.replace(/\.\.\//g, "").replace(/^\/+/, "");
			let abs: string;
			if (statSync(root).isFile()) {
				abs = root;
			} else {
				abs = safeResolve(join(root, rel));
				if (!abs.startsWith(root + sep) && abs !== root) {
					throw new TRPCError({ code: "BAD_REQUEST", message: "path escape" });
				}
			}
			if (!existsSync(abs) || !statSync(abs).isFile()) {
				throw new TRPCError({ code: "NOT_FOUND", message: "file not found" });
			}
			const raw = readFileSync(abs, "utf8");
			const secret = isSecretPath(rel) || isSecretPath(basename(abs));
			const content = secret && !input.revealSecrets ? maskContent(raw) : raw;
			const sha = createHash("sha256").update(raw).digest("hex");
			return {
				relativePath: rel,
				content,
				sha,
				absolutePath: abs,
				secret,
				masked: secret && !input.revealSecrets,
			};
		}),

	writeFile: protectedProcedure
		.input(
			z.object({
				rootId: z.string(),
				relativePath: z.string().min(1),
				content: z.string(),
				expectedSha: z.string().optional(),
				allowSecretWrite: z.boolean().optional(),
			}),
		)
		.mutation(async ({ ctx, input }) => {
			const row = await requireRoot(input.rootId, ctx.user.teamId!);
			const root = normalizeAgentPath(row.path);
			const rel = input.relativePath.replace(/\.\.\//g, "").replace(/^\/+/, "");
			let abs: string;
			if (existsSync(root) && statSync(root).isFile()) {
				abs = root;
			} else {
				abs = safeResolve(join(root, rel));
				if (!abs.startsWith(root + sep) && abs !== root) {
					throw new TRPCError({ code: "BAD_REQUEST", message: "path escape" });
				}
			}
			if (
				(isSecretPath(rel) || isSecretPath(basename(abs))) &&
				!input.allowSecretWrite
			) {
				throw new TRPCError({
					code: "FORBIDDEN",
					message: "secret file — pass allowSecretWrite to confirm",
				});
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

	createFile: protectedProcedure
		.input(
			z.object({
				rootId: z.string(),
				relativePath: z.string().min(1).max(512),
				content: z.string().optional(),
			}),
		)
		.mutation(async ({ ctx, input }) => {
			const row = await requireRoot(input.rootId, ctx.user.teamId!);
			const root = normalizeAgentPath(row.path);
			if (!existsSync(root) || !statSync(root).isDirectory()) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message: "root is not a writable directory",
				});
			}
			const rel = input.relativePath
				.replace(/\\/g, "/")
				.replace(/\.\.\//g, "")
				.replace(/^\/+/, "")
				.trim();
			if (!rel || rel.endsWith("/")) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message: "relativePath must be a file path",
				});
			}
			if (!FILE_RE.test(basename(rel))) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message:
						"unsupported extension — use md, json, toml, yaml, txt, ts, js, or sh",
				});
			}
			const abs = safeResolve(join(root, rel));
			if (!abs.startsWith(root + sep) && abs !== root) {
				throw new TRPCError({ code: "BAD_REQUEST", message: "path escape" });
			}
			if (existsSync(abs)) {
				throw new TRPCError({
					code: "CONFLICT",
					message: "file already exists",
				});
			}
			if (isSecretPath(rel)) {
				throw new TRPCError({
					code: "FORBIDDEN",
					message: "cannot create secret-named files from this UI",
				});
			}
			const content = input.content ?? "";
			mkdirSync(dirname(abs), { recursive: true });
			const tmp = `${abs}.tmp-${Date.now()}`;
			try {
				writeFileSync(tmp, content, "utf8");
				renameSync(tmp, abs);
			} catch (err) {
				try {
					unlinkSync(tmp);
				} catch {}
				throw new TRPCError({
					code: "INTERNAL_SERVER_ERROR",
					message: `failed to create: ${(err as Error).message}`,
				});
			}
			const sha = createHash("sha256").update(content).digest("hex");
			return { relativePath: rel, sha };
		}),

	probePath: protectedProcedure
		.input(z.object({ path: z.string().min(1) }))
		.query(async ({ input }) => {
			try {
				const resolved = normalizeAgentPath(input.path);
				const exists = existsSync(resolved);
				return {
					ok: exists,
					resolved,
					error: exists
						? null
						: "path does not exist (is it mounted under /host/home?)",
				};
			} catch (err) {
				return {
					ok: false,
					resolved: null as string | null,
					error: (err as Error).message,
				};
			}
		}),

	addRoot: protectedProcedure
		.input(
			z.object({
				agent: agentEnum,
				label: z.string().min(1).max(80),
				path: z.string().min(1).max(1024),
			}),
		)
		.mutation(async ({ ctx, input }) => {
			const teamId = ctx.user.teamId!;
			let resolved: string;
			try {
				resolved = normalizeAgentPath(input.path);
			} catch (err) {
				throw new TRPCError({
					code: "BAD_REQUEST",
					message: (err as Error).message,
				});
			}
			const [row] = await db
				.insert(agentConfigRoots)
				.values({
					id: randomUUID(),
					teamId,
					agent: input.agent,
					label: input.label.trim(),
					path: resolved,
					enabled: true,
					sortOrder: 100,
				})
				.returning();
			return row;
		}),

	removeRoot: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ ctx, input }) => {
			await db
				.delete(agentConfigRoots)
				.where(
					and(
						eq(agentConfigRoots.id, input.id),
						eq(agentConfigRoots.teamId, ctx.user.teamId!),
					),
				);
			return { ok: true };
		}),

	updateRoot: protectedProcedure
		.input(
			z.object({
				id: z.string(),
				label: z.string().min(1).max(80).optional(),
				enabled: z.boolean().optional(),
				agent: agentEnum.optional(),
			}),
		)
		.mutation(async ({ ctx, input }) => {
			const { id, ...patch } = input;
			const [row] = await db
				.update(agentConfigRoots)
				.set({
					...patch,
					updatedAt: new Date().toISOString(),
				})
				.where(
					and(
						eq(agentConfigRoots.id, id),
						eq(agentConfigRoots.teamId, ctx.user.teamId!),
					),
				)
				.returning();
			if (!row) throw new TRPCError({ code: "NOT_FOUND" });
			return row;
		}),
});
