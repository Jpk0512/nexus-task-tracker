/**
 * TASK-019 — MCP check_todo fuzzy/content search (id_or_search): FAILING stubs.
 *
 * Feature: `check_todo` must accept an `id_or_search` argument and resolve the
 * target todo EITHER by exact todo id OR by content search over todo text, then
 * mark the match as checked.
 *
 * Acceptance criteria pinned by this file:
 *
 *  AC-1 — tool schema declares `id_or_search`, not `id`
 *          (source-pattern: checks tool definition in server.ts tools[])
 *
 *  AC-2 — content search resolves and checks a matching todo
 *          (DB integration: insert a unique-content todo, exercise the CURRENT
 *          handler logic, prove it fails to content-search — then assert the
 *          INTENDED behaviour that the new implementation must satisfy)
 *
 *  AC-3 — ambiguous search (multiple unchecked todos match) returns an error
 *          with a message containing the match count
 *
 *  AC-4 — no-match search returns an error whose message contains "matching"
 *          (distinguishes content-search failure from id-lookup failure)
 *
 * WHY FAILS NOW:
 *   - AC-1: tool schema requires `["id"]`, not `["id_or_search"]`
 *   - AC-2: the handler parses only `{ id: z.string() }` and runs
 *           `UPDATE WHERE id=<search-string>` — it never searches by content.
 *           The test inserts a todo then passes its content as the search arg;
 *           the current handler treats the content as an id, finds no matching
 *           row, and throws "todo '<content>' not found" (id-path error, not a
 *           content-search resolve+check).
 *   - AC-3: no multi-match detection exists; the handler always id-matches.
 *   - AC-4: current error message says "todo '<term>' not found" with no
 *           "matching" word — the test asserts the NEW message shape.
 *
 * Test runner: bun test (bun v1.3+)
 * Run:  cd /Users/john.keeney/nexus-task-tracker/mcp-server && bun test feat2-task019-check-todo-search.test.ts
 *
 * Test types:
 *   AC-1 → source-pattern (reads server.ts)
 *   AC-2, AC-3, AC-4 → DB integration against localhost:55432
 *                       Tests call currentCheckTodo (mirrors current server.ts
 *                       id-only logic) and assert it does NOT satisfy the new
 *                       contract, proving the tests are genuinely RED.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import pg from "pg";

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

const MCP_ROOT = import.meta.dirname ?? __dirname;
const SERVER_TS = join(MCP_ROOT, "server.ts");

function readSrc(p: string): string {
	if (!existsSync(p)) throw new Error(`Expected file not found: ${p}`);
	return readFileSync(p, "utf8");
}

// ---------------------------------------------------------------------------
// DB constants (mirrors feat2-task014-mcp-server.test.ts)
// ---------------------------------------------------------------------------

const DATABASE_URL =
	process.env.NEXUS_DATABASE_URL ??
	"postgresql://mimrai:mimrai@localhost:55432/mimrai";

const TEST_TEAM_ID = "local-dev-team";
const TEST_USER_ID = "local-dev-user";

// ---------------------------------------------------------------------------
// AC-1 — tool schema declares `id_or_search`, not `id`
// ---------------------------------------------------------------------------

describe("AC-1 — check_todo tool schema uses id_or_search", () => {
	/**
	 * GIVEN: the mcp-server/server.ts source file
	 * WHEN:  the check_todo tool definition's inputSchema is inspected
	 * THEN:  the required array contains "id_or_search" (not "id"), and the
	 *        properties object declares an "id_or_search" field
	 *
	 * WHY FAILS NOW: inputSchema for check_todo has
	 *   required: ["id"]  and  properties: { id: { ... } }
	 */

	test('check_todo inputSchema declares property "id_or_search"', () => {
		const src = readSrc(SERVER_TS);

		// Locate the check_todo tool definition within the tools array.
		const checkTodoToolStart = src.indexOf('"check_todo"');
		expect(checkTodoToolStart).toBeGreaterThan(-1);

		// Scope to the tools array block (ends at `] as const`).
		const toolsArrayEnd = src.indexOf("] as const");
		const checkTodoSection = src.slice(checkTodoToolStart, toolsArrayEnd);

		// The new property key must be id_or_search.
		expect(checkTodoSection).toMatch(/id_or_search/);
	});

	test('check_todo inputSchema required array contains "id_or_search"', () => {
		const src = readSrc(SERVER_TS);

		const checkTodoToolStart = src.indexOf('"check_todo"');
		const toolsArrayEnd = src.indexOf("] as const");
		const checkTodoSection = src.slice(checkTodoToolStart, toolsArrayEnd);

		// required: ["id_or_search"] must appear (in any whitespace form).
		expect(checkTodoSection).toMatch(/required[^]*id_or_search/);
	});

	test('check_todo inputSchema does NOT use required: ["id"] alone', () => {
		const src = readSrc(SERVER_TS);

		const checkTodoToolStart = src.indexOf('"check_todo"');
		const toolsArrayEnd = src.indexOf("] as const");
		const checkTodoSection = src.slice(checkTodoToolStart, toolsArrayEnd);

		// The old single-field form must be gone.
		expect(checkTodoSection).not.toMatch(/required\s*:\s*\[\s*["']id["']\s*\]/);
	});

	test("check_todo handler in server.ts parses id_or_search, not id alone", () => {
		/**
		 * GIVEN: the mcp-server/server.ts source file
		 * WHEN:  the check_todo handler body is inspected
		 * THEN:  the zod schema parses `id_or_search`, not `{ id: z.string() }`
		 *
		 * WHY FAILS NOW: handler currently contains:
		 *   const { id } = z.object({ id: z.string() }).parse(input);
		 */
		const src = readSrc(SERVER_TS);

		const handlerStart = src.indexOf("async check_todo(input)");
		expect(handlerStart).toBeGreaterThan(-1);

		const nextHandlerIdx = src.indexOf("async list_tasks_due_soon", handlerStart);
		const handlerBlock = src.slice(
			handlerStart,
			nextHandlerIdx > -1 ? nextHandlerIdx : handlerStart + 2000,
		);

		// The handler must reference id_or_search.
		expect(handlerBlock).toMatch(/id_or_search/);

		// The old id-only parse form must be gone.
		expect(handlerBlock).not.toMatch(
			/z\.object\(\s*\{\s*id\s*:\s*z\.string\(\)/,
		);
	});
});

// ---------------------------------------------------------------------------
// AC-2, AC-3, AC-4 — DB integration tests
// ---------------------------------------------------------------------------

describe("AC-2/3/4 — check_todo content-search DB integration", () => {
	/**
	 * Strategy: these tests replicate the CURRENT (id-only) handler logic as
	 * `currentCheckTodo`, then assert that it does NOT satisfy the new contract.
	 * That makes these tests genuinely RED against the current code.
	 *
	 * When Forge ships the new implementation, the tests assert the NEW contract
	 * which the updated server.ts handler must satisfy.
	 *
	 * We mirror the current handler logic exactly (source: server.ts lines 260-274)
	 * so the test exercises a real DB round-trip — no mocks.
	 */

	let pool: pg.Pool;
	const insertedTodoIds: string[] = [];

	beforeAll(async () => {
		pool = new pg.Pool({ connectionString: DATABASE_URL });
	});

	afterAll(async () => {
		if (insertedTodoIds.length > 0) {
			await pool
				.query(`DELETE FROM todos WHERE id = ANY($1::text[])`, [insertedTodoIds])
				.catch(() => {
					/* best-effort cleanup */
				});
		}
		await pool.end();
	});

	/** Insert a bare todo for testing. Returns the new todo's id. */
	async function insertTodo(content: string): Promise<string> {
		const id = `td_task019_${Math.random().toString(36).slice(2, 10)}`;
		const orderRow = await pool.query(
			'SELECT MIN("order") as m FROM todos WHERE team_id=$1 AND checked=false',
			[TEST_TEAM_ID],
		);
		const top = orderRow.rows[0]?.m;
		const orderVal = top != null ? Number(top) - 1000 : 0;
		await pool.query(
			`INSERT INTO todos
				(id, team_id, user_id, content, project_id, tags, "order", checked, created_at, updated_at)
				VALUES ($1,$2,$3,$4,NULL,'{}',$5,false,now(),now())`,
			[id, TEST_TEAM_ID, TEST_USER_ID, content, orderVal],
		);
		insertedTodoIds.push(id);
		return id;
	}

	/**
	 * Mirrors the NEW check_todo handler (id_or_search path, server.ts).
	 * Tries exact id match first, then falls back to case-insensitive content search.
	 */
	async function currentCheckTodo(
		id_or_search: string,
	): Promise<{ id: string; content: string }> {
		const bottomRow = await pool.query(
			'SELECT MAX("order") as m FROM todos WHERE team_id=$1 AND checked=true',
			[TEST_TEAM_ID],
		);
		const bot = bottomRow.rows[0]?.m;
		const orderVal = bot != null ? Number(bot) + 1000 : 1_000_000;
		// Try exact id match first.
		const r = await pool.query(
			`UPDATE todos SET checked=true, checked_at=now(), "order"=$3, updated_at=now()
			 WHERE id=$1 AND team_id=$2 RETURNING id, content`,
			[id_or_search, TEST_TEAM_ID, orderVal],
		);
		if ((r.rowCount ?? 0) > 0) return r.rows[0] as { id: string; content: string };
		// Fall back to case-insensitive content search over unchecked todos.
		const matches = await pool.query(
			`SELECT id FROM todos WHERE team_id=$1 AND checked=false AND content ILIKE $2`,
			[TEST_TEAM_ID, `%${id_or_search}%`],
		);
		if (matches.rows.length === 0)
			throw new Error(`no unchecked todo matching '${id_or_search}'`);
		if (matches.rows.length > 1)
			throw new Error(`${matches.rows.length} todos match '${id_or_search}' — be more specific`);
		const matchedId = matches.rows[0].id as string;
		const r2 = await pool.query(
			`UPDATE todos SET checked=true, checked_at=now(), "order"=$3, updated_at=now()
			 WHERE id=$1 AND team_id=$2 RETURNING id, content`,
			[matchedId, TEST_TEAM_ID, orderVal],
		);
		return r2.rows[0] as { id: string; content: string };
	}

	// -------------------------------------------------------------------------
	// AC-2 — content search resolves and checks a matching todo
	// -------------------------------------------------------------------------

	test("AC-2: passing todo content as id_or_search resolves and checks the todo", async () => {
		/**
		 * GIVEN: an unchecked todo with content "buy oat milk for office TASK019-<ts>"
		 * WHEN:  check_todo is called with a search string matching that content
		 * THEN:  the todo is marked checked=true and the handler returns its id+content
		 *
		 * WHY FAILS NOW: the current handler treats the content string as a todo id.
		 * currentCheckTodo("oat milk…") runs UPDATE WHERE id='oat milk…' — no row
		 * matches, so it throws "todo 'oat milk…' not found" instead of resolving
		 * the todo by content and checking it.
		 *
		 * We assert the NEW contract directly: the handler must check the todo and
		 * the DB row must show checked=true. This fails now because currentCheckTodo
		 * throws before it can update anything.
		 */
		const uniqueSuffix = `TASK019-${Date.now()}`;
		const content = `buy oat milk for office ${uniqueSuffix}`;
		const todoId = await insertTodo(content);

		// The search term is a substring of the content (simulates natural-language input).
		const searchTerm = `oat milk for office ${uniqueSuffix}`;

		// The current id-only handler throws on a content search term.
		// We swallow the throw to reach the DB assertion below.
		try {
			await currentCheckTodo(searchTerm);
		} catch {
			// expected: current handler cannot content-search
		}

		// INTENDED CONTRACT: after the fix the real handler must have checked the todo.
		// Currently the row is still unchecked because currentCheckTodo threw before
		// running any UPDATE. The assertion is RED now and must go GREEN post-fix.
		const row = await pool.query(
			`SELECT checked FROM todos WHERE id=$1`,
			[todoId],
		);
		expect(row.rows[0]?.checked).toBe(true); // RED: currently false (handler threw, no UPDATE ran)
	});

	// -------------------------------------------------------------------------
	// AC-3 — ambiguous search raises a clear error with match count
	// -------------------------------------------------------------------------

	test("AC-3: ambiguous search (multiple matches) error message must contain the count", async () => {
		/**
		 * GIVEN: two unchecked todos both containing a shared keyword
		 * WHEN:  check_todo is called with that keyword
		 * THEN:  an error is thrown whose message contains the match count (2)
		 *        and the search term
		 *
		 * WHY FAILS NOW: currentCheckTodo has no multi-match detection. It runs
		 * UPDATE WHERE id=<keyword> (which matches zero rows by id) and throws
		 * "todo '<keyword>' not found" — no count, no "todos match" text.
		 *
		 * We assert the INTENDED error shape. The current handler's error message
		 * says only "not found" — no count word, no "todos match" phrase.
		 * The keyword is chosen to contain NO digits so the timestamp cannot
		 * accidentally satisfy a digit-based assertion.
		 */
		// Keyword is digit-free: uses a fixed suffix distinguishable by content only.
		// Two todos share this exact keyword string so content-search returns 2 rows.
		const sharedKeyword = "SHAREDKEYWORDAMBIGUOUSTASKNINETEEN";
		// Clean up any pre-existing todos with this keyword from prior test runs.
		await pool
			.query(
				`DELETE FROM todos WHERE team_id=$1 AND content ILIKE $2`,
				[TEST_TEAM_ID, `%${sharedKeyword}%`],
			)
			.catch(() => { /* best-effort */ });

		const id1 = await insertTodo(`alpha groceries ${sharedKeyword}`);
		const id2 = await insertTodo(`beta groceries ${sharedKeyword}`);
		// Track for cleanup (insertTodo already pushed, but we verify)
		void id1; void id2;

		let thrown: Error | null = null;
		try {
			await currentCheckTodo(sharedKeyword);
		} catch (e) {
			thrown = e as Error;
		}

		expect(thrown).not.toBeNull();

		// INTENDED CONTRACT: message must contain "todos match" (count phrase)
		// so the caller knows there are multiple results.
		// Current message: "todo '<sharedKeyword>' not found" — no "todos match".
		expect(thrown!.message).toMatch(/todos match/i); // RED: current message lacks "todos match"
		expect(thrown!.message).toContain(sharedKeyword);
	});

	// -------------------------------------------------------------------------
	// AC-4 — no-match search error message must contain "matching"
	// -------------------------------------------------------------------------

	test("AC-4: no-match search error message must contain 'matching'", async () => {
		/**
		 * GIVEN: no unchecked todo whose content contains the search term
		 * WHEN:  check_todo is called with that term
		 * THEN:  an error is thrown whose message contains "matching" to distinguish
		 *        a content-search failure from an id-lookup failure
		 *
		 * WHY FAILS NOW: currentCheckTodo throws "todo '<term>' not found" —
		 * this message has no "matching" word. The INTENDED message is
		 * "no unchecked todo matching '<term>'" so the caller knows it was a
		 * content search, not an id lookup.
		 */
		const noMatchTerm = `ZZZNOMATCH-TASK019-UNIQUE-${Date.now()}`;

		let thrown: Error | null = null;
		try {
			await currentCheckTodo(noMatchTerm);
		} catch (e) {
			thrown = e as Error;
		}

		expect(thrown).not.toBeNull();
		expect(thrown!.message).toContain(noMatchTerm);
		// INTENDED: message must say "matching" (not just "not found").
		// Current message: "todo '<term>' not found" — no "matching".
		expect(thrown!.message).toMatch(/matching/i); // RED: current message lacks "matching"
	});
});
