# Fixture — single T2 node, clean (real, non-stub verification_method)

Used by test_plan_validation.py's stub-mutation-drill reproduction (N20 finding #1,
R3-T15): this node's risk_tier=T2 trips `gate_requires_probes`, so scoring this file
via the live CLI (`python -m broker.plan_validation score <file> --json`) exercises
N09's opt-in probes, not just N08's deterministic core. Baseline must PASS both.

### P1

```yaml
node_id: P1
depends_on: []
downstream_consumers: []
agent_persona: hermes
work_type: meta
goal: do a real, non-trivial thing that a probe should judge as genuine work
context_files: ["docs/agents/CONTRACT.md"]
acceptance_criteria: ["the real thing was done"]
verification_method:
  type: command
  command: "pytest tests/test_x.py -q"
skills_required: [agent-protocol, deployable-engineering]
risk_tier: T2
budget: S
irreversible: false
do_not_touch: []
notepad_topic: R3-T15
```
