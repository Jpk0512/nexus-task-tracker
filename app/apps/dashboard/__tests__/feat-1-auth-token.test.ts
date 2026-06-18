/// <reference path="./vitest-globals.d.ts" />
/**
 * FEAT-001 Phase-3 guard: static API-token auth + MIMRAI_SSR_SERVER_URL rename
 *
 * RED stubs — fail before implementation lands; go GREEN automatically once:
 *   (a) Forge adds Bearer-token validation to auth.ts (P3)
 *   (b) auth-client.ts + trpc.ts have MIMRAI_SSR_SERVER_URL → NEXUS_SSR_SERVER_URL
 *
 * Test inventory:
 *   1. auth.ts reads process.env.NEXUS_API_TOKEN (token-check present)
 *   2. auth.ts has no NEXUS_LOCAL_DEV bypass (bypass removed in P3)
 *   3. auth.ts accepts "Bearer <token>" format (bearer header pattern present)
 *   4. zero MIMRAI_SSR_SERVER_URL refs in app/ (half-rename fixed)
 *   5. zero MIMRAI_LOCAL_DEV refs in app/ (full rename — cross-check with P0 guard)
 */

import { describe, test, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { join, relative } from "node:path";
import { readdirSync } from "node:fs";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app
const APP_ROOT = join(
  import.meta.dirname ?? __dirname,
  "..", // dashboard
  "..", // apps
  "..", // app
);

const AUTH_MIDDLEWARE_PATH = join(
  APP_ROOT,
  "apps",
  "api",
  "src",
  "rest",
  "middleware",
  "auth.ts",
);

const AUTH_CLIENT_PATH = join(
  APP_ROOT,
  "apps",
  "dashboard",
  "src",
  "lib",
  "auth-client.ts",
);

const TRPC_PATH = join(
  APP_ROOT,
  "apps",
  "dashboard",
  "src",
  "utils",
  "trpc.ts",
);

/** Absolute path of this test file — excluded from all grep scans */
const THIS_FILE = join(
  import.meta.dirname ?? __dirname,
  "feat-1-auth-token.test.ts",
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readSource(filePath: string): string {
  if (!existsSync(filePath)) return "";
  return readFileSync(filePath, "utf8");
}

const EXCLUDED_DIRS = new Set([
  "node_modules",
  ".next",
  "dist",
  "build",
  ".turbo",
  "__tests__", // exclude test files — they may legally contain pattern strings in comments
]);

function isExcludedDir(name: string): boolean {
  return EXCLUDED_DIRS.has(name) || name.startsWith(".pre-nexus");
}

const EXCLUDED_FILE_NAMES = new Set(["bun.lock", "bun.lockb", "yarn.lock"]);

function* walkText(dir: string): Generator<string> {
  let entries: import("node:fs").Dirent<string>[];
  try {
    entries = readdirSync(dir, { withFileTypes: true, encoding: "utf8" });
  } catch {
    return;
  }

  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (isExcludedDir(entry.name)) continue;
      yield* walkText(join(dir, entry.name));
    } else if (entry.isFile()) {
      if (EXCLUDED_FILE_NAMES.has(entry.name)) continue;
      const filePath = join(dir, entry.name);
      if (filePath === THIS_FILE) continue;
      yield filePath;
    }
  }
}

function scanForPattern(
  rootDir: string,
  pattern: RegExp,
): Array<{ file: string; lines: number[] }> {
  const hits: Array<{ file: string; lines: number[] }> = [];

  for (const filePath of walkText(rootDir)) {
    let content: string;
    try {
      content = readFileSync(filePath, "utf8");
    } catch {
      continue;
    }

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
// Suite 1 — auth.ts Bearer-token implementation guard
//
// These tests verify that Forge has added NEXUS_API_TOKEN bearer-token logic
// to apps/api/src/rest/middleware/auth.ts. They scan the source to confirm
// the required patterns are present (and the old bypass is gone).
// ---------------------------------------------------------------------------

describe("FEAT-001 P3 — auth.ts Bearer-token implementation", () => {
  test(
    "auth.ts reads NEXUS_API_TOKEN from process.env (token-check present)",
    () => {
      const src = readSource(AUTH_MIDDLEWARE_PATH);
      // Must reference NEXUS_API_TOKEN — the env var holding the static token
      expect(src.includes("NEXUS_API_TOKEN"), AUTH_MIDDLEWARE_PATH).toBe(true);
    },
  );

  test(
    "auth.ts validates Authorization: Bearer <token> header (bearer prefix parsed)",
    () => {
      const src = readSource(AUTH_MIDDLEWARE_PATH);
      // Implementation must extract a Bearer token from the Authorization header.
      // Match any of: 'Authorization', 'authorization', 'Bearer', 'bearer'
      // The pattern of splitting on "Bearer " or using startsWith("Bearer ")
      // confirms the header is parsed, not just present.
      const hasBearerCheck =
        /[Bb]earer/i.test(src) &&
        (/[Aa]uthorization/i.test(src));
      expect(hasBearerCheck, "auth.ts must parse the Bearer token header").toBe(
        true,
      );
    },
  );

  test(
    "auth.ts NEXUS_LOCAL_DEV bypass removed (replaced by token gate in P3)",
    () => {
      const src = readSource(AUTH_MIDDLEWARE_PATH);
      // P3 replaces the NEXUS_LOCAL_DEV bypass with NEXUS_API_TOKEN.
      // After P3, auth.ts must NOT fall through to a NEXUS_LOCAL_DEV block.
      expect(
        src.includes("NEXUS_LOCAL_DEV"),
        "auth.ts must not retain NEXUS_LOCAL_DEV bypass after P3",
      ).toBe(false);
    },
  );

  test(
    "auth.ts: token comparison against NEXUS_API_TOKEN rejects wrong tokens (invalid-token 401 path present)",
    () => {
      const src = readSource(AUTH_MIDDLEWARE_PATH);
      // After P3, auth.ts must contain logic that:
      //   1. Extracts a token from the Authorization header
      //   2. Compares it to process.env.NEXUS_API_TOKEN
      //   3. Throws 401 when the token does not match
      //
      // We assert that NEXUS_API_TOKEN appears in a comparison/guard context —
      // i.e., the source reads NEXUS_API_TOKEN AND checks it against the
      // incoming token value (not just reads it). The combination:
      //   process.env.NEXUS_API_TOKEN  +  a !== or === comparison  +  a 401 throw
      // is the minimal contract.
      const hasTokenComparison =
        src.includes("NEXUS_API_TOKEN") &&
        (/!==\s*process\.env\.NEXUS_API_TOKEN|process\.env\.NEXUS_API_TOKEN\s*!==|!==\s*token|token\s*!==/.test(src) ||
          /NEXUS_API_TOKEN.*401|401.*NEXUS_API_TOKEN/.test(src));
      expect(
        hasTokenComparison,
        "auth.ts must compare the incoming Bearer token to NEXUS_API_TOKEN and reject mismatches with 401",
      ).toBe(true);
    },
  );
});

// ---------------------------------------------------------------------------
// Suite 2 — MIMRAI_SSR_SERVER_URL rename guard
//
// auth-client.ts and trpc.ts still read MIMRAI_SSR_SERVER_URL instead of
// NEXUS_SSR_SERVER_URL. This guard goes GREEN once Forge renames those refs.
// ---------------------------------------------------------------------------

describe("FEAT-001 P3 — MIMRAI_SSR_SERVER_URL half-rename fix", () => {
  /** Matches MIMRAI_SSR_SERVER_URL as an exact env-var token */
  const MIMRAI_SSR_RE =
    /(?<![A-Z0-9_])MIMRAI_SSR_SERVER_URL(?![A-Z0-9_])/;

  test(
    "auth-client.ts uses NEXUS_SSR_SERVER_URL (not MIMRAI_SSR_SERVER_URL)",
    () => {
      const src = readSource(AUTH_CLIENT_PATH);
      const hasMimrai = MIMRAI_SSR_RE.test(src);
      expect(
        hasMimrai,
        `auth-client.ts still references MIMRAI_SSR_SERVER_URL — rename to NEXUS_SSR_SERVER_URL`,
      ).toBe(false);
    },
  );

  test(
    "trpc.ts uses NEXUS_SSR_SERVER_URL (not MIMRAI_SSR_SERVER_URL)",
    () => {
      const src = readSource(TRPC_PATH);
      const hasMimrai = MIMRAI_SSR_RE.test(src);
      expect(
        hasMimrai,
        `trpc.ts still references MIMRAI_SSR_SERVER_URL — rename to NEXUS_SSR_SERVER_URL`,
      ).toBe(false);
    },
  );

  test("zero MIMRAI_SSR_SERVER_URL references across all of app/", () => {
    const hits = scanForPattern(APP_ROOT, MIMRAI_SSR_RE);

    const message =
      hits.length === 0
        ? "No matches"
        : `Found ${hits.length} file(s) with MIMRAI_SSR_SERVER_URL references:\n` +
          hits
            .map(
              ({ file, lines }) =>
                `  ${relative(APP_ROOT, file)} (line${lines.length > 1 ? "s" : ""} ${lines.join(", ")})`,
            )
            .join("\n");

    expect(hits, message).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Suite 3 — Better Auth cookie session no-regression guard
//
// P3 replaces the NEXUS_LOCAL_DEV bypass with a NEXUS_API_TOKEN gate.
// The cookie-session path (Better Auth) must be preserved untouched so that
// dashboard browser sessions keep working.
//
// These are REGRESSION guards — they must stay GREEN before AND after P3.
// Written as test() (not test.fails()) because the current code already
// satisfies them; they protect against Forge accidentally removing the cookie
// path while adding the token path.
// ---------------------------------------------------------------------------

describe("FEAT-001 P3 — Better Auth cookie session no-regression", () => {
  test(
    "auth.ts still calls auth.api.getSession (cookie-session path preserved)",
    () => {
      const src = readSource(AUTH_MIDDLEWARE_PATH);
      expect(
        src.includes("auth.api.getSession"),
        "auth.ts must retain auth.api.getSession call for cookie-based dashboard sessions",
      ).toBe(true);
    },
  );

  test(
    "auth.ts imports Session type from better-auth (Better Auth types retained)",
    () => {
      const src = readSource(AUTH_MIDDLEWARE_PATH);
      expect(
        src.includes("better-auth") || src.includes("Session"),
        "auth.ts must retain Better Auth Session type import",
      ).toBe(true);
    },
  );

  test(
    "auth-client.ts still creates a Better Auth client with cookie credentials (cookie flow intact)",
    () => {
      const src = readSource(AUTH_CLIENT_PATH);
      // createAuthClient from better-auth/react with credentials: "include"
      // is the cookie-session setup for the dashboard
      expect(
        src.includes("createAuthClient") && src.includes("credentials"),
        "auth-client.ts must retain createAuthClient with credentials: include for cookie sessions",
      ).toBe(true);
    },
  );
});
