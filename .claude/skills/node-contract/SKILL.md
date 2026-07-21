---
name: node-contract
description: "The planner/executor I/O contract — DAG-shaped briefs where every leaf carries depends_on edges, a machine-checkable verification_method (a concrete command, not prose), and acceptance criteria. Use when a planner is authoring a MECE task DAG, when validating a plan before dispatch (the plan-validation gate), or when checking a leaf's verification_method is concrete enough to be atomic. Do NOT use for the flat single-brief shape (required brief fields, return schema, completion-marker vocabulary) — that lives in docs/agents/CONTRACT.md; this skill only owns the DAG layer on top of it."
metadata: {tier: sonnet, token_budget: 900, injectable: true}
---

# Node Contract

## When this fires

`planner` is decomposing a feature into a task DAG (each node = one brief), or the
plan-validation gate is scoring a DAG before dispatch. Authoring or validating one
already-flat brief with no dependency edges → use `docs/agents/CONTRACT.md` directly;
this skill is the layer that turns a flat brief into a DAG leaf.

## Rules

- Every node is a `docs/agents/CONTRACT.md` brief PLUS `depends_on` (upstream node ids)
  and `downstream_consumers` (who reads this node's output) — node-contract does not
  redefine the brief fields, it adds the edges. Restating the brief schema here would
  create a second copy to drift — this skill is the single home for the DAG-edge layer,
  `docs/agents/CONTRACT.md` for the flat brief schema.
- `verification_method` per leaf must be a concrete command, never prose ("check that
  X works" is rejected). A leaf whose only proposed check is a sentence is not atomic —
  it is still decomposable, and the plan-validation gate rejects it.
- `acceptance_criteria` are pass/fail and externally checkable, not self-graded — a
  self-reported "looks good" acceptance defeats the back gate the same way an
  unverified `## NEXUS:DONE` does (see CONTRACT.md's fallback evidence ladder).
- `skills_required` is planner-derived from `(persona, work_type)` via
  `docs/agents/SKILL_MAP.md`, not hand-named by whoever authors the DAG — hand-naming
  skills at dispatch time is exactly the kind of undetectable drift this field fixes.
- The DAG must be acyclic and MECE (no leaf's output silently required by two
  disjoint branches, no gap between leaves that leaves part of the goal uncovered) —
  both are deterministic, pre-dispatch checks the plan-validation gate runs before
  any execution begins.
- A dispatched node is the **frozen instance** of its contract at dispatch time — see
  `docs/agents/CONTRACT.md` for the exact dispatch-time brief/return shape and the
  completion-marker vocabulary a dispatched node returns through. This skill only adds
  the DAG edges on top of that shape; it never re-derives it.
- `isolation_mode` (enum: `worktree` | `main`, per node/leg, RDEC-018 Option 3): `worktree`
  for a leg that runs alongside ≥2 other independent code-writing legs in the same
  parallel dispatch — the DEFAULT for that shape; `main` for a single indivisible node or
  a sequential/read-only leg. A node marked `worktree` carries the registered
  `worktree_path` the orchestrator obtained before dispatch; `worktree-guard.sh` DENIES
  an unregistered path regardless of what this field says. See the `team-routing` skill's
  isolation-discipline section for the registration + mandatory merge-back/release
  mechanics this field feeds into.

## Worked example

See `examples/full-dag-example.md` for a full 4-node DAG (a small, realistic app
feature) with `depends_on` edges, acceptance criteria, a concrete `verification_method`
per leaf, and the plan-validation gate's scoring shown (DAG acyclic, every leaf
verification_method present, MECE coverage).

## Decision path

| Situation | What to do |
|---|---|
| Authoring a brief with no dependency edges (one flat, standalone task) | Use `docs/agents/CONTRACT.md` directly — this skill is not needed. |
| Decomposing a feature into ≥2 nodes with dependency edges between them | Author a DAG per this skill: each node = a CONTRACT.md brief + `depends_on` + `downstream_consumers`. |
| A leaf's proposed `verification_method` is a sentence ("check that X works") | REJECT — not atomic, still decomposable; the plan-validation gate rejects it. Rewrite as a concrete runnable command. |
| A leaf's `acceptance_criteria` reduces to a self-reported "looks good" | REJECT — acceptance must be externally checkable, not self-graded. |
| Naming `skills_required` for a node | Derive it from `(persona, work_type)` via `docs/agents/SKILL_MAP.md` — never hand-name it. |
| Validating a DAG before dispatch (plan-validation gate) | Check acyclic + MECE (no leaf silently required by two disjoint branches, no coverage gap) — both are deterministic pre-dispatch checks. |
| A node is being dispatched (moving from contract to runtime) | It becomes a frozen instance of its brief — see `docs/agents/CONTRACT.md`, do not re-derive the shape here. |
| A node is one of ≥2 independent code-writing legs in one parallel dispatch | Set `isolation_mode: worktree` (the DEFAULT for this shape) and carry the registered `worktree_path`. |
| A node is a single indivisible task, or sequential/read-only | Set `isolation_mode: main` — no worktree. |

Default when none of the rows match: treat the work as a single flat brief and use `docs/agents/CONTRACT.md` directly — only escalate to a DAG when a real dependency edge exists.

## References

- `docs/agents/CONTRACT.md` — read for the full flat brief/return field list and the
  completion-marker table; node-contract only adds the DAG edges on top of it.
- `docs/agents/SKILL_MAP.md` — read when deriving `skills_required` for a node from
  its `(persona, work_type)` pair.
- `examples/full-dag-example.md` — read before authoring your first multi-node DAG;
  copy its shape rather than inventing a new one.
