/**
 * TASK-021 — Knowledge vault backend: real DB integration tests.
 *
 * Two acceptance criteria:
 *
 *  GWT-A  Scan link-population
 *  -----------------------------------------------------------------------
 *  GIVEN  a vault note whose body contains [[Resolvable]] and [[Missing]]
 *         where "Resolvable" matches an existing note's name
 *         and   "Missing"    matches NO note in the vault
 *  WHEN   the wiki-link indexing logic runs (mirrors scanVault's link phase)
 *  THEN   knowledge_links has a row for [[Resolvable]] with to_note_id set
 *         AND a row for [[Missing]] with to_note_id = NULL
 *
 *  GWT-B  FTS ranking: title-weight (A) > body-weight (B)
 *  -----------------------------------------------------------------------
 *  GIVEN  two notes:
 *           note-title — has "quartz" only in the name/title column
 *           note-body  — has "quartz" only in the body content
 *  WHEN   a FTS query `ts_rank(content_fts, to_tsquery('quartz'))` runs
 *  THEN   note-title rank > note-body rank  (weight A beats weight B)
 *
 * Real Postgres (nexus-postgres, host=localhost:55432, DB=mimrai).
 * Uses drizzle-orm/node-postgres (already a dep of @nexus-app/db).
 * No mocking. Fixture rows are cleaned up in afterAll.
 *
 * Run from app/apps/api/: bun test src/__tests__/task-021-knowledge-db-integration.test.ts
 */

import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { createHash } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { sql } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import { extractLinks, resolveLinks } from "../lib/wiki-link-parser";

// ---------------------------------------------------------------------------
// DB connection — real postgres, no mock.
// drizzle-orm/node-postgres is already a transitive dep of @nexus-app/db.
// ---------------------------------------------------------------------------

const DB_URL =
	process.env.DATABASE_URL ??
	"postgresql://mimrai:mimrai@localhost:55432/mimrai";

const db = drizzle(DB_URL);

// ---------------------------------------------------------------------------
// Fixture ID constants (prefixed so cleanup is safe and precise)
// ---------------------------------------------------------------------------

const TEAM_ID = "local-dev-team"; // pre-seeded team in nexus-postgres
const VAULT_ID = "kv-task021-test";
const NOTE_SOURCE_ID = "kn-task021-source";
const NOTE_RESOLVABLE_ID = "kn-task021-resolvable";
const NOTE_TITLE_ID = "kn-task021-fts-title";
const NOTE_BODY_ID = "kn-task021-fts-body";

// A real temp directory so root_path is an absolute path (stored in DB).
const VAULT_ROOT = mkdtempSync(join(tmpdir(), "task021-vault-"));

// Collect link IDs for afterAll targeted cleanup.
const insertedLinkIds: string[] = [];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sha(s: string): string {
	return createHash("sha256").update(s).digest("hex");
}

function linkId(fromNoteId: string, i: number, linkText: string): string {
	return `kl-${createHash("sha256").update(`${fromNoteId}:${i}:${linkText}`).digest("hex").slice(0, 16)}`;
}

// ---------------------------------------------------------------------------
// beforeAll: seed fixture vault + notes
// ---------------------------------------------------------------------------

beforeAll(async () => {
	// Vault
	await db.execute(
		sql`INSERT INTO knowledge_vaults (id, team_id, label, root_path, is_default)
        VALUES (${VAULT_ID}, ${TEAM_ID}, ${"task-021-test-vault"}, ${VAULT_ROOT}, false)
        ON CONFLICT (id) DO NOTHING`,
	);

	// "Resolvable" note — the link target, name matches [[Resolvable]]
	const resolvableBody = "# Resolvable\n\nThis note is the link target.";
	await db.execute(
		sql`INSERT INTO knowledge_notes
          (id, vault_id, relative_path, absolute_path, name, content, file_sha)
        VALUES (
          ${NOTE_RESOLVABLE_ID}, ${VAULT_ID},
          ${"Resolvable.md"}, ${join(VAULT_ROOT, "Resolvable.md")},
          ${"Resolvable"}, ${resolvableBody}, ${sha(resolvableBody)}
        )
        ON CONFLICT (id) DO NOTHING`,
	);

	// "Source" note — contains [[Resolvable]] and [[Missing]]
	const sourceBody =
		"Some content that links to [[Resolvable]] and also [[Missing]].";
	await db.execute(
		sql`INSERT INTO knowledge_notes
          (id, vault_id, relative_path, absolute_path, name, content, file_sha)
        VALUES (
          ${NOTE_SOURCE_ID}, ${VAULT_ID},
          ${"source.md"}, ${join(VAULT_ROOT, "source.md")},
          ${"source"}, ${sourceBody}, ${sha(sourceBody)}
        )
        ON CONFLICT (id) DO NOTHING`,
	);

	// FTS title-weight note — "quartz" ONLY in name column (→ tsvector weight A)
	const titleBody = "Plain body with no special term here.";
	await db.execute(
		sql`INSERT INTO knowledge_notes
          (id, vault_id, relative_path, absolute_path, name, content, file_sha)
        VALUES (
          ${NOTE_TITLE_ID}, ${VAULT_ID},
          ${"quartz-title.md"}, ${join(VAULT_ROOT, "quartz-title.md")},
          ${"quartz title note"}, ${titleBody}, ${sha(titleBody)}
        )
        ON CONFLICT (id) DO NOTHING`,
	);

	// FTS body-weight note — "quartz" ONLY in content (→ tsvector weight B)
	const bodyContent =
		"# plain name\n\nThis body discusses quartz crystals extensively.";
	await db.execute(
		sql`INSERT INTO knowledge_notes
          (id, vault_id, relative_path, absolute_path, name, content, file_sha)
        VALUES (
          ${NOTE_BODY_ID}, ${VAULT_ID},
          ${"quartz-body.md"}, ${join(VAULT_ROOT, "quartz-body.md")},
          ${"plain name"}, ${bodyContent}, ${sha(bodyContent)}
        )
        ON CONFLICT (id) DO NOTHING`,
	);
});

// ---------------------------------------------------------------------------
// afterAll: clean up all fixture rows
// ---------------------------------------------------------------------------

afterAll(async () => {
	// Delete links we inserted by ID
	if (insertedLinkIds.length > 0) {
		await db.execute(
			sql`DELETE FROM knowledge_links WHERE id = ANY(ARRAY[${sql.join(
				insertedLinkIds.map((id) => sql`${id}`),
				sql`, `,
			)}])`,
		);
	}
	// Delete fixture notes (ON DELETE CASCADE will also remove their links)
	await db.execute(
		sql`DELETE FROM knowledge_notes WHERE id = ANY(ARRAY[
        ${sql.raw(`'${NOTE_SOURCE_ID}','${NOTE_RESOLVABLE_ID}','${NOTE_TITLE_ID}','${NOTE_BODY_ID}'`)}
      ])`,
	);
	// Delete vault (CASCADE removes remaining notes)
	await db.execute(sql`DELETE FROM knowledge_vaults WHERE id = ${VAULT_ID}`);
	await db.$client.end();
});

// ---------------------------------------------------------------------------
// GWT-A — Scan link-population
// ---------------------------------------------------------------------------

describe("GWT-A — scan link-population: [[Resolvable]] resolved, [[Missing]] null", () => {
	test("extractLinks + resolveLinks on source note body produces two links with correct resolution", async () => {
		// Fetch the seeded source note content from the live DB
		const noteRows = await db.execute<{ id: string; content: string }>(
			sql`SELECT id, content FROM knowledge_notes WHERE id = ${NOTE_SOURCE_ID}`,
		);
		expect(noteRows.rows).toHaveLength(1);
		const note = noteRows.rows[0];
		expect(note).toBeDefined();

		// Fetch all note refs for this vault (same as scanVault does)
		const refRows = await db.execute<{ id: string; relative_path: string }>(
			sql`SELECT id, relative_path FROM knowledge_notes WHERE vault_id = ${VAULT_ID}`,
		);
		const allNoteRefs = refRows.rows.map((r) => ({
			id: r.id,
			relativePath: r.relative_path,
		}));

		// WHEN: run extractLinks + resolveLinks (same logic as scanVault)
		const parsed = extractLinks(note!.content ?? "");
		const resolved = resolveLinks(parsed, allNoteRefs);

		// THEN: two links found
		expect(parsed).toHaveLength(2);
		const linkTexts = parsed.map((p) => p.linkText);
		expect(linkTexts).toContain("Resolvable");
		expect(linkTexts).toContain("Missing");

		const resolvableLink = resolved.find((r) => r.linkText === "Resolvable");
		const missingLink = resolved.find((r) => r.linkText === "Missing");

		// [[Resolvable]] resolves to NOTE_RESOLVABLE_ID (basename match)
		expect(resolvableLink).toBeDefined();
		expect(resolvableLink!.toNoteId).toBe(NOTE_RESOLVABLE_ID);

		// [[Missing]] resolves to null (no note with that name)
		expect(missingLink).toBeDefined();
		expect(missingLink!.toNoteId).toBeNull();
	});

	test("knowledge_links rows are persisted: [[Resolvable]] has to_note_id set, [[Missing]] has to_note_id null", async () => {
		// Clear any stale links for the source note first
		await db.execute(
			sql`DELETE FROM knowledge_links WHERE from_note_id = ${NOTE_SOURCE_ID}`,
		);

		// Fetch source note content + vault refs from real DB
		const noteRows = await db.execute<{ id: string; content: string }>(
			sql`SELECT id, content FROM knowledge_notes WHERE id = ${NOTE_SOURCE_ID}`,
		);
		const note = noteRows.rows[0];
		expect(note).toBeDefined();

		const refRows = await db.execute<{ id: string; relative_path: string }>(
			sql`SELECT id, relative_path FROM knowledge_notes WHERE vault_id = ${VAULT_ID}`,
		);
		const allNoteRefs = refRows.rows.map((r) => ({
			id: r.id,
			relativePath: r.relative_path,
		}));

		// Run link-indexing (mirrors scanVault exactly)
		const parsed = extractLinks(note!.content ?? "");
		const resolved = resolveLinks(parsed, allNoteRefs);

		for (let i = 0; i < resolved.length; i++) {
			const item = resolved[i]!;
			const id = linkId(NOTE_SOURCE_ID, i, item.linkText);
			insertedLinkIds.push(id);
			await db.execute(
				sql`INSERT INTO knowledge_links (id, from_note_id, to_note_id, link_text)
            VALUES (${id}, ${NOTE_SOURCE_ID}, ${item.toNoteId ?? null}, ${item.linkText})
            ON CONFLICT (id) DO NOTHING`,
			);
		}

		// THEN: assert on persisted DB rows
		const linkRows = await db.execute<{
			id: string;
			from_note_id: string;
			to_note_id: string | null;
			link_text: string;
		}>(
			sql`SELECT id, from_note_id, to_note_id, link_text
          FROM knowledge_links
          WHERE from_note_id = ${NOTE_SOURCE_ID}
          ORDER BY link_text`,
		);

		expect(linkRows.rows).toHaveLength(2);

		const missingRow = linkRows.rows.find((r) => r.link_text === "Missing");
		const resolvableRow = linkRows.rows.find(
			(r) => r.link_text === "Resolvable",
		);

		// [[Missing]] — to_note_id must be null
		expect(missingRow).toBeDefined();
		expect(missingRow!.to_note_id).toBeNull();

		// [[Resolvable]] — to_note_id must equal NOTE_RESOLVABLE_ID
		expect(resolvableRow).toBeDefined();
		expect(resolvableRow!.to_note_id).toBe(NOTE_RESOLVABLE_ID);
	});
});

// ---------------------------------------------------------------------------
// GWT-B — FTS ranking: title-weight (A) > body-weight (B)
// ---------------------------------------------------------------------------

describe("GWT-B — FTS ts_rank: title-weight (A) beats body-weight (B) for same search term", () => {
	/**
	 * The generated column DDL is:
	 *   setweight(to_tsvector('english', COALESCE(name, '')), 'A') ||
	 *   setweight(to_tsvector('english', COALESCE(content, '')), 'B')
	 *
	 * So "quartz" in name → weight A → higher ts_rank.
	 * "quartz" in content → weight B → lower ts_rank.
	 */

	test("note with 'quartz' in title ranks higher than note with 'quartz' only in body", async () => {
		const result = await db.execute<{ id: string; rank: string }>(
			sql`SELECT id,
              ts_rank(content_fts, to_tsquery('english', 'quartz'))::text AS rank
          FROM knowledge_notes
          WHERE id IN (${NOTE_TITLE_ID}, ${NOTE_BODY_ID})
            AND content_fts @@ to_tsquery('english', 'quartz')
          ORDER BY rank DESC`,
		);

		expect(result.rows).toHaveLength(2);

		const titleRow = result.rows.find((r) => r.id === NOTE_TITLE_ID);
		const bodyRow = result.rows.find((r) => r.id === NOTE_BODY_ID);

		expect(titleRow).toBeDefined();
		expect(bodyRow).toBeDefined();

		const titleRank = Number.parseFloat(titleRow!.rank);
		const bodyRank = Number.parseFloat(bodyRow!.rank);

		// Weight A (title) must produce strictly higher rank than weight B (body)
		expect(titleRank).toBeGreaterThan(bodyRank);
	});

	test("FTS results ordered by ts_rank DESC places title-match note first", async () => {
		const result = await db.execute<{ id: string; rank: string }>(
			sql`SELECT id,
              ts_rank(content_fts, to_tsquery('english', 'quartz'))::text AS rank
          FROM knowledge_notes
          WHERE vault_id = ${VAULT_ID}
            AND content_fts @@ to_tsquery('english', 'quartz')
          ORDER BY ts_rank(content_fts, to_tsquery('english', 'quartz')) DESC`,
		);

		expect(result.rows.length).toBeGreaterThanOrEqual(2);
		// First result (highest rank) must be the title-match note
		expect(result.rows[0]!.id).toBe(NOTE_TITLE_ID);
	});
});
