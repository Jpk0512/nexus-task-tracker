> **Canonical (DEC-060/061; owner-ratified 2026-07-05); dispatch-wiring deferred to R3 — loadable now, not yet orchestrator-routed.** Adapted from
> the meta-repo's skill-agent audit archive for `review-panel` (DEC-060/061). Not wired to any agent.

# Golden evaluator pair — review-panel (security lens): one input, a verdict that MEETS the bar, one that FAILS it

Asset type: **worked example, valid/invalid pair** — loaded via `review-panel`'s
References section before writing or grading any panelist verdict.
Doctrine implemented: structured verdict schema, the specificity bar (built on this
project's own kept-verbatim exemplar), independent re-derivation, adversarial framing,
UNCERTAIN-never-defaults-to-PASS.

---

## INPUT (what the panelist receives)

```json
{
  "lens": "security",
  "leg_id": "TASK-231-leg2",
  "risk_score_inputs": {"auth_surface": true, "data_write_path": false, "prior_revise": false},
  "diff": "see below",
  "acceptance_criteria": ["Session cookie validated on every /api/admin/* request"]
}
```

Diff under review:

```diff
--- a/app/auth/session.ts
+++ b/app/auth/session.ts
@@ -38,9 +38,12 @@ export async function validateSession(req: Request): Promise<Session | null> {
   const cookie = req.headers.get("cookie");
   const token = parseSessionToken(cookie);
-  if (!token) return null;
+  if (!token) {
+    logAuthEvent("missing_token", req);
+    return null;
+  }
   const stored = await getStoredToken(token.sessionId);
-  if (token.value !== stored.value) return null;
+  if (token.value == stored.value) return loadSession(token.sessionId);
+  return null;
 }
```

The panelist's standing instruction (from `review-panel` + its security lens fragment):
*derive your own analysis of the diff BEFORE reading the producer's claims; your job is
to refute this work — PASS is the conclusion of a failed refutation.*

---

## VERDICT THAT MEETS THE BAR (golden output — copy this shape)

```json
{
  "lens": "security",
  "verdict": "FAIL",
  "findings": [
    {
      "file_line": "app/auth/session.ts:42",
      "claim": "token compared with == not timing-safe",
      "evidence": "Refactor changed `token.value !== stored.value` (early-return) to `token.value == stored.value` (accept path). Two regressions in one line: (1) loose equality replaces strict — `==` coerces, so a token value of '' compares equal to a stored value of 0-like states; (2) neither form is constant-time — the original code shared this flaw, but the rewrite was the moment to move to crypto.timingSafeEqual, and the acceptance criterion puts this line squarely in scope.",
      "severity": "high"
    },
    {
      "file_line": "app/auth/session.ts:40",
      "claim": "logAuthEvent receives the raw Request on the unauthenticated path",
      "evidence": "logAuthEvent('missing_token', req) — if logAuthEvent serializes headers (could not confirm from this diff: its impl is not in the provided context), the cookie header lands in logs on every unauthenticated hit.",
      "severity": "uncertain"
    }
  ],
  "checks_run": [
    "re-derived: traced both return paths of validateSession against the acceptance criterion",
    "compared: producer claims 'strict comparison preserved' — contradicted at line 42",
    "searched provided context for logAuthEvent impl — absent, hence UNCERTAIN finding, not silence"
  ]
}
```

**Why this meets the bar:** every finding is `file:line` + a claim naming the exact
mechanism + evidence quoting the code. The specificity exemplar is this project's own:
`app/auth/session.ts:42 — token compared with == not timing-safe`. The unresolvable
question became an explicit UNCERTAIN finding — routed up into the findings union, never
silently dropped. `checks_run` shows independent re-derivation happened before diffing
against the producer's claims — the checklist-reviewer gave zero lift (LSN-016 vs
LSN-017); re-derivation is what pays.

---

## VERDICT THAT FAILS THE BAR (rejected — schema-valid but content-invalid)

```json
{
  "lens": "security",
  "verdict": "FAIL",
  "findings": [
    {
      "file_line": "app/auth/session.ts",
      "claim": "security looks off in the token handling",
      "evidence": "the comparison logic was changed and should be double-checked",
      "severity": "medium"
    }
  ],
  "checks_run": ["reviewed the diff"]
}
```

**Why it's rejected, field by field:**
- `file_line` has no line number — not actionable, not verifiable.
- `claim` is a bare verb ("looks off") — FORBIDDEN by the in-prompt specificity bar.
- `evidence` delegates the work back ("should be double-checked") — a finding IS the check.
- `checks_run` shows no re-derivation — "reviewed the diff" is the checklist-reviewer
  anti-pattern that measured zero lift (LSN-016 vs LSN-017).
- Note: the verdict word `FAIL` was even *correct* here — the verdict is still rejected.
  A right answer with unusable findings gives the synthesis judge (`lens`) nothing to
  adjudicate on; the findings union is the panel's actual product.

---

## Applying this to review-panel aggregation

If this were one of `N` panelist verdicts in a live panel run: the MEETS-BAR verdict
contributes its `findings` (one `high`, one `uncertain`) into the union that `lens`
adjudicates on when aggregating — per `review-panel`'s aggregation/voting protocol, any
FAIL or UNCERTAIN from any panelist routes to `lens` synthesis; unanimous PASS is the
only path that skips synthesis. The FAILS-BAR verdict, if produced, would be treated as
effectively unusable input to that synthesis step even though its `verdict` field alone
would not fail schema validation — which is exactly why the specificity bar is graded on
`findings` content, not just the top-level `verdict` string.
