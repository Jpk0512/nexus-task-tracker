# Fixture — two unordered nodes claim the same write_scope glob

### B1

```yaml
node_id: B1
depends_on: []
downstream_consumers: [B3]
agent_persona: hermes
work_type: meta
goal: do a thing
context_files: ["docs/agents/CONTRACT.md"]
acceptance_criteria: ["thing done"]
verification_method:
  type: command
  command: "true"
skills_required: [agent-protocol, deployable-engineering]
risk_tier: T0
budget: S
irreversible: false
do_not_touch: []
write_scope: ["nexus-broker/src/broker/shared_file.py"]
notepad_topic: FIX-B1
```

### B2

```yaml
node_id: B2
depends_on: []
downstream_consumers: [B3]
agent_persona: hermes
work_type: meta
goal: do an unrelated thing, unordered against B1
context_files: ["docs/agents/CONTRACT.md"]
acceptance_criteria: ["thing done"]
verification_method:
  type: command
  command: "true"
skills_required: [agent-protocol, deployable-engineering]
risk_tier: T0
budget: S
irreversible: false
do_not_touch: []
write_scope: ["nexus-broker/src/broker/shared_file.py"]
notepad_topic: FIX-B2
```

### B3

```yaml
node_id: B3
depends_on: [B1, B2]
downstream_consumers: []
agent_persona: hermes
work_type: meta
goal: terminal sink consuming both
context_files: ["docs/agents/CONTRACT.md"]
acceptance_criteria: ["thing done"]
verification_method:
  type: command
  command: "true"
skills_required: [agent-protocol, deployable-engineering]
risk_tier: T0
budget: S
irreversible: false
do_not_touch: []
notepad_topic: FIX-B3
```
