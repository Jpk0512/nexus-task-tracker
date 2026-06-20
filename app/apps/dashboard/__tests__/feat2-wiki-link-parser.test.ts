/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-012 — feat2 unit tests for the wiki-link parser.
 *
 * Pure, regression-critical logic. No I/O, no DB, no network.
 *
 * Acceptance criteria (GWT format):
 *
 *  AC1 — extractLinks finds all [[Note]] occurrences in content and ignores
 *         non-link text.
 *  AC2 — resolveLinks tie-break (DEC-007):
 *         (a) full relative-path match wins over basename match,
 *         (b) basename match with SHALLOWEST path winning on ties,
 *         (c) case-insensitive basename,
 *         (d) unresolved [[missing]] yields null toNoteId.
 *  AC3 — linkText is preserved verbatim in both extractLinks and resolveLinks.
 *
 * Run: rtk vitest run app/apps/dashboard/__tests__/feat2-wiki-link-parser.test.ts
 */

import { describe, expect, test } from "vitest";
import type { NoteRef, ParsedLink } from "../fixtures/wiki-link-parser";
import { extractLinks, resolveLinks } from "../fixtures/wiki-link-parser";

// ---------------------------------------------------------------------------
// AC1 — extractLinks identifies all [[...]] occurrences
// ---------------------------------------------------------------------------

describe("AC1 — extractLinks finds all wiki-link occurrences", () => {
	/**
	 * GIVEN content that contains two [[...]] links separated by prose
	 * WHEN extractLinks is called
	 * THEN both links are returned and non-link text is not included
	 */
	test("returns a ParsedLink for every [[...]] pattern in the content", () => {
		const content =
			"This is some prose [[Alpha]] more text [[Beta]] and a tail.";
		const result = extractLinks(content);
		expect(result).toHaveLength(2);
		expect(result[0]!.linkText).toBe("Alpha");
		expect(result[1]!.linkText).toBe("Beta");
	});

	/**
	 * GIVEN content with no [[...]] patterns
	 * WHEN extractLinks is called
	 * THEN an empty array is returned
	 */
	test("returns an empty array when content has no wiki-link syntax", () => {
		const result = extractLinks("Just a plain sentence with no links here.");
		expect(result).toHaveLength(0);
	});

	/**
	 * GIVEN content with three back-to-back [[...]] links
	 * WHEN extractLinks is called
	 * THEN all three links are returned in order
	 */
	test("finds multiple links including three consecutive occurrences", () => {
		const content = "[[One]][[Two]][[Three]]";
		const result = extractLinks(content);
		expect(result).toHaveLength(3);
		expect(result[0]!.linkText).toBe("One");
		expect(result[1]!.linkText).toBe("Two");
		expect(result[2]!.linkText).toBe("Three");
	});

	/**
	 * GIVEN content with a single-bracket occurrence [like this] (not a wiki link)
	 * WHEN extractLinks is called
	 * THEN it is NOT included in results (only [[ ]] syntax qualifies)
	 */
	test("does not capture single-bracket [text] as a link", () => {
		const content = "[NotALink] and [[RealLink]]";
		const result = extractLinks(content);
		expect(result).toHaveLength(1);
		expect(result[0]!.linkText).toBe("RealLink");
	});

	/**
	 * GIVEN content where the link text has surrounding whitespace inside [[ ]]
	 * WHEN extractLinks is called
	 * THEN the linkText is trimmed
	 */
	test("trims surrounding whitespace inside the [[...]] brackets", () => {
		const content = "[[  SpacedNote  ]]";
		const result = extractLinks(content);
		expect(result).toHaveLength(1);
		expect(result[0]!.linkText).toBe("SpacedNote");
	});

	/**
	 * GIVEN empty content
	 * WHEN extractLinks is called
	 * THEN an empty array is returned
	 */
	test("returns an empty array for an empty string", () => {
		const result = extractLinks("");
		expect(result).toHaveLength(0);
	});
});

// ---------------------------------------------------------------------------
// AC2(a) — resolveLinks: full relative-path match wins
// ---------------------------------------------------------------------------

describe("AC2(a) — full relative-path match wins over basename match", () => {
	/**
	 * GIVEN two notes whose basenames are identical ("A.md") at different depths
	 *   - notes/A.md  (id: "shallow")
	 *   - deep/nested/A.md (id: "deep")
	 * AND a link with text "notes/A" (matches the relative path of the shallow note)
	 * WHEN resolveLinks is called
	 * THEN the full-path match ("shallow") wins, not the shallowest basename match
	 */
	test("full-path match takes priority over basename match", () => {
		const notes: NoteRef[] = [
			{ id: "shallow", relativePath: "notes/A.md" },
			{ id: "deep", relativePath: "deep/nested/A.md" },
		];
		const links: ParsedLink[] = [{ linkText: "notes/A" }];
		const result = resolveLinks(links, notes);
		expect(result).toHaveLength(1);
		expect(result[0]!.toNoteId).toBe("shallow");
		expect(result[0]!.linkText).toBe("notes/A");
	});

	/**
	 * GIVEN a note at "root/Alpha.md" (id: "root-alpha")
	 * AND a link with text "root/Alpha" (the full path without extension)
	 * WHEN resolveLinks is called
	 * THEN the resolved id is "root-alpha" (full-path match)
	 */
	test("full-path match works for multi-segment paths (strips .md extension)", () => {
		const notes: NoteRef[] = [
			{ id: "root-alpha", relativePath: "root/Alpha.md" },
			{ id: "other-alpha", relativePath: "other/deep/path/Alpha.md" },
		];
		const links: ParsedLink[] = [{ linkText: "root/Alpha" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe("root-alpha");
	});
});

// ---------------------------------------------------------------------------
// AC2(b) — resolveLinks: shallowest path wins basename tie
// ---------------------------------------------------------------------------

describe("AC2(b) — shallowest path wins on basename tie", () => {
	/**
	 * GIVEN two notes with the same basename "Beta.md" at different depths
	 *   - docs/Beta.md  (shorter relative path → shallower)
	 *   - docs/sub/dir/Beta.md  (longer relative path → deeper)
	 * AND a link [[Beta]] that matches both by basename
	 * WHEN resolveLinks is called
	 * THEN the shallower note ("docs/Beta.md") wins
	 */
	test("picks the shallowest (shortest relativePath) when multiple basenames match", () => {
		const notes: NoteRef[] = [
			{ id: "deep-beta", relativePath: "docs/sub/dir/Beta.md" },
			{ id: "shallow-beta", relativePath: "docs/Beta.md" },
		];
		const links: ParsedLink[] = [{ linkText: "Beta" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe("shallow-beta");
	});

	/**
	 * GIVEN three notes with the same basename "Gamma.md" at depths 1, 2, 3
	 * WHEN resolveLinks is called with [[Gamma]]
	 * THEN the depth-1 note wins
	 */
	test("picks the depth-1 note when three matches exist at depths 1, 2, 3", () => {
		const notes: NoteRef[] = [
			{ id: "d3", relativePath: "a/b/c/Gamma.md" },
			{ id: "d1", relativePath: "Gamma.md" },
			{ id: "d2", relativePath: "a/Gamma.md" },
		];
		const links: ParsedLink[] = [{ linkText: "Gamma" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe("d1");
	});

	/**
	 * GIVEN exactly one note with basename "Unique.md"
	 * WHEN resolveLinks is called with [[Unique]]
	 * THEN it resolves directly (no tie-break needed)
	 */
	test("resolves directly when only one basename match exists", () => {
		const notes: NoteRef[] = [
			{ id: "only-one", relativePath: "some/path/Unique.md" },
		];
		const links: ParsedLink[] = [{ linkText: "Unique" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe("only-one");
	});
});

// ---------------------------------------------------------------------------
// AC2(c) — resolveLinks: case-insensitive basename matching
// ---------------------------------------------------------------------------

describe("AC2(c) — basename matching is case-insensitive", () => {
	/**
	 * GIVEN a note at "Notes/MyNote.md"
	 * AND a link [[mynote]] (all-lowercase)
	 * WHEN resolveLinks is called
	 * THEN the link resolves to the note (case-insensitive match)
	 */
	test("lowercase link text resolves against mixed-case basename", () => {
		const notes: NoteRef[] = [
			{ id: "note-id", relativePath: "Notes/MyNote.md" },
		];
		const links: ParsedLink[] = [{ linkText: "mynote" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe("note-id");
	});

	/**
	 * GIVEN a note at "Uppercase.md"
	 * AND a link [[UPPERCASE]] (all-caps)
	 * WHEN resolveLinks is called
	 * THEN it resolves correctly (case-insensitive)
	 */
	test("all-caps link text resolves against title-case basename", () => {
		const notes: NoteRef[] = [{ id: "upper-id", relativePath: "Uppercase.md" }];
		const links: ParsedLink[] = [{ linkText: "UPPERCASE" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe("upper-id");
	});

	/**
	 * GIVEN a note at "Mixed/Case.md"
	 * AND a link [[case]] (lowercase)
	 * WHEN resolveLinks is called
	 * THEN it resolves (case-insensitive on the .md-stripped basename)
	 */
	test("case-insensitive matching strips .md before comparing", () => {
		const notes: NoteRef[] = [{ id: "case-id", relativePath: "Mixed/Case.md" }];
		const links: ParsedLink[] = [{ linkText: "case" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe("case-id");
	});
});

// ---------------------------------------------------------------------------
// AC2(d) — resolveLinks: unresolved links yield null toNoteId
// ---------------------------------------------------------------------------

describe("AC2(d) — unresolved links produce null toNoteId", () => {
	/**
	 * GIVEN a set of notes that does not include any note matching [[missing]]
	 * WHEN resolveLinks is called
	 * THEN the toNoteId is null for that link
	 */
	test("unresolved link [[missing]] returns null toNoteId", () => {
		const notes: NoteRef[] = [{ id: "exists", relativePath: "exists.md" }];
		const links: ParsedLink[] = [{ linkText: "missing" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe(null);
	});

	/**
	 * GIVEN a link list with one resolvable and one unresolvable link
	 * WHEN resolveLinks is called
	 * THEN the resolvable link gets an id and the unresolvable one gets null
	 */
	test("mixed list: resolvable link gets id, unresolvable gets null", () => {
		const notes: NoteRef[] = [{ id: "real-id", relativePath: "RealNote.md" }];
		const links: ParsedLink[] = [
			{ linkText: "RealNote" },
			{ linkText: "GhostNote" },
		];
		const result = resolveLinks(links, notes);
		expect(result).toHaveLength(2);
		expect(result[0]!.toNoteId).toBe("real-id");
		expect(result[1]!.toNoteId).toBe(null);
	});

	/**
	 * GIVEN an empty notes list
	 * WHEN resolveLinks is called with any link
	 * THEN all toNoteIds are null
	 */
	test("all links are unresolved when the notes vault is empty", () => {
		const notes: NoteRef[] = [];
		const links: ParsedLink[] = [
			{ linkText: "Anything" },
			{ linkText: "AlsoThis" },
		];
		const result = resolveLinks(links, notes);
		expect(result[0]!.toNoteId).toBe(null);
		expect(result[1]!.toNoteId).toBe(null);
	});
});

// ---------------------------------------------------------------------------
// AC3 — linkText is preserved verbatim
// ---------------------------------------------------------------------------

describe("AC3 — linkText is preserved verbatim through extract and resolve", () => {
	/**
	 * GIVEN a wiki-link with mixed-case text [[MyNote]]
	 * WHEN extractLinks is called
	 * THEN linkText is exactly "MyNote" (not lowercased or altered)
	 */
	test("extractLinks preserves the original casing of linkText", () => {
		const result = extractLinks("See [[MyNote]] for details.");
		expect(result[0]!.linkText).toBe("MyNote");
	});

	/**
	 * GIVEN a link text that includes a path separator [[folder/SubNote]]
	 * WHEN extractLinks is called
	 * THEN linkText is the full inner content "folder/SubNote"
	 */
	test("extractLinks preserves path-separator link text verbatim", () => {
		const result = extractLinks("See [[folder/SubNote]] here.");
		expect(result[0]!.linkText).toBe("folder/SubNote");
	});

	/**
	 * GIVEN a resolved link list
	 * WHEN resolveLinks is called
	 * THEN the returned ResolvedLink.linkText equals the original ParsedLink.linkText
	 */
	test("resolveLinks preserves linkText verbatim in the returned ResolvedLink", () => {
		const notes: NoteRef[] = [{ id: "note-abc", relativePath: "Abc.md" }];
		const links: ParsedLink[] = [{ linkText: "Abc" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.linkText).toBe("Abc");
		expect(result[0]!.toNoteId).toBe("note-abc");
	});

	/**
	 * GIVEN an unresolved link with mixed-case text [[NeverFound]]
	 * WHEN resolveLinks is called
	 * THEN linkText is still "NeverFound" even though toNoteId is null
	 */
	test("resolveLinks preserves linkText even when toNoteId is null", () => {
		const notes: NoteRef[] = [];
		const links: ParsedLink[] = [{ linkText: "NeverFound" }];
		const result = resolveLinks(links, notes);
		expect(result[0]!.linkText).toBe("NeverFound");
		expect(result[0]!.toNoteId).toBe(null);
	});
});
