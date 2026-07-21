> **Canonical (DEC-060/061; owner-ratified 2026-07-05); dispatch-wiring deferred to R3 — loadable now, not yet orchestrator-routed.** Reviewer
> fragment for `review-panel` (DEC-060/061). Not wired to any agent.

# Lens: Security

## What this lens checks
Auth/session handling, input trust boundaries, secret/credential exposure, and
comparison operations on security-sensitive values (tokens, signatures, hashes). Derive
your own analysis of the diff BEFORE reading the producer's claims — your job is to
refute this work; PASS is the conclusion of a failed refutation, never the default.

Checks in scope: strict vs loose equality on any token/credential comparison; whether a
comparison is timing-safe where it needs to be; whether request/header data reaches a
logging or storage sink unredacted; whether an auth check can be bypassed by a
short-circuit added elsewhere in the same diff; whether newly-added error paths leak
internal state (stack traces, raw DB errors) to the caller.

## Worked example
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

Finding (meets the bar):
```json
{
  "file_line": "app/auth/session.ts:42",
  "claim": "token compared with == not timing-safe",
  "evidence": "Refactor changed `token.value !== stored.value` (early-return) to `token.value == stored.value` (accept path). Loose equality replaces strict, and neither form is constant-time — the rewrite was the moment to move to crypto.timingSafeEqual, and the acceptance criterion puts this line in scope.",
  "severity": "high"
}
```
The non-obvious part: the verdict word could be right even when the finding is useless
— `"security looks off in the token handling"` with no line number is REJECTED even if
the overall verdict is FAIL. A finding IS the check; "should be double-checked" delegates
the work back to whoever reads the verdict, which is the exact anti-pattern that measured
zero lift (LSN-016 vs LSN-017). See `examples/golden-verdict-pair.md` for the full
meets/fails-bar pair, field by field.
