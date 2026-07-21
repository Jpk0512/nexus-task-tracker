# nexus-broker/tests — syrupy snapshot workflow (F3-04)

No pre-existing test-suite README was found under `nexus-broker/` (only
`src/broker/vault/README.md` and `router_train_data/README.md`, both scoped to
their own subsystem) — this file is the new, narrow home for the one
cross-cutting convention this task introduced. If a broader `tests/`
conventions doc already exists elsewhere by the time you read this, fold this
section into it and remove this file (see `open_items` in the F3-04 dispatch
record for the placement flag).

## Why snapshots

A handful of suites assert on **generated content** — a rendered/rewritten doc
(`test_docs_watcher.py`), a message string injected into an agent's next-turn
context (`test_advisory_handlers.py`), or a structured RPC/dispatch response
envelope (`test_event_bus.py`, `test_daemon_ready_set.py`,
`test_daemon_ownership.py`, `test_daemon_session_digest.py`,
`test_daemon_pilot.py`, `test_conductor_pool.py`, `test_conductor_dag.py`,
`test_capability_index.py`). These used to pin the expected value as an
inline string/dict literal in the test body. That works, but a legitimate
content change (a reworded advisory message, an added envelope field) meant
hand-editing the literal in the test source, with no diff review step and no
signal that the OLD literal was the reviewed baseline rather than an
arbitrary guess.

[syrupy](https://github.com/tophat/syrupy) moves the pinned value out of the
test body into a reviewed `.ambr` file under `tests/__snapshots__/`, so drift
shows up as a normal, readable file diff at review time, and updating it is a
deliberate, visible act (`--snapshot-update`) rather than a silent inline
edit.

**Scope discipline:** only assertions that were already exact-equality pins
on a literal (`result == {...}`, `text == "..."`) were converted. Substring
(`"x" in text`) and structural-property assertions are left exactly as they
are — DEC-068 ("assert properties, not prose") still governs new tests;
syrupy is for the existing pinned-literal suites named above, not a reason to
start pinning full text everywhere.

## Workflow

1. **Write the assertion** against the `snapshot` fixture (auto-injected by
   the `syrupy` pytest plugin — no import needed in the test body beyond the
   optional `from syrupy.assertion import SnapshotAssertion` type hint):

   ```python
   def test_something(snapshot: SnapshotAssertion) -> None:
       result = under_test()
       assert result == snapshot(name="something_envelope")
   ```

   Use an explicit `name=` whenever a test makes more than one snapshot
   assertion — it keeps the `.ambr` entries self-describing on review; a
   single-assertion test can omit it.

2. **Generate the baseline** (first time, or after an intentional change):

   ```bash
   cd nexus-broker && uv run pytest tests/test_docs_watcher.py --snapshot-update
   ```

   Scope `--snapshot-update` to the file(s) you touched — running it
   repo-wide will silently accept unrelated drift.

3. **Review the diff.** `--snapshot-update` writes/updates the `.ambr` file
   under `tests/__snapshots__/<test_file>.ambr`; `git diff` on that file IS
   the review artifact. A snapshot update is only a genuine assertion change
   if the diff shows the content you intended to change — nothing else.

4. **Re-run normally** (no `--snapshot-update`) to confirm the suite is green
   against the now-committed baseline:

   ```bash
   cd nexus-broker && uv run pytest tests/test_docs_watcher.py -q
   ```

5. **Commit the `.ambr` file alongside the test/source change** — an
   uncommitted or stale snapshot is a false green for everyone else.

## Failure reading

A snapshot mismatch prints a full readable diff (old vs. new) in the pytest
failure output — read it like any other diff. If the new value is correct,
regenerate with `--snapshot-update` (step 2) and review the resulting file
diff before committing. If it isn't, the production code (or the test's
setup) regressed — fix that, don't update the snapshot to match the bug.

## Dependency

`syrupy` is a `nexus-broker` dev dependency, declared as `syrupy>=5.5` in
`[dependency-groups].dev` in `nexus-broker/pyproject.toml` (landed in b0d8c11).
A plain `uv sync` / `uv run pytest` picks it up automatically — no manual
`.venv` install needed.
