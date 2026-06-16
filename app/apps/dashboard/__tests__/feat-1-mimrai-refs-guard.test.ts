/// <reference path="./vitest-globals.d.ts" />
/**
 * FEAT-001 Phase-0 guard: zero residual MIMRAI_LOCAL_DEV + absolute mimrai paths
 *
 * RED stub — currently FAILS because those refs still exist pre-Phase-0.
 * Goes GREEN automatically once Forge completes the atomic rename commit.
 *
 * Excluded from scan:
 *   - node_modules, .next, dist, build, .turbo, bun.lock, .pre-nexus* dirs
 *   - mimrai-pg-data docker volume name (intentionally kept)
 *   - @mimir/ package scope (intentionally kept)
 *
 * Patterns matched:
 *   (a) MIMRAI_LOCAL_DEV  or  NEXT_PUBLIC_MIMRAI_LOCAL_DEV  (exact token)
 *   (b) /Users/john.keeney/mimrai  path prefix (but NOT mimrai-pg-data or @mimir/)
 */

import { describe, test, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

// ---------------------------------------------------------------------------
// Patterns
// ---------------------------------------------------------------------------

/** Matches MIMRAI_LOCAL_DEV or NEXT_PUBLIC_MIMRAI_LOCAL_DEV as exact env-var tokens */
const ENV_VAR_RE =
  /(?<![A-Z0-9_])(?:NEXT_PUBLIC_)?MIMRAI_LOCAL_DEV(?![A-Z0-9_])/;

/**
 * Matches the absolute path prefix /Users/john.keeney/mimrai (with or without
 * a trailing slash or path segment) but NOT:
 *   - mimrai-pg-data  (docker volume, intentionally kept)
 *   - @mimir/         (package scope, intentionally kept)
 *
 * Strategy: match /Users/john.keeney/mimrai and then require the next char (if
 * any) to be '/' or end-of-token — but explicitly exclude the "-pg-data" suffix.
 */
const ABS_PATH_RE =
  /\/Users\/john\.keeney\/mimrai(?!-pg-data)(?:[\/\s"']|$)/;

// ---------------------------------------------------------------------------
// Directories / files to exclude from the walk
// ---------------------------------------------------------------------------

const EXCLUDED_DIRS = new Set([
  "node_modules",
  ".next",
  "dist",
  "build",
  ".turbo",
]);

/** Also skip any directory whose name starts with .pre-nexus */
function isExcludedDir(name: string): boolean {
  return EXCLUDED_DIRS.has(name) || name.startsWith(".pre-nexus");
}

/** Skip binary / lock files that are not text source */
const EXCLUDED_FILE_NAMES = new Set(["bun.lock", "bun.lockb", "yarn.lock"]);

// ---------------------------------------------------------------------------
// Recursive walk
// ---------------------------------------------------------------------------

function* walkText(dir: string, selfPath: string): Generator<string> {
  let entries: import("node:fs").Dirent<string>[];
  try {
    entries = readdirSync(dir, { withFileTypes: true, encoding: "utf8" });
  } catch {
    return;
  }

  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (isExcludedDir(entry.name)) continue;
      yield* walkText(join(dir, entry.name), selfPath);
    } else if (entry.isFile()) {
      if (EXCLUDED_FILE_NAMES.has(entry.name)) continue;
      const filePath = join(dir, entry.name);
      if (filePath === selfPath) continue; // never scan this guard test itself
      yield filePath;
    }
  }
}

// ---------------------------------------------------------------------------
// Scan
// ---------------------------------------------------------------------------

/**
 * Returns files that contain at least one match of the given regex.
 * Reads files as UTF-8 text; skips files that cannot be decoded (binary).
 */
function scanForPattern(
  rootDir: string,
  pattern: RegExp,
): Array<{ file: string; lines: number[] }> {
  const hits: Array<{ file: string; lines: number[] }> = [];

  for (const filePath of walkText(rootDir, THIS_FILE)) {
    let content: string;
    try {
      content = readFileSync(filePath, "utf8");
    } catch {
      continue; // skip unreadable / binary files
    }

    // Quick pre-check before splitting lines
    if (!pattern.test(content)) continue;

    const matchedLines: number[] = [];
    content.split("\n").forEach((line, idx) => {
      if (pattern.test(line)) matchedLines.push(idx + 1);
    });

    if (matchedLines.length > 0) {
      hits.push({ file: filePath, lines: matchedLines });
    }
  }

  return hits;
}

// ---------------------------------------------------------------------------
// Root of the monorepo app/ directory (two levels up from this file's dir)
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app
const APP_ROOT = join(
  import.meta.dirname ?? __dirname,
  "..",  // dashboard
  "..",  // apps
  "..",  // app
);

/** Absolute path of this test file — always excluded from the scan */
const THIS_FILE = join(
  import.meta.dirname ?? __dirname,
  "feat-1-mimrai-refs-guard.test.ts",
);

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("FEAT-001 Phase-0 guard: residual mimrai refs", () => {
  test(
    "zero MIMRAI_LOCAL_DEV / NEXT_PUBLIC_MIMRAI_LOCAL_DEV references in app/",
    () => {
      const hits = scanForPattern(APP_ROOT, ENV_VAR_RE);

      const message =
        hits.length === 0
          ? "No matches"
          : `Found ${hits.length} file(s) with MIMRAI_LOCAL_DEV references:\n` +
            hits
              .map(
                ({ file, lines }) =>
                  `  ${relative(APP_ROOT, file)} (line${lines.length > 1 ? "s" : ""} ${lines.join(", ")})`,
              )
              .join("\n");

      expect(hits, message).toHaveLength(0);
    },
  );

  test(
    "zero /Users/john.keeney/mimrai absolute path references in app/",
    () => {
      const hits = scanForPattern(APP_ROOT, ABS_PATH_RE);

      const message =
        hits.length === 0
          ? "No matches"
          : `Found ${hits.length} file(s) with /Users/john.keeney/mimrai path references:\n` +
            hits
              .map(
                ({ file, lines }) =>
                  `  ${relative(APP_ROOT, file)} (line${lines.length > 1 ? "s" : ""} ${lines.join(", ")})`,
              )
              .join("\n");

      expect(hits, message).toHaveLength(0);
    },
  );
});
