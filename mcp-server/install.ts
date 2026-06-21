#!/usr/bin/env bun
/**
 * install.ts — idempotent installer for the nexus-mcp server entry in ~/.claude/mcp.json
 *
 * Usage:
 *   bun run install:mcp             # live run
 *   DRY_RUN=1 bun run install:mcp  # print merged JSON to stdout, touch nothing
 *
 * Steps:
 *   1. Build the server (bun run build → dist/index.js) unless already current.
 *   2. Back up ~/.claude/mcp.json (if it exists) to ~/.claude/mcp.json.<timestamp>.bak
 *   3. Merge the nexus-mcp entry idempotently (no duplicate entry).
 *   4. Write the result back to ~/.claude/mcp.json.
 *
 * Required env vars (read from the environment, never hardcoded):
 *   NEXUS_API_TOKEN  — bearer token used by the MCP server to authenticate to the Nexus API
 *   NEXUS_TEAM_ID    — team identifier scoping all queries (e.g. "local-dev-team")
 *
 * Optional env vars (forwarded into the MCP server entry):
 *   NEXUS_USER_ID          — defaults to "local-dev-user" inside server.ts if absent
 *   NEXUS_KNOWLEDGE_ROOT   — absolute path to the Obsidian vault directory
 *   NEXUS_DATABASE_URL     — postgres connection string; defaults to local dev URL in server.ts
 */

import { execSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, resolve } from "node:path";
import { homedir } from "node:os";

// ── config ────────────────────────────────────────────────────────────────────

const MCP_SERVER_NAME = "nexus-mcp";
// MCP_JSON_OVERRIDE lets the dry-run/test path target a temp file instead of ~/.claude/mcp.json.
const MCP_JSON_PATH = process.env.MCP_JSON_OVERRIDE
  ? resolve(process.env.MCP_JSON_OVERRIDE)
  : resolve(homedir(), ".claude", "mcp.json");
const REPO_ROOT = resolve(import.meta.dirname, "..");
const SERVER_DIR = resolve(REPO_ROOT, "mcp-server");
const DIST_FILE = resolve(SERVER_DIR, "dist", "index.js");
const DRY_RUN = process.env.DRY_RUN === "1";

// ── required env var guard ────────────────────────────────────────────────────
// Fail fast if the vars the MCP server actually needs are missing at install time.
// (They must be present in the *caller's* environment so the script can embed them.)

const REQUIRED_ENV: Record<string, string> = {};
const OPTIONAL_ENV: Record<string, string> = {};

function requireEnv(key: string): string {
  const val = process.env[key];
  if (!val) {
    console.error(
      `\nERROR: required env var ${key} is not set.\n` +
        `  Set it in your shell before running install:mcp, e.g.:\n` +
        `  export ${key}=<value>\n`
    );
    process.exit(1);
  }
  return val;
}

REQUIRED_ENV["NEXUS_API_TOKEN"] = requireEnv("NEXUS_API_TOKEN");
REQUIRED_ENV["NEXUS_TEAM_ID"] = requireEnv("NEXUS_TEAM_ID");

// Optional passthrough vars — include only if set.
for (const key of [
  "NEXUS_USER_ID",
  "NEXUS_KNOWLEDGE_ROOT",
  "NEXUS_DATABASE_URL",
]) {
  const val = process.env[key];
  if (val) OPTIONAL_ENV[key] = val;
}

// ── step 1: build ─────────────────────────────────────────────────────────────

function build() {
  if (!existsSync(DIST_FILE)) {
    console.log("[install:mcp] Building mcp-server → dist/index.js …");
    execSync("bun run build", { cwd: SERVER_DIR, stdio: "inherit" });
    console.log("[install:mcp] Build complete.");
  } else {
    console.log("[install:mcp] dist/index.js already exists; skipping build.");
  }
}

// ── step 2: back up existing mcp.json ────────────────────────────────────────

function backup() {
  if (!existsSync(MCP_JSON_PATH)) return;
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const backupPath = `${MCP_JSON_PATH}.${ts}.bak`;
  copyFileSync(MCP_JSON_PATH, backupPath);
  console.log(`[install:mcp] Backed up existing mcp.json → ${backupPath}`);
}

// ── step 3: build the new entry ───────────────────────────────────────────────

function buildEntry() {
  return {
    type: "stdio" as const,
    command: "bun",
    args: [DIST_FILE],
    env: {
      ...REQUIRED_ENV,
      ...OPTIONAL_ENV,
    },
  };
}

// ── step 4: read → merge → write ─────────────────────────────────────────────

interface McpJson {
  mcpServers: Record<string, unknown>;
}

function readMcpJson(): McpJson {
  if (!existsSync(MCP_JSON_PATH)) {
    return { mcpServers: {} };
  }
  const raw = readFileSync(MCP_JSON_PATH, "utf8");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    console.error(
      `ERROR: ${MCP_JSON_PATH} is not valid JSON. Back it up and fix it before running this script.`
    );
    process.exit(1);
  }
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    !("mcpServers" in parsed)
  ) {
    return { mcpServers: parsed as Record<string, unknown> };
  }
  return parsed as McpJson;
}

function merge(): McpJson {
  const current = readMcpJson();
  const entry = buildEntry();

  const alreadyRegistered =
    MCP_SERVER_NAME in current.mcpServers &&
    JSON.stringify(current.mcpServers[MCP_SERVER_NAME]) ===
      JSON.stringify(entry);

  if (alreadyRegistered) {
    console.log(
      `[install:mcp] Entry "${MCP_SERVER_NAME}" already up-to-date; nothing to do.`
    );
  } else if (MCP_SERVER_NAME in current.mcpServers) {
    console.log(
      `[install:mcp] Updating existing "${MCP_SERVER_NAME}" entry …`
    );
  } else {
    console.log(
      `[install:mcp] Registering new "${MCP_SERVER_NAME}" entry …`
    );
  }

  return {
    ...current,
    mcpServers: {
      ...current.mcpServers,
      [MCP_SERVER_NAME]: entry,
    },
  };
}

// ── main ──────────────────────────────────────────────────────────────────────

function main() {
  console.log(
    `[install:mcp] ${DRY_RUN ? "DRY RUN — no files will be written" : "Installing nexus-mcp into ~/.claude/mcp.json"}`
  );

  if (!DRY_RUN) {
    build();
    backup();
  }

  const merged = merge();
  const output = JSON.stringify(merged, null, 2);

  if (DRY_RUN) {
    console.log("\n── merged mcp.json (dry run) ──────────────────────────────");
    console.log(output);
    console.log("───────────────────────────────────────────────────────────\n");
    console.log("[install:mcp] Dry run complete. No files were written.");
    return;
  }

  // Ensure ~/.claude/ exists.
  const dir = dirname(MCP_JSON_PATH);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }

  writeFileSync(MCP_JSON_PATH, output + "\n", "utf8");
  console.log(`[install:mcp] Written to ${MCP_JSON_PATH}`);
  console.log("[install:mcp] Done. Restart Claude Desktop to pick up the new server.");
}

main();
