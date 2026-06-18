# nexus-vault MCP server

Phase 5a — read-only stdio reader + single-writer daemon.
Phase 5b — HTTP MCP daemon + Tailscale serve + bearer rotation + launchd plists.
Phase 8  — scheduled jobs (backup / restore-test / eval / pii-scan) + final
            Claude.ai Custom Connector registration recipe.

## Architecture (plan §7.1, B3)

Three processes share one SQLite WAL DB and one `vault_jobs` queue table:

```
Claude Code (CLI)     ─┐
Claude Desktop (mac)  ─┼─stdio→  broker.vault.stdio   (read-only)
Claude.ai web*        ─┘ HTTPS→  broker.vault.http    (Phase 5b)

                          ↓ (all reader processes enqueue jobs via
                            INSERT into vault_jobs — that's the ONLY
                            write a reader makes)

                          broker.vault.writer   (SINGLE writer; drains
                                                 vault_jobs serially)
```

`*` = Phase 5b adds the HTTP daemon + Tailscale + bearer rotation.

## Tool surface (plan §7.2)

- **Read (4):** `vault_query`, `vault_get_note`, `vault_related`, `vault_moc`
- **Graph + health (2):** `vault_graph_query`, `vault_health`
- **Write (4) — each enqueues a job:** `vault_append_inbox`,
  `vault_capture_idea`, `ingest_url`, `ingest_repo`
- **Prompts (2):** `vault-state-summary`, `vault-graduate-suggestions`
- **Resources (2):** `note://<path>`, `job://<id>`

### Per-transport surface (Phase 5b — INTENTIONAL deviation from §7.2)

| Transport | Read (4) | Graph (2) | Write (4) | Prompts | Resources |
|-----------|----------|-----------|-----------|---------|-----------|
| stdio (local — Claude Code, Claude Desktop) | yes | yes | **yes** | yes | yes |
| http  (Claude.ai web via Tailscale) | yes | yes | **no**  | yes | yes |

Plexus Phase-5b decision: elevated bearer **DENIED**. The HTTP surface registers
read tools only. Writes are stdio-only. This is a deliberate trade-off — plan
§7.2 lists 10 tools across both transports; the web surface intentionally
exposes a strict subset (6 tools) to keep the blast radius small. The privacy
fence (web_default mode) additionally blocks personal/work-domain reads.

## Registration

**Claude Code (already done in Phase 5a):**

```bash
claude mcp add --transport stdio --scope user nexus-vault \
  -- "$(command -v uv)" run \
     --project ~/nexus-installer/nexus-broker \
     python -m broker.vault.stdio
```

*Substitute `~/nexus-installer` with wherever you cloned this repo. The `uv`
binary is resolved via `$(command -v uv)`; if `uv` is not on your `PATH`, use
its absolute path (e.g. `/opt/homebrew/bin/uv` or `~/.local/bin/uv`).*

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(this Phase 5b commit registers it on this machine):

```json
{
  "mcpServers": {
    "nexus-vault": {
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "run", "--project",
        "/path/to/nexus-installer/nexus-broker",
        "python", "-m", "broker.vault.stdio"
      ],
      "env": {
        "NEXUS_VAULT_ROOT": "/path/to/nexus-installer/research",
        "NEXUS_VAULT_DB":   "/path/to/nexus-installer/.memory/project.db"
      }
    }
  }
}
```

Restart Claude Desktop after editing. The `nexus-vault` server then exposes
the full 10-tool stdio surface (plan §7.2). If the JSON above is the only
entry, that is the entire `mcpServers` block; if other servers are present,
add this entry alongside them without removing the others.

## Writer daemon

Run manually (drain queue once + exit):

```bash
uv run --project ~/nexus-installer/nexus-broker \
  python -m broker.vault.writer --once
```

Run as a long-running daemon (Phase 5b adds launchd):

```bash
uv run --project ~/nexus-installer/nexus-broker \
  python -m broker.vault.writer
```

Second instance refuses to start (fcntl lock at `~/.cache/nexus-research/writer.lock`).

## Env vars

- `NEXUS_VAULT_ROOT`         — default: walks up from broker.vault for a sibling `research/` containing `.privacy-rules.yaml`.
- `NEXUS_VAULT_DB`           — default: walks up for `.memory/project.db`.
- `NEXUS_VAULT_WRITE_PATHS`  — comma-separated; default: `40-inbox/raw/,20-workshop/brainstorms/capsules/,20-workshop/pulled/,40-inbox/_jobs/`.

## Privacy fence (plan §7.4)

`research/.privacy-rules.yaml` declares `fenced_domains: [personal, work]`.
`access_mode='local_stdio'` (stdio server) can read fenced content.
`access_mode='web_default'` (HTTP daemon, default bearer) cannot — fenced
queries return `{hits: [], fenced: true}`. Constant-time bearer compare via
`broker.vault.policy.bearer_matches` (`hmac.compare_digest`).

## HTTP MCP daemon (Phase 5b)

```bash
uv run --project ~/nexus-installer/nexus-broker \
  python -m broker.vault.http
```

Binds `127.0.0.1:8848`. Refuses to bind non-loopback hosts — Tailscale serve
fronts it.

- Endpoints: `POST /mcp` (Streamable HTTP MCP), `GET /health` (bearer not
  required — returns `{status, build, bearer_loaded}`).
- Bearer source: `~/.config/nexus-research/bearers.d/default.token` (mode 0600).
- SIGHUP reloads the bearer from disk without a restart (used after
  rotation).
- Every MCP call logs one JSON line to `research/_meta/vault-access.log`.

### Bearer rotation (90d cadence per plan §7.4)

```bash
python3 ~/nexus-installer/bin/vault-rotate-bearer.py default
```

Only `default` is supported — `elevated` is intentionally absent per Plexus
Phase-5b decision.

### launchd activation (user-controlled)

`bin/install-launchd.sh` installs **six** plists. The two daemons run
continuously; the four scheduled jobs fire on a calendar (and can be
triggered manually with `launchctl start`).

```bash
~/nexus-installer/bin/install-launchd.sh

# Long-running daemons:
launchctl load -w ~/Library/LaunchAgents/com.nexus.vault.writer.plist
launchctl load -w ~/Library/LaunchAgents/com.nexus.vault.http.plist

# Scheduled jobs (Phase 8):
launchctl load -w ~/Library/LaunchAgents/com.nexus.vault.backup.plist
launchctl load -w ~/Library/LaunchAgents/com.nexus.vault.restore-test.plist
launchctl load -w ~/Library/LaunchAgents/com.nexus.vault.eval.plist
launchctl load -w ~/Library/LaunchAgents/com.nexus.vault.pii-scan.plist

launchctl list | grep nexus.vault    # must show all six labels
```

Schedule (all local time):

| Label                     | When                  | Action                                         |
|---------------------------|-----------------------|------------------------------------------------|
| `com.nexus.vault.writer`  | continuous            | Drains `vault_jobs` serially                   |
| `com.nexus.vault.http`    | continuous            | HTTP MCP daemon on 127.0.0.1:8848              |
| `com.nexus.vault.backup`     | nightly 03:00       | `bin/vault-backup.sh` (7-day GC)               |
| `com.nexus.vault.pii-scan`   | nightly 02:00       | `bin/scan-pii.py --corpus research/`              |
| `com.nexus.vault.restore-test` | weekly Sun 04:00  | `bin/vault-restore-test.sh`                    |
| `com.nexus.vault.eval`       | weekly Sat 05:00    | `bin/run-eval.py --resume` (B4 deploy-gate)    |

Manual trigger (ignores the calendar, runs once):

```bash
launchctl start  com.nexus.vault.backup
launchctl start  com.nexus.vault.restore-test
launchctl start  com.nexus.vault.pii-scan
launchctl start  com.nexus.vault.eval
```

Logs: `~/Library/Logs/nexus-research/{http,writer,backup,restore-test,eval,pii-scan}.{out,err}.log`.

### Tailscale serve runbook

```bash
~/nexus-installer/bin/install-tailscale-serve.sh
```

The script discovers your tailnet MagicDNS hostname and prints the exact
`tailscale serve` command (we do not auto-execute — `tailscale serve` may
need sudo and would clobber existing serve configs). After you run the
serve commands, verify:

```bash
~/nexus-installer/bin/install-tailscale-serve.sh --verify
```

### Claude.ai web — Custom Connector registration (final recipe)

Final URL on this machine: `https://work-mac-ns.tail9b5f2f.ts.net/mcp`.

Prerequisites — all done in Phase 5b/8:
1. `bin/install-launchd.sh` was run (writes all six plists).
2. `launchctl load -w` was run for `com.nexus.vault.{writer,http}.plist`
   (and the four scheduled jobs). `launchctl list | grep nexus.vault`
   must show all six entries.
3. A bearer exists at `~/.config/nexus-research/bearers.d/default.token`
   (rotate with `python3 bin/vault-rotate-bearer.py default`; the HTTP
   daemon reloads on SIGHUP).
4. `bin/tailscale-serve-start.sh` was run; the user executed the two
   printed `sudo tailscale serve …` commands; `bin/tailscale-serve-start.sh
   --verify` returns HTTP 200.

Verify from this machine (must return HTTP 200 with `bearer_loaded=true`):

```bash
curl -fsS https://work-mac-ns.tail9b5f2f.ts.net/health

curl -fsS -H "Authorization: Bearer $(cat ~/.config/nexus-research/bearers.d/default.token)" \
  https://work-mac-ns.tail9b5f2f.ts.net/health
```

Register in Claude.ai:

1. Open **claude.ai → Settings → Connectors → Add custom connector**.
2. **URL:** `https://work-mac-ns.tail9b5f2f.ts.net/mcp`
3. **Authorization header:** `Bearer <paste contents of ~/.config/nexus-research/bearers.d/default.token>`
4. **One entry only** — no elevated variant (Plexus Phase-5b decision;
   elevated bearer never leaves this machine).
5. Connect — Claude.ai should list 6 tools (read-only surface; writes
   are stdio-only). Try the `vault-state-summary` prompt to confirm.

Privacy fence over the network (plan §7.4, B6):

- `domain ∈ {research, ai-techniques, plexus, repo-knowledge}` reads return hits.
- `domain ∈ {personal, work}` reads return `{hits: [], fenced: true}` — the
  default bearer's `access_mode='web_default'` denies fenced reads at the
  policy layer in `broker.vault.policy.allowed_for`. This holds even when
  the connector is registered on Claude.ai web.
- Writes (`vault_append_inbox`, `vault_capture_idea`, `ingest_url`,
  `ingest_repo`) are intentionally absent from the HTTP surface — the
  registered tool set on Claude.ai will not include them.

Rotate every 90 days:

```bash
python3 ~/nexus-installer/bin/vault-rotate-bearer.py default
# Then re-paste the new token into the Claude.ai connector header field.
```
