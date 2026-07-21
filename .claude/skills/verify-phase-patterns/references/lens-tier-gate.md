# N-distinct-lens-row requirement for gated / risk-tiered work

`lens-gate.sh` enforces this at `SubagentStop` (per-dispatch, can block) and a
Stop-event backstop (`lens-tier-backstop.sh`) audits it session-wide
(advisory-only — Stop cannot block). A verify phase you author MUST NOT rely
on a single generic Lens call to satisfy a T2 (gated/risky) requirement — the
Lens dispatch has to write a validation row keyed at the SPECIFIC required
tier, not just any tier.

**The invariant, unchanged (structural floor — NEVER weakened):** every
code-touching DONE marker needs >=1 `validation_log` row with
`agent_validated='lens'` and `verdict='PASS'`. This holds for T0 (no row
required) and T1 (light row, any tier) work exactly as before.

**The strengthening, additive on top (T2 / gated / risk-tiered dispatches
only):** the PASS row must ALSO carry `lens_type` matching the tier the
dispatch actually required (`risk_tier`) — a stale or lower-tier row (e.g. a
leftover T0/T1 PASS inside the validation window) no longer silently
satisfies a T2 requirement. When your verify phase's classifier determines a
leg is T2, the Lens dispatch for that leg MUST call:

```
python3 .memory/log.py validation add --agent lens --target <implementer> \
  --task-hash <hash> --verdict PASS --lens-type T2 --risk-tier T2 \
  --summary "..."
```

If your Workflow fans a single task out across MULTIPLE risk tiers (e.g. one
leg needs only a light T1 check, another leg touches a gated prefix and
needs the full T2 audit), each leg's Lens dispatch writes its OWN row at its
own tier — the gate then requires a DISTINCT row per required tier, not one
row reused across tiers. Do not have one Lens call claim coverage for
multiple tiers it didn't actually audit at that depth.

**Do not silently swallow the Stop-event backstop's WARN.** If
`lens-tier-backstop.sh` surfaces a gap at session end (a T2 row was claimed
but the matching `lens_type='T2'` row never landed), treat it as a signal to
investigate before trusting that session's DONE markers — it means a
SubagentStop dispatch's gate check was bypassed or never fired.

See `docs/ORCHESTRATOR-GATES.md` for the full gate contract and exact
deny messages.

## Per-leg Lens invocation for worktree legs

Worktree isolation is the DEFAULT for ≥2 parallel code-writing Workflow legs
(see `Skill nexus-dispatch-catalog` §Workflow legs isolation strategy). When multiple legs
run in parallel worktrees, invoke a SEPARATE Lens agent PER WORKTREE, with
`files_changed` scoped to that worktree's outputs only — never one generic Lens call over
the union of all legs' diffs. This prevents one leg's failing Lens from blocking another
leg's already-passing verdict (the independent-verification principle already governing
the fan-out-and-synthesize technique). The final merge-back-and-remove phase runs ONLY
after ALL parallel Lens verdicts are PASS; a single REVISE holds back only its own
worktree's merge, not the others.
