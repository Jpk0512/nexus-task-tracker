# Fixture — valid 2-node plan

### A1

```yaml
node_id: A1
depends_on: []
downstream_consumers: [A2]
agent_persona: hermes
work_type: meta
goal: do the first thing
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
notepad_topic: FIX-A1
```

### A2

```yaml
node_id: A2
depends_on: [A1]
downstream_consumers: []
agent_persona: hermes
work_type: meta
goal: do the second thing
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
notepad_topic: FIX-A2
```
