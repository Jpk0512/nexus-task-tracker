# S12 — Never Fabricate a Green

**The rule:** never fabricate a green / passing result. `verification_result` MUST carry the
*verbatim* terminal output of every `verification_required` command — the actual text the
command printed, including the exit code. Claims without captured output are rejected.

**Narrative is not evidence.** A `verification_result` that reads "all checks passed" or
"tests green" in prose — without the actual command output — is a placeholder and counts as
UNVERIFIED. The structural proof is the *terminal text*, not a summary of it.

## What counts as evidence (fallback ladder, strongest first)

1. **JSON block with verbatim `verification_result`** — a fenced ```json block whose
   `verification_result` value is the non-empty, non-placeholder verbatim terminal output of
   every verification command. This is the required form.
2. **Verbatim passing code block** — a fenced shell/output block (no JSON wrapper) carrying a
   recognisable PASS signal (`N passed`, `no issues`, `exit 0`, `rc=0`) that **co-occurs**
   with either a command echo naming the tool that produced it (e.g. `$ <test-runner> -q`) or
   a structured result summary. A bare "ok" or "pass" in prose is NOT evidence.
3. **Bare marker with neither** — the completion marker present but no rung-1/rung-2 evidence.
   This is UNVERIFIED and MUST NOT be accepted at face value.

## Banned in `verification_result` without a real rc=0 capture

`todo`, `tbd`, `n/a`, `none`, `pending`, any `<angle-bracket token>`, `...`, `-`, `deferred`,
`structure verified`, `Ready for <tool>`, `verified complete`, and any checkmark not backed
by captured command output.

## The discipline

- Run the command. Paste what it printed. If it failed, paste the failure and either fix and
  re-run, or return `## NEXUS:BLOCKED` with the verbatim error.
- If you did not run a verification command, you may not claim its result. "It should pass" is
  a fabrication under this rule.
- A truthful non-green (`## NEXUS:BLOCKED` / `## NEXUS:CHECKPOINT`) is always correct over a
  fabricated `## NEXUS:DONE`.
