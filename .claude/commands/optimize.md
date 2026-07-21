---
description: Rewrite a messy/voice-dropped task dump into a best-practices Nexus task prompt. REWRITE ONLY — returns the improved prompt and STOPS; never executes anything.
argument-hint: <the raw task dump — paste or voice-drop everything>
---

# /optimize — task-prompt rewriter

You have been given a raw task dump in `$ARGUMENTS` — typically voice-transcribed,
stream-of-consciousness, multi-task ("do this, do that, make sure X, don't forget Y").
Your ONLY job is to rewrite it into the canonical task-prompt shape below and return it.

## HARD CONTRACT — rewrite only

- **Take NO action.** No file reads beyond what this command names, no edits, no
  dispatches, no task creation, no memory writes. The rewritten prompt is your ENTIRE
  output. The owner reviews it and submits it as a new message — THAT submission is
  the actual instruction; this invocation never is.
- Do not "helpfully" start on any task you recognize, no matter how obvious or urgent
  it looks. If the dump says "the build is broken, fix it" you still only rewrite.
- Preserve owner intent exactly. Where wording is precise, keep it verbatim. You are
  reorganizing and sharpening, never re-deciding.

## Rewrite procedure

1. **De-noise** — strip transcription artifacts: fillers, false starts, repeated
   "don't forget X" (keep one), self-corrections (keep the correction, drop the
   original). Resolve pronoun soup ("that thing", "the other one") from context when
   unambiguous; otherwise carry it into ASSUMPTIONS as a question.
2. **Extract the goal** — one sentence: what does DONE look like for the whole dump?
3. **Decompose into tasks** — numbered, MECE, one outcome each. Merge duplicates.
   Split any "and" hiding two outcomes. Order by dependency, then priority.
4. **Make every task verifiable** — each "make sure / check / don't forget" becomes an
   acceptance criterion with a concrete check (a command, an observable state, a file
   condition) — never a vibe ("works properly" → the command that proves it).
5. **Surface constraints** — anything phrased as a warning, boundary, or preference
   ("don't touch X", "keep it simple", "before Friday") goes in CONSTRAINTS, not
   buried inside a task.
6. **Mark parallelism** — tag tasks that are independent of each other as
   `[parallel-ok]` so the orchestrator can fan out without re-deriving it.
7. **Surface your inferences** — every gap you filled or ambiguity you resolved goes
   in ASSUMPTIONS as "I read X as meaning Y — correct?" (max 5; if more, the dump
   needs a conversation, say so). Never silently guess on scope, deletion, or anything
   irreversible.

## Output format (return EXACTLY this, then stop)

```markdown
## GOAL
<one sentence>

## TASKS
1. <imperative outcome> — [parallel-ok?] [depends: #N?]
   - accept: <concrete verifiable check>
2. ...

## CONSTRAINTS
- <boundary / do-not-touch / deadline / preference>

## ASSUMPTIONS I MADE (confirm or correct before submitting)
- <inference> — correct?
```

Close with one line: *"Review the assumptions, edit anything wrong, then submit the
block above as your task."* Nothing else.
