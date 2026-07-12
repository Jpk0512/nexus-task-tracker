# Fixture skill map — pinned minimums for plan_validation's own unit-test fixtures

Deliberately NOT the repo's real `docs/agents/SKILL_MAP.md`: `score.py`'s
`DEFAULT_SKILL_MAP_PATH` resolves relative to wherever this package physically
lives, so the SAME test, run unmodified from the package snapshot
(`nexus-package/nexus-broker/tests/`), reads a structurally different table
(the PRODUCT-INSTALL map) than the live tree does (the META-REPO map) — the
two are intentionally different vocabularies (Tableau/Azure domain skills vs.
Plexus meta-repo skills), not a drift bug. Any fixture-driven unit test that
asserts a specific `skills_derived` pass/fail outcome needs a hermetic,
tree-independent table instead of the ambient default — this file is that
table, mirroring exactly the live meta-repo rows the fixtures below were
originally authored against.

| persona | work_type | skills |
|---|---|---|
| hermes | * | agent-protocol, deployable-engineering |
| pipeline-async | * | agent-protocol, deployable-engineering |
