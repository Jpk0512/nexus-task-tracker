# Worked Example — Full Node-Contract DAG

A small, realistic app feature: **add a `/api/health` endpoint** — "Given the app's
primary database connection, when a client calls `GET /api/health`, then it returns a
`status` of `"ok"` or `"down"` and the UI surfaces that state." This decomposes into a
4-node DAG. Copy this shape.

---

## The DAG

```
N1 (status contract)  N2 (API route) ──┐
                                        ├──▶ N4 (contract test)
                       N3 (UI badge) ──┘
```

`N2` and `N3` both depend on `N1` and can run in parallel (disjoint files: N2 touches
`app/api/health/route.ts`, N3 touches `app/components/HealthBadge.tsx` — neither reads
the other's runtime output, only N1's documented status vocabulary). `N4` depends on
both because it verifies the combined behavior: the route and the badge must agree on
the same two-value status vocabulary.

---

### N1 — confirm the health-check status vocabulary

```yaml
node_id: N1
depends_on: []
downstream_consumers: [N2, N3]
agent_persona: atlas
goal: "Name the exact DB connectivity check (a fast SELECT against the primary connection) and the two-value status vocabulary (\"ok\" | \"down\") every downstream node must reuse verbatim."
context_files:
  - "docs/ARCHITECTURE.md"
acceptance_criteria:
  - "docs/ARCHITECTURE.md names the health-check query and the exact status strings \"ok\"/\"down\" (or documents the ones already in use)"
verification_method:
  type: command
  command: "grep -A5 'health.check' docs/ARCHITECTURE.md"
risk_tier: T0
skills_required: []
do_not_touch: ["app/**", "ingestion/**"]
```

**Why this is atomic:** the verification_method is a single grep against a design doc
that either shows the vocabulary or doesn't — no judgment call, no follow-up
decomposition needed.

---

### N2 — implement `GET /api/health`

```yaml
node_id: N2
depends_on: [N1]
downstream_consumers: [N4]
agent_persona: forge-wire
goal: "Implement GET /api/health returning {\"status\": \"ok\"} (200) on a healthy DB connection and {\"status\": \"down\"} (503) on failure, using the query named in N1."
context_files:
  - "app/api/health/route.ts"
acceptance_criteria:
  - "GET /api/health returns 200 {\"status\":\"ok\"} when the DB connection succeeds"
  - "GET /api/health returns 503 {\"status\":\"down\"} when the DB connection fails"
verification_method:
  type: command
  command: "curl -sf http://localhost:3000/api/health | jq -r .status"
skills_required: ["agent-protocol", "forge-wire-conventions"]
risk_tier: T1
do_not_touch: ["ingestion/**", "models/**"]
```

**Why this is atomic:** the verification_method is the exact command a caller would
run; its output either is `ok`/`down` matching the acceptance criteria or it doesn't —
pass/fail, not "review the output and judge if it looks right."

---

### N3 — add a `HealthBadge` status indicator

```yaml
node_id: N3
depends_on: [N1]
downstream_consumers: [N4]
agent_persona: forge-ui
goal: "Add a HealthBadge component that renders \"Operational\" for status=\"ok\" and \"Degraded\" for status=\"down\" (the vocabulary named in N1), presentational only — no live fetch."
context_files:
  - "app/components/HealthBadge.tsx"
acceptance_criteria:
  - "HealthBadge renders \"Operational\" when passed status=\"ok\""
  - "HealthBadge renders \"Degraded\" when passed status=\"down\""
verification_method:
  type: command
  command: "rtk tsc && rtk lint"
skills_required: ["agent-protocol", "forge-ui-conventions"]
risk_tier: T1
do_not_touch: ["ingestion/**", "models/**", "app/api/**"]
```

**Why this is atomic:** `rtk tsc && rtk lint` is the exact gate command; a human or
lens-fast can confirm it exits 0 without re-deriving the feature.

---

### N4 — verify the route and badge agree on the status vocabulary

```yaml
node_id: N4
depends_on: [N2, N3]
downstream_consumers: []
agent_persona: quill-ts
goal: "Add a contract test asserting /api/health and HealthBadge use the identical two-value status vocabulary (\"ok\"/\"down\")."
context_files:
  - "app/api/health/route.ts"
  - "app/components/HealthBadge.tsx"
acceptance_criteria:
  - "A test exists asserting the API route's status values and HealthBadge's accepted status values are the same two-value set"
verification_method:
  type: command
  command: "rtk vitest run app/__tests__/health-contract.test.ts"
skills_required: ["agent-protocol", "tdd-core"]
risk_tier: T1
do_not_touch: ["ingestion/**", "models/**"]
```

**Boundary note:** a project-README mention of the new status endpoint is NOT a leaf
assignment — live `.claude/skills/**` and top-level docs like `README.md` are
orchestrator+user surface for most personas (agent-protocol deny-tail), so the
orchestrator handles that doc edit inline when reviewing N4.

**Why this is atomic:** `rtk vitest run app/__tests__/health-contract.test.ts` is a
single command with a binary exit code — one test criterion, verified by that
command's exit status, with nothing left to re-litigate.

---

## Plan-validation gate scoring (deterministic-first checks)

```
Gate: DAG acyclic
  N1 → {N2, N3} → N4        no back-edges, no cycle
  ✓ PASS

Gate: every leaf has a verification_method
  N1: command="grep -A5 'health.check' docs/ARCHITECTURE.md"                 ✓
  N2: command="curl -sf http://localhost:3000/api/health | jq -r .status"    ✓
  N3: command="rtk tsc && rtk lint"                                          ✓
  N4: command="rtk vitest run app/__tests__/health-contract.test.ts"         ✓
  ✓ PASS (4/4 leaves carry a concrete command, none are prose)

Gate: MECE coverage
  Acceptance criterion decomposes as:
    "returns ok/down status"       → N2 (route) + N1 (status vocabulary)
    "UI surfaces that state"       → N3 (badge)
    "verified"                     → N4 (contract test); doc mention handled inline by orchestrator
  No leaf's output is required by two disjoint branches without an edge (N2/N3 both
  legitimately depend on N1 only — that is a shared dependency, not an overlap).
  No gap: vocabulary → route → badge → verify covers the full criterion.
  ✓ PASS (mutually exclusive work per node, collectively exhaustive over the criterion)

RESULT: plan admitted to dispatch (3/3 deterministic gates pass; no model-judge
residue needed for this DAG — see docs/agents/CONTRACT.md for the exact brief/return
shape each node compiles to at dispatch time).
```
