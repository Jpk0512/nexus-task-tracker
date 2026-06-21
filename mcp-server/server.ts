#!/usr/bin/env bun
// nexus-mcp — expose todos, tasks, projects, knowledge, and prompts to Claude
// over MCP stdio. Direct pg client; no API hop.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
	CallToolRequestSchema,
	ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import pg from "pg";
import { z } from "zod";
import { nanoid } from "nanoid";

const TEAM_ID = process.env.NEXUS_TEAM_ID ?? "local-dev-team";
const USER_ID = process.env.NEXUS_USER_ID ?? "local-dev-user";
const KNOWLEDGE_ROOT = process.env.NEXUS_KNOWLEDGE_ROOT
	? resolve(process.env.NEXUS_KNOWLEDGE_ROOT)
	: "/Users/john.keeney/nexus-knowledge";
const DATABASE_URL =
	process.env.NEXUS_DATABASE_URL ??
	"postgresql://mimrai:mimrai@localhost:55432/mimrai";

const pool = new pg.Pool({ connectionString: DATABASE_URL });

// Re-use server log channel via stderr (stdout is reserved for MCP transport).
function log(msg: string) {
	process.stderr.write(`[nexus-mcp] ${msg}\n`);
}

// ── path safety for the knowledge vault ──────────────────────────────────
function safeKnowledgePath(rel: string): string {
	const cleaned = rel.replace(/^\/+/, "");
	const full = resolve(KNOWLEDGE_ROOT, cleaned);
	if (!full.startsWith(`${KNOWLEDGE_ROOT}/`) && full !== KNOWLEDGE_ROOT) {
		throw new Error(`path outside knowledge root: ${rel}`);
	}
	return full;
}

// ── tool definitions ─────────────────────────────────────────────────────
const tools = [
	{
		name: "add_todo",
		description:
			"Add a quick to-do item. These are checklist-style (separate from project tasks). Optionally attach to a project.",
		inputSchema: {
			type: "object",
			properties: {
				content: { type: "string", description: "What needs doing" },
				project_slug: {
					type: "string",
					description: "Optional project slug (e.g. 'ai-interaction-dashboard')",
				},
				tags: {
					type: "array",
					items: { type: "string" },
					description: "Optional tag strings",
				},
			},
			required: ["content"],
		},
	},
	{
		name: "list_todos",
		description:
			"List to-dos. By default returns open (unchecked) ones in their drag-order. Pass include_done=true to also see completed ones at the bottom.",
		inputSchema: {
			type: "object",
			properties: {
				include_done: { type: "boolean", default: false },
				project_slug: { type: "string" },
			},
		},
	},
	{
		name: "check_todo",
		description:
			"Mark a to-do as done (strikes it through and sinks it to the bottom). Pass the exact todo id OR a search string that uniquely matches the todo's content.",
		inputSchema: {
			type: "object",
			properties: {
				id_or_search: {
					type: "string",
					description: "Todo ID returned by list_todos, or a search string that uniquely matches todo content (case-insensitive contains)",
				},
			},
			required: ["id_or_search"],
		},
	},
	{
		name: "list_tasks_due_soon",
		description:
			"List project tasks that are due within the next N days (default 7). These are the heavy-weight tasks with project/priority/due-date, not the checklist to-dos.",
		inputSchema: {
			type: "object",
			properties: {
				days: { type: "integer", default: 7, minimum: 0, maximum: 90 },
				limit: { type: "integer", default: 25, minimum: 1, maximum: 200 },
			},
		},
	},
	{
		name: "list_projects",
		description: "List all active projects with their slug + prefix.",
		inputSchema: { type: "object", properties: {} },
	},
	{
		name: "search_knowledge",
		description:
			"Full-text search the Obsidian-compatible knowledge vault. Returns matching notes with title + path + a short snippet.",
		inputSchema: {
			type: "object",
			properties: {
				query: { type: "string" },
				limit: { type: "integer", default: 10, minimum: 1, maximum: 50 },
			},
			required: ["query"],
		},
	},
	{
		name: "read_note",
		description:
			"Read a knowledge note by its relative path (e.g. 'daily/2026-05-16.md').",
		inputSchema: {
			type: "object",
			properties: { path: { type: "string" } },
			required: ["path"],
		},
	},
	{
		name: "write_note",
		description:
			"Create or overwrite a knowledge note. Path is relative to the vault root. Use mode='append' to add to the bottom of an existing note instead of replacing it.",
		inputSchema: {
			type: "object",
			properties: {
				path: { type: "string" },
				content: { type: "string" },
				mode: { type: "string", enum: ["replace", "append"], default: "replace" },
			},
			required: ["path", "content"],
		},
	},
	{
		name: "list_prompts",
		description:
			"List saved prompts for an AI product (e.g. product_slug='kbuddy'). Omit product_slug to list everything.",
		inputSchema: {
			type: "object",
			properties: { product_slug: { type: "string" } },
		},
	},
	{
		name: "get_prompt",
		description:
			"Fetch the full content + notes + variables of a saved prompt by product+slug.",
		inputSchema: {
			type: "object",
			properties: {
				product_slug: { type: "string" },
				prompt_slug: { type: "string" },
				vars: {
					type: "object",
					additionalProperties: { type: "string" },
					description: "Optional map of variable name → value to substitute {{var}} placeholders in the prompt content.",
				},
			},
			required: ["product_slug", "prompt_slug"],
		},
	},
	{
		name: "add_task",
		description:
			"Create a new project task with a title and project. Tasks are the heavy-weight work items with due dates, priority, and status — distinct from checklist to-dos.",
		inputSchema: {
			type: "object",
			properties: {
				title: { type: "string", description: "Task title" },
				project_slug: { type: "string", description: "Project slug or name to attach the task to" },
				due_date: { type: "string", description: "Optional ISO-8601 due date (e.g. '2026-07-01')" },
				priority: { type: "string", enum: ["urgent", "high", "medium", "low"], description: "Optional priority level" },
				status_name: { type: "string", description: "Optional status name (e.g. 'In Progress')" },
			},
			required: ["title", "project_slug"],
		},
	},
] as const;

// ── helpers ──────────────────────────────────────────────────────────────
function nid(prefix: string): string {
	return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

// Projects have no slug column; resolve by case-insensitive name or by
// lowercased-dashed match (so "ai-interaction-dashboard" matches "AI Interaction Dashboard").
async function projectIdByName(name: string): Promise<string | null> {
	const r = await pool.query(
		`SELECT id, name FROM projects
			WHERE team_id=$1
				AND (lower(name) = lower($2)
					OR regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g') = lower($2)
					OR lower(name) ILIKE '%' || lower($2) || '%')
			ORDER BY length(name) ASC LIMIT 1`,
		[TEAM_ID, name],
	);
	return r.rows[0]?.id ?? null;
}

// ── tool handlers ────────────────────────────────────────────────────────
const handlers: Record<string, (input: any) => Promise<unknown>> = {
	async add_todo(input) {
		const schema = z.object({
			content: z.string().min(1),
			project_slug: z.string().optional(),
			tags: z.array(z.string()).optional(),
		});
		const { content, project_slug, tags } = schema.parse(input);
		const projectId = project_slug
			? await projectIdByName(project_slug)
			: null;
		if (project_slug && !projectId)
			throw new Error(`project matching '${project_slug}' not found`);
		const topRow = await pool.query(
			'SELECT MIN("order") as m FROM todos WHERE team_id=$1 AND checked=false',
			[TEAM_ID],
		);
		const top = topRow.rows[0]?.m;
		const orderVal = top != null ? Number(top) - 1000 : 0;
		const id = nid("td");
		await pool.query(
			`INSERT INTO todos
				(id, team_id, user_id, content, project_id, tags, "order", checked, created_at, updated_at)
				VALUES ($1,$2,$3,$4,$5,$6,$7,false,now(),now())`,
			[id, TEAM_ID, USER_ID, content, projectId, tags ?? [], orderVal],
		);
		return { id, content, projectId };
	},

	async list_todos(input) {
		const { include_done = false, project_slug } = (input ?? {}) as any;
		const params: unknown[] = [TEAM_ID];
		let where = "team_id=$1";
		if (!include_done) where += " AND checked=false";
		if (project_slug) {
			const pid = await projectIdByName(project_slug);
			if (!pid) throw new Error(`project matching '${project_slug}' not found`);
			params.push(pid);
			where += ` AND project_id=$${params.length}`;
		}
		const r = await pool.query(
			`SELECT id, content, checked as done, tags, project_id, "order"
				FROM todos WHERE ${where}
				ORDER BY checked ASC, "order" ASC, created_at DESC
				LIMIT 200`,
			params,
		);
		return r.rows;
	},

	async check_todo(input) {
		const { id_or_search } = z.object({ id_or_search: z.string() }).parse(input);
		const bottomRow = await pool.query(
			'SELECT MAX("order") as m FROM todos WHERE team_id=$1 AND checked=true',
			[TEAM_ID],
		);
		const bot = bottomRow.rows[0]?.m;
		const orderVal = bot != null ? Number(bot) + 1000 : 1000000;
		// Try exact id match first.
		const r = await pool.query(
			`UPDATE todos SET checked=true, checked_at=now(), "order"=$3, updated_at=now()
				WHERE id=$1 AND team_id=$2 RETURNING id, content`,
			[id_or_search, TEAM_ID, orderVal],
		);
		if ((r.rowCount ?? 0) > 0) return r.rows[0];
		// Fall back to case-insensitive content search over unchecked todos.
		const matches = await pool.query(
			`SELECT id FROM todos WHERE team_id=$1 AND checked=false AND content ILIKE $2`,
			[TEAM_ID, `%${id_or_search}%`],
		);
		if (matches.rows.length === 0)
			throw new Error(`no unchecked todo matching '${id_or_search}'`);
		if (matches.rows.length > 1)
			throw new Error(`${matches.rows.length} todos match '${id_or_search}' — be more specific`);
		const matchedId = matches.rows[0].id as string;
		const r2 = await pool.query(
			`UPDATE todos SET checked=true, checked_at=now(), "order"=$3, updated_at=now()
				WHERE id=$1 AND team_id=$2 RETURNING id, content`,
			[matchedId, TEAM_ID, orderVal],
		);
		return r2.rows[0];
	},

	async list_tasks_due_soon(input) {
		const { days = 7, limit = 25 } = (input ?? {}) as any;
		const r = await pool.query(
			`SELECT t.id, t.permalink_id, t.title, t.due_date, t.priority,
				p.name as project, st.name as status, st.type as status_type
				FROM tasks t
				LEFT JOIN projects p ON p.id = t.project_id
				LEFT JOIN statuses st ON st.id = t.status_id
				WHERE t.team_id=$1
					AND t.due_date IS NOT NULL
					AND t.due_date <= now() + ($2 || ' days')::interval
					AND (st.type IS NULL OR st.type::text <> 'done')
				ORDER BY t.due_date ASC
				LIMIT $3`,
			[TEAM_ID, String(days), limit],
		);
		return r.rows;
	},

	async list_projects() {
		const r = await pool.query(
			`SELECT id, name, prefix, status FROM projects
				WHERE team_id=$1 AND archived=false
				ORDER BY name ASC`,
			[TEAM_ID],
		);
		return r.rows;
	},

	async search_knowledge(input) {
		const { query, limit = 10 } = z
			.object({ query: z.string().min(1), limit: z.number().int().optional() })
			.parse(input);
		// DB has indexed paths/titles; fall back to filesystem grep for body.
		const dbRows = await pool.query(
			`SELECT n.relative_path as path, n.name as title
				FROM knowledge_notes n
				JOIN knowledge_vaults v ON v.id = n.vault_id
				WHERE v.team_id=$1 AND (n.name ILIKE $2 OR n.relative_path ILIKE $2)
				ORDER BY n.last_edited_at DESC NULLS LAST LIMIT $3`,
			[TEAM_ID, `%${query}%`, limit],
		).catch((e: Error) => {
			log(`knowledge db lookup skipped: ${e.message}`);
			return { rows: [] as any[] };
		});
		const results: Array<{ path: string; title: string; snippet?: string }> = [];
		for (const r of dbRows.rows) results.push({ path: r.path, title: r.title });
		// Body search via filesystem (lightweight rg-style)
		const seen = new Set(results.map((r) => r.path));
		const all = await walk(KNOWLEDGE_ROOT);
		const needle = query.toLowerCase();
		for (const abs of all) {
			if (results.length >= limit) break;
			const rel = abs.slice(KNOWLEDGE_ROOT.length + 1);
			if (seen.has(rel)) continue;
			let body: string;
			try {
				body = readFileSync(abs, "utf8");
			} catch {
				continue;
			}
			const idx = body.toLowerCase().indexOf(needle);
			if (idx === -1) continue;
			const start = Math.max(0, idx - 60);
			const end = Math.min(body.length, idx + needle.length + 80);
			results.push({
				path: rel,
				title: rel,
				snippet: `…${body.slice(start, end).replace(/\s+/g, " ")}…`,
			});
		}
		return { query, hits: results };
	},

	async read_note(input) {
		const { path: rel } = z.object({ path: z.string() }).parse(input);
		const abs = safeKnowledgePath(rel);
		if (!existsSync(abs)) throw new Error(`note not found: ${rel}`);
		return { path: rel, content: readFileSync(abs, "utf8") };
	},

	async write_note(input) {
		const { path: rel, content, mode = "replace" } = z
			.object({
				path: z.string(),
				content: z.string(),
				mode: z.enum(["replace", "append"]).optional(),
			})
			.parse(input);
		const abs = safeKnowledgePath(rel);
		mkdirSync(dirname(abs), { recursive: true });
		let finalContent = content;
		if (mode === "append" && existsSync(abs)) {
			const existing = readFileSync(abs, "utf8");
			finalContent = `${existing.replace(/\n+$/, "")}\n\n${content}\n`;
		}
		const tmp = `${abs}.tmp-${process.pid}`;
		writeFileSync(tmp, finalContent);
		const fs = await import("node:fs");
		fs.renameSync(tmp, abs);
		return { path: rel, bytes: Buffer.byteLength(finalContent, "utf8"), mode };
	},

	async list_prompts(input) {
		const { product_slug } = (input ?? {}) as any;
		if (product_slug) {
			const r = await pool.query(
				`SELECT pr.id, pr.name, pr.slug, pr.version, pr.tags, pp.name as product, pp.slug as product_slug
					FROM prompts pr
					JOIN prompt_products pp ON pp.id = pr.product_id
					WHERE pp.team_id=$1 AND pp.slug=$2
					ORDER BY pr.updated_at DESC`,
				[TEAM_ID, product_slug],
			);
			return r.rows;
		}
		const r = await pool.query(
			`SELECT pr.id, pr.name, pr.slug, pr.version, pp.name as product, pp.slug as product_slug
				FROM prompts pr
				JOIN prompt_products pp ON pp.id = pr.product_id
				WHERE pp.team_id=$1
				ORDER BY pp.name, pr.name`,
			[TEAM_ID],
		);
		return r.rows;
	},

	async get_prompt(input) {
		const { product_slug, prompt_slug, vars } = z
			.object({
				product_slug: z.string(),
				prompt_slug: z.string(),
				vars: z.record(z.string()).optional(),
			})
			.parse(input);
		const r = await pool.query(
			`SELECT pr.name, pr.content, pr.notes, pr.variables, pr.version, pr.updated_at,
				pp.name as product, pp.slug as product_slug
				FROM prompts pr
				JOIN prompt_products pp ON pp.id = pr.product_id
				WHERE pp.team_id=$1 AND pp.slug=$2 AND pr.slug=$3
				LIMIT 1`,
			[TEAM_ID, product_slug, prompt_slug],
		);
		if (r.rowCount === 0)
			throw new Error(`prompt ${product_slug}/${prompt_slug} not found`);
		const row = r.rows[0];
		if (vars && row.content) {
			row.content = row.content.replace(/\{\{(\w+)\}\}/g, (_match: string, key: string) =>
				Object.prototype.hasOwnProperty.call(vars, key) ? vars[key]! : `{{${key}}}`,
			);
		}
		return row;
	},

	async add_task(input) {
		const schema = z.object({
			title: z.string().min(1),
			project_slug: z.string(),
			due_date: z.string().optional(),
			priority: z.enum(["urgent", "high", "medium", "low"]).optional(),
			status_name: z.string().optional(),
		});
		const { title, project_slug, due_date, priority, status_name } = schema.parse(input);
		const projectId = await projectIdByName(project_slug);
		if (!projectId) throw new Error(`project matching '${project_slug}' not found`);

		// Resolve status_id: named lookup first, then project default, then team default.
		// tasks.status_id is NOT NULL — never insert null.
		// Auth-error shape (Postgres 23502 not-null violation):
		//   { severity: "ERROR", code: "23502", column: "status_id", table: "tasks" }
		let statusId: string | null = null;
		if (status_name) {
			const sr = await pool.query(
				`SELECT id FROM statuses WHERE team_id=$1 AND lower(name)=lower($2) LIMIT 1`,
				[TEAM_ID, status_name],
			);
			statusId = sr.rows[0]?.id ?? null;
			if (!statusId) throw new Error(`status '${status_name}' not found for team`);
		} else {
			// Fall back to the first status allowed for this project.
			// statuses.project_ids is text[] — empty means team-wide; non-empty means
			// project-scoped. Prefer project-specific, then team-wide, ordered by "order".
			const dr = await pool.query(
				`SELECT id FROM statuses
				 WHERE team_id=$1
				   AND (project_ids = '{}' OR $2 = ANY(project_ids))
				 ORDER BY (project_ids = '{}') ASC, "order" ASC NULLS LAST
				 LIMIT 1`,
				[TEAM_ID, projectId],
			);
			statusId = dr.rows[0]?.id ?? null;
		}
		if (!statusId) throw new Error(`no default status found for project '${project_slug}' — pass status_name`);

		// permalink_id: nanoid(12) with uniqueness retry (mirrors generateTaskPermalinkId).
		// tasks.permalink_id is NOT NULL UNIQUE.
		async function genPermalinkId(size = 12): Promise<string> {
			const value = nanoid(size);
			const { rows } = await pool.query(
				`SELECT 1 FROM tasks WHERE permalink_id=$1 LIMIT 1`,
				[value],
			);
			return rows.length > 0 ? genPermalinkId(size + 1) : value;
		}
		const permalinkId = await genPermalinkId();

		// Team-scoped sequence + order (mirrors getNextTaskSequence: WHERE team_id=...).
		// tasks.order is NOT NULL (default 6000 when table is empty).
		const seqRow = await pool.query(
			`SELECT COALESCE(MAX(sequence), 0) + 1 AS next_seq,
			        COALESCE(MAX("order"), 5999) + 1 AS next_order
			 FROM tasks WHERE team_id=$1`,
			[TEAM_ID],
		);
		const sequence: number = seqRow.rows[0]?.next_seq ?? 1;
		const order: number = seqRow.rows[0]?.next_order ?? 6000;

		const id = nid("tsk");
		await pool.query(
			`INSERT INTO tasks
				(id, team_id, title, project_id, status_id, priority, due_date,
				 sequence, permalink_id, "order", created_at, updated_at)
				VALUES ($1,$2,$3,$4,$5,COALESCE($6::task_priority,'medium'::task_priority),$7,$8,$9,$10,now(),now())`,
			[id, TEAM_ID, title, projectId, statusId, priority ?? null, due_date ?? null,
			 sequence, permalinkId, order],
		);
		return { id, title, projectId, sequence };
	},
};

async function walk(dir: string): Promise<string[]> {
	const fs = await import("node:fs/promises");
	const skip = new Set([".git", ".obsidian", ".trash", "node_modules"]);
	const out: string[] = [];
	async function recurse(d: string) {
		let entries;
		try {
			entries = await fs.readdir(d, { withFileTypes: true });
		} catch {
			return;
		}
		for (const e of entries) {
			if (skip.has(e.name)) continue;
			const full = join(d, e.name);
			if (e.isDirectory()) await recurse(full);
			else if (e.isFile() && /\.(md|markdown|txt)$/i.test(e.name)) out.push(full);
		}
	}
	await recurse(dir);
	return out;
}

// ── MCP server bootstrap ─────────────────────────────────────────────────
const server = new Server(
	{ name: "nexus", version: "0.1.0" },
	{ capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
	const name = request.params.name;
	const args = request.params.arguments ?? {};
	const fn = handlers[name];
	if (!fn) {
		return {
			isError: true,
			content: [{ type: "text", text: `Unknown tool: ${name}` }],
		};
	}
	try {
		const result = await fn(args);
		return {
			content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
		};
	} catch (err) {
		const message = err instanceof Error ? err.message : String(err);
		log(`tool ${name} failed: ${message}`);
		return {
			isError: true,
			content: [{ type: "text", text: `Error: ${message}` }],
		};
	}
});

async function main() {
	log(
		`nexus-mcp starting | team=${TEAM_ID} user=${USER_ID} vault=${KNOWLEDGE_ROOT}`,
	);
	const transport = new StdioServerTransport();
	await server.connect(transport);
	log("ready");
}

main().catch((err) => {
	log(`fatal: ${err instanceof Error ? err.stack : String(err)}`);
	process.exit(1);
});
